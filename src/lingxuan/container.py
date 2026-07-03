"""Lightweight DI container: lazy singleton construction with dependency ordering.

Phase 1 temporary adapters (wrapping MVP memory.py / user_memory.py) live here
as private inner classes.  Phase 2 will replace them with Repository-based
implementations injected via ``override()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lingxuan.adapters.clock import SystemClock
from lingxuan.adapters.config_provider import EnvConfigProvider
from lingxuan.adapters.logging.sink import BridgeLogSink
from lingxuan.adapters.onebot.transport import OneBotTransport
from lingxuan.adapters.openai.provider import OpenAIProvider
from lingxuan.core.admin_commands import (
    AdminCommandService,
    MemoryAccess,
    ObservationAccess,
    UserMemoryAccess,
)
from lingxuan.core.group_reply_executor import GroupReplyExecutor
from lingxuan.core.observation import ObservationService
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.logging import LogSink
from lingxuan.protocols.memory import MemoryService, UserMemoryService
from lingxuan.protocols.messaging import MessageTransport, SessionId
from lingxuan.protocols.repositories import SessionRepository, StoredMessage

if TYPE_CHECKING:
    from lingxuan.core.dialogue import DialogueService


# ---------------------------------------------------------------------------
# Phase 1 temporary adapters — thin wrappers over MVP modules
# ---------------------------------------------------------------------------


class _LegacyMemoryService:
    """Phase 1 MemoryService wrapping MVP memory.py.

    Delegates to the module-level functions in ``lingxuan.memory``.
    Phase 2 will replace this with a SessionRepository-based implementation.
    """

    async def append_message(self, session_id: SessionId, msg: StoredMessage) -> None:
        from lingxuan.memory import append_message as _append

        _append(
            session_id.as_str(),
            msg.role,
            msg.content,
            user_id=msg.user_id,
        )

    async def update_meta(
        self,
        session_id: SessionId,
        *,
        nickname: str | None = None,
        group_id: int | None = None,
    ) -> None:
        from lingxuan.memory import update_meta as _update

        kwargs: dict[str, object] = {}
        if nickname is not None:
            kwargs["nickname"] = nickname
        if group_id is not None:
            kwargs["group_id"] = group_id
        _update(session_id.as_str(), **kwargs)

    def schedule_summarize(self, session_id: SessionId) -> None:
        from lingxuan.memory import schedule_summarize as _schedule

        _schedule(session_id.as_str())


class _LegacyUserMemoryService:
    """Phase 1 UserMemoryService wrapping MVP user_memory.py.

    Delegates to the module-level functions in ``lingxuan.user_memory``.
    Phase 2 will replace this with a Repository-based implementation.
    """

    def on_user_message(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        is_private: bool = False,
        session_id: SessionId | None = None,
    ) -> None:
        from lingxuan.user_memory import on_user_message as _on

        _on(
            user_id,
            text,
            nickname=nickname,
            is_private=is_private,
            session_id=session_id.as_str() if session_id else "",
        )

    def schedule_cognition_refine(
        self,
        user_id: int,
        *,
        recent_exchange: str = "",
    ) -> None:
        from lingxuan.user_memory import schedule_cognition_refine as _schedule

        _schedule(user_id, recent_exchange=recent_exchange)

    def schedule_memory_extract(
        self,
        user_id: int,
        text: str,
        *,
        nickname: str = "",
        group_id: int | None = None,
        context_lines: list[str] | None = None,
    ) -> None:
        from lingxuan.user_memory import schedule_memory_extract as _schedule

        _schedule(
            user_id,
            text,
            nickname=nickname,
            group_id=group_id,
            context_lines=context_lines,
        )


class _LegacyMemoryAccess:
    """Phase 1 MemoryAccess for AdminCommandService, wrapping MVP memory.py."""

    async def count_messages(self, session_id: SessionId) -> int:
        from lingxuan.memory import load_history

        return len(load_history(session_id.as_str()))

    async def clear(self, session_id: SessionId) -> None:
        from lingxuan.memory import clear_history

        clear_history(session_id.as_str())

    async def get_summary(self, session_id: SessionId) -> str:
        from lingxuan.memory import get_summary

        return get_summary(session_id.as_str()) or ""

    async def get_meta(self, session_id: SessionId) -> dict:
        from lingxuan.memory import get_session_meta

        return get_session_meta(session_id.as_str())


class _LegacyUserMemoryAccess:
    """Phase 1 UserMemoryAccess for AdminCommandService, wrapping MVP user_memory.py."""

    def list_user_ids(self) -> list[int]:
        from lingxuan.user_memory import list_user_profiles

        return list_user_profiles()

    async def load_profile_summary(self, user_id: int) -> str:
        from lingxuan.user_memory import format_user_profile_summary

        return format_user_profile_summary(user_id)

    async def clear_profile(self, user_id: int) -> bool:
        from lingxuan.user_memory import clear_user_profile

        return clear_user_profile(user_id)

    async def clear_all_profiles(self) -> int:
        from lingxuan.user_memory import clear_all_user_memory

        return clear_all_user_memory()

    async def clear_social_graph(self) -> None:
        from lingxuan.user_memory import clear_social_graph

        clear_social_graph()


class _LegacyObservationAccess:
    """Phase 1 ObservationAccess for AdminCommandService, wrapping MVP group_observer.py."""

    def format_observation(self, group_id: int) -> str:
        from lingxuan.group_observer import format_observation as _fmt

        return _fmt(group_id)

    def recent_entries(self, group_id: int, limit: int = 5) -> list:
        from lingxuan.group_observer import get_recent_entries

        return get_recent_entries(group_id, limit=limit)

    def observe_state(self, group_id: int) -> dict:
        from lingxuan.group_observer import get_observe_state

        return get_observe_state(group_id)


class _LegacySessionRepository:
    """Phase 1 SessionRepository wrapping MVP memory.py.

    Only the surface needed by ObservationService and DialogueService's
    GroupReplyExecutor is implemented.  Phase 2 replaces with full DB-backed
    SessionRepository.
    """

    async def get(self, sid: SessionId):
        # Not needed by current core services; stub.
        return None

    async def ensure(self, sid: SessionId, **kwargs):
        # Not needed by current core services; stub.
        return None

    async def append_message(self, sid: SessionId, msg: StoredMessage) -> None:
        from lingxuan.memory import append_message as _append

        _append(sid.as_str(), msg.role, msg.content, user_id=msg.user_id)

    async def load_history(
        self, sid: SessionId, limit: int | None = None
    ) -> list[StoredMessage]:
        from lingxuan.memory import load_history

        raw = load_history(sid.as_str())
        if limit is not None and limit >= 0:
            raw = raw[-limit:]
        return [
            StoredMessage(
                role=m.get("role", "user"),
                content=m.get("content", ""),
                user_id=m.get("user_id"),
            )
            for m in raw
        ]

    async def count_messages(self, sid: SessionId) -> int:
        from lingxuan.memory import load_history

        return len(load_history(sid.as_str()))

    async def trim_to_last(self, sid: SessionId, n: int) -> None:
        from lingxuan.memory import load_history, save_history

        history = load_history(sid.as_str())
        save_history(sid.as_str(), history[-n:])

    async def get_summary(self, sid: SessionId) -> str:
        from lingxuan.memory import get_summary

        return get_summary(sid.as_str()) or ""

    async def set_summary(self, sid: SessionId, text: str) -> None:
        from lingxuan.memory import save_summary

        save_summary(sid.as_str(), text)

    async def clear(self, sid: SessionId) -> None:
        from lingxuan.memory import clear_history

        clear_history(sid.as_str())

    async def update_meta(self, sid: SessionId, **kwargs) -> None:
        from lingxuan.memory import update_meta

        update_meta(sid.as_str(), **kwargs)

    async def merge_entity(self, sid: SessionId, name: str, user_id: int) -> None:
        from lingxuan.memory import merge_entity

        merge_entity(sid.as_str(), name, user_id)

    async def get_entities(self, sid: SessionId) -> dict[str, int]:
        from lingxuan.memory import get_entities

        return get_entities(sid.as_str())

    async def list_sessions(self) -> list[str]:
        # Not needed by current core services; stub.
        return []


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class Container:
    """Lightweight DI container: lazy singletons, topological construction order.

    Public properties expose the assembled services.  ``override()`` allows
    Phase 2+ to swap individual factories (e.g. replace _LegacySessionRepository
    with a real DB-backed one) without touching the wiring logic.
    """

    def __init__(self) -> None:
        self._cache: dict[str, object] = {}

    # ── override mechanism ────────────────────────────────────────────────

    def override(self, key: str, instance_or_factory: type | object) -> None:
        """Inject a pre-built instance or factory for *key*.

        Must be called *before* the first access to the corresponding
        property.  Typical use: tests inject fakes; Phase 2 swaps
        ``session_repo`` with a real DB-backed SessionRepository.
        """
        if key in self._cache:
            raise RuntimeError(
                f"Cannot override '{key}': already instantiated. "
                "Call override() before first property access."
            )
        if isinstance(instance_or_factory, type):
            self._cache[key] = instance_or_factory()
        else:
            self._cache[key] = instance_or_factory

    def _get_or_build(self, key: str) -> object:
        if key in self._cache:
            return self._cache[key]
        builder = getattr(self, f"_build_{key}", None)
        if builder is None:
            raise KeyError(f"No builder registered for '{key}'")
        instance = builder()
        self._cache[key] = instance
        return instance

    # ── builders (one per service, called lazily) ─────────────────────────

    def _build_config(self) -> EnvConfigProvider:
        from lingxuan.config import set_global_config

        provider = EnvConfigProvider()
        set_global_config(provider)
        return provider

    def _build_clock(self) -> SystemClock:
        return SystemClock()

    def _build_log(self) -> BridgeLogSink:
        return BridgeLogSink()

    def _build_llm(self) -> OpenAIProvider:
        return OpenAIProvider(self.config, self.log)

    def _build_transport(self) -> OneBotTransport:
        return OneBotTransport(self.config, self.log)

    def _build_persona(self) -> PersonaService:
        return PersonaService(self.config)

    def _build_prompt(self) -> PromptBuilder:
        return PromptBuilder(self.persona, self.config)

    def _build_planner(self) -> ReplyPlanner:
        return ReplyPlanner(self.config)

    def _build_observation_store(self) -> ObservationStore:
        return ObservationStore(self.config, self.clock)

    def _build_group_executor(self) -> GroupReplyExecutor:
        return GroupReplyExecutor(
            prompt=self.prompt,
            llm=self.llm,
            planner=self.planner,
            transport=self.transport,
            sessions=self.session_repo,
            config=self.config,
        )

    def _build_session_repo(self) -> _LegacySessionRepository:
        return _LegacySessionRepository()

    def _build_memory(self) -> _LegacyMemoryService:
        return _LegacyMemoryService()

    def _build_user_memory(self) -> _LegacyUserMemoryService:
        return _LegacyUserMemoryService()

    def _build_memory_access(self) -> _LegacyMemoryAccess:
        return _LegacyMemoryAccess()

    def _build_user_memory_access(self) -> _LegacyUserMemoryAccess:
        return _LegacyUserMemoryAccess()

    def _build_observation_access(self) -> _LegacyObservationAccess:
        return _LegacyObservationAccess()

    def _build_observation(self) -> ObservationService:
        return ObservationService(
            store=self.observation_store,
            executor=self.group_executor,
            llm=self.llm,
            sessions=self.session_repo,
            memory=self.memory,
            user_memory=self.user_memory,
            config=self.config,
            clock=self.clock,
        )

    def _build_admin_commands(self) -> AdminCommandService:
        return AdminCommandService(
            config=self.config,
            memory=self.memory_access,
            user_memory=self.user_memory_access,
            observation=self.observation_access,
        )

    def _build_dialogue(self) -> "DialogueService":
        from lingxuan.core.dialogue import DialogueService

        return DialogueService(
            config=self.config,
            llm=self.llm,
            prompt=self.prompt,
            planner=self.planner,
            transport=self.transport,
            memory=self.memory,
            user_memory=self.user_memory,
            admin_commands=self.admin_commands,
            persona=self.persona,
            observation=self.observation,
            observation_store=self.observation_store,
            sessions=self.session_repo,
            clock=self.clock,
            group_executor=self.group_executor,
        )

    # ── public properties (lazy singletons) ───────────────────────────────

    @property
    def config(self) -> ConfigProvider:
        return self._get_or_build("config")  # type: ignore[return-value]

    @property
    def clock(self) -> Clock:
        return self._get_or_build("clock")  # type: ignore[return-value]

    @property
    def log(self) -> LogSink:
        return self._get_or_build("log")  # type: ignore[return-value]

    @property
    def llm(self) -> LLMProvider:
        return self._get_or_build("llm")  # type: ignore[return-value]

    @property
    def transport(self) -> MessageTransport:
        return self._get_or_build("transport")  # type: ignore[return-value]

    @property
    def persona(self) -> PersonaService:
        return self._get_or_build("persona")  # type: ignore[return-value]

    @property
    def prompt(self) -> PromptBuilder:
        return self._get_or_build("prompt")  # type: ignore[return-value]

    @property
    def planner(self) -> ReplyPlanner:
        return self._get_or_build("planner")  # type: ignore[return-value]

    @property
    def observation_store(self) -> ObservationStore:
        return self._get_or_build("observation_store")  # type: ignore[return-value]

    @property
    def group_executor(self) -> GroupReplyExecutor:
        return self._get_or_build("group_executor")  # type: ignore[return-value]

    @property
    def session_repo(self) -> SessionRepository:
        return self._get_or_build("session_repo")  # type: ignore[return-value]

    @property
    def memory(self) -> MemoryService:
        return self._get_or_build("memory")  # type: ignore[return-value]

    @property
    def user_memory(self) -> UserMemoryService:
        return self._get_or_build("user_memory")  # type: ignore[return-value]

    @property
    def memory_access(self) -> MemoryAccess:
        return self._get_or_build("memory_access")  # type: ignore[return-value]

    @property
    def user_memory_access(self) -> UserMemoryAccess:
        return self._get_or_build("user_memory_access")  # type: ignore[return-value]

    @property
    def observation_access(self) -> ObservationAccess:
        return self._get_or_build("observation_access")  # type: ignore[return-value]

    @property
    def observation(self) -> ObservationService:
        return self._get_or_build("observation")  # type: ignore[return-value]

    @property
    def admin_commands(self) -> AdminCommandService:
        return self._get_or_build("admin_commands")  # type: ignore[return-value]

    @property
    def dialogue(self) -> "DialogueService":
        return self._get_or_build("dialogue")  # type: ignore[return-value]


def build_container() -> Container:
    """Build the default Container with EnvConfigProvider + SystemClock + BridgeLogSink.

    This is the single entry point for constructing a production Container.
    Tests should construct Container directly and call ``override()`` with
    fakes before accessing any properties.
    """
    return Container()
