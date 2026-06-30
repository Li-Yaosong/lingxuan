from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import PrivateMessageEvent

from lingxuan.llm import chat
from lingxuan.memory import append_message, user_session

private_handler = nonebot.on_type(PrivateMessageEvent, priority=10, block=True)


@private_handler.handle()
async def handle_private(event: PrivateMessageEvent) -> None:
    user_message = event.get_plaintext().strip()
    if not user_message:
        return

    session_id = user_session(event.user_id)
    append_message(session_id, "user", user_message)

    reply = await chat(session_id, is_group=False)
    append_message(session_id, "assistant", reply)

    await private_handler.finish(reply)
