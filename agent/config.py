"""Typed application settings loaded from environment variables and ``.env``."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.errors import ConfigError


class Settings(BaseSettings):
    """All runtime configuration. Secrets are ``SecretStr`` so they never print."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    nvidia_api_key: SecretStr = Field(min_length=1)
    llm_model: str = "meta/llama-3.3-70b-instruct"
    llm_base_url: HttpUrl = HttpUrl("https://integrate.api.nvidia.com/v1")
    llm_cache: bool = False
    llm_cache_dir: Path = Path(".llm_cache")

    flagsmith_admin_api_key: SecretStr = Field(min_length=1)
    flagsmith_api_url: HttpUrl = HttpUrl("https://api.flagsmith.com/api/v1/")
    flagsmith_project_id: int = Field(gt=0)
    flagsmith_environment_key: SecretStr = Field(min_length=1)

    sim_visitors: int = Field(default=500, gt=0)
    audit_log_path: Path = Path("audit_log.jsonl")


def _describe(error: ValidationError) -> str:
    """Turn a pydantic ValidationError into a short list of env var names and problems."""
    parts = []
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "settings"
        problem = "is missing" if item["type"] == "missing" else item["msg"].lower()
        parts.append(f"{name} {problem}")
    return "; ".join(parts)


def load_settings(**overrides: object) -> Settings:
    """Build Settings, raising ConfigError with a friendly message if anything is wrong."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as error:
        raise ConfigError(
            "FlagPilot is not configured yet. Copy .env.example to .env and fill it in: "
            + _describe(error)
        ) from error


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings instance (loaded once)."""
    return load_settings()
