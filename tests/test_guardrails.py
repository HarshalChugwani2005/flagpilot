"""Every guardrail rule, with passing and failing cases."""

from datetime import UTC, datetime

import pytest

from agent.changes import build_changes
from agent.guardrails import (
    check_approval,
    check_environment,
    check_no_deletions,
    check_prefix,
    check_traffic,
    evaluate,
    validate_plan,
)
from agent.models import ApprovalRecord, PlannedCall, plan_hash
from tests.conftest import make_plan


def call(tool: str = "create_feature", name: str | None = "fp_x") -> PlannedCall:
    arguments = {} if name is None else {"name": name}
    return PlannedCall(tool=tool, arguments=arguments, summary=["x"])


# --- Environment ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "allow_production", "status"),
    [
        ("development", False, "pass"),
        ("development", True, "pass"),
        ("production", True, "warn"),
        ("production", False, "fail"),
    ],
)
def test_environment_rule(environment: str, allow_production: bool, status: str) -> None:
    check = check_environment(make_plan(environment=environment), allow_production=allow_production)

    assert check.status == status
    if status == "fail":
        assert "ALLOW_PRODUCTION=1" in check.message


# --- Traffic ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("traffic", "first_run", "status"),
    [
        (1, True, "pass"),
        (50, True, "pass"),
        (51, True, "fail"),
        (100, True, "fail"),
        (51, False, "pass"),
        (100, False, "pass"),
    ],
)
def test_traffic_rule(traffic: int, first_run: bool, status: str) -> None:
    check = check_traffic(make_plan(traffic_percent=traffic), first_run=first_run)

    assert check.status == status
    if status == "fail":
        assert "at most 50%" in check.message and f"{traffic}%" in check.message


# --- No deletions -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tools", "status"),
    [
        (["create_feature"], "pass"),
        (["create_feature", "update_environment_feature_state"], "pass"),
        (["delete_feature_segment"], "fail"),
        (["create_feature", "delete_feature_multivariate_option"], "fail"),
    ],
)
def test_no_deletions_rule(tools: list[str], status: str) -> None:
    check = check_no_deletions([call(tool) for tool in tools])

    assert check.status == status
    if status == "fail":
        assert "never delete anything" in check.message


# --- fp_ prefix -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "status"),
    [
        (["fp_checkout"], "pass"),
        ([None], "pass"),  # calls without a flag name are not affected
        (["checkout_button"], "fail"),
        (["fp_ok", "FP_shouting"], "fail"),
    ],
)
def test_prefix_rule(names: list[str | None], status: str) -> None:
    check = check_prefix([call(name=name) for name in names])

    assert check.status == status
    if status == "fail":
        assert 'must start with "fp_"' in check.message


# --- Approval ---------------------------------------------------------------------------


def approval_for(hash_: str) -> ApprovalRecord:
    return ApprovalRecord(approver="Asha", approved_at=datetime.now(UTC), plan_hash=hash_)


def test_approval_rule_passes_for_the_approved_plan() -> None:
    current = plan_hash(make_plan())

    assert check_approval(current, approval_for(current)) == []


def test_approval_rule_fails_without_an_approval() -> None:
    assert "no approval record" in check_approval(plan_hash(make_plan()), None)[0]


def test_approval_rule_fails_when_the_plan_changed() -> None:
    approved = plan_hash(make_plan())
    edited = plan_hash(make_plan(hypothesis="A different hypothesis."))

    [problem] = check_approval(edited, approval_for(approved))

    assert "changed after Asha approved it" in problem
    assert approved[:12] in problem and edited[:12] in problem


# --- All rules together -----------------------------------------------------------------


def test_a_planned_experiment_passes_every_rule() -> None:
    plan = make_plan()
    checks = evaluate(plan, build_changes(plan, 1), allow_production=False, first_run=True)

    assert [(check.rule, check.status) for check in checks] == [
        ("Environment", "pass"),
        ("Traffic", "pass"),
        ("No deletions", "pass"),
        ("Flag prefix", "pass"),
    ]


def test_validate_plan_returns_only_blocking_messages() -> None:
    plan = make_plan(environment="production", traffic_percent=80)

    problems = validate_plan(plan, build_changes(plan, 1), allow_production=False, first_run=True)

    assert len(problems) == 2
    assert validate_plan(plan, build_changes(plan, 1), allow_production=True, first_run=False) == []
