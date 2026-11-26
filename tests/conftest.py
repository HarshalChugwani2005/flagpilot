"""Shared test helpers: a scripted stand-in for the OpenAI chat completions client."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import httpx2
import openai
import pytest

from agent.audit import AuditLog
from agent.config import Settings, load_llm_settings, load_settings
from agent.llm import LLMClient
from agent.models import ExperimentPlan

PLAN_DATA: dict[str, Any] = {
    "flag_name": "fp_checkout_button_color",
    "hypothesis": "A green Buy now button will increase purchases because it stands out.",
    "variants": [
        {"name": "control", "value": {"color": "#6b7280", "label": "Add to cart"}},
        {"name": "green_buy_now", "value": {"color": "#16a34a", "label": "Buy now"}},
    ],
    "traffic_percent": 50,
    "split": {"control": 50, "green_buy_now": 50},
    "metric_name": "Purchase conversion",
    "metric_event": "purchase",
    "environment": "development",
    "min_sample_size": 300,
    "risks": ["Low traffic may delay a clear result."],
}


def make_plan(**changes: Any) -> ExperimentPlan:
    """A valid ExperimentPlan with optional field overrides."""
    return ExperimentPlan.model_validate({**PLAN_DATA, **changes})


class FakeRunner:
    """Answers Flagsmith calls like Flagsmith would; optionally fails with ``error``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error
        self._options: list[dict[str, Any]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        if name == "create_feature":
            self._options = []
            return {"id": 99, "name": arguments.get("name"), "multivariate_options": []}
        if name == "create_feature_multivariate_option":
            option = {
                "id": 500 + len(self._options),
                "key": arguments.get("key"),
                "string_value": arguments.get("string_value"),
            }
            self._options.append(option)
            return option
        if name == "list_feature_multivariate_options":
            return {"results": self._options}
        if name == "admin_api.create_metric":
            return {"id": 31, "name": arguments.get("name")}
        if name == "create_experiment":
            return {"id": 41, "name": arguments.get("name"), "status": "created"}
        if name == "admin_api.start_experiment":
            return {"id": arguments.get("id"), "status": "running"}
        return {"id": 99, "name": arguments.get("name")}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        _env_file=None,
        nvidia_api_key="nvapi-test",
        flagsmith_admin_api_key="admin-test",
        flagsmith_project_id=7,
        flagsmith_environment_key="env-test",
        audit_log_path=tmp_path / "audit_log.jsonl",
    )


@pytest.fixture
def audit(settings: Settings) -> AuditLog:
    return AuditLog(settings.audit_log_path)


class FakeChatClient:
    """Mimics ``openai.OpenAI().chat.completions.create`` with a queue of scripted replies.

    Each queued item is either reply text or an exception instance to raise.
    """

    def __init__(self) -> None:
        self.replies: list[str | Exception] = []
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def queue(self, *replies: str | Exception) -> None:
        self.replies.extend(replies)

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        message = SimpleNamespace(content=reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def status_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    """Build a real openai status error for ``status``."""
    request = httpx2.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
    response = httpx2.Response(status, request=request)
    return cls(f"HTTP {status}", response=response, body=None)


@pytest.fixture
def fake_chat() -> FakeChatClient:
    return FakeChatClient()


@pytest.fixture
def make_llm(fake_chat: FakeChatClient, tmp_path: Path) -> Callable[..., LLMClient]:
    """Factory for an LLMClient wired to ``fake_chat``; sleeps are recorded, not taken."""

    def factory(*, cache: bool = False, sleeps: list[float] | None = None) -> LLMClient:
        settings = load_llm_settings(
            _env_file=None,
            nvidia_api_key="nvapi-test",
            llm_cache=cache,
            llm_cache_dir=tmp_path / "cache",
        )
        record = sleeps if sleeps is not None else []
        return LLMClient(settings, client=fake_chat, sleep=record.append)

    return factory


@pytest.fixture(autouse=True)
def no_real_http(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must never reach Flagsmith or NIM; only integration tests may."""
    if request.node.get_closest_marker("integration"):
        return

    def refuse(self: Any, http_request: Any) -> Any:
        raise AssertionError(f"Unit test tried to reach the network: {http_request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
