"""Tests for OneBotTransport: send / send_stream / _build_group_msg.

Matcher registration is not tested here (requires a running nonebot instance);
that belongs in integration tests.  We focus on the sending logic using a
mock bot, verifying call order, at-segment presence, and delay behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lingxuan.adapters.onebot.transport import OneBotTransport
from lingxuan.protocols.messaging import (
    OutboundChunk,
    OutboundMessage,
    ReplyTarget,
    SessionId,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> MagicMock:
    """Return a mock ConfigProvider with sensible defaults."""
    cfg = MagicMock()
    defaults = {
        "ENABLE_PRIVATE_CHAT": True,
        "ENABLE_GROUP_CHAT": True,
    }
    defaults.update(overrides)

    def _get(key: str) -> object:
        return defaults.get(key)

    def _get_bool(key: str) -> bool:
        return bool(defaults.get(key, False))

    cfg.get = MagicMock(side_effect=_get)
    cfg.get_bool = MagicMock(side_effect=_get_bool)
    return cfg


def _make_log() -> MagicMock:
    log = MagicMock()
    log.emit = MagicMock()
    return log


def _make_bot() -> MagicMock:
    """Mock Bot with async send methods."""
    bot = MagicMock()
    bot.self_id = "12345"
    bot.send_private_msg = AsyncMock()
    bot.send_group_msg = AsyncMock()
    return bot


def _private_target(user_id: int = 999) -> ReplyTarget:
    return ReplyTarget(session_id=SessionId(kind="private", peer_id=user_id))


def _group_target(group_id: int = 111) -> ReplyTarget:
    return ReplyTarget(session_id=SessionId(kind="group", peer_id=group_id))


async def _chunk_iter(*chunks: OutboundChunk) -> AsyncIterator[OutboundChunk]:
    """Turn OutboundChunk args into an async iterator."""
    for c in chunks:
        yield c


# ---------------------------------------------------------------------------
# _build_group_msg
# ---------------------------------------------------------------------------


class TestBuildGroupMsg:
    def test_first_chunk_with_at(self) -> None:
        chunk = OutboundChunk(text="hello", at_user_id=42)
        msg = OneBotTransport._build_group_msg(chunk, is_first=True)
        # MessageSegment.at(42) + " hello" — check string representation
        assert "hello" in str(msg)
        assert "42" in str(msg)

    def test_first_chunk_without_at(self) -> None:
        chunk = OutboundChunk(text="hello", at_user_id=None)
        msg = OneBotTransport._build_group_msg(chunk, is_first=True)
        assert msg == "hello"

    def test_non_first_chunk_ignores_at(self) -> None:
        chunk = OutboundChunk(text="world", at_user_id=42)
        msg = OneBotTransport._build_group_msg(chunk, is_first=False)
        assert msg == "world"

    def test_non_first_no_at(self) -> None:
        chunk = OutboundChunk(text="world", at_user_id=None)
        msg = OneBotTransport._build_group_msg(chunk, is_first=False)
        assert msg == "world"


# ---------------------------------------------------------------------------
# send — private
# ---------------------------------------------------------------------------


class TestSendPrivate:
    @pytest.mark.asyncio
    async def test_single_chunk(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        out = OutboundMessage(
            target=_private_target(888),
            chunks=[OutboundChunk(text="hi there")],
        )

        with patch.object(transport, "_current_bot", return_value=bot):
            await transport.send(out)

        bot.send_private_msg.assert_called_once_with(user_id=888, message="hi there")
        bot.send_group_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_chunk_with_delay(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        out = OutboundMessage(
            target=_private_target(888),
            chunks=[
                OutboundChunk(text="first", delay_before=0.0),
                OutboundChunk(text="second", delay_before=0.05),
                OutboundChunk(text="third", delay_before=0.05),
            ],
        )

        with patch.object(transport, "_current_bot", return_value=bot), \
             patch("lingxuan.adapters.onebot.transport.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await transport.send(out)

        assert bot.send_private_msg.call_count == 3
        calls = bot.send_private_msg.call_args_list
        assert calls[0].kwargs["message"] == "first"
        assert calls[1].kwargs["message"] == "second"
        assert calls[2].kwargs["message"] == "third"

        # delay_before=0 should NOT trigger sleep
        # delay_before=0.05 should trigger sleep twice
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert 0.0 not in sleep_calls
        assert sleep_calls == [0.05, 0.05]


# ---------------------------------------------------------------------------
# send — group
# ---------------------------------------------------------------------------


class TestSendGroup:
    @pytest.mark.asyncio
    async def test_single_chunk_with_at(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        out = OutboundMessage(
            target=_group_target(555),
            chunks=[OutboundChunk(text="hello!", at_user_id=42)],
        )

        with patch.object(transport, "_current_bot", return_value=bot):
            await transport.send(out)

        bot.send_group_msg.assert_called_once()
        call_kwargs = bot.send_group_msg.call_args.kwargs
        assert call_kwargs["group_id"] == 555
        # First chunk with at_user_id → MessageSegment.at(42) + " hello!"
        msg = call_kwargs["message"]
        assert "hello!" in str(msg)
        assert "42" in str(msg)
        bot.send_private_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_chunk_first_at_rest_plain(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        out = OutboundMessage(
            target=_group_target(555),
            chunks=[
                OutboundChunk(text="first", at_user_id=42, delay_before=0.0),
                OutboundChunk(text="second", delay_before=0.1),
                OutboundChunk(text="third", delay_before=0.1),
            ],
        )

        with patch.object(transport, "_current_bot", return_value=bot), \
             patch("lingxuan.adapters.onebot.transport.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await transport.send(out)

        assert bot.send_group_msg.call_count == 3
        calls = bot.send_group_msg.call_args_list

        # First: at + text
        msg0 = calls[0].kwargs["message"]
        assert "first" in str(msg0)
        assert "42" in str(msg0)

        # Second & third: plain text
        assert calls[1].kwargs["message"] == "second"
        assert calls[2].kwargs["message"] == "third"

        # Sleep called for delay_before > 0 only
        sleep_durations = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_durations == [0.1, 0.1]

    @pytest.mark.asyncio
    async def test_no_at_user_id_plain_text(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        out = OutboundMessage(
            target=_group_target(555),
            chunks=[OutboundChunk(text="just text", at_user_id=None)],
        )

        with patch.object(transport, "_current_bot", return_value=bot):
            await transport.send(out)

        bot.send_group_msg.assert_called_once_with(
            group_id=555, message="just text"
        )


# ---------------------------------------------------------------------------
# send_stream — private
# ---------------------------------------------------------------------------


class TestSendStreamPrivate:
    @pytest.mark.asyncio
    async def test_single_chunk(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        chunks = _chunk_iter(OutboundChunk(text="stream msg"))

        with patch.object(transport, "_current_bot", return_value=bot):
            result = await transport.send_stream(_private_target(777), chunks)

        assert result == "stream msg"
        bot.send_private_msg.assert_called_once_with(user_id=777, message="stream msg")

    @pytest.mark.asyncio
    async def test_multi_chunk_returns_concatenated(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        chunks = _chunk_iter(
            OutboundChunk(text="A", delay_before=0.0),
            OutboundChunk(text="B", delay_before=0.01),
            OutboundChunk(text="C", delay_before=0.01),
        )

        with patch.object(transport, "_current_bot", return_value=bot), \
             patch("lingxuan.adapters.onebot.transport.asyncio.sleep", new_callable=AsyncMock):
            result = await transport.send_stream(_private_target(777), chunks)

        assert result == "ABC"
        assert bot.send_private_msg.call_count == 3


# ---------------------------------------------------------------------------
# send_stream — group
# ---------------------------------------------------------------------------


class TestSendStreamGroup:
    @pytest.mark.asyncio
    async def test_first_at_rest_plain(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        chunks = _chunk_iter(
            OutboundChunk(text="hello", at_user_id=42, delay_before=0.0),
            OutboundChunk(text="world", delay_before=0.02),
        )

        with patch.object(transport, "_current_bot", return_value=bot), \
             patch("lingxuan.adapters.onebot.transport.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await transport.send_stream(_group_target(333), chunks)

        assert result == "helloworld"
        assert bot.send_group_msg.call_count == 2

        # First chunk has at
        msg0 = bot.send_group_msg.call_args_list[0].kwargs["message"]
        assert "hello" in str(msg0)
        assert "42" in str(msg0)

        # Second chunk is plain
        assert bot.send_group_msg.call_args_list[1].kwargs["message"] == "world"

        # Only the second chunk's delay_before triggers sleep
        sleep_durations = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_durations == [0.02]

    @pytest.mark.asyncio
    async def test_empty_stream_returns_empty(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        async def _empty() -> AsyncIterator[OutboundChunk]:
            return
            yield  # make this an async generator

        with patch.object(transport, "_current_bot", return_value=bot):
            result = await transport.send_stream(_group_target(333), _empty())

        assert result == ""
        bot.send_group_msg.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_self_id
# ---------------------------------------------------------------------------


class TestResolveSelfId:
    @pytest.mark.asyncio
    async def test_returns_bot_self_id(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        bot.self_id = "99887"
        transport = OneBotTransport(cfg, log)

        with patch.object(transport, "_current_bot", return_value=bot):
            sid = await transport.resolve_self_id()

        assert sid == 99887


# ---------------------------------------------------------------------------
# _current_bot
# ---------------------------------------------------------------------------


class TestCurrentBot:
    @pytest.mark.asyncio
    async def test_raises_when_no_bots(self) -> None:
        cfg = _make_config()
        log = _make_log()
        transport = OneBotTransport(cfg, log)

        with patch("lingxuan.adapters.onebot.transport.nonebot.get_bots", return_value={}):
            with pytest.raises(RuntimeError, match="No bot instance"):
                transport._current_bot()

    @pytest.mark.asyncio
    async def test_returns_first_bot(self) -> None:
        cfg = _make_config()
        log = _make_log()
        bot1 = _make_bot()
        transport = OneBotTransport(cfg, log)

        with patch("lingxuan.adapters.onebot.transport.nonebot.get_bots", return_value={"1": bot1}):
            result = transport._current_bot()

        assert result is bot1


# ---------------------------------------------------------------------------
# send — delay_before=0 does NOT sleep
# ---------------------------------------------------------------------------


class TestNoDoubleDelay:
    @pytest.mark.asyncio
    async def test_zero_delay_no_sleep(self) -> None:
        """Transport must NOT add its own random delay — only chunk.delay_before."""
        cfg = _make_config()
        log = _make_log()
        bot = _make_bot()
        transport = OneBotTransport(cfg, log)

        out = OutboundMessage(
            target=_group_target(100),
            chunks=[
                OutboundChunk(text="a", delay_before=0.0),
                OutboundChunk(text="b", delay_before=0.0),
            ],
        )

        with patch.object(transport, "_current_bot", return_value=bot), \
             patch("lingxuan.adapters.onebot.transport.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await transport.send(out)

        # delay_before=0 for both → no sleep at all
        mock_sleep.assert_not_called()
