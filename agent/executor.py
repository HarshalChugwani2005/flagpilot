"""The single gateway for Flagsmith writes. Every write needs an approval and is audited."""

from agent.models import ApprovalRecord, ExecutionResult, ExperimentPlan, ExperimentResult


def execute_plan(plan: ExperimentPlan, approval: ApprovalRecord) -> ExecutionResult:
    """Create the experiment flag in Flagsmith and append the write to ``audit_log.jsonl``."""
    raise NotImplementedError("agent.executor.execute_plan is implemented in a later step.")


def roll_out_winner(result: ExperimentResult, approval: ApprovalRecord) -> ExecutionResult:
    """Send 100% of traffic to the winning variant and append the write to the audit log."""
    raise NotImplementedError("agent.executor.roll_out_winner is implemented in a later step.")
