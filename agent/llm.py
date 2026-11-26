"""Thin wrapper over the official openai SDK pointed at NVIDIA NIM.

``complete_json`` returns Pydantic-validated objects; ``complete_text`` returns prose.
429/5xx responses are retried with exponential backoff, and responses can be cached on
disk (``LLM_CACHE=1``) to save free-tier quota.
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from agent.config import LLMSettings, load_llm_settings
from agent.errors import ConfigError, FlagPilotError

T = TypeVar("T", bound=BaseModel)

PLANNING_TEMPERATURE = 0.0
REPORTING_TEMPERATURE = 0.3
SCHEMA_PLACEHOLDER = "{schema}"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

log = logging.getLogger(__name__)

Message = dict[str, str]


class LLMError(FlagPilotError):
    """The LLM could not be reached or did not return a usable answer."""


def strip_fences(text: str) -> str:
    """Remove a surrounding Markdown code fence (```json ... ```) if present."""
    match = _FENCE.match(text)
    return (match.group(1) if match else text).strip()


def _describe_validation(error: ValidationError) -> str:
    """Compact, LLM-readable list of validation problems."""
    lines = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"- {location}: {item['msg']}")
    return "\n".join(lines)


def _friendly_status_error(error: openai.APIStatusError) -> LLMError:
    """Map an HTTP error from NIM to a message a user can act on."""
    status = error.status_code
    if status == 401 or status == 403:
        message = "NVIDIA NIM rejected the API key. Check NVIDIA_API_KEY in .env (starts nvapi-)."
    elif status == 404:
        message = "NVIDIA NIM does not know that model. Check LLM_MODEL in .env."
    elif status == 410:
        message = (
            "NVIDIA has retired the model in LLM_MODEL. Pick a current chat model on "
            "build.nvidia.com (Free Endpoint) and put its id in LLM_MODEL in .env."
        )
    elif status == 429:
        message = (
            "NVIDIA NIM is rate-limiting requests (free tier is about 40/minute). "
            "Wait a minute and try again."
        )
    elif status >= 500:
        message = "NVIDIA NIM is having problems right now. Please try again in a moment."
    else:
        message = f"NVIDIA NIM refused the request (HTTP {status})."
    return LLMError(message, detail=str(error))


class ResponseCache:
    """On-disk cache of raw LLM replies, keyed by a hash of the model and the request."""

    def __init__(self, directory: Path) -> None:
        """Use ``directory`` (created on first write) to store one JSON file per reply."""
        self.directory = directory

    @staticmethod
    def key(model: str, messages: list[Message], temperature: float) -> str:
        """Stable SHA-256 of everything that determines the reply."""
        payload = json.dumps(
            {"model": model, "messages": messages, "temperature": temperature},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> str | None:
        """Return the cached reply for ``key``, or None (unreadable files are reported)."""
        path = self.directory / f"{key}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))["content"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError) as error:
            log.warning("Ignoring unreadable LLM cache file %s (%s); asking NIM again", path, error)
            return None

    def put(self, key: str, model: str, content: str) -> None:
        """Store ``content`` for ``key``."""
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {"model": model, "content": content}
        (self.directory / f"{key}.json").write_text(json.dumps(record), encoding="utf-8")


def cached_replies(directory: Path) -> list[Path]:
    """Return the cached reply files in ``directory`` (empty if it does not exist)."""
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def clear_cache(directory: Path) -> int:
    """Delete every cached reply in ``directory``; return how many were removed."""
    files = cached_replies(directory)
    for path in files:
        path.unlink()
    return len(files)


class LLMClient:
    """NVIDIA NIM chat client with JSON validation, retries and optional caching."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the client; ``client`` and ``sleep`` can be replaced in tests."""
        self.model = settings.llm_model
        self.max_tokens = settings.llm_max_tokens
        self._client = client or openai.OpenAI(
            base_url=str(settings.llm_base_url),
            api_key=settings.nvidia_api_key.get_secret_value(),
            max_retries=0,
            timeout=60,
        )
        self._cache = ResponseCache(settings.llm_cache_dir) if settings.llm_cache else None
        self._sleep = sleep
        self._guided_json_supported = True

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        temperature: float = REPORTING_TEMPERATURE,
        use_cache: bool = True,
    ) -> str:
        """Return the model's plain-text reply to ``user`` under ``system``."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return self._chat(messages, temperature, use_cache=use_cache)

    def complete_json(
        self,
        system: str,
        user: str,
        schema_model: type[T],
        *,
        temperature: float = PLANNING_TEMPERATURE,
        context: Mapping[str, Any] | None = None,
    ) -> T:
        """Return a ``schema_model`` instance parsed from the model's JSON reply.

        The schema is sent in the system prompt (at ``{schema}`` if the prompt has that
        placeholder, otherwise appended) and as ``nvext.guided_json``. If the reply fails
        validation (``context`` is passed to Pydantic), the model gets one retry with the
        validation errors.
        """
        schema = schema_model.model_json_schema()
        if SCHEMA_PLACEHOLDER in system:
            system_prompt = system.replace(SCHEMA_PLACEHOLDER, json.dumps(schema))
        else:
            system_prompt = (
                f"{system}\n\nOutput ONLY JSON matching this schema:\n{json.dumps(schema)}"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user},
        ]
        reply = self._chat(messages, temperature, guided_schema=schema, store=False)
        try:
            return self._validated(messages, temperature, reply, schema_model, context)
        except ValidationError as error:
            problems = _describe_validation(error)
        log.info("LLM JSON failed validation, retrying once:\n%s", problems)
        messages = messages + [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "Your JSON was invalid:\n"
                f"{problems}\nReply with corrected JSON only, matching the schema.",
            },
        ]
        reply = self._chat(messages, temperature, guided_schema=schema, store=False)
        try:
            return self._validated(messages, temperature, reply, schema_model, context)
        except ValidationError as error:
            raise LLMError(
                "The AI returned an answer FlagPilot could not use, even after a retry. "
                "Try rephrasing your request.",
                detail=_describe_validation(error),
            ) from error

    def _validated(
        self,
        messages: list[Message],
        temperature: float,
        reply: str,
        schema_model: type[T],
        context: Mapping[str, Any] | None,
    ) -> T:
        """Validate ``reply``; only a reply that validates is written to the cache."""
        result = self._validate(reply, schema_model, context)
        if self._cache:
            self._cache.put(ResponseCache.key(self.model, messages, temperature), self.model, reply)
        return result

    @staticmethod
    def _validate(reply: str, schema_model: type[T], context: Mapping[str, Any] | None) -> T:
        """Strip fences and validate ``reply`` against ``schema_model``."""
        return schema_model.model_validate_json(strip_fences(reply), context=dict(context or {}))

    def _chat(
        self,
        messages: list[Message],
        temperature: float,
        *,
        guided_schema: dict[str, Any] | None = None,
        use_cache: bool = True,
        store: bool = True,
    ) -> str:
        """Send ``messages`` (read from the cache if enabled) and return the reply text.

        ``store=False`` leaves writing to the caller, so JSON is cached only once valid.
        """
        cache = self._cache if use_cache else None
        key = ResponseCache.key(self.model, messages, temperature)
        if cache and (cached := cache.get(key)) is not None:
            return cached
        content = self._request_with_guided_fallback(messages, temperature, guided_schema)
        if cache and store:
            cache.put(key, self.model, content)
        return content

    def _request_with_guided_fallback(
        self, messages: list[Message], temperature: float, guided_schema: dict[str, Any] | None
    ) -> str:
        """Use ``nvext.guided_json`` when possible; drop it for good if the endpoint rejects it."""
        if guided_schema is None or not self._guided_json_supported:
            return self._request(messages, temperature, extra_body=None)
        try:
            return self._request(
                messages, temperature, extra_body={"nvext": {"guided_json": guided_schema}}
            )
        except (openai.BadRequestError, openai.UnprocessableEntityError) as error:
            log.warning("NIM rejected nvext.guided_json; continuing without it (%s)", error)
            self._guided_json_supported = False
            return self._request(messages, temperature, extra_body=None)

    def _request(
        self, messages: list[Message], temperature: float, *, extra_body: dict[str, Any] | None
    ) -> str:
        """One chat completion, retrying 429/5xx/connection errors with exponential backoff."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=self.max_tokens,
                    extra_body=extra_body,
                )
                break
            except (openai.RateLimitError, openai.InternalServerError) as error:
                if attempt == MAX_ATTEMPTS:
                    raise _friendly_status_error(error) from error
                self._backoff(attempt, error)
            except (openai.BadRequestError, openai.UnprocessableEntityError) as error:
                if extra_body is not None:
                    raise  # let the caller retry without nvext
                raise _friendly_status_error(error) from error
            except openai.APIStatusError as error:
                raise _friendly_status_error(error) from error
            except openai.APIConnectionError as error:
                if attempt == MAX_ATTEMPTS:
                    raise LLMError(
                        "Could not reach NVIDIA NIM. Check your internet connection.",
                        detail=str(error),
                    ) from error
                self._backoff(attempt, error)
        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice else None
        if not content:
            if choice is not None and choice.finish_reason == "length":
                raise LLMError(
                    f"The model used all {self.max_tokens} tokens thinking and gave no answer. "
                    "Raise LLM_MAX_TOKENS in .env."
                )
            raise LLMError("NVIDIA NIM returned an empty reply. Please try again.")
        return content

    def _backoff(self, attempt: int, error: Exception) -> None:
        """Sleep 2s, 4s, ... before the next attempt."""
        delay = BACKOFF_SECONDS * 2 ** (attempt - 1)
        log.warning("NIM request failed (%s); retrying in %.0fs", type(error).__name__, delay)
        self._sleep(delay)


