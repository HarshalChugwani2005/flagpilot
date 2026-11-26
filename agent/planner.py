"""Turns a plain-English request into a structured :class:`ExperimentPlan`."""

from agent.models import ExperimentPlan


def plan_experiment(request: str) -> ExperimentPlan:
    """Build an experiment plan for ``request`` using the prompt in ``prompts/planner.md``."""
    raise NotImplementedError("agent.planner.plan_experiment is implemented in a later step.")
