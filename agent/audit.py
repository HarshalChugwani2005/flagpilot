"""Append-only JSONL audit log of approvals and every Flagsmith call FlagPilot makes."""

import json
import shutil
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.errors import FlagPilotError
from agent.models import ApprovalRecord, AuditEntry, AuditEvent, utc_now

SECRET_KEY_PARTS = ("api_key", "apikey", "token", "secret", "password", "authorization")
REDACTED = "***"


class AuditLogError(FlagPilotError):
    """The audit log could not be written or read."""


def redact(value: Any) -> Any:
    """Return ``value`` with any secret-looking dict keys masked, recursively."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if any(part in str(key).lower() for part in SECRET_KEY_PARTS)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class AuditLog:
    """Reads and appends ``audit_log.jsonl``; one JSON object per line."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = utc_now) -> None:
        """Log to ``path`` (created on first write); ``clock`` is replaceable in tests."""
        self.path = path
        self._clock = clock

    def record(
        self, event: AuditEvent, *, timestamp: datetime | None = None, **fields: Any
    ) -> AuditEntry:
        """Append an entry for ``event``; arguments and results are redacted first."""
        for name in ("arguments", "result"):
            if name in fields:
                fields[name] = redact(fields[name])
        entry = AuditEntry(timestamp=timestamp or self._clock(), event=event, **fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.model_dump_json() + "\n")
        except OSError as error:
            raise AuditLogError(
                f"Could not write the audit log at {self.path}, so nothing was changed.",
                detail=str(error),
            ) from error
        return entry

    def entries(self) -> list[AuditEntry]:
        """All entries, oldest first. A missing file means an empty log."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as error:
            raise AuditLogError(f"Could not read {self.path}.", detail=str(error)) from error
        entries = []
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                entries.append(AuditEntry.model_validate_json(line))
            except ValidationError as error:
                raise AuditLogError(
                    f"Line {number} of {self.path} is not a valid audit entry.", detail=str(error)
                ) from error
        return entries

    def has_approval(self, approval: ApprovalRecord) -> bool:
        """Return True if ``approval`` was recorded here (same approver, time and plan hash)."""
        return any(
            entry.event == "approved"
            and entry.plan_hash == approval.plan_hash
            and entry.approver == approval.approver
            and entry.timestamp == approval.approved_at
            for entry in self.entries()
        )

    def backup_and_clear(self, backup_dir: Path) -> Path | None:
        """Copy the log into ``backup_dir`` (timestamped), then empty it; None if no log."""
        if not self.path.exists():
            return None
        target = backup_dir / f"{self.path.stem}-{self._clock():%Y%m%d-%H%M%S}{self.path.suffix}"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, target)
            self.path.write_text("", encoding="utf-8")
        except OSError as error:
            raise AuditLogError(
                f"Could not back up and clear {self.path}.", detail=str(error)
            ) from error
        return target

    def rollout_of(self, flag_name: str) -> AuditEntry | None:
        """Return the ``rolled_out`` entry for ``flag_name``, if it was rolled out."""
        return next(
            (e for e in self.entries() if e.event == "rolled_out" and e.flag_name == flag_name),
            None,
        )

    def has_live_run(self, flag_name: str) -> bool:
        """Return True if a real (non-dry-run) Flagsmith call for ``flag_name`` succeeded."""
        return any(
            entry.event == "result" and not entry.dry_run and entry.flag_name == flag_name
            for entry in self.entries()
        )


ACTIONS = {
    "planned": "Plan drafted",
    "approved": "Approved",
    "rejected": "Rejected",
    "refused": "Refused by guardrails",
    "call": "Flagsmith call",
    "result": "Flagsmith result",
    "error": "Flagsmith error",
    "rolled_out": "Rolled out winner",
    "reset": "Demo reset",
    "timed": "Timing recorded",
}
RESULT_PREVIEW_CHARS = 120


def _outcome(entry: AuditEntry) -> str:
    """Short human-readable outcome for one entry."""
    if entry.error:
        return entry.error
    if entry.event in ("planned", "approved") and entry.plan_hash:
        return f"plan {entry.plan_hash[:12]}"
    if entry.event == "rolled_out" and entry.arguments:
        stale = ", ".join(entry.arguments.get("stale_variants", []))
        return f"winner {entry.arguments.get('winner')}; stale: {stale}"
    if entry.event == "timed" and entry.arguments:
        return (
            f"FlagPilot {entry.arguments.get('flagpilot_seconds', 0):.1f} s, "
            f"review {entry.arguments.get('review_seconds', 0):.1f} s"
        )
    if entry.event == "call":
        return "skipped (dry run)" if entry.dry_run else "sent"
    if entry.event == "result":
        text = json.dumps(entry.result, ensure_ascii=False)
        return text if len(text) <= RESULT_PREVIEW_CHARS else text[:RESULT_PREVIEW_CHARS] + "…"
    return ""


def audit_rows(entries: Iterable[AuditEntry]) -> list[dict[str, str]]:
    """Table rows (time, action, flag, approver, result) for display and CSV export."""
    rows = []
    for entry in entries:
        action = ACTIONS[entry.event] + (f" · {entry.tool}" if entry.tool else "")
        rows.append(
            {
                "time": entry.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "action": action + (" (dry run)" if entry.dry_run else ""),
                "flag": entry.flag_name or "",
                "approver": entry.approver or "",
                "result": _outcome(entry),
            }
        )
    return rows