@lru_cache(maxsize=1)
def get_llm() -> LLMClient:
    """Return the process-wide LLMClient built from ``.env``."""
    return LLMClient(load_llm_settings())


def complete_json(
    system: str,
    user: str,
    schema_model: type[T],
    *,
    temperature: float = PLANNING_TEMPERATURE,
    context: Mapping[str, Any] | None = None,
) -> T:
    """Module-level shortcut for :meth:`LLMClient.complete_json` on the default client."""
    return get_llm().complete_json(
        system, user, schema_model, temperature=temperature, context=context
    )


def complete_text(system: str, user: str, *, temperature: float = REPORTING_TEMPERATURE) -> str:
    """Module-level shortcut for :meth:`LLMClient.complete_text` on the default client."""
    return get_llm().complete_text(system, user, temperature=temperature)


def _smoke() -> None:
    """Send one tiny uncached prompt and print the model name and reply."""
    llm = get_llm()
    reply = llm.complete_text(
        "You are a terse assistant.",
        "Reply with exactly: FlagPilot OK",
        temperature=PLANNING_TEMPERATURE,
        use_cache=False,
    )
    print(f"model: {llm.model}")
    print(f"reply: {reply}")


def main() -> None:
    """CLI entry point: ``python -m agent.llm --smoke``."""
    parser = argparse.ArgumentParser(description="NVIDIA NIM client for FlagPilot")
    parser.add_argument("--smoke", action="store_true", help="send one tiny prompt")
    args = parser.parse_args()
    if not args.smoke:
        parser.print_help()
        return
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        _smoke()
    except (ConfigError, LLMError) as error:
        sys.exit(error.friendly)


if __name__ == "__main__":
    main()
