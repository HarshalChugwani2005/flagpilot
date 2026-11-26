"""Tests for agent.executor: approval gating, hash check, dry run and audit logging."""

from datetime import UTC, datetime

import pytest

from agent.approval import approve
from agent.audit import AuditLog
from agent.errors import FlagPilotError
from agent.executor import ExecutionError, execute_plan
from agent.models import ApprovalRecord, plan_hash
from tests.conftest import FakeRunner, make_plan


def events(audit: AuditLog) -> list[str]:
    return [entry.event for entry in audit.entries()]


def test_approved_plan_runs_and_every_call_is_logged(audit: AuditLog) -> None:
    plan = make_plan()
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner()

    result = execute_plan(plan, approval, runner=runner, audit=audit, project_id=7)

    assert [name for name, _ in runner.calls] == ["create_feature"]
    assert result.outcomes[0].ok and not result.dry_run
    assert events(audit) == ["approved", "call", "result"]
    call_entry, result_entry = audit.entries()[1:]
    assert call_entry.arguments is not None and call_entry.arguments["name"] == plan.flag_name
    assert result_entry.result == {"id": 99, "name": plan.flag_name}
    assert result_entry.approver == "Asha"


def test_dry_run_sends_nothing_and_needs_no_approval(audit: AuditLog) -> None:
    runner = FakeRunner()

    result = execute_plan(make_plan(), None, runner=runner, audit=audit, project_id=7, dry_run=True)

    assert runner.calls == []
    assert result.dry_run and result.outcomes[0].dry_run
    assert events(audit) == ["call"]
    assert audit.entries()[0].dry_run


def test_refuses_without_approval(audit: AuditLog) -> None:
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="no approval record"):
        execute_plan(make_plan(), None, runner=runner, audit=audit, project_id=7)

    assert runner.calls == []
    assert events(audit) == ["refused"]


def test_refuses_if_plan_changed_after_approval(audit: AuditLog) -> None:
    approval = approve(make_plan(), "Asha", audit)
    edited = make_plan(traffic_percent=30)
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="changed after Asha approved it"):
        execute_plan(edited, approval, runner=runner, audit=audit, project_id=7)

    assert runner.calls == []
    assert events(audit) == ["approved", "refused"]


def test_refuses_an_approval_missing_from_the_audit_log(audit: AuditLog) -> None:
    plan = make_plan()
    forged = ApprovalRecord(
        approver="Mallory", approved_at=datetime.now(UTC), plan_hash=plan_hash(plan)
    )

    with pytest.raises(ExecutionError, match="not in the audit log"):
        execute_plan(plan, forged, runner=FakeRunner(), audit=audit, project_id=7)


def test_guardrails_apply_even_when_approved(audit: AuditLog) -> None:
    plan = make_plan(traffic_percent=80)
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="at most 50%"):
        execute_plan(plan, approval, runner=runner, audit=audit, project_id=7)
    assert runner.calls == []


def test_flagsmith_failure_is_logged_and_reported(audit: AuditLog) -> None:
    plan = make_plan()
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner(error=FlagPilotError("Flagsmith said no."))

    with pytest.raises(ExecutionError, match="create_feature failed: Flagsmith said no."):
        execute_plan(plan, approval, runner=runner, audit=audit, project_id=7)

    assert events(audit) == ["approved", "call", "error"]
    assert audit.entries()[-1].error == "Flagsmith said no."


def test_dry_run_makes_no_flagsmith_calls_even_with_a_runner(audit: AuditLog) -> None:
    plan = make_plan()
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner()

    result = execute_plan(plan, approval, runner=runner, audit=audit, project_id=7, dry_run=True)

    assert runner.calls == []
    assert all(outcome.dry_run and outcome.result is None for outcome in result.outcomes)
    assert "result" not in events(audit)
    assert not audit.has_live_run(plan.flag_name)


def test_dry_run_still_enforces_guardrails(audit: AuditLog) -> None:
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="at most 50%"):
        execute_plan(
            make_plan(traffic_percent=90),
            None,
            runner=runner,
            audit=audit,
            project_id=7,
            dry_run=True,
        )
    assert runner.calls == []
    assert events(audit) == ["refused"]


def test_refuses_an_approval_of_a_different_plan(audit: AuditLog) -> None:
    other = approve(make_plan(flag_name="fp_something_else"), "Asha", audit)
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="changed after Asha approved it"):
        execute_plan(make_plan(), other, runner=runner, audit=audit, project_id=7)
    assert runner.calls == []


@pytest.mark.parametrize(
    "edit",
    [
        {"traffic_percent": 49},
        {"split": {"control": 60, "green_buy_now": 40}},
        {
            "variants": [
                {"name": "control", "value": {"color": "#6b7280", "label": "Add to cart"}},
                {"name": "green_buy_now", "value": {"color": "#ff0000", "label": "Buy now"}},
            ]
        },
        {"environment": "production"},
    ],
)
def test_any_edit_after_approval_is_refused(audit: AuditLog, edit: dict) -> None:
    approval = approve(make_plan(), "Asha", audit)
    runner = FakeRunner()

    with pytest.raises(ExecutionError):
        execute_plan(
            make_plan(**edit),
            approval,
            runner=runner,
            audit=audit,
            project_id=7,
            allow_production=True,
        )
    assert runner.calls == []
