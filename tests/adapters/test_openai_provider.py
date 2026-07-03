"""Tests for OpenAIProvider: mock AsyncOpenAI, verify fallback/judge/stream."""

from __future__ import annotations

import pytest

from lingxuan.adapters.openai.provider import (
    FALLBACK_NO_KEY,
    FALLBACK_REPLY,
    JUDGE_TIMEOUT,
    LLM_TIMEOUT,
    OpenAIProvider,
    _parse_judge,
)
from lingxuan.protocols.llm import ChatMessage
from tests.fakes.config import FakeConfigProvider


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_config(**overrides: object) -> FakeConfigProvider:
    """Create a FakeConfigProvider with optional overrides."""
    return FakeConfigProvider(overrides)


def _make_provider(**config_overrides: object) -> OpenAIProvider:
    return OpenAIProvider(config=_make_config(**config_overrides))


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)
        self.delta = _FakeDelta(content)


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeCompletion:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeStreamChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeStreamResponse:
    """Async iterator yielding stream chunks."""

    def __init__(self, tokens: list[str | None]) -> None:
        self._tokens = tokens
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._tokens):
            raise StopAsyncIteration
        token = self._tokens[self._idx]
        self._idx += 1
        return _FakeStreamChunk(token)


class _FakeAsyncOpenAI:
    """Minimal mock of AsyncOpenAI.chat.completions."""

    def __init__(
        self,
        *,
        response: str | None = "hello",
        stream_tokens: list[str | None] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._response = response
        self._stream_tokens = stream_tokens
        self._raise_error = raise_error
        self.chat = _FakeChat(self)


class _FakeChat:
    def __init__(self, client: _FakeAsyncOpenAI) -> None:
        self._client = client
        self.completions = _FakeCompletions(client)

    # Allow monkeypatching at the completions level


class _FakeCompletions:
    def __init__(self, client: _FakeAsyncOpenAI) -> None:
        self._client = client

    async def create(self, **kwargs):
        if self._client._raise_error:
            raise RuntimeError("LLM error")
        if kwargs.get("stream"):
            return _FakeStreamResponse(self._client._stream_tokens or [])
        return _FakeCompletion(self._client._response)


# ── Tests: no API key ──────────────────────────────────────────────────────


class TestNoApiKey:
    async def test_chat_returns_fallback_no_key(self):
        provider = _make_provider(OPENAI_API_KEY="")
        result = await provider.chat(
            [ChatMessage(role="user", content="hi")],
        )
        assert result == FALLBACK_NO_KEY

    async def test_judge_returns_default_no_key(self):
        provider = _make_provider(OPENAI_API_KEY="")
        assert await provider.judge("test?", default=False) is False
        assert await provider.judge("test?", default=True) is True

    async def test_chat_stream_yields_fallback_no_key(self):
        provider = _make_provider(OPENAI_API_KEY="")
        tokens = [t async for t in provider.chat_stream(
            [ChatMessage(role="user", content="hi")],
        )]
        assert tokens == [FALLBACK_NO_KEY]


# ── Tests: chat ────────────────────────────────────────────────────────────


class TestChat:
    async def test_chat_returns_response(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="world")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        result = await provider.chat(
            [ChatMessage(role="user", content="hi")],
        )
        assert result == "world"

    async def test_chat_fallback_on_error(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(raise_error=True)
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        result = await provider.chat(
            [ChatMessage(role="user", content="hi")],
        )
        assert result == FALLBACK_REPLY

    async def test_chat_fallback_on_empty_content(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response=None)
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        result = await provider.chat(
            [ChatMessage(role="user", content="hi")],
        )
        assert result == FALLBACK_REPLY

    async def test_chat_passes_kwargs(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        captured: dict = {}
        fake_client = _FakeAsyncOpenAI(response="ok")

        async def _capture_create(**kwargs):
            captured.update(kwargs)
            return _FakeCompletion("ok")

        fake_client.chat.completions.create = _capture_create
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)

        await provider.chat(
            [ChatMessage(role="user", content="hi")],
            max_tokens=512,
            temperature=0.3,
            timeout=10.0,
        )
        assert captured["max_tokens"] == 512
        assert captured["temperature"] == 0.3
        assert captured["timeout"] == 10.0

    async def test_chat_converts_messages_to_dicts(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        captured: list = []
        fake_client = _FakeAsyncOpenAI(response="ok")

        async def _capture_create(**kwargs):
            captured.append(kwargs["messages"])
            return _FakeCompletion("ok")

        fake_client.chat.completions.create = _capture_create
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)

        msgs = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
        ]
        await provider.chat(msgs)
        assert captured[0] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]


