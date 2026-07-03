"""Tests for core/prompting.py — message assembly order, prompt text, and local skip."""

from __future__ import annotations

import pytest

from lingxuan.core.persona import PersonaService
from lingxuan.core.prompting import (
    PromptBuilder,
    build_group_reply_user,
    build_judge_prompt,
    build_summary_prompt,
    should_skip_reply_locally,
)
from lingxuan.protocols.llm import ChatMessage
from lingxuan.protocols.repositories import StoredMessage
from tests.fakes.config import FakeConfigProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_builder(**overrides: object) -> PromptBuilder:
    config = FakeConfigProvider(overrides)
    persona = PersonaService(config)
    return PromptBuilder(persona, config)


def _msg(role: str, content: str) -> StoredMessage:
    return StoredMessage(role=role, content=content)


def _roles(messages: list[ChatMessage]) -> list[str]:
    return [m.role for m in messages]


# ---------------------------------------------------------------------------
# build_context_messages — assembly order
# ---------------------------------------------------------------------------

class TestBuildContextMessagesBasicOrder:
    """Minimal case: system prompt + history."""

    def test_private_chat_minimal(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        history = [_msg("user", "你好"), _msg("assistant", "你好呀")]
        messages = builder.build_context_messages(
            is_group=False, history=history
        )
        # system + user + assistant = 3
        assert len(messages) == 3
        assert messages[0].role == "system"
        assert "灵轩" in messages[0].content
        assert messages[1] == ChatMessage(role="user", content="你好")
        assert messages[2] == ChatMessage(role="assistant", content="你好呀")

    def test_group_chat_minimal(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        history = [_msg("user", "[小明]: 嗨")]
        messages = builder.build_context_messages(
            is_group=True, history=history
        )
        assert messages[0].role == "system"
        assert "群聊观察模式" in messages[0].content
        assert messages[1].role == "user"


class TestBuildContextMessagesSummary:
    """Summary block: present when non-empty, absent otherwise."""

    def test_summary_present(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            summary="之前聊过天气",
        )
        # system, summary-system, user
        assert len(messages) == 3
        assert messages[1].role == "system"
        assert "此前对话摘要" in messages[1].content
        assert "之前聊过天气" in messages[1].content

    def test_summary_absent_when_empty(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            summary="",
        )
        # system, user — no summary block
        assert len(messages) == 2
        assert all("此前对话摘要" not in m.content for m in messages)


class TestBuildContextMessagesUserContext:
    """User context block: gated by ENABLE_USER_MEMORY."""

    def test_user_context_present_when_enabled(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=True)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            user_context_text="【正在对话的人】\n- 小明",
        )
        # system, user-context-system, user
        assert len(messages) == 3
        assert messages[1].role == "system"
        assert "正在对话的人" in messages[1].content

    def test_user_context_absent_when_disabled(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            user_context_text="【正在对话的人】\n- 小明",
        )
        # system, user — no user context block
        assert len(messages) == 2

    def test_user_context_absent_when_empty_text(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=True)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            user_context_text="",
        )
        # system, user — no user context block
        assert len(messages) == 2


class TestBuildContextMessagesEntities:
    """Group entities block: only for group, only when non-empty."""

    def test_entities_present_in_group(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=True,
            history=[_msg("user", "hi")],
            entities_text="【群成员昵称】\n- 小明: QQ 123",
        )
        # system, entities-system, user
        has_entities = any("群成员昵称" in m.content for m in messages if m.role == "system")
        assert has_entities

    def test_entities_absent_in_private(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            entities_text="【群成员昵称】\n- 小明: QQ 123",
        )
        has_entities = any("群成员昵称" in m.content for m in messages)
        assert not has_entities

    def test_entities_absent_when_empty(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=True,
            history=[_msg("user", "hi")],
            entities_text="",
        )
        # system + user = 2, no entities block
        assert len(messages) == 2


