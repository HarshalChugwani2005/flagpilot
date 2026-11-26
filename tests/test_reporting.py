"""Tests for agent.reporter, agent.rollout and the rollout executor, plus the edit form."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from agent.approval import approve, approve_rollout
from agent.audit import AuditLog
from agent.executor import ExecutionError, roll_out_winner
from agent.experiments import list_experiments
from agent.llm import LLMClient
from agent.models import ExperimentResult, VariantCounts
from agent.reporter import fetch_result, summarise, summary_payload
from agent.rollout import RolloutError, build_rollout, describe_rollout, find_feature_state
from agent.stats import analyse
from tests.conftest import FakeChatClient, make_plan
from ui.plan_form import FormError, VariantFields, plan_from_form

ENV_KEY = "env-test"
WIN_COUNTS = {
    "control": VariantCounts(visitors=1000, conversions=100),
    "green_buy_now": VariantCounts(visitors=1000, conversions=140),
}


def winning_result() -> ExperimentResult:
    return analyse(make_plan(min_sample_size=100), WIN_COUNTS)


# --- Reporter ---------------------------------------------------------------------------


class Counts:
    def get_variant_counts(self, flag_name: str, metric_event: str) -> dict[str, VariantCounts]:
        assert (flag_name, metric_event) == ("fp_checkout_button_color", "purchase")
        return WIN_COUNTS


def test_fetch_result_computes_lifts_and_p_value() -> None:
    result = fetch_result(make_plan(min_sample_size=100), Counts())

    assert result.absolute_lift == pytest.approx(0.04)
    assert result.relative_lift == pytest.approx(0.4)
    assert result.p_value < 0.01
    assert result.winner == "green_buy_now"


def test_payload_holds_only_rounded_computed_numbers() -> None:
    payload = summary_payload(winning_result())

    assert payload["relative_lift_percent"] == 40.0
    assert payload["absolute_lift_percentage_points"] == 4.0
    assert payload["variants"][1]["conversion_rate_percent"] == 14.0
    assert set(payload) == {
        "metric",
        "verdict",
        "winner",
        "control",
        "best_treatment",
        "variants",
        "absolute_lift_percentage_points",
        "relative_lift_percent",
        "p_value",
        "significance_threshold",
        "min_visitors_per_variant",
    }


def test_summary_sends_only_the_payload(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue("green_buy_now wins.")
    result = winning_result()

    assert summarise(result, llm=make_llm()) == "green_buy_now wins."
    call = fake_chat.calls[0]
    assert call["temperature"] == 0.3
    assert "Never calculate" in call["messages"][0]["content"]
    assert json.loads(call["messages"][1]["content"]) == summary_payload(result)


# --- Rollout ----------------------------------------------------------------------------


class ScriptedFlagsmith:
    """Realistic replies for the rollout's reads; records the write."""

    def __init__(self, *, v2: bool = False) -> None:
        self.v2 = v2
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if name == "list_project_environments":
            return [
                {"id": 3, "name": "Production", "api_key": "prod-key"},
                {
                    "id": 4,
                    "name": "Development",
                    "api_key": ENV_KEY,
                    "use_v2_feature_versioning": self.v2,
                },
            ]
        if name == "list_project_features":
            return {
                "results": [
                    {
                        "id": 55,
                        "name": "fp_checkout_button_color",
                        "environment_feature_state": {"id": 901, "enabled": False},
                    }
                ]
            }
        if name == "list_feature_multivariate_options":
            return {"results": [{"id": 77, "key": "green_buy_now", "string_value": "{}"}]}
        if name == "update_environment_feature_state":
            return {"id": 901}
        raise AssertionError(f"unexpected tool {name}")


def live_experiment(audit: AuditLog) -> None:
    plan = make_plan(min_sample_size=100)
    approve(plan, "Asha", audit)
    audit.record("result", flag_name=plan.flag_name, tool="create_feature", result={"id": 55})


def run_rollout(audit: AuditLog, flagsmith: ScriptedFlagsmith, approval: Any = "approve") -> Any:
    rollout = build_rollout(make_plan(min_sample_size=100), winning_result())
    if approval == "approve":
        approval = approve_rollout(rollout, "Ben", audit)
    return roll_out_winner(
        rollout,
        approval,
        runner=flagsmith,
        audit=audit,
        project_id=7,
        environment_api_key=ENV_KEY,
    )


