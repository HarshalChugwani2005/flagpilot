"""Experiment statistics with scipy: Wilson intervals and a two-proportion z-test.

The best treatment is compared with the control; with several treatments the
significance level is Bonferroni-corrected. The LLM never computes any of this.
"""

import math
from collections.abc import Mapping

from scipy.stats import norm

from agent.errors import FlagPilotError
from agent.models import ExperimentPlan, ExperimentResult, VariantCounts, VariantStats

ALPHA = 0.05
Z_95 = float(norm.ppf(0.975))


class StatsError(FlagPilotError):
    """The counts do not fit the experiment."""


def wilson_interval(conversions: int, visitors: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a conversion rate (``(0, 0)`` with no visitors)."""
    if visitors == 0:
        return 0.0, 0.0
    rate = conversions / visitors
    denominator = 1 + z**2 / visitors
    centre = (rate + z**2 / (2 * visitors)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / visitors + z**2 / (4 * visitors**2)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def two_proportion_p_value(control: VariantCounts, treatment: VariantCounts) -> float:
    """Two-sided p-value of the pooled two-proportion z-test (1.0 when undefined)."""
    if control.visitors == 0 or treatment.visitors == 0:
        return 1.0
    pooled = (control.conversions + treatment.conversions) / (control.visitors + treatment.visitors)
    variance = pooled * (1 - pooled) * (1 / control.visitors + 1 / treatment.visitors)
    if variance == 0:
        return 1.0
    difference = treatment.conversions / treatment.visitors - control.conversions / control.visitors
    return float(2 * norm.sf(abs(difference) / math.sqrt(variance)))


def _variant_stats(name: str, counts: VariantCounts) -> VariantStats:
    """Rate and interval for one variant."""
    low, high = wilson_interval(counts.conversions, counts.visitors)
    rate = counts.conversions / counts.visitors if counts.visitors else 0.0
    return VariantStats(
        name=name,
        visitors=counts.visitors,
        conversions=counts.conversions,
        rate=rate,
        ci_low=low,
        ci_high=high,
    )


def analyse(plan: ExperimentPlan, counts: Mapping[str, VariantCounts]) -> ExperimentResult:
    """Compute rates, intervals, lift, p-value and the verdict for ``plan``."""
    names = [variant.name for variant in plan.variants]
    if set(counts) != set(names):
        raise StatsError(
            f"Flagsmith returned results for {sorted(counts)}, but the experiment has "
            f"{sorted(names)}."
        )
    control, *treatments = names
    stats = [_variant_stats(name, counts[name]) for name in names]
    by_name = {item.name: item for item in stats}
    best = max(treatments, key=lambda name: by_name[name].rate)
    p_value = two_proportion_p_value(counts[control], counts[best])
    alpha = ALPHA / len(treatments)
    control_rate = by_name[control].rate
    lift = (by_name[best].rate - control_rate) / control_rate if control_rate else None

    winner: str | None = None
    if any(item.visitors < plan.min_sample_size for item in stats):
        verdict = "not_enough_data"
    elif p_value < alpha:
        verdict = "winner"
        winner = best if by_name[best].rate > control_rate else control
    else:
        verdict = "not_significant"
    return ExperimentResult(
        flag_name=plan.flag_name,
        metric_name=plan.metric_name,
        control=control,
        variants=stats,
        best_treatment=best,
        p_value=p_value,
        alpha=alpha,
        absolute_lift=by_name[best].rate - control_rate,
        relative_lift=lift,
        min_sample_size=plan.min_sample_size,
        verdict=verdict,
        winner=winner,
    )
