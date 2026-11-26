"""Audit log: every approval decision and Flagsmith call, newest first, filterable."""

import csv
import io

import streamlit as st

from agent.audit import AuditLog, audit_rows
from agent.config import get_settings
from agent.errors import FlagPilotError
from ui.components import show_error

ALL_FLAGS = "All flags"


def to_csv(rows: list[dict[str, str]]) -> str:
    """Rows as CSV text."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]) if rows else ["time"])
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def main() -> None:
    """Render the audit log table."""
    st.title("Audit log")
    st.caption("Every plan, approval, rejection and Flagsmith call FlagPilot made.")
    try:
        settings = get_settings()
        with st.spinner("Reading the audit log…"):
            entries = AuditLog(settings.audit_log_path).entries()
    except FlagPilotError as error:
        show_error(error)
        st.stop()
    if not entries:
        st.info("Nothing logged yet.")
        return
    rows = audit_rows(reversed(entries))
    flags = sorted({row["flag"] for row in rows if row["flag"]})
    left, right = st.columns([3, 1])
    chosen = left.selectbox("Filter by flag", [ALL_FLAGS, *flags], key="audit_flag")
    shown = rows if chosen == ALL_FLAGS else [row for row in rows if row["flag"] == chosen]
    right.download_button(
        "Download CSV",
        to_csv(shown),
        file_name="flagpilot_audit_log.csv",
        mime="text/csv",
        width="stretch",
    )
    st.dataframe(shown, hide_index=True, width="stretch")


main()
