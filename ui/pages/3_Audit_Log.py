"""Audit Log page: every approval decision and Flagsmith call, newest first."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.audit import AuditLog  # noqa: E402
from agent.config import get_settings  # noqa: E402
from agent.errors import FlagPilotError  # noqa: E402


def main() -> None:
    """Render the audit log as a table plus the raw JSON of recent entries."""
    st.title("Audit log")
    try:
        settings = get_settings()
        entries = AuditLog(settings.audit_log_path).entries()
    except FlagPilotError as error:
        st.error(error.friendly)
        st.stop()
    if not entries:
        st.info("No entries yet. Approvals, rejections and Flagsmith calls appear here.")
        return
    newest_first = list(reversed(entries))
    st.dataframe(
        [
            {
                "time (UTC)": entry.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "event": entry.event,
                "flag": entry.flag_name,
                "tool": entry.tool,
                "by": entry.approver,
                "dry run": entry.dry_run,
                "plan": (entry.plan_hash or "")[:12],
                "error": entry.error,
            }
            for entry in newest_first
        ],
        hide_index=True,
        width="stretch",
    )
    with st.expander("Raw entries (latest 20)"):
        st.json([entry.model_dump(mode="json") for entry in newest_first[:20]])


main()
