from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from openai import AsyncOpenAI

from lingxuan.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from lingxuan.memory import load_history
from lingxuan.persona import get_system_prompt

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY 未配置，请在 .env 文件中设置")
    return AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def _build_messages(
    session_id: str,
    is_group: bool = False,
) -> list[dict[str, str]]:
    system_prompt = get_system_prompt(is_group=is_group)
    history = load_history(session_id)
    return [{"role": "system", "content": system_prompt}] + history


async def chat(
    session_id: str,
    is_group: bool = False,
) -> str:
    messages = _build_messages(session_id, is_group)
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
        )
        return response.choices[0].message.content or ""
    except ValueError as e:
        logger.error(str(e))
        return "我还没配置好呢，让主人先设置一下 API Key 吧~"
    except Exception:
        logger.exception("LLM chat error")
        return "抱歉，我现在有点不舒服，稍后再聊吧~"


async def chat_stream(
    session_id: str,
    is_group: bool = False,
) -> AsyncIterator[str]:
    messages = _build_messages(session_id, is_group)
    try:
        client = _get_client()
        stream = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
    except ValueError as e:
        logger.error(str(e))
        yield "我还没配置好呢，让主人先设置一下 API Key 吧~"
    except Exception:
        logger.exception("LLM stream error")
        yield "抱歉，我现在有点不舒服，稍后再聊吧~"
