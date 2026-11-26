"""The single gateway for Flagsmith writes. Every write needs an approval and is audited."""

from typing import Any, Protocol

from agent.audit import AuditLog
from agent.changes import build_changes
from agent.errors import FlagPilotError
from agent.guardrails import check_approval, validate_plan
from agent.models import (
    ApprovalRecord,
    CallOutcome,
    ExecutionResult,
    ExperimentPlan,
    ExperimentResult,
    plan_hash,
)


class ToolRunner(Protocol):
    """Anything that can run a Flagsmith MCP tool (implemented by FlagsmithClient)."""

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run MCP tool ``name`` with ``arguments`` and return its result."""
        ...


class ExecutionError(FlagPilotError):
    """The executor refused to run a plan, or a Flagsmith call failed."""


def _refuse(audit: AuditLog, plan: ExperimentPlan, current_hash: str, problems: list[str]) -> None:
    """Log a refusal and raise it as a friendly error."""
    audit.record(
        "refused", flag_name=plan.flag_name, plan_hash=current_hash, error="; ".join(problems)
    )
    raise ExecutionError("FlagPilot did not run this plan: " + " ".join(problems))


def execute_plan(
    plan: ExperimentPlan,
    approval: ApprovalRecord | None,
    *,
    runner: ToolRunner | None,
    audit: AuditLog,
    project_id: int,
    allow_production: bool = False,
    dry_run: bool = False,
) -> ExecutionResult:
    """Apply ``plan`` in Flagsmith, logging every call and result to the audit log.

    Guardrails always run. A real run additionally needs an approval whose hash matches
    the plan *now* and which is present in the audit log. ``dry_run`` logs the calls it
    would make without contacting Flagsmith (and needs no approval).
    """
    current_hash = plan_hash(plan)
    calls = build_changes(plan, project_id)
    problems = validate_plan(
        plan,
        calls,
        allow_production=allow_production,
        first_run=not audit.has_live_run(plan.flag_name),
    )
    if not dry_run:
        problems += check_approval(current_hash, approval)
        if approval is not None and not problems and not audit.has_approval(approval):
            problems.append("The approval is not in the audit log. Approve the plan again.")
        if runner is None:
            problems.append("No Flagsmith connection was provided.")
    if problems:
        _refuse(audit, plan, current_hash, problems)

    approver = approval.approver if approval else None
    context = {"flag_name": plan.flag_name, "plan_hash": current_hash, "approver": approver}
    outcomes = []
    for call in calls:
        audit.record("call", tool=call.tool, arguments=call.arguments, dry_run=dry_run, **context)
        if dry_run:
            outcomes.append(CallOutcome(tool=call.tool, ok=True, dry_run=True))
            continue
        assert runner is not None  # checked above
        try:
            result = runner.call_tool(call.tool, call.arguments)
        except Exception as error:
            message = error.friendly if isinstance(error, FlagPilotError) else str(error)
            audit.record("error", tool=call.tool, error=message, **context)
            raise ExecutionError(f"Flagsmith call {call.tool} failed: {message}") from error
        audit.record("result", tool=call.tool, result=result, **context)
        outcomes.append(CallOutcome(tool=call.tool, ok=True, dry_run=False, result=result))

    return ExecutionResult(
        flag_name=plan.flag_name, plan_hash=current_hash, dry_run=dry_run, outcomes=outcomes
    )


def roll_out_winner(result: ExperimentResult, approval: ApprovalRecord) -> ExecutionResult:
    """Send 100% of traffic to the winning variant and append the write to the audit log."""
    raise NotImplementedError("agent.executor.roll_out_winner is implemented in a later step.")
