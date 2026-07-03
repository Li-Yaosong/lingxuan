"""Fake clock: controllable time for tests, no real waiting."""

from __future__ import annotations

from datetime import datetime, timezone


class FakeClock:
    """Implements Clock protocol with manually controllable time."""

    def __init__(
        self,
        now: datetime | None = None,
        monotonic_start: float = 0.0,
    ) -> None:
        self._now = now or datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._monotonic = monotonic_start
        self.sleep_calls: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self._monotonic += seconds

    def advance(self, seconds: float) -> None:
        """Advance both wall clock and monotonic clock by *seconds*."""
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)
        self._monotonic += seconds

    def set_now(self, dt: datetime) -> None:
        self._now = dt
