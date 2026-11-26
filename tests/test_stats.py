"""Tests for agent.stats (values checked against textbook formulas)."""

import pytest

from agent.models import VariantCounts
from agent.stats import StatsError, analyse, two_proportion_p_value, wilson_interval
from tests.conftest import make_plan


def counts(control: tuple[int, int], treatment: tuple[int, int]) -> dict[str, VariantCounts]:
    return {
        "control": VariantCounts(visitors=control[0], conversions=control[1]),
        "green_buy_now": VariantCounts(visitors=treatment[0], conversions=treatment[1]),
    }


def test_wilson_interval_known_value() -> None:
    low, high = wilson_interval(50, 100)

    assert low == pytest.approx(0.4038, abs=1e-4)
    assert high == pytest.approx(0.5962, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_two_proportion_p_value_known_value() -> None:
    p = two_proportion_p_value(
        VariantCounts(visitors=1000, conversions=100), VariantCounts(visitors=1000, conversions=130)
    )

    assert p == pytest.approx(0.0357, abs=1e-3)


def test_clear_winner() -> None:
    result = analyse(make_plan(min_sample_size=300), counts((1000, 100), (1000, 140)))

    assert result.verdict == "winner"
    assert result.winner == "green_buy_now"
    assert result.relative_lift == pytest.approx(0.4)
    assert result.absolute_lift == pytest.approx(0.04)
    assert result.visitors == 2000 and result.conversions == 240


def test_not_significant() -> None:
    result = analyse(make_plan(min_sample_size=300), counts((1000, 100), (1000, 105)))

    assert result.verdict == "not_significant"
    assert result.winner is None


def test_not_enough_data() -> None:
    result = analyse(make_plan(min_sample_size=500), counts((100, 10), (100, 40)))

    assert result.verdict == "not_enough_data"


def test_mismatched_variants_are_reported() -> None:
    with pytest.raises(StatsError, match="returned results"):
        analyse(make_plan(), {"control": VariantCounts(visitors=1, conversions=0)})


def test_conversions_cannot_exceed_visitors() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        VariantCounts(visitors=1, conversions=2)


# --- Known inputs -> known outputs (reference values computed by hand) -----------------
#
# control 100/1000 = 10%, treatment 130/1000 = 13%
# pooled p = 230/2000 = 0.115; SE = sqrt(0.115 * 0.885 * (1/1000 + 1/1000)) = 0.0142671
# z = 0.03 / 0.0142671 = 2.10274; two-sided p = 2 * (1 - Phi(2.10274)) = 0.035490


def test_reporter_numbers_match_hand_calculation() -> None:
    result = analyse(make_plan(min_sample_size=300), counts((1000, 100), (1000, 130)))

    assert result.absolute_lift == pytest.approx(0.03)
    assert result.relative_lift == pytest.approx(0.30)
    assert result.p_value == pytest.approx(0.035490, abs=1e-5)
    assert result.alpha == 0.05
    assert result.verdict == "winner" and result.winner == "green_buy_now"
    control, treatment = result.variants
    assert (control.rate, treatment.rate) == (pytest.approx(0.10), pytest.approx(0.13))


def test_several_treatments_use_bonferroni_correction() -> None:
    plan = make_plan(
        min_sample_size=300,
        variants=[
            {"name": "control", "value": {"label": "A"}},
            {"name": "b", "value": {"label": "B"}},
            {"name": "c", "value": {"label": "C"}},
        ],
        split={"control": 34, "b": 33, "c": 33},
    )
    result = analyse(
        plan,
        {
            "control": VariantCounts(visitors=1000, conversions=100),
            "b": VariantCounts(visitors=1000, conversions=130),
            "c": VariantCounts(visitors=1000, conversions=110),
        },
    )

    assert result.best_treatment == "b"
    assert result.alpha == pytest.approx(0.025)
    # p = 0.0355 would win with one treatment, but not against the corrected 0.025
    assert result.verdict == "not_significant"


def test_control_can_win() -> None:
    result = analyse(make_plan(min_sample_size=300), counts((1000, 150), (1000, 100)))

    assert result.verdict == "winner" and result.winner == "control"
    assert result.relative_lift == pytest.approx(-1 / 3)


def test_zero_control_rate_has_no_relative_lift() -> None:
    result = analyse(make_plan(min_sample_size=300), counts((1000, 0), (1000, 20)))

    assert result.relative_lift is None
    assert result.absolute_lift == pytest.approx(0.02)