def test_build_rollout_marks_losers_stale() -> None:
    rollout = build_rollout(make_plan(), winning_result())

    assert (rollout.winner, rollout.stale_variants) == ("green_buy_now", ["control"])
    assert describe_rollout(rollout)[1] == 'Set "control" to 0% and record it as stale'


def test_build_rollout_needs_a_winner() -> None:
    no_winner = analyse(make_plan(min_sample_size=5000), WIN_COUNTS)

    with pytest.raises(RolloutError, match="no significant winner"):
        build_rollout(make_plan(), no_winner)


def test_rollout_sets_winner_to_100_and_records_stale(audit: AuditLog) -> None:
    live_experiment(audit)
    flagsmith = ScriptedFlagsmith()

    run_rollout(audit, flagsmith)

    write_name, write = flagsmith.calls[-1]
    assert write_name == "update_environment_feature_state"
    assert write == {
        "environment_api_key": ENV_KEY,
        "id__path": 901,
        "feature": 55,
        "multivariate_feature_state_values": [
            {"multivariate_feature_option": 77, "percentage_allocation": 100}
        ],
    }
    assert flagsmith.calls[1][1]["environment"] == 4
    events = [entry.event for entry in audit.entries()]
    assert events[-1] == "rolled_out" and events.count("call") == 4
    [experiment] = list_experiments(audit.entries())
    assert experiment.status == "Rolled out"
    assert experiment.stale_variants == ["control"]


def test_rollout_needs_its_own_approval(audit: AuditLog) -> None:
    live_experiment(audit)
    flagsmith = ScriptedFlagsmith()

    with pytest.raises(ExecutionError, match="no approval record"):
        run_rollout(audit, flagsmith, approval=None)
    assert flagsmith.calls == []


def test_rollout_refuses_flags_that_never_went_live(audit: AuditLog) -> None:
    with pytest.raises(ExecutionError, match="made live"):
        run_rollout(audit, ScriptedFlagsmith())


def test_rollout_only_once(audit: AuditLog) -> None:
    live_experiment(audit)
    run_rollout(audit, ScriptedFlagsmith())

    with pytest.raises(ExecutionError, match="already rolled out"):
        run_rollout(audit, ScriptedFlagsmith())


def test_rollout_refuses_v2_versioning_without_writing(audit: AuditLog) -> None:
    live_experiment(audit)
    flagsmith = ScriptedFlagsmith(v2=True)

    with pytest.raises(ExecutionError, match="v2 feature versioning"):
        run_rollout(audit, flagsmith)
    assert [name for name, _ in flagsmith.calls] == ["list_project_environments"]
    assert audit.entries()[-1].event == "error"


def test_unexpected_flagsmith_reply_is_explained() -> None:
    with pytest.raises(RolloutError, match="unexpected features response") as error:
        find_feature_state({"oops": 1}, "fp_a")
    assert error.value.detail == '{"oops": 1}'


# --- Edit form --------------------------------------------------------------------------


def form_fields(**changes: object) -> dict[str, object]:
    fields = make_plan().model_dump(exclude={"variants", "split"})
    fields["risks"] = "Risk one\n\n Risk two "
    return {**fields, **changes}


def form_variants(control_weight: int = 50, value: str = '{"label": "B"}') -> list[VariantFields]:
    return [
        VariantFields(name="control", weight=control_weight, value_json='{"label": "A"}'),
        VariantFields(name="green_buy_now", weight=50, value_json=value),
    ]


def test_form_round_trips_to_a_valid_plan() -> None:
    plan = plan_from_form(form_fields(traffic_percent=30), form_variants(), [])

    assert plan.traffic_percent == 30
    assert plan.risks == ["Risk one", "Risk two"]


def test_form_reports_bad_json_and_bad_weights() -> None:
    with pytest.raises(FormError) as bad_json:
        plan_from_form(form_fields(), form_variants(value="{nope"), [])
    assert "not valid JSON" in bad_json.value.problems[0]

    with pytest.raises(FormError) as bad_sum:
        plan_from_form(form_fields(), form_variants(control_weight=70), [])
    assert any("sum to 100" in problem for problem in bad_sum.value.problems)
