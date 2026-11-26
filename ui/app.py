"""FlagPilot Streamlit shell: ``streamlit run ui/app.py``. Renders the sidebar and routes pages."""

import logging
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.audit import AuditLog  # noqa: E402
from agent.config import Settings, get_settings  # noqa: E402
from agent.errors import FlagPilotError  # noqa: E402
from agent.timing import format_duration, time_saved  # noqa: E402
from ui.components import STATUS, inject_css, show_error  # noqa: E402
from ui.data import flagsmith_health  # noqa: E402

log = logging.getLogger("flagpilot.ui")

PAGES = [
    st.Page("pages/new_experiment.py", title="New experiment", icon="✨", default=True),
    st.Page("pages/experiments.py", title="Experiments", icon="🧪"),
    st.Page("pages/results.py", title="Results", icon="📊"),
    st.Page("pages/audit_log.py", title="Audit log", icon="📜"),
]


def render_connection(settings: Settings) -> None:
    """Flagsmith project / environment with a green or red dot."""
    connected, message = flagsmith_health()
    color = STATUS["good"] if connected else STATUS["critical"]
    label = "Connected" if connected else "Not connected"
    environment = settings.flagsmith_environment_key.get_secret_value()
    st.html(
        f'<div><span class="fp-dot" style="background:{color}"></span><b>Flagsmith: {label}</b>'
        f"<br><small>Project {settings.flagsmith_project_id} · environment "
        f"{environment[:6]}…</small></div>"
    )
    if not connected:
        st.caption(message)


def render_time_saved(settings: Settings) -> None:
    """Measured FlagPilot time vs. the user's timed manual setup; review time shown apart."""
    try:
        entries = AuditLog(settings.audit_log_path).entries()
    except FlagPilotError as error:
        st.caption(error.friendly)
        return
    if settings.manual_baseline_minutes is None:
        st.metric(
            "Time saved",
            "—",
            help="Time yourself creating the same experiment in the Flagsmith dashboard, "
            "then set MANUAL_BASELINE_MINUTES in .env.",
        )
        return
    comparison = time_saved(entries, settings.manual_baseline_minutes)
    if comparison is None:
        st.metric("Time saved", "—", help="Appears after the first experiment goes live.")
        return
    st.metric(
        "Time saved",
        format_duration(comparison.total_saved_seconds),
        help=f"Across {comparison.experiments} live experiment(s): your measured manual "
        "setup minus FlagPilot's planning + applying time. Your review time is excluded.",
    )
    st.caption(
        f"By hand: {format_duration(comparison.manual_seconds)} · "
        f"FlagPilot: {format_duration(comparison.average_flagpilot_seconds)} · "
        f"your review: {format_duration(comparison.average_review_seconds)}"
        + (" (averages)" if comparison.experiments > 1 else "")
    )


def render_sidebar(settings: Settings | None) -> None:
    """Wordmark, connection status, time saved and navigation."""
    with st.sidebar:
        st.html('<p class="fp-wordmark">🚩 FlagPilot</p>')
        st.caption("Plain-English A/B experiments on Flagsmith, approved by a human.")
        if settings is not None:
            render_connection(settings)
            render_time_saved(settings)
        st.divider()
        for page in PAGES:
            st.page_link(page)


def main() -> None:
    """Configure the page, draw the shell and run the selected page."""
    st.set_page_config(page_title="FlagPilot", page_icon="🚩", layout="wide")
    inject_css()
    try:
        settings = get_settings()
    except FlagPilotError as error:
        render_sidebar(None)
        st.error(error.friendly)
        st.stop()
    render_sidebar(settings)
    page = st.navigation(PAGES, position="hidden")
    try:
        page.run()
    except Exception as error:  # last resort: a plain message, never a stack trace
        log.exception("Unhandled error on page %s", page.title)
        show_error(error)


main()
