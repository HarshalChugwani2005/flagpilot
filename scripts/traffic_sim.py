"""Simulate visitors against a Flagsmith experiment with the official Python SDK.

Each visitor gets a unique ``sim_`` identity. ``get_experiment_flag`` fetches their
variant (and records Flagsmith's exposure event when the identity is in a running
experiment); a ``purchase`` event is then sent with the probability given for that
variant in ``--rates``.

    python scripts/traffic_sim.py --rates control=0.08,green_buy_now=0.11
    python scripts/traffic_sim.py --visitors 2000 --rates ...   # final demo run only

Every visitor costs one Flagsmith API request (events are batched), so keep runs small
on the free tier (50K requests/month).
"""

import argparse
import logging
import random
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO

from flagsmith import Flagsmith
from flagsmith.exceptions import FlagsmithClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings  # noqa: E402
from agent.errors import ConfigError, FlagPilotError  # noqa: E402

IDENTITY_PREFIX = "sim_"
MAX_VISITORS = 2000
DEFAULT_RPS = 5.0
MAX_CONSECUTIVE_FAILURES = 5
NO_VARIANT = "(no variant)"
EVENT_METADATA = {"source": "flagpilot_traffic_sim"}


class SimulationError(FlagPilotError):
    """The simulation could not start or had to stop."""


class ExperimentClient(Protocol):
    """The parts of ``flagsmith.Flagsmith`` the simulator uses."""

    def get_experiment_flag(self, feature_name: str, identifier: str) -> Any:
        """Resolve the identity's flag and record an exposure if it is in an experiment."""
        ...

    def track_event(self, event: str, identifier: str, metadata: dict[str, Any]) -> None:
        """Buffer a custom event for the identity."""
        ...


def parse_rates(text: str) -> dict[str, float]:
    """Parse ``control=0.08,green=0.11`` into ``{"control": 0.08, "green": 0.11}``."""
    rates: dict[str, float] = {}
    for part in filter(None, (item.strip() for item in text.split(","))):
        name, sep, raw = part.partition("=")
        if not sep or not name.strip():
            raise SimulationError(f'"{part}" should look like variant=0.10.')
        try:
            rate = float(raw)
        except ValueError as error:
            raise SimulationError(f'"{raw}" in "{part}" is not a number.') from error
        if not 0 <= rate <= 1:
            raise SimulationError(f"Rate for {name.strip()} must be between 0 and 1, got {rate}.")
        rates[name.strip()] = rate
    if not rates:
        raise SimulationError("Give at least one rate, e.g. --rates control=0.08,green=0.11.")
    return rates


