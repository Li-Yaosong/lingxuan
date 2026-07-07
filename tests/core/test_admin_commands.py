"""Tests for core/admin_commands.py — AdminCommandService & parse_command."""

from __future__ import annotations

import pytest

from lingxuan.core.admin_commands import (
    AdminCommandService,
    CommandContext,
    MemoryAccess,
    ObservationAccess,
    UserMemoryAccess,
    parse_command,
)
from lingxuan.protocols.messaging import ObservationEntry, SessionId
from lingxuan.protocols.config import mask_secret
from tests.fakes.config import FakeConfigProvider


# ---------------------------------------------------------------------------
# Fake service implementations
# ---------------------------------------------------------------------------


class FakeMemoryAccess:
    """Implements MemoryAccess protocol with controllable responses."""

    def __init__(
        self,
        *,
        message_count: int = 0,
        summary: str = "",
        meta: dict | None = None,
    ) -> None:
        self.message_count = message_count
        self.summary = summary
        self.meta = meta or {}
        self.cleared_sessions: list[SessionId] = []

    async def count_messages(self, session_id: SessionId) -> int:
        return self.message_count

    async def clear(self, session_id: SessionId) -> None:
        self.cleared_sessions.append(session_id)

    async def get_summary(self, session_id: SessionId) -> str:
        return self.summary

    async def get_meta(self, session_id: SessionId) -> dict:
        return self.meta


class FakeUserMemoryAccess:
    """Implements UserMemoryAccess protocol with controllable responses."""

    def __init__(
        self,
        *,
        user_ids: list[int] | None = None,
        profile_summaries: dict[int, str] | None = None,
        clear_profile_result: bool = True,
        clear_all_count: int = 3,
    ) -> None:
        self.user_ids = user_ids or []
        self.profile_summaries = profile_summaries or {}
        self.clear_profile_result = clear_profile_result
        self.clear_all_count = clear_all_count
        self.cleared_profiles: list[int] = []
        self.cleared_all: bool = False
        self.cleared_graph: bool = False

    def list_user_ids(self) -> list[int]:
        return self.user_ids

    async def load_profile_summary(self, user_id: int) -> str:
        return self.profile_summaries.get(user_id, "")

    async def clear_profile(self, user_id: int) -> bool:
        self.cleared_profiles.append(user_id)
        return self.clear_profile_result

    async def clear_all_profiles(self) -> int:
        self.cleared_all = True
        return self.clear_all_count

    async def clear_social_graph(self) -> None:
        self.cleared_graph = True


class FakeObservationAccess:
    """Implements ObservationAccess protocol with controllable responses."""

    def __init__(
        self,
        *,
        formatted: str = "",
        recent: list[ObservationEntry] | None = None,
        state: dict | None = None,
    ) -> None:
        self.formatted = formatted
        self.recent = recent or []
        self.state_data = state or {
            "buffer_len": 5,
            "last_judge_result": "",
            "in_cooldown": False,
            "cooldown_remaining": 0.0,
        }

    def format_observation(self, group_id: int) -> str:
        return self.formatted

    def recent_entries(self, group_id: int, limit: int = 5) -> list:
        return self.recent[:limit]

    def observe_state(self, group_id: int) -> dict:
        return self.state_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    user_id: int = 12345,
    is_group: bool = False,
    group_id: int | None = None,
    nickname: str = "测试用户",
) -> CommandContext:
    return CommandContext(
        user_id=user_id,
        session_id=SessionId(kind="private", peer_id=user_id),
        is_group=is_group,
        group_id=group_id,
        nickname=nickname,
    )


def _make_service(
    *,
    config_overrides: dict[str, object] | None = None,
    memory: FakeMemoryAccess | None = None,
    user_memory: FakeUserMemoryAccess | None = None,
    observation: FakeObservationAccess | None = None,
) -> AdminCommandService:
    config = FakeConfigProvider(config_overrides or {})
    return AdminCommandService(
        config=config,
        memory=memory or FakeMemoryAccess(),
        user_memory=user_memory or FakeUserMemoryAccess(),
        observation=observation or FakeObservationAccess(),
    )


# ===========================================================================
# parse_command tests
# ===========================================================================


