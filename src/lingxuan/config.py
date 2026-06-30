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
    group_observe_delay: float = 2.5
    group_observe_cooldown: float = 30.0
    enable_private_chat: bool = True
    enable_group_chat: bool = True
    enable_group_observe: bool = True
    enable_memory_summary: bool = True
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
            group_observe_delay=float(os.getenv("GROUP_OBSERVE_DELAY", "2.5")),
            group_observe_cooldown=float(os.getenv("GROUP_OBSERVE_COOLDOWN", "30")),
            enable_private_chat=_env_bool("ENABLE_PRIVATE_CHAT", True),
            enable_group_chat=_env_bool("ENABLE_GROUP_CHAT", True),
            enable_group_observe=_env_bool("ENABLE_GROUP_OBSERVE", True),
            enable_memory_summary=_env_bool("ENABLE_MEMORY_SUMMARY", True),
        )

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"


settings = Settings.from_env()

# 向后兼容的模块级导出
DRIVER: str = settings.driver
OPENAI_API_KEY: str = settings.openai_api_key
OPENAI_BASE_URL: str = settings.openai_base_url
OPENAI_MODEL: str = settings.openai_model
BOT_NAME: str = settings.bot_name
BOT_PERSONA: str = settings.bot_persona
BOT_ADMINS: list[int] = settings.bot_admins
MEMORY_WINDOW: int = settings.memory_window
GROUP_OBSERVE_WINDOW: int = settings.group_observe_window
GROUP_OBSERVE_DELAY: float = settings.group_observe_delay
GROUP_OBSERVE_COOLDOWN: float = settings.group_observe_cooldown
ENABLE_PRIVATE_CHAT: bool = settings.enable_private_chat
ENABLE_GROUP_CHAT: bool = settings.enable_group_chat
ENABLE_GROUP_OBSERVE: bool = settings.enable_group_observe
ENABLE_MEMORY_SUMMARY: bool = settings.enable_memory_summary
DATA_DIR = settings.data_dir
MEMORY_DIR = settings.memory_dir

_FEATURE_MAP = {
    "enable_private_chat": lambda: settings.enable_private_chat,
    "enable_group_chat": lambda: settings.enable_group_chat,
    "enable_group_observe": lambda: settings.enable_group_observe,
    "enable_memory_summary": lambda: settings.enable_memory_summary,
}


def is_feature_enabled(feature_name: str) -> bool:
    fn = _FEATURE_MAP.get(feature_name)
    return fn() if fn else False


def get_admin_ids() -> list[int]:
    return list(settings.bot_admins)


def get_llm_config() -> dict[str, str]:
    return {
        "api_key": settings.openai_api_key,
        "base_url": settings.openai_base_url,
        "model": settings.openai_model,
    }


def mask_api_key(key: str) -> str:
    if not key:
        return "(未配置)"
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"


def get_runtime_config() -> dict[str, Any]:
    return {
        "driver": settings.driver,
        "bot_name": settings.bot_name,
        "openai_model": settings.openai_model,
        "openai_base_url": settings.openai_base_url,
        "openai_api_key": mask_api_key(settings.openai_api_key),
        "bot_admins": settings.bot_admins,
        "memory_window": settings.memory_window,
        "group_observe_window": settings.group_observe_window,
        "group_observe_delay": settings.group_observe_delay,
        "group_observe_cooldown": settings.group_observe_cooldown,
        "enable_private_chat": settings.enable_private_chat,
        "enable_group_chat": settings.enable_group_chat,
        "enable_group_observe": settings.enable_group_observe,
        "enable_memory_summary": settings.enable_memory_summary,
    }


def validate_config() -> list[str]:
    issues: list[str] = []
    if not settings.openai_api_key:
        issues.append("OPENAI_API_KEY 未配置，LLM 功能将不可用")
    else:
        parsed = urlparse(settings.openai_base_url)
        if not parsed.scheme or not parsed.netloc:
            issues.append(f"OPENAI_BASE_URL 格式无效: {settings.openai_base_url}")
    if not settings.bot_admins:
        issues.append("BOT_ADMINS 未配置，管理员命令将不可用")
    return issues
