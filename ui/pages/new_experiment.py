"""New experiment: prompt -> plan (or questions) -> review -> Approve / Edit / Reject -> apply."""

import json
import time

import streamlit as st

from agent.approval import approve, record_plan, reject
from agent.audit import AuditLog
from agent.changes import OUTSIDE, build_changes, traffic_segments, variant_allocations
from agent.config import Settings, get_settings
from agent.errors import FlagPilotError
from agent.executor import execute_plan
from agent.flagsmith_client import FlagsmithClient
from agent.guardrails import evaluate
from agent.models import ExecutionResult, ExperimentPlan, plan_hash
from agent.planner import plan_experiment
from agent.timing import ExperimentTiming, format_duration, record_timing
from ui.components import button_preview, guardrail_pills, show_error, split_chart
from ui.plan_form import FormError, VariantFields, plan_from_form

EXAMPLES = {
    "Checkout button": "Test a green 'Buy now' checkout button against our current grey "
    "'Add to cart' button and measure purchases.",
    "Free-shipping banner": "Try a free-shipping banner above the product photo versus no "
    "banner, and see whether more visitors add to cart.",
    "Pricing copy": "Compare 'Save 20%' with 'Only ₹1,999' as the price label on the "
    "product page and measure checkout starts.",
}

STATE_DEFAULTS: dict[str, object] = {
    "plan": None,
    "questions": None,
    "asked_request": "",
    "existing_flags": (),
    "approval": None,
    "execution": None,
}


def reset_review() -> None:
    """Forget the current plan and everything derived from it."""
    for key, value in STATE_DEFAULTS.items():
        st.session_state[key] = value


def start_timer() -> None:
    """Start timing a new experiment from zero (on a fresh "Plan experiment" click)."""
    st.session_state.timer = {"planning_seconds": 0.0, "plan_shown_at": None}


def use_example(text: str) -> None:
    """Fill the request box with an example prompt."""
    st.session_state.request = text


# --- Planning ---------------------------------------------------------------------------


def run_planning(request: str, settings: Settings, audit: AuditLog) -> None:
    """Plan with live progress steps; store a plan or clarifying questions.

    The run's duration is added to the experiment's planning time, so a round of
    clarifying questions counts the planner's time but not the user's answering time.
    """
    timer = st.session_state.setdefault("timer", {"planning_seconds": 0.0, "plan_shown_at": None})
    started = time.monotonic()
    with st.status("Planning your experiment…", expanded=True) as status:
        try:
            status.write("Reading existing flags from Flagsmith…")
            existing = tuple(FlagsmithClient().list_flag_names())
            status.write(f"Drafting plan with {settings.llm_model}…")
            result = plan_experiment(request, existing)
            if isinstance(result, ExperimentPlan):
                status.write("Checking guardrails…")
                record_plan(result, audit)
        except FlagPilotError as error:
            timer["planning_seconds"] += time.monotonic() - started
            status.update(label="Planning failed", state="error", expanded=False)
            show_error(error)
            return
        timer["planning_seconds"] += time.monotonic() - started
        reset_review()
        st.session_state.existing_flags = existing
        if isinstance(result, ExperimentPlan):
            st.session_state.plan = result
            timer["plan_shown_at"] = time.monotonic()
            status.update(label="Plan ready for review", state="complete", expanded=False)
        else:
            st.session_state.questions = result.questions
            st.session_state.asked_request = request
            status.update(label="A few questions first", state="complete", expanded=False)


def render_questions(settings: Settings, audit: AuditLog) -> None:
    """Friendly card with the planner's questions and a box to answer them."""
    questions: list[str] = st.session_state.questions
    with st.container(border=True):
        st.markdown("#### 🤔 A few questions before I plan this")
        st.markdown("\n".join(f"{number}. {q}" for number, q in enumerate(questions, start=1)))
        answers = st.text_area("Your answers", key="answers", placeholder="Answer in any order…")
        if st.button("Re-plan with my answers", type="primary", disabled=not answers.strip()):
            request = (
                f"{st.session_state.asked_request}\n\nAnswers to your questions:\n"
                + "\n".join(f"Q: {q}" for q in questions)
                + f"\nA: {answers.strip()}"
            )
            run_planning(request, settings, audit)
            st.rerun()