class TestParseCommand:
    """Tests for the standalone parse_command function."""

    def test_hit_basic(self) -> None:
        result = parse_command("/灵轩 status", "灵轩")
        assert result == ("status", [])

    def test_hit_with_args(self) -> None:
        result = parse_command("/灵轩 reset_memory all", "灵轩")
        assert result == ("reset_memory", ["all"])

    def test_hit_empty_rest(self) -> None:
        result = parse_command("/灵轩 ", "灵轩")
        assert result == ("", [])

    def test_hit_empty_rest_no_trailing_space(self) -> None:
        # "/灵轩" without trailing space does NOT match — needs the space
        result = parse_command("/灵轩", "灵轩")
        assert result is None

    def test_miss_no_prefix(self) -> None:
        result = parse_command("你好", "灵轩")
        assert result is None

    def test_miss_wrong_bot_name(self) -> None:
        result = parse_command("/其他 status", "灵轩")
        assert result is None

    def test_alias_reset_memory(self) -> None:
        result = parse_command("/灵轩 重置记忆", "灵轩")
        assert result == ("reset_memory", [])

    def test_alias_status(self) -> None:
        result = parse_command("/灵轩 状态", "灵轩")
        assert result == ("status", [])

    def test_alias_observe(self) -> None:
        result = parse_command("/灵轩 观察", "灵轩")
        assert result == ("observe", [])

    def test_alias_user_memory(self) -> None:
        result = parse_command("/灵轩 用户记忆", "灵轩")
        assert result == ("user_memory", [])

    def test_alias_reset_user_memory(self) -> None:
        result = parse_command("/灵轩 重置用户记忆", "灵轩")
        assert result == ("reset_user_memory", [])

    def test_alias_with_args(self) -> None:
        result = parse_command("/灵轩 重置记忆 all", "灵轩")
        assert result == ("reset_memory", ["all"])

    def test_custom_bot_name(self) -> None:
        result = parse_command("/小轩 status", "小轩")
        assert result == ("status", [])

    def test_unknown_command_passes_through(self) -> None:
        result = parse_command("/灵轩 foobar", "灵轩")
        assert result == ("foobar", [])

    def test_multiple_args(self) -> None:
        result = parse_command("/灵轩 reset_memory user 12345", "灵轩")
        assert result == ("reset_memory", ["user", "12345"])


# ===========================================================================
# AdminCommandService.parse_command (instance method)
# ===========================================================================


class TestServiceParseCommand:
    """Tests for AdminCommandService.parse_command (reads bot_name from config)."""

    def test_uses_config_bot_name(self) -> None:
        svc = _make_service(config_overrides={"BOT_NAME": "小轩"})
        result = svc.parse_command("/小轩 status")
        assert result == ("status", [])

    def test_default_bot_name(self) -> None:
        svc = _make_service()
        result = svc.parse_command("/灵轩 status")
        assert result == ("status", [])

    def test_miss(self) -> None:
        svc = _make_service()
        result = svc.parse_command("hello")
        assert result is None


# ===========================================================================
# AdminCommandService.run — command dispatch
# ===========================================================================


class TestHelpCommand:
    """Empty command returns help list."""

    @pytest.mark.asyncio
    async def test_help(self) -> None:
        svc = _make_service()
        result = await svc.run("", [], _make_ctx())
        assert "status" in result
        assert "reset_memory" in result
        assert "user_memory" in result
        assert "reset_user_memory" in result
        assert "observe" in result

    @pytest.mark.asyncio
    async def test_unknown_command(self) -> None:
        svc = _make_service()
        result = await svc.run("nonexistent", [], _make_ctx())
        assert "未知命令" in result


