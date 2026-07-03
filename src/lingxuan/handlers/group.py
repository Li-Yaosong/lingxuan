from __future__ import annotations

import time

import nonebot
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from lingxuan.admin import CommandContext, parse_command, run_command
from lingxuan._config import _cfg
from lingxuan.group_entities import learn_entities_from_entry
from lingxuan.group_observer import (
    ObservationEntry,
    append_bot_message,
    append_entry,
    format_observation,
    get_last_user_text,
    get_latest_user_entry,
    get_recent_entries,
    get_reply_target,
    group_reply_session,
    is_directed_at_bot,
    is_followup_after_bot,
    is_in_cooldown,
    is_introducing_other,
    is_seeking_engagement,
    latest_user_at_bot,
    latest_user_replies_to_bot,
    mark_last_trigger,
    mark_observed,
    record_judge_result,
    register_observe_callback,
    run_observe_loop,
    schedule_observe,
    should_bypass_cooldown,
    should_skip_observe,
)
from lingxuan.llm import (
    chat_in_group,
    chat_in_group_stream,
    chat_stream,
    schedule_summarize,
    should_reply_in_group,
    should_skip_reply_locally,
)
from lingxuan.memory import append_message, group_session, update_meta
from lingxuan.message_chunk import send_group_chunks, send_group_stream
from lingxuan.user_memory import schedule_cognition_refine, schedule_memory_extract

logger = nonebot.logger

group_handler = nonebot.on_type(GroupMessageEvent, priority=20, block=False)

_registered_groups: set[int] = set()


def _is_at_bot(event: GroupMessageEvent) -> bool:
    if getattr(event, "to_me", False):
        return True
    self_id = int(event.self_id)
    self_qq = str(self_id)
    for seg in event.message:
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        if qq is None:
            continue
        qq_str = str(qq)
        if qq_str in ("all", "所有人"):
            continue
        try:
            if int(qq) == self_id:
                return True
        except (TypeError, ValueError):
            pass
        if qq_str == self_qq:
            return True
    raw = getattr(event, "raw_message", "") or ""
    if f"qq={self_qq}" in raw or f"qq={self_id}" in raw:
        return True
    return False


def _log_at_check(event: GroupMessageEvent, at_bot: bool) -> None:
    if at_bot:
        return
    at_segments = [
        {"qq": seg.data.get("qq"), "type": seg.type}
        for seg in event.message
        if seg.type == "at"
    ]
    if at_segments:
        logger.warning(
            "at segments present but not matched self_id={} segments={!r} plain={!r}",
            event.self_id,
            at_segments,
            event.get_plaintext(),
        )


def _is_reply_bot(event: GroupMessageEvent) -> bool:
    if not event.reply:
        return False
    try:
        return int(event.reply.sender.user_id) == event.self_id
    except (ValueError, TypeError):
        return False


