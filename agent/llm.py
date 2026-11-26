"""NVIDIA NIM client (OpenAI-compatible) that returns Pydantic-validated JSON."""

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def complete_json(system_prompt: str, user_prompt: str, schema: type[T]) -> T:
    """Ask the LLM for JSON matching ``schema`` via ``nvext.guided_json`` and validate it.

    Retries HTTP 429 with exponential backoff and uses the disk cache when ``LLM_CACHE=1``.
    """
    raise NotImplementedError("agent.llm.complete_json is implemented in a later step.")
