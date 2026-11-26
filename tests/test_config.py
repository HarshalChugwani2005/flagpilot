"""Tests for agent.config."""

import pytest

from agent.config import load_settings
from agent.errors import ConfigError

REQUIRED = {
    "NVIDIA_API_KEY": "nvapi-test",
    "FLAGSMITH_ADMIN_API_KEY": "admin-test",
    "FLAGSMITH_PROJECT_ID": "42",
    "FLAGSMITH_ENVIRONMENT_KEY": "env-test",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove every FlagPilot variable so tests don't depend on the developer's shell."""
    for name in [*REQUIRED, "LLM_MODEL", "LLM_CACHE", "SIM_VISITORS"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_loads_required_and_defaults(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        clean_env.setenv(name, value)

    settings = load_settings(_env_file=None)

    assert settings.flagsmith_project_id == 42
    assert settings.nvidia_api_key.get_secret_value() == "nvapi-test"
    assert settings.llm_model == "meta/llama-3.3-70b-instruct"
    assert settings.sim_visitors == 500
    assert "nvapi-test" not in repr(settings)


def test_missing_values_give_friendly_error(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=None)

    message = excinfo.value.friendly
    assert "Copy .env.example to .env" in message
    for name in REQUIRED:
        assert f"{name} is missing" in message


def test_invalid_value_is_reported(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        clean_env.setenv(name, value)
    clean_env.setenv("SIM_VISITORS", "0")

    with pytest.raises(ConfigError, match="SIM_VISITORS"):
        load_settings(_env_file=None)
