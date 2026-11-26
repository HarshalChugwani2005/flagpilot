"""Safety rules checked before a plan is proposed for approval and again before it runs."""

from agent.models import SAFE_ENVIRONMENT, ApprovalRecord, ExperimentPlan, PlannedCall

MAX_FIRST_RUN_TRAFFIC = 50
FLAG_PREFIX = "fp_"
FORBIDDEN_TOOL_PREFIXES = ("delete_",)


def check_plan(plan: ExperimentPlan, *, allow_production: bool, first_run: bool) -> list[str]:
    """Problems with the plan itself; an empty list means it may be proposed."""
    problems = []
    if plan.environment != SAFE_ENVIRONMENT and not allow_production:
        problems.append(
            f"Plans for {plan.environment!r} are blocked. Set ALLOW_PRODUCTION=1 in .env "
            "to allow them."
        )
    if first_run and plan.traffic_percent > MAX_FIRST_RUN_TRAFFIC:
        problems.append(
            f"A first run may use at most {MAX_FIRST_RUN_TRAFFIC}% of traffic; "
            f"this plan uses {plan.traffic_percent}%."
        )
    return problems


def check_changes(calls: list[PlannedCall]) -> list[str]:
    """Problems with the concrete Flagsmith calls: no deletions, only ``fp_`` flags."""
    problems = []
    for call in calls:
        if call.tool.startswith(FORBIDDEN_TOOL_PREFIXES):
            problems.append(f"FlagPilot never deletes anything in Flagsmith ({call.tool}).")
        name = call.arguments.get("name")
        if isinstance(name, str) and not name.startswith(FLAG_PREFIX):
            problems.append(f'Flag "{name}" must start with "{FLAG_PREFIX}".')
    return problems


def validate_plan(
    plan: ExperimentPlan,
    calls: list[PlannedCall],
    *,
    allow_production: bool,
    first_run: bool,
) -> list[str]:
    """All guardrail problems for ``plan`` and its Flagsmith calls."""
    return check_plan(plan, allow_production=allow_production, first_run=first_run) + (
        check_changes(calls)
    )


def check_approval(current_hash: str, approval: ApprovalRecord | None) -> list[str]:
    """Problems with the approval: missing, or for a different version of the plan."""
    if approval is None:
        return ["This plan has no approval record. Approve it first."]
    if approval.plan_hash != current_hash:
        return [
            f"The plan changed after {approval.approver} approved it "
            f"(approved {approval.plan_hash[:12]}, now {current_hash[:12]}). Approve it again."
        ]
    return []
