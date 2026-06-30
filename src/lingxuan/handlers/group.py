from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

from lingxuan.config import BOT_ADMINS
from lingxuan.group_observer import (
    ObservationEntry,
    append_bot_message,
    append_entry,
    format_observation,
    get_last_user_text,
    get_reply_target,
    mark_observed,
    register_observe_callback,
    schedule_observe,
)
from lingxuan.llm import chat, chat_in_group, should_reply_in_group
from lingxuan.memory import append_message, clear_history, group_session

logger = nonebot.logger

group_handler = nonebot.on_type(GroupMessageEvent, priority=20, block=False)

_registered_groups: set[int] = set()


def _is_at_bot(event: GroupMessageEvent) -> bool:
    for seg in event.message:
        if seg.type == "at":
            try:
                if int(seg.data["qq"]) == event.self_id:
                    return True
            except (KeyError, ValueError, TypeError):
                pass
    return False


def _is_reply_bot(event: GroupMessageEvent) -> bool:
    if not event.reply:
        return False
    try:
        return int(event.reply.sender.user_id) == event.self_id
    except (ValueError, TypeError):
        return False


def _nickname(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


def _ensure_observer(group_id: int) -> None:
    if group_id in _registered_groups:
        return
    register_observe_callback(group_id, _observe_group)
    _registered_groups.add(group_id)


async def _observe_group(group_id: int) -> None:
    observation = format_observation(group_id)
    if not observation.strip():
        mark_observed(group_id)
        return

    if not await should_reply_in_group(observation):
        logger.info("judge=no group={}", group_id)
        mark_observed(group_id)
        return

    logger.info("judge=yes group={}", group_id)
    target = get_reply_target(group_id)
    if not target:
        mark_observed(group_id)
        return

    user_id, nickname = target
    session_id = group_session(group_id)
    last_text = get_last_user_text(group_id)

    append_message(session_id, "user", f"[{nickname}]: {last_text}")
    reply = await chat_in_group(session_id, observation)
    append_message(session_id, "assistant", reply)
    append_bot_message(group_id, reply)
    mark_observed(group_id)

    bots = nonebot.get_bots()
    if not bots:
        return
    bot = next(iter(bots.values()))
    await bot.send_group_msg(
        group_id=group_id,
        message=MessageSegment.at(user_id) + f" {reply}",
    )
    logger.info("reply sent group={} user={}", group_id, user_id)


@group_handler.handle()
async def handle_group(bot: Bot, event: GroupMessageEvent) -> None:
    if event.user_id == event.self_id:
        return

    user_message = event.get_plaintext().strip()
    nickname = _nickname(event)
    group_id = event.group_id

    if user_message.startswith("/灵轩 ") and event.user_id in BOT_ADMINS:
        cmd = user_message[len("/灵轩 "):].strip()
        if cmd == "重置记忆":
            clear_history(group_session(group_id))
            await group_handler.finish("记忆已清空~")
        return

    at_bot = _is_at_bot(event)
    if not at_bot and not user_message:
        return

    append_entry(
        group_id,
        ObservationEntry(
            user_id=event.user_id,
            nickname=nickname,
            text=user_message or ("在呢" if at_bot else ""),
            at_bot=at_bot,
            reply_to_bot=_is_reply_bot(event),
        ),
    )

    _ensure_observer(group_id)

    if at_bot:
        clean_message = user_message or "在呢"
        session_id = group_session(group_id)
        append_message(session_id, "user", f"[{nickname}]: {clean_message}")
        reply = await chat(session_id, is_group=True)
        append_message(session_id, "assistant", reply)
        append_bot_message(group_id, reply)
        mark_observed(group_id)
        await group_handler.finish(
            MessageSegment.at(event.user_id) + f" {reply}"
        )
        return

    schedule_observe(group_id)
