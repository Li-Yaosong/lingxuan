"""DEPRECATED: Legacy MVP configuration module.

This module is retained for reference only.  All new code should use
``lingxuan._config._cfg()`` or inject ``ConfigProvider`` via constructor.
The module-level uppercase constants and helper functions have been removed.
This module will be deleted in Phase 2.
"""

import warnings

warnings.warn(
    "lingxuan.config is deprecated; use lingxuan._config._cfg() or ConfigProvider",
    DeprecationWarning,
    stacklevel=2,
)

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.getenv(key, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _parse_admins(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


@dataclass
class Settings:
    driver: str = "~fastapi"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_model: str = "deepseek-chat"
    bot_name: str = "灵轩"
    bot_persona: str = ""
    bot_admins: list[int] = field(default_factory=list)
    memory_window: int = 20
    group_observe_window: int = 20
    group_observe_delay: float = 1.5
    group_observe_cooldown: float = 30.0
    group_burst_merge_window: float = 10.0
    group_followup_window: float = 60.0
    group_chat_context: int = 6
    group_chat_max_tokens: int = 512
    enable_stream_chunk: bool = True
    group_msg_chunk_max: int = 35
    group_msg_chunk_min: int = 6
    group_msg_chunk_limit: int = 6
    group_chunk_delay_min: float = 0.4
    group_chunk_delay_max: float = 1.2
    enable_private_chat: bool = True
    enable_group_chat: bool = True
    enable_group_observe: bool = True
    enable_memory_summary: bool = True
    enable_user_memory: bool = True
    user_memory_burst_merge: float = 3.0
    user_memory_max_facts: int = 30
    enable_user_cognition_refine: bool = True
    user_cognition_refine_interval: int = 5
    user_cognition_refine_delay: float = 2.0
    user_cognition_max_chars: int = 150
    data_dir: Path = field(default_factory=lambda: BASE_DIR / "data")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            driver=os.getenv("DRIVER", "~fastapi"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
            openai_model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
            bot_name=os.getenv("BOT_NAME", "灵轩"),
            bot_persona=os.getenv("BOT_PERSONA", ""),
            bot_admins=_parse_admins(os.getenv("BOT_ADMINS", "")),
            memory_window=int(os.getenv("MEMORY_WINDOW", "20")),
            group_observe_window=int(os.getenv("GROUP_OBSERVE_WINDOW", "20")),
            group_observe_delay=float(os.getenv("GROUP_OBSERVE_DELAY", "1.5")),
            group_observe_cooldown=float(os.getenv("GROUP_OBSERVE_COOLDOWN", "30")),
            group_burst_merge_window=float(os.getenv("GROUP_BURST_MERGE_WINDOW", "10")),
            group_followup_window=float(os.getenv("GROUP_FOLLOWUP_WINDOW", "60")),
            group_chat_context=int(os.getenv("GROUP_CHAT_CONTEXT", "6")),
            group_chat_max_tokens=int(os.getenv("GROUP_CHAT_MAX_TOKENS", "512")),
            enable_stream_chunk=_env_bool("ENABLE_STREAM_CHUNK", True),
            group_msg_chunk_max=int(os.getenv("GROUP_MSG_CHUNK_MAX", "35")),
            group_msg_chunk_min=int(os.getenv("GROUP_MSG_CHUNK_MIN", "6")),
            group_msg_chunk_limit=int(os.getenv("GROUP_MSG_CHUNK_LIMIT", "6")),
            group_chunk_delay_min=float(os.getenv("GROUP_CHUNK_DELAY_MIN", "0.4")),
            group_chunk_delay_max=float(os.getenv("GROUP_CHUNK_DELAY_MAX", "1.2")),
            enable_private_chat=_env_bool("ENABLE_PRIVATE_CHAT", True),
            enable_group_chat=_env_bool("ENABLE_GROUP_CHAT", True),
            enable_group_observe=_env_bool("ENABLE_GROUP_OBSERVE", True),
            enable_memory_summary=_env_bool("ENABLE_MEMORY_SUMMARY", True),
            enable_user_memory=_env_bool("ENABLE_USER_MEMORY", True),
            user_memory_burst_merge=float(os.getenv("USER_MEMORY_BURST_MERGE", "3.0")),
            user_memory_max_facts=int(os.getenv("USER_MEMORY_MAX_FACTS", "30")),
            enable_user_cognition_refine=_env_bool("ENABLE_USER_COGNITION_REFINE", True),
            user_cognition_refine_interval=int(os.getenv("USER_COGNITION_REFINE_INTERVAL", "5")),
            user_cognition_refine_delay=float(os.getenv("USER_COGNITION_REFINE_DELAY", "2.0")),
            user_cognition_max_chars=int(os.getenv("USER_COGNITION_MAX_CHARS", "150")),
        )

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"


settings = Settings.from_env()
