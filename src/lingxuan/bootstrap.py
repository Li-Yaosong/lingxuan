"""Bootstrap: process entry point replacing MVP bot.main().

Wires the DI Container, initialises NoneBot with the OneBot adapter,
registers lifecycle hooks, starts the message transport, and launches
the admin FastAPI sub-app on an independent port.

Only this module (and adapters/onebot/*) may import nonebot.
"""

from __future__ import annotations

import asyncio
import logging

import nonebot
import uvicorn

from lingxuan.adapters.onebot.lifecycle import (
    init_nonebot,
    register_lifecycle,
    run,
)
from lingxuan.container import Container, build_container

logger = logging.getLogger(__name__)


def _validate_config(container: Container) -> list[str]:
    """Check required config keys; return warning messages for missing ones."""
    issues: list[str] = []
    api_key = container.config.get_str("OPENAI_API_KEY")
    if not api_key:
        issues.append("OPENAI_API_KEY 未配置，LLM 调用将返回 fallback")
    base_url = container.config.get_str("OPENAI_BASE_URL")
    if base_url and not base_url.startswith(("http://", "https://")):
        issues.append(f"OPENAI_BASE_URL 格式不正确: {base_url}")
    admins = container.config.get_int_list("BOT_ADMINS")
    if not admins:
        issues.append("BOT_ADMINS 未配置，管理员命令不可用")
    return issues


async def _startup(container: Container) -> None:
    """Startup hook: auto-migrate, validate config, init user memory, print summary."""
    logger = nonebot.logger

    # Auto-migrate: schema upgrade + first-run JSON import (controlled by AUTO_MIGRATE).
    from lingxuan.migration.auto import AutoMigrateError, run_auto_migrate

    try:
        result = await run_auto_migrate(container.db, container.config)
        if result.import_performed:
            logger.info("自动迁移: 首次数据导入已完成")
        elif result.import_needed and not result.import_performed:
            logger.warning("自动迁移: 需要导入但未执行 (检查日志)")
    except AutoMigrateError as exc:
        logger.error("自动迁移失败, 拒绝启动: {}", exc)
        raise

    # Load DB values into ConfigProvider (triggers _ensure_db_loaded on first access)
    _ = container.config_repo

    # Initialize user memory service
    await container.user_memory.ensure_user_memory_initialized()

    # Initialize plugins: wire services, bridge config changes, discover & register
    await container.init_plugins()

    # Auto-start NapCat if configured
    if container.config.get_bool("NAPCAT_AUTO_START"):
        _try_start_napcat(container.config)

    # Validate config
    issues = _validate_config(container)

    # Print config summary
    cfg = await container.config.get_all()
    logger.info("灵轩配置摘要: {}", cfg)

    for msg in issues:
        logger.warning("配置检查: {}", msg)

    if not issues:
        logger.info("配置检查通过")

    logger.info("SQLite 存储就绪")


async def _shutdown(container: Container) -> None:
    """Shutdown hook: dispose DB, log departure."""
    await container.db.dispose()
    nonebot.logger.info("灵轩下线~")


def _try_start_napcat(config: "ConfigProvider") -> None:
    """Try to auto-start NapCat in background mode.

    Non-fatal: if NapCat is already running or can't start, just log a warning.
    """
    import os
    from pathlib import Path

    from lingxuan.napcat.manager import NapCatManager

    napcat_dir = Path(config.get_str("NAPCAT_DIR"))
    qq_dir = Path(config.get_str("NAPCAT_QQ_DIR"))

    # Make NAPCAT_WS_URL available to the manager's _ensure_onebot11_configs
    os.environ.setdefault("NAPCAT_WS_URL", config.get_str("NAPCAT_WS_URL"))

    manager = NapCatManager(napcat_dir=napcat_dir, qq_dir=qq_dir)

    if manager.is_running():
        nonebot.logger.info("NapCat 已在运行中，跳过自动启动")
        return

    try:
        manager.start(foreground=False)
        nonebot.logger.info("NapCat 自动启动成功")
    except Exception as exc:
        nonebot.logger.warning("NapCat 自动启动失败: {}", exc)


def _start_admin_server(container: Container, driver: nonebot.drivers.Driver) -> None:
    """Create the admin FastAPI app and schedule its uvicorn server.

    The admin server runs on an independent port (default 127.0.0.1:8081)
    as an asyncio task within the same event loop as NoneBot.

    Fallback: if running the admin on an independent port proves
    problematic (e.g. event-loop integration issues), set the env var
    ``ADMIN_SAME_PORT=1`` to mount the admin sub-app on NoneBot's
    FastAPI driver instead.  The independent-port approach is the
    default and preferred method.
    """
    import os

    from lingxuan.admin.app import create_admin_app

    admin_app = create_admin_app(container)
    admin_host = container.config.get_str("ADMIN_HOST")
    admin_port = container.config.get_int("ADMIN_PORT")

    same_port = os.environ.get("ADMIN_SAME_PORT", "").strip() in ("1", "true", "yes")
    if same_port:
        # Fallback: mount on NoneBot's FastAPI app (same port)
        from nonebot import get_app

        nb_app = get_app()
        nb_app.mount("/admin", admin_app)
        logger.info("管理端已挂载于同端口 (ADMIN_SAME_PORT=1)")
        return

    config = uvicorn.Config(
        app=admin_app,
        host=admin_host,
        port=admin_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    @driver.on_startup  # type: ignore[misc]
    async def _launch_admin() -> None:
        """Start the admin uvicorn server as a background asyncio task."""
        asyncio.create_task(server.serve())
        nonebot.logger.info(
            "管理端已启动 → http://{}:{}", admin_host, admin_port
        )

    @driver.on_shutdown  # type: ignore[misc]
    async def _shutdown_admin() -> None:
        """Gracefully shut down the admin server."""
        server.should_exit = True
        nonebot.logger.info("管理端已关闭")


def main() -> None:
    """Process entry point: build container → init nonebot → register lifecycle → start admin → run."""
    # 1. Build Container (set_global_config happens inside _build_config)
    container = build_container()

    # 2. Initialise NoneBot via lifecycle adapter
    driver = init_nonebot(container.config)

    # 3. Register lifecycle hooks
    register_lifecycle(
        driver,
        on_startup=lambda: _startup(container),
        on_shutdown=lambda: _shutdown(container),
    )

    # 4. Register inbound handler
    container.transport.start(container.dialogue.handle_inbound)

    # 5. Start admin FastAPI sub-app on independent port
    _start_admin_server(container, driver)

    # 6. Run
    run()


if __name__ == "__main__":
    main()
