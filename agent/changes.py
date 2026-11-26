"""Translates an approved plan into the exact Flagsmith calls, shown verbatim for approval.

The calls follow Flagsmith's documented experimentation flow:

1. MCP ``create_feature``: a multivariate flag whose base value is the control. Created
   switched off (features are project-wide, so this keeps every environment, production
   included, unchanged).
2. MCP ``create_feature_multivariate_option``, once per other variant, with ``key`` = the
   variant name and 0% default allocation (identities outside the experiment see the
   control). Flagsmith only accepts ``key`` on this endpoint, not inline in
   ``create_feature``, and an option without a key records no exposures.
3. Admin API ``.../experiment-metrics/`` (the MCP ``create_metric`` tool drops the
   required ``definition``): ``{"aggregation": "occurrence", "direction": "up",
   "definition": {"version": 1, "event": <metric_event>}}``.
4. MCP ``create_experiment``: rollout ``traffic_percent``; inside it each treatment gets
   its split weight and the control the remainder.
5. Admin API ``.../experiments/<id>/start/`` (the MCP server cannot start experiments).

Values that only exist after an earlier call are written as ``$ref:<name>`` and filled
in by the executor; ``$ref:<name>:str`` passes the value as a string.
"""

import json
from typing import Any

from agent.models import ExperimentPlan, PlannedCall, Variant

CREATE_FEATURE = "create_feature"
CREATE_OPTION = "create_feature_multivariate_option"
CREATE_METRIC = "admin_api.create_metric"
CREATE_EXPERIMENT = "create_experiment"
START_EXPERIMENT = "admin_api.start_experiment"
OUTSIDE = "not in experiment"
REF = "$ref:"
ENVIRONMENT_KEY_REF = f"{REF}environment_api_key"


def _compact(value: object) -> str:
    """JSON without spaces, as stored in Flagsmith."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def option_values(plan: ExperimentPlan) -> dict[str, str]:
    """Return each non-control variant's stored value (compact JSON), as sent to Flagsmith."""
    return {variant.name: _compact(variant.value) for variant in plan.variants[1:]}


def match_options(options: list[dict[str, Any]], values: dict[str, str]) -> dict[str, int]:
    """Map variant name -> Flagsmith option id by stored value.

    Matching on ``string_value`` (unique per variant in a plan) also works for flags
    created before FlagPilot set option keys, which Flagsmith stored as null.
    """
    ids_by_value = {
        option["string_value"]: option["id"]
        for option in options
        if isinstance(option.get("string_value"), str) and isinstance(option.get("id"), int)
    }
    return {name: ids_by_value[value] for name, value in values.items() if value in ids_by_value}


def variant_allocations(plan: ExperimentPlan) -> dict[str, float]:
    """Percentage of all identities that each variant is served (sums to 100)."""
    control, *treatments = plan.variants
    shares = {
        variant.name: round(plan.split[variant.name] * plan.traffic_percent / 100, 2)
        for variant in treatments
    }
    return {control.name: round(100 - sum(shares.values()), 2), **shares}


def traffic_segments(plan: ExperimentPlan) -> dict[str, float]:
    """Share of all visitors per variant inside the experiment, plus everyone outside it."""
    segments = {
        variant.name: round(plan.split[variant.name] * plan.traffic_percent / 100, 2)
        for variant in plan.variants
    }
    outside = round(100 - plan.traffic_percent, 2)
    return {**segments, OUTSIDE: outside} if outside else segments


def _create_feature(plan: ExperimentPlan, project_id: int) -> PlannedCall:
    """Build the multivariate flag with the control as its base value."""
    control = plan.variants[0]
    return PlannedCall(
        tool=CREATE_FEATURE,
        arguments={
            "project_pk": project_id,
            "name": plan.flag_name,
            "type": "MULTIVARIATE",
            "description": plan.hypothesis,
            "default_enabled": False,
            "initial_value": _compact(control.value),
        },
        summary=[
            f'Create multivariate flag "{plan.flag_name}" in Flagsmith project {project_id}, '
            "switched off in every environment",
            f'Set the control value "{control.name}" = {_compact(control.value)}',
        ],
    )


def _create_option(variant: Variant, project_id: int) -> PlannedCall:
    """Add one variant as a keyed option, 0% outside the experiment."""
    return PlannedCall(
        tool=CREATE_OPTION,
        arguments={
            "feature_pk": f"{REF}create_feature.id",
            "project_pk": str(project_id),
            "feature": f"{REF}create_feature.id",  # Flagsmith also requires it in the body
            "key": variant.name,
            "type": "unicode",
            "string_value": _compact(variant.value),
            "default_percentage_allocation": 0,
        },
        summary=[f'Add variant "{variant.name}" = {_compact(variant.value)}'],
    )


def _create_experiment(plan: ExperimentPlan) -> PlannedCall:
    """Build the Flagsmith experiment: rollout share, per-variant split and the metric."""
    control, *treatments = plan.variants
    allocations = variant_allocations(plan)
    return PlannedCall(
        tool=CREATE_EXPERIMENT,
        arguments={
            "environment_api_key": ENVIRONMENT_KEY_REF,
            "feature": f"{REF}create_feature.id",
            "name": plan.flag_name,
            "hypothesis": plan.hypothesis,
            "metrics": [
                {"metric": f"{REF}admin_api.create_metric.id:str", "expected_direction": "increase"}
            ],
            "experiment_rollout": {
                "enabled": True,
                "rollout_percentage": plan.traffic_percent,
                "feature_state_value": {"type": "string", "value": _compact(control.value)},
                "multivariate_feature_state_values": [
                    {
                        "multivariate_feature_option": f"{REF}option.{variant.name}",
                        "percentage_allocation": plan.split[variant.name],
                    }
                    for variant in treatments
                ],
            },
        },
        summary=[
            f'Create a Flagsmith experiment on "{plan.flag_name}" for '
            f"{plan.traffic_percent}% of visitors",
            *(
                f'Set weights: "{variant.name}" {plan.split[variant.name]}% of the experiment '
                f"({allocations[variant.name]:g}% of all identities)"
                for variant in plan.variants
            ),
        ],
    )


def build_changes(plan: ExperimentPlan, project_id: int) -> list[PlannedCall]:
    """Return every Flagsmith call that implements ``plan`` in project ``project_id``."""
    return [
        _create_feature(plan, project_id),
        *(_create_option(variant, project_id) for variant in plan.variants[1:]),
        PlannedCall(
            tool=CREATE_METRIC,
            arguments={
                "environment_api_key": ENVIRONMENT_KEY_REF,
                "name": f"{plan.metric_name} ({plan.flag_name})",
                "aggregation": "occurrence",
                "direction": "up",
                "definition": {"version": 1, "event": plan.metric_event},
            },
            summary=[
                f'Create metric "{plan.metric_name}": visitors who send the '
                f'"{plan.metric_event}" event (higher is better)'
            ],
        ),
        _create_experiment(plan),
        PlannedCall(
            tool=START_EXPERIMENT,
            arguments={
                "environment_api_key": ENVIRONMENT_KEY_REF,
                "id": f"{REF}create_experiment.id",
            },
            summary=["Start the experiment (Flagsmith Admin API; MCP cannot start experiments)"],
        ),
    ]
