"""Configuration defaults: keys, types, default values, groups, sensitivity, hot-reload flags.

Single source of truth for all config items. Used by ConfigProvider and admin config page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: Literal["str", "int", "float", "bool", "int_list"]
    default: object
    group: str  # api / bot / observe / chunk / feature / user_memory / storage / admin / security
    is_secret: bool = False
    hot_reloadable: bool = True
    description: str = ""


SETTINGS: list[SettingSpec] = [
    # ── api ──────────────────────────────────────────────────────────────
    SettingSpec("DRIVER", "str", "~fastapi", "api", hot_reloadable=False,
                description="NoneBot2 driver spec"),
    SettingSpec("OPENAI_API_KEY", "str", "", "api", is_secret=True,
                description="OpenAI-compatible API key"),
    SettingSpec("OPENAI_BASE_URL", "str", "https://api.deepseek.com/v1", "api",
                description="OpenAI-compatible API base URL"),
    SettingSpec("OPENAI_MODEL", "str", "deepseek-chat", "api",
                description="Model name for chat completions"),
    # ── bot ──────────────────────────────────────────────────────────────
    SettingSpec("BOT_NAME", "str", "灵轩", "bot",
                description="Bot display name"),
    SettingSpec("BOT_PERSONA", "str", "", "bot",
                description="Custom persona text; non-empty replaces default persona"),
    SettingSpec("BOT_ADMINS", "int_list", [], "bot",
                description="Comma-separated admin user IDs"),
    SettingSpec("MEMORY_WINDOW", "int", 20, "bot",
                description="Max history messages kept per session (before trim)"),
    # ── observe ──────────────────────────────────────────────────────────
    SettingSpec("GROUP_OBSERVE_WINDOW", "int", 20, "observe",
                description="Max messages buffered for group observation"),
    SettingSpec("GROUP_OBSERVE_DELAY", "float", 1.5, "observe",
                description="Debounce delay (seconds) before triggering observation"),
    SettingSpec("GROUP_OBSERVE_COOLDOWN", "float", 30.0, "observe",
                description="Min interval (seconds) between observations for same group"),
    SettingSpec("GROUP_BURST_MERGE_WINDOW", "float", 10.0, "observe",
                description="Time window (seconds) to merge burst messages"),
    SettingSpec("GROUP_FOLLOWUP_WINDOW", "float", 60.0, "observe",
                description="Time window (seconds) for follow-up context after bot reply"),
    SettingSpec("GROUP_CHAT_CONTEXT", "int", 6, "observe",
                description="Number of recent group messages included as context"),
    SettingSpec("GROUP_CHAT_MAX_TOKENS", "int", 512, "observe",
                description="Max tokens for group chat completion"),
    # ── chunk ────────────────────────────────────────────────────────────
    SettingSpec("ENABLE_STREAM_CHUNK", "bool", True, "chunk",
                description="Enable streaming chunked replies in group"),
    SettingSpec("GROUP_MSG_CHUNK_MAX", "int", 35, "chunk",
                description="Max chars per chunk"),
    SettingSpec("GROUP_MSG_CHUNK_MIN", "int", 6, "chunk",
                description="Min chars per chunk"),
    SettingSpec("GROUP_MSG_CHUNK_LIMIT", "int", 6, "chunk",
                description="Max number of chunks per reply"),
    SettingSpec("GROUP_CHUNK_DELAY_MIN", "float", 0.4, "chunk",
                description="Min delay (seconds) between chunks"),
    SettingSpec("GROUP_CHUNK_DELAY_MAX", "float", 1.2, "chunk",
                description="Max delay (seconds) between chunks"),
    # ── feature ──────────────────────────────────────────────────────────
    SettingSpec("ENABLE_PRIVATE_CHAT", "bool", True, "feature",
                description="Enable private chat handler"),
    SettingSpec("ENABLE_GROUP_CHAT", "bool", True, "feature",
                description="Enable group @-reply handler"),
    SettingSpec("ENABLE_GROUP_OBSERVE", "bool", True, "feature",
                description="Enable passive group observation"),
    SettingSpec("ENABLE_MEMORY_SUMMARY", "bool", True, "feature",
                description="Enable session memory summarization"),
    SettingSpec("ENABLE_USER_MEMORY", "bool", True, "feature",
                description="Enable user profile & social graph memory"),
    # ── user_memory ──────────────────────────────────────────────────────
    SettingSpec("USER_MEMORY_BURST_MERGE", "float", 3.0, "user_memory",
                description="Burst merge window (seconds) for user memory updates"),
    SettingSpec("USER_MEMORY_MAX_FACTS", "int", 30, "user_memory",
                description="Max active facts per user; oldest soft-deleted beyond this"),
    SettingSpec("ENABLE_USER_COGNITION_REFINE", "bool", True, "user_memory",
                description="Enable cognition refinement for user profiles"),
    SettingSpec("USER_COGNITION_REFINE_INTERVAL", "int", 5, "user_memory",
                description="Interaction count interval between cognition refinements"),
    SettingSpec("USER_COGNITION_REFINE_DELAY", "float", 2.0, "user_memory",
                description="Delay (seconds) before cognition refinement call"),
    SettingSpec("USER_COGNITION_MAX_CHARS", "int", 150, "user_memory",
                description="Max chars for cognition summary output"),
    # ── storage ──────────────────────────────────────────────────────────
    SettingSpec("DB_URL", "str", "sqlite+aiosqlite:///./data/lingxuan.db", "storage",
                hot_reloadable=False,
                description="SQLAlchemy async database URL (relative path under DATA_ROOT)"),
    SettingSpec("DATA_ROOT", "str", "./data", "storage",
                hot_reloadable=False,
                description="Root directory for data files"),
    SettingSpec("AUTO_MIGRATE", "bool", True, "storage",
                hot_reloadable=False,
                description="Auto-run Alembic migration + first-run JSON import on startup; false = schema only"),
    # ── admin ────────────────────────────────────────────────────────────
    SettingSpec("ADMIN_HOST", "str", "127.0.0.1", "admin",
                hot_reloadable=False,
                description="Admin panel bind host"),
    SettingSpec("ADMIN_PORT", "int", 8081, "admin",
                hot_reloadable=False,
                description="Admin panel bind port"),
    # ── security ─────────────────────────────────────────────────────────
    SettingSpec("SECRET_KEY", "str", "", "security",
                is_secret=True, hot_reloadable=False,
                description="Secret key for JWT signing & config encryption; must be set for admin"),
    SettingSpec("JWT_ACCESS_TTL", "int", 900, "security",
                description="Access token TTL in seconds"),
    SettingSpec("JWT_REFRESH_TTL", "int", 604800, "security",
                description="Refresh token TTL in seconds"),
]

SETTINGS_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SETTINGS}


def parse_value(spec: SettingSpec, raw: str) -> object:
    """Parse a raw string value according to the spec's type.

    Mirrors MVP semantics:
    - bool: "1"/"true"/"yes"/"on" (case-insensitive) → True, else False
    - int_list: comma-separated, whitespace/non-digit tokens ignored
    """
    if spec.type == "str":
        return raw
    if spec.type == "int":
        return int(raw.strip())
    if spec.type == "float":
        return float(raw.strip())
    if spec.type == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if spec.type == "int_list":
        return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    raise ValueError(f"Unknown spec type: {spec.type}")


def mask_secret(value: str) -> str:
    """Mask a secret value for display.

    - Empty → "(未配置)"
    - ≤4 chars → "****"
    - Otherwise → first 2 + **** + last 2
    """
    if not value:
        return "(未配置)"
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}****{value[-2:]}"
