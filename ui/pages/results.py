"""Results: verdict, conversion chart with 95% CIs, metrics, AI summary, second approval."""

import streamlit as st

from agent.approval import approve_rollout
from agent.audit import AuditLog
from agent.config import Settings, get_settings
from agent.errors import FlagPilotError
from agent.executor import roll_out_winner
from agent.experiments import ExperimentSummary, list_experiments
from agent.flagsmith_client import FlagsmithClient
from agent.models import ExperimentResult
from agent.reporter import summarise
from agent.rollout import build_rollout, describe_rollout
from ui.components import conversion_chart, show_error, verdict_banner
from ui.data import experiment_result


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
    """Visitors, conversions, absolute and relative lift, p-value."""
    columns = st.columns(5)
    columns[0].metric("Visitors", f"{result.visitors:,}")
    columns[1].metric("Conversions", f"{result.conversions:,}")
    comparison = f"{result.best_treatment} vs {result.control}"
    columns[2].metric(
        "Absolute lift", f"{result.absolute_lift * 100:+.2f} pp", help=f"{comparison}, in points"
    )
    lift = "—" if result.relative_lift is None else f"{result.relative_lift * 100:+.1f}%"
    columns[3].metric("Relative lift", lift, help=comparison)
    columns[4].metric(
        "p-value",
        f"{result.p_value:.4f}",
        help=f"Two-proportion z-test; significant below {result.alpha:.4g}",
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
        st.caption("Written by the AI from the computed numbers above; it calculates nothing.")


@st.dialog("Roll out the winner")
def rollout_dialog(experiment: ExperimentSummary, result: ExperimentResult) -> None:
    """Second approval: the reviewer types the flag name, then the rollout runs."""
    settings: Settings = get_settings()
    rollout = build_rollout(experiment.plan, result)
    st.markdown("This will:\n" + "\n".join(f"- {line}" for line in describe_rollout(rollout)))
    approver = st.text_input("Your name", key="rollout_approver")
    typed = st.text_input(f"Type `{rollout.flag_name}` to confirm", key="rollout_confirm")
    confirmed = typed.strip() == rollout.flag_name and bool(approver.strip())
    if not st.button("Roll out winner", type="primary", disabled=not confirmed, key="rollout_go"):
        return
    audit = AuditLog(settings.audit_log_path)
    with st.status("Rolling out in Flagsmith…", expanded=True) as status:
        try:
            approval = approve_rollout(rollout, approver, audit)
            status.write(f"✓ Second approval by {approval.approver}")
            roll_out_winner(
                rollout,
                approval,
                runner=FlagsmithClient(),
                audit=audit,
                project_id=settings.flagsmith_project_id,
                environment_api_key=settings.flagsmith_environment_key.get_secret_value(),
                allow_production=settings.allow_production,
                on_call=lambda call, outcome: status.write(f"✓ {call.summary[0]}"),
            )
        except FlagPilotError as error:
            status.update(label="Rollout stopped", state="error")
            show_error(error)
            return
        status.update(label="Rolled out", state="complete", expanded=False)
    st.success(f"{rollout.winner} now gets 100% of {rollout.flag_name}.")
    st.toast("Winner rolled out", icon="✅")
    st.link_button("Open in Flagsmith", settings.flagsmith_features_url())


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
    if experiment.status == "Rolled out":
        st.success(
            f"Rolled out: **{experiment.winner}** gets 100%. Stale: "
            + ", ".join(experiment.stale_variants)
        )
        return
    with st.spinner("Fetching results from Flagsmith…"):
        result, error = experiment_result(experiment.plan.model_dump_json())
    if result is None:
        st.error(f"Could not load results: {error}")
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
        rollout_dialog(experiment, result)


main()