# --- Review -----------------------------------------------------------------------------


def render_headline(plan: ExperimentPlan) -> None:
    """Hypothesis as the headline, metric underneath."""
    with st.container(border=True):
        st.caption(f"Proposed experiment · `{plan.flag_name}` · {plan.environment}")
        st.markdown(f"### {plan.hypothesis}")
        st.markdown(
            f"**Metric:** {plan.metric_name} (event `{plan.metric_event}`) · "
            f"at least **{plan.min_sample_size}** visitors per variant"
        )


def render_variants(plan: ExperimentPlan) -> None:
    """Variant cards side by side, with a live button preview when the value is one."""
    allocations = variant_allocations(plan)
    for column, variant in zip(st.columns(len(plan.variants)), plan.variants, strict=True):
        with column, st.container(border=True):
            role = "Control" if variant is plan.variants[0] else "Variant"
            st.markdown(f"**{variant.name}** · {role}")
            st.caption(
                f"{plan.split[variant.name]}% of the experiment · "
                f"{allocations[variant.name]:g}% of all identities"
            )
            preview = button_preview(variant.value)
            if preview:
                st.html(preview)
            else:
                st.code(json.dumps(variant.value, indent=2), language="json")


def render_changes(plan: ExperimentPlan, settings: Settings) -> None:
    """Numbered list of the exact operations FlagPilot will perform."""
    calls = build_changes(plan, settings.flagsmith_project_id)
    lines = [line for call in calls for line in call.summary]
    with st.container(border=True):
        st.markdown("#### Changes FlagPilot will make in Flagsmith")
        st.markdown("\n".join(f"{number}. {line}" for number, line in enumerate(lines, 1)))


def render_safety(plan: ExperimentPlan, settings: Settings, audit: AuditLog) -> bool:
    """Guardrail pills and risks; return True if nothing blocks the plan."""
    calls = build_changes(plan, settings.flagsmith_project_id)
    checks = evaluate(
        plan,
        calls,
        allow_production=settings.allow_production,
        first_run=not audit.has_live_run(plan.flag_name),
    )
    st.markdown("#### Guardrails")
    st.html(f"<div>{guardrail_pills(checks)}</div>")
    if plan.risks:
        st.markdown("**Risks**\n" + "\n".join(f"- {risk}" for risk in plan.risks))
    return all(check.status != "fail" for check in checks)


@st.dialog("Edit plan", width="large")
def edit_dialog(plan: ExperimentPlan) -> None:
    """Every field prefilled; saving re-validates and clears any approval."""
    key = plan_hash(plan)[:12]
    with st.form(f"edit_{key}"):
        flag_name = st.text_input("Flag name", plan.flag_name)
        hypothesis = st.text_area("Hypothesis", plan.hypothesis, height=80)
        left, right = st.columns(2)
        metric_name = left.text_input("Metric name", plan.metric_name)
        metric_event = right.text_input("Metric event", plan.metric_event)
        first, second, third = st.columns(3)
        environment = first.selectbox(
            "Environment",
            ["development", "production"],
            index=["development", "production"].index(plan.environment),
        )
        traffic = second.slider("Traffic in experiment (%)", 1, 100, plan.traffic_percent)
        min_sample = third.number_input("Min. visitors per variant", 1, value=plan.min_sample_size)
        st.markdown("**Variants** (the first one is the control)")
        variants = []
        for index, variant in enumerate(plan.variants):
            name_col, weight_col, value_col = st.columns([2, 1, 4])
            variants.append(
                VariantFields(
                    name=name_col.text_input("Name", variant.name, key=f"{key}_name_{index}"),
                    weight=int(
                        weight_col.number_input(
                            "Weight %", 0, 100, plan.split[variant.name], key=f"{key}_w_{index}"
                        )
                    ),
                    value_json=value_col.text_area(
                        "Value (JSON)",
                        json.dumps(variant.value),
                        height=68,
                        key=f"{key}_value_{index}",
                    ),
                )
            )
        risks = st.text_area("Risks (one per line)", "\n".join(plan.risks), height=80)
        saved = st.form_submit_button("Save changes", type="primary")
    if not saved:
        return
    fields = {
        "flag_name": flag_name.strip(),
        "hypothesis": hypothesis.strip(),
        "metric_name": metric_name.strip(),
        "metric_event": metric_event.strip(),
        "environment": environment,
        "traffic_percent": traffic,
        "min_sample_size": int(min_sample),
        "risks": risks,
    }
    try:
        edited = plan_from_form(fields, variants, st.session_state.existing_flags)
    except FormError as error:
        st.error("Please fix these problems:\n" + "\n".join(f"- {p}" for p in error.problems))
        return
    st.session_state.plan = edited
    st.session_state.approval = None
    st.session_state.execution = None
    st.rerun()


