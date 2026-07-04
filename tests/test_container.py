"""Container construction test: build with fakes, verify key attribute types.

Phase 2 (P2-10): Container now uses SQLite repos by default. Tests that need
fakes should override ``config``, ``db``, and the repos they care about.
The helper ``_build_test_container`` creates a Container with an in-memory
SQLite database and fake adapters for config/clock/log/llm/transport.
"""

from __future__ import annotations

import pytest

from lingxuan.adapters.storage.db import Database
from lingxuan.container import Container, build_container
from lingxuan.core.admin_commands import AdminCommandService
from lingxuan.core.dialogue import DialogueService
from lingxuan.core.memory import MemoryService
from lingxuan.core.observation import ObservationService
from lingxuan.core.observation_state import ObservationStore
from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import PromptBuilder
from lingxuan.core.reply_planner import ReplyPlanner
from lingxuan.core.user_memory import UserMemoryService
from lingxuan.protocols.clock import Clock
from lingxuan.protocols.config import ConfigProvider
from lingxuan.protocols.llm import LLMProvider
from lingxuan.protocols.logging import LogSink
from lingxuan.protocols.messaging import MessageTransport
from lingxuan.protocols.repositories import SessionRepository

from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import InMemorySessionRepository
from tests.fakes.transport import FakeTransport


def _build_test_container() -> Container:
    """Build a Container with fakes instead of real adapters.

    Uses an in-memory SQLite DB so the SQL repos can work without
    touching the filesystem.
    """
    c = Container()
    c.override("config", FakeConfigProvider)
    c.override("clock", FakeClock)
    c.override("log", FakeLogSink)
    c.override("llm", FakeLLMProvider)
    c.override("transport", FakeTransport)
    # Use in-memory SQLite for the DB
    c.override("db", Database("sqlite+aiosqlite://"))
    return c


class TestContainerConstruction:
    """Container can be built without exceptions using fake deps."""

    def test_build_does_not_throw(self) -> None:
        c = _build_test_container()
        assert c is not None

    def test_config_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.config, FakeConfigProvider)

    def test_clock_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.clock, FakeClock)

    def test_log_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.log, FakeLogSink)

    def test_llm_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.llm, FakeLLMProvider)

    def test_transport_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.transport, FakeTransport)

    def test_persona_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.persona, PersonaService)

    def test_prompt_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.prompt, PromptBuilder)

    def test_planner_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.planner, ReplyPlanner)

    def test_observation_store_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.observation_store, ObservationStore)

    def test_session_repo_type(self) -> None:
        c = _build_test_container()
        # Now backed by SqlSessionRepository
        from lingxuan.adapters.storage.repositories import SqlSessionRepository

        assert isinstance(c.session_repo, SqlSessionRepository)

    def test_memory_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.memory, MemoryService)

    def test_user_memory_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.user_memory, UserMemoryService)

    def test_observation_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.observation, ObservationService)

    def test_admin_commands_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.admin_commands, AdminCommandService)

    def test_dialogue_type(self) -> None:
        c = _build_test_container()
        assert isinstance(c.dialogue, DialogueService)


class TestContainerSingletons:
    """Repeated property access returns the same instance (lazy singleton)."""

    def test_config_singleton(self) -> None:
        c = _build_test_container()
        assert c.config is c.config

    def test_llm_singleton(self) -> None:
        c = _build_test_container()
        assert c.llm is c.llm

    def test_dialogue_singleton(self) -> None:
        c = _build_test_container()
        assert c.dialogue is c.dialogue

    def test_observation_singleton(self) -> None:
        c = _build_test_container()
        assert c.observation is c.observation


class TestContainerOverride:
    """override() replaces factories before first access."""

    def test_override_before_access(self) -> None:
        c = Container()
        custom_config = FakeConfigProvider({"BOT_NAME": "测试轩"})
        c.override("config", custom_config)
        assert c.config is custom_config
        assert c.config.get_str("BOT_NAME") == "测试轩"

    def test_override_after_access_raises(self) -> None:
        c = _build_test_container()
        _ = c.config  # trigger instantiation
        with pytest.raises(RuntimeError, match="Cannot override"):
            c.override("config", FakeConfigProvider)

    def test_override_session_repo(self) -> None:
        c = Container()
        c.override("config", FakeConfigProvider)
        c.override("clock", FakeClock)
        c.override("log", FakeLogSink)
        c.override("db", Database("sqlite+aiosqlite://"))
        custom_repo = InMemorySessionRepository()
        c.override("session_repo", custom_repo)
        assert c.session_repo is custom_repo


class TestBuildContainer:
    """build_container() produces a Container with production defaults."""

    def test_build_container_type(self) -> None:
        c = build_container()
        assert isinstance(c, Container)

    def test_build_container_config_builder(self) -> None:
        c = build_container()
        from lingxuan.adapters.config_provider import EnvConfigProvider

        assert hasattr(c, "_build_config")
