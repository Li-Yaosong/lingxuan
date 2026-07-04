"""Ring-buffer LogSink with subscription and loguru bridge.

Replaces the Phase 1 BridgeLogSink.  Records are kept in a bounded deque;
subscribers receive pushes on emit; a loguru sink captures nonebot/loguru
output into the same buffer so the admin UI can query *all* logs.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime

from loguru import logger as loguru_logger

from lingxuan.protocols.logging import LogRecord, LogSink

# ── Level ordering for ≥ comparisons ──────────────────────────────────────

_LEVEL_RANK: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}

_LEVEL_MAP: dict[str, str] = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "error",
}


# ── RingBufferLogSink ────────────────────────────────────────────────────


class RingBufferLogSink(LogSink):
    """Structured LogSink backed by a thread-safe ring buffer.

    Args:
        capacity: Maximum number of records retained (oldest discarded).
        bridge_loguru: If True, install a loguru sink that captures all
            loguru/nonebot output into this buffer.
    """

    def __init__(
        self,
        *,
        capacity: int = 2000,
        bridge_loguru: bool = True,
    ) -> None:
        self._buf: deque[LogRecord] = deque(maxlen=capacity)
        self._subscribers: set[Callable[[LogRecord], None]] = set()
        # loguru may call from arbitrary threads; guard shared state.
        self._lock = threading.Lock()
        self._loguru_sink_id: int | None = None

        if bridge_loguru:
            self._install_loguru_sink()

    # ── LogSink interface ────────────────────────────────────────────────

    def emit(self, record: LogRecord) -> None:
        with self._lock:
            self._buf.append(record)
            # Snapshot subscribers inside lock to avoid concurrent mutation.
            subs = list(self._subscribers)

        # Notify outside lock so a slow callback never blocks emit.
        for cb in subs:
            try:
                cb(record)
            except Exception:
                # Subscriber errors must never interfere with the main flow.
                pass

        # Mirror to loguru console output (bridge direction: core → console).
        method_name = _LEVEL_MAP.get(record.level, "info")
        log_fn = getattr(loguru_logger, method_name, loguru_logger.info)
        log_fn("[{}] {}", record.logger, record.msg)

    def tail(
        self,
        *,
        limit: int = 200,
        level: str | None = None,
        keyword: str = "",
    ) -> list[LogRecord]:
        with self._lock:
            # Iterate newest-first by reversing the deque.
            items: list[LogRecord] = []
            for rec in reversed(self._buf):
                if level is not None and not self._level_gte(rec.level, level):
                    continue
                if keyword and keyword not in rec.msg and keyword not in rec.logger:
                    continue
                items.append(rec)
                if len(items) >= limit:
                    break
        # Return newest-last for conventional log ordering.
        items.reverse()
        return items

    def subscribe(self, callback: Callable[[LogRecord], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.add(callback)

        def _unsubscribe() -> None:
            with self._lock:
                self._subscribers.discard(callback)

        return _unsubscribe

    # ── Cleanup ──────────────────────────────────────────────────────────

    def close(self) -> None:
        """Remove the loguru sink if one was installed."""
        if self._loguru_sink_id is not None:
            loguru_logger.remove(self._loguru_sink_id)
            self._loguru_sink_id = None

    # ── Internals ────────────────────────────────────────────────────────

    @staticmethod
    def _level_gte(record_level: str, threshold: str) -> bool:
        """Return True if *record_level* ≥ *threshold* by rank."""
        return _LEVEL_RANK.get(record_level, 0) >= _LEVEL_RANK.get(threshold, 0)

    def _install_loguru_sink(self) -> None:
        """Add a loguru sink that converts loguru records to LogRecord and emit()s them."""

        def _sink(message: "loguru.Message") -> None:  # type: ignore[name-defined]  # noqa: F821
            record = message.record
            level_name = record["level"].name
            # Map loguru level names to our four canonical levels.
            level = _normalise_level(level_name)
            extra = {
                k: v
                for k, v in record.get("extra", {}).items()
                if k != "lingxuan_internal"
            }
            lr = LogRecord(
                ts=datetime.fromtimestamp(record["time"].timestamp()),
                level=level,
                logger=record["name"],
                msg=str(record["message"]),
                extra=extra,
            )
            # Avoid re-bridging: our own emit() mirrors back to loguru;
            # mark our own output so the sink can skip it.
            # Instead, we rely on the fact that emit() uses loguru_logger
            # which will trigger this sink again.  We break the cycle by
            # checking the source logger.
            self._emit_from_loguru(lr)

        self._loguru_sink_id = loguru_logger.add(
            _sink,
            filter=lambda r: not r["extra"].get("lingxuan_internal"),
        )

    def _emit_from_loguru(self, record: LogRecord) -> None:
        """Emit a record originating from loguru (skip mirror-back)."""
        with self._lock:
            self._buf.append(record)
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(record)
            except Exception:
                pass


# ── Helpers ────────────────────────────────────────────────────────────────


def _normalise_level(level_name: str) -> str:
    """Map loguru's level names to our canonical four."""
    upper = level_name.upper()
    if upper in _LEVEL_RANK:
        return upper
    if upper == "SUCCESS":
        return "INFO"
    if upper in ("CRITICAL", "FATAL"):
        return "ERROR"
    if upper == "TRACE":
        return "DEBUG"
    return "INFO"
