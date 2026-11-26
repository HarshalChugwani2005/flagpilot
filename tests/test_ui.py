"""End-to-end UI tests with Streamlit's AppTest (planner, Flagsmith and LLM mocked)."""

from pathlib import Path
from typing import Any

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import agent.admin_api
import agent.config
import agent.planner
import agent.reporter
from agent.approval import approve
from agent.audit import AuditLog
from agent.config import Settings
from agent.flagsmith_client import FlagsmithClient, FlagsmithError
from agent.models import ClarifyingQuestions, VariantCounts
from agent.timing import ExperimentTiming, record_timing
from tests.conftest import FakeRunner, make_plan

UI = Path(__file__).resolve().parents[1] / "ui"
TIMEOUT = 30


def page(name: str) -> AppTest:
    return AppTest.from_file(str(UI / "pages" / name), default_timeout=TIMEOUT)


def events(settings: Settings) -> list[str]:
    return [entry.event for entry in AuditLog(settings.audit_log_path).entries()]


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> FakeRunner:
    st.cache_data.clear()
    fake = FakeRunner()
    monkeypatch.setattr(agent.config, "get_settings", lambda: settings)
    monkeypatch.setattr(FlagsmithClient, "list_flag_names", lambda self: ["fp_existing"])
    monkeypatch.setattr(FlagsmithClient, "call_tool", lambda self, n, a: fake.call_tool(n, a))
    monkeypatch.setattr(agent.admin_api, "AdminAPI", lambda settings: fake)
    monkeypatch.setattr(agent.planner, "plan_experiment", lambda request, existing: make_plan())
    return fake


def planned() -> AppTest:
    app = page("new_experiment.py").run()
    app.text_area(key="request").input("Test a green Buy now button").run()
    app.button(key="plan_btn").click().run()
    return app


# --- New experiment ---------------------------------------------------------------------


def test_example_button_fills_the_prompt(runner: FakeRunner) -> None:
    app = page("new_experiment.py").run()
    app.button(key="ex_Checkout button").click().run()

    assert "checkout button" in app.text_area(key="request").value


def test_plan_review_shows_headline_changes_and_guardrails(runner: FakeRunner) -> None:
    app = planned()

    assert not app.exception
    markdown = "\n".join(item.value for item in app.markdown)
    assert "A green Buy now button will increase purchases" in markdown
    assert '1. Create multivariate flag "fp_checkout_button_color"' in markdown
    assert 'Add variant "green_buy_now"' in markdown
    assert not app.error


def test_approve_and_apply_runs_every_call(runner: FakeRunner, settings: Settings) -> None:
    app = planned()
    assert app.button(key="approve").disabled  # needs a reviewer name
    app.text_input(key="reviewer").input("Asha").run()
    app.button(key="approve").click().run()

    assert not app.exception
    assert [name for name, _ in runner.calls] == [
        "create_feature",
        "list_feature_multivariate_options",
        "admin_api.create_metric",
        "create_experiment",
        "admin_api.start_experiment",
    ]
    assert events(settings) == ["planned", "approved"] + ["call", "result"] * 5 + ["timed"]
    assert any("created in Flagsmith" in item.value for item in app.success)
    assert any("FlagPilot:" in item.value and "your review:" in item.value for item in app.caption)


def test_dry_run_changes_nothing(runner: FakeRunner, settings: Settings) -> None:
    app = planned()
    app.text_input(key="reviewer").input("Asha").run()
    app.toggle(key="dry_run").set_value(True).run()
    app.button(key="approve").click().run()

    assert runner.calls == []
    assert events(settings) == ["planned", "approved"] + ["call"] * 5
    assert any("Dry run complete" in item.value for item in app.info)


