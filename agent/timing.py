"""How long FlagPilot takes from "Plan experiment" to "experiment live", vs. doing it by hand.

FlagPilot's time = planning (every planner run for the request) + applying (approval to
the last Flagsmith call). The reviewer's reading time is measured too but kept apart, so
"time saved" compares machine time with the user's own timed manual setup.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from agent.audit import AuditLog
from agent.models import AuditEntry, ExperimentPlan, plan_hash


class ExperimentTiming(BaseModel):
    """Measured seconds for one experiment going live."""

    planning_seconds: float = Field(ge=0)
    review_seconds: float = Field(ge=0)
    apply_seconds: float = Field(ge=0)

    @property
    def flagpilot_seconds(self) -> float:
        """FlagPilot's own time, excluding the reviewer's reading time."""
        return self.planning_seconds + self.apply_seconds


class TimeSaved(BaseModel):
    """Comparison of FlagPilot's measured times with the manual baseline."""

    experiments: int
    average_flagpilot_seconds: float
    average_review_seconds: float
    manual_seconds: float
    total_saved_seconds: float


def record_timing(audit: AuditLog, plan: ExperimentPlan, timing: ExperimentTiming) -> None:
    """Store the timing in the audit log next to the experiment's other entries."""
    audit.record(
        "timed",
        flag_name=plan.flag_name,
        plan_hash=plan_hash(plan),
        arguments={**timing.model_dump(), "flagpilot_seconds": timing.flagpilot_seconds},
    )


def timings(entries: Sequence[AuditEntry]) -> list[ExperimentTiming]:
    """Every recorded timing, oldest first."""
    return [
        ExperimentTiming.model_validate(
            {key: entry.arguments[key] for key in ExperimentTiming.model_fields}
        )
        for entry in entries
        if entry.event == "timed" and entry.arguments
    ]


def time_saved(entries: Sequence[AuditEntry], manual_minutes: float) -> TimeSaved | None:
    """Compare recorded timings with ``manual_minutes``; None until one experiment is timed."""
    measured = timings(entries)
    if not measured:
        return None
    manual = manual_minutes * 60
    count = len(measured)
    return TimeSaved(
        experiments=count,
        average_flagpilot_seconds=sum(t.flagpilot_seconds for t in measured) / count,
        average_review_seconds=sum(t.review_seconds for t in measured) / count,
        manual_seconds=manual,
        total_saved_seconds=sum(max(0.0, manual - t.flagpilot_seconds) for t in measured),
    )


def format_duration(seconds: float) -> str:
    """``42`` -> ``"42 s"``, ``75`` -> ``"1 min 15 s"``, ``3720`` -> ``"1 h 2 min"``."""
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"
