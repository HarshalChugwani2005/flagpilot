"""Flagsmith access over the official Flagsmith MCP server (stdio, started with ``uvx``).

One MCP session is kept open per process on a background event loop, so Streamlit's
synchronous code can call tools without restarting the server each time. The tool list
is read once at startup. Only :mod:`agent.executor` may call write tools.

    python -m agent.flagsmith_client --smoke   # lists your organisations and projects
"""

import argparse
import asyncio
import atexit
import json
import logging
import sys
import threading
from collections.abc import Coroutine
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent import config
from agent.config import Settings
from agent.errors import ConfigError, FlagPilotError
from agent.models import VariantCounts

STARTUP_TIMEOUT_SECONDS = 240  # first run clones the server from GitHub
CALL_TIMEOUT_SECONDS = 60
CLOSE_TIMEOUT_SECONDS = 10
PAGE_SIZE = 100


class FlagsmithError(FlagPilotError):
    """Flagsmith could not be reached or returned an error."""


class _IgnoreServerLogLines(logging.Filter):
    """The official server prints log lines on stdout; the SDK skips them but logs a traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Drop only that one known, harmless message."""
        return record.getMessage() != "Failed to parse JSONRPC message from server"


logging.getLogger("mcp.client.stdio").addFilter(_IgnoreServerLogLines())
log = logging.getLogger(__name__)


def results_of(response: Any) -> list[dict[str, Any]]:
    """Items of a Flagsmith list response (plain list or paginated ``{"results": [...]}``)."""
    items = response.get("results") if isinstance(response, dict) else response
    if not isinstance(items, list):
        raise FlagsmithError(
            "Flagsmith returned an unexpected list response.", detail=str(response)[:500]
        )
    return [item for item in items if isinstance(item, dict)]


def parse_tool_result(result: Any) -> Any:
    """Return a tool's JSON (structured content, else parsed text); raise on tool errors."""
    text = "\n".join(
        part.text for part in getattr(result, "content", []) or [] if hasattr(part, "text")
    )
    if getattr(result, "is_error", False):
        if "401" in text or "403" in text:
            raise FlagsmithError(
                "Flagsmith rejected FLAGSMITH_ADMIN_API_KEY. Use an Organisation API key "
                "(Organisation Settings > API Keys).",
                detail=text[:1000],
            )
        raise FlagsmithError("Flagsmith rejected the request.", detail=text[:1000] or None)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


class MCPSession:
    """A stdio MCP session owned by a background thread running its own event loop."""

    def __init__(self, params: StdioServerParameters) -> None:
        """Start the server and wait until its tool list has been read."""
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._session: ClientSession | None = None
        self._startup_error: BaseException | None = None
        self.tools: set[str] = set()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="flagsmith-mcp", daemon=True
        )
        self._thread.start()
        self._served = asyncio.run_coroutine_threadsafe(self._serve(params), self._loop)
        if not self._ready.wait(STARTUP_TIMEOUT_SECONDS):
            raise FlagsmithError("The Flagsmith MCP server did not start in time.")
        if self._startup_error is not None:
            raise _startup_failure(self._startup_error) from self._startup_error

    async def _serve(self, params: StdioServerParameters) -> None:
        """Open the session, read the tools, then keep it open until closed."""
        self._stop = asyncio.Event()
        try:
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                self.tools = {tool.name for tool in (await session.list_tools()).tools}
                self._session = session
                self._ready.set()
                await self._stop.wait()
        except BaseException as error:  # reported to the caller, never swallowed
            self._startup_error = error
            self._session = None
            self._ready.set()

    def _run(self, coroutine: Coroutine[Any, Any, Any], timeout: float) -> Any:
        """Run ``coroutine`` on the session's loop and wait for its result."""
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout)
        except FutureTimeout as error:
            future.cancel()
            raise FlagsmithError("Flagsmith did not answer in time. Please try again.") from error

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call MCP tool ``name`` and return its raw result."""
        if self._session is None:
            raise FlagsmithError("The Flagsmith MCP connection was closed. Reload the page.")
        return self._run(self._session.call_tool(name, arguments), CALL_TIMEOUT_SECONDS)

    def close(self) -> None:
        """Shut the session (and the server process) down cleanly, then stop the loop."""
        if self._stop is not None and not self._served.done():
            self._loop.call_soon_threadsafe(self._stop.set)
            try:
                self._served.result(CLOSE_TIMEOUT_SECONDS)
            except Exception:  # best effort at shutdown; the process is exiting anyway
                log.warning("The Flagsmith MCP server did not shut down cleanly.")
        self._session = None
        self._loop.call_soon_threadsafe(self._loop.stop)


def _leaf_errors(error: BaseException) -> list[BaseException]:
    """Return the real errors inside (possibly nested) exception groups."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _leaf_errors(inner)]
    return [error]


