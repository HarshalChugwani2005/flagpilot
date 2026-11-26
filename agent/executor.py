"""The single gateway for Flagsmith writes. Every write needs an approval and is audited."""

from collections.abc import Callable
from typing import Any, Protocol

from agent.admin_api import DELETE_FEATURE
from agent.audit import AuditLog
from agent.changes import build_changes
from agent.errors import FlagPilotError
from agent.guardrails import FLAG_PREFIX, check_approval, validate_plan
from agent.models import (
    SAFE_ENVIRONMENT,
    ApprovalRecord,
    CallOutcome,
    ExecutionResult,
    ExperimentPlan,
    PlannedCall,
    ResetPlan,
    RolloutPlan,
    model_hash,
    plan_hash,
)
from agent.rollout import (
    RolloutError,
    find_environment,
    find_feature_state,
    option_ids,
    rollout_allocations,
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


def _run_call(
    call: PlannedCall, runner: ToolRunner | None, audit: AuditLog, context: dict[str, Any]
) -> CallOutcome:
    """Send one call to Flagsmith and log its result or error."""
    assert runner is not None  # execute_plan refuses real runs without a runner
    try:
        result = runner.call_tool(call.tool, call.arguments)
    except Exception as error:
        message = error.friendly if isinstance(error, FlagPilotError) else str(error)
        audit.record("error", tool=call.tool, error=message, **context)
        raise ExecutionError(f"Flagsmith call {call.tool} failed: {message}") from error
    audit.record("result", tool=call.tool, result=result, **context)
    return CallOutcome(tool=call.tool, ok=True, dry_run=False, result=result)


def execute_plan(
    plan: ExperimentPlan,
    approval: ApprovalRecord | None,
    *,
    runner: ToolRunner | None,
    audit: AuditLog,
    project_id: int,
    allow_production: bool = False,
    dry_run: bool = False,
    on_call: Callable[[PlannedCall, CallOutcome], None] | None = None,
) -> ExecutionResult:
    """Apply ``plan`` in Flagsmith, logging every call and result to the audit log.

    Guardrails always run. A real run additionally needs an approval whose hash matches
    the plan *now* and which is present in the audit log. ``dry_run`` logs the calls it
    would make without contacting Flagsmith (and needs no approval). ``on_call`` is told
    about each finished call, so a UI can show progress.
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
            outcome = CallOutcome(tool=call.tool, ok=True, dry_run=True)
        else:
            outcome = _run_call(call, runner, audit, context)
        outcomes.append(outcome)
        if on_call:
            on_call(call, outcome)

    return ExecutionResult(
        flag_name=plan.flag_name, plan_hash=current_hash, dry_run=dry_run, outcomes=outcomes
    )


def _rollout_problems(
    rollout: RolloutPlan,
    approval: ApprovalRecord | None,
    audit: AuditLog,
    *,
    runner: ToolRunner | None,
    allow_production: bool,
) -> list[str]:
    """Reasons the rollout may not run (empty when it may)."""
    problems = []
    if rollout.environment != SAFE_ENVIRONMENT and not allow_production:
        problems.append(f"Rollouts in {rollout.environment!r} need ALLOW_PRODUCTION=1.")
    if not audit.has_live_run(rollout.flag_name):
        problems.append("Only experiments FlagPilot made live in Flagsmith can be rolled out.")
    if audit.rollout_of(rollout.flag_name) is not None:
        problems.append(f"{rollout.flag_name} was already rolled out.")
    problems += check_approval(model_hash(rollout), approval)
    if approval is not None and not problems and not audit.has_approval(approval):
        problems.append("The rollout approval is not in the audit log. Approve it again.")
    if runner is None:
        problems.append("No Flagsmith connection was provided.")
    return problems


def roll_out_winner(
    rollout: RolloutPlan,
    approval: ApprovalRecord | None,
    *,
    runner: ToolRunner | None,
    audit: AuditLog,
    project_id: int,
    environment_api_key: str,
    allow_production: bool = False,
    on_call: Callable[[PlannedCall, CallOutcome], None] | None = None,
) -> ExecutionResult:
    """Second approval executed: winner to 100%, the other variants recorded as stale.

    Needs an approval of exactly this ``rollout`` (hash checked, present in the audit
    log). Every Flagsmith read and write is logged; see :mod:`agent.rollout`.
    """
    current_hash = model_hash(rollout)
    problems = _rollout_problems(
        rollout, approval, audit, runner=runner, allow_production=allow_production
    )
    if problems:
        audit.record(
            "refused",
            flag_name=rollout.flag_name,
            plan_hash=current_hash,
            error="; ".join(problems),
        )
        raise ExecutionError("FlagPilot did not roll out: " + " ".join(problems))

    assert approval is not None and runner is not None  # checked above
    context = {
        "flag_name": rollout.flag_name,
        "plan_hash": current_hash,
        "approver": approval.approver,
    }
    outcomes: list[CallOutcome] = []

    def call(tool: str, arguments: dict[str, Any], summary: str) -> Any:
        planned = PlannedCall(tool=tool, arguments=arguments, summary=[summary])
        audit.record("call", tool=tool, arguments=arguments, **context)
        outcome = _run_call(planned, runner, audit, context)
        outcomes.append(outcome)
        if on_call:
            on_call(planned, outcome)
        return outcome.result

    try:
        environment = find_environment(
            call("list_project_environments", {"id": project_id}, "Find the environment"),
            environment_api_key,
        )
        if environment.uses_v2_versioning:
            raise RolloutError(
                f'Environment "{environment.name}" uses v2 feature versioning, which FlagPilot '
                "does not support yet. Nothing was changed."
            )
        state = find_feature_state(
            call(
                "list_project_features",
                {
                    "project_pk": project_id,
                    "environment": environment.id,
                    "search": rollout.flag_name,
                },
                f'Find "{rollout.flag_name}" in {environment.name}',
            ),
            rollout.flag_name,
        )
        ids = option_ids(
            call(
                "list_feature_multivariate_options",
                {"feature_pk": state.feature_id, "project_pk": str(project_id)},
                "Read the variant options",
            )
        )
        call(
            "update_environment_feature_state",
            {
                "environment_api_key": environment_api_key,
                "id__path": state.feature_state_id,
                "feature": state.feature_id,
                "multivariate_feature_state_values": rollout_allocations(rollout, ids),
            },
            f'Send 100% of identities to "{rollout.winner}"',
        )
    except RolloutError as error:
        audit.record("error", error=error.friendly, **context)
        raise ExecutionError(error.friendly, detail=error.detail) from error

    audit.record(
        "rolled_out",
        arguments={"winner": rollout.winner, "stale_variants": rollout.stale_variants},
        **context,
    )
    return ExecutionResult(
        flag_name=rollout.flag_name, plan_hash=current_hash, dry_run=False, outcomes=outcomes
    )


def delete_demo_flags(
    reset: ResetPlan,
    approval: ApprovalRecord | None,
    *,
    runner: ToolRunner,
    audit: AuditLog,
    on_call: Callable[[PlannedCall, CallOutcome], None] | None = None,
) -> ExecutionResult:
    """Delete the demo project's ``fp_`` flags listed in ``reset`` (``make reset`` only).

    Needs an approval of exactly this ``reset``. Refuses any flag without the ``fp_``
    prefix, so flags FlagPilot did not create are never touched. Every call is logged.
    """
    current_hash = model_hash(reset)
    problems = check_approval(current_hash, approval)
    if approval is not None and not problems and not audit.has_approval(approval):
        problems.append("The reset approval is not in the audit log.")
    outsiders = [flag.name for flag in reset.flags if not flag.name.startswith(FLAG_PREFIX)]
    if outsiders:
        problems.append(f"Refusing to delete flags FlagPilot did not create: {outsiders}.")
    scope = f"project {reset.project_id}"
    if problems:
        audit.record("refused", flag_name=scope, plan_hash=current_hash, error="; ".join(problems))
        raise ExecutionError("FlagPilot did not reset the demo: " + " ".join(problems))

    assert approval is not None  # checked above
    outcomes = []
    for flag in reset.flags:
        context = {"flag_name": flag.name, "plan_hash": current_hash, "approver": approval.approver}
        call = PlannedCall(
            tool=DELETE_FEATURE,
            arguments={"project_pk": reset.project_id, "id": flag.id},
            summary=[f'Delete flag "{flag.name}" (id {flag.id})'],
        )
        audit.record("call", tool=call.tool, arguments=call.arguments, **context)
        outcome = _run_call(call, runner, audit, context)
        outcomes.append(outcome)
        if on_call:
            on_call(call, outcome)
    return ExecutionResult(
        flag_name=scope, plan_hash=current_hash, dry_run=False, outcomes=outcomes
    )
