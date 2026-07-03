"""Phase 1 minimal LogSink: bridge to nonebot.logger.

Phase 4 (P4-01) will replace this with a ring-buffer + subscription + bridge
implementation.  This file only guarantees Core can obtain a usable LogSink
without blocking Phase 1.
"""

from __future__ import annotations

from collections.abc import Callable

import nonebot

from lingxuan.protocols.logging import LogRecord, LogSink

_LEVEL_MAP: dict[str, str] = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
}


class BridgeLogSink(LogSink):
    """Minimal LogSink that forwards to nonebot's global logger."""

    def emit(self, record: LogRecord) -> None:
        logger = nonebot.logger
        method_name = _LEVEL_MAP.get(record.level, "info")
        log_fn = getattr(logger, method_name, logger.info)
        log_fn("[%s] %s", record.logger, record.msg)

    def tail(
        self,
        *,
        limit: int = 200,
        level: str | None = None,
        keyword: str = "",
    ) -> list[LogRecord]:
        # TODO(P4-01): ring buffer implementation
        return []

    def subscribe(self, callback: Callable[[LogRecord], None]) -> Callable[[], None]:
        # TODO(P4-01): real subscription with ring buffer
        return lambda: None
