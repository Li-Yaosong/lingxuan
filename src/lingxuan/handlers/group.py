from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment

from lingxuan.config import BOT_ADMINS
from lingxuan.llm import chat
from lingxuan.memory import append_message, clear_history, group_session

group_handler = nonebot.on_type(GroupMessageEvent, priority=20, block=False)


def _is_triggered(event: GroupMessageEvent) -> bool:
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")) == str(event.self_id):
            return True
    if event.reply:
        if str(event.reply.sender.user_id) == str(event.self_id):
            return True
    return False


@group_handler.handle()
async def handle_group(event: GroupMessageEvent) -> None:
    user_message = event.get_plaintext().strip()

    if user_message.startswith("/灵轩 ") and event.user_id in BOT_ADMINS:
        cmd = user_message[len("/灵轩 "):].strip()
        if cmd == "重置记忆":
            clear_history(group_session(event.group_id))
            await group_handler.finish("记忆已清空~")
        return

    if not _is_triggered(event):
        return

    clean_message = user_message
    if not clean_message:
        clean_message = "在呢"

    session_id = group_session(event.group_id)
    append_message(session_id, "user", f"[用户{event.user_id}]: {clean_message}")

    reply = await chat(session_id, is_group=True)
    append_message(session_id, "assistant", reply)

    await group_handler.finish(
        MessageSegment.at(event.user_id) + f" {reply}"
    )
