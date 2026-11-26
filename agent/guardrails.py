"""Safety rules checked before a plan is proposed for approval and again before it runs."""

from typing import Literal

from pydantic import BaseModel

from agent.models import SAFE_ENVIRONMENT, ApprovalRecord, ExperimentPlan, PlannedCall

MAX_FIRST_RUN_TRAFFIC = 50
FLAG_PREFIX = "fp_"
FORBIDDEN_TOOL_PREFIXES = ("delete_",)


class GuardrailCheck(BaseModel):
    """Outcome of one rule: ``pass`` (green), ``warn`` (amber) or ``fail`` (red, blocks)."""

    rule: str
    status: Literal["pass", "warn", "fail"]
    message: str


def check_environment(plan: ExperimentPlan, *, allow_production: bool) -> GuardrailCheck:
    """Production is blocked unless ALLOW_PRODUCTION=1, and flagged amber even then."""
    if plan.environment == SAFE_ENVIRONMENT:
        return GuardrailCheck(rule="Environment", status="pass", message="Development only")
    if allow_production:
        return GuardrailCheck(
            rule="Environment",
            status="warn",
            message=f"Targets {plan.environment} (allowed by ALLOW_PRODUCTION=1)",
        )
    return GuardrailCheck(
        rule="Environment",
        status="fail",
        message=f"Plans for {plan.environment!r} are blocked. Set ALLOW_PRODUCTION=1 in .env "
        "to allow them.",
    )


def check_traffic(plan: ExperimentPlan, *, first_run: bool) -> GuardrailCheck:
    """Allow at most 50% of traffic on a flag's first live run."""
    if first_run and plan.traffic_percent > MAX_FIRST_RUN_TRAFFIC:
        return GuardrailCheck(
            rule="Traffic",
            status="fail",
            message=f"A first run may use at most {MAX_FIRST_RUN_TRAFFIC}% of traffic; "
            f"this plan uses {plan.traffic_percent}%.",
        )
    return GuardrailCheck(rule="Traffic", status="pass", message=f"{plan.traffic_percent}%")


def check_no_deletions(calls: list[PlannedCall]) -> GuardrailCheck:
    """FlagPilot never deletes anything in Flagsmith."""
    deleting = [call.tool for call in calls if call.tool.startswith(FORBIDDEN_TOOL_PREFIXES)]
    if deleting:
        return GuardrailCheck(
            rule="No deletions",
            status="fail",
            message=f"Experiment plans never delete anything in Flagsmith ({', '.join(deleting)}).",
        )
    return GuardrailCheck(rule="No deletions", status="pass", message="Nothing is deleted")


def check_prefix(calls: list[PlannedCall]) -> GuardrailCheck:
    """Every flag FlagPilot creates starts with ``fp_``."""
    names = [call.arguments.get("name") for call in calls if call.tool == "create_feature"]
    bad = [name for name in names if isinstance(name, str) and not name.startswith(FLAG_PREFIX)]
    if bad:
        return GuardrailCheck(
            rule="Flag prefix",
            status="fail",
            message=f'Flag "{bad[0]}" must start with "{FLAG_PREFIX}".',
        )
    return GuardrailCheck(rule="Flag prefix", status="pass", message=f"{FLAG_PREFIX} prefix")


def evaluate(
    plan: ExperimentPlan,
    calls: list[PlannedCall],
    *,
    allow_production: bool,
    first_run: bool,
) -> list[GuardrailCheck]:
    """Every rule's outcome for ``plan`` and its Flagsmith calls."""
    return [
        check_environment(plan, allow_production=allow_production),
        check_traffic(plan, first_run=first_run),
        check_no_deletions(calls),
        check_prefix(calls),
    ]


def validate_plan(
    plan: ExperimentPlan,
    calls: list[PlannedCall],
    *,
    allow_production: bool,
    first_run: bool,
) -> list[str]:
    """Messages of the rules that block ``plan`` (empty means it may run)."""
    checks = evaluate(plan, calls, allow_production=allow_production, first_run=first_run)
    return [check.message for check in checks if check.status == "fail"]


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
