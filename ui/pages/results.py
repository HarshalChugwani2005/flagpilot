"""Results: verdict, conversion chart with 95% CIs, metrics, AI summary, second approval."""

import streamlit as st

from agent.audit import AuditLog
from agent.config import get_settings
from agent.errors import FlagPilotError
from agent.executor import roll_out_winner
from agent.experiments import ExperimentSummary, list_experiments
from agent.models import ExperimentResult
from agent.reporter import summarise
from agent.stats import analyse
from ui.components import conversion_chart, show_error, verdict_banner
from ui.data import variant_counts


def verdict_detail(result: ExperimentResult) -> str:
    """One line under the verdict banner."""
    if result.verdict == "winner":
        return f"{result.winner} wins on {result.metric_name} (p = {result.p_value:.4f})."
    if result.verdict == "not_significant":
        return (
            f"No variant beats the others yet (p = {result.p_value:.4f}, "
            f"needs < {result.alpha:.4g})."
        )
    return f"Each variant needs at least {result.min_sample_size} visitors before deciding."


def render_metrics(result: ExperimentResult) -> None:
    """Visitors, conversions, relative lift and p-value."""
    columns = st.columns(4)
    columns[0].metric("Visitors", f"{result.visitors:,}")
    columns[1].metric("Conversions", f"{result.conversions:,}")
    lift = "—" if result.relative_lift is None else f"{result.relative_lift * 100:+.1f}%"
    columns[2].metric("Relative lift", lift, help=f"{result.best_treatment} vs {result.control}")
    columns[3].metric(
        "p-value", f"{result.p_value:.4f}", help=f"Significant below {result.alpha:.4g}"
    )


def render_summary(result: ExperimentResult) -> None:
    """AI narrative of the computed numbers, cached per result in session state."""
    cache: dict[str, str] = st.session_state.setdefault("summaries", {})
    key = result.model_dump_json()
    with st.container(border=True):
        st.markdown("#### 🧠 What this means")
        if key not in cache:
            try:
                with st.spinner("Summarising the results…"):
                    cache[key] = summarise(result)
            except FlagPilotError as error:
                show_error(error)
                return
        st.write(cache[key])
        st.caption("Written by the AI from the numbers above; it does not calculate anything.")


@st.dialog("Roll out the winner")
def rollout_dialog(result: ExperimentResult) -> None:
    """Second approval: the reviewer types the flag name to confirm."""
    st.markdown(f"This sends **100%** of traffic to **{result.winner}** on `{result.flag_name}`.")
    approver = st.text_input("Your name", key="rollout_approver")
    typed = st.text_input(f"Type `{result.flag_name}` to confirm", key="rollout_confirm")
    if st.button(
        "Roll out winner",
        type="primary",
        disabled=typed.strip() != result.flag_name or not approver.strip(),
        key="rollout_confirm_btn",
    ):
        try:
            with st.spinner("Rolling out…"):
                roll_out_winner(result, approver=approver, confirmation=typed)
        except FlagPilotError as error:
            show_error(error)


def select_experiment(experiments: list[ExperimentSummary]) -> ExperimentSummary:
    """Experiment picker, defaulting to the one chosen on the Experiments page."""
    names = [experiment.flag_name for experiment in experiments]
    preferred = st.session_state.get("selected_flag")
    index = names.index(preferred) if preferred in names else 0
    chosen = st.selectbox("Experiment", names, index=index, key="results_flag")
    st.session_state.selected_flag = chosen
    return experiments[names.index(chosen)]


def main() -> None:
    """Render the results page."""
    st.title("Results")
    try:
        settings = get_settings()
        experiments = [
            item
            for item in list_experiments(AuditLog(settings.audit_log_path).entries())
            if item.live_since is not None
        ]
    except FlagPilotError as error:
        show_error(error)
        st.stop()
    if not experiments:
        st.info("No live experiments yet. Approve and apply one on the New experiment page.")
        return
    experiment = select_experiment(experiments)
    with st.spinner("Fetching results from Flagsmith…"):
        counts, error = variant_counts(experiment.flag_name, experiment.plan.metric_event)
    if counts is None:
        st.error(f"Could not load results from Flagsmith: {error}")
        return
    try:
        result = analyse(experiment.plan, counts)
    except FlagPilotError as stats_error:
        show_error(stats_error)
        return

    verdict_banner(result.verdict, verdict_detail(result))
    st.plotly_chart(
        conversion_chart(result.variants, result.metric_name),
        width="stretch",
        config={"displayModeBar": False},
    )
    render_metrics(result)
    render_summary(result)
    if st.button(
        "Roll out winner",
        type="primary",
        disabled=result.verdict != "winner",
        key="rollout",
        help=None if result.verdict == "winner" else "Available once there is a winner.",
    ):
        rollout_dialog(result)


main()
