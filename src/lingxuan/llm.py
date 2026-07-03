from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import nonebot
from openai import AsyncOpenAI

from lingxuan.config import _cfg
from lingxuan.memory import (
    format_entities_for_prompt,
    load_history,
    load_session,
    save_summary,
    trim_history_half,
)
from lingxuan.persona import get_system_prompt
from lingxuan.user_memory import format_user_brief, format_user_context_for_prompt

logger = nonebot.logger

LLM_TIMEOUT = 30.0
JUDGE_TIMEOUT = 5.0
FALLBACK_REPLY = "抱歉，我现在有点不舒服，稍后再聊吧~"
FALLBACK_NO_KEY = "我还没配置好呢，让主人先设置一下 API Key 吧~"
_FALLBACK_TEXTS = frozenset({FALLBACK_REPLY, FALLBACK_NO_KEY, "no"})

# Module-level client cache (replaces @lru_cache to support config changes)
_cached_client: AsyncOpenAI | None = None
_cached_client_key: str = ""


def _is_fallback_text(text: str) -> bool:
    return text.strip() in _FALLBACK_TEXTS


def _get_client() -> AsyncOpenAI:
    global _cached_client, _cached_client_key
    api_key = _cfg().get_str("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY 未配置，请在 .env 文件中设置")
    if _cached_client is None or api_key != _cached_client_key:
        base_url = _cfg().get_str("OPENAI_BASE_URL")
        _cached_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        _cached_client_key = api_key
    return _cached_client


async def _call_llm(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    timeout: float = LLM_TIMEOUT,
    fallback: str = FALLBACK_REPLY,
) -> str:
    return await call_llm_raw(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        fallback=fallback,
    )


