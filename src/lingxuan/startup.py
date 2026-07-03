from __future__ import annotations

import nonebot

from lingxuan.config import _cfg
from lingxuan.user_memory import ensure_user_memory_initialized


async def startup_check() -> None:
    logger = nonebot.logger
    cfg = _cfg()

    # Inline validate_config equivalent
    issues: list[str] = []
    api_key = cfg.get_str("OPENAI_API_KEY")
    if not api_key:
        issues.append("OPENAI_API_KEY 未配置，LLM 功能将不可用")
    else:
        base_url = cfg.get_str("OPENAI_BASE_URL")
        if base_url and not base_url.startswith(("http://", "https://")):
            issues.append(f"OPENAI_BASE_URL 格式无效: {base_url}")
    admins = cfg.get_int_list("BOT_ADMINS")
    if not admins:
        issues.append("BOT_ADMINS 未配置，管理员命令将不可用")

    # Inline get_runtime_config equivalent
    cfg_all = await cfg.get_all()
    logger.info("灵轩配置摘要: {}", cfg_all)

    for msg in issues:
        logger.warning("配置检查: {}", msg)

    ensure_user_memory_initialized()

    if not issues:
        logger.info("配置检查通过")


async def shutdown_check() -> None:
    cfg_all = await _cfg().get_all()
    nonebot.logger.info("灵轩下线，配置摘要: {}", cfg_all)
