"""PromptBuilder: pure prompt assembly logic migrated from MVP llm.py.

Produces ``list[ChatMessage]`` for LLM consumption. No IO, no LLM calls,
no framework imports. All data is passed as parameters.
"""

from __future__ import annotations

from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import ChatMessage
from lingxuan.protocols.repositories import StoredMessage
from lingxuan.core.persona import PersonaService


class PromptBuilder:
    """Assemble context messages from persona, history, and optional blocks.

    Injected with ``PersonaService`` and ``ConfigProvider`` so that runtime
    config changes (BOT_NAME, ENABLE_USER_MEMORY, etc.) are reflected
    without module-level constants.
    """

    def __init__(self, persona: PersonaService, config: ConfigProvider) -> None:
        self._persona = persona
        self._config = config

    def build_context_messages(
        self,
        *,
        is_group: bool,
        history: list[StoredMessage],
        summary: str = "",
        user_context_text: str = "",
        entities_text: str = "",
        extra_user: str | None = None,
        history_limit: int | None = None,
    ) -> list[ChatMessage]:
        """Build the ordered message list for an LLM call.

        Assembly order (matches MVP ``llm.build_context_messages``):
        1. system: persona system prompt
        2. system (optional): summary block when summary is non-empty
        3. system (optional): user context when ENABLE_USER_MEMORY is true
        4. system (optional): group entities block when is_group
        5. history messages (truncated by history_limit for group)
        6. user (optional): extra_user
        """
        messages: list[ChatMessage] = []

        # 1. system prompt
        messages.append(
            ChatMessage(role="system", content=self._persona.get_system_prompt(is_group))
        )

        # 2. summary
        if summary:
            messages.append(
                ChatMessage(role="system", content=f"【此前对话摘要】\n{summary}")
            )

        # 3. user context (ENABLE_USER_MEMORY gate)
        if self._config.get_bool("ENABLE_USER_MEMORY") and user_context_text:
            messages.append(ChatMessage(role="system", content=user_context_text))

        # 4. group entities
        if is_group and entities_text:
            messages.append(ChatMessage(role="system", content=entities_text))

        # 5. history (with optional truncation)
        effective_history = history
        if history_limit is not None and history_limit >= 0:
            effective_history = history[-history_limit:]
        for msg in effective_history:
            messages.append(ChatMessage(role=msg.role, content=msg.content))

        # 6. extra user message
        if extra_user:
            messages.append(ChatMessage(role="user", content=extra_user))

        return messages


# ---------------------------------------------------------------------------
# Standalone pure functions for prompt text construction
# ---------------------------------------------------------------------------

def build_judge_prompt(
    observation_text: str, *, user_brief: str = "", bot_name: str = "灵轩"
) -> str:
    """Build the yes/no judge prompt for group observation.

    Aligns with MVP ``should_reply_in_group`` user prompt text.
    ``bot_name`` is injected by the caller from ConfigProvider.
    """
    prompt = f"近期群聊：\n{observation_text}\n\n"
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
    return prompt


def build_summary_prompt(
    history: list[StoredMessage],
    *,
    memory_window: int = 20,
    identity_note: str = "",
) -> str:
    """Build the session summary prompt.

    Aligns with MVP ``summarize_session`` prompt text.
    ``memory_window`` is injected by the caller from ConfigProvider.
    """
    lines = [f"{m.role}: {m.content}" for m in history[:memory_window]]
    prompt = (
        "请将以下对话压缩成简短摘要，保留关键事实和情感，"
        "尤其保留出现的人名、昵称及其指代关系，不超过200字：\n"
        + "\n".join(lines)
        + identity_note
    )
    return prompt


def build_group_reply_user(observation: str) -> str:
    """Build the user message for group chat reply.

    Aligns with MVP ``chat_in_group[_stream]`` extra_user text.
    """
    return (
        f"【当前群聊观察】\n{observation}\n\n"
        "请根据以上群聊内容，自然地回复正在和你说话的人。"
    )


def should_skip_reply_locally(text: str) -> bool:
    """Short-circuit: skip reply for teasing/short messages without substance.

    Aligns with MVP ``llm.should_skip_reply_locally``:
    ≤6 chars, no question mark or 吗, and contains a teasing keyword.
    """
    t = text.strip()
    if not t:
        return True
    if len(t) <= 6 and "?" not in t and "？" not in t and "吗" not in t:
        teasing = ("傻", "笨", "菜", "哈", "呵", "哼", "哦", "嗯", "6", "典")
        if any(k in t for k in teasing):
            return True
    return False
