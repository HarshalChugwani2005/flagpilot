"""Smoke test: every agent module imports cleanly."""

import importlib

import pytest

MODULES = [
    "agent.config",
    "agent.errors",
    "agent.models",
    "agent.audit",
    "agent.approval",
    "agent.changes",
    "agent.experiments",
    "agent.stats",
    "agent.rollout",
    "agent.llm",
    "agent.planner",
    "agent.guardrails",
    "agent.flagsmith_client",
    "agent.executor",
    "agent.reporter",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    importlib.import_module(name)