class TestStatusCommand:
    """status / 状态 command."""

    @pytest.mark.asyncio
    async def test_basic_status(self) -> None:
        memory = FakeMemoryAccess(message_count=10, summary="测试摘要")
        user_memory = FakeUserMemoryAccess(user_ids=[1, 2, 3])
        svc = _make_service(memory=memory, user_memory=user_memory)
        result = await svc.run("status", [], _make_ctx())
        assert "模型:" in result
        assert "私聊:" in result
        assert "群聊:" in result
        assert "观察:" in result
        assert "用户记忆:" in result
        assert "认知整合:" in result
        assert "记忆条数: 10" in result
        assert "摘要: 有" in result
        assert "用户档案数: 3" in result
        assert "API Key:" in result

    @pytest.mark.asyncio
    async def test_status_no_summary(self) -> None:
        memory = FakeMemoryAccess(summary="")
        svc = _make_service(memory=memory)
        result = await svc.run("status", [], _make_ctx())
        assert "摘要: 无" in result

    @pytest.mark.asyncio
    async def test_status_with_meta(self) -> None:
        memory = FakeMemoryAccess(
            meta={"nickname": "小明", "last_active_at": "2025-01-01"}
        )
        svc = _make_service(memory=memory)
        result = await svc.run("status", [], _make_ctx())
        assert "昵称: 小明" in result
        assert "最后活跃: 2025-01-01" in result

    @pytest.mark.asyncio
    async def test_status_group_with_observation(self) -> None:
        observation = FakeObservationAccess(
            state={
                "buffer_len": 8,
                "last_judge_result": "yes:at_bot",
                "in_cooldown": True,
                "cooldown_remaining": 15.3,
            }
        )
        svc = _make_service(observation=observation)
        ctx = _make_ctx(is_group=True, group_id=999)
        result = await svc.run("status", [], ctx)
        assert "观察缓冲: 8 条" in result
        assert "最近 judge: yes:at_bot" in result
        assert "冷却剩余: 15s" in result

    @pytest.mark.asyncio
    async def test_status_group_no_cooldown(self) -> None:
        observation = FakeObservationAccess(
            state={
                "buffer_len": 3,
                "last_judge_result": "",
                "in_cooldown": False,
                "cooldown_remaining": 0.0,
            }
        )
        svc = _make_service(observation=observation)
        ctx = _make_ctx(is_group=True, group_id=999)
        result = await svc.run("status", [], ctx)
        assert "最近 judge: (无)" in result
        assert "冷却" not in result

    @pytest.mark.asyncio
    async def test_status_private_no_observation(self) -> None:
        svc = _make_service()
        result = await svc.run("status", [], _make_ctx(is_group=False))
        assert "观察缓冲" not in result


class TestResetMemoryCommand:
    """reset_memory / 重置记忆 command."""

    @pytest.mark.asyncio
    async def test_default_session_scope(self) -> None:
        memory = FakeMemoryAccess()
        svc = _make_service(memory=memory)
        ctx = _make_ctx()
        result = await svc.run("reset_memory", [], ctx)
        assert "当前会话记忆已清空" in result
        assert ctx.session_id in memory.cleared_sessions

    @pytest.mark.asyncio
    async def test_scope_all(self) -> None:
        memory = FakeMemoryAccess()
        user_memory = FakeUserMemoryAccess(clear_all_count=5)
        svc = _make_service(memory=memory, user_memory=user_memory)
        ctx = _make_ctx()
        result = await svc.run("reset_memory", ["all"], ctx)
        assert "5 个用户档案" in result
        assert ctx.session_id in memory.cleared_sessions
        assert user_memory.cleared_all

    @pytest.mark.asyncio
    async def test_scope_all_chinese(self) -> None:
        memory = FakeMemoryAccess()
        user_memory = FakeUserMemoryAccess(clear_all_count=2)
        svc = _make_service(memory=memory, user_memory=user_memory)
        ctx = _make_ctx()
        result = await svc.run("reset_memory", ["全部"], ctx)
        assert "2 个用户档案" in result

    @pytest.mark.asyncio
    async def test_scope_users(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_all_count=7)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_memory", ["users"], _make_ctx())
        assert "7 个用户档案和社会关系图" in result
        assert user_memory.cleared_all

    @pytest.mark.asyncio
    async def test_scope_users_chinese(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_all_count=4)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_memory", ["用户"], _make_ctx())
        assert "4 个用户档案和社会关系图" in result

    @pytest.mark.asyncio
    async def test_scope_user_with_qq(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=True)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_memory", ["user", "11111"], _make_ctx())
        assert "已清空用户 11111 的档案" in result
        assert 11111 in user_memory.cleared_profiles

    @pytest.mark.asyncio
    async def test_scope_user_chinese_with_qq(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=True)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_memory", ["用户档案", "22222"], _make_ctx())
        assert "已清空用户 22222 的档案" in result

    @pytest.mark.asyncio
    async def test_scope_user_no_profile(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=False)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_memory", ["user", "99999"], _make_ctx())
        assert "没有档案" in result

    @pytest.mark.asyncio
    async def test_scope_user_invalid_qq(self) -> None:
        svc = _make_service()
        result = await svc.run("reset_memory", ["user", "abc"], _make_ctx())
        assert "请提供有效的 QQ 号" in result

    @pytest.mark.asyncio
    async def test_scope_user_missing_qq(self) -> None:
        # "user" without a second arg falls through to session scope
        memory = FakeMemoryAccess()
        svc = _make_service(memory=memory)
        result = await svc.run("reset_memory", ["user"], _make_ctx())
        assert "当前会话记忆已清空" in result


