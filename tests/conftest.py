"""Shared pytest fixtures providing fake implementations."""

from __future__ import annotations

import pytest

from tests.fakes.clock import FakeClock
from tests.fakes.config import FakeConfigProvider
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.repositories import (
    InMemoryAdminUserRepository,
    InMemoryAuditRepository,
    InMemoryConfigRepository,
    InMemoryPluginConfigRepository,
    InMemorySessionRepository,
    InMemorySocialGraphRepository,
    InMemoryUserProfileRepository,
)
from tests.fakes.transport import FakeTransport


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def fake_config() -> FakeConfigProvider:
    return FakeConfigProvider()


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport(self_id=9999)


@pytest.fixture
def fake_logsink() -> FakeLogSink:
    return FakeLogSink()


@pytest.fixture
def session_repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def user_profile_repo() -> InMemoryUserProfileRepository:
    return InMemoryUserProfileRepository()


@pytest.fixture
def social_graph_repo() -> InMemorySocialGraphRepository:
    return InMemorySocialGraphRepository()


@pytest.fixture
def config_repo() -> InMemoryConfigRepository:
    return InMemoryConfigRepository()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def plugin_config_repo() -> InMemoryPluginConfigRepository:
    return InMemoryPluginConfigRepository()


@pytest.fixture
def admin_user_repo() -> InMemoryAdminUserRepository:
    return InMemoryAdminUserRepository()
