from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

import asyncio
import nonebot
from openai import AsyncOpenAI

from lingxuan.config import (
    BOT_NAME,
    ENABLE_MEMORY_SUMMARY,
    MEMORY_WINDOW,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)
from lingxuan.memory import (
    load_history,
    load_session,
    save_summary,
    trim_history_half,
)
from lingxuan.persona import get_system_prompt

logger = nonebot.logger

LLM_TIMEOUT = 30.0
FALLBACK_REPLY = "抱歉，我现在有点不舒服，稍后再聊吧~"
FALLBACK_NO_KEY = "我还没配置好呢，让主人先设置一下 API Key 吧~"
_FALLBACK_TEXTS = frozenset({FALLBACK_REPLY, FALLBACK_NO_KEY, "no"})


def _is_fallback_text(text: str) -> bool:
    return text.strip() in _FALLBACK_TEXTS


@lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY 未配置，请在 .env 文件中设置")
    return AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


async def _call_llm(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout: float = LLM_TIMEOUT,
    fallback: str = FALLBACK_REPLY,
) -> str:
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return response.choices[0].message.content or fallback
    except ValueError as e:
        logger.error("{}", e)
        return FALLBACK_NO_KEY
    except Exception:
        logger.exception("LLM call error")
        return fallback


def build_context_messages(
    session_id: str,
    is_group: bool = False,
    extra_user: str | None = None,
) -> list[dict[str, str]]:
    system_prompt = get_system_prompt(is_group=is_group)
    session = load_session(session_id)
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if session.summary:
        messages.append(
            {
                "role": "system",
                "content": f"【此前对话摘要】\n{session.summary}",
            }
        )
    messages.extend(session.history)
    if extra_user:
        messages.append({"role": "user", "content": extra_user})
    return messages


async def summarize_session(session_id: str) -> str:
    session = load_session(session_id)
    if not session.history:
        return ""
    lines = [
        f"{m['role']}: {m['content']}" for m in session.history[: MEMORY_WINDOW]
    ]
    prompt = (
        "请将以下对话压缩成简短摘要，保留关键事实和情感，不超过200字：\n"
        + "\n".join(lines)
    )
    summary = await _call_llm(
        [{"role": "user", "content": prompt}],
        max_tokens=256,
        temperature=0.3,
    )
    if _is_fallback_text(summary):
        logger.warning("summarize skipped, LLM returned fallback session_id={}", session_id)
        return ""
    save_summary(session_id, summary)
    trim_history_half(session_id)
    logger.info("session summarized session_id={}", session_id)
    return summary


async def maybe_summarize(session_id: str) -> None:
    if not ENABLE_MEMORY_SUMMARY:
        return
    if len(load_history(session_id)) <= MEMORY_WINDOW:
        return
    await summarize_session(session_id)


def schedule_summarize(session_id: str) -> None:
    asyncio.create_task(maybe_summarize(session_id))


async def should_reply_in_group(
    observation: str,
    group_id: int | None = None,
) -> bool:
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
    result = await _call_llm(
        [{"role": "user", "content": prompt}],
        max_tokens=10,
        temperature=0.0,
        fallback="no",
    )
    result = result.strip().lower()
    logger.info("judge result={!r} group={}", result, group_id)
    if group_id is not None:
        from lingxuan.group_observer import record_judge_result

        record_judge_result(group_id, result)
    if result.startswith("yes") or result.startswith("是"):
        return True
    if result.startswith("no") or result.startswith("否"):
        return False
    return result.startswith("y")


judge_reply_need = should_reply_in_group


async def chat_in_group(session_id: str, observation: str) -> str:
    context_msg = (
        f"【当前群聊观察】\n{observation}\n\n"
        "请根据以上群聊内容，自然地回复正在和你说话的人。"
    )
    messages = build_context_messages(session_id, is_group=True, extra_user=context_msg)
    return await _call_llm(messages, max_tokens=1024, temperature=0.7)


async def chat(session_id: str, is_group: bool = False) -> str:
    messages = build_context_messages(session_id, is_group=is_group)
    return await _call_llm(messages, max_tokens=1024, temperature=0.7)


async def chat_stream(
    session_id: str,
    is_group: bool = False,
) -> AsyncIterator[str]:
    messages = build_context_messages(session_id, is_group=is_group)
    try:
        client = _get_client()
        stream = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
            stream=True,
            timeout=LLM_TIMEOUT,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
    except ValueError as e:
        logger.error("{}", e)
        yield FALLBACK_NO_KEY
    except Exception:
        logger.exception("LLM stream error")
        yield FALLBACK_REPLY
