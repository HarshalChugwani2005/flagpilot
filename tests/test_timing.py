"""Tests for agent.timing (FlagPilot time vs. the manual baseline)."""

import pytest

from agent.audit import AuditLog, audit_rows
from agent.timing import ExperimentTiming, format_duration, record_timing, time_saved, timings
from tests.conftest import make_plan


def timing(planning: float, review: float, apply: float) -> ExperimentTiming:
    return ExperimentTiming(planning_seconds=planning, review_seconds=review, apply_seconds=apply)


def test_flagpilot_time_excludes_review_time() -> None:
    assert timing(30, 90, 8).flagpilot_seconds == 38


def test_timing_is_stored_in_the_audit_log(audit: AuditLog) -> None:
    plan = make_plan()
    record_timing(audit, plan, timing(30.5, 90, 7.5))

    [entry] = audit.entries()
    assert entry.event == "timed" and entry.flag_name == plan.flag_name
    assert entry.arguments == {
        "planning_seconds": 30.5,
        "review_seconds": 90,
        "apply_seconds": 7.5,
        "flagpilot_seconds": 38.0,
    }
    assert timings(audit.entries()) == [timing(30.5, 90, 7.5)]
    assert audit_rows(audit.entries())[0]["result"] == "FlagPilot 38.0 s, review 90.0 s"


def test_time_saved_against_manual_baseline(audit: AuditLog) -> None:
    record_timing(audit, make_plan(), timing(30, 60, 10))  # 40 s
    record_timing(audit, make_plan(flag_name="fp_two"), timing(50, 120, 10))  # 60 s

    comparison = time_saved(audit.entries(), manual_minutes=10)

    assert comparison is not None
    assert comparison.experiments == 2
    assert comparison.manual_seconds == 600
    assert comparison.average_flagpilot_seconds == pytest.approx(50)
    assert comparison.average_review_seconds == pytest.approx(90)
    assert comparison.total_saved_seconds == pytest.approx(560 + 540)


def test_nothing_to_compare_until_an_experiment_is_timed(audit: AuditLog) -> None:
    assert time_saved(audit.entries(), manual_minutes=10) is None


def test_slower_than_manual_saves_nothing_rather_than_negative(audit: AuditLog) -> None:
    record_timing(audit, make_plan(), timing(300, 0, 400))

    comparison = time_saved(audit.entries(), manual_minutes=5)

    assert comparison is not None and comparison.total_saved_seconds == 0


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.4, "0 s"), (42, "42 s"), (75, "1 min 15 s"), (600, "10 min 00 s"), (3720, "1 h 2 min")],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text
