"""Tests for RingBufferLogSink: emit, tail, filtering, capacity, subscribe, loguru bridge."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from loguru import logger as loguru_logger

from lingxuan.adapters.logging.sink import RingBufferLogSink
from lingxuan.protocols.logging import LogRecord


# ── Helpers ────────────────────────────────────────────────────────────────


def _rec(
    level: str = "INFO",
    logger: str = "test",
    msg: str = "hello",
    ts: datetime | None = None,
    extra: dict | None = None,
) -> LogRecord:
    return LogRecord(
        ts=ts or datetime.now(),
        level=level,
        logger=logger,
        msg=msg,
        extra=extra or {},
    )


# ── emit + tail ────────────────────────────────────────────────────────────


class TestEmitAndTail:
    def test_emit_then_tail(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        r = _rec()
        sink.emit(r)
        result = sink.tail()
        assert len(result) == 1
        assert result[0] is r

    def test_tail_returns_newest_last(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        r1 = _rec(msg="first")
        r2 = _rec(msg="second")
        sink.emit(r1)
        sink.emit(r2)
        result = sink.tail()
        assert result[0].msg == "first"
        assert result[1].msg == "second"

    def test_tail_empty(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        assert sink.tail() == []


# ── Capacity ───────────────────────────────────────────────────────────────


class TestCapacity:
    def test_capacity_discards_oldest(self) -> None:
        sink = RingBufferLogSink(capacity=3, bridge_loguru=False)
        for i in range(5):
            sink.emit(_rec(msg=f"msg-{i}"))
        result = sink.tail(limit=10)
        assert len(result) == 3
        assert result[0].msg == "msg-2"
        assert result[2].msg == "msg-4"

    def test_default_capacity(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        # Should accept up to 2000 without discarding.
        for i in range(2000):
            sink.emit(_rec(msg=f"m-{i}"))
        result = sink.tail(limit=2001)
        assert len(result) == 2000
        # Emit one more → oldest discarded.
        sink.emit(_rec(msg="overflow"))
        result = sink.tail(limit=2001)
        assert len(result) == 2000
        assert result[0].msg == "m-1"


# ── Level filtering ────────────────────────────────────────────────────────


class TestLevelFilter:
    def test_filter_by_level_gte(self) -> None:
        """tail(level=X) returns records with level ≥ X."""
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(level="DEBUG", msg="dbg"))
        sink.emit(_rec(level="INFO", msg="inf"))
        sink.emit(_rec(level="WARNING", msg="wrn"))
        sink.emit(_rec(level="ERROR", msg="err"))

        result = sink.tail(level="WARNING")
        levels = {r.level for r in result}
        assert levels == {"WARNING", "ERROR"}

    def test_filter_by_level_info(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(level="DEBUG", msg="dbg"))
        sink.emit(_rec(level="INFO", msg="inf"))
        result = sink.tail(level="INFO")
        assert len(result) == 1
        assert result[0].level == "INFO"

    def test_filter_by_level_none_returns_all(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(level="DEBUG"))
        sink.emit(_rec(level="ERROR"))
        result = sink.tail(level=None)
        assert len(result) == 2


# ── Keyword filtering ─────────────────────────────────────────────────────


class TestKeywordFilter:
    def test_keyword_matches_msg(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(msg="session summarized"))
        sink.emit(_rec(msg="LLM call failed"))
        result = sink.tail(keyword="summar")
        assert len(result) == 1
        assert "summar" in result[0].msg

    def test_keyword_matches_logger(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(logger="user_memory", msg="ok"))
        sink.emit(_rec(logger="openai", msg="ok"))
        result = sink.tail(keyword="user_memory")
        assert len(result) == 1
        assert result[0].logger == "user_memory"

    def test_keyword_empty_returns_all(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(msg="a"))
        sink.emit(_rec(msg="b"))
        assert len(sink.tail(keyword="")) == 2

    def test_combined_level_and_keyword(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        sink.emit(_rec(level="WARNING", msg="session summarized"))
        sink.emit(_rec(level="INFO", msg="session summarized"))
        sink.emit(_rec(level="WARNING", msg="other"))
        result = sink.tail(level="WARNING", keyword="session")
        assert len(result) == 1
        assert result[0].level == "WARNING"


# ── Limit ──────────────────────────────────────────────────────────────────


class TestLimit:
    def test_tail_limit(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        for i in range(10):
            sink.emit(_rec(msg=f"m-{i}"))
        result = sink.tail(limit=3)
        assert len(result) == 3
        # Should return the *newest* 3.
        assert result[0].msg == "m-7"
        assert result[2].msg == "m-9"


# ── Subscribe ──────────────────────────────────────────────────────────────


class TestSubscribe:
    def test_subscribe_receives_records(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        received: list[LogRecord] = []
        sink.subscribe(received.append)
        r = _rec(msg="pushed")
        sink.emit(r)
        assert len(received) == 1
        assert received[0] is r

    def test_unsubscribe_stops_receiving(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        received: list[LogRecord] = []
        unsub = sink.subscribe(received.append)
        sink.emit(_rec(msg="first"))
        unsub()
        sink.emit(_rec(msg="second"))
        assert len(received) == 1
        assert received[0].msg == "first"

    def test_subscriber_exception_isolation(self) -> None:
        """A failing subscriber must not block emit or other subscribers."""
        sink = RingBufferLogSink(bridge_loguru=False)
        good: list[LogRecord] = []

        def _bad(rec: LogRecord) -> None:
            raise RuntimeError("boom")

        sink.subscribe(_bad)
        sink.subscribe(good.append)
        sink.emit(_rec(msg="safe"))
        assert len(good) == 1

    def test_multiple_subscribers(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=False)
        a: list[LogRecord] = []
        b: list[LogRecord] = []
        sink.subscribe(a.append)
        sink.subscribe(b.append)
        sink.emit(_rec())
        assert len(a) == 1
        assert len(b) == 1


# ── Loguru bridge ──────────────────────────────────────────────────────────


class TestLoguruBridge:
    def test_loguru_output_captured_in_buffer(self) -> None:
        """loguru logger output should appear in the ring buffer."""
        sink = RingBufferLogSink(bridge_loguru=True)
        try:
            loguru_logger.info("test-bridge-msg")
            result = sink.tail(keyword="test-bridge-msg")
            assert len(result) >= 1
            assert any("test-bridge-msg" in r.msg for r in result)
        finally:
            sink.close()

    def test_loguru_levels_normalised(self) -> None:
        """Loguru levels (SUCCESS, TRACE, CRITICAL) are normalised."""
        sink = RingBufferLogSink(bridge_loguru=True)
        try:
            loguru_logger.success("ok")
            loguru_logger.critical("bad")
            result = sink.tail()
            success_recs = [r for r in result if "ok" in r.msg]
            crit_recs = [r for r in result if "bad" in r.msg]
            assert any(r.level == "INFO" for r in success_recs)
            assert any(r.level == "ERROR" for r in crit_recs)
        finally:
            sink.close()

    def test_no_infinite_loop_on_emit(self) -> None:
        """emit() mirrors to loguru, but the loguru sink must not re-emit it."""
        sink = RingBufferLogSink(bridge_loguru=True)
        try:
            sink.emit(_rec(msg="from-core"))
            # Should not hang or produce duplicate records from the mirror.
            result = sink.tail(keyword="from-core")
            # Exactly one record with this msg (the original emit).
            core_records = [r for r in result if r.msg == "from-core"]
            assert len(core_records) == 1
        finally:
            sink.close()

    def test_close_removes_loguru_sink(self) -> None:
        sink = RingBufferLogSink(bridge_loguru=True)
        sink.close()
        # After close, loguru output should NOT enter the buffer.
        loguru_logger.info("after-close")
        result = sink.tail(keyword="after-close")
        assert len(result) == 0


# ── Thread safety (basic smoke) ────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_emit(self) -> None:
        """Multiple threads emitting should not corrupt the buffer."""
        import threading

        sink = RingBufferLogSink(capacity=500, bridge_loguru=False)
        errors: list[Exception] = []

        def _worker(start: int) -> None:
            try:
                for i in range(100):
                    sink.emit(_rec(msg=f"t-{start}-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(j,)) for j in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        result = sink.tail(limit=1000)
        assert len(result) == 500  # capacity limit
