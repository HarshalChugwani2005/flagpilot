"""Reset the demo: delete fp_ flags, back up and clear the audit log, clear the LLM cache.

Prints exactly what it will change and asks for confirmation first.

    python scripts/reset_demo.py     (or: make reset)

Flags are deleted through the Flagsmith Admin API, because the official MCP server has
no delete tool. Only flags starting with fp_ in FLAGSMITH_PROJECT_ID are touched.
"""

import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.admin_api import AdminAPI  # noqa: E402
from agent.approval import approve_reset  # noqa: E402
from agent.audit import AuditLog  # noqa: E402
from agent.config import Settings, load_settings  # noqa: E402
from agent.errors import FlagPilotError  # noqa: E402
from agent.executor import ToolRunner, delete_demo_flags  # noqa: E402
from agent.flagsmith_client import FlagsmithClient  # noqa: E402
from agent.guardrails import FLAG_PREFIX  # noqa: E402
from agent.llm import cached_replies, clear_cache  # noqa: E402
from agent.models import DemoFlag, ResetPlan  # noqa: E402
from scripts.common import ask_approver, confirm  # noqa: E402

BACKUP_DIR_NAME = "audit_backups"


def build_reset(client: FlagsmithClient) -> ResetPlan:
    """Read the project and its fp_ flags from Flagsmith."""
    project = client.get_project()
    flags = [
        DemoFlag(id=flag["id"], name=flag["name"])
        for flag in client.list_flags()
        if flag["name"].startswith(FLAG_PREFIX)
    ]
    return ResetPlan(
        project_id=int(project["id"]), project_name=str(project.get("name", "")), flags=flags
    )


def describe(reset: ResetPlan, audit: AuditLog, backup_dir: Path, cache_dir: Path) -> list[str]:
    """Exactly what the reset will change, one line per change."""
    lines = [f'FlagPilot demo reset: project "{reset.project_name}" (id {reset.project_id})']
    if reset.flags:
        lines.append(f"  Delete {len(reset.flags)} Flagsmith flag(s) (Admin API):")
        lines += [f"    - {flag.name} (id {flag.id})" for flag in reset.flags]
    else:
        lines.append(f"  No {FLAG_PREFIX} flags to delete in Flagsmith.")
    entries = len(audit.entries())
    if audit.path.exists():
        lines.append(
            f"  Back up {audit.path} ({entries} entries) into {backup_dir}/, then clear it"
        )
    cached = len(cached_replies(cache_dir))
    lines.append(
        f"  Delete {cached} cached LLM repl{'y' if cached == 1 else 'ies'} in {cache_dir}/"
    )
    return lines


def apply_reset(
    reset: ResetPlan,
    settings: Settings,
    audit: AuditLog,
    runner: ToolRunner,
    approver: str,
) -> None:
    """Delete the flags (logged), back up and clear the log, clear the cache, record it."""
    if reset.flags:
        approval = approve_reset(reset, approver, audit)
        delete_demo_flags(
            reset,
            approval,
            runner=runner,
            audit=audit,
            on_call=lambda call, outcome: print(f"  done: {call.summary[0]}"),
        )
    backup = audit.backup_and_clear(settings.audit_log_path.parent / BACKUP_DIR_NAME)
    if backup:
        print(f"  done: Audit log backed up to {backup} and cleared")
    removed = clear_cache(settings.llm_cache_dir)
    print(f"  done: Removed {removed} cached LLM repl{'y' if removed == 1 else 'ies'}")
    audit.record(
        "reset",
        flag_name=f"project {reset.project_id}",
        approver=approver,
        arguments={
            "deleted_flags": [flag.name for flag in reset.flags],
            "audit_backup": str(backup) if backup else None,
            "cleared_llm_replies": removed,
        },
    )


def main(input_fn: Callable[[str], str] = input) -> None:
    """Show the reset, ask for the project name, then apply it."""
    try:
        settings = load_settings()
        audit = AuditLog(settings.audit_log_path)
        print("Reading the demo project from Flagsmith…")
        reset = build_reset(FlagsmithClient(settings))
        backup_dir = settings.audit_log_path.parent / BACKUP_DIR_NAME
        print("\n".join(describe(reset, audit, backup_dir, settings.llm_cache_dir)))
        if not confirm(
            f'Type the project name "{reset.project_name}" to continue (anything else cancels)',
            reset.project_name,
            input_fn,
        ):
            print("Cancelled. Nothing was changed.")
            return
        approver = ask_approver(input_fn)
        if approver is None:
            print("Cancelled: an approver name is required. Nothing was changed.")
            return
        apply_reset(reset, settings, audit, AdminAPI(settings), approver)
    except FlagPilotError as error:
        sys.exit(error.friendly + (f"\nDetails: {error.detail}" if error.detail else ""))
    print("Demo reset complete.")


if __name__ == "__main__":
    main()
