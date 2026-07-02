from __future__ import annotations

from dataclasses import dataclass

from lingxuan.config import BOT_NAME, OPENAI_MODEL, get_runtime_config, is_feature_enabled
from lingxuan.group_observer import format_observation, get_observe_state, get_recent_entries
from lingxuan.memory import clear_history, get_session_meta, get_summary, load_history
from lingxuan.user_memory import (
    clear_all_user_memory,
    clear_social_graph,
    clear_user_profile,
    format_user_profile_summary,
    list_user_profiles,
    load_user_profile,
)

COMMAND_PREFIX = f"/{BOT_NAME} "


@dataclass
class CommandContext:
    user_id: int
    session_id: str
    is_group: bool = False
    group_id: int | None = None
    nickname: str = ""


def parse_command(text: str) -> tuple[str, list[str]] | None:
    if not text.startswith(COMMAND_PREFIX):
        return None
    rest = text[len(COMMAND_PREFIX) :].strip()
    if not rest:
        return "", []
    parts = rest.split()
    cmd = parts[0]
    args = parts[1:]
    aliases = {
        "重置记忆": "reset_memory",
        "状态": "status",
        "观察": "observe",
        "用户记忆": "user_memory",
        "重置用户记忆": "reset_user_memory",
    }
    cmd = aliases.get(cmd, cmd)
    return cmd, args


async def run_command(cmd: str, args: list[str], ctx: CommandContext) -> str:
    if cmd == "":
        return (
            "可用命令: status / 状态, reset_memory / 重置记忆, "
            "user_memory / 用户记忆, reset_user_memory / 重置用户记忆, "
            "observe / 观察(仅群聊)"
        )
    if cmd == "status":
        return _cmd_status(ctx)
    if cmd == "reset_memory":
        return _cmd_reset_memory(args, ctx)
    if cmd == "user_memory":
        return _cmd_user_memory(args, ctx)
    if cmd == "reset_user_memory":
        return _cmd_reset_user_memory(args, ctx)
    if cmd == "observe":
        if ctx.is_group and ctx.group_id is not None:
            return _cmd_observe(ctx.group_id)
        return "observe 命令仅在群聊中可用"
    return f"未知命令: {cmd}"


def _cmd_reset_memory(args: list[str], ctx: CommandContext) -> str:
    scope = args[0] if args else "session"
    if scope in ("all", "全部"):
        clear_history(ctx.session_id)
        n = clear_all_user_memory()
        return f"会话记忆和用户档案已全部清空（{n} 个用户档案）~"
    if scope in ("users", "用户"):
        n = clear_all_user_memory()
        return f"已清空 {n} 个用户档案和社会关系图~"
    if scope in ("user", "用户档案") and len(args) >= 2:
        try:
            uid = int(args[1])
        except ValueError:
            return "请提供有效的 QQ 号"
        if clear_user_profile(uid):
            return f"已清空用户 {uid} 的档案~"
        return f"用户 {uid} 没有档案"
    clear_history(ctx.session_id)
    return "当前会话记忆已清空~"


def _cmd_user_memory(args: list[str], ctx: CommandContext) -> str:
    if args:
        try:
            uid = int(args[0])
        except ValueError:
            return "请提供有效的 QQ 号"
        return format_user_profile_summary(uid)
    uid = ctx.user_id
    profile = load_user_profile(uid)
    if profile.relationship.interaction_count == 0 and not profile.facts:
        all_users = list_user_profiles()
        if not all_users:
            return "暂无用户档案"
        lines = [f"共 {len(all_users)} 个用户档案："]
        for u in all_users[:10]:
            p = load_user_profile(u)
            lines.append(f"- QQ {u}: {p.identity.preferred_name or '(未命名)'}")
        if len(all_users) > 10:
            lines.append(f"... 还有 {len(all_users) - 10} 个")
        lines.append("\n使用 /灵轩 用户记忆 <QQ号> 查看详情")
        return "\n".join(lines)
    return format_user_profile_summary(uid)


def _cmd_reset_user_memory(args: list[str], ctx: CommandContext) -> str:
    if not args:
        if clear_user_profile(ctx.user_id):
            return "你的用户档案已清空~"
        return "你还没有用户档案"
    if args[0] in ("all", "全部"):
        n = clear_all_user_memory()
        return f"已清空 {n} 个用户档案~"
    if args[0] == "graph":
        clear_social_graph()
        return "社会关系图已清空~"
    try:
        uid = int(args[0])
    except ValueError:
        return "请提供有效的 QQ 号"
    if clear_user_profile(uid):
        return f"用户 {uid} 的档案已清空~"
    return f"用户 {uid} 没有档案"


def _cmd_status(ctx: CommandContext) -> str:
    cfg = get_runtime_config()
    history = load_history(ctx.session_id)
    meta = get_session_meta(ctx.session_id)
    summary = get_summary(ctx.session_id)
    lines = [
        f"模型: {OPENAI_MODEL}",
        f"私聊: {'开' if is_feature_enabled('enable_private_chat') else '关'}",
        f"群聊: {'开' if is_feature_enabled('enable_group_chat') else '关'}",
        f"观察: {'开' if is_feature_enabled('enable_group_observe') else '关'}",
        f"用户记忆: {'开' if is_feature_enabled('enable_user_memory') else '关'}",
        f"认知整合: {'开' if is_feature_enabled('enable_user_cognition_refine') else '关'}",
        f"记忆条数: {len(history)}",
        f"摘要: {'有' if summary else '无'}",
        f"用户档案数: {len(list_user_profiles())}",
    ]
    if meta.get("nickname"):
        lines.append(f"昵称: {meta['nickname']}")
    if meta.get("last_active_at"):
        lines.append(f"最后活跃: {meta['last_active_at']}")
    if ctx.is_group and ctx.group_id is not None:
        obs = get_observe_state(ctx.group_id)
        lines.append(f"观察缓冲: {obs['buffer_len']} 条")
        lines.append(f"最近 judge: {obs['last_judge_result'] or '(无)'}")
        if obs["in_cooldown"]:
            lines.append(f"冷却剩余: {obs['cooldown_remaining']:.0f}s")
    lines.append(f"API Key: {cfg['openai_api_key']}")
    return "\n".join(lines)


def _cmd_observe(group_id: int) -> str:
    entries = get_recent_entries(group_id, limit=5)
    obs = get_observe_state(group_id)
    header = f"最近 judge: {obs['last_judge_result'] or '(无)'}\n"
    full = format_observation(group_id)
    if full:
        return header + f"\n完整观察:\n{full}"
    if not entries:
        return header + "\n观察缓冲为空"
    lines = [f"[{e.nickname}]: {e.text}" for e in entries]
    return header + "\n".join(lines)
