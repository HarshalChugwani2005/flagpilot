"""Tests for agent.experiments (status and time saved from the audit log)."""

from datetime import UTC, datetime, timedelta

import pytest

from agent.approval import approve, record_plan
from agent.audit import AuditLog, audit_rows
from agent.errors import ConfigError
from agent.experiments import (
    demo_flag_name,
    list_experiments,
    prompt_to_live_minutes,
    time_saved_minutes,
)
from tests.conftest import make_plan

START = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)


def clock_from(times: list[datetime]):
    iterator = iter(times)
    return lambda: next(iterator)


def test_draft_then_running(tmp_path) -> None:
    audit = AuditLog(
        tmp_path / "log.jsonl",
        clock=clock_from([START, START + timedelta(minutes=3)]),
    )
    plan = make_plan()
    approve(plan, "Asha", audit, clock=lambda: START + timedelta(minutes=1))

    [draft] = list_experiments(audit.entries())
    assert draft.status == "Draft" and draft.live_since is None

    audit.record("result", flag_name=plan.flag_name, tool="create_feature", result={"id": 1})
    [running] = list_experiments(audit.entries())
    assert running.status == "Running"


def test_dry_runs_do_not_make_an_experiment_live(audit: AuditLog) -> None:
    plan = make_plan()
    approve(plan, "Asha", audit)
    audit.record("result", flag_name=plan.flag_name, dry_run=True)

    assert list_experiments(audit.entries())[0].status == "Draft"


def test_time_saved_uses_measured_prompt_to_live_minutes(tmp_path) -> None:
    audit = AuditLog(
        tmp_path / "log.jsonl",
        clock=clock_from([START, START + timedelta(minutes=4)]),
    )
    plan = make_plan()
    record_plan(plan, audit)
    audit.record("result", flag_name=plan.flag_name)

    assert prompt_to_live_minutes(audit.entries()) == [pytest.approx(4.0)]
    assert time_saved_minutes(audit.entries(), manual_baseline=30) == pytest.approx(26.0)
    assert time_saved_minutes(audit.entries(), manual_baseline=2) == 0


def test_audit_rows_are_readable(audit: AuditLog) -> None:
    audit.record("call", flag_name="fp_a", tool="create_feature", dry_run=True)
    audit.record("error", flag_name="fp_a", tool="create_feature", error="Flagsmith said no")

    call, error = audit_rows(audit.entries())
    assert call["action"] == "Flagsmith call · create_feature (dry run)"
    assert call["result"] == "skipped (dry run)"
    assert error["result"] == "Flagsmith said no"
    assert set(call) == {"time", "action", "flag", "approver", "result"}


def test_demo_flag_prefers_setting_then_latest_live(audit: AuditLog) -> None:
    with pytest.raises(ConfigError, match="No FlagPilot experiment is live"):
        demo_flag_name(None, audit.entries())

    plan = make_plan()
    approve(plan, "Asha", audit)
    audit.record("result", flag_name=plan.flag_name)

    assert demo_flag_name(None, audit.entries()) == plan.flag_name
    assert demo_flag_name("checkout_button", audit.entries()) == "checkout_button"
