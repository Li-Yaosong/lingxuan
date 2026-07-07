"""Tests for EnvConfigProvider: defaults, env override, set+subscribe, masking, attach_db."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from lingxuan.adapters.config_provider import EnvConfigProvider
from lingxuan.config.defaults import SETTINGS, SETTINGS_BY_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Keys that exist in the real .env — we must clear them for isolated tests
_ENV_KEYS = [s.key for s in SETTINGS]


def _make_provider(**env_patches: str) -> EnvConfigProvider:
    """Create an EnvConfigProvider with a clean env + specific patches.

    We clear all SETTINGS keys from os.environ first, then apply only
    the explicitly provided env_patches, so tests are isolated from
    the developer's real .env file.
    """
    clean_env = {k: v for k, v in os.environ.items() if k not in SETTINGS_BY_KEY}
    clean_env.update(env_patches)
    with patch.dict(os.environ, clean_env, clear=True):
        return EnvConfigProvider(_skip_dotenv=True)


# ---------------------------------------------------------------------------
# 1. Defaults: no env → all keys return spec defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_all_keys_present(self) -> None:
        provider = _make_provider()
        for spec in SETTINGS:
            val = provider.get(spec.key)
            assert val == spec.default, f"{spec.key}: expected {spec.default!r}, got {val!r}"

    def test_get_str_default(self) -> None:
        provider = _make_provider()
        assert provider.get_str("BOT_NAME") == "灵轩"

    def test_get_int_default(self) -> None:
        provider = _make_provider()
        assert provider.get_int("MEMORY_WINDOW") == 20

    def test_get_float_default(self) -> None:
        provider = _make_provider()
        assert provider.get_float("GROUP_OBSERVE_DELAY") == 1.5

    def test_get_bool_default(self) -> None:
        provider = _make_provider()
        assert provider.get_bool("ENABLE_PRIVATE_CHAT") is True

    def test_get_int_list_default(self) -> None:
        provider = _make_provider()
        assert provider.get_int_list("BOT_ADMINS") == []

    def test_unknown_key_raises(self) -> None:
        provider = _make_provider()
        with pytest.raises(KeyError):
            provider.get("NONEXISTENT_KEY")


# ---------------------------------------------------------------------------
# 2. Env override: .env / os.environ values override defaults
# ---------------------------------------------------------------------------

class TestEnvOverride:
    def test_str_override(self) -> None:
        provider = _make_provider(BOT_NAME="测试")
        assert provider.get_str("BOT_NAME") == "测试"

    def test_int_override(self) -> None:
        provider = _make_provider(MEMORY_WINDOW="50")
        assert provider.get_int("MEMORY_WINDOW") == 50

    def test_float_override(self) -> None:
        provider = _make_provider(GROUP_OBSERVE_DELAY="3.0")
        assert provider.get_float("GROUP_OBSERVE_DELAY") == 3.0

    def test_bool_override_true(self) -> None:
        provider = _make_provider(ENABLE_PRIVATE_CHAT="true")
        assert provider.get_bool("ENABLE_PRIVATE_CHAT") is True

    def test_bool_override_false(self) -> None:
        provider = _make_provider(ENABLE_PRIVATE_CHAT="0")
        assert provider.get_bool("ENABLE_PRIVATE_CHAT") is False

    def test_int_list_override(self) -> None:
        provider = _make_provider(BOT_ADMINS="123,456,789")
        assert provider.get_int_list("BOT_ADMINS") == [123, 456, 789]

    def test_env_not_set_keeps_default(self) -> None:
        provider = _make_provider(BOT_NAME="测试")
        # OPENAI_MODEL not in env_patches → keeps default
        assert provider.get_str("OPENAI_MODEL") == "deepseek-chat"


# ---------------------------------------------------------------------------
# 3. set + subscribe: runtime override and callback notification
# ---------------------------------------------------------------------------

class TestSetAndSubscribe:
    @pytest.mark.asyncio
    async def test_set_updates_get(self) -> None:
        provider = _make_provider()
        await provider.set("BOT_NAME", "新名字")
        assert provider.get_str("BOT_NAME") == "新名字"

    @pytest.mark.asyncio
    async def test_set_triggers_callback(self) -> None:
        provider = _make_provider()
        received: list[tuple[str, object]] = []
        provider.subscribe(lambda k, v: received.append((k, v)))
        await provider.set("MEMORY_WINDOW", 99)
        assert received == [("MEMORY_WINDOW", 99)]

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe(self) -> None:
        provider = _make_provider()
        received: list[tuple[str, object]] = []
        unsub = provider.subscribe(lambda k, v: received.append((k, v)))
        await provider.set("BOT_NAME", "A")
        assert len(received) == 1
        unsub()
        await provider.set("BOT_NAME", "B")
        assert len(received) == 1  # no new callback after unsubscribe

    @pytest.mark.asyncio
    async def test_set_unknown_key_raises(self) -> None:
        provider = _make_provider()
        with pytest.raises(KeyError):
            await provider.set("NONEXISTENT_KEY", "value")

    @pytest.mark.asyncio
    async def test_set_overrides_env(self) -> None:
        provider = _make_provider(BOT_NAME="env值")
        assert provider.get_str("BOT_NAME") == "env值"
        await provider.set("BOT_NAME", "内存值")
        assert provider.get_str("BOT_NAME") == "内存值"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self) -> None:
        provider = _make_provider()
        received_a: list[tuple[str, object]] = []
        received_b: list[tuple[str, object]] = []
        provider.subscribe(lambda k, v: received_a.append((k, v)))
        provider.subscribe(lambda k, v: received_b.append((k, v)))
        await provider.set("BOT_NAME", "X")
        assert received_a == [("BOT_NAME", "X")]
        assert received_b == [("BOT_NAME", "X")]


# ---------------------------------------------------------------------------
# 4. Masking: get_all(mask_secrets=True) masks sensitive values
# ---------------------------------------------------------------------------

class TestMasking:
    @pytest.mark.asyncio
    async def test_api_key_masked(self) -> None:
        provider = _make_provider(OPENAI_API_KEY="sk-1234567890abcdef")
        all_vals = await provider.get_all(mask_secrets=True)
        masked = all_vals["OPENAI_API_KEY"]
        assert isinstance(masked, str)
        assert "sk-" not in masked or "****" in masked
        # Original value should NOT appear in full
        assert "sk-1234567890abcdef" not in str(masked)

    @pytest.mark.asyncio
    async def test_secret_key_masked(self) -> None:
        provider = _make_provider(SECRET_KEY="my-super-secret-key-123")
        all_vals = await provider.get_all(mask_secrets=True)
        assert "my-super-secret-key-123" not in str(all_vals["SECRET_KEY"])

    @pytest.mark.asyncio
    async def test_non_secret_not_masked(self) -> None:
        provider = _make_provider(BOT_NAME="灵轩")
        all_vals = await provider.get_all(mask_secrets=True)
        assert all_vals["BOT_NAME"] == "灵轩"

    @pytest.mark.asyncio
    async def test_mask_secrets_false_shows_all(self) -> None:
        provider = _make_provider(OPENAI_API_KEY="sk-1234567890abcdef")
        all_vals = await provider.get_all(mask_secrets=False)
        assert all_vals["OPENAI_API_KEY"] == "sk-1234567890abcdef"

    @pytest.mark.asyncio
    async def test_empty_secret_shows_placeholder(self) -> None:
        provider = _make_provider()
        all_vals = await provider.get_all(mask_secrets=True)
        assert all_vals["OPENAI_API_KEY"] == "(未配置)"

    @pytest.mark.asyncio
    async def test_short_secret_masked(self) -> None:
        provider = _make_provider(OPENAI_API_KEY="abc")
        all_vals = await provider.get_all(mask_secrets=True)
        assert all_vals["OPENAI_API_KEY"] == "****"


# ---------------------------------------------------------------------------
# 5. attach_db: wiring DB repo + audit repo after construction
# ---------------------------------------------------------------------------

class TestAttachDb:
    @pytest.mark.asyncio
    async def test_attach_db_wires_repos(self) -> None:
        """attach_db() should wire db_repo and audit_repo for set() persistence."""
        from lingxuan.adapters.storage.db import Database
        from lingxuan.adapters.storage.repositories import SqlAuditRepository, SqlConfigRepository

        db = Database("sqlite+aiosqlite://")
        await db.create_all()
        try:
            config_repo = SqlConfigRepository(db)
            audit_repo = SqlAuditRepository(db)

            provider = _make_provider()
            assert provider._db_repo is None
            assert provider._audit_repo is None

            provider.attach_db(config_repo, audit_repo)

            assert provider._db_repo is config_repo
            assert provider._audit_repo is audit_repo
            assert provider._db_loaded is False

            # set() should now persist to DB and record audit
            await provider.set("BOT_NAME", "DB测试", actor="test_actor")

            # Verify persisted to DB
            all_db = await config_repo.get_all()
            assert all_db["BOT_NAME"] == "DB测试"

            # Verify audit recorded
            entries = await audit_repo.query(action="config_set", limit=5)
            assert any(e.target == "BOT_NAME" and e.actor == "test_actor" for e in entries)
        finally:
            await db.dispose()

    @pytest.mark.asyncio
    async def test_attach_db_without_audit(self) -> None:
        """attach_db() with no audit_repo should still wire db_repo."""
        from lingxuan.adapters.storage.db import Database
        from lingxuan.adapters.storage.repositories import SqlConfigRepository

        db = Database("sqlite+aiosqlite://")
        await db.create_all()
        try:
            config_repo = SqlConfigRepository(db)
            provider = _make_provider()
            provider.attach_db(config_repo)  # no audit_repo

            assert provider._db_repo is config_repo
            assert provider._db_loaded is False

            # set() should persist to DB without audit
            await provider.set("BOT_NAME", "无审计")
            all_db = await config_repo.get_all()
            assert all_db["BOT_NAME"] == "无审计"
        finally:
            await db.dispose()
