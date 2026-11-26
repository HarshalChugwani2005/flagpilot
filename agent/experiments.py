"""Experiments FlagPilot has proposed, derived from the audit log."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from agent.errors import ConfigError
from agent.models import AuditEntry, ExperimentPlan

Status = Literal["Draft", "Running", "Ready to decide", "Rolled out"]


class ExperimentSummary(BaseModel):
    """One approved experiment and where it stands."""

    plan: ExperimentPlan
    status: Status
    approved_at: datetime
    live_since: datetime | None
    winner: str | None = None
    stale_variants: list[str] = []

    @property
    def flag_name(self) -> str:
        """The experiment's Flagsmith flag."""
        return self.plan.flag_name


def _first_live_result(entries: Sequence[AuditEntry], flag_name: str) -> AuditEntry | None:
    """Return the first successful real (non-dry-run) Flagsmith call for ``flag_name``."""
    return next(
        (
            entry
            for entry in entries
            if entry.event == "result" and not entry.dry_run and entry.flag_name == flag_name
        ),
        None,
    )


def list_experiments(entries: Sequence[AuditEntry]) -> list[ExperimentSummary]:
    """Latest approved plan per flag, newest first, with Draft / Running / Rolled out status."""
    latest: dict[str, AuditEntry] = {}
    for entry in entries:
        if entry.event == "approved" and entry.flag_name and "plan" in (entry.arguments or {}):
            latest[entry.flag_name] = entry
    rollouts = {
        entry.flag_name: entry.arguments or {}
        for entry in entries
        if entry.event == "rolled_out" and entry.flag_name
    }
    summaries = []
    for flag_name, approval in latest.items():
        live = _first_live_result(entries, flag_name)
        rollout = rollouts.get(flag_name)
        status: Status = "Rolled out" if rollout else "Running" if live else "Draft"
        summaries.append(
            ExperimentSummary(
                plan=ExperimentPlan.model_validate(approval.arguments["plan"]),  # type: ignore[index]
                status=status,
                approved_at=approval.timestamp,
                live_since=live.timestamp if live else None,
                winner=rollout.get("winner") if rollout else None,
                stale_variants=list(rollout.get("stale_variants", [])) if rollout else [],
            )
        )
    return sorted(summaries, key=lambda item: item.approved_at, reverse=True)


def latest_live_flag(entries: Sequence[AuditEntry]) -> str | None:
    """Return the most recently approved experiment that is live in Flagsmith, if any."""
    live = [item for item in list_experiments(entries) if item.live_since is not None]
    return live[0].flag_name if live else None


def demo_flag_name(configured: str | None, entries: Sequence[AuditEntry]) -> str:
    """Return the demo flag: STORE_FLAG_NAME if set, else the latest live experiment."""
    if configured:
        return configured
    latest = latest_live_flag(entries)
    if latest is None:
        raise ConfigError(
            "No FlagPilot experiment is live yet. Approve and apply one in the app, "
            "or set STORE_FLAG_NAME in .env."
        )
    return latest
