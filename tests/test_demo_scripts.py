"""Tests for scripts/reset_demo.py, scripts/seed_demo.py and the Admin API fallback."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import scripts.reset_demo as reset_demo
import scripts.seed_demo as seed_demo
from agent.admin_api import DELETE_FEATURE, AdminAPI
from agent.approval import approve_reset
from agent.audit import AuditLog
from agent.config import Settings
from agent.executor import ExecutionError, delete_demo_flags
from agent.flagsmith_client import FlagsmithError
from agent.models import DemoFlag, ResetPlan
from tests.conftest import FakeRunner, make_plan

FLAGS = [
    {"id": 1, "name": "fp_pricing_cta"},
    {"id": 2, "name": "checkout_button"},
    {"id": 3, "name": "fp_old_banner"},
]


class FakeFlagsmith(FakeRunner):
    """MCP reads for the scripts, plus recorded tool calls."""

    def __init__(self, flags: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.flags = FLAGS if flags is None else flags

    def get_project(self) -> dict[str, Any]:
        return {"id": 7, "name": "Demo Store"}

    def list_flags(self) -> list[dict[str, Any]]:
        return self.flags

    def list_flag_names(self) -> list[str]:
        return [flag["name"] for flag in self.flags]


@pytest.fixture
def cache_dir(settings: Settings) -> Path:
    settings.llm_cache_dir.mkdir(parents=True)
    for index in range(3):
        (settings.llm_cache_dir / f"{index}.json").write_text("{}", encoding="utf-8")
    return settings.llm_cache_dir


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path) -> dict:
    settings.llm_cache_dir = tmp_path / "llm_cache"
    flagsmith, admin = FakeFlagsmith(), FakeRunner()
    for module in (reset_demo, seed_demo):
        monkeypatch.setattr(module, "load_settings", lambda: settings)
        monkeypatch.setattr(module, "FlagsmithClient", lambda s: flagsmith)
    monkeypatch.setattr(reset_demo, "AdminAPI", lambda s: admin)
    monkeypatch.setattr(seed_demo, "AdminAPI", lambda s: flagsmith)
    return {"flagsmith": flagsmith, "admin": admin, "settings": settings}


# --- reset ------------------------------------------------------------------------------


def test_reset_lists_exactly_what_changes_and_cancels(
    patched: dict, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    AuditLog(patched["settings"].audit_log_path).record("planned", flag_name="fp_pricing_cta")

    reset_demo.main(input_fn=lambda prompt: "nope")

    out = capsys.readouterr().out
    assert 'project "Demo Store" (id 7)' in out
    assert "- fp_pricing_cta (id 1)" in out and "- fp_old_banner (id 3)" in out
    assert "checkout_button" not in out
    assert "(1 entries)" in out and "Delete 3 cached LLM replies" in out
    assert "Cancelled. Nothing was changed." in out
    assert patched["admin"].calls == []
    assert len(list(cache_dir.glob("*.json"))) == 3


def test_reset_deletes_backs_up_and_clears(patched: dict, cache_dir: Path) -> None:
    settings = patched["settings"]
    AuditLog(settings.audit_log_path).record("planned", flag_name="fp_pricing_cta")

    reset_demo.main(input_fn=lambda prompt: "Demo Store")

    assert patched["admin"].calls == [
        (DELETE_FEATURE, {"project_pk": 7, "id": 1}),
        (DELETE_FEATURE, {"project_pk": 7, "id": 3}),
    ]
    [backup] = (settings.audit_log_path.parent / "audit_backups").glob("*.jsonl")
    backed_up = [json.loads(line)["event"] for line in backup.read_text().splitlines()]
    assert backed_up[:2] == ["planned", "approved"] and backed_up.count("result") == 2
    [entry] = AuditLog(settings.audit_log_path).entries()
    assert entry.event == "reset" and entry.arguments is not None
    assert entry.arguments["deleted_flags"] == ["fp_pricing_cta", "fp_old_banner"]
    assert list(cache_dir.glob("*.json")) == []


def test_delete_refuses_flags_flagpilot_did_not_create(audit: AuditLog) -> None:
    reset = ResetPlan(
        project_id=7, project_name="Demo", flags=[DemoFlag(id=2, name="checkout_button")]
    )
    approval = approve_reset(reset, "asha", audit)
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="did not create"):
        delete_demo_flags(reset, approval, runner=runner, audit=audit)
    assert runner.calls == []


def test_delete_needs_an_approval(audit: AuditLog) -> None:
    reset = ResetPlan(project_id=7, project_name="Demo", flags=[DemoFlag(id=1, name="fp_a")])

    with pytest.raises(ExecutionError, match="no approval record"):
        delete_demo_flags(reset, None, runner=FakeRunner(), audit=audit)


# --- Admin API --------------------------------------------------------------------------


def admin(settings: Settings, status: int, seen: list[httpx.Request]) -> AdminAPI:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, text="{}")

    return AdminAPI(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_admin_delete_uses_documented_endpoint(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    result = admin(settings, 204, seen).call_tool(DELETE_FEATURE, {"project_pk": 7, "id": 5})

    assert result == {"deleted": 5}
    assert seen[0].method == "DELETE"
    assert str(seen[0].url) == "https://api.flagsmith.com/api/v1/projects/7/features/5/"
    assert seen[0].headers["Authorization"] == "Api-Key admin-test"


def test_admin_delete_errors_are_friendly(settings: Settings) -> None:
    with pytest.raises(FlagsmithError, match="FLAGSMITH_ADMIN_API_KEY"):
        admin(settings, 401, []).delete_feature(7, 5)
    assert admin(settings, 404, []).delete_feature(7, 5)["note"] == "already gone"


# --- seed -------------------------------------------------------------------------------


def seed_plan(**changes: Any) -> Any:
    return make_plan(
        flag_name="fp_pricing_cta",
        variants=[
            {"name": "control", "value": {"color": "#6b7280", "label": "Buy"}},
            {"name": "variant", "value": {"color": "#16a34a", "label": "Get it now"}},
        ],
        split={"control": 50, "variant": 50},
        **changes,
    )


def test_seed_creates_the_experiment_then_runs_traffic(
    patched: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched["flagsmith"].flags = []
    monkeypatch.setattr(seed_demo, "plan_experiment", lambda request, existing: seed_plan())
    traffic: list[list[str]] = []
    monkeypatch.setattr(seed_demo, "run_traffic", traffic.append)

    seed_demo.main(input_fn=lambda prompt: "fp_pricing_cta")

    assert [name for name, _ in patched["flagsmith"].calls] == [
        "create_feature",
        "list_feature_multivariate_options",
        "admin_api.create_metric",
        "create_experiment",
        "admin_api.start_experiment",
    ]
    assert traffic == [
        [
            "--flag",
            "fp_pricing_cta",
            "--visitors",
            "1500",
            "--rates",
            "control=0.08,variant=0.115",
            "--control",
            "control",
        ]
    ]
    events = [e.event for e in AuditLog(patched["settings"].audit_log_path).entries()]
    assert events == ["planned", "approved"] + ["call", "result"] * 5


def test_seed_refuses_when_the_flag_exists(patched: dict) -> None:
    with pytest.raises(SystemExit, match="Run make reset first"):
        seed_demo.main(input_fn=lambda prompt: "fp_pricing_cta")


def test_seed_checks_the_planners_names(patched: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    patched["flagsmith"].flags = []
    monkeypatch.setattr(
        seed_demo, "plan_experiment", lambda request, existing: make_plan(flag_name="fp_other")
    )

    with pytest.raises(SystemExit, match="Run make seed again"):
        seed_demo.main(input_fn=lambda prompt: "fp_pricing_cta")
    assert patched["flagsmith"].calls == []


def answers(*replies: str):
    """input() stand-in that returns ``replies`` in order."""
    iterator = iter(replies)
    return lambda prompt: next(iterator)


def test_reset_needs_an_approver_name(patched: dict, cache_dir: Path) -> None:
    reset_demo.main(input_fn=answers("Demo Store", "   "))

    assert patched["admin"].calls == []
    assert len(list(cache_dir.glob("*.json"))) == 3


def test_reset_records_the_named_approver(patched: dict, cache_dir: Path) -> None:
    reset_demo.main(input_fn=answers("Demo Store", "Asha"))

    backup = next((patched["settings"].audit_log_path.parent / "audit_backups").glob("*.jsonl"))
    approvals = [json.loads(line) for line in backup.read_text().splitlines()]
    assert approvals[0]["event"] == "approved" and approvals[0]["approver"] == "Asha"