def apply_plan(plan: ExperimentPlan, settings: Settings, audit: AuditLog, *, dry_run: bool) -> None:
    """Record the approval, then run each Flagsmith call with live progress."""
    reviewer = st.session_state.get("reviewer", "")
    clicked = time.monotonic()
    label = "Dry run: logging the Flagsmith calls…" if dry_run else "Applying in Flagsmith…"
    with st.status(label, expanded=True) as status:
        try:
            approval = approve(plan, reviewer, audit)
            st.session_state.approval = approval
            status.write(f"✓ Approved by {approval.approver} · plan {approval.plan_hash[:12]}")
            result = execute_plan(
                plan,
                approval,
                runner=None if dry_run else FlagsmithClient(),
                audit=audit,
                project_id=settings.flagsmith_project_id,
                allow_production=settings.allow_production,
                dry_run=dry_run,
                on_call=lambda call, outcome: status.write(
                    f"✓ {'Would run' if outcome.dry_run else 'Done'}: {call.summary[0]}"
                ),
            )
        except FlagPilotError as error:
            status.update(label="Nothing more was changed", state="error", expanded=True)
            show_error(error)
            return
        status.update(label="All changes logged", state="complete", expanded=False)
    if not dry_run:
        record_live_timing(plan, audit, clicked)
    st.session_state.execution = result
    st.toast("Dry run logged" if dry_run else f"{plan.flag_name} is live in Flagsmith", icon="✅")
    render_outcome(result, settings)


def record_live_timing(plan: ExperimentPlan, audit: AuditLog, approve_clicked: float) -> None:
    """Store planning, review and apply seconds once the experiment is live.

    Nothing is recorded if the timer was lost (e.g. the page was reloaded mid-review),
    rather than guessing a number.
    """
    timer = st.session_state.pop("timer", None)
    if not timer or timer.get("plan_shown_at") is None:
        st.session_state.last_timing = None
        return
    timing = ExperimentTiming(
        planning_seconds=timer["planning_seconds"],
        review_seconds=approve_clicked - timer["plan_shown_at"],
        apply_seconds=time.monotonic() - approve_clicked,
    )
    record_timing(audit, plan, timing)
    st.session_state.last_timing = timing


def render_outcome(result: ExecutionResult, settings: Settings) -> None:
    """Success message and a link to the flag in Flagsmith."""
    if result.dry_run:
        st.info(
            f"Dry run complete: {len(result.outcomes)} Flagsmith call(s) logged, nothing changed."
        )
        return
    st.success(f"`{result.flag_name}` was created in Flagsmith.")
    timing: ExperimentTiming | None = st.session_state.get("last_timing")
    if timing is not None:
        st.caption(
            f"⏱ FlagPilot: {format_duration(timing.flagpilot_seconds)} "
            f"(planning {format_duration(timing.planning_seconds)} + applying "
            f"{format_duration(timing.apply_seconds)}) · your review: "
            f"{format_duration(timing.review_seconds)}"
        )
    st.link_button("Open in Flagsmith", settings.flagsmith_features_url())


