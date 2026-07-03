"""NoneBot initialization & lifecycle hooks for the OneBot v11 adapter.

Migrates MVP ``bot.py`` + ``startup.py`` NoneBot init, adapter registration,
and startup/shutdown hooks into the Adapter layer.  Called by bootstrap; no
business logic lives here — concrete startup checks are injected as callbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.drivers import Driver

if TYPE_CHECKING:
    from lingxuan.protocols.config import ConfigProvider

__all__ = ["init_nonebot", "register_lifecycle", "run"]


def init_nonebot(config: ConfigProvider) -> Driver:
    """Initialize NoneBot, register the OneBot v11 adapter, and return the driver.

    Mirrors the MVP ``bot.py`` module-level init sequence, but driven by
    ``ConfigProvider`` instead of module-level constants.
    """
    driver_str = config.get_str("DRIVER")
    nonebot.init(driver=driver_str, log_level="INFO")

    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    nonebot.logger.info("NoneBot 已初始化 (driver={})", driver_str)
    return driver


def register_lifecycle(
    driver: Driver,
    *,
    on_startup: Callable[[], Awaitable[None]],
    on_shutdown: Callable[[], Awaitable[None]],
) -> None:
    """Attach startup / shutdown callbacks to the NoneBot driver.

    The callbacks themselves are provided by bootstrap (config validation,
    DB init, user-memory init, etc.).  This function only wires them into
    NoneBot's lifecycle — it contains zero business logic.
    """

    @driver.on_startup  # type: ignore[misc]
    async def _on_startup() -> None:
        await on_startup()
        nonebot.logger.info("灵轩已上线~")

    @driver.on_shutdown  # type: ignore[misc]
    async def _on_shutdown() -> None:
        await on_shutdown()


def run() -> None:
    """Block and run the NoneBot event loop (delegates to ``nonebot.run()``)."""
    nonebot.run()
