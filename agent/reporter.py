"""Computes experiment statistics with scipy and asks the LLM to narrate them."""

from agent.models import ExperimentPlan, ExperimentResult


def analyse(plan: ExperimentPlan) -> ExperimentResult:
    """Collect per-variant outcomes and compute conversion rates and significance in code."""
    raise NotImplementedError("agent.reporter.analyse is implemented in a later step.")


def summarise(result: ExperimentResult) -> str:
    """Plain-English summary of ``result``; the LLM only describes the numbers it is given."""
    raise NotImplementedError("agent.reporter.summarise is implemented in a later step.")