class TestUserMemoryCommand:
    """user_memory / 用户记忆 command."""

    @pytest.mark.asyncio
    async def test_with_qq_arg(self) -> None:
        user_memory = FakeUserMemoryAccess(
            profile_summaries={11111: "称呼: 小明\n关系: familiar"}
        )
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("user_memory", ["11111"], _make_ctx())
        assert "小明" in result

    @pytest.mark.asyncio
    async def test_with_invalid_qq(self) -> None:
        svc = _make_service()
        result = await svc.run("user_memory", ["abc"], _make_ctx())
        assert "请提供有效的 QQ 号" in result

    @pytest.mark.asyncio
    async def test_self_with_profile(self) -> None:
        user_memory = FakeUserMemoryAccess(
            profile_summaries={12345: "称呼: 测试\n关系: close"}
        )
        svc = _make_service(user_memory=user_memory)
        ctx = _make_ctx(user_id=12345)
        result = await svc.run("user_memory", [], ctx)
        assert "测试" in result

    @pytest.mark.asyncio
    async def test_self_no_profile_shows_list(self) -> None:
        user_memory = FakeUserMemoryAccess(
            user_ids=[1, 2, 3],
            profile_summaries={
                1: "称呼: 小明",
                2: "称呼: 小红",
                3: "称呼: 小刚",
            },
        )
        svc = _make_service(user_memory=user_memory)
        ctx = _make_ctx(user_id=12345)  # no profile for this user
        result = await svc.run("user_memory", [], ctx)
        assert "3 个用户档案" in result
        assert "小明" in result

    @pytest.mark.asyncio
    async def test_self_no_profile_no_users(self) -> None:
        user_memory = FakeUserMemoryAccess(user_ids=[], profile_summaries={})
        svc = _make_service(user_memory=user_memory)
        ctx = _make_ctx(user_id=12345)
        result = await svc.run("user_memory", [], ctx)
        assert "暂无用户档案" in result

    @pytest.mark.asyncio
    async def test_list_truncation(self) -> None:
        user_ids = list(range(1, 16))  # 15 users
        summaries = {i: f"称呼: 用户{i}" for i in user_ids}
        user_memory = FakeUserMemoryAccess(user_ids=user_ids, profile_summaries=summaries)
        svc = _make_service(user_memory=user_memory)
        ctx = _make_ctx(user_id=99999)
        result = await svc.run("user_memory", [], ctx)
        assert "15 个用户档案" in result
        assert "还有 5 个" in result


