"""Seed the demo: plan, approve and create fp_pricing_cta, then simulate 1,500 visitors.

    python scripts/seed_demo.py     (or: make seed)

The plan comes from the real planner (NVIDIA NIM) and is created through the executor
and the official Flagsmith MCP server, exactly as in the app; you approve it by typing
the flag name. The traffic simulator then sends ~1,500 Flagsmith API requests.
"""

import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.approval import approve, record_plan  # noqa: E402
from agent.audit import AuditLog  # noqa: E402
from agent.changes import build_changes  # noqa: E402
from agent.config import load_settings  # noqa: E402
from agent.errors import FlagPilotError  # noqa: E402
from agent.executor import execute_plan  # noqa: E402
from agent.flagsmith_client import FlagsmithClient  # noqa: E402
from agent.guardrails import validate_plan  # noqa: E402
from agent.models import ExperimentPlan  # noqa: E402
from agent.planner import plan_experiment  # noqa: E402
from scripts.common import confirm, operator_name  # noqa: E402
from scripts.traffic_sim import main as run_traffic  # noqa: E402

SEED_FLAG = "fp_pricing_cta"
VARIANTS = ["control", "variant"]
VISITORS = 1500
RATES = "control=0.08,variant=0.115"
SEED_REQUEST = (
    "Test a new pricing call-to-action button on the product page against the current "
    "one and measure purchases. Name the flag fp_pricing_cta and use exactly two "
    "variants named control and variant. Each variant's value is a JSON object with a "
    "hex colour under 'color' and the button text under 'label'."
)


class SeedError(FlagPilotError):
    """The demo could not be seeded."""


def check_seed_plan(plan: ExperimentPlan) -> None:
    """Require the flag name and variant names the simulator's rates expect."""
    names = [variant.name for variant in plan.variants]
    if plan.flag_name != SEED_FLAG or names != VARIANTS:
        raise SeedError(
            f"The planner proposed {plan.flag_name} with variants {names}; the seed needs "
            f"{SEED_FLAG} with {VARIANTS}. Run make seed again."
        )


def describe(plan: ExperimentPlan, project_id: int) -> list[str]:
    """Return the plan and the exact Flagsmith changes, for the confirmation prompt."""
    lines = [f"Hypothesis: {plan.hypothesis}", f"Traffic in experiment: {plan.traffic_percent}%"]
    lines += [f"  {variant.name}: {variant.value}" for variant in plan.variants]
    lines.append("Flagsmith changes:")
    for call in build_changes(plan, project_id):
        lines += [f"  {number}. {line}" for number, line in enumerate(call.summary, start=1)]
    lines.append(f"Then: simulate {VISITORS} visitors with rates {RATES}")
    return lines


def main(input_fn: Callable[[str], str] = input) -> None:
    """Plan, confirm, create the experiment, then run the traffic simulator."""
    try:
        settings = load_settings()
        audit = AuditLog(settings.audit_log_path)
        client = FlagsmithClient(settings)
        print("Reading existing flags from Flagsmith…")
        existing = client.list_flag_names()
        if SEED_FLAG in existing:
            raise SeedError(f"{SEED_FLAG} already exists. Run make reset first.")
        print(f"Planning with {settings.llm_model}…")
        plan = plan_experiment(SEED_REQUEST, existing)
        if not isinstance(plan, ExperimentPlan):
            raise SeedError("The planner asked questions instead: " + " ".join(plan.questions))
        check_seed_plan(plan)
        calls = build_changes(plan, settings.flagsmith_project_id)
        problems = validate_plan(
            plan, calls, allow_production=settings.allow_production, first_run=True
        )
        if problems:
            raise SeedError("The guardrails blocked the seed plan: " + " ".join(problems))
        print("\n".join(describe(plan, settings.flagsmith_project_id)))
        if not confirm(f'Type "{SEED_FLAG}" to approve and create it', SEED_FLAG, input_fn):
            print("Cancelled. Nothing was changed.")
            return
        record_plan(plan, audit)
        approval = approve(plan, operator_name(), audit)
        execute_plan(
            plan,
            approval,
            runner=client,
            audit=audit,
            project_id=settings.flagsmith_project_id,
            allow_production=settings.allow_production,
            on_call=lambda call, outcome: print(f"  done: {call.summary[0]}"),
        )
    except FlagPilotError as error:
        sys.exit(error.friendly + (f"\nDetails: {error.detail}" if error.detail else ""))
    run_traffic(
        ["--flag", SEED_FLAG, "--visitors", str(VISITORS), "--rates", RATES, "--control", "control"]
    )
    print(f"Seeded {SEED_FLAG}. Open the app's Experiments page to see it.")


if __name__ == "__main__":
    main()
