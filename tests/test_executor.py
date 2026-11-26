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

    result = execute_plan(
        plan,
        approval,
        runner=runner,
        admin=runner,
        audit=audit,
        project_id=7,
        environment_api_key="env-test",
    )

    assert [name for name, _ in runner.calls] == [
        "create_feature",
        "list_feature_multivariate_options",
        "admin_api.create_metric",
        "create_experiment",
        "admin_api.start_experiment",
    ]
    assert all(outcome.ok for outcome in result.outcomes) and not result.dry_run
    assert events(audit) == ["approved"] + ["call", "result"] * 5
    call_entry, result_entry = audit.entries()[1:3]
    assert call_entry.arguments is not None and call_entry.arguments["name"] == plan.flag_name
    assert result_entry.result["id"] == 99 and result_entry.result["name"] == plan.flag_name
    assert result_entry.approver == "Asha"


def test_dry_run_sends_nothing_and_needs_no_approval(audit: AuditLog) -> None:
    runner = FakeRunner()

    result = execute_plan(
        make_plan(),
        None,
        runner=runner,
        admin=runner,
        audit=audit,
        project_id=7,
        environment_api_key="env-test",
        dry_run=True,
    )

    assert runner.calls == []
    assert result.dry_run and all(outcome.dry_run for outcome in result.outcomes)
    assert events(audit) == ["call"] * 5
    assert all(entry.dry_run for entry in audit.entries())


def test_refuses_without_approval(audit: AuditLog) -> None:
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="no approval record"):
        execute_plan(
            make_plan(),
            None,
            runner=runner,
            admin=runner,
            audit=audit,
            project_id=7,
            environment_api_key="env-test",
        )

    assert runner.calls == []
    assert events(audit) == ["refused"]


def test_refuses_if_plan_changed_after_approval(audit: AuditLog) -> None:
    approval = approve(make_plan(), "Asha", audit)
    edited = make_plan(traffic_percent=30)
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="changed after Asha approved it"):
        execute_plan(
            edited,
            approval,
            runner=runner,
            admin=runner,
            audit=audit,
            project_id=7,
            environment_api_key="env-test",
        )

    assert runner.calls == []
    assert events(audit) == ["approved", "refused"]


def test_refuses_an_approval_missing_from_the_audit_log(audit: AuditLog) -> None:
    plan = make_plan()
    forged = ApprovalRecord(
        approver="Mallory", approved_at=datetime.now(UTC), plan_hash=plan_hash(plan)
    )

    with pytest.raises(ExecutionError, match="not in the audit log"):
        execute_plan(
            plan,
            forged,
            runner=FakeRunner(),
            admin=FakeRunner(),
            audit=audit,
            project_id=7,
            environment_api_key="env-test",
        )


def test_guardrails_apply_even_when_approved(audit: AuditLog) -> None:
    plan = make_plan(traffic_percent=80)
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="at most 50%"):
        execute_plan(
            plan,
            approval,
            runner=runner,
            admin=runner,
            audit=audit,
            project_id=7,
            environment_api_key="env-test",
        )
    assert runner.calls == []


def test_flagsmith_failure_is_logged_and_reported(audit: AuditLog) -> None:
    plan = make_plan()
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner(error=FlagPilotError("Flagsmith said no."))

    with pytest.raises(ExecutionError, match="create_feature failed: Flagsmith said no."):
        execute_plan(
            plan,
            approval,
            runner=runner,
            admin=runner,
            audit=audit,
            project_id=7,
            environment_api_key="env-test",
        )

    assert events(audit) == ["approved", "call", "error"]
    assert audit.entries()[-1].error == "Flagsmith said no."


def test_dry_run_makes_no_flagsmith_calls_even_with_a_runner(audit: AuditLog) -> None:
    plan = make_plan()
    approval = approve(plan, "Asha", audit)
    runner = FakeRunner()

    result = execute_plan(
        plan,
        approval,
        runner=runner,
        admin=runner,
        audit=audit,
        project_id=7,
        environment_api_key="env-test",
        dry_run=True,
    )

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
        execute_plan(
            make_plan(),
            other,
            runner=runner,
            admin=runner,
            audit=audit,
            project_id=7,
            environment_api_key="env-test",
        )
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


def run_live(plan, audit, runner):
    return execute_plan(
        plan,
        approve(plan, "Asha", audit),
        runner=runner,
        admin=runner,
        audit=audit,
        project_id=7,
        environment_api_key="env-test",
    )


def test_references_are_filled_from_earlier_replies(audit: AuditLog) -> None:
    runner = FakeRunner()

    run_live(make_plan(traffic_percent=40), audit, runner)

    sent = dict(runner.calls)
    assert sent["list_feature_multivariate_options"] == {"feature_pk": 99, "project_pk": "7"}
    metric = sent["admin_api.create_metric"]
    assert metric["definition"] == {"version": 1, "event": "purchase"}
    assert metric["environment_api_key"] == "env-test"
    experiment = sent["create_experiment"]
    assert experiment["feature"] == 99
    assert experiment["metrics"] == [{"metric": "31", "expected_direction": "increase"}]
    rollout = experiment["experiment_rollout"]
    assert rollout["rollout_percentage"] == 40
    assert rollout["multivariate_feature_state_values"] == [
        {"multivariate_feature_option": 500, "percentage_allocation": 50}
    ]
    assert sent["admin_api.start_experiment"] == {"environment_api_key": "env-test", "id": 41}


def test_missing_reply_id_stops_before_the_next_call(audit: AuditLog) -> None:
    class NoIds(FakeRunner):
        def call_tool(self, name, arguments):
            super().call_tool(name, arguments)
            return {}

    runner = NoIds()

    with pytest.raises(ExecutionError, match="did not include create_feature.id"):
        run_live(make_plan(), audit, runner)
    assert [name for name, _ in runner.calls] == ["create_feature"]
    assert events(audit)[-1] == "error"


def test_real_run_needs_the_admin_api_and_environment_key(audit: AuditLog) -> None:
    plan = make_plan()
    runner = FakeRunner()

    with pytest.raises(ExecutionError, match="Admin API connection") as error:
        execute_plan(plan, approve(plan, "Asha", audit), runner=runner, audit=audit, project_id=7)
    assert "FLAGSMITH_ENVIRONMENT_KEY" in error.value.friendly
    assert runner.calls == []


def test_options_are_matched_by_value_because_flagsmith_drops_the_key(audit: AuditLog) -> None:
    runner = FakeRunner()  # answers like Flagsmith: option "key" comes back null

    run_live(make_plan(), audit, runner)

    experiment = dict(runner.calls)["create_experiment"]
    [allocation] = experiment["experiment_rollout"]["multivariate_feature_state_values"]
    assert allocation["multivariate_feature_option"] == 500
