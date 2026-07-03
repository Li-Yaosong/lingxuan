from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import PrivateMessageEvent

from lingxuan.admin import CommandContext, parse_command, run_command
from lingxuan.config import _cfg
from lingxuan.llm import chat, schedule_summarize
from lingxuan.memory import append_message, update_meta, user_session
from lingxuan.user_memory import on_user_message, schedule_cognition_refine

private_handler = nonebot.on_type(PrivateMessageEvent, priority=10, block=True)


@private_handler.handle()
async def handle_private(event: PrivateMessageEvent) -> None:
    cfg = _cfg()
    if not cfg.get_bool("ENABLE_PRIVATE_CHAT"):
        return

    user_message = event.get_plaintext().strip()
    if not user_message:
        return

    session_id = user_session(event.user_id)
    nickname = event.sender.nickname or str(event.user_id)

    if event.user_id in cfg.get_int_list("BOT_ADMINS"):
        parsed = parse_command(user_message)
        if parsed is not None:
            cmd, args = parsed
            ctx = CommandContext(
                user_id=event.user_id,
                session_id=session_id,
                nickname=nickname,
            )
            reply = await run_command(cmd, args, ctx)
            await private_handler.finish(reply)
            return

    update_meta(session_id, nickname=nickname)
    on_user_message(
        event.user_id,
        user_message,
        nickname=nickname,
        is_private=True,
        session_id=session_id,
    )
    append_message(session_id, "user", user_message, user_id=event.user_id)

    reply = await chat(session_id, is_group=False, primary_user_id=event.user_id)
    append_message(session_id, "assistant", reply)

    schedule_cognition_refine(
        event.user_id,
        recent_exchange=f"用户: {user_message}\n灵轩: {reply}",
    )

    await private_handler.finish(reply)
    schedule_summarize(session_id)
