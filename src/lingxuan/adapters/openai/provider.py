"""OpenAI-compatible LLM provider adapter.

Implements the LLMProvider protocol using ``openai.AsyncOpenAI``.
No prompt assembly, no business orchestration — pure LLM I/O.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from lingxuan.protocols.llm import ChatMessage

if TYPE_CHECKING:
    from lingxuan.protocols.config import ConfigProvider
    from lingxuan.protocols.logging import LogSink

# ── Constants (migrated from MVP llm.py) ──────────────────────────────────

LLM_TIMEOUT: float = 30.0
JUDGE_TIMEOUT: float = 5.0
FALLBACK_REPLY: str = "抱歉，我现在有点不舒服，稍后再聊吧~"
FALLBACK_NO_KEY: str = "我还没配置好呢，让主人先设置一下 API Key 吧~"

_YES_PATTERNS = frozenset({"yes", "是", "需要"})
_NO_PATTERNS = frozenset({"no", "否"})


class OpenAIProvider:
    """LLMProvider backed by an OpenAI-compatible API.

    Args:
        config: ConfigProvider — reads ``OPENAI_API_KEY``,
            ``OPENAI_BASE_URL``, ``OPENAI_MODEL``.
        log: Optional LogSink for structured logging; falls back to
            ``logging.getLogger("lingxuan.openai")``.
    """

    def __init__(
        self,
        config: ConfigProvider,
        log: LogSink | None = None,
    ) -> None:
        self._config = config
        self._log = log
        self._logger = logging.getLogger("lingxuan.openai")
        # Lazy-created client; invalidated when API key changes.
        self._client: AsyncOpenAI | None = None
        self._cached_api_key: str = ""
        self._cached_base_url: str = ""
        # Subscribe to config changes so we can rebuild the client.
        self._config.subscribe(self._on_config_change)

    # ── Config change handling ─────────────────────────────────────────

    def _on_config_change(self, key: str, _value: object) -> None:
        if key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            self._client = None  # force rebuild on next call

    def _get_client(self) -> AsyncOpenAI | None:
        """Return a cached client, rebuilding if the key/base_url changed."""
        api_key = self._config.get_str("OPENAI_API_KEY")
        base_url = self._config.get_str("OPENAI_BASE_URL")
        if not api_key:
            return None
        if (
            self._client is not None
            and api_key == self._cached_api_key
            and base_url == self._cached_base_url
        ):
            return self._client
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self._cached_api_key = api_key
        self._cached_base_url = base_url
        return self._client

    def _model(self) -> str:
        return self._config.get_str("OPENAI_MODEL")

    # ── Logging helper ─────────────────────────────────────────────────

    def _emit(self, level: str, msg: str, **extra: object) -> None:
        if self._log is not None:
            from lingxuan.protocols.logging import LogRecord
            from datetime import datetime

            self._log.emit(LogRecord(
                ts=datetime.now(), level=level, logger="openai", msg=msg, extra=extra,
            ))
        else:
            log_fn = getattr(self._logger, level.lower())
            # ``exc_info`` is a logging kwarg, not an ``extra`` key.
            exc = extra.pop("exc_info", None)  # type: ignore[arg-type]
            log_fn(msg, extra=extra, exc_info=exc)  # type: ignore[arg-type]

    # ── LLMProvider interface ──────────────────────────────────────────

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        timeout: float = LLM_TIMEOUT,
    ) -> str:
        client = self._get_client()
        if client is None:
            self._emit("WARNING", "No API key configured, returning fallback")
            return FALLBACK_NO_KEY
        try:
            response = await client.chat.completions.create(
                model=self._model(),
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return response.choices[0].message.content or FALLBACK_REPLY
        except Exception:
            self._emit("ERROR", "LLM chat call failed", exc_info=True)
            return FALLBACK_REPLY

    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        return self._chat_stream_impl(messages, max_tokens=max_tokens, temperature=temperature)

    async def _chat_stream_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        client = self._get_client()
        if client is None:
            self._emit("WARNING", "No API key configured, yielding fallback")
            yield FALLBACK_NO_KEY
            return
        try:
            stream = await client.chat.completions.create(
                model=self._model(),
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                timeout=LLM_TIMEOUT,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except Exception:
            self._emit("ERROR", "LLM stream error, ending stream")

    async def judge(
        self,
        prompt: str,
        *,
        timeout: float = JUDGE_TIMEOUT,
        default: bool = False,
    ) -> bool:
        client = self._get_client()
        if client is None:
            self._emit("WARNING", "No API key configured, judge returns default")
            return default
        try:
            response = await client.chat.completions.create(
                model=self._model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10,
                timeout=timeout,
            )
            text = (response.choices[0].message.content or "").strip().lower()
            return _parse_judge(text, default)
        except Exception:
            self._emit("ERROR", "LLM judge call failed, returning default")
            return default


def _parse_judge(text: str, default: bool = False) -> bool:
    """Parse yes/no judge output, aligned with MVP ``should_reply_in_group``.

    - Starts with "yes"/"是"/"需要" → True
    - Starts with "no"/"否" → False
    - Otherwise → *default*
    """
    lower = text.lower()
    for pattern in _YES_PATTERNS:
        if lower.startswith(pattern):
            return True
    for pattern in _NO_PATTERNS:
        if lower.startswith(pattern):
            return False
    return default
