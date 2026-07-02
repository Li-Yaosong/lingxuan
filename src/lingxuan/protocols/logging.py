"""Structured log sink: LogRecord dataclass and LogSink protocol."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class LogRecord:
    ts: datetime
    level: str  # DEBUG / INFO / WARNING / ERROR
    logger: str
    msg: str
    extra: dict = field(default_factory=dict)


class LogSink(Protocol):
    def emit(self, record: LogRecord) -> None: ...

    def tail(
        self,
        *,
        limit: int = 200,
        level: str | None = None,
        keyword: str = "",
    ) -> list[LogRecord]: ...

    def subscribe(self, callback: Callable[[LogRecord], None]) -> Callable[[], None]: ...
