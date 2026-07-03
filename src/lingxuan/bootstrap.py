"""Bootstrap: process entry point replacing MVP bot.main().

Wires the DI Container, initialises NoneBot with the OneBot adapter,
registers lifecycle hooks, and starts the message transport.

Only this module (and adapters/onebot/*) may import nonebot.
"""

from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from lingxuan._config import set_global_config
from lingxuan.container import Container, build_container


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
    """Startup hook: validate config, init user memory, print summary."""
    logger = nonebot.logger
    issues = _validate_config(container)

    # Initialize user memory (aligns with MVP ensure_user_memory_initialized)
    from lingxuan.user_memory import ensure_user_memory_initialized

    ensure_user_memory_initialized()

    # Print config summary (aligns with MVP startup_check)
    cfg = await container.config.get_all()
    logger.info("灵轩配置摘要: {}", cfg)

    for msg in issues:
        logger.warning("配置检查: {}", msg)

    if not issues:
        logger.info("配置检查通过")

    logger.info("灵轩已上线~")


async def _shutdown(container: Container) -> None:
    """Shutdown hook: log departure."""
    nonebot.logger.info("灵轩下线~")


def main() -> None:
    """Process entry point: build container → init nonebot → register lifecycle → run."""
    # 1. Build Container
    container = build_container()

    # 2. Set global ConfigProvider for legacy MVP modules
    set_global_config(container.config)

    # 2. Initialise NoneBot
    driver_spec = container.config.get_str("DRIVER")
    nonebot.init(driver=driver_spec, log_level="INFO")

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    # 3. Lifecycle hooks
    driver.on_startup(lambda: _startup(container))
    driver.on_shutdown(lambda: _shutdown(container))

    # 4. Register inbound handler
    container.transport.start(container.dialogue.handle_inbound)

    # 5. Run
    nonebot.run()


if __name__ == "__main__":
    main()
