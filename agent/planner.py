"""Turns a plain-English request into an :class:`ExperimentPlan` or clarifying questions."""

from collections.abc import Collection
from pathlib import Path

from agent.errors import FlagPilotError
from agent.llm import PLANNING_TEMPERATURE, LLMClient, get_llm
from agent.models import (
    MAX_RISKS,
    SAFE_ENVIRONMENT,
    ClarifyingQuestions,
    ExperimentPlan,
    PlannerOutput,
)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "planner.md"
FLAGS_PLACEHOLDER = "{existing_flags}"


class PlanningError(FlagPilotError):
    """The request could not be turned into a plan."""


def build_system_prompt(existing_flag_names: Collection[str], path: Path = PROMPT_PATH) -> str:
    """Render ``prompts/planner.md`` with the project's existing flag names.

    The ``{schema}`` placeholder is filled in by :meth:`LLMClient.complete_json`.
    """
    try:
        template = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PlanningError(f"The planner prompt is missing ({path}).") from error
    names = sorted(existing_flag_names)
    flags = ", ".join(names) if names else "(none yet)"
    return template.replace(FLAGS_PLACEHOLDER, flags)


def plan_experiment(
    request: str,
    existing_flag_names: Collection[str],
    *,
    llm: LLMClient | None = None,
) -> ExperimentPlan | ClarifyingQuestions:
    """Plan an experiment for ``request``, avoiding ``existing_flag_names`` from Flagsmith."""
    text = request.strip()
    if not text:
        raise PlanningError("Describe the experiment you want to run, in a sentence or two.")
    output = (llm or get_llm()).complete_json(
        build_system_prompt(existing_flag_names),
        text,
        PlannerOutput,
        temperature=PLANNING_TEMPERATURE,
        context={"existing_flag_names": list(existing_flag_names)},
    )
    result = output.root
    return keep_in_development(result) if isinstance(result, ExperimentPlan) else result


def keep_in_development(plan: ExperimentPlan) -> ExperimentPlan:
    """Never let the LLM propose a non-development environment; record the change in risks."""
    if plan.environment == SAFE_ENVIRONMENT:
        return plan
    note = (
        f"Requested environment {plan.environment!r} was changed to {SAFE_ENVIRONMENT!r}: "
        "FlagPilot plans experiments in development; production needs ALLOW_PRODUCTION=1 "
        "and a separate approval."
    )
    # Keep within the 1-3 risk limit: the environment note always stays.
    risks = [*plan.risks[: MAX_RISKS - 1], note]
    return plan.model_copy(update={"environment": SAFE_ENVIRONMENT, "risks": risks})
