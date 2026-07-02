"""Testable clock abstraction: Clock protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...  # tz-aware UTC

    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...