def render_actions(plan: ExperimentPlan, settings: Settings, audit: AuditLog, safe: bool) -> None:
    """Reviewer name, dry-run toggle and the Approve / Edit / Reject buttons."""
    st.markdown("#### Your decision")
    left, right = st.columns([3, 2])
    left.text_input("Your name (recorded in the audit log)", key="reviewer")
    dry_run = right.toggle(
        "Dry run", value=settings.dry_run, key="dry_run", help="Log the calls, change nothing."
    )
    named = bool(st.session_state.get("reviewer", "").strip())
    approve_col, edit_col, reject_col = st.columns(3)
    if approve_col.button(
        "Approve (dry run)" if dry_run else "Approve and apply",
        type="primary",
        disabled=not (safe and named),
        width="stretch",
        key="approve",
        help=None if named else "Enter your name first.",
    ):
        apply_plan(plan, settings, audit, dry_run=dry_run)
    if edit_col.button("Edit", width="stretch", key="edit"):
        edit_dialog(plan)
    with reject_col.popover("Reject", width="stretch"):
        reason = st.text_input("Reason (optional)", key="reject_reason")
        if st.button("Reject this plan", disabled=not named, key="reject"):
            try:
                with st.spinner("Recording rejection…"):
                    reject(plan, st.session_state.reviewer, audit, reason=reason)
            except FlagPilotError as error:
                show_error(error)
            else:
                reset_review()
                st.session_state.rejected_flag = plan.flag_name
                st.rerun()


def render_technical_details(plan: ExperimentPlan, settings: Settings) -> None:
    """Raw JSON for the curious, collapsed by default."""
    with st.expander("Technical details"):
        st.caption(f"Plan hash `{plan_hash(plan)}`")
        st.json(plan.model_dump(mode="json"), expanded=False)
        calls = build_changes(plan, settings.flagsmith_project_id)
        st.json([call.model_dump(include={"tool", "arguments"}) for call in calls], expanded=False)


def render_review(plan: ExperimentPlan, settings: Settings, audit: AuditLog) -> None:
    """Render the full plan review."""
    render_headline(plan)
    render_variants(plan)
    st.markdown("#### Traffic split")
    st.plotly_chart(
        split_chart(traffic_segments(plan), neutral_name=OUTSIDE),
        width="stretch",
        config={"displayModeBar": False},
    )
    render_changes(plan, settings)
    safe = render_safety(plan, settings, audit)
    execution = st.session_state.execution
    current = execution is not None and execution.plan_hash == plan_hash(plan)
    if current:
        render_outcome(execution, settings)
    if not current or execution.dry_run:
        render_actions(plan, settings, audit, safe)
    render_technical_details(plan, settings)


# --- Page -------------------------------------------------------------------------------


def main() -> None:
    """Render the New experiment page."""
    try:
        settings = get_settings()
    except FlagPilotError as error:
        show_error(error)
        st.stop()
    audit = AuditLog(settings.audit_log_path)
    for key, value in STATE_DEFAULTS.items():
        st.session_state.setdefault(key, value)

    st.title("New experiment")
    st.caption("Describe what you want to test. FlagPilot plans it; you approve every change.")
    if rejected := st.session_state.pop("rejected_flag", None):
        st.info(f"Rejected `{rejected}`. Nothing was changed in Flagsmith.")

    request = st.text_area(
        "Experiment request",
        key="request",
        height=140,
        placeholder="Describe the experiment you want to run...",
        label_visibility="collapsed",
    )
    for column, (title, text) in zip(st.columns(3), EXAMPLES.items(), strict=True):
        column.button(
            f"💡 {title}", on_click=use_example, args=(text,), width="stretch", key=f"ex_{title}"
        )
    if st.button("Plan experiment", type="primary", disabled=not request.strip(), key="plan_btn"):
        start_timer()
        run_planning(request, settings, audit)

    if st.session_state.questions:
        render_questions(settings, audit)
    if st.session_state.plan is not None:
        render_review(st.session_state.plan, settings, audit)


main()
