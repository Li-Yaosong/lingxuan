from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from lingxuan.config import _cfg, set_global_config
from lingxuan.adapters.config_provider import EnvConfigProvider
from lingxuan.startup import shutdown_check, startup_check

set_global_config(EnvConfigProvider())

nonebot.init(
    driver=_cfg().get_str("DRIVER"),
    log_level="INFO",
)

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

import lingxuan.handlers.private  # noqa: E402, F401
import lingxuan.handlers.group  # noqa: E402, F401


@driver.on_startup
async def _startup() -> None:
    await startup_check()
    nonebot.logger.info("灵轩已上线~")


@driver.on_shutdown
async def _shutdown() -> None:
    await shutdown_check()


def main() -> None:
    nonebot.run()


if __name__ == "__main__":
    main()