class TestBuildContextMessagesHistoryLimit:
    """history_limit truncation for group context."""

    def test_history_truncated_from_tail(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        history = [_msg("user", f"msg{i}") for i in range(10)]
        messages = builder.build_context_messages(
            is_group=True,
            history=history,
            history_limit=3,
        )
        # system + 3 history = 4
        history_msgs = [m for m in messages if m.role != "system"]
        assert len(history_msgs) == 3
        assert history_msgs[0].content == "msg7"
        assert history_msgs[2].content == "msg9"

    def test_history_limit_none_keeps_all(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        history = [_msg("user", f"msg{i}") for i in range(10)]
        messages = builder.build_context_messages(
            is_group=False,
            history=history,
            history_limit=None,
        )
        history_msgs = [m for m in messages if m.role != "system"]
        assert len(history_msgs) == 10

    def test_history_limit_zero_yields_all_history(self) -> None:
        # history[-0:] == history[0:] in Python, so limit=0 returns all
        # This matches MVP behavior — GROUP_CHAT_CONTEXT is never 0 in practice
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        history = [_msg("user", "hi")]
        messages = builder.build_context_messages(
            is_group=True,
            history=history,
            history_limit=0,
        )
        # system + 1 history (because list[-0:] == list)
        assert len(messages) == 2


class TestBuildContextMessagesExtraUser:
    """extra_user appended as final user message."""

    def test_extra_user_appended(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            extra_user="【当前群聊观察】\n一些观察",
        )
        assert messages[-1].role == "user"
        assert "当前群聊观察" in messages[-1].content

    def test_no_extra_user_when_none(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=False)
        messages = builder.build_context_messages(
            is_group=False,
            history=[_msg("user", "hi")],
            extra_user=None,
        )
        assert messages[-1].content == "hi"


class TestBuildContextMessagesFullOrder:
    """Full assembly with all blocks present — verify exact ordering."""

    def test_full_group_order(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=True)
        history = [_msg("user", "msg1"), _msg("assistant", "msg2")]
        messages = builder.build_context_messages(
            is_group=True,
            history=history,
            summary="之前聊过",
            user_context_text="【正在对话的人】\n- 小明",
            entities_text="【群成员昵称】\n- 小明: QQ 123",
            extra_user="【当前群聊观察】\n观察内容",
            history_limit=6,
        )
        roles = _roles(messages)
        # system, summary-system, user-ctx-system, entities-system, user, assistant, user
        assert roles == ["system", "system", "system", "system", "user", "assistant", "user"]
        # Verify content markers in order
        assert "灵轩" in messages[0].content
        assert "此前对话摘要" in messages[1].content
        assert "正在对话的人" in messages[2].content
        assert "群成员昵称" in messages[3].content
        assert messages[4].content == "msg1"
        assert messages[5].content == "msg2"
        assert "当前群聊观察" in messages[6].content

    def test_full_private_order(self) -> None:
        builder = _make_builder(BOT_NAME="灵轩", ENABLE_USER_MEMORY=True)
        history = [_msg("user", "hello")]
        messages = builder.build_context_messages(
            is_group=False,
            history=history,
            summary="之前聊过",
            user_context_text="【正在对话的人】\n- 小明",
        )
        roles = _roles(messages)
        # system, summary, user-ctx, history-user
        assert roles == ["system", "system", "system", "user"]
        # No entities or group suffix
        assert "群聊观察模式" not in messages[0].content
        assert not any("群成员昵称" in m.content for m in messages)


# ---------------------------------------------------------------------------
# build_judge_prompt
# ---------------------------------------------------------------------------

class TestBuildJudgePrompt:
    def test_basic_prompt(self) -> None:
        prompt = build_judge_prompt("小明: 你好")
        assert "近期群聊" in prompt
        assert "小明: 你好" in prompt
        assert "只回答 yes 或 no" in prompt

    def test_with_user_brief(self) -> None:
        prompt = build_judge_prompt("观察文本", user_brief="小明(关系:熟悉)")
        assert "最后发言者：小明(关系:熟悉)" in prompt

    def test_without_user_brief(self) -> None:
        prompt = build_judge_prompt("观察文本")
        assert "最后发言者" not in prompt

    def test_custom_bot_name(self) -> None:
        prompt = build_judge_prompt("观察文本", bot_name="测试轩")
        assert "你是测试轩" in prompt
        assert "找测试轩说话" in prompt

    def test_yes_no_rules(self) -> None:
        prompt = build_judge_prompt("obs")
        assert "回答 yes 的情况" in prompt
        assert "回答 no 的情况" in prompt


# ---------------------------------------------------------------------------
# build_summary_prompt
# ---------------------------------------------------------------------------

class TestBuildSummaryPrompt:
    def test_basic_prompt(self) -> None:
        history = [_msg("user", "你好"), _msg("assistant", "你好呀")]
        prompt = build_summary_prompt(history)
        assert "压缩成简短摘要" in prompt
        assert "user: 你好" in prompt
        assert "assistant: 你好呀" in prompt

    def test_with_identity_note(self) -> None:
        history = [_msg("user", "hi")]
        prompt = build_summary_prompt(history, identity_note="\n\n称呼：小明")
        assert "称呼：小明" in prompt

    def test_memory_window_truncation(self) -> None:
        history = [_msg("user", f"msg{i}") for i in range(30)]
        prompt = build_summary_prompt(history, memory_window=10)
        # Should only contain first 10 messages
        assert "user: msg9" in prompt
        assert "user: msg10" not in prompt


# ---------------------------------------------------------------------------
# build_group_reply_user
# ---------------------------------------------------------------------------

class TestBuildGroupReplyUser:
    def test_format(self) -> None:
        result = build_group_reply_user("小明: 你好")
        assert result.startswith("【当前群聊观察】")
        assert "小明: 你好" in result
        assert "请根据以上群聊内容，自然地回复正在和你说话的人" in result

    def test_matches_mvp_format(self) -> None:
        observation = "群里在聊天"
        result = build_group_reply_user(observation)
        expected = (
            f"【当前群聊观察】\n{observation}\n\n"
            "请根据以上群聊内容，自然地回复正在和你说话的人。"
        )
        assert result == expected


# ---------------------------------------------------------------------------
# should_skip_reply_locally
# ---------------------------------------------------------------------------

class TestShouldSkipReplyLocally:
    def test_empty_string(self) -> None:
        assert should_skip_reply_locally("") is True

    def test_whitespace_only(self) -> None:
        assert should_skip_reply_locally("   ") is True

    def test_short_teasing_skip(self) -> None:
        # ≤6 chars, no question mark/吗, contains teasing word
        assert should_skip_reply_locally("傻") is True
        assert should_skip_reply_locally("笨蛋") is True
        assert should_skip_reply_locally("哈哈") is True
        assert should_skip_reply_locally("呵呵") is True
        assert should_skip_reply_locally("哼") is True
        assert should_skip_reply_locally("哦") is True
        assert should_skip_reply_locally("嗯嗯") is True
        assert should_skip_reply_locally("666") is True
        assert should_skip_reply_locally("典") is True
        assert should_skip_reply_locally("菜") is True

    def test_short_with_question_not_skipped(self) -> None:
        # Has question mark → not skipped
        assert should_skip_reply_locally("傻?") is False
        assert should_skip_reply_locally("傻？") is False
        assert should_skip_reply_locally("你吗") is False

    def test_short_no_teasing_not_skipped(self) -> None:
        # ≤6 chars but no teasing keyword
        assert should_skip_reply_locally("你好") is False
        assert should_skip_reply_locally("在吗") is False

    def test_long_message_not_skipped(self) -> None:
        # >6 chars → not skipped regardless
        assert should_skip_reply_locally("你好呀，我是小明") is False
        # 6 chars with teasing → still skipped (≤6 check passes)
        assert should_skip_reply_locally("哈哈哈哈哈哈") is True
        # 7 chars → length check fails → not skipped
        assert should_skip_reply_locally("哈哈哈哈哈哈哈") is False

    def test_six_chars_with_teasing(self) -> None:
        # Exactly 6 chars with teasing word → skip
        assert should_skip_reply_locally("哈哈哈哈") is True  # 4 chars
        assert should_skip_reply_locally("哈哈哈哈哈哈") is True  # 6 chars with 哈

    def test_seven_chars_not_skipped(self) -> None:
        # 7 chars → length check fails → not skipped
        assert should_skip_reply_locally("哈哈哈哈哈哈哈") is False

    def test_normal_message_not_skipped(self) -> None:
        assert should_skip_reply_locally("灵轩你好呀") is False
        assert should_skip_reply_locally("今天天气怎么样？") is False
