"""Translates an approved plan into the exact Flagsmith calls, shown verbatim for approval.

The calls follow Flagsmith's documented experimentation flow:

1. MCP ``create_feature``: a multivariate flag. The first variant is the control and
   becomes the flag's base value; the others become options with 0% default allocation,
   so identities outside the experiment see the control. Created switched off (features
   are project-wide, so this keeps every environment, production included, unchanged).
2. MCP ``list_feature_multivariate_options``: read back the option ids.
3. MCP ``create_metric``: ``{"aggregation": "occurrence", "direction": "up",
   "definition": {"version": 1, "event": <metric_event>}}``.
4. MCP ``create_experiment``: rollout ``traffic_percent``; inside it each treatment gets
   its split weight and the control the remainder.
5. Admin API ``.../experiments/<id>/start/`` (the MCP server cannot start experiments).

Values that only exist after an earlier call are written as ``$ref:<name>`` and filled
in by the executor; ``$ref:<name>:str`` passes the value as a string.
"""

import json

from agent.models import ExperimentPlan, PlannedCall

CREATE_FEATURE = "create_feature"
LIST_OPTIONS = "list_feature_multivariate_options"
CREATE_METRIC = "create_metric"
CREATE_EXPERIMENT = "create_experiment"
START_EXPERIMENT = "admin_api.start_experiment"
OUTSIDE = "not in experiment"
REF = "$ref:"
ENVIRONMENT_KEY_REF = f"{REF}environment_api_key"


def _compact(value: object) -> str:
    """JSON without spaces, as stored in Flagsmith."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


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
    """Build the multivariate flag: control as base value, options at 0% by default."""
    control, *treatments = plan.variants
    return PlannedCall(
        tool=CREATE_FEATURE,
        arguments={
            "project_pk": project_id,
            "name": plan.flag_name,
            "type": "MULTIVARIATE",
            "description": plan.hypothesis,
            "default_enabled": False,
            "initial_value": _compact(control.value),
            "multivariate_options": [
                {
                    "key": variant.name,
                    "type": "unicode",
                    "string_value": _compact(variant.value),
                    "default_percentage_allocation": 0,
                }
                for variant in treatments
            ],
        },
        summary=[
            f'Create multivariate flag "{plan.flag_name}" in Flagsmith project {project_id}, '
            "switched off in every environment",
            f'Set the control value "{control.name}" = {_compact(control.value)}',
            *(
                f'Add variant "{variant.name}" = {_compact(variant.value)}'
                for variant in treatments
            ),
        ],
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
            "metrics": [{"metric": f"{REF}create_metric.id:str", "expected_direction": "increase"}],
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
        PlannedCall(
            tool=LIST_OPTIONS,
            arguments={"feature_pk": f"{REF}create_feature.id", "project_pk": str(project_id)},
            summary=["Read back the variant option ids"],
        ),
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
