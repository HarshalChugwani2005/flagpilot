"""Tests for agent.changes: plan -> exact Flagsmith MCP calls -> diff."""

import json

from agent.changes import OUTSIDE, build_changes, traffic_segments, variant_allocations
from tests.conftest import make_plan


def test_allocations_scale_treatments_by_traffic() -> None:
    plan = make_plan(traffic_percent=50)

    assert variant_allocations(plan) == {"control": 75.0, "green_buy_now": 25.0}


def test_allocations_with_three_variants_sum_to_100() -> None:
    plan = make_plan(
        traffic_percent=30,
        variants=[
            {"name": "control", "value": {"label": "A"}},
            {"name": "b", "value": {"label": "B"}},
            {"name": "c", "value": {"label": "C"}},
        ],
        split={"control": 34, "b": 33, "c": 33},
    )

    allocations = variant_allocations(plan)

    assert allocations == {"control": 80.2, "b": 9.9, "c": 9.9}
    assert round(sum(allocations.values()), 2) == 100


def test_create_feature_call_matches_plan() -> None:
    [call] = build_changes(make_plan(), project_id=7)

    assert call.tool == "create_feature"
    args = call.arguments
    assert args["project_pk"] == 7
    assert args["name"] == "fp_checkout_button_color"
    assert args["type"] == "MULTIVARIATE"
    assert args["default_enabled"] is False
    assert json.loads(args["initial_value"]) == {"color": "#6b7280", "label": "Add to cart"}
    [option] = args["multivariate_options"]
    assert option["key"] == "green_buy_now"
    assert json.loads(option["string_value"]) == {"color": "#16a34a", "label": "Buy now"}
    assert option["default_percentage_allocation"] == 25.0


def test_summary_lists_each_operation() -> None:
    [call] = build_changes(make_plan(), project_id=7)

    create, control, variant = call.summary
    assert create.startswith('Create multivariate flag "fp_checkout_button_color"')
    assert "switched off in every environment" in create
    assert (
        control.startswith('Set the control value "control"') and "(75% of identities)" in control
    )
    assert variant.startswith('Add variant "green_buy_now"') and "(25% of identities)" in variant


def test_traffic_segments_show_visitors_outside_the_experiment() -> None:
    assert traffic_segments(make_plan(traffic_percent=50)) == {
        "control": 25.0,
        "green_buy_now": 25.0,
        OUTSIDE: 50.0,
    }
    assert OUTSIDE not in traffic_segments(make_plan(traffic_percent=100))