def _startup_failure(error: BaseException) -> FlagsmithError:
    """Friendly explanation of why the MCP server could not start."""
    if any(isinstance(leaf, FileNotFoundError) for leaf in _leaf_errors(error)):
        return FlagsmithError(
            "Could not start the Flagsmith MCP server: `uvx` was not found. "
            "Install uv (https://docs.astral.sh/uv/) and restart FlagPilot."
        )
    return FlagsmithError("Could not start the Flagsmith MCP server.", detail=repr(error))


def server_parameters(settings: Settings) -> StdioServerParameters:
    """How to launch the official Flagsmith MCP server over stdio."""
    return StdioServerParameters(
        command="uvx",
        args=["--from", settings.flagsmith_mcp_source, "flagsmith-mcp"],
        env={
            "TRANSPORT": "stdio",
            "FLAGSMITH_API_TOKEN": settings.flagsmith_admin_api_key.get_secret_value(),
        },
    )


_shared: MCPSession | None = None
_shared_lock = threading.Lock()


def shared_session(settings: Settings) -> MCPSession:
    """Return the process-wide MCP session, starting it on first use."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = MCPSession(server_parameters(settings))
            atexit.register(_shared.close)
        return _shared


class FlagsmithClient:
    """Typed access to the Flagsmith MCP tools FlagPilot needs."""

    def __init__(self, settings: Settings | None = None, session: MCPSession | None = None):
        """Use ``session`` if given (tests), else the shared stdio session."""
        self._settings = settings or config.get_settings()
        self._session = session

    @property
    def session(self) -> MCPSession:
        """The MCP session, started lazily."""
        if self._session is None:
            self._session = shared_session(self._settings)
        return self._session

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # path: MCP
        """Run MCP tool ``name`` with ``arguments`` and return its parsed result."""
        if name not in self.session.tools:
            raise FlagsmithError(f"The Flagsmith MCP server does not offer the tool {name!r}.")
        return parse_tool_result(self.session.call(name, arguments))

    def list_projects(self) -> list[dict[str, Any]]:  # path: MCP
        """Every project the API key can see, with its organisation name."""
        projects = []
        for organisation in results_of(self.call_tool("list_organizations", {})):
            for project in results_of(
                self.call_tool("list_projects_in_organization", {"id": organisation["id"]})
            ):
                projects.append({**project, "organisation": organisation.get("name")})
        return projects

    def get_project(self) -> dict[str, Any]:  # path: MCP
        """Details of the configured project (FLAGSMITH_PROJECT_ID)."""
        project = self.call_tool("get_project", {"id": self._settings.flagsmith_project_id})
        if not isinstance(project, dict):
            raise FlagsmithError("Flagsmith returned unexpected project details.")
        return project

    def list_flag_names(self) -> list[str]:  # path: MCP
        """Names of every flag in the configured project (all pages)."""
        return [flag["name"] for flag in self.list_flags()]

    def list_flags(self) -> list[dict[str, Any]]:  # path: MCP
        """``{"id", "name"}`` of every flag in the configured project (all pages)."""
        flags: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.call_tool(
                "list_project_features",
                {
                    "project_pk": self._settings.flagsmith_project_id,
                    "page": page,
                    "page_size": PAGE_SIZE,
                },
            )
            flags += [
                {"id": item["id"], "name": item["name"]}
                for item in results_of(response)
                if "id" in item and "name" in item
            ]
            if not (isinstance(response, dict) and response.get("next")):
                return flags
            page += 1

    def get_variant_counts(self, flag_name: str, metric_event: str) -> dict[str, VariantCounts]:
        """Visitors and conversions per variant, from Flagsmith's experiment results.

        FlagPilot creates and starts the Flagsmith experiment, but Flagsmith does not
        document the shape of the results/exposures responses yet, so they are not parsed.
        """
        raise FlagsmithError(
            "Flagsmith is running this experiment, but FlagPilot cannot read its results "
            "yet: Flagsmith has not documented the results response. Open the experiment in "
            "Flagsmith to see them."
        )


def _smoke() -> None:
    """List the organisations and projects the API key can see."""
    client = FlagsmithClient()
    print(f"MCP tools available: {len(client.session.tools)}")
    for project in client.list_projects():
        print(f"- {project.get('organisation')} / {project.get('name')} (id {project.get('id')})")


def main() -> None:
    """CLI entry point: ``python -m agent.flagsmith_client --smoke``."""
    parser = argparse.ArgumentParser(description="Flagsmith MCP client for FlagPilot")
    parser.add_argument("--smoke", action="store_true", help="list organisations and projects")
    if not parser.parse_args().smoke:
        parser.print_help()
        return
    try:
        _smoke()
    except (ConfigError, FlagsmithError) as error:
        sys.exit(error.friendly + (f"\nDetails: {error.detail}" if error.detail else ""))


if __name__ == "__main__":
    main()
