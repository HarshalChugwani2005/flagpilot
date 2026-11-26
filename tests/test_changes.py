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


def test_calls_follow_flagsmiths_experiment_flow() -> None:
    assert [call.tool for call in build_changes(make_plan(), project_id=7)] == [
        "create_feature",
        "list_feature_multivariate_options",
        "create_metric",
        "create_experiment",
        "admin_api.start_experiment",
    ]


def test_create_feature_call_matches_plan() -> None:
    args = build_changes(make_plan(), project_id=7)[0].arguments

    assert args["project_pk"] == 7
    assert args["name"] == "fp_checkout_button_color"
    assert args["type"] == "MULTIVARIATE"
    assert args["default_enabled"] is False
    assert json.loads(args["initial_value"]) == {"color": "#6b7280", "label": "Add to cart"}
    [option] = args["multivariate_options"]
    assert option["key"] == "green_buy_now"
    assert json.loads(option["string_value"]) == {"color": "#16a34a", "label": "Buy now"}
    assert option["default_percentage_allocation"] == 0  # outside the experiment: control


def test_metric_and_experiment_use_documented_bodies() -> None:
    _, _, metric, experiment, start = build_changes(make_plan(traffic_percent=40), 7)

    assert metric.arguments["aggregation"] == "occurrence"
    assert metric.arguments["direction"] == "up"
    assert metric.arguments["definition"] == {"version": 1, "event": "purchase"}
    rollout = experiment.arguments["experiment_rollout"]
    assert rollout["enabled"] is True and rollout["rollout_percentage"] == 40
    assert rollout["multivariate_feature_state_values"] == [
        {"multivariate_feature_option": "$ref:option.green_buy_now", "percentage_allocation": 50}
    ]
    assert experiment.arguments["metrics"][0]["expected_direction"] == "increase"
    assert start.arguments["id"] == "$ref:create_experiment.id"


def test_summary_lists_each_operation() -> None:
    lines = [line for call in build_changes(make_plan(), project_id=7) for line in call.summary]

    assert lines[0].startswith('Create multivariate flag "fp_checkout_button_color"')
    assert 'Add variant "green_buy_now"' in lines[2]
    assert any(line.startswith('Create metric "Purchase conversion"') for line in lines)
    assert any("for 50% of visitors" in line for line in lines)
    assert any("50% of the experiment (25% of all identities)" in line for line in lines)
    assert lines[-1].startswith("Start the experiment")


def test_traffic_segments_show_visitors_outside_the_experiment() -> None:
    assert traffic_segments(make_plan(traffic_percent=50)) == {
        "control": 25.0,
        "green_buy_now": 25.0,
        OUTSIDE: 50.0,
    }
    assert OUTSIDE not in traffic_segments(make_plan(traffic_percent=100))
