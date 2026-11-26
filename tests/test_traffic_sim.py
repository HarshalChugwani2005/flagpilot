"""Tests for scripts/traffic_sim.py (Flagsmith SDK mocked)."""

import io
import random
from dataclasses import dataclass
from typing import Any

import pytest
from flagsmith.exceptions import FlagsmithAPIError

from scripts.traffic_sim import (
    IDENTITY_PREFIX,
    MAX_CONSECUTIVE_FAILURES,
    ProgressBar,
    RateLimiter,
    SimulationError,
    SimulationReport,
    format_report,
    parse_rates,
    simulate,
)


@dataclass(frozen=True)
class FakeExperiment:
    in_experiment: bool


@dataclass(frozen=True)
class FakeFlag:
    variant: str | None
    experiment: FakeExperiment | None = FakeExperiment(in_experiment=True)
    is_default: bool = False


class FakeSDK:
    """Assigns variants round-robin and records every call."""

    def __init__(self, variants: list[str], fail_after: int | None = None) -> None:
        self.variants = variants
        self.fail_after = fail_after
        self.identities: list[str] = []
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def get_experiment_flag(self, feature_name: str, identifier: str) -> FakeFlag:
        if self.fail_after is not None and len(self.identities) >= self.fail_after:
            raise FlagsmithAPIError("Unable to get valid response from Flagsmith API.")
        self.identities.append(identifier)
        return FakeFlag(variant=self.variants[len(self.identities) % len(self.variants)])

    def track_event(self, event: str, identifier: str, metadata: dict[str, Any]) -> None:
        self.events.append((event, identifier, metadata))


class NoWait:
    def wait(self) -> None:
        pass


def run(sdk: FakeSDK, rates: dict[str, float], visitors: int = 100) -> SimulationReport:
    report = SimulationReport(flag_name="fp_test", event="purchase", rates=rates)
    return simulate(
        sdk,
        report,
        visitors=visitors,
        rng=random.Random(1),
        limiter=NoWait(),  # type: ignore[arg-type]
        on_progress=lambda done: None,
        run_id="abc123",
    )


def test_parse_rates() -> None:
    assert parse_rates("control=0.08, green=0.11") == {"control": 0.08, "green": 0.11}


@pytest.mark.parametrize("text", ["", "control", "control=high", "control=1.5", "=0.1"])
def test_bad_rates_are_explained(text: str) -> None:
    with pytest.raises(SimulationError):
        parse_rates(text)


def test_identities_are_unique_and_prefixed() -> None:
    sdk = FakeSDK(["control", "green"])

    run(sdk, {"control": 0.5, "green": 0.5}, visitors=50)

    assert len(set(sdk.identities)) == 50
    assert all(identity.startswith(f"{IDENTITY_PREFIX}abc123_") for identity in sdk.identities)


def test_purchases_follow_each_variants_rate() -> None:
    sdk = FakeSDK(["control", "green"])

    report = run(sdk, {"control": 0.0, "green": 1.0}, visitors=40)

    assert report.tallies["control"].purchases == 0
    assert report.tallies["green"].purchases == 20
    assert {event for event, _, _ in sdk.events} == {"purchase"}
    assert all(meta == {"source": "flagpilot_traffic_sim"} for _, _, meta in sdk.events)


def test_missing_rate_is_reported_not_guessed() -> None:
    report = run(FakeSDK(["control", "blue"]), {"control": 1.0}, visitors=10)

    assert report.tallies["blue"].purchases == 0
    assert "No --rates entry for blue" in format_report(report)


def test_repeated_failures_stop_the_run() -> None:
    report = run(FakeSDK(["control"], fail_after=3), {"control": 0.1}, visitors=50)

    assert report.stopped_early
    assert report.failures == MAX_CONSECUTIVE_FAILURES
    assert "failed" in format_report(report)


def test_missing_flag_stops_immediately() -> None:
    class MissingFlagSDK(FakeSDK):
        def get_experiment_flag(self, feature_name: str, identifier: str) -> FakeFlag:
            return FakeFlag(variant=None, experiment=None, is_default=True)

    with pytest.raises(SimulationError, match="was not found"):
        run(MissingFlagSDK([]), {"control": 0.1}, visitors=5)


def test_warns_when_no_experiment_is_running() -> None:
    class NoExperimentSDK(FakeSDK):
        def get_experiment_flag(self, feature_name: str, identifier: str) -> FakeFlag:
            self.identities.append(identifier)
            return FakeFlag(variant="control", experiment=None)

    report = run(NoExperimentSDK([]), {"control": 0.1}, visitors=5)

    assert "No visitor was in a running Flagsmith experiment" in format_report(report)


def test_identities_without_a_variant_key_count_as_control() -> None:
    report = run(FakeSDK([None]), {"control": 1.0}, visitors=4)  # type: ignore[list-item]

    assert report.tallies["control"].purchases == 4


def test_summary_table() -> None:
    report = run(FakeSDK(["control", "green"]), {"control": 0.0, "green": 1.0}, visitors=10)

    table = format_report(report)

    assert "Variant" in table and "Observed" in table
    assert "green" in table and "100.0%" in table
    assert "Total" in table


def test_rate_limiter_spaces_calls() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(4, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()

    assert sleeps == [0.25, 0.25]


def test_progress_bar() -> None:
    stream = io.StringIO()
    bar = ProgressBar(4, stream=stream, width=4)
    bar.update(2)
    bar.update(4)

    assert "[##--] 2/4 (50%)" in stream.getvalue()
    assert stream.getvalue().endswith("[####] 4/4 (100%)\n")


def test_variant_is_recognised_by_value_when_flagsmith_sends_no_key() -> None:
    @dataclass(frozen=True)
    class ValueFlag:
        value: str
        variant: None = None
        experiment: FakeExperiment | None = FakeExperiment(in_experiment=True)
        is_default: bool = False

    class ValueSDK(FakeSDK):
        def get_experiment_flag(self, feature_name: str, identifier: str) -> ValueFlag:
            self.identities.append(identifier)
            green = len(self.identities) % 2 == 0
            return ValueFlag(
                value='{"label": "Go", "color": "#0f0"}' if green else '{"label":"Buy"}'
            )

    report = SimulationReport(
        flag_name="fp_test",
        event="purchase",
        rates={"control": 0.0, "green": 1.0},
        names_by_value={'{"label":"Buy"}': "control", '{"color":"#0f0","label":"Go"}': "green"},
    )
    simulate(
        ValueSDK([]),
        report,
        visitors=10,
        rng=random.Random(1),
        limiter=NoWait(),  # type: ignore[arg-type]
        on_progress=lambda done: None,
        run_id="abc123",
    )

    assert report.tallies["green"].visitors == 5 and report.tallies["green"].purchases == 5
    assert report.tallies["control"].visitors == 5 and report.tallies["control"].purchases == 0
