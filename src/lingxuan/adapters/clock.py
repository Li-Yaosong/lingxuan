"""System clock adapter: stdlib-backed Clock implementation."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from lingxuan.protocols.clock import Clock


class SystemClock(Clock):
    """Concrete Clock using stdlib time/asyncio."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
