"""REAL end-to-end flow against Flagsmith and NVIDIA NIM (no mocks).

plan (NIM) -> approve -> create an fp_ flag (executor + official Flagsmith MCP server)
-> read it back from Flagsmith -> clean it up (Admin API delete through the executor).

Skipped when the keys are missing (no .env and no environment variables), e.g. in CI.
Run locally with ``make test-integration``. Cost per run: 1-2 NIM requests and about
10 Flagsmith API requests. The flag is created switched off in every environment of
FLAGSMITH_PROJECT_ID and deleted again at the end, even if an assertion fails.
"""

import json
import uuid
from pathlib import Path

import pytest

from agent.admin_api import AdminAPI
from agent.approval import approve, approve_reset
from agent.audit import AuditLog
from agent.config import Settings, load_settings
from agent.errors import ConfigError
from agent.executor import delete_demo_flags, execute_plan
from agent.flagsmith_client import FlagsmithClient, results_of
from agent.llm import LLMClient
from agent.models import DemoFlag, ExperimentPlan, ResetPlan
from agent.planner import plan_experiment

pytestmark = pytest.mark.integration

REQUEST = (
    "Test a green 'Buy now' checkout button against our current grey 'Add to cart' "
    "button and measure purchases."
)
APPROVER = "integration-test"


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    try:
        return load_settings()
    except ConfigError as error:
        pytest.skip(f"Integration keys are missing: {error.friendly}")


def find_flag_id(client: FlagsmithClient, name: str) -> int | None:
    return next((flag["id"] for flag in client.list_flags() if flag["name"] == name), None)


def clean_up(settings: Settings, audit: AuditLog, flag_id: int, name: str) -> None:
    reset = ResetPlan(
        project_id=settings.flagsmith_project_id,
        project_name="integration test",
        flags=[DemoFlag(id=flag_id, name=name)],
    )
    delete_demo_flags(
        reset,
        approve_reset(reset, APPROVER, audit),
        runner=AdminAPI(settings),
        audit=audit,
    )


def test_plan_approve_create_read_back_and_clean_up(
    live_settings: Settings, tmp_path: Path
) -> None:
    audit = AuditLog(tmp_path / "audit_log.jsonl")
    client = FlagsmithClient(live_settings)
    project_id = live_settings.flagsmith_project_id
    existing = client.list_flag_names()

    # 1. Plan with the real LLM (cache off so this really calls NVIDIA NIM).
    llm = LLMClient(live_settings.model_copy(update={"llm_cache": False}))
    proposed = plan_experiment(REQUEST, existing, llm=llm)
    assert isinstance(proposed, ExperimentPlan), f"planner asked: {proposed}"

    # 2. A unique name (a reviewer's edit before approving), then approve.
    name = f"fp_it_{uuid.uuid4().hex[:8]}"
    plan = ExperimentPlan.model_validate(
        {**proposed.model_dump(), "flag_name": name},
        context={"existing_flag_names": existing},
    )
    approval = approve(plan, APPROVER, audit)

    flag_id: int | None = None
    try:
        # 3. Create it through the executor and the official MCP server.
        result = execute_plan(plan, approval, runner=client, audit=audit, project_id=project_id)
        assert all(outcome.ok for outcome in result.outcomes)

        # 4. Read it back from Flagsmith.
        flag_id = find_flag_id(client, name)
        assert flag_id is not None, f"{name} was not found in Flagsmith after creation"
        feature = client.call_tool("get_feature_flag", {"id": flag_id, "project_pk": project_id})
        assert feature["name"] == name
        assert feature["type"] == "MULTIVARIATE"
        options = results_of(
            client.call_tool(
                "list_feature_multivariate_options",
                {"feature_pk": flag_id, "project_pk": str(project_id)},
            )
        )
        served = {option["key"]: json.loads(option["string_value"]) for option in options}
        for variant in plan.variants[1:]:
            assert served[variant.name] == variant.value
    finally:
        # 5. Clean up, even if an assertion above failed.
        flag_id = flag_id or find_flag_id(client, name)
        if flag_id is not None:
            clean_up(live_settings, audit, flag_id, name)

    assert name not in client.list_flag_names()
    events = [entry.event for entry in audit.entries()]
    assert events[:3] == ["approved", "call", "result"]
    assert "error" not in events
