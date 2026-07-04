"""P2-10 acceptance test: build Container with temp DB, run a private message end-to-end.

Uses a temporary SQLite database (in-memory), runs ``db.create_all()`` to set up
schema, builds the Container with fake adapters (LLM/transport), sends a private
message, and asserts that ``session_messages`` has a persisted row.
"""

from __future__ import annotations

import pytest

from lingxuan.adapters.config_provider import EnvConfigProvider
from lingxuan.adapters.storage.db import Database
from lingxuan.container import Container
from lingxuan.protocols.messaging import (
    Actor,
    InboundMessage,
    SessionId,
)

from tests.fakes.clock import FakeClock
from tests.fakes.llm import FakeLLMProvider
from tests.fakes.logsink import FakeLogSink
from tests.fakes.transport import FakeTransport


async def _startup(c: Container) -> None:
    """Mimic bootstrap._startup: create tables + wire config_repo."""
    await c.db.create_all()
    # Trigger config_repo build which attaches DB repo to config provider
    _ = c.config_repo


@pytest.fixture
async def sqlite_container() -> Container:
    """Build a Container with an in-memory SQLite DB and fake adapters."""
    c = Container()
    c.override("config", EnvConfigProvider(_skip_dotenv=True))
    c.override("clock", FakeClock)
    c.override("log", FakeLogSink)
    c.override("llm", FakeLLMProvider)
    c.override("transport", FakeTransport)
    c.override("db", Database("sqlite+aiosqlite://"))

    await _startup(c)

    yield c

    await c.db.dispose()


@pytest.mark.asyncio
async def test_private_message_persists_to_session_messages(
    sqlite_container: Container,
) -> None:
    """A private chat message should be persisted in the session_messages table."""
    c = sqlite_container

    session_id = SessionId(kind="private", peer_id=12345)
    inbound = InboundMessage(
        session_id=session_id,
        actor=Actor(user_id=12345, nickname="测试用户", is_admin=False, is_self=False),
        text="你好",
        raw_text="你好",
        at_bot=False,
        reply_to_bot=False,
        at_user_ids=[],
        group_id=None,
    )

    await c.dialogue.handle_inbound(inbound)

    # Verify the message was persisted to the DB
    history = await c.session_repo.load_history(session_id)
    assert len(history) >= 1

    # The user message should be in history
    user_msgs = [m for m in history if m.role == "user"]
    assert len(user_msgs) >= 1
    assert "你好" in user_msgs[0].content

    # The fake LLM returns a response, so assistant message should also be persisted
    assistant_msgs = [m for m in history if m.role == "assistant"]
    assert len(assistant_msgs) >= 1


@pytest.mark.asyncio
async def test_config_set_persists_to_db(sqlite_container: Container) -> None:
    """Config set() should persist to the DB and survive a reload."""
    c = sqlite_container

    # Set a config value
    await c.config.set("BOT_NAME", "测试灵轩", actor="test")

    # Verify it's in the DB via config_repo directly
    all_config = await c.config_repo.get_all()
    assert "BOT_NAME" in all_config
    assert all_config["BOT_NAME"] == "测试灵轩"

    # Verify reading back from config provider
    assert c.config.get_str("BOT_NAME") == "测试灵轩"


@pytest.mark.asyncio
async def test_audit_log_recorded_on_config_set(sqlite_container: Container) -> None:
    """Config set() should record an audit log entry."""
    c = sqlite_container

    await c.config.set("BOT_NAME", "新名字", actor="admin_user")

    # Check audit log
    entries = await c.audit_repo.query(action="config_set", limit=10)
    assert len(entries) >= 1
    latest = entries[0]
    assert latest.actor == "admin_user"
    assert latest.action == "config_set"
    assert latest.target == "BOT_NAME"


@pytest.mark.asyncio
async def test_session_repo_has_persisted_rows(sqlite_container: Container) -> None:
    """Verify session_messages table has rows after a message exchange."""
    c = sqlite_container

    session_id = SessionId(kind="private", peer_id=99999)
    inbound = InboundMessage(
        session_id=session_id,
        actor=Actor(user_id=99999, nickname="用户A", is_admin=False, is_self=False),
        text="测试消息",
        raw_text="测试消息",
        at_bot=False,
        reply_to_bot=False,
        at_user_ids=[],
        group_id=None,
    )

    await c.dialogue.handle_inbound(inbound)

    # Use raw SQL to verify rows exist in session_messages
    from sqlalchemy import text

    async with c.db.session() as s:
        result = await s.execute(
            text("SELECT COUNT(*) FROM session_messages WHERE session_id = :sid"),
            {"sid": session_id.as_str()},
        )
        count = result.scalar_one()
        assert count >= 1, "session_messages should have at least one row for the session"
