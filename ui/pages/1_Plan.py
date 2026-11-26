"""Plan page: request -> plan -> diff of Flagsmith changes -> Approve / Edit / Reject -> run."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import ValidationError  # noqa: E402

from agent.approval import approve, reject  # noqa: E402
from agent.audit import AuditLog  # noqa: E402
from agent.changes import build_changes, render_diff  # noqa: E402
from agent.config import Settings, get_settings  # noqa: E402
from agent.errors import FlagPilotError  # noqa: E402
from agent.executor import execute_plan  # noqa: E402
from agent.flagsmith_client import FlagsmithClient  # noqa: E402
from agent.guardrails import check_approval, validate_plan  # noqa: E402
from agent.models import ExecutionResult, ExperimentPlan, plan_hash  # noqa: E402
from agent.planner import plan_experiment  # noqa: E402

STATE_DEFAULTS: dict[str, object] = {
    "plan": None,
    "questions": None,
    "existing_flags": (),
    "approval": None,
    "editing": False,
    "execution": None,
}


def reset_review() -> None:
    """Forget the current plan and everything derived from it."""
    for key, value in STATE_DEFAULTS.items():
        st.session_state[key] = value


def generate(request: str) -> None:
    """Fetch existing flags from Flagsmith, then ask the planner for a plan or questions."""
    try:
        existing = tuple(FlagsmithClient().list_flag_names())
        with st.spinner("Planning the experiment…"):
            result = plan_experiment(request, existing)
    except FlagPilotError as error:
        st.error(error.friendly)
        return
    reset_review()
    st.session_state.existing_flags = existing
    if isinstance(result, ExperimentPlan):
        st.session_state.plan = result
    else:
        st.session_state.questions = result.questions


def render_plan(plan: ExperimentPlan, settings: Settings, audit: AuditLog) -> list[str]:
    """Show the plan and the exact Flagsmith diff; return guardrail problems."""
    st.subheader(f"Proposed experiment: `{plan.flag_name}`")
    st.markdown(f"**Hypothesis:** {plan.hypothesis}")
    columns = st.columns(4)
    columns[0].metric("Traffic in experiment", f"{plan.traffic_percent}%")
    columns[1].metric("Environment", plan.environment)
    columns[2].metric("Metric", plan.metric_name, help=f"event: {plan.metric_event}")
    columns[3].metric("Min. sample / variant", plan.min_sample_size)
    if plan.risks:
        st.markdown("**Risks**\n" + "\n".join(f"- {risk}" for risk in plan.risks))

    calls = build_changes(plan, settings.flagsmith_project_id)
    st.markdown("#### Changes FlagPilot will make in Flagsmith")
    st.code(render_diff(calls), language="diff")
    with st.expander("Exact MCP tool calls"):
        st.json([call.model_dump(include={"tool", "arguments"}) for call in calls])

    problems = validate_plan(
        plan,
        calls,
        allow_production=settings.allow_production,
        first_run=not audit.has_live_run(plan.flag_name),
    )
    for problem in problems:
        st.error(problem)
    return problems


def render_review(plan: ExperimentPlan, audit: AuditLog, problems: list[str]) -> None:
    """Approve / Edit / Reject buttons."""
    st.markdown("#### Review")
    reviewer = st.text_input("Your name (recorded in the audit log)", key="approver")
    reason = st.text_input("Reason (optional, for rejections)", key="reject_reason")
    approved = st.session_state.approval is not None
    approve_col, edit_col, reject_col = st.columns(3)
    try:
        if approve_col.button(
            "Approve", type="primary", disabled=bool(problems) or approved, key="approve"
        ):
            st.session_state.approval = approve(plan, reviewer, audit)
            st.rerun()
        if edit_col.button("Edit", key="edit"):
            st.session_state.editing = True
            st.session_state.plan_editor = plan.model_dump_json(indent=2)
            st.rerun()
        if reject_col.button("Reject", key="reject"):
            reject(plan, reviewer, audit, reason=reason)
            reset_review()
            st.session_state.rejected_flag = plan.flag_name
            st.rerun()
    except FlagPilotError as error:
        st.error(error.friendly)


def render_editor() -> None:
    """Edit the plan as JSON; saving re-validates it and clears any approval."""
    st.markdown("#### Edit plan")
    text = st.text_area("Plan JSON", height=420, key="plan_editor")
    save_col, cancel_col = st.columns(2)
    if save_col.button("Save changes", type="primary", key="save_edit"):
        try:
            edited = ExperimentPlan.model_validate_json(
                text, context={"existing_flag_names": st.session_state.existing_flags}
            )
        except ValidationError as error:
            problems = "\n".join(
                f"- {'.'.join(map(str, item['loc'])) or 'plan'}: {item['msg']}"
                for item in error.errors(include_url=False)
            )
            st.error(f"The edited plan is not valid:\n{problems}")
            return
        st.session_state.plan = edited
        st.session_state.approval = None
        st.session_state.execution = None
        st.session_state.editing = False
        st.rerun()
    if cancel_col.button("Cancel", key="cancel_edit"):
        st.session_state.editing = False
        st.rerun()


def render_execution(
    plan: ExperimentPlan, settings: Settings, audit: AuditLog, problems: list[str]
) -> None:
    """Show the approval status and run the plan (for real or as a dry run)."""
    approval = st.session_state.approval
    if approval is not None:
        stale = check_approval(plan_hash(plan), approval)
        if stale:
            st.warning(stale[0])
        else:
            st.success(
                f"Approved by {approval.approver} at {approval.approved_at:%Y-%m-%d %H:%M:%S} UTC "
                f"· plan {approval.plan_hash[:12]}"
            )
    dry_run = st.checkbox(
        "Dry run (log the calls, change nothing in Flagsmith)",
        value=settings.dry_run,
        key="dry_run",
    )
    label = "Run dry run" if dry_run else "Apply in Flagsmith"
    disabled = bool(problems) or (not dry_run and approval is None)
    if st.button(label, type="primary", disabled=disabled, key="execute"):
        try:
            st.session_state.execution = execute_plan(
                plan,
                approval,
                runner=None if dry_run else FlagsmithClient(),
                audit=audit,
                project_id=settings.flagsmith_project_id,
                allow_production=settings.allow_production,
                dry_run=dry_run,
            )
        except FlagPilotError as error:
            st.error(error.friendly)
    show_execution(st.session_state.execution)


def show_execution(result: ExecutionResult | None) -> None:
    """Summarise what the executor did."""
    if result is None:
        return
    count = len(result.outcomes)
    if result.dry_run:
        st.info(f"Dry run: nothing was changed. {count} Flagsmith call(s) logged to the audit log.")
        return
    st.success(f"Applied {count} change(s) in Flagsmith for `{result.flag_name}`.")
    with st.expander("Flagsmith responses"):
        st.json([outcome.model_dump() for outcome in result.outcomes])


def main() -> None:
    """Render the Plan page."""
    st.title("Plan an experiment")
    try:
        settings = get_settings()
    except FlagPilotError as error:
        st.error(error.friendly)
        st.stop()
    audit = AuditLog(settings.audit_log_path)
    for key, value in STATE_DEFAULTS.items():
        st.session_state.setdefault(key, value)

    if rejected := st.session_state.pop("rejected_flag", None):
        st.info(f"Rejected `{rejected}`. Nothing was changed in Flagsmith.")
    request = st.text_area(
        "What do you want to test?",
        placeholder="e.g. Test a green 'Buy now' checkout button against the current one "
        "and measure purchases.",
        key="request",
    )
    if st.button("Generate plan", key="generate", disabled=not request.strip()):
        generate(request)

    if st.session_state.questions:
        st.warning("FlagPilot needs a bit more detail before it can plan this:")
        st.markdown("\n".join(f"- {question}" for question in st.session_state.questions))

    plan = st.session_state.plan
    if plan is None:
        return
    problems = render_plan(plan, settings, audit)
    if st.session_state.editing:
        render_editor()
        return
    render_review(plan, audit, problems)
    render_execution(plan, settings, audit, problems)


main()
