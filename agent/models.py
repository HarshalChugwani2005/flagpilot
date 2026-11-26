"""Pydantic v2 data models shared by the planner, guardrails, executor and reporter."""

import hashlib
import json
from datetime import UTC, datetime
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

    name: str = Field(min_length=1, max_length=40, pattern=EVENT_NAME_PATTERN)
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
    environment: Literal["development", "production"] = SAFE_ENVIRONMENT
    min_sample_size: int = Field(ge=1)
    risks: list[str] = Field(default_factory=list)

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


def model_hash(model: BaseModel) -> str:
    """SHA-256 of a model's canonical JSON; any change to the model changes it."""
    canonical = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def plan_hash(plan: ExperimentPlan) -> str:
    """Hash an experiment plan; any edit to the plan changes it."""
    return model_hash(plan)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class PlannedCall(BaseModel):
    """One Flagsmith MCP tool call the executor will make, shown verbatim to the approver."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1)
    arguments: dict[str, Any]
    summary: list[str] = Field(min_length=1, description="Human-readable lines for the diff.")


class ApprovalRecord(BaseModel):
    """A human approval of one exact plan, required before any Flagsmith write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approver: str = Field(min_length=1, max_length=80)
    approved_at: datetime
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("approver", mode="before")
    @classmethod
    def approver_trimmed(cls, approver: Any) -> Any:
        """Strip whitespace so a blank name fails the length check."""
        return approver.strip() if isinstance(approver, str) else approver


class CallOutcome(BaseModel):
    """What happened to one planned call."""

    tool: str
    ok: bool
    dry_run: bool
    result: Any = None
    error: str | None = None


class ExecutionResult(BaseModel):
    """What the executor did (or, in dry-run mode, would do) in Flagsmith."""

    flag_name: str
    plan_hash: str
    dry_run: bool
    outcomes: list[CallOutcome]


class VariantCounts(BaseModel):
    """Raw counts for one variant, as measured in Flagsmith."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    visitors: int = Field(ge=0)
    conversions: int = Field(ge=0)

    @model_validator(mode="after")
    def conversions_within_visitors(self) -> Self:
        """Reject more conversions than visitors."""
        if self.conversions > self.visitors:
            raise ValueError("conversions cannot exceed visitors")
        return self


class VariantStats(BaseModel):
    """Conversion rate and its 95% Wilson confidence interval for one variant."""

    name: str
    visitors: int
    conversions: int
    rate: float
    ci_low: float
    ci_high: float


Verdict = Literal["winner", "not_significant", "not_enough_data"]


class ExperimentResult(BaseModel):
    """Statistics computed in code with scipy (never by the LLM)."""

    flag_name: str
    metric_name: str
    control: str
    variants: list[VariantStats]
    best_treatment: str
    p_value: float
    alpha: float = Field(description="Significance level after Bonferroni correction.")
    absolute_lift: float = Field(description="Best treatment rate minus control rate.")
    relative_lift: float | None = Field(description="Best treatment vs control; None if 0%.")
    min_sample_size: int
    verdict: Verdict
    winner: str | None

    @property
    def visitors(self) -> int:
        """Visitors across all variants."""
        return sum(variant.visitors for variant in self.variants)

    @property
    def conversions(self) -> int:
        """Conversions across all variants."""
        return sum(variant.conversions for variant in self.variants)


class RolloutPlan(BaseModel):
    """What the second approval covers: the winner gets 100%, the others become stale."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flag_name: str = Field(pattern=FLAG_NAME_PATTERN)
    control: str = Field(min_length=1)
    winner: str = Field(min_length=1)
    stale_variants: list[str] = Field(min_length=1)
    environment: Literal["development", "production"]

    @model_validator(mode="after")
    def winner_is_not_stale(self) -> Self:
        """Reject a winner that is also listed as stale."""
        if self.winner in self.stale_variants:
            raise ValueError("the winner cannot also be stale")
        return self


class DemoFlag(BaseModel):
    """A Flagsmith flag the demo reset will delete."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    name: str = Field(min_length=1)


class ResetPlan(BaseModel):
    """What ``make reset`` will delete in Flagsmith; approved by typing the project name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: int
    project_name: str
    flags: list[DemoFlag]


AuditEvent = Literal[
    "planned",
    "approved",
    "rejected",
    "refused",
    "call",
    "result",
    "error",
    "rolled_out",
    "reset",
    "timed",
]


class AuditEntry(BaseModel):
    """One line of ``audit_log.jsonl``: an approval decision or a Flagsmith call and its result."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    event: AuditEvent
    flag_name: str | None = None
    plan_hash: str | None = None
    approver: str | None = None
    dry_run: bool = False
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    result: Any = None
    error: str | None = None
