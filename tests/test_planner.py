"""Tests for agent.planner and the ExperimentPlan rules (LLM mocked)."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from agent.llm import LLMClient, LLMError
from agent.models import ClarifyingQuestions, ExperimentPlan
from agent.planner import PlanningError, build_system_prompt, plan_experiment
from tests.conftest import FakeChatClient

EXISTING = ["fp_old_banner", "checkout_button"]


def plan_json(**changes: Any) -> str:
    """A valid plan as the model would return it, with optional field overrides."""
    plan = {
        "flag_name": "fp_checkout_button_color",
        "hypothesis": "A green Buy now button will increase purchases because it stands out.",
        "variants": [
            {"name": "control", "value": {"color": "#6b7280", "label": "Add to cart"}},
            {"name": "green_buy_now", "value": {"color": "#16a34a", "label": "Buy now"}},
        ],
        "traffic_percent": 100,
        "split": {"control": 50, "green_buy_now": 50},
        "metric_name": "Purchase conversion",
        "metric_event": "purchase",
        "environment": "development",
        "min_sample_size": 200,
        "risks": ["Colour change may reduce contrast for some users."],
    }
    plan.update(changes)
    return json.dumps(plan)


def test_clear_request_returns_plan(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue(plan_json())

    result = plan_experiment(
        "Test a green 'Buy now' checkout button against the current one, measure purchases.",
        EXISTING,
        llm=make_llm(),
    )

    assert isinstance(result, ExperimentPlan)
    assert result.flag_name == "fp_checkout_button_color"
    assert result.split == {"control": 50, "green_buy_now": 50}
    call = fake_chat.calls[0]
    system = call["messages"][0]["content"]
    assert "- fp_old_banner" in system and "- checkout_button" in system
    assert "Output ONLY JSON matching this schema" in system
    assert "guided_json" in call["extra_body"]["nvext"]
    assert call["temperature"] == 0.0
    assert call["max_tokens"] == 1024


def test_vague_request_returns_questions(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue(json.dumps({"questions": ["Which page?", "What counts as success?"]}))

    result = plan_experiment("make the site better", EXISTING, llm=make_llm())

    assert isinstance(result, ClarifyingQuestions)
    assert result.questions == ["Which page?", "What counts as success?"]


def test_production_request_stays_in_development(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue(plan_json(environment="production"))

    result = plan_experiment("Run the button test straight in production", [], llm=make_llm())

    assert isinstance(result, ExperimentPlan)
    assert result.environment == "development"
    assert any("'production'" in risk for risk in result.risks)


def test_weights_not_summing_to_100_are_rejected() -> None:
    with pytest.raises(ValidationError, match="sum to 100"):
        ExperimentPlan.model_validate_json(plan_json(split={"control": 60, "green_buy_now": 60}))


def test_bad_weights_twice_give_friendly_error(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    bad = plan_json(split={"control": 70, "green_buy_now": 20})
    fake_chat.queue(bad, bad)

    with pytest.raises(LLMError) as excinfo:
        plan_experiment("Test a green button", [], llm=make_llm())

    assert "rephrasing" in excinfo.value.friendly
    retry_prompt = fake_chat.calls[1]["messages"][-1]["content"]
    assert "sum to 100" in retry_prompt


def test_duplicate_flag_name_is_retried_with_the_error(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue(plan_json(flag_name="fp_old_banner"), plan_json(flag_name="fp_new_banner"))

    result = plan_experiment("Test a new banner", EXISTING, llm=make_llm())

    assert isinstance(result, ExperimentPlan)
    assert result.flag_name == "fp_new_banner"
    assert "already exists" in fake_chat.calls[1]["messages"][-1]["content"]


def test_duplicate_flag_name_is_case_insensitive() -> None:
    with pytest.raises(ValidationError, match="already exists"):
        ExperimentPlan.model_validate_json(
            plan_json(flag_name="fp_old_banner"),
            context={"existing_flag_names": ["FP_OLD_BANNER"]},
        )


def test_fenced_output_is_still_parsed(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue(f"```json\n{plan_json()}\n```")

    result = plan_experiment("Test a green button", [], llm=make_llm())

    assert isinstance(result, ExperimentPlan)
    assert len(fake_chat.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"flag_name": "checkout_color"},
        {"flag_name": "fp_CheckoutColor"},
        {"flag_name": "fp_" + "x" * 40},
        {"traffic_percent": 0},
        {"traffic_percent": 101},
        {"variants": [{"name": "only", "value": {"a": 1}}], "split": {"only": 100}},
        {"split": {"control": 50, "other": 50}},
        {"metric_event": "Purchase Event"},
        {"risks": ["  "]},
    ],
)
def test_plan_rules_are_enforced(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate_json(plan_json(**changes))


def test_blank_request_is_refused_without_calling_llm(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    with pytest.raises(PlanningError):
        plan_experiment("   ", [], llm=make_llm())
    assert fake_chat.calls == []


def test_prompt_without_existing_flags() -> None:
    assert "(none yet)" in build_system_prompt([])