class RateLimiter:
    """Allows at most ``per_second`` calls to :meth:`wait` per second."""

    def __init__(
        self,
        per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Space calls ``1 / per_second`` seconds apart."""
        self._interval = 1 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next = clock()

    def wait(self) -> None:
        """Sleep until the next call is allowed."""
        now = self._clock()
        if now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval


class ProgressBar:
    """Single-line text progress bar."""

    def __init__(self, total: int, stream: TextIO = sys.stderr, width: int = 30) -> None:
        """Draw to ``stream``."""
        self.total, self.stream, self.width = total, stream, width

    def update(self, done: int) -> None:
        """Redraw for ``done`` of ``total``."""
        filled = round(self.width * done / self.total)
        bar = "#" * filled + "-" * (self.width - filled)
        self.stream.write(f"\r[{bar}] {done}/{self.total} ({done / self.total:.0%})")
        if done == self.total:
            self.stream.write("\n")
        self.stream.flush()


@dataclass
class VariantTally:
    """Counts for one variant."""

    visitors: int = 0
    in_experiment: int = 0
    purchases: int = 0


@dataclass
class SimulationReport:
    """Everything the summary table needs."""

    flag_name: str
    event: str
    rates: dict[str, float]
    tallies: dict[str, VariantTally] = field(default_factory=dict)
    failures: int = 0
    first_error: str | None = None
    missing_rates: set[str] = field(default_factory=set)
    sdk_warnings: list[str] = field(default_factory=list)
    stopped_early: bool = False

    @property
    def visitors(self) -> int:
        """Visitors whose flag was fetched."""
        return sum(tally.visitors for tally in self.tallies.values())


def simulate_visitor(
    client: ExperimentClient,
    report: SimulationReport,
    identity: str,
    rng: random.Random,
) -> None:
    """Fetch one visitor's variant and maybe send the purchase event."""
    flag = client.get_experiment_flag(report.flag_name, identity)
    if getattr(flag, "is_default", False):
        raise SimulationError(
            f'Flag "{report.flag_name}" was not found in this Flagsmith environment.'
        )
    variant = getattr(flag, "variant", None) or NO_VARIANT
    experiment = getattr(flag, "experiment", None)
    tally = report.tallies.setdefault(variant, VariantTally())
    tally.visitors += 1
    if experiment is not None and experiment.in_experiment:
        tally.in_experiment += 1
    rate = report.rates.get(variant)
    if rate is None:
        report.missing_rates.add(variant)
    elif rng.random() < rate:
        client.track_event(report.event, identifier=identity, metadata=EVENT_METADATA)
        tally.purchases += 1


def simulate(
    client: ExperimentClient,
    report: SimulationReport,
    *,
    visitors: int,
    rng: random.Random,
    limiter: RateLimiter,
    on_progress: Callable[[int], None],
    run_id: str,
) -> SimulationReport:
    """Run ``visitors`` simulated visits; stop after repeated Flagsmith failures."""
    consecutive = 0
    for index in range(visitors):
        identity = f"{IDENTITY_PREFIX}{run_id}_{index:05d}"
        limiter.wait()
        try:
            simulate_visitor(client, report, identity, rng)
        except FlagsmithClientError as error:
            report.failures += 1
            cause = f" ({error.__cause__})" if error.__cause__ else ""
            report.first_error = report.first_error or f"{error}{cause}"
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                report.stopped_early = True
                break
        else:
            consecutive = 0
        on_progress(index + 1)
    return report


def format_report(report: SimulationReport) -> str:
    """Summary table plus any warnings, as plain text."""
    header = (
        f"{'Variant':<22}{'Visitors':>10}{'In exp.':>10}{'Purchases':>11}"
        f"{'Observed':>10}{'Target':>9}"
    )
    lines = [f"Flag {report.flag_name} - event {report.event}", header, "-" * len(header)]
    for name, tally in sorted(report.tallies.items()):
        observed = f"{tally.purchases / tally.visitors:.1%}" if tally.visitors else "-"
        target = f"{report.rates[name]:.1%}" if name in report.rates else "none"
        lines.append(
            f"{name:<22}{tally.visitors:>10}{tally.in_experiment:>10}{tally.purchases:>11}"
            f"{observed:>10}{target:>9}"
        )
    total_purchases = sum(tally.purchases for tally in report.tallies.values())
    lines.append("-" * len(header))
    lines.append(f"{'Total':<22}{report.visitors:>10}{'':>10}{total_purchases:>11}")
    lines.extend(_warnings(report))
    return "\n".join(lines)


def _warnings(report: SimulationReport) -> list[str]:
    """Human-readable problems worth knowing about."""
    notes = []
    if report.failures:
        notes.append(f"! {report.failures} request(s) to Flagsmith failed: {report.first_error}")
    if report.stopped_early:
        notes.append(f"! Stopped after {MAX_CONSECUTIVE_FAILURES} failures in a row.")
    if report.missing_rates:
        missing = ", ".join(sorted(report.missing_rates))
        notes.append(f"! No --rates entry for {missing}: no purchases were sent for them.")
    if report.visitors and not any(tally.in_experiment for tally in report.tallies.values()):
        notes.append(
            "! No visitor was in a running Flagsmith experiment, so Flagsmith recorded no "
            "exposures. Purchases were still sent."
        )
    notes.extend(f"! Flagsmith SDK: {warning}" for warning in report.sdk_warnings)
    return notes


class _WarningCollector(logging.Handler):
    """Keeps the SDK's warnings so they are shown in the summary, not lost."""

    def __init__(self, sink: list[str]) -> None:
        """Append formatted warning messages to ``sink``."""
        super().__init__(level=logging.WARNING)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        """Record one warning."""
        self.sink.append(record.getMessage())


def parse_args(
    argv: list[str] | None, default_visitors: int, default_flag: str
) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Simulate visitors against a Flagsmith flag.")
    parser.add_argument("--rates", required=True, help="e.g. control=0.08,green_buy_now=0.11")
    parser.add_argument("--visitors", type=int, default=default_visitors)
    parser.add_argument("--flag", default=default_flag, help=f"default: {default_flag}")
    parser.add_argument("--event", default="purchase")
    parser.add_argument("--rps", type=float, default=DEFAULT_RPS, help="max requests/second")
    parser.add_argument("--seed", type=int, help="random seed for repeatable conversions")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the simulation from the command line."""
    try:
        settings = load_settings()
        args = parse_args(argv, settings.sim_visitors, settings.store_flag_name)
        rates = parse_rates(args.rates)
        if not 1 <= args.visitors <= MAX_VISITORS:
            raise SimulationError(f"--visitors must be between 1 and {MAX_VISITORS}.")
        if args.rps <= 0:
            raise SimulationError("--rps must be positive.")
    except (ConfigError, SimulationError) as error:
        sys.exit(error.friendly)

    run_id = uuid.uuid4().hex[:6]
    report = SimulationReport(flag_name=args.flag, event=args.event, rates=rates)
    logging.getLogger("flagsmith").addHandler(_WarningCollector(report.sdk_warnings))
    print(
        f"Simulating {args.visitors} visitors (identities {IDENTITY_PREFIX}{run_id}_*) "
        f"at up to {args.rps:g} requests/s: about {args.visitors} Flagsmith API requests."
    )
    client = Flagsmith(environment_key=settings.sdk_environment_key(), enable_events=True)
    progress = ProgressBar(args.visitors)
    try:
        simulate(
            client,
            report,
            visitors=args.visitors,
            rng=random.Random(args.seed),
            limiter=RateLimiter(args.rps),
            on_progress=progress.update,
            run_id=run_id,
        )
    except SimulationError as error:
        print()
        sys.exit(error.friendly)
    except KeyboardInterrupt:
        print("\nInterrupted: sending the events recorded so far.")
        report.stopped_early = True
    finally:
        client.close()
    print(format_report(report))
    if report.stopped_early:
        sys.exit(1)


if __name__ == "__main__":
    main()
