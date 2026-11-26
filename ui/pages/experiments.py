"""Experiments: every approved experiment as a card with its status and split."""

from datetime import datetime

import streamlit as st

from agent.audit import AuditLog
from agent.changes import OUTSIDE, traffic_segments
from agent.config import get_settings
from agent.errors import FlagPilotError
from agent.experiments import ExperimentSummary, list_experiments
from agent.models import ExperimentResult, utc_now
from ui.components import show_error, split_bar_html, status_pill
from ui.data import experiment_result

COLUMNS = 3


def days_running(live_since: datetime | None, now: datetime) -> str:
    """Whole days since the flag went live, or a note if it has not."""
    if live_since is None:
        return "Not live yet"
    days = (now - live_since).days
    return "Today" if days == 0 else f"{days} day{'s' if days != 1 else ''}"


def live_result(experiment: ExperimentSummary) -> tuple[ExperimentResult | None, str | None]:
    """Results from Flagsmith for running experiments only."""
    if experiment.status != "Running":
        return None, None
    return experiment_result(experiment.plan.model_dump_json())


def render_card(experiment: ExperimentSummary, now: datetime) -> str | None:
    """One experiment card; returns a Flagsmith error to show once, if any."""
    result, error = live_result(experiment)
    status = experiment.status
    if status == "Running" and result is not None and result.verdict == "winner":
        status = "Ready to decide"
    with st.container(border=True):
        st.markdown(f"**`{experiment.flag_name}`**")
        st.html(status_pill(status))
        st.caption(experiment.plan.hypothesis)
        st.html(split_bar_html(traffic_segments(experiment.plan), neutral_name=OUTSIDE))
        left, right = st.columns(2)
        left.metric("Visitors so far", f"{result.visitors:,}" if result else "—")
        right.metric("Running", days_running(experiment.live_since, now))
        if experiment.status == "Rolled out":
            st.caption(
                f"Winner **{experiment.winner}** at 100% · stale: "
                + ", ".join(experiment.stale_variants)
            )
        if st.button(
            "View results",
            key=f"view_{experiment.flag_name}",
            width="stretch",
            disabled=experiment.live_since is None,
        ):
            st.session_state.selected_flag = experiment.flag_name
            st.switch_page("pages/results.py")
    return error


def main() -> None:
    """Render the experiments grid."""
    st.title("Experiments")
    try:
        settings = get_settings()
        with st.spinner("Loading experiments…"):
            experiments = list_experiments(AuditLog(settings.audit_log_path).entries())
    except FlagPilotError as error:
        show_error(error)
        st.stop()
    if not experiments:
        st.info("No experiments yet. Plan your first one on the New experiment page.")
        st.page_link("pages/new_experiment.py", label="New experiment", icon="✨")
        return
    now = utc_now()
    errors = set()
    for start in range(0, len(experiments), COLUMNS):
        row = experiments[start : start + COLUMNS]
        for column, experiment in zip(st.columns(COLUMNS), row, strict=False):
            with column:
                if error := render_card(experiment, now):
                    errors.add(error)
    for error in errors:
        st.warning(f"Live results are unavailable: {error}")


main()