class TestResetUserMemoryCommand:
    """reset_user_memory / 重置用户记忆 command."""

    @pytest.mark.asyncio
    async def test_no_args_clear_self(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=True)
        svc = _make_service(user_memory=user_memory)
        ctx = _make_ctx(user_id=12345)
        result = await svc.run("reset_user_memory", [], ctx)
        assert "你的用户档案已清空" in result
        assert 12345 in user_memory.cleared_profiles

    @pytest.mark.asyncio
    async def test_no_args_self_no_profile(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=False)
        svc = _make_service(user_memory=user_memory)
        ctx = _make_ctx(user_id=12345)
        result = await svc.run("reset_user_memory", [], ctx)
        assert "你还没有用户档案" in result

    @pytest.mark.asyncio
    async def test_all(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_all_count=10)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_user_memory", ["all"], _make_ctx())
        assert "10 个用户档案" in result
        assert user_memory.cleared_all

    @pytest.mark.asyncio
    async def test_all_chinese(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_all_count=8)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_user_memory", ["全部"], _make_ctx())
        assert "8 个用户档案" in result

    @pytest.mark.asyncio
    async def test_graph(self) -> None:
        user_memory = FakeUserMemoryAccess()
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_user_memory", ["graph"], _make_ctx())
        assert "社会关系图已清空" in result
        assert user_memory.cleared_graph

    @pytest.mark.asyncio
    async def test_specific_qq(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=True)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_user_memory", ["11111"], _make_ctx())
        assert "用户 11111 的档案已清空" in result
        assert 11111 in user_memory.cleared_profiles

    @pytest.mark.asyncio
    async def test_specific_qq_no_profile(self) -> None:
        user_memory = FakeUserMemoryAccess(clear_profile_result=False)
        svc = _make_service(user_memory=user_memory)
        result = await svc.run("reset_user_memory", ["11111"], _make_ctx())
        assert "没有档案" in result

    @pytest.mark.asyncio
    async def test_invalid_qq(self) -> None:
        svc = _make_service()
        result = await svc.run("reset_user_memory", ["abc"], _make_ctx())
        assert "请提供有效的 QQ 号" in result


class TestObserveCommand:
    """observe / 观察 command."""

    @pytest.mark.asyncio
    async def test_group_with_full_observation(self) -> None:
        observation = FakeObservationAccess(
            formatted="完整观察内容",
            state={"buffer_len": 5, "last_judge_result": "yes", "in_cooldown": False, "cooldown_remaining": 0.0},
        )
        svc = _make_service(observation=observation)
        ctx = _make_ctx(is_group=True, group_id=999)
        result = await svc.run("observe", [], ctx)
        assert "最近 judge: yes" in result
        assert "完整观察" in result
        assert "完整观察内容" in result

    @pytest.mark.asyncio
    async def test_group_with_recent_entries_no_full(self) -> None:
        entries = [
            ObservationEntry(user_id=1, nickname="小明", text="你好"),
            ObservationEntry(user_id=2, nickname="小红", text="在吗"),
        ]
        observation = FakeObservationAccess(
            formatted="",
            recent=entries,
            state={"buffer_len": 2, "last_judge_result": "", "in_cooldown": False, "cooldown_remaining": 0.0},
        )
        svc = _make_service(observation=observation)
        ctx = _make_ctx(is_group=True, group_id=999)
        result = await svc.run("observe", [], ctx)
        assert "最近 judge: (无)" in result
        assert "[小明]: 你好" in result
        assert "[小红]: 在吗" in result

    @pytest.mark.asyncio
    async def test_group_empty_buffer(self) -> None:
        observation = FakeObservationAccess(
            formatted="",
            recent=[],
            state={"buffer_len": 0, "last_judge_result": "", "in_cooldown": False, "cooldown_remaining": 0.0},
        )
        svc = _make_service(observation=observation)
        ctx = _make_ctx(is_group=True, group_id=999)
        result = await svc.run("observe", [], ctx)
        assert "观察缓冲为空" in result

    @pytest.mark.asyncio
    async def test_private_chat_rejected(self) -> None:
        svc = _make_service()
        ctx = _make_ctx(is_group=False)
        result = await svc.run("observe", [], ctx)
        assert "仅在群聊中可用" in result

    @pytest.mark.asyncio
    async def test_group_without_group_id(self) -> None:
        # is_group=True but group_id=None — edge case
        svc = _make_service()
        ctx = _make_ctx(is_group=True, group_id=None)
        result = await svc.run("observe", [], ctx)
        assert "仅在群聊中可用" in result


# ===========================================================================
# mask_secret helper (unified in settings_defaults)
# ===========================================================================


class TestMaskSecret:
    def test_empty(self) -> None:
        assert mask_secret("") == "(未配置)"

    def test_short(self) -> None:
        assert mask_secret("ab") == "****"

    def test_exactly_4(self) -> None:
        assert mask_secret("abcd") == "****"

    def test_long(self) -> None:
        assert mask_secret("sk-1234567890") == "sk****90"

    def test_5_chars(self) -> None:
        assert mask_secret("abcde") == "ab****de"
