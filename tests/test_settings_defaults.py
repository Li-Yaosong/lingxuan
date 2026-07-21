"""Tests for settings_defaults: spec coverage and parse_value correctness."""

from __future__ import annotations

import pytest

from lingxuan.config.defaults import SETTINGS, SETTINGS_BY_KEY, SettingSpec, parse_value

# ── 33 existing MVP keys with their expected defaults ───────────────────
MVP_DEFAULTS: dict[str, object] = {
    "DRIVER": "~fastapi",
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
    "OPENAI_MODEL": "deepseek-chat",
    "BOT_NAME": "灵轩",
    "BOT_PERSONA": "",
    "BOT_ADMINS": [],
    "MEMORY_WINDOW": 20,
    "GROUP_OBSERVE_WINDOW": 20,
    "GROUP_OBSERVE_DELAY": 1.5,
    "GROUP_OBSERVE_COOLDOWN": 30.0,
    "GROUP_BURST_MERGE_WINDOW": 10.0,
    "GROUP_FOLLOWUP_WINDOW": 60.0,
    "GROUP_CHAT_CONTEXT": 6,
    "GROUP_CHAT_MAX_TOKENS": 512,
    "ENABLE_STREAM_CHUNK": True,
    "GROUP_MSG_CHUNK_MAX": 35,
    "GROUP_MSG_CHUNK_MIN": 6,
    "GROUP_MSG_CHUNK_LIMIT": 6,
    "GROUP_CHUNK_DELAY_MIN": 0.4,
    "GROUP_CHUNK_DELAY_MAX": 1.2,
    "ENABLE_PRIVATE_CHAT": True,
    "ENABLE_GROUP_CHAT": True,
    "ENABLE_GROUP_OBSERVE": True,
    "ENABLE_MEMORY_SUMMARY": True,
    "ENABLE_USER_MEMORY": True,
    "USER_MEMORY_BURST_MERGE": 3.0,
    "USER_MEMORY_MAX_FACTS": 30,
    "ENABLE_USER_COGNITION_REFINE": True,
    "USER_COGNITION_REFINE_INTERVAL": 5,
    "USER_COGNITION_REFINE_DELAY": 2.0,
    "USER_COGNITION_MAX_CHARS": 150,
}


@pytest.mark.parametrize("key,expected", list(MVP_DEFAULTS.items()))
def test_mvp_key_exists_with_correct_default(key: str, expected: object) -> None:
    spec = SETTINGS_BY_KEY.get(key)
    assert spec is not None, f"Missing MVP key: {key}"
    assert spec.default == expected, f"{key}: expected {expected!r}, got {spec.default!r}"


def test_total_settings_count() -> None:
    # 32 existing MVP env vars + 8 v2 new items = 40
    assert len(SETTINGS) >= 40


def test_settings_by_key_covers_all() -> None:
    assert len(SETTINGS_BY_KEY) == len(SETTINGS)
    for spec in SETTINGS:
        assert SETTINGS_BY_KEY[spec.key] is spec


def test_no_duplicate_keys() -> None:
    keys = [s.key for s in SETTINGS]
    assert len(keys) == len(set(keys))


# ── New keys (v2) ───────────────────────────────────────────────────────

NEW_DEFAULTS: dict[str, object] = {
    "DB_URL": "sqlite+aiosqlite:///./data/lingxuan.db",
    "DATA_ROOT": "./data",
    "AUTO_MIGRATE": True,
    "ADMIN_HOST": "127.0.0.1",
    "ADMIN_PORT": 8081,
    "SECRET_KEY": "",
    "JWT_ACCESS_TTL": 900,
    "JWT_REFRESH_TTL": 604800,
}


@pytest.mark.parametrize("key,expected", list(NEW_DEFAULTS.items()))
def test_new_key_exists_with_correct_default(key: str, expected: object) -> None:
    spec = SETTINGS_BY_KEY.get(key)
    assert spec is not None, f"Missing new key: {key}"
    assert spec.default == expected, f"{key}: expected {expected!r}, got {spec.default!r}"


# ── Sensitivity flags ───────────────────────────────────────────────────

def test_secret_keys() -> None:
    secret_keys = {s.key for s in SETTINGS if s.is_secret}
    assert secret_keys == {"OPENAI_API_KEY", "SECRET_KEY"}


def test_non_hot_reloadable_keys() -> None:
    non_reloadable = {s.key for s in SETTINGS if not s.hot_reloadable}
    expected = {
        "DRIVER", "DB_URL", "DATA_ROOT", "ADMIN_HOST", "ADMIN_PORT", "SECRET_KEY",
        "AUTO_MIGRATE", "NAPCAT_DIR", "NAPCAT_QQ_DIR", "NAPCAT_AUTO_START",
        "NAPCAT_NO_SANDBOX", "NAPCAT_USE_XVFB",
    }
    assert non_reloadable == expected


# ── parse_value ─────────────────────────────────────────────────────────

def _spec(key: str, type_: str, **kw: object) -> SettingSpec:
    return SettingSpec(key=key, type=type_, default=None, group="test", **kw)


class TestParseValue:
    def test_str(self) -> None:
        assert parse_value(_spec("X", "str"), "hello") == "hello"

    def test_int(self) -> None:
        assert parse_value(_spec("X", "int"), "42") == 42
        assert parse_value(_spec("X", "int"), "  7  ") == 7

    def test_float(self) -> None:
        assert parse_value(_spec("X", "float"), "3.14") == 3.14
        assert parse_value(_spec("X", "float"), "  0.5  ") == 0.5

    @pytest.mark.parametrize("raw", ["1", "true", "True", "TRUE", "yes", "Yes", "on", "ON"])
    def test_bool_true(self, raw: str) -> None:
        assert parse_value(_spec("X", "bool"), raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "no", "No", "off", "OFF", ""])
    def test_bool_false(self, raw: str) -> None:
        assert parse_value(_spec("X", "bool"), raw) is False

    def test_int_list_normal(self) -> None:
        assert parse_value(_spec("X", "int_list"), "1,2,3") == [1, 2, 3]

    def test_int_list_with_spaces_and_non_digits(self) -> None:
        assert parse_value(_spec("X", "int_list"), "1,2, x,3") == [1, 2, 3]

    def test_int_list_empty(self) -> None:
        assert parse_value(_spec("X", "int_list"), "") == []

    def test_int_list_single(self) -> None:
        assert parse_value(_spec("X", "int_list"), "42") == [42]

    def test_unknown_type_raises(self) -> None:
        bad_spec = SettingSpec(key="X", type="bad", default=None, group="test")
        with pytest.raises(ValueError, match="Unknown spec type"):
            parse_value(bad_spec, "x")
