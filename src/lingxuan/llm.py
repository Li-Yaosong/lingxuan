from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

import nonebot
from openai import AsyncOpenAI

from lingxuan.config import BOT_NAME, OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from lingxuan.memory import load_history
from lingxuan.persona import get_system_prompt

logger = nonebot.logger


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


async def should_reply_in_group(observation: str) -> bool:
    if not observation.strip():
        return False
    prompt = (
        f"近期群聊：\n{observation}\n\n"
        f"你是{BOT_NAME}，正在群里观察大家聊天。判断你是否需要回应。\n"
        f"回答 yes 的情况：\n"
        f"- 有人在找{BOT_NAME}说话（叫名字、@你、或继续跟你对话）\n"
        f"- 有人在群里求助、诉苦、提问，像是在等有人接话，你可以关心一下\n"
        f"回答 no 的情况：\n"
        f"- 明显是别人之间的闲聊，与你无关\n"
        f"- 不需要你参与\n"
        f"只回答 yes 或 no。"
    )
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        result = (response.choices[0].message.content or "").strip().lower()
        logger.info("judge result={!r}", result)
        if result.startswith("yes") or result.startswith("是"):
            return True
        if result.startswith("no") or result.startswith("否"):
            return False
        return result.startswith("y")
    except ValueError as e:
        logger.error(str(e))
        return False
    except Exception:
        logger.exception("LLM judge error")
        return False


async def chat_in_group(session_id: str, observation: str) -> str:
    system_prompt = get_system_prompt(is_group=True)
    history = load_history(session_id)
    context_msg = (
        f"【当前群聊观察】\n{observation}\n\n"
        "请根据以上群聊内容，自然地回复正在和你说话的人。"
    )
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": context_msg}]
    )
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
        logger.exception("LLM chat_in_group error")
        return "抱歉，我现在有点不舒服，稍后再聊吧~"


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
