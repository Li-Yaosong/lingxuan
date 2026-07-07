"""DialogueService: private-chat & group @-direct-reply orchestration.

Migrates the business logic from MVP ``handlers/private.py`` and
``handlers/group.py`` (the @-bot direct-reply path) into Core.
Handlers only need to convert framework events into ``InboundMessage``
and call ``handle_inbound``.

No framework / IO imports — all dependencies are injected protocols.
"""

from __future__ import annotations

from lingxuan.core.admin_commands import AdminCommandService as AdminCommandService
from lingxuan.core.admin_commands import CommandContext
from lingxuan.core.group_reply_executor import GroupReplyExecutor
from lingxuan.core.observation import ObservationService
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder, build_group_reply_user
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.repositories import MemoryService, UserMemoryService
from lingxuan.protocols.messaging import (
    InboundMessage,
    MessageTransport,
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
    SessionId,
)
from lingxuan.protocols.plugins import HookType, PluginContext, PluginHost
from lingxuan.protocols.repositories import SessionRepository, StoredMessage
from lingxuan.protocols.messaging import ReplyPlan


# ---------------------------------------------------------------------------
# DialogueService
# ---------------------------------------------------------------------------


class DialogueService:
    """Orchestrates private-chat dialogue and group @-direct-reply.

    Entry point is :meth:`handle_inbound` which dispatches to
    ``_handle_private`` or ``_handle_group`` based on ``session_id.kind``.
    """

    def __init__(
        self,
        config: ConfigProvider,
        llm: LLMProvider,
        prompt: PromptBuilder,
        planner: ReplyPlanner,
        transport: MessageTransport,
        memory: MemoryService,
        user_memory: UserMemoryService,
        admin_commands: AdminCommandService,
        persona: PersonaService,
        observation: ObservationService,
        observation_store: ObservationStore,
        sessions: SessionRepository,
        clock: Clock,
        group_executor: GroupReplyExecutor,
        plugin_host: PluginHost | None = None,
    ) -> None:
        self._config = config
        self._llm = llm
        self._prompt = prompt
        self._planner = planner
        self._transport = transport
        self._memory = memory
        self._user_memory = user_memory
        self._admin_commands = admin_commands
        self._persona = persona
        self._observation = observation
        self._obs_store = observation_store
        self._sessions = sessions
        self._clock = clock
        self._plugin_host = plugin_host

        # Shared executor for group replies (injected, same instance as ObservationService)
        self._group_executor = group_executor

    # ── config helpers ────────────────────────────────────────────────────

    @property
    def _private_enabled(self) -> bool:
        return self._config.get_bool("ENABLE_PRIVATE_CHAT")

    @property
    def _group_enabled(self) -> bool:
        return self._config.get_bool("ENABLE_GROUP_CHAT")

    @property
    def _observe_enabled(self) -> bool:
        return self._config.get_bool("ENABLE_GROUP_OBSERVE")

    @property
    def _bot_name(self) -> str:
        return self._config.get_str("BOT_NAME")

    # ── main entry ────────────────────────────────────────────────────────

    async def handle_inbound(self, inbound: InboundMessage) -> None:
        """Dispatch to private or group handler based on session kind."""
        # Plugin hook: on_inbound_message
        if self._plugin_host is not None:
            ctx = PluginContext(hook=HookType.on_inbound_message, inbound=inbound)
            ctx = await self._plugin_host.dispatch(ctx)
            if ctx.cancelled:
                return

        if inbound.session_id.kind == "private":
            await self._handle_private(inbound)
        elif inbound.session_id.kind == "group":
            await self._handle_group(inbound)

    # ── private chat ──────────────────────────────────────────────────────

    async def _handle_private(self, inbound: InboundMessage) -> None:
        """Aligns with MVP ``handlers/private.handle_private``.

        Flow:
        1. ENABLE_PRIVATE_CHAT off → return; text empty → return.
        2. Admin command → dispatch → transport.send → return.
        3. Normal: user_memory → memory.append → prompt → LLM chat (non-stream)
           → memory.append → transport.send → cognition_refine → summarize.
        """
        if not self._private_enabled:
            return

        if not inbound.text.strip():
            return

        # Admin command check
        if inbound.actor.is_admin:
            parsed = self._admin_commands.parse_command(inbound.text)
            if parsed is not None:
                cmd, args = parsed
                ctx = CommandContext(
                    user_id=inbound.actor.user_id,
                    session_id=inbound.session_id,
                    nickname=inbound.actor.nickname,
                )
                reply = await self._admin_commands.run(cmd, args, ctx)
                out = OutboundMessage(
                    target=ReplyTarget(session_id=inbound.session_id),
                    chunks=[OutboundChunk(text=reply)],
                )
                await self._transport.send(out)
                return

        # Normal private chat flow
        nickname = inbound.actor.nickname or str(inbound.actor.user_id)

        await self._user_memory.on_user_message(
            inbound.actor.user_id,
            inbound.text,
            nickname=nickname,
            is_private=True,
            session_id=inbound.session_id,
        )

        await self._memory.append_message(
            inbound.session_id,
            StoredMessage(
                role="user",
                content=inbound.text,
                user_id=inbound.actor.user_id,
            ),
        )

        # Build messages and call LLM (non-streaming for private chat)
        history = await self._sessions.load_history(inbound.session_id)
        summary = await self._sessions.get_summary(inbound.session_id)

        messages = self._prompt.build_context_messages(
            is_group=False,
            history=history,
            summary=summary,
        )

        # Plugin hook: on_before_reply
        reply_plan = ReplyPlan(should_reply=True, reason="private_chat", stream=False)
        reply_plan = await self._dispatch_before_reply(inbound, reply_plan)
        if not reply_plan.should_reply:
            return

        reply = await self._llm.chat(messages)

        await self._memory.append_message(
            inbound.session_id,
            StoredMessage(role="assistant", content=reply),
        )

        out = OutboundMessage(
            target=ReplyTarget(session_id=inbound.session_id),
            chunks=[OutboundChunk(text=reply)],
        )
        await self._transport.send(out)

        # Plugin hook: on_after_reply
        await self._dispatch_after_reply(
            inbound, reply, target_session_id=inbound.session_id,
        )

        await self._user_memory.schedule_cognition_refine(
            inbound.actor.user_id,
            recent_exchange=_format_exchange(
                self._bot_name, nickname, inbound.text, reply
            ),
        )

        self._memory.schedule_summarize(inbound.session_id)

    # ── group chat ────────────────────────────────────────────────────────

    async def _handle_group(self, inbound: InboundMessage) -> None:
        """Aligns with MVP ``handlers/group.handle_group``.

        Flow:
        1. Ignore self messages; ENABLE_GROUP_CHAT off → return.
        2. Admin command dispatch (group scope).
        3. Memory/entity/schedule_memory_extract.
        4. at_bot (incl. reply_to_bot) → direct stream reply via
           GroupReplyExecutor → memory write → observation mark → summarize → cognition.
        5. Non-@ → observation.on_group_message.
        """
        if inbound.actor.is_self:
            return

        if not self._group_enabled:
            return

        # Admin command check
        if inbound.actor.is_admin:
            parsed = self._admin_commands.parse_command(inbound.text)
            if parsed is not None:
                cmd, args = parsed
                ctx = CommandContext(
                    user_id=inbound.actor.user_id,
                    session_id=inbound.session_id,
                    is_group=True,
                    group_id=inbound.group_id,
                    nickname=inbound.actor.nickname,
                )
                reply = await self._admin_commands.run(cmd, args, ctx)
                out = OutboundMessage(
                    target=ReplyTarget(session_id=inbound.session_id),
                    chunks=[OutboundChunk(text=reply)],
                )
                await self._transport.send(out)
                return

        group_id = inbound.group_id
        if group_id is None:
            return

        at_bot = inbound.at_bot or inbound.reply_to_bot

        # Empty text and not at_bot → skip (aligns with MVP)
        if not at_bot and not inbound.text.strip():
            return

        nickname = inbound.actor.nickname or str(inbound.actor.user_id)

        # Update session meta
        await self._memory.update_meta(
            inbound.session_id,
            nickname=nickname,
            group_id=group_id,
        )

        # Entity learning is handled by the group_entities plugin (on_inbound_message).
        # schedule_memory_extract remains here for LLM-based fact extraction.
        clean_message = inbound.text.strip() or ("在呢" if at_bot else "")
        await self._user_memory.schedule_memory_extract(
            inbound.actor.user_id,
            clean_message,
            nickname=nickname,
            group_id=group_id,
            context_lines=self._observation_context_lines(group_id),
        )

        if at_bot:
            # Direct-@ reply path
            await self._handle_group_at_direct(inbound, clean_message, nickname)
        else:
            # Passive observation path — delegate to ObservationService
            await self._observation.on_group_message(inbound)

    # ── group @-direct reply ──────────────────────────────────────────────

    async def _handle_group_at_direct(
        self,
        inbound: InboundMessage,
        clean_message: str,
        nickname: str,
    ) -> None:
        """@-bot direct reply: append user message → stream reply → memory.

        Aligns with the at_bot branch of MVP ``handlers/group.handle_group``:
        - Append user message to session history
        - Stream reply via GroupReplyExecutor
        - Append assistant reply, record in observation buffer, mark state
        - Schedule summarize and cognition refine
        """
        group_id = inbound.group_id
        if group_id is None:
            return

        # Write user message to session memory
        await self._memory.append_message(
            inbound.session_id,
            StoredMessage(
                role="user",
                content=f"[{nickname}]: {clean_message}",
                user_id=inbound.actor.user_id,
            ),
        )

        # Plugin hook: on_before_reply
        reply_plan = ReplyPlan(
            should_reply=True, reason="group_at_direct", stream=True,
        )
        reply_plan = await self._dispatch_before_reply(inbound, reply_plan)
        if not reply_plan.should_reply:
            return

        # Generate and send reply via GroupReplyExecutor
        reply_text = await self._group_executor.execute(
            session_id=inbound.session_id,
            observation_text=None,  # direct-@ path: no observation prompt
            at_user_id=inbound.actor.user_id,
        )

        if reply_text:
            # Write assistant message to session memory
            await self._memory.append_message(
                inbound.session_id,
                StoredMessage(role="assistant", content=reply_text),
            )

            # Record bot message in observation buffer
            self._obs_store.append_bot_message(group_id, reply_text)

            # Mark observation state so scheduler won't re-trigger
            self._observation.mark_last_trigger(
                group_id, reply_user_id=inbound.actor.user_id
            )
            self._obs_store.mark_observed(group_id)

            # Schedule summarize
            self._memory.schedule_summarize(inbound.session_id)

            # Schedule cognition refine
            await self._user_memory.schedule_cognition_refine(
                inbound.actor.user_id,
                recent_exchange=_format_exchange(
                    self._bot_name, nickname, clean_message, reply_text
                ),
            )

            # Plugin hook: on_after_reply
            await self._dispatch_after_reply(
                inbound, reply_text,
                target_session_id=inbound.session_id,
                at_user_id=inbound.actor.user_id,
            )

    # ── helpers ────────────────────────────────────────────────────────────

    def _observation_context_lines(self, group_id: int, limit: int = 3) -> list[str]:
        entries = self._obs_store.recent(group_id, limit=limit)
        return [f"[{e.nickname}]: {e.text}" for e in entries]

    # ── plugin hook helpers ────────────────────────────────────────────────

    async def _dispatch_before_reply(
        self,
        inbound: InboundMessage,
        reply_plan: ReplyPlan,
    ) -> ReplyPlan:
        """Dispatch on_before_reply and return the (possibly modified) plan."""
        if self._plugin_host is None:
            return reply_plan
        ctx = PluginContext(
            hook=HookType.on_before_reply,
            inbound=inbound,
            reply_plan=reply_plan,
        )
        ctx = await self._plugin_host.dispatch(ctx)
        return ctx.reply_plan if ctx.reply_plan is not None else reply_plan

    async def _dispatch_after_reply(
        self,
        inbound: InboundMessage,
        reply_text: str,
        *,
        target_session_id: SessionId | None = None,
        at_user_id: int | None = None,
    ) -> None:
        """Dispatch on_after_reply after a reply has been sent."""
        if self._plugin_host is None:
            return
        ctx = PluginContext(
            hook=HookType.on_after_reply,
            inbound=inbound,
            extra={
                "reply_text": reply_text,
                "session_id": target_session_id.as_str() if target_session_id else "",
                "at_user_id": at_user_id or 0,
            },
        )
        await self._plugin_host.dispatch(ctx)


def _format_exchange(
    bot_name: str, nickname: str, user_text: str, bot_reply: str
) -> str:
    """Format a user-bot exchange for cognition refine — aligns with MVP."""
    return f"用户[{nickname}]: {user_text}\n{bot_name}: {bot_reply}"
