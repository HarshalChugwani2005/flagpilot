"""Pydantic v2 data models shared by the planner, guardrails, executor and reporter.

Models other than the planner's are defined in the step that implements them.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    RootModel,
    Tag,
    ValidationInfo,
    field_validator,
    model_validator,
)

FLAG_NAME_PATTERN = r"^fp_[a-z0-9]+(?:_[a-z0-9]+)*$"
EVENT_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"
SAFE_ENVIRONMENT = "development"


class Variant(BaseModel):
    """One arm of an experiment: a name and the JSON value the flag serves for it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    value: dict[str, Any] = Field(min_length=1)


class ExperimentPlan(BaseModel):
    """The LLM's structured experiment proposal, validated before any human sees it.

    Pass ``context={"existing_flag_names": [...]}`` to ``model_validate`` to reject a
    ``flag_name`` that already exists in Flagsmith.
    """

    model_config = ConfigDict(extra="forbid")

    flag_name: str = Field(max_length=40, pattern=FLAG_NAME_PATTERN)
    hypothesis: str = Field(min_length=1)
    variants: list[Variant] = Field(min_length=2, max_length=4)
    traffic_percent: int = Field(ge=1, le=100)
    split: dict[str, int] = Field(description="Variant name -> weight; weights sum to 100.")
    metric_name: str = Field(min_length=1)
    metric_event: str = Field(min_length=1, max_length=64, pattern=EVENT_NAME_PATTERN)
    environment: Literal["development"] = SAFE_ENVIRONMENT
    min_sample_size: int = Field(ge=1)
    risks: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def keep_environment_safe(cls, data: Any) -> Any:
        """Downgrade any other environment to development and record why in ``risks``."""
        if not isinstance(data, dict):
            return data
        requested = data.get("environment", SAFE_ENVIRONMENT)
        if requested == SAFE_ENVIRONMENT:
            return data
        note = (
            f"Requested environment {requested!r} was changed to {SAFE_ENVIRONMENT!r}: "
            "FlagPilot only creates experiments in development."
        )
        risks = data.get("risks")
        risks = [*risks, note] if isinstance(risks, list) else [note]
        return {**data, "environment": SAFE_ENVIRONMENT, "risks": risks}

    @field_validator("flag_name")
    @classmethod
    def flag_name_is_new(cls, flag_name: str, info: ValidationInfo) -> str:
        """Reject a flag name that already exists (case-insensitive) when context provides them."""
        existing = (info.context or {}).get("existing_flag_names", ())
        if flag_name.lower() in {name.lower() for name in existing}:
            raise ValueError(f"flag {flag_name!r} already exists in Flagsmith; choose a new name")
        return flag_name

    @field_validator("risks")
    @classmethod
    def risks_not_blank(cls, risks: list[str]) -> list[str]:
        """Drop surrounding whitespace and reject blank risk entries."""
        cleaned = [risk.strip() for risk in risks]
        if any(not risk for risk in cleaned):
            raise ValueError("risks must not contain blank entries")
        return cleaned

    @model_validator(mode="after")
    def split_matches_variants(self) -> Self:
        """Variant names are unique, the split covers exactly them, and weights sum to 100."""
        names = [variant.name for variant in self.variants]
        if len(set(names)) != len(names):
            raise ValueError("variant names must be unique")
        if set(self.split) != set(names):
            raise ValueError(f"split keys {sorted(self.split)} must match variants {sorted(names)}")
        if any(weight < 0 for weight in self.split.values()):
            raise ValueError("split weights must not be negative")
        total = sum(self.split.values())
        if total != 100:
            raise ValueError(f"split weights must sum to 100, got {total}")
        return self


class ClarifyingQuestions(BaseModel):
    """Questions the planner asks when the request is too vague to plan safely."""

    model_config = ConfigDict(extra="forbid")

    questions: list[str] = Field(min_length=1, max_length=5)

    @field_validator("questions")
    @classmethod
    def questions_not_blank(cls, questions: list[str]) -> list[str]:
        """Drop surrounding whitespace and reject blank questions."""
        cleaned = [question.strip() for question in questions]
        if any(not question for question in cleaned):
            raise ValueError("questions must not be blank")
        return cleaned


def _planner_output_kind(value: Any) -> str:
    """Pick the union branch so validation errors describe only the intended model."""
    if isinstance(value, ClarifyingQuestions):
        return "questions"
    if isinstance(value, dict) and "questions" in value:
        return "questions"
    return "plan"


class PlannerOutput(
    RootModel[
        Annotated[
            Annotated[ExperimentPlan, Tag("plan")]
            | Annotated[ClarifyingQuestions, Tag("questions")],
            Discriminator(_planner_output_kind),
        ]
    ]
):
    """What the planner LLM returns: either a full plan or clarifying questions."""


class ApprovalRecord(BaseModel):
    """A human approval of a specific plan or rollout, required before any Flagsmith write."""


class ExecutionResult(BaseModel):
    """What the executor changed in Flagsmith, and through which path (MCP or Admin API)."""


class ExperimentResult(BaseModel):
    """Per-variant counts and the statistics computed in code (never by the LLM)."""


class AuditEntry(BaseModel):
    """One line of ``audit_log.jsonl`` describing a Flagsmith write and its approval."""
