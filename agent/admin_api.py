"""Flagsmith Admin API (httpx), used only where the official MCP server lacks a tool.

Used where the official MCP server cannot do the job:

- ``DELETE /api/v1/projects/{project_pk}/features/{id}/`` (no delete tool; published OpenAPI)
- ``POST /api/v1/environments/{key}/experiment-metrics/`` (the MCP ``create_metric`` tool
  drops the required ``definition`` field; body from Flagsmith's Experimentation docs)
- ``POST /api/v1/environments/{key}/experiments/{id}/start/`` (no start tool; docs)
- ``POST /api/v1/environments/{key}/experiments/{id}/results/refresh/`` (no refresh tool;
  Flagsmith's "Set up with a coding agent" docs). It only asks Flagsmith to recompute
  results; it changes no flag or experiment, so it is a read and is not audited.

All are authenticated with an Organisation (Master) API key.
"""

from typing import Any

import httpx

from agent.config import Settings
from agent.flagsmith_client import FlagsmithError

TIMEOUT_SECONDS = 20
HTTP_TOO_MANY_REQUESTS = 429
DELETE_FEATURE = "admin_api.delete_feature"
START_EXPERIMENT = "admin_api.start_experiment"
CREATE_METRIC = "admin_api.create_metric"


class AdminAPI:
    """Minimal Admin API client exposing the executor's ``call_tool`` interface."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        """Authenticate with FLAGSMITH_ADMIN_API_KEY against FLAGSMITH_API_URL."""
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self._base = str(settings.flagsmith_api_url).rstrip("/")
        self._headers = {
            "Authorization": f"Api-Key {settings.flagsmith_admin_api_key.get_secret_value()}"
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run an Admin API operation by name (delete a flag, or start an experiment)."""
        if name == DELETE_FEATURE:
            return self.delete_feature(int(arguments["project_pk"]), int(arguments["id"]))
        if name == CREATE_METRIC:
            body = {key: value for key, value in arguments.items() if key != "environment_api_key"}
            return self.create_metric(str(arguments["environment_api_key"]), body)
        if name == START_EXPERIMENT:
            return self.start_experiment(
                str(arguments["environment_api_key"]), int(arguments["id"])
            )
        raise FlagsmithError(f"The Admin API fallback does not support {name!r}.")

    def create_metric(self, environment_key: str, body: dict[str, Any]) -> dict[str, Any]:
        # path: Admin API (the MCP create_metric tool drops the required "definition" field)
        """Create an experiment metric from Flagsmith's documented body; returns it with its id."""
        url = f"{self._base}/environments/{environment_key}/experiment-metrics/"
        try:
            response = self._client.request("POST", url, headers=self._headers, json=body)
        except httpx.HTTPError as error:
            raise FlagsmithError(
                "Could not reach the Flagsmith Admin API.", detail=str(error)
            ) from error
        self._raise_for_status(response, f"create metric {body.get('name')!r}")
        return response.json()

    def start_experiment(self, environment_key: str, experiment_id: int) -> dict[str, Any]:
        # path: Admin API (the MCP server cannot start experiments)
        """Start a created experiment so Flagsmith begins assigning and counting visitors."""
        url = f"{self._base}/environments/{environment_key}/experiments/{experiment_id}/start/"
        response = self._send("POST", url)
        self._raise_for_status(response, f"start experiment {experiment_id}")
        return response.json() if response.content else {"started": experiment_id}

    def refresh_results(self, environment_key: str, experiment_id: int) -> bool:
        # path: Admin API (the MCP server has no refresh tool)
        """Ask Flagsmith to recompute results; False if it refreshed in the last 5 minutes."""
        url = (
            f"{self._base}/environments/{environment_key}/experiments/{experiment_id}"
            "/results/refresh/"
        )
        response = self._send("POST", url)
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            return False
        self._raise_for_status(response, f"refresh the results of experiment {experiment_id}")
        return True

    def delete_feature(self, project_id: int, feature_id: int) -> dict[str, Any]:  # path: Admin API
        """Delete one feature; a flag that is already gone counts as deleted."""
        url = f"{self._base}/projects/{project_id}/features/{feature_id}/"
        response = self._send("DELETE", url)
        if response.status_code == 404:
            return {"deleted": feature_id, "note": "already gone"}
        self._raise_for_status(response, f"delete flag {feature_id}")
        return {"deleted": feature_id}

    def _send(self, method: str, url: str) -> httpx.Response:
        """Send one authenticated request, turning network errors into FlagsmithError."""
        try:
            return self._client.request(method, url, headers=self._headers)
        except httpx.HTTPError as error:
            raise FlagsmithError(
                "Could not reach the Flagsmith Admin API.", detail=str(error)
            ) from error

    @staticmethod
    def _raise_for_status(response: httpx.Response, action: str) -> None:
        """Raise a friendly FlagsmithError for 4xx/5xx replies."""
        if response.status_code in (401, 403):
            raise FlagsmithError(
                "Flagsmith rejected FLAGSMITH_ADMIN_API_KEY. Use an Organisation API key.",
                detail=response.text[:500],
            )
        if response.status_code >= 400:
            raise FlagsmithError(
                f"Flagsmith could not {action} (HTTP {response.status_code}).",
                detail=response.text[:500],
            )
