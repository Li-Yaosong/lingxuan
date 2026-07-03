"""Tests for SystemClock adapter."""

from __future__ import annotations

import asyncio
from datetime import timezone

import pytest

from lingxuan.adapters.clock import SystemClock


@pytest.fixture()
def clock() -> SystemClock:
    return SystemClock()


class TestSystemClockNow:
    def test_returns_tz_aware_utc(self, clock: SystemClock) -> None:
        dt = clock.now()
        assert dt.tzinfo is not None, "now() must return tz-aware datetime"
        assert dt.tzinfo == timezone.utc

    def test_returns_recent_time(self, clock: SystemClock) -> None:
        from datetime import datetime

        dt = clock.now()
        delta = (datetime.now(timezone.utc) - dt).total_seconds()
        assert abs(delta) < 1.0


class TestSystemClockMonotonic:
    def test_returns_float(self, clock: SystemClock) -> None:
        val = clock.monotonic()
        assert isinstance(val, float)

    def test_monotonically_increasing(self, clock: SystemClock) -> None:
        first = clock.monotonic()
        second = clock.monotonic()
        assert second >= first


class TestSystemClockSleep:
    @pytest.mark.asyncio()
    async def test_sleep_zero(self, clock: SystemClock) -> None:
        # Should return without error
        await clock.sleep(0)

    @pytest.mark.asyncio()
    async def test_sleep_elapses_time(self, clock: SystemClock) -> None:
        before = clock.monotonic()
        await clock.sleep(0.02)
        after = clock.monotonic()
        assert after - before >= 0.01  # at least some time passed
