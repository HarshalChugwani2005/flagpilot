"""Recording human decisions (approve / reject a plan, approve a rollout) in the audit log."""

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ValidationError

from agent.audit import AuditLog
from agent.errors import FlagPilotError
from agent.models import (
    ApprovalRecord,
    ExperimentPlan,
    ResetPlan,
    RolloutPlan,
    model_hash,
    plan_hash,
    utc_now,
)


class ApprovalError(FlagPilotError):
    """The approval could not be recorded."""


def record_plan(plan: ExperimentPlan, audit: AuditLog) -> None:
    """Log that the planner proposed ``plan`` (starts the prompt-to-live timer)."""
    audit.record(
        "planned",
        flag_name=plan.flag_name,
        plan_hash=plan_hash(plan),
        arguments={"plan": plan.model_dump(mode="json")},
    )


def _approve(
    subject: BaseModel,
    kind: str,
    flag_name: str,
    approver: str,
    audit: AuditLog,
    clock: Callable[[], datetime],
) -> ApprovalRecord:
    """Create an approval for exactly ``subject`` and write it to the audit log."""
    try:
        record = ApprovalRecord(
            approver=approver, approved_at=clock(), plan_hash=model_hash(subject)
        )
    except ValidationError as error:
        raise ApprovalError("Enter your name (up to 80 characters) to approve.") from error
    audit.record(
        "approved",
        timestamp=record.approved_at,
        flag_name=flag_name,
        plan_hash=record.plan_hash,
        approver=record.approver,
        arguments={kind: subject.model_dump(mode="json")},
    )
    return record


def approve(
    plan: ExperimentPlan,
    approver: str,
    audit: AuditLog,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> ApprovalRecord:
    """First approval: this exact experiment plan may be created in Flagsmith."""
    return _approve(plan, "plan", plan.flag_name, approver, audit, clock)


def approve_rollout(
    rollout: RolloutPlan,
    approver: str,
    audit: AuditLog,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> ApprovalRecord:
    """Second approval: this exact rollout may be applied in Flagsmith."""
    return _approve(rollout, "rollout", rollout.flag_name, approver, audit, clock)


def approve_reset(
    reset: ResetPlan,
    approver: str,
    audit: AuditLog,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> ApprovalRecord:
    """Approval to delete exactly these ``fp_`` flags from the demo project."""
    return _approve(reset, "reset", f"project {reset.project_id}", approver, audit, clock)


def reject(plan: ExperimentPlan, reviewer: str, audit: AuditLog, *, reason: str = "") -> None:
    """Record that ``reviewer`` rejected ``plan``; nothing is sent to Flagsmith."""
    name = reviewer.strip()
    if not name:
        raise ApprovalError("Enter your name to reject a plan.")
    audit.record(
        "rejected",
        flag_name=plan.flag_name,
        plan_hash=plan_hash(plan),
        approver=name,
        error=reason.strip() or None,
    )
