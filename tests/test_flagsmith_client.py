"""Tests for agent.flagsmith_client (MCP session mocked; the real server is smoke-tested)."""

from types import SimpleNamespace
from typing import Any

import pytest

from agent.config import Settings
from agent.flagsmith_client import (
    FlagsmithClient,
    FlagsmithError,
    _startup_failure,
    parse_tool_result,
    server_parameters,
)


def text_result(text: str, *, error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)], structured_content=None, is_error=error
    )


class FakeSession:
    """Answers MCP tool calls from a dict of tool -> list of replies."""

    def __init__(self, replies: dict[str, list[Any]]) -> None:
        self.replies = replies
        self.tools = set(replies)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return self.replies[name].pop(0)


def test_parse_json_text_and_structured_content() -> None:
    assert parse_tool_result(text_result('{"id": 1}')) == {"id": 1}
    structured = SimpleNamespace(content=[], structured_content={"a": 1}, is_error=False)
    assert parse_tool_result(structured) == {"a": 1}


def test_auth_errors_name_the_key_to_fix() -> None:
    with pytest.raises(FlagsmithError, match="FLAGSMITH_ADMIN_API_KEY") as error:
        parse_tool_result(text_result("HTTP error 401: Unauthorized", error=True))
    assert "401" in (error.value.detail or "")


def test_list_flag_names_reads_every_page(settings: Settings) -> None:
    session = FakeSession(
        {
            "list_project_features": [
                text_result('{"next": "page2", "results": [{"id": 1, "name": "fp_a"}]}'),
                text_result('{"next": null, "results": [{"id": 2, "name": "checkout_button"}]}'),
            ]
        }
    )

    names = FlagsmithClient(settings, session=session).list_flag_names()  # type: ignore[arg-type]

    assert names == ["fp_a", "checkout_button"]
    assert [args["page"] for _, args in session.calls] == [1, 2]
    assert session.calls[0][1]["project_pk"] == settings.flagsmith_project_id


def test_list_projects(settings: Settings) -> None:
    session = FakeSession(
        {
            "list_organizations": [text_result('{"results": [{"id": 9, "name": "Acme"}]}')],
            "list_projects_in_organization": [text_result('[{"id": 7, "name": "Store"}]')],
        }
    )

    projects = FlagsmithClient(settings, session=session).list_projects()  # type: ignore[arg-type]

    assert projects == [{"id": 7, "name": "Store", "organisation": "Acme"}]


def test_unknown_tool_is_refused(settings: Settings) -> None:
    client = FlagsmithClient(settings, session=FakeSession({}))  # type: ignore[arg-type]

    with pytest.raises(FlagsmithError, match="does not offer the tool 'delete_feature'"):
        client.call_tool("delete_feature", {})


def test_missing_uvx_is_explained() -> None:
    error = _startup_failure(ExceptionGroup("startup", [FileNotFoundError("uvx")]))

    assert "uvx" in error.friendly and "Install uv" in error.friendly


def test_server_is_the_official_one_over_stdio(settings: Settings) -> None:
    params = server_parameters(settings)

    assert params.command == "uvx"
    assert "github.com/Flagsmith/flagsmith.git" in params.args[1]
    assert params.env == {"TRANSPORT": "stdio", "FLAGSMITH_API_TOKEN": "admin-test"}
