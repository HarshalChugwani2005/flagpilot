"""Experiment results: fetch counts from Flagsmith, compute statistics in Python, narrate.

All numbers (conversion per variant, absolute and relative lift, the two-proportion
z-test p-value) are computed in :mod:`agent.stats`. The LLM receives only those
computed, already-rounded numbers and writes prose around them.
"""

import json
from pathlib import Path
from typing import Any, Protocol

from agent.errors import FlagPilotError
from agent.llm import REPORTING_TEMPERATURE, LLMClient, get_llm
from agent.models import ExperimentPlan, ExperimentResult, VariantCounts
from agent.stats import analyse

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reporter.md"


class ReportError(FlagPilotError):
    """The summary could not be produced."""


class CountsSource(Protocol):
    """Anything that can return per-variant counts (implemented by FlagsmithClient)."""

    def get_variant_counts(self, flag_name: str, metric_event: str) -> dict[str, VariantCounts]:
        """Visitors and ``metric_event`` conversions per variant of ``flag_name``."""
        ...


def fetch_result(plan: ExperimentPlan, source: CountsSource) -> ExperimentResult:
    """Fetch the experiment's counts from Flagsmith and compute its statistics."""
    return analyse(plan, source.get_variant_counts(plan.flag_name, plan.metric_event))


def summary_payload(result: ExperimentResult) -> dict[str, Any]:
    """Return the computed numbers (rounded in code) and labels the LLM may use; nothing else."""
    return {
        "metric": result.metric_name,
        "verdict": result.verdict,
        "winner": result.winner,
        "control": result.control,
        "best_treatment": result.best_treatment,
        "variants": [
            {
                "name": variant.name,
                "visitors": variant.visitors,
                "conversions": variant.conversions,
                "conversion_rate_percent": round(variant.rate * 100, 2),
            }
            for variant in result.variants
        ],
        "absolute_lift_percentage_points": round(result.absolute_lift * 100, 2),
        "relative_lift_percent": (
            None if result.relative_lift is None else round(result.relative_lift * 100, 1)
        ),
        "p_value": round(result.p_value, 4),
        "significance_threshold": round(result.alpha, 4),
        "min_visitors_per_variant": result.min_sample_size,
    }


def summarise(result: ExperimentResult, *, llm: LLMClient | None = None) -> str:
    """Plain-English summary written by the LLM from :func:`summary_payload` only."""
    try:
        system = PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise ReportError(f"The reporter prompt is missing ({PROMPT_PATH}).") from error
    payload = json.dumps(summary_payload(result), indent=2)
    return (llm or get_llm()).complete_text(system, payload, temperature=REPORTING_TEMPERATURE)
