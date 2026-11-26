"""Safety checks applied before anything reaches Flagsmith."""

from agent.models import ApprovalRecord, ExperimentPlan


def validate_plan(plan: ExperimentPlan) -> list[str]:
    """Return human-readable problems with ``plan`` (empty list means it is safe to propose)."""
    raise NotImplementedError("agent.guardrails.validate_plan is implemented in a later step.")


def ensure_fp_prefix(flag_name: str) -> str:
    """Return ``flag_name`` with the mandatory ``fp_`` prefix applied."""
    raise NotImplementedError("agent.guardrails.ensure_fp_prefix is implemented in a later step.")


def check_approval(plan: ExperimentPlan, approval: ApprovalRecord | None) -> None:
    """Raise if ``approval`` is missing or does not cover exactly this ``plan``."""
    raise NotImplementedError("agent.guardrails.check_approval is implemented in a later step.")