async def call_llm_raw(
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
            model=_cfg().get_str("OPENAI_MODEL"),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return response.choices[0].message.content or fallback
    except ValueError as e:
        logger.error("{}", e)
        return FALLBACK_NO_KEY if fallback == FALLBACK_REPLY else fallback
    except Exception:
        logger.exception("LLM call error")
        return fallback


def should_skip_reply_locally(text: str) -> bool:
    """短句调侃/骂人/无实质内容，直接跳过回复，不调 judge LLM。"""
    t = text.strip()
    if not t:
        return True
    if len(t) <= 6 and "?" not in t and "？" not in t and "吗" not in t:
        teasing = ("傻", "笨", "菜", "哈", "呵", "哼", "哦", "嗯", "6", "典")
        if any(k in t for k in teasing):
            return True
    return False


def build_context_messages(
    session_id: str,
    is_group: bool = False,
    extra_user: str | None = None,
    history_limit: int | None = None,
    primary_user_id: int | None = None,
    observation_text: str = "",
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
    if _cfg().get_bool("ENABLE_USER_MEMORY"):
        obs = observation_text
        if extra_user and "【当前群聊观察】" in extra_user:
            obs = extra_user
        user_ctx = format_user_context_for_prompt(
            primary_user_id=primary_user_id,
            observation_text=obs,
            is_private=not is_group,
        )
        if user_ctx:
            messages.append({"role": "system", "content": user_ctx})
    if is_group:
        entities_block = format_entities_for_prompt(session_id)
        if entities_block:
            messages.append({"role": "system", "content": entities_block})
    history = session.history
    if history_limit is not None and history_limit >= 0:
        history = history[-history_limit:]
    messages.extend(history)
    if extra_user:
        messages.append({"role": "user", "content": extra_user})
    return messages


def _group_llm_kwargs() -> dict[str, int]:
    cfg = _cfg()
    return {
        "history_limit": cfg.get_int("GROUP_CHAT_CONTEXT"),
        "max_tokens": cfg.get_int("GROUP_CHAT_MAX_TOKENS"),
    }


async def summarize_session(session_id: str) -> str:
    cfg = _cfg()
    session = load_session(session_id)
    if not session.history:
        return ""
    memory_window = cfg.get_int("MEMORY_WINDOW")
    lines = [
        f"{m['role']}: {m['content']}" for m in session.history[:memory_window]
    ]
    identity_note = ""
    if cfg.get_bool("ENABLE_USER_MEMORY"):
        from lingxuan.user_memory import list_user_profiles, load_user_profile, display_name

        id_lines = []
        for uid in list_user_profiles()[:20]:
            p = load_user_profile(uid)
            if p.identity.preferred_name:
                id_lines.append(f"{display_name(p)}(QQ{uid})")
        if id_lines:
            identity_note = (
                "\n\n以下称呼已确认，摘要中请使用正确称呼："
                + "、".join(id_lines)
            )
    prompt = (
        "请将以下对话压缩成简短摘要，保留关键事实和情感，"
        "尤其保留出现的人名、昵称及其指代关系，不超过200字：\n"
        + "\n".join(lines)
        + identity_note
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
    cfg = _cfg()
    if not cfg.get_bool("ENABLE_MEMORY_SUMMARY"):
        return
    if len(load_history(session_id)) <= cfg.get_int("MEMORY_WINDOW"):
        return
    await summarize_session(session_id)


def schedule_summarize(session_id: str) -> None:
    asyncio.create_task(maybe_summarize(session_id))


async def should_reply_in_group(
    observation: str,
    group_id: int | None = None,
    primary_user_id: int | None = None,
) -> bool:
    if not observation.strip():
        return False
    last_line = observation.strip().split("\n")[-1]
    if ":" in last_line:
        last_content = last_line.split(":", 1)[-1].strip()
        if should_skip_reply_locally(last_content):
            logger.info("judge=no local_skip group={}", group_id)
            if group_id is not None:
                from lingxuan.group_observer import record_judge_result

                record_judge_result(group_id, "no:local_skip")
            return False
    user_brief = ""
    cfg = _cfg()
    bot_name = cfg.get_str("BOT_NAME")
    if cfg.get_bool("ENABLE_USER_MEMORY") and primary_user_id:
        user_brief = format_user_brief(primary_user_id)
    prompt = (
        f"近期群聊：\n{observation}\n\n"
    )
    if user_brief:
        prompt += f"最后发言者：{user_brief}\n\n"
    prompt += (
        f"你是{bot_name}，正在群里观察大家聊天。判断你是否需要回应。\n"
        f"回答 yes 的情况：\n"
        f"- 有人在找{bot_name}说话（叫名字、@你、或继续跟你对话）\n"
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
        timeout=JUDGE_TIMEOUT,
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


def _parse_observe_reply(raw: str) -> tuple[bool, str]:
    text = raw.strip()
    upper = text.upper()
    if upper.startswith("SKIP") or upper == "NO":
        return False, ""
    if "REPLY:" in text:
        reply = text.split("REPLY:", 1)[1].strip()
        return bool(reply), reply
    if upper.startswith("REPLY"):
        reply = text.split(":", 1)[-1].strip() if ":" in text else text[5:].strip()
        return bool(reply), reply
    if _is_fallback_text(text):
        return False, ""
    return True, text


async def observe_and_chat_in_group(
    session_id: str,
    observation: str,
    group_id: int | None = None,
) -> tuple[bool, str]:
    bot_name = _cfg().get_str("BOT_NAME")
    context_msg = (
        f"近期群聊：\n{observation}\n\n"
        f"你是{bot_name}，正在群里观察大家聊天。\n"
        "需要回复的情况：有人 @你、叫你的名字、让你回答/回复某人、或在跟你说话。\n"
        "不需要回复：纯闲聊、别人之间对话、与你无关的技术讨论。\n"
        "若不需要参与，只输出一行：SKIP\n"
        "若需要回复，按以下格式输出：\n"
        "REPLY:\n"
        "（你的回复，简短口语化，可分句）"
    )
    kwargs = _group_llm_kwargs()
    messages = build_context_messages(
        session_id,
        is_group=True,
        extra_user=context_msg,
        history_limit=kwargs["history_limit"],
    )
    raw = await _call_llm(
        messages,
        max_tokens=kwargs["max_tokens"],
        temperature=0.7,
    )
    should_reply, reply = _parse_observe_reply(raw)
    if group_id is not None:
        from lingxuan.group_observer import record_judge_result

        record_judge_result(group_id, "yes" if should_reply else "no")
    logger.info(
        "observe+chat should_reply={} group={} reply_len={}",
        should_reply,
        group_id,
        len(reply),
    )
    return should_reply, reply


async def chat_in_group(session_id: str, observation: str, primary_user_id: int | None = None) -> str:
    context_msg = (
        f"【当前群聊观察】\n{observation}\n\n"
        "请根据以上群聊内容，自然地回复正在和你说话的人。"
    )
    kwargs = _group_llm_kwargs()
    messages = build_context_messages(
        session_id,
        is_group=True,
        extra_user=context_msg,
        history_limit=kwargs["history_limit"],
        primary_user_id=primary_user_id,
        observation_text=observation,
    )
    return await _call_llm(
        messages,
        max_tokens=kwargs["max_tokens"],
        temperature=0.7,
    )


async def chat(session_id: str, is_group: bool = False, primary_user_id: int | None = None) -> str:
    if is_group:
        kwargs = _group_llm_kwargs()
        messages = build_context_messages(
            session_id,
            is_group=True,
            history_limit=kwargs["history_limit"],
            primary_user_id=primary_user_id,
        )
        return await _call_llm(
            messages,
            max_tokens=kwargs["max_tokens"],
            temperature=0.7,
        )
    messages = build_context_messages(
        session_id,
        is_group=False,
        primary_user_id=primary_user_id,
    )
    return await _call_llm(messages, max_tokens=1024, temperature=0.7)


async def chat_stream(
    session_id: str,
    is_group: bool = False,
    extra_user: str | None = None,
    primary_user_id: int | None = None,
    observation_text: str = "",
) -> AsyncIterator[str]:
    cfg = _cfg()
    if is_group:
        kwargs = _group_llm_kwargs()
        obs = observation_text or (extra_user or "")
        messages = build_context_messages(
            session_id,
            is_group=True,
            extra_user=extra_user,
            history_limit=kwargs["history_limit"],
            primary_user_id=primary_user_id,
            observation_text=obs,
        )
        max_tokens = kwargs["max_tokens"]
    else:
        messages = build_context_messages(
            session_id,
            is_group=is_group,
            extra_user=extra_user,
            primary_user_id=primary_user_id,
        )
        max_tokens = 1024
    try:
        client = _get_client()
        stream = await client.chat.completions.create(
            model=cfg.get_str("OPENAI_MODEL"),
            messages=messages,
            temperature=0.7,
            max_tokens=max_tokens,
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


async def chat_in_group_stream(
    session_id: str,
    observation: str,
    primary_user_id: int | None = None,
) -> AsyncIterator[str]:
    context_msg = (
        f"【当前群聊观察】\n{observation}\n\n"
        "请根据以上群聊内容，自然地回复正在和你说话的人。"
    )
    async for token in chat_stream(
        session_id,
        is_group=True,
        extra_user=context_msg,
        primary_user_id=primary_user_id,
        observation_text=observation,
    ):
        yield token
