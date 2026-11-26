"""Typed application settings loaded from environment variables and ``.env``."""

from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import Field, HttpUrl, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.errors import ConfigError

S = TypeVar("S", bound=BaseSettings)


class LLMSettings(BaseSettings):
    """Settings needed to talk to NVIDIA NIM (enough on their own for ``agent.llm --smoke``)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    nvidia_api_key: SecretStr = Field(min_length=1)
    llm_model: str = Field(default="nvidia/nemotron-3.5-super-vl-120b-a12b", min_length=1)
    llm_base_url: HttpUrl = HttpUrl("https://integrate.api.nvidia.com/v1")
    llm_cache: bool = False
    # Reasoning models spend tokens thinking before they answer, so leave headroom.
    llm_max_tokens: int = Field(default=4096, ge=256)
    llm_cache_dir: Path = Path(".llm_cache")


class Settings(LLMSettings):
    """All runtime configuration. Secrets are ``SecretStr`` so they never print."""

    flagsmith_admin_api_key: SecretStr = Field(min_length=1)
    flagsmith_api_url: HttpUrl = HttpUrl("https://api.flagsmith.com/api/v1/")
    flagsmith_project_id: int = Field(gt=0)
    flagsmith_environment_key: SecretStr = Field(min_length=1)
    flagsmith_app_url: HttpUrl = HttpUrl("https://app.flagsmith.com")
    flagsmith_server_key: SecretStr | None = None
    flagsmith_mcp_source: str = (
        "git+https://github.com/Flagsmith/flagsmith.git@main#subdirectory=mcp"
    )

    def sdk_environment_key(self) -> str:
        """Server-side key for the Python SDK if set, else the client-side key."""
        key = self.flagsmith_server_key or self.flagsmith_environment_key
        return key.get_secret_value()

    store_flag_name: str | None = None

    sim_visitors: int = Field(default=500, gt=0)
    audit_log_path: Path = Path("audit_log.jsonl")

    allow_production: bool = False
    dry_run: bool = False
    manual_baseline_minutes: float | None = Field(default=None, gt=0)

    def flagsmith_features_url(self) -> str:
        """Dashboard page listing this environment's flags."""
        base = str(self.flagsmith_app_url).rstrip("/")
        environment = self.flagsmith_environment_key.get_secret_value()
        return f"{base}/project/{self.flagsmith_project_id}/environment/{environment}/features"


def _describe(error: ValidationError) -> str:
    """Turn a pydantic ValidationError into a short list of env var names and problems."""
    parts = []
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "settings"
        problem = "is missing" if item["type"] == "missing" else item["msg"].lower()
        parts.append(f"{name} {problem}")
    return "; ".join(parts)


def _load(settings_cls: type[S], overrides: dict[str, object]) -> S:
    """Instantiate ``settings_cls``, converting validation errors into a friendly ConfigError."""
    try:
        return settings_cls(**overrides)
    except ValidationError as error:
        raise ConfigError(
            "FlagPilot is not configured yet. Copy .env.example to .env and fill it in: "
            + _describe(error)
        ) from error


def load_settings(**overrides: object) -> Settings:
    """Build the full Settings, raising ConfigError with a friendly message if anything is wrong."""
    return _load(Settings, overrides)


def load_llm_settings(**overrides: object) -> LLMSettings:
    """Build only the LLM settings, so the NVIDIA key can be checked before Flagsmith is set up."""
    return _load(LLMSettings, overrides)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings instance (loaded once)."""
    return load_settings()
