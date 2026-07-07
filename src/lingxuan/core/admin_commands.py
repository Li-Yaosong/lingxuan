"""AdminCommandService: admin command parsing and execution.

Migrates MVP ``admin.py`` into Core with injected service dependencies.
Command parsing and output text are aligned with the MVP; data access
goes through injected services instead of direct file/DB reads.

No framework / IO imports — all dependencies are injected protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lingxuan.protocols.config import ConfigProvider, mask_secret
from lingxuan.protocols.messaging import SessionId


# ---------------------------------------------------------------------------
# CommandContext — shared between DialogueService and AdminCommandService
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    user_id: int
    session_id: SessionId
    is_group: bool = False
    group_id: int | None = None
    nickname: str = ""


# ---------------------------------------------------------------------------
# Injected service protocols (admin-specific surface)
# ---------------------------------------------------------------------------


class MemoryAccess(Protocol):
    """Session memory access surface needed by admin commands."""

    async def count_messages(self, session_id: SessionId) -> int: ...

    async def clear(self, session_id: SessionId) -> None: ...

    async def get_summary(self, session_id: SessionId) -> str: ...

    async def get_meta(self, session_id: SessionId) -> dict: ...


class UserMemoryAccess(Protocol):
    """User memory access surface needed by admin commands."""

    def list_user_ids(self) -> list[int]: ...

    async def load_profile_summary(self, user_id: int) -> str: ...

    async def clear_profile(self, user_id: int) -> bool: ...

    async def clear_all_profiles(self) -> int: ...

    async def clear_social_graph(self) -> None: ...


class ObservationAccess(Protocol):
    """Observation access surface needed by admin commands."""

    def format_observation(self, group_id: int) -> str: ...

    def recent_entries(self, group_id: int, limit: int = 5) -> list: ...

    def observe_state(self, group_id: int) -> dict: ...


# ---------------------------------------------------------------------------
# Aliases for Chinese command names
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    "重置记忆": "reset_memory",
    "状态": "status",
    "观察": "observe",
    "用户记忆": "user_memory",
    "重置用户记忆": "reset_user_memory",
}


# ---------------------------------------------------------------------------
# parse_command (pure function)
# ---------------------------------------------------------------------------


def parse_command(text: str, bot_name: str) -> tuple[str, list[str]] | None:
    """Parse an admin command from *text*.

    Returns ``(cmd, args)`` if *text* starts with ``/{bot_name} ``,
    otherwise ``None``.  Empty rest yields ``("", [])``.
    Chinese aliases are mapped to their English counterparts.
    """
    prefix = f"/{bot_name} "
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix):].strip()
    if not rest:
        return "", []
    parts = rest.split()
    cmd = _ALIASES.get(parts[0], parts[0])
    args = parts[1:]
    return cmd, args


# ---------------------------------------------------------------------------
# AdminCommandService
# ---------------------------------------------------------------------------


class AdminCommandService:
    """Admin command parsing and execution — aligns with MVP ``admin.py``.

    Permissions (is_admin) are checked by the caller (DialogueService)
    before invoking this service.
    """

    def __init__(
        self,
        config: ConfigProvider,
        memory: MemoryAccess,
        user_memory: UserMemoryAccess,
        observation: ObservationAccess,
    ) -> None:
        self._config = config
        self._memory = memory
        self._user_memory = user_memory
        self._observation = observation

    # ── config helpers ────────────────────────────────────────────────────

    @property
    def _bot_name(self) -> str:
        return self._config.get_str("BOT_NAME")

    def _is_feature_enabled(self, key: str) -> bool:
        return self._config.get_bool(key)

    # ── public API ────────────────────────────────────────────────────────

    def parse_command(self, text: str) -> tuple[str, list[str]] | None:
        """Parse command using the bot name from config."""
        return parse_command(text, self._bot_name)

    async def run(self, cmd: str, args: list[str], ctx: CommandContext) -> str:
        """Dispatch to command handler, return text reply."""
        if cmd == "":
            return (
                "可用命令: status / 状态, reset_memory / 重置记忆, "
                "user_memory / 用户记忆, reset_user_memory / 重置用户记忆, "
                "observe / 观察(仅群聊)"
            )
        if cmd == "status":
            return await self._cmd_status(ctx)
        if cmd == "reset_memory":
            return await self._cmd_reset_memory(args, ctx)
        if cmd == "user_memory":
            return await self._cmd_user_memory(args, ctx)
        if cmd == "reset_user_memory":
            return await self._cmd_reset_user_memory(args, ctx)
        if cmd == "observe":
            if ctx.is_group and ctx.group_id is not None:
                return self._cmd_observe(ctx.group_id)
            return "observe 命令仅在群聊中可用"
        return f"未知命令: {cmd}"

    # ── command implementations ───────────────────────────────────────────

    async def _cmd_status(self, ctx: CommandContext) -> str:
        model = self._config.get_str("OPENAI_MODEL")
        history_count = await self._memory.count_messages(ctx.session_id)
        summary = await self._memory.get_summary(ctx.session_id)
        meta = await self._memory.get_meta(ctx.session_id)
        user_ids = self._user_memory.list_user_ids()

        lines = [
            f"模型: {model}",
            f"私聊: {'开' if self._is_feature_enabled('ENABLE_PRIVATE_CHAT') else '关'}",
            f"群聊: {'开' if self._is_feature_enabled('ENABLE_GROUP_CHAT') else '关'}",
            f"观察: {'开' if self._is_feature_enabled('ENABLE_GROUP_OBSERVE') else '关'}",
            f"用户记忆: {'开' if self._is_feature_enabled('ENABLE_USER_MEMORY') else '关'}",
            f"认知整合: {'开' if self._is_feature_enabled('ENABLE_USER_COGNITION_REFINE') else '关'}",
            f"记忆条数: {history_count}",
            f"摘要: {'有' if summary else '无'}",
            f"用户档案数: {len(user_ids)}",
        ]
        if meta.get("nickname"):
            lines.append(f"昵称: {meta['nickname']}")
        if meta.get("last_active_at"):
            lines.append(f"最后活跃: {meta['last_active_at']}")
        if ctx.is_group and ctx.group_id is not None:
            obs = self._observation.observe_state(ctx.group_id)
            lines.append(f"观察缓冲: {obs['buffer_len']} 条")
            lines.append(f"最近 judge: {obs['last_judge_result'] or '(无)'}")
            if obs["in_cooldown"]:
                lines.append(f"冷却剩余: {obs['cooldown_remaining']:.0f}s")

        # API key (masked) — aligns with MVP
        api_key = str(self._config.get("OPENAI_API_KEY") or "")
        masked = mask_secret(api_key)
        lines.append(f"API Key: {masked}")

        return "\n".join(lines)

    async def _cmd_reset_memory(self, args: list[str], ctx: CommandContext) -> str:
        scope = args[0] if args else "session"
        if scope in ("all", "全部"):
            await self._memory.clear(ctx.session_id)
            n = await self._user_memory.clear_all_profiles()
            return f"会话记忆和用户档案已全部清空（{n} 个用户档案）~"
        if scope in ("users", "用户"):
            n = await self._user_memory.clear_all_profiles()
            return f"已清空 {n} 个用户档案和社会关系图~"
        if scope in ("user", "用户档案") and len(args) >= 2:
            try:
                uid = int(args[1])
            except ValueError:
                return "请提供有效的 QQ 号"
            if await self._user_memory.clear_profile(uid):
                return f"已清空用户 {uid} 的档案~"
            return f"用户 {uid} 没有档案"
        await self._memory.clear(ctx.session_id)
        return "当前会话记忆已清空~"

    async def _cmd_user_memory(self, args: list[str], ctx: CommandContext) -> str:
        if args:
            try:
                uid = int(args[0])
            except ValueError:
                return "请提供有效的 QQ 号"
            return await self._user_memory.load_profile_summary(uid)
        uid = ctx.user_id
        summary = await self._user_memory.load_profile_summary(uid)
        # If the user has no profile, show the list instead
        # (aligns with MVP: interaction_count==0 and no facts → list)
        if not summary or summary.strip() == "":
            all_users = self._user_memory.list_user_ids()
            if not all_users:
                return "暂无用户档案"
            lines = [f"共 {len(all_users)} 个用户档案："]
            for u in all_users[:10]:
                profile_text = await self._user_memory.load_profile_summary(u)
                # Extract display name from summary or fall back
                name = "(未命名)"
                if profile_text:
                    for line in profile_text.splitlines():
                        if line.startswith("称呼:") or line.startswith("名称:"):
                            name = line.split(":", 1)[1].strip() or "(未命名)"
                            break
                lines.append(f"- QQ {u}: {name}")
            if len(all_users) > 10:
                lines.append(f"... 还有 {len(all_users) - 10} 个")
            lines.append("\n使用 /灵轩 用户记忆 <QQ号> 查看详情")
            return "\n".join(lines)
        return summary

    async def _cmd_reset_user_memory(self, args: list[str], ctx: CommandContext) -> str:
        if not args:
            if await self._user_memory.clear_profile(ctx.user_id):
                return "你的用户档案已清空~"
            return "你还没有用户档案"
        if args[0] in ("all", "全部"):
            n = await self._user_memory.clear_all_profiles()
            return f"已清空 {n} 个用户档案~"
        if args[0] == "graph":
            await self._user_memory.clear_social_graph()
            return "社会关系图已清空~"
        try:
            uid = int(args[0])
        except ValueError:
            return "请提供有效的 QQ 号"
        if await self._user_memory.clear_profile(uid):
            return f"用户 {uid} 的档案已清空~"
        return f"用户 {uid} 没有档案"

    def _cmd_observe(self, group_id: int) -> str:
        entries = self._observation.recent_entries(group_id, limit=5)
        obs = self._observation.observe_state(group_id)
        header = f"最近 judge: {obs['last_judge_result'] or '(无)'}\n"
        full = self._observation.format_observation(group_id)
        if full:
            return header + f"\n完整观察:\n{full}"
        if not entries:
            return header + "\n观察缓冲为空"
        lines = [f"[{e.nickname}]: {e.text}" for e in entries]
        return header + "\n".join(lines)
