"""Lightweight DI container: lazy singleton construction with dependency ordering.

Phase 2: SQLite Repository implementations replace the Phase 1 legacy wrappers.
All persistence now goes through Database + Sql*Repository classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lingxuan.adapters.clock import SystemClock
from lingxuan.adapters.config_provider import EnvConfigProvider
from lingxuan.adapters.logging.sink import RingBufferLogSink
from lingxuan.adapters.onebot.transport import OneBotTransport
from lingxuan.adapters.openai.provider import OpenAIProvider
from lingxuan.adapters.storage.db import Database
from lingxuan.adapters.storage.repositories import (
    SqlAdminUserRepository,
    SqlAuditRepository,
    SqlConfigRepository,
    SqlPluginConfigRepository,
    SqlSessionRepository,
    SqlSocialGraphRepository,
    SqlUserProfileRepository,
)
from lingxuan.core.admin_commands import (
    AdminCommandService,
    MemoryAccess,
    ObservationAccess,
    UserMemoryAccess,
)
from lingxuan.core.group_reply_executor import GroupReplyExecutor
from lingxuan.core.memory import MemoryService
from lingxuan.core.observation import ObservationService
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.core.stats import StatsService
from lingxuan.core.user_memory import UserMemoryService
from lingxuan.plugins.host import DefaultPluginHost
from lingxuan.plugins.loader import PluginLoader
from lingxuan.plugins.services import PluginServices
from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.logging import LogSink
from lingxuan.protocols.memory import MemoryService as MemoryServiceProtocol
from lingxuan.protocols.memory import UserMemoryService as UserMemoryServiceProtocol
from lingxuan.protocols.messaging import MessageTransport, SessionId
from lingxuan.protocols.plugins import HookType, PluginContext
from lingxuan.protocols.repositories import (
    AdminUserRepository,
    AuditRepository,
    ConfigRepository,
    PluginConfigRepository,
    SessionRepository,
    SocialGraphRepository,
    StoredMessage,
    UserProfileRepository,
)

if TYPE_CHECKING:
    from lingxuan.core.dialogue import DialogueService


# ---------------------------------------------------------------------------
# DB-backed MemoryAccess / UserMemoryAccess / ObservationAccess for AdminCommandService
# ---------------------------------------------------------------------------


class _DbMemoryAccess:
    """MemoryAccess backed by SessionRepository (DB)."""

    def __init__(self, sessions: SessionRepository) -> None:
        self._sessions = sessions

    async def count_messages(self, session_id: SessionId) -> int:
        return await self._sessions.count_messages(session_id)

    async def clear(self, session_id: SessionId) -> None:
        await self._sessions.clear(session_id)

    async def get_summary(self, session_id: SessionId) -> str:
        return await self._sessions.get_summary(session_id)

    async def get_meta(self, session_id: SessionId) -> dict:
        session = await self._sessions.get(session_id)
        if session is None:
            return {}
        meta: dict[str, object] = {}
        if session.nickname:
            meta["nickname"] = session.nickname
        if session.last_active_at:
            meta["last_active_at"] = session.last_active_at.isoformat()
        if session.group_id is not None:
            meta["group_id"] = session.group_id
        return meta


class _DbUserMemoryAccess:
    """UserMemoryAccess backed by UserProfileRepository + SocialGraphRepository."""

    def __init__(
        self,
        profiles: UserProfileRepository,
        graph: SocialGraphRepository,
    ) -> None:
        self._profiles = profiles
        self._graph = graph

    async def _list_user_ids_async(self) -> list[int]:
        return await self._profiles.list_user_ids()

    def list_user_ids(self) -> list[int]:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Inside an existing event loop (e.g. NoneBot) — schedule a task.
            # AdminCommandService.caller runs in an async context anyway,
            # but the MemoryAccess protocol declares this as sync.
            # We use a thread-based fallback to avoid blocking.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._list_user_ids_async())
                return future.result()
        return asyncio.run(self._list_user_ids_async())

    async def load_profile_summary(self, user_id: int) -> str:
        from lingxuan.core.models import display_name, stage_label

        profile = await self._profiles.get(user_id)
        if profile is None:
            return ""
        lines = [
            f"用户: {display_name(profile)} (QQ {user_id})",
            f"关系: {stage_label(profile.stage)}",
            f"互动次数: {profile.interaction_count}",
            f"首选称呼: {profile.preferred_name or '(未设置)'}",
        ]
        if profile.aliases:
            lines.append(f"别名: {', '.join(profile.aliases)}")
        if profile.cognition_summary:
            lines.append(f"认知总结: {profile.cognition_summary}")
        elif profile.impression:
            lines.append(f"印象: {profile.impression}")
        return "\n".join(lines)

    async def clear_profile(self, user_id: int) -> bool:
        return await self._profiles.delete(user_id)

    async def clear_all_profiles(self) -> int:
        n = await self._profiles.delete_all()
        await self._graph.clear()
        return n

    async def clear_social_graph(self) -> None:
        await self._graph.clear()


class _DbObservationAccess:
    """ObservationAccess backed by ObservationStore."""

    def __init__(self, observation_store: ObservationStore) -> None:
        self._store = observation_store

    def format_observation(self, group_id: int) -> str:
        entries = self._store.buffer(group_id)
        if not entries:
            return ""
        lines: list[str] = []
        for entry in entries:
            name = entry.nickname or str(entry.user_id)
            lines.append(f"[{name}]: {entry.text}")
        return "\n".join(lines)

    def recent_entries(self, group_id: int, limit: int = 5) -> list:
        return self._store.recent(group_id, limit=limit)

    def observe_state(self, group_id: int) -> dict:
        state = self._store.state(group_id)
        return {
            "buffer_len": len(self._store.buffer(group_id)),
            "last_judge_result": state.last_judge_result,
            "in_cooldown": state.cooldown_until > 0,
            "cooldown_remaining": max(0, state.cooldown_until),
        }


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class Container:
    """Lightweight DI container: lazy singletons, topological construction order.

    Public properties expose the assembled services.  ``override()`` allows
    tests to swap individual factories with fakes before first access.

    Boot order: config (no DB) → db → repos → config_repo → attach DB to
    config → services.  The config is built first *without* db_repo so that
    ``DB_URL`` can be read from env/defaults; then config_repo is created
    and retroactively attached.
    """

    def __init__(self) -> None:
        self._cache: dict[str, object] = {}

    # ── override mechanism ────────────────────────────────────────────────

    def override(self, key: str, instance_or_factory: type | object) -> None:
        """Inject a pre-built instance or factory for *key*.

        Must be called *before* the first access to the corresponding
        property.  Typical use: tests inject fakes.
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
        """Build config WITHOUT db_repo (breaks the config→db→config cycle).

        After db + config_repo are built, ``_attach_config_db`` is called
        to wire them together.
        """
        provider = EnvConfigProvider()
        return provider

    def _build_clock(self) -> SystemClock:
        return SystemClock()

    def _build_log(self) -> RingBufferLogSink:
        return RingBufferLogSink()

    def _build_db(self) -> Database:
        return Database(self.config.get_str("DB_URL"))

    def _build_session_repo(self) -> SqlSessionRepository:
        return SqlSessionRepository(self.db)

    def _build_user_profile_repo(self) -> SqlUserProfileRepository:
        return SqlUserProfileRepository(self.db)

    def _build_social_graph_repo(self) -> SqlSocialGraphRepository:
        return SqlSocialGraphRepository(self.db)

    def _build_config_repo(self) -> SqlConfigRepository:
        # Ensure audit_repo is built first so it can be attached for set() auditing
        _ = self.audit_repo
        repo = SqlConfigRepository(self.db)
        # Attach DB repo + audit repo to the config provider now that both exist
        self._attach_config_db(repo)
        return repo

    def _build_audit_repo(self) -> SqlAuditRepository:
        return SqlAuditRepository(self.db)

    def _build_plugin_config_repo(self) -> SqlPluginConfigRepository:
        return SqlPluginConfigRepository(self.db)

    def _build_admin_user_repo(self) -> SqlAdminUserRepository:
        return SqlAdminUserRepository(self.db)

    def _attach_config_db(self, repo: SqlConfigRepository) -> None:
        """Wire the ConfigRepository and AuditRepository into the already-built EnvConfigProvider."""
        cfg = self._cache.get("config")
        if isinstance(cfg, EnvConfigProvider):
            audit = self._cache.get("audit_repo")
            cfg.attach_db(
                db_repo=repo,
                audit_repo=audit if isinstance(audit, SqlAuditRepository) else None,
            )

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

    def _build_memory(self) -> MemoryService:
        return MemoryService(
            sessions=self.session_repo,
            llm=self.llm,
            prompt=self.prompt,
            config=self.config,
            clock=self.clock,
            log=self.log,
            user_memory=self.user_memory,
        )

    def _build_user_memory(self) -> UserMemoryService:
        return UserMemoryService(
            profiles=self.user_profile_repo,
            graph=self.social_graph_repo,
            sessions=self.session_repo,
            llm=self.llm,
            config=self.config,
            clock=self.clock,
            log=self.log,
            plugin_host=self.plugin_host,
        )

    def _build_memory_access(self) -> _DbMemoryAccess:
        return _DbMemoryAccess(self.session_repo)

    def _build_user_memory_access(self) -> _DbUserMemoryAccess:
        return _DbUserMemoryAccess(self.user_profile_repo, self.social_graph_repo)

    def _build_observation_access(self) -> _DbObservationAccess:
        return _DbObservationAccess(self.observation_store)

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
            plugin_host=self.plugin_host,
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
            plugin_host=self.plugin_host,
        )

    def _build_stats_service(self) -> StatsService:
        return StatsService(
            sessions=self.session_repo,
            users=self.user_profile_repo,
            graph=self.social_graph_repo,
        )

    def _build_plugin_host(self) -> DefaultPluginHost:
        # Built without services initially; services are set via
        # set_services() before discover_and_register() is called,
        # breaking the host→services→user_memory→host cycle.
        return DefaultPluginHost()

    def _build_plugin_services(self) -> PluginServices:
        return PluginServices(
            sessions=self.session_repo,
            user_profiles=self.user_profile_repo,
            social_graph=self.social_graph_repo,
            user_memory=self.user_memory,
            config=self.config,
            log=self.log,
        )

    def _build_plugin_loader(self) -> PluginLoader:
        return PluginLoader(
            host=self.plugin_host,
            plugin_configs=self.plugin_config_repo,
            services=self.plugin_services,
        )

    def wire_config_change_bridge(self) -> None:
        """Subscribe ConfigProvider changes → PluginHost.dispatch(on_config_change).

        Must be called after ``plugin_host`` and ``config`` are both built.
        The subscription is synchronous (ConfigProvider callback contract);
        the async dispatch is scheduled onto the event loop.
        """
        import asyncio

        host = self.plugin_host

        def _on_config_change(key: str, value: object) -> None:
            ctx = PluginContext(
                hook=HookType.on_config_change,
                extra={"key": key, "value": value},
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(host.dispatch(ctx))
            except RuntimeError:
                pass

        self.config.subscribe(_on_config_change)

    async def init_plugins(self) -> None:
        """Wire plugin services and discover/register all plugins.

        Must be called once during startup, after the event loop is running.
        This sets the services on the host (breaking the construction cycle),
        wires the config-change bridge, then discovers and registers plugins.
        """
        # Set services on host so plugin.setup() receives the real services
        self.plugin_host.set_services(self.plugin_services)
        # Bridge config changes to on_config_change hook
        self.wire_config_change_bridge()
        # Discover and register built-in + entry_point plugins
        await self.plugin_loader.discover_and_register()

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
    def db(self) -> Database:
        return self._get_or_build("db")  # type: ignore[return-value]

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
    def user_profile_repo(self) -> UserProfileRepository:
        return self._get_or_build("user_profile_repo")  # type: ignore[return-value]

    @property
    def social_graph_repo(self) -> SocialGraphRepository:
        return self._get_or_build("social_graph_repo")  # type: ignore[return-value]

    @property
    def config_repo(self) -> ConfigRepository:
        return self._get_or_build("config_repo")  # type: ignore[return-value]

    @property
    def audit_repo(self) -> AuditRepository:
        return self._get_or_build("audit_repo")  # type: ignore[return-value]

    @property
    def plugin_config_repo(self) -> PluginConfigRepository:
        return self._get_or_build("plugin_config_repo")  # type: ignore[return-value]

    @property
    def admin_user_repo(self) -> AdminUserRepository:
        return self._get_or_build("admin_user_repo")  # type: ignore[return-value]

    @property
    def memory(self) -> MemoryServiceProtocol:
        return self._get_or_build("memory")  # type: ignore[return-value]

    @property
    def user_memory(self) -> UserMemoryServiceProtocol:
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

    @property
    def stats_service(self) -> StatsService:
        return self._get_or_build("stats_service")  # type: ignore[return-value]

    @property
    def plugin_host(self) -> DefaultPluginHost:
        return self._get_or_build("plugin_host")  # type: ignore[return-value]

    @property
    def plugin_services(self) -> PluginServices:
        return self._get_or_build("plugin_services")  # type: ignore[return-value]

    @property
    def plugin_loader(self) -> PluginLoader:
        return self._get_or_build("plugin_loader")  # type: ignore[return-value]


def build_container() -> Container:
    """Build the default Container with SQLite-backed repositories.

    This is the single entry point for constructing a production Container.
    Tests should construct Container directly and call ``override()`` with
    fakes before accessing any properties.
    """
    return Container()
