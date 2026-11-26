"""Turns the Edit dialog's form fields back into a validated ExperimentPlan."""

import json
from collections.abc import Collection, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.errors import FlagPilotError
from agent.models import ExperimentPlan


class VariantFields(BaseModel):
    """One variant row of the form, as typed."""

    name: str
    weight: int
    value_json: str


class FormError(FlagPilotError):
    """The edited plan is not valid; ``problems`` lists each issue."""

    def __init__(self, problems: list[str]) -> None:
        """Keep the individual problems for display."""
        super().__init__("Please fix the highlighted problems:\n" + "\n".join(problems))
        self.problems = problems


def _describe(error: ValidationError) -> list[str]:
    """Readable one-liners for each validation problem."""
    return [
        f"{'.'.join(str(part) for part in item['loc']) or 'plan'}: {item['msg']}"
        for item in error.errors(include_url=False)
    ]


def plan_from_form(
    fields: dict[str, Any],
    variants: Sequence[VariantFields],
    existing_flag_names: Collection[str],
) -> ExperimentPlan:
    """Build and validate a plan from the form, raising FormError with every problem."""
    problems = []
    parsed = []
    for variant in variants:
        try:
            value = json.loads(variant.value_json)
        except json.JSONDecodeError as error:
            problems.append(
                f"variant {variant.name or '(unnamed)'}: value is not valid JSON ({error.msg})"
            )
            continue
        parsed.append({"name": variant.name.strip(), "value": value})
    if problems:
        raise FormError(problems)
    risks = [line.strip() for line in str(fields.get("risks", "")).splitlines() if line.strip()]
    data = {
        **fields,
        "risks": risks,
        "variants": parsed,
        "split": {variant.name.strip(): variant.weight for variant in variants},
    }
    try:
        return ExperimentPlan.model_validate(
            data, context={"existing_flag_names": list(existing_flag_names)}
        )
    except ValidationError as error:
        raise FormError(_describe(error)) from error
