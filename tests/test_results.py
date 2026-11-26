"""Tests for agent.results: reading counts from Flagsmith's experiment results."""

from typing import Any

import httpx
import pytest

from agent.admin_api import AdminAPI
from agent.config import Settings
from agent.flagsmith_client import FlagsmithError
from agent.models import VariantCounts
from agent.reporter import ReportError, fetch_result
from agent.results import FlagsmithResults, counts_from_results, find_experiment
from tests.conftest import make_plan

FLAG = "fp_checkout_button_color"


def experiment(**changes: Any) -> dict[str, Any]:
    """One item of a real ``list_experiments`` reply (trimmed)."""
    return {
        "id": 708,
        "feature": {"id": 306159, "name": FLAG},
        "status": "running",
        "metrics": [{"id": 802, "metric": 712, "metric_name": "purchases"}],
        "created_at": "2026-11-26T18:36:29.231686Z",
        **changes,
    }


def results(variants: dict[str, dict[str, float]] | None) -> dict[str, Any]:
    """A ``get_experiment_results`` reply shaped like Flagsmith's ResultsSummary."""
    if variants is None:
        return {"results": None}
    return {
        "results": {
            "as_of": "2026-11-26T19:00:48.443720Z",
            "last_error_at": None,
            "payload": {
                "metrics": [
                    {"metric_id": 712, "variants": variants, "inference": {}},
                ],
                "srm_p_value": None,
            },
            "is_final": False,
        }
    }


REAL_VARIANTS = {
    "control": {"n": 54, "sum": 4.0, "sum_squares": 4.0},
    "green_buy_now": {"n": 49, "sum": 7.0, "sum_squares": 7.0},
}


class FakeClient:
    """Answers the two MCP reads."""

    def __init__(self, listed: list[dict[str, Any]], reply: dict[str, Any]) -> None:
        self.listed, self.reply = listed, reply
        self.calls: list[str] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(name)
        if name == "list_experiments":
            assert arguments["q"] == FLAG
            return {"count": len(self.listed), "results": self.listed}
        assert name == "get_experiment_results" and arguments["experiment_id"] == 708
        return self.reply


class FakeAdmin:
    """Records refresh requests."""

    def __init__(self) -> None:
        self.refreshed: list[int] = []

    def refresh_results(self, environment_key: str, experiment_id: int) -> bool:
        self.refreshed.append(experiment_id)
        return True


def reader(settings: Settings, client: FakeClient, admin: FakeAdmin) -> FlagsmithResults:
    return FlagsmithResults(settings, client=client, admin=admin)  # type: ignore[arg-type]


def test_counts_come_from_n_and_sum(settings: Settings) -> None:
    client, admin = FakeClient([experiment()], results(REAL_VARIANTS)), FakeAdmin()

    counts = reader(settings, client, admin).get_variant_counts(FLAG, "purchase")

    assert counts == {
        "control": VariantCounts(visitors=54, conversions=4),
        "green_buy_now": VariantCounts(visitors=49, conversions=7),
    }
    assert admin.refreshed == [708]
    assert client.calls == ["list_experiments", "get_experiment_results"]


def test_completed_experiment_is_not_refreshed(settings: Settings) -> None:
    client = FakeClient([experiment(status="completed")], results(REAL_VARIANTS))
    admin = FakeAdmin()

    reader(settings, client, admin).get_variant_counts(FLAG, "purchase")

    assert admin.refreshed == []


def test_not_computed_yet_is_a_friendly_error(settings: Settings) -> None:
    client, admin = FakeClient([experiment()], results(None)), FakeAdmin()

    with pytest.raises(FlagsmithError, match="Reload in a minute"):
        reader(settings, client, admin).get_variant_counts(FLAG, "purchase")


def test_failed_computation_points_at_the_warehouse() -> None:
    reply = {"results": {"payload": None, "last_error_at": "2026-11-26T19:00:00Z"}}

    with pytest.raises(FlagsmithError, match="warehouse"):
        counts_from_results(reply, 712)


def test_missing_metric_is_reported() -> None:
    with pytest.raises(FlagsmithError, match="metric"):
        counts_from_results(results(REAL_VARIANTS), 999)


def test_latest_experiment_on_the_flag_wins() -> None:
    old = experiment(id=1, created_at="2026-11-01T00:00:00Z")
    other = experiment(id=2, feature={"name": "fp_other"}, created_at="2026-12-01T00:00:00Z")

    assert find_experiment({"results": [old, experiment(), other]}, FLAG)["id"] == 708


def test_no_experiment_is_reported() -> None:
    with pytest.raises(FlagsmithError, match="no experiment"):
        find_experiment({"results": []}, FLAG)


def test_variant_without_visitors_gets_a_clear_message(settings: Settings) -> None:
    only_control = {"control": REAL_VARIANTS["control"]}
    source = reader(settings, FakeClient([experiment()], results(only_control)), FakeAdmin())

    with pytest.raises(ReportError, match="not recorded any visitors for green_buy_now"):
        fetch_result(make_plan(), source)


def test_refresh_posts_to_the_documented_path(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    admin = AdminAPI(settings, client=httpx.Client(transport=httpx.MockTransport(handle)))

    assert admin.refresh_results("env-test", 708) is True
    assert seen[0].method == "POST"
    assert seen[0].url.path.endswith("/environments/env-test/experiments/708/results/refresh/")


def test_throttled_refresh_keeps_the_last_results(settings: Settings) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    admin = AdminAPI(settings, client=httpx.Client(transport=transport))

    assert admin.refresh_results("env-test", 708) is False
