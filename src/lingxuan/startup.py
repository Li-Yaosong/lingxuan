from __future__ import annotations

import nonebot

from lingxuan.config import get_runtime_config, validate_config


async def startup_check() -> None:
    logger = nonebot.logger
    issues = validate_config()
    cfg = get_runtime_config()
    logger.info("灵轩配置摘要: {}", cfg)
    for msg in issues:
        logger.warning("配置检查: {}", msg)
    if not issues:
        logger.info("配置检查通过")


async def shutdown_check() -> None:
    nonebot.logger.info("灵轩下线，配置摘要: {}", get_runtime_config())
