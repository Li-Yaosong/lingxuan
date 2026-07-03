"""GroupReplyExecutor: shared group reply generation and sending.

Encapsulates prompt→LLM→planner→transport for group replies.
Shared between DialogueService (@-direct) and ObservationService (passive reply).
Returns the full reply text so the caller can persist it to memory.
"""

from __future__ import annotations

from lingxuan.core.prompting import PromptBuilder, build_group_reply_user
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.messaging import MessageTransport, ReplyTarget, SessionId
from lingxuan.protocols.repositories import SessionRepository


class GroupReplyExecutor:
    """Build messages, stream LLM, plan chunks, send via transport.

    Returns the full reply text so the caller can persist it to memory.
    Shared between DialogueService (direct-@ reply) and ObservationService
    (passive observation reply).
    """

    def __init__(
        self,
        prompt: PromptBuilder,
        llm: LLMProvider,
        planner: ReplyPlanner,
        transport: MessageTransport,
        sessions: SessionRepository,
        config: ConfigProvider,
    ) -> None:
        self._prompt = prompt
        self._llm = llm
        self._planner = planner
        self._transport = transport
        self._sessions = sessions
        self._config = config

    @property
    def _group_chat_context(self) -> int:
        return self._config.get_int("GROUP_CHAT_CONTEXT")

    @property
    def _group_chat_max_tokens(self) -> int:
        return self._config.get_int("GROUP_CHAT_MAX_TOKENS")

    async def execute(
        self,
        session_id: SessionId,
        observation_text: str | None = None,
        at_user_id: int | None = None,
    ) -> str:
        """Generate and send a group reply. Returns the full reply text.

        When *observation_text* is ``None`` (direct-@ path), builds a simple
        group-context prompt without an extra observation block — the user
        message is already in history.  When non-``None`` (observation path),
        uses it as the extra user block.
        """
        history = await self._sessions.load_history(
            session_id, limit=self._group_chat_context
        )
        summary = await self._sessions.get_summary(session_id)

        extra_user: str | None = None
        if observation_text is not None:
            extra_user = build_group_reply_user(observation_text)

        messages = self._prompt.build_context_messages(
            is_group=True,
            history=history,
            summary=summary,
            extra_user=extra_user,
            history_limit=self._group_chat_context,
        )

        # LLM stream → planner → transport
        reply_target = ReplyTarget(session_id=session_id, at_user_id=at_user_id)
        token_iter = self._llm.chat_stream(
            messages,
            max_tokens=self._group_chat_max_tokens,
        )
        chunk_iter = self._planner.plan_stream(
            token_iter, at_user_id=at_user_id
        )
        reply_text = await self._transport.send_stream(reply_target, chunk_iter)
        return reply_text