def _nickname(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


def _parse_at_user_ids(event: GroupMessageEvent) -> list[int]:
    self_id = int(event.self_id)
    ids: list[int] = []
    for seg in event.message:
        if seg.type != "at":
            continue
        qq = seg.data.get("qq")
        if qq is None:
            continue
        qq_str = str(qq)
        if qq_str in ("all", "所有人"):
            continue
        try:
            uid = int(qq)
        except (TypeError, ValueError):
            continue
        if uid == self_id:
            continue
        ids.append(uid)
    return ids


def _ensure_observer(group_id: int) -> None:
    if group_id in _registered_groups:
        return
    register_observe_callback(group_id, _observe_group_loop)
    _registered_groups.add(group_id)


def _should_shortcircuit_judge(group_id: int) -> tuple[bool, str]:
    bot_name = _cfg().get_str("BOT_NAME")
    if latest_user_at_bot(group_id):
        return True, "at_bot"
    if latest_user_replies_to_bot(group_id):
        return True, "reply_to_bot"
    last_text = get_last_user_text(group_id)
    if bot_name in last_text:
        return True, "name_mention"
    if is_directed_at_bot(last_text):
        return True, "directed_request"
    if is_seeking_engagement(last_text):
        return True, "engagement"
    entry = get_latest_user_entry(group_id)
    if entry and is_introducing_other(entry):
        return True, "intro_other"
    if is_followup_after_bot(group_id):
        return True, "followup"
    return False, ""


def _observation_context_lines(group_id: int, limit: int = 3) -> list[str]:
    entries = get_recent_entries(group_id, limit=limit)
    return [f"[{e.nickname}]: {e.text}" for e in entries]


def _format_exchange(nickname: str, user_text: str, bot_reply: str) -> str:
    return f"用户[{nickname}]: {user_text}\n灵轩: {bot_reply}"


async def _send_group_reply(
    bot: Bot,
    group_id: int,
    user_id: int,
    session_id: str,
    *,
    observation: str | None = None,
    use_stream: bool = True,
) -> str:
    if use_stream:
        if observation is not None:
            stream = chat_in_group_stream(session_id, observation, primary_user_id=user_id)
        else:
            stream = chat_stream(session_id, is_group=True, primary_user_id=user_id)
        return await send_group_stream(bot, group_id, user_id, stream)

    if observation is not None:
        reply = await chat_in_group(session_id, observation, primary_user_id=user_id)
    else:
        from lingxuan.llm import chat

        reply = await chat(session_id, is_group=True, primary_user_id=user_id)
    await send_group_chunks(bot, group_id, user_id, reply)
    return reply


async def _observe_group(group_id: int) -> None:
    if not _cfg().get_bool("ENABLE_GROUP_OBSERVE"):
        mark_observed(group_id)
        return

    observation = format_observation(group_id)
    if not observation.strip():
        mark_observed(group_id)
        return

    if should_skip_observe(group_id):
        logger.info("observe skipped at_others_only group={}", group_id)
        mark_observed(group_id)
        return

    shortcircuit, reason = _should_shortcircuit_judge(group_id)
    bypass_cooldown = should_bypass_cooldown(group_id)
    t0 = time.monotonic()

    if not shortcircuit and not bypass_cooldown and is_in_cooldown(group_id):
        logger.info("observe skipped cooldown group={}", group_id)
        mark_observed(group_id)
        return

    if shortcircuit:
        should_reply = True
        record_judge_result(group_id, f"yes:{reason}")
        logger.info("judge=yes group={} shortcircuit={}", group_id, reason)
    else:
        last_text = get_last_user_text(group_id)
        if should_skip_reply_locally(last_text):
            record_judge_result(group_id, "no:local_skip")
            logger.info("judge=no group={} local_skip", group_id)
            mark_observed(group_id)
            return
        should_reply = await should_reply_in_group(
            observation,
            group_id=group_id,
            primary_user_id=get_reply_target(group_id)[0] if get_reply_target(group_id) else None,
        )
        logger.info(
            "judge={} group={} judge_ms={:.0f}",
            "yes" if should_reply else "no",
            group_id,
            (time.monotonic() - t0) * 1000,
        )
        if not should_reply:
            mark_observed(group_id)
            return

    target = get_reply_target(group_id)
    if not target:
        mark_observed(group_id)
        return

    user_id, nickname = target
    session_id = group_session(group_id)
    last_text = get_last_user_text(group_id)

    append_message(
        session_id,
        "user",
        f"[{nickname}]: {last_text}",
        user_id=user_id,
    )

    bots = nonebot.get_bots()
    if not bots:
        mark_observed(group_id)
        return
    bot = next(iter(bots.values()))

    t1 = time.monotonic()
    reply = await _send_group_reply(
        bot,
        group_id,
        user_id,
        session_id,
        observation=observation,
    )
    logger.info(
        "observe reply done group={} total_ms={:.0f} stream_ms={:.0f}",
        group_id,
        (time.monotonic() - t0) * 1000,
        (time.monotonic() - t1) * 1000,
    )

    if reply:
        append_message(session_id, "assistant", reply)
        append_bot_message(group_id, reply)
        mark_last_trigger(group_id, reply_user_id=user_id)
        schedule_summarize(session_id)
        schedule_cognition_refine(
            user_id,
            recent_exchange=_format_exchange(nickname, last_text, reply),
        )

    mark_observed(group_id)


async def _observe_group_wrapped(group_id: int) -> None:
    async with group_reply_session(group_id):
        await _observe_group(group_id)


async def _observe_group_loop(group_id: int) -> None:
    await run_observe_loop(group_id, _observe_group_wrapped)


@group_handler.handle()
async def handle_group(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = _cfg()
    if event.user_id == event.self_id:
        return

    if not cfg.get_bool("ENABLE_GROUP_CHAT"):
        return

    user_message = event.get_plaintext().strip()
    nickname = _nickname(event)
    group_id = event.group_id
    session_id = group_session(group_id)

    if event.user_id in cfg.get_int_list("BOT_ADMINS"):
        parsed = parse_command(user_message)
        if parsed is not None:
            cmd, args = parsed
            ctx = CommandContext(
                user_id=event.user_id,
                session_id=session_id,
                is_group=True,
                group_id=group_id,
                nickname=nickname,
            )
            reply = await run_command(cmd, args, ctx)
            await group_handler.finish(reply)
            return

    at_bot = _is_at_bot(event)
    _log_at_check(event, at_bot)
    if not at_bot and not user_message:
        return

    update_meta(session_id, nickname=nickname, group_id=group_id)

    obs_entry = ObservationEntry(
        user_id=event.user_id,
        nickname=nickname,
        text=user_message or ("在呢" if at_bot else ""),
        at_bot=at_bot,
        reply_to_bot=_is_reply_bot(event),
        at_user_ids=_parse_at_user_ids(event),
    )
    append_entry(group_id, obs_entry)
    learn_entities_from_entry(session_id, group_id, obs_entry)
    schedule_memory_extract(
        event.user_id,
        user_message or ("在呢" if at_bot else ""),
        nickname=nickname,
        group_id=group_id,
        context_lines=_observation_context_lines(group_id),
    )

    _ensure_observer(group_id)

    if at_bot:
        clean_message = user_message or "在呢"
        async with group_reply_session(group_id):
            append_message(
                session_id,
                "user",
                f"[{nickname}]: {clean_message}",
                user_id=event.user_id,
            )
            reply = await _send_group_reply(
                bot,
                group_id,
                event.user_id,
                session_id,
                observation=None,
            )
            append_message(session_id, "assistant", reply)
            append_bot_message(group_id, reply)
            mark_observed(group_id)
            mark_last_trigger(group_id, reply_user_id=event.user_id)
            schedule_summarize(session_id)
            schedule_cognition_refine(
                event.user_id,
                recent_exchange=_format_exchange(nickname, clean_message, reply),
            )
        return

    if cfg.get_bool("ENABLE_GROUP_OBSERVE"):
        schedule_observe(group_id)
