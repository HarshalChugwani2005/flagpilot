"""Tests for agent.llm retry, guided_json fallback and caching (OpenAI client mocked)."""

import logging
from collections.abc import Callable

import openai
import pytest
from pydantic import BaseModel

from agent.llm import LLMClient, LLMError, strip_fences
from tests.conftest import FakeChatClient, status_error


class Answer(BaseModel):
    answer: int


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('  {"a": 1}  ', '{"a": 1}'),
    ],
)
def test_strip_fences(text: str, expected: str) -> None:
    assert strip_fences(text) == expected


def test_rejected_nvext_is_logged_once_and_dropped(
    make_llm: Callable[..., LLMClient],
    fake_chat: FakeChatClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = make_llm()
    fake_chat.queue(status_error(openai.BadRequestError, 400), '{"answer": 1}', '{"answer": 2}')

    with caplog.at_level(logging.WARNING):
        assert llm.complete_json("sys", "q1", Answer).answer == 1
        assert llm.complete_json("sys", "q2", Answer).answer == 2

    assert fake_chat.calls[0]["extra_body"] == {
        "nvext": {"guided_json": Answer.model_json_schema()}
    }
    assert fake_chat.calls[1]["extra_body"] is None
    assert fake_chat.calls[2]["extra_body"] is None
    assert sum("guided_json" in record.message for record in caplog.records) == 1


def test_429_is_retried_with_exponential_backoff(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    sleeps: list[float] = []
    llm = make_llm(sleeps=sleeps)
    fake_chat.queue(
        status_error(openai.RateLimitError, 429),
        status_error(openai.InternalServerError, 503),
        "hello",
    )

    assert llm.complete_text("sys", "hi") == "hello"
    assert sleeps == [2.0, 4.0]


def test_persistent_429_gives_friendly_error(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    llm = make_llm()
    fake_chat.queue(*(status_error(openai.RateLimitError, 429) for _ in range(3)))

    with pytest.raises(LLMError, match="40/minute"):
        llm.complete_text("sys", "hi")
    assert len(fake_chat.calls) == 3


def test_bad_key_gives_friendly_error(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue(status_error(openai.AuthenticationError, 401))

    with pytest.raises(LLMError, match="NVIDIA_API_KEY"):
        make_llm().complete_text("sys", "hi")


def test_report_text_uses_reporting_temperature(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue("summary")

    make_llm().complete_text("sys", "summarise")

    assert fake_chat.calls[0]["temperature"] == 0.3
    assert fake_chat.calls[0]["extra_body"] is None


def test_cache_returns_stored_reply_without_calling_nim(
    make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient
) -> None:
    fake_chat.queue("first")

    assert make_llm(cache=True).complete_text("sys", "hi") == "first"
    assert make_llm(cache=True).complete_text("sys", "hi") == "first"
    assert len(fake_chat.calls) == 1


def test_cache_bypass(make_llm: Callable[..., LLMClient], fake_chat: FakeChatClient) -> None:
    fake_chat.queue("first", "second")
    llm = make_llm(cache=True)

    llm.complete_text("sys", "hi")

    assert llm.complete_text("sys", "hi", use_cache=False) == "second"
