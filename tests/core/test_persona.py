"""Tests for PersonaService — four combinations of BOT_PERSONA × is_group."""

from __future__ import annotations

import pytest

from lingxuan.core.persona import (
    DEFAULT_PERSONA,
    GROUP_PERSONA_SUFFIX,
    PersonaService,
)
from tests.fakes.config import FakeConfigProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(**overrides: object) -> PersonaService:
    return PersonaService(FakeConfigProvider(overrides))


# ---------------------------------------------------------------------------
# 1. Default persona, private chat
# ---------------------------------------------------------------------------

class TestDefaultPersonaPrivate:
    def test_contains_bot_name(self) -> None:
        svc = _make_service(BOT_NAME="测试轩")
        prompt = svc.get_system_prompt(is_group=False)
        assert "测试轩" in prompt
        assert "你是测试轩" in prompt

    def test_no_group_suffix(self) -> None:
        svc = _make_service(BOT_NAME="灵轩")
        prompt = svc.get_system_prompt(is_group=False)
        assert "群聊观察模式" not in prompt

    def test_matches_default_template(self) -> None:
        svc = _make_service(BOT_NAME="灵轩")
        prompt = svc.get_system_prompt(is_group=False)
        expected = DEFAULT_PERSONA.format(BOT_NAME="灵轩")
        assert prompt == expected


# ---------------------------------------------------------------------------
# 2. Default persona, group chat
# ---------------------------------------------------------------------------

class TestDefaultPersonaGroup:
    def test_contains_group_suffix(self) -> None:
        svc = _make_service(BOT_NAME="灵轩")
        prompt = svc.get_system_prompt(is_group=True)
        assert "群聊观察模式" in prompt
        assert prompt.endswith(GROUP_PERSONA_SUFFIX)

    def test_private_shorter_than_group(self) -> None:
        svc = _make_service(BOT_NAME="灵轩")
        private = svc.get_system_prompt(is_group=False)
        group = svc.get_system_prompt(is_group=True)
        assert len(group) > len(private)


# ---------------------------------------------------------------------------
# 3. Custom persona, private chat
# ---------------------------------------------------------------------------

class TestCustomPersonaPrivate:
    def test_custom_replaces_default(self) -> None:
        custom = "我是自定义的{BOT_NAME}人设"
        svc = _make_service(BOT_NAME="灵轩", BOT_PERSONA=custom)
        prompt = svc.get_system_prompt(is_group=False)
        assert prompt == custom
        # Should NOT contain default persona text
        assert "温柔但偶尔调皮" not in prompt

    def test_empty_string_falls_back(self) -> None:
        svc = _make_service(BOT_NAME="灵轩", BOT_PERSONA="")
        prompt = svc.get_system_prompt(is_group=False)
        assert "温柔但偶尔调皮" in prompt


# ---------------------------------------------------------------------------
# 4. Custom persona, group chat
# ---------------------------------------------------------------------------

class TestCustomPersonaGroup:
    def test_custom_plus_suffix(self) -> None:
        custom = "我是自定义人设"
        svc = _make_service(BOT_NAME="灵轩", BOT_PERSONA=custom)
        prompt = svc.get_system_prompt(is_group=True)
        assert prompt.startswith(custom)
        assert prompt.endswith(GROUP_PERSONA_SUFFIX)

    def test_custom_no_default_in_group(self) -> None:
        custom = "简洁人设"
        svc = _make_service(BOT_NAME="灵轩", BOT_PERSONA=custom)
        prompt = svc.get_system_prompt(is_group=True)
        assert "温柔但偶尔调皮" not in prompt


# ---------------------------------------------------------------------------
# Runtime config change reflects new name
# ---------------------------------------------------------------------------

class TestRuntimeNameChange:
    @pytest.mark.asyncio
    async def test_name_change_reflected(self) -> None:
        config = FakeConfigProvider({"BOT_NAME": "灵轩", "BOT_PERSONA": ""})
        svc = PersonaService(config)

        prompt1 = svc.get_system_prompt(is_group=False)
        assert "灵轩" in prompt1

        await config.set("BOT_NAME", "新名字")
        prompt2 = svc.get_system_prompt(is_group=False)
        assert "新名字" in prompt2
        assert "灵轩" not in prompt2