# ── Tests: chat_stream ────────────────────────────────────────────────────


class TestChatStream:
    async def test_stream_yields_tokens(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(
            stream_tokens=["Hello", " world", "!"],
        )
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        tokens = [t async for t in provider.chat_stream(
            [ChatMessage(role="user", content="hi")],
        )]
        assert tokens == ["Hello", " world", "!"]

    async def test_stream_skips_none_deltas(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(
            stream_tokens=["Hi", None, "!"],
        )
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        tokens = [t async for t in provider.chat_stream(
            [ChatMessage(role="user", content="hi")],
        )]
        assert tokens == ["Hi", "!"]

    async def test_stream_ends_on_error(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(raise_error=True)
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        tokens = [t async for t in provider.chat_stream(
            [ChatMessage(role="user", content="hi")],
        )]
        assert tokens == []


# ── Tests: judge ───────────────────────────────────────────────────────────


class TestJudge:
    async def test_judge_yes(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="yes")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("should I reply?") is True

    async def test_judge_yes_chinese(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="是")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("should I reply?") is True

    async def test_judge_need(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="需要")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("should I reply?") is True

    async def test_judge_no(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="no")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("should I reply?") is False

    async def test_judge_no_chinese(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="否")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("should I reply?") is False

    async def test_judge_gibberish_returns_default(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(response="maybe so")
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("test?", default=False) is False
        assert await provider.judge("test?", default=True) is True

    async def test_judge_error_returns_default(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        fake_client = _FakeAsyncOpenAI(raise_error=True)
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        assert await provider.judge("test?", default=False) is False

    async def test_judge_uses_low_max_tokens(self, monkeypatch):
        provider = _make_provider(OPENAI_API_KEY="test-key")
        captured: dict = {}
        fake_client = _FakeAsyncOpenAI(response="yes")

        async def _capture_create(**kwargs):
            captured.update(kwargs)
            return _FakeCompletion("yes")

        fake_client.chat.completions.create = _capture_create
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)

        await provider.judge("test?")
        assert captured["max_tokens"] == 10
        assert captured["temperature"] == 0.0


# ── Tests: _parse_judge unit ──────────────────────────────────────────────


class TestParseJudge:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("yes", True),
            ("Yes", True),
            ("YES", True),
            ("yes, I think so", True),
            ("是", True),
            ("是的", True),
            ("需要", True),
            ("需要回复", True),
            ("no", False),
            ("No", False),
            ("NO", False),
            ("no, skip", False),
            ("否", False),
            ("否决", False),
            ("maybe", False),  # default=False
            ("不确定", False),  # default=False
        ],
    )
    def test_parse_judge_cases(self, text, expected):
        assert _parse_judge(text, default=False) is expected

    def test_parse_judge_default_true(self):
        assert _parse_judge("maybe", default=True) is True


# ── Tests: client rebuild on config change ─────────────────────────────────


class TestConfigChange:
    async def test_client_rebuilt_on_key_change(self):
        config = _make_config(OPENAI_API_KEY="old-key")
        provider = OpenAIProvider(config=config)
        # First call builds client with old key
        client1 = provider._get_client()
        assert client1 is not None
        # Simulate config change
        await config.set("OPENAI_API_KEY", "new-key")
        # Client should be invalidated and rebuilt
        client2 = provider._get_client()
        assert client2 is not None
        assert client1 is not client2

    async def test_client_rebuilt_on_base_url_change(self):
        config = _make_config(OPENAI_API_KEY="key", OPENAI_BASE_URL="http://old")
        provider = OpenAIProvider(config=config)
        client1 = provider._get_client()
        await config.set("OPENAI_BASE_URL", "http://new")
        client2 = provider._get_client()
        assert client1 is not client2

    async def test_client_not_rebuilt_on_unrelated_change(self):
        config = _make_config(OPENAI_API_KEY="key")
        provider = OpenAIProvider(config=config)
        client1 = provider._get_client()
        await config.set("BOT_NAME", "test")
        client2 = provider._get_client()
        assert client1 is client2


# ── Tests: constants match MVP ─────────────────────────────────────────────


class TestConstants:
    def test_timeout_values(self):
        assert LLM_TIMEOUT == 30.0
        assert JUDGE_TIMEOUT == 5.0

    def test_fallback_texts(self):
        assert FALLBACK_REPLY == "抱歉，我现在有点不舒服，稍后再聊吧~"
        assert FALLBACK_NO_KEY == "我还没配置好呢，让主人先设置一下 API Key 吧~"
