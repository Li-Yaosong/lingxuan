from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# NoneBot2 驱动
DRIVER: str = os.getenv("DRIVER", "~fastapi")

# OpenAI 兼容 API
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "deepseek-chat")

# 机器人设定
BOT_NAME: str = os.getenv("BOT_NAME", "灵轩")
BOT_PERSONA: str = os.getenv("BOT_PERSONA", "")

_admins_raw = os.getenv("BOT_ADMINS", "")
BOT_ADMINS: list[int] = [
    int(x.strip()) for x in _admins_raw.split(",") if x.strip().isdigit()
]

# 对话记忆
MEMORY_WINDOW: int = int(os.getenv("MEMORY_WINDOW", "20"))

# 群聊观察
GROUP_OBSERVE_WINDOW: int = int(os.getenv("GROUP_OBSERVE_WINDOW", "20"))
GROUP_OBSERVE_DELAY: float = float(os.getenv("GROUP_OBSERVE_DELAY", "2.5"))

# 数据目录
DATA_DIR = BASE_DIR / "data"
MEMORY_DIR = DATA_DIR / "memory"
