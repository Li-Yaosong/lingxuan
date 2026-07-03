"""Fake log sink: collects records, supports tail filtering and subscribe."""

from __future__ import annotations

from collections.abc import Callable

from lingxuan.protocols.logging import LogRecord


class FakeLogSink:
    """Implements LogSink protocol with in-memory record collection."""

    def __init__(self) -> None:
        self.records: list[LogRecord] = []
        self._subscribers: list[Callable[[LogRecord], None]] = []

    def emit(self, record: LogRecord) -> None:
        self.records.append(record)
        for cb in self._subscribers:
            cb(record)

    def tail(
        self,
        *,
        limit: int = 200,
        level: str | None = None,
        keyword: str = "",
    ) -> list[LogRecord]:
        result = self.records
        if level is not None:
            result = [r for r in result if r.level == level]
        if keyword:
            result = [r for r in result if keyword in r.msg]
        return result[-limit:]

    def subscribe(self, callback: Callable[[LogRecord], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            self._subscribers.remove(callback)

        return _unsubscribe
