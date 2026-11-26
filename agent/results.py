"""Per-variant counts from Flagsmith's own experiment results (read-only).

1. MCP ``list_experiments`` (searched by flag name): the experiment and its metric id.
2. Admin API ``.../experiments/<id>/results/refresh/``: Flagsmith computes results only
   on request, at most once every 5 minutes (no MCP tool). Skipped once completed.
3. MCP ``get_experiment_results``: per metric, per variant ``n`` (identities exposed) and
   ``sum`` (for FlagPilot's occurrence metrics: identities that converted).

Flagsmith's payload shape is its ``ResultsSummary`` (api/experimentation/dataclasses.py).
FlagPilot reads only the raw counts and computes every statistic itself.
"""

from typing import Any

from agent import config
from agent.admin_api import AdminAPI
from agent.config import Settings
from agent.flagsmith_client import FlagsmithClient, FlagsmithError, results_of
from agent.models import VariantCounts

COMPLETED = "completed"


def _preview(value: Any) -> str:
    """Short text of a response for error details."""
    return str(value)[:500]


def find_experiment(response: Any, flag_name: str) -> dict[str, Any]:
    """Return the most recent experiment on ``flag_name`` from a ``list_experiments`` reply."""
    matches = [
        item
        for item in results_of(response)
        if isinstance(item.get("feature"), dict) and item["feature"].get("name") == flag_name
    ]
    if not matches:
        raise FlagsmithError(f'Flagsmith has no experiment on "{flag_name}".')
    return max(matches, key=lambda item: str(item.get("created_at", "")))


def metric_id_of(experiment: dict[str, Any]) -> int:
    """Return the id of the metric FlagPilot attached (it attaches exactly one)."""
    metrics = experiment.get("metrics")
    if not (isinstance(metrics, list) and metrics and isinstance(metrics[0], dict)):
        raise FlagsmithError(
            "This Flagsmith experiment has no metric attached.", detail=_preview(experiment)
        )
    return int(metrics[0]["metric"])


def counts_from_results(response: Any, metric_id: int) -> dict[str, VariantCounts]:
    """Return identities and conversions per variant key for ``metric_id``.

    Raises a friendly error while Flagsmith is still computing, or if it failed to.
    """
    results = response.get("results") if isinstance(response, dict) else None
    payload = results.get("payload") if isinstance(results, dict) else None
    if payload is None:
        if isinstance(results, dict) and results.get("last_error_at"):
            raise FlagsmithError(
                "Flagsmith could not compute this experiment's results. Check its warehouse "
                "connection (Environment Settings > Warehouse) and try again.",
                detail=_preview(results),
            )
        raise FlagsmithError(
            "Flagsmith is computing this experiment's results. Reload in a minute."
        )
    for metric in payload.get("metrics", []):
        if isinstance(metric, dict) and metric.get("metric_id") == metric_id:
            try:
                return {
                    name: VariantCounts(visitors=int(stats["n"]), conversions=round(stats["sum"]))
                    for name, stats in metric["variants"].items()
                }
            except (KeyError, TypeError, ValueError, AttributeError) as error:
                raise FlagsmithError(
                    "Flagsmith returned results FlagPilot could not read.",
                    detail=_preview(metric),
                ) from error
    raise FlagsmithError(
        "Flagsmith's results do not include this experiment's metric yet. Reload in a minute.",
        detail=_preview(payload),
    )


class FlagsmithResults:
    """Reads experiment counts from Flagsmith (implements reporter.CountsSource)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: FlagsmithClient | None = None,
        admin: AdminAPI | None = None,
    ) -> None:
        """Use the given clients (tests), else the shared MCP session and the Admin API."""
        self._settings = settings or config.get_settings()
        self._client = client or FlagsmithClient(self._settings)
        self._admin = admin or AdminAPI(self._settings)

    def get_variant_counts(self, flag_name: str, metric_event: str) -> dict[str, VariantCounts]:
        """Identities and ``metric_event`` conversions per variant of ``flag_name``."""
        key = self._settings.flagsmith_environment_key.get_secret_value()
        experiment = find_experiment(
            self._client.call_tool(
                "list_experiments", {"environment_api_key": key, "q": flag_name}
            ),
            flag_name,
        )
        if experiment.get("status") != COMPLETED:
            self._admin.refresh_results(key, int(experiment["id"]))
        return counts_from_results(
            self._client.call_tool(
                "get_experiment_results",
                {"environment_api_key": key, "experiment_id": experiment["id"]},
            ),
            metric_id_of(experiment),
        )
