"""Fake LLM provider: pre-set responses, record calls for assertions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from lingxuan.protocols.llm import ChatMessage


class FakeLLMProvider:
    """Implements LLMProvider protocol with controllable responses."""

    def __init__(self) -> None:
        self.chat_responses: list[str] = []
        self._chat_idx = 0
        self.stream_tokens: list[str] = []
        self.judge_results: list[bool] = []
        self._judge_idx = 0
        # Records of calls for assertions
        self.chat_calls: list[list[ChatMessage]] = []
        self.chat_kwargs: list[dict] = []
        self.stream_calls: list[list[ChatMessage]] = []
        self.stream_kwargs: list[dict] = []
        self.judge_calls: list[str] = []
        self.judge_kwargs: list[dict] = []

    def set_chat_response(self, text: str) -> None:
        self.chat_responses.append(text)

    def set_chat_responses(self, texts: list[str]) -> None:
        self.chat_responses.extend(texts)

    def set_stream_tokens(self, tokens: list[str]) -> None:
        self.stream_tokens = tokens

    def set_judge_results(self, results: list[bool]) -> None:
        self.judge_results = results

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        timeout: float = 30.0,
    ) -> str:
        self.chat_calls.append(messages)
        self.chat_kwargs.append(
            {"max_tokens": max_tokens, "temperature": temperature, "timeout": timeout}
        )
        if self._chat_idx < len(self.chat_responses):
            result = self.chat_responses[self._chat_idx]
            self._chat_idx += 1
            return result
        return ""

    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        self.stream_calls.append(messages)
        self.stream_kwargs.append(
            {"max_tokens": max_tokens, "temperature": temperature}
        )
        return self._iter_tokens()

    async def _iter_tokens(self) -> AsyncIterator[str]:
        for token in self.stream_tokens:
            yield token

    async def judge(
        self,
        prompt: str,
        *,
        timeout: float = 5.0,
        default: bool = False,
    ) -> bool:
        self.judge_calls.append(prompt)
        self.judge_kwargs.append({"timeout": timeout, "default": default})
        if self._judge_idx < len(self.judge_results):
            result = self.judge_results[self._judge_idx]
            self._judge_idx += 1
            return result
        return default
