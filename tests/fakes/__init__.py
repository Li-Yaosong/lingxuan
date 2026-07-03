"""Reusable test fakes for Core unit tests (no real IO)."""

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

__all__ = [
    "FakeClock",
    "FakeConfigProvider",
    "FakeLLMProvider",
    "FakeLogSink",
    "FakeTransport",
    "InMemoryAdminUserRepository",
    "InMemoryAuditRepository",
    "InMemoryConfigRepository",
    "InMemoryPluginConfigRepository",
    "InMemorySessionRepository",
    "InMemorySocialGraphRepository",
    "InMemoryUserProfileRepository",
]