def test_flagsmith_failure_is_friendly(
    runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def fail(self: Any, name: str, arguments: dict[str, Any]) -> Any:
        raise FlagsmithError("Flagsmith is down.")

    monkeypatch.setattr(FlagsmithClient, "call_tool", fail)
    app = planned()
    app.text_input(key="reviewer").input("Asha").run()
    app.button(key="approve").click().run()

    assert not app.exception
    assert any("Flagsmith is down." in item.value for item in app.error)
    assert events(settings)[-1] == "error"


def test_reject_is_logged(runner: FakeRunner, settings: Settings) -> None:
    app = planned()
    app.text_input(key="reviewer").input("Asha").run()
    app.button(key="reject").click().run()

    assert app.session_state.plan is None
    assert events(settings) == ["planned", "rejected"]


def test_questions_can_be_answered_and_replanned(
    runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[str] = []

    def planner(request: str, existing: Any) -> Any:
        requests.append(request)
        if len(requests) == 1:
            return ClarifyingQuestions(questions=["Which page?"])
        return make_plan()

    monkeypatch.setattr(agent.planner, "plan_experiment", planner)
    app = planned()
    assert any("Which page?" in item.value for item in app.markdown)

    app.text_area(key="answers").input("The product page").run()
    app.button[-1].click().run()

    assert "Q: Which page?" in requests[1] and "A: The product page" in requests[1]
    assert app.session_state.plan is not None


def test_flagsmith_unreachable_while_planning(
    runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(self: Any) -> list[str]:
        raise FlagsmithError("Cannot reach Flagsmith.")

    monkeypatch.setattr(FlagsmithClient, "list_flag_names", down)
    app = planned()

    assert not app.exception
    assert any("Cannot reach Flagsmith." in item.value for item in app.error)
    assert app.session_state.plan is None


# --- Experiments, Results, Audit log ----------------------------------------------------


def go_live(settings: Settings, **plan_changes: Any) -> None:
    audit = AuditLog(settings.audit_log_path)
    plan = make_plan(**plan_changes)
    approve(plan, "Asha", audit)
    audit.record("result", flag_name=plan.flag_name, tool="create_feature", result={"id": 1})


def test_experiments_grid(runner: FakeRunner, settings: Settings, monkeypatch) -> None:
    go_live(settings)
    monkeypatch.setattr(
        FlagsmithClient,
        "get_variant_counts",
        lambda self, flag, event: {
            "control": VariantCounts(visitors=120, conversions=10),
            "green_buy_now": VariantCounts(visitors=130, conversions=15),
        },
    )
    app = page("experiments.py").run()

    assert not app.exception
    assert any("fp_checkout_button_color" in item.value for item in app.markdown)
    assert [metric.value for metric in app.metric] == ["250", "Today"]


def test_results_winner(runner: FakeRunner, settings: Settings, monkeypatch) -> None:
    go_live(settings, min_sample_size=300)
    monkeypatch.setattr(
        FlagsmithClient,
        "get_variant_counts",
        lambda self, flag, event: {
            "control": VariantCounts(visitors=1000, conversions=100),
            "green_buy_now": VariantCounts(visitors=1000, conversions=140),
        },
    )
    monkeypatch.setattr(agent.reporter, "summarise", lambda result: "Green wins.")
    app = page("results.py").run()

    assert not app.exception
    values = {metric.label: metric.value for metric in app.metric}
    assert values["Visitors"] == "2,000"
    assert values["Relative lift"] == "+40.0%"
    assert values["Absolute lift"] == "+4.00 pp"
    assert any("Green wins." in item.value for item in app.markdown)
    assert not app.button(key="rollout").disabled


def test_results_when_flagsmith_is_unavailable(runner: FakeRunner, settings: Settings) -> None:
    go_live(settings)
    app = page("results.py").run()

    assert not app.exception
    assert any("Could not load results" in item.value for item in app.error)


def test_audit_log_table_and_filter(runner: FakeRunner, settings: Settings) -> None:
    go_live(settings)
    AuditLog(settings.audit_log_path).record("rejected", flag_name="fp_other", approver="Ben")
    app = page("audit_log.py").run()

    assert not app.exception
    assert len(app.dataframe[0].value) == 3
    app.selectbox(key="audit_flag").select("fp_other").run()
    assert len(app.dataframe[0].value) == 1


def test_shell_renders_sidebar(runner: FakeRunner) -> None:
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TIMEOUT).run()

    assert not app.exception
    assert any(metric.label == "Time saved" for metric in app.sidebar.metric)


def test_results_after_rollout(runner: FakeRunner, settings: Settings) -> None:
    go_live(settings)
    AuditLog(settings.audit_log_path).record(
        "rolled_out",
        flag_name="fp_checkout_button_color",
        approver="Ben",
        arguments={"winner": "green_buy_now", "stale_variants": ["control"]},
    )
    app = page("results.py").run()

    assert not app.exception
    assert any("Stale: control" in item.value for item in app.success)


def test_apply_for_real_after_a_dry_run(runner: FakeRunner, settings: Settings) -> None:
    app = planned()
    app.text_input(key="reviewer").input("Asha").run()
    app.toggle(key="dry_run").set_value(True).run()
    app.button(key="approve").click().run()
    app.toggle(key="dry_run").set_value(False).run()
    app.button(key="approve").click().run()

    assert not app.exception
    assert [name for name, _ in runner.calls] == [
        "create_feature",
        "list_feature_multivariate_options",
        "admin_api.create_metric",
        "create_experiment",
        "admin_api.start_experiment",
    ]
    assert events(settings)[-3:] == ["call", "result", "timed"]
    assert events(settings).count("timed") == 1  # the dry run was not timed


def test_sidebar_compares_with_the_manual_baseline(runner: FakeRunner, settings: Settings) -> None:
    settings.manual_baseline_minutes = 10
    record_timing(
        AuditLog(settings.audit_log_path),
        make_plan(),
        ExperimentTiming(planning_seconds=30, review_seconds=75, apply_seconds=8),
    )
    app = AppTest.from_file(str(UI / "app.py"), default_timeout=TIMEOUT).run()

    [metric] = [m for m in app.sidebar.metric if m.label == "Time saved"]
    assert metric.value == "9 min 22 s"
    captions = " ".join(item.value for item in app.sidebar.caption)
    assert "By hand: 10 min 00 s" in captions and "FlagPilot: 38 s" in captions
    assert "your review: 1 min 15 s" in captions
