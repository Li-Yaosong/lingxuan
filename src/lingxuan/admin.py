from __future__ import annotations

from dataclasses import dataclass

from lingxuan.config import BOT_NAME, OPENAI_MODEL, get_runtime_config, is_feature_enabled
from lingxuan.group_observer import format_observation, get_observe_state, get_recent_entries
from lingxuan.memory import clear_history, get_session_meta, get_summary, load_history

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
    }
    cmd = aliases.get(cmd, cmd)
    return cmd, args


async def run_command(cmd: str, args: list[str], ctx: CommandContext) -> str:
    if cmd == "":
        return "可用命令: status / 状态, reset_memory / 重置记忆, observe / 观察(仅群聊)"
    if cmd == "status":
        return _cmd_status(ctx)
    if cmd == "reset_memory":
        clear_history(ctx.session_id)
        return "记忆已清空~"
    if cmd == "observe":
        if ctx.is_group and ctx.group_id is not None:
            return _cmd_observe(ctx.group_id)
        return "observe 命令仅在群聊中可用"
    return f"未知命令: {cmd}"


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
        f"记忆条数: {len(history)}",
        f"摘要: {'有' if summary else '无'}",
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
    return header + "\n" + "\n".join(lines)
