"""Tests for agent.audit and agent.approval."""

import json

import pytest

from agent.approval import ApprovalError, approve, reject
from agent.audit import AuditLog, AuditLogError
from agent.models import plan_hash
from tests.conftest import make_plan


def test_secrets_are_redacted(audit: AuditLog) -> None:
    audit.record(
        "call",
        tool="get_experiment_results",
        arguments={"environment_api_key": "secret-key", "nested": [{"token": "t"}], "id": 3},
    )

    line = audit.path.read_text(encoding="utf-8")
    assert "secret-key" not in line and '"t"' not in line
    [entry] = audit.entries()
    assert entry.arguments == {"environment_api_key": "***", "nested": [{"token": "***"}], "id": 3}


def test_entries_round_trip_and_missing_file_is_empty(audit: AuditLog) -> None:
    assert audit.entries() == []
    audit.record("rejected", flag_name="fp_a", approver="Asha")

    assert [entry.event for entry in audit.entries()] == ["rejected"]
    assert json.loads(audit.path.read_text(encoding="utf-8"))["flag_name"] == "fp_a"


def test_corrupted_line_gives_friendly_error(audit: AuditLog) -> None:
    audit.path.write_text("not json\n", encoding="utf-8")

    with pytest.raises(AuditLogError, match="Line 1"):
        audit.entries()


def test_live_run_ignores_dry_runs(audit: AuditLog) -> None:
    audit.record("result", flag_name="fp_a", dry_run=True)
    assert not audit.has_live_run("fp_a")

    audit.record("result", flag_name="fp_a")
    assert audit.has_live_run("fp_a")


def test_approve_records_the_plan_hash(audit: AuditLog) -> None:
    plan = make_plan()

    approval = approve(plan, "  Asha  ", audit)

    assert approval.approver == "Asha"
    assert approval.plan_hash == plan_hash(plan)
    [entry] = audit.entries()
    assert entry.event == "approved"
    assert entry.plan_hash == approval.plan_hash
    assert audit.has_approval(approval)


def test_approve_needs_a_name(audit: AuditLog) -> None:
    with pytest.raises(ApprovalError, match="Enter your name"):
        approve(make_plan(), "   ", audit)
    assert audit.entries() == []


def test_reject_is_logged(audit: AuditLog) -> None:
    reject(make_plan(), "Asha", audit, reason="Too risky before the sale")

    [entry] = audit.entries()
    assert (entry.event, entry.approver, entry.error) == (
        "rejected",
        "Asha",
        "Too risky before the sale",
    )
