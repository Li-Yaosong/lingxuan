"""Smoke tests for adapters/onebot/lifecycle.py.

NoneBot's real init/driver are not started here — we mock the nonebot API
surface and verify that our thin wrapper calls through correctly.
"""

from __future__ import annotations

from collections.abc import Awaitable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lingxuan.adapters.onebot.lifecycle import init_nonebot, register_lifecycle, run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(driver: str = "~fastapi") -> MagicMock:
    cfg = MagicMock()
    cfg.get_str.return_value = driver
    return cfg


# ---------------------------------------------------------------------------
# init_nonebot
# ---------------------------------------------------------------------------


class TestInitNonebot:
    @patch("lingxuan.adapters.onebot.lifecycle.nonebot")
    def test_calls_init_with_driver_from_config(self, mock_nb: MagicMock) -> None:
        mock_driver = MagicMock()
        mock_nb.get_driver.return_value = mock_driver

        cfg = _make_config(driver="~fastapi")
        result = init_nonebot(cfg)

        mock_nb.init.assert_called_once_with(driver="~fastapi", log_level="INFO")
        mock_nb.get_driver.assert_called_once()
        mock_driver.register_adapter.assert_called_once()
        assert result is mock_driver

    @patch("lingxuan.adapters.onebot.lifecycle.nonebot")
    def test_registers_onebot_v11_adapter(self, mock_nb: MagicMock) -> None:
        mock_driver = MagicMock()
        mock_nb.get_driver.return_value = mock_driver

        with patch(
            "lingxuan.adapters.onebot.lifecycle.OneBotV11Adapter"
        ) as MockAdapter:
            init_nonebot(_make_config())
            mock_driver.register_adapter.assert_called_once_with(MockAdapter)

    @patch("lingxuan.adapters.onebot.lifecycle.nonebot")
    def test_logs_driver_string(self, mock_nb: MagicMock) -> None:
        mock_nb.get_driver.return_value = MagicMock()
        mock_logger = MagicMock()
        mock_nb.logger = mock_logger

        init_nonebot(_make_config(driver="~fastapi"))

        mock_logger.info.assert_called_once()
        args = mock_logger.info.call_args[0]
        assert "~fastapi" in args


# ---------------------------------------------------------------------------
# register_lifecycle
# ---------------------------------------------------------------------------


class TestRegisterLifecycle:
    def test_attaches_startup_hook(self) -> None:
        mock_driver = MagicMock()
        on_startup = AsyncMock()
        on_shutdown = AsyncMock()

        register_lifecycle(mock_driver, on_startup=on_startup, on_shutdown=on_shutdown)

        mock_driver.on_startup.assert_called_once()

    def test_attaches_shutdown_hook(self) -> None:
        mock_driver = MagicMock()
        on_startup = AsyncMock()
        on_shutdown = AsyncMock()

        register_lifecycle(mock_driver, on_startup=on_startup, on_shutdown=on_shutdown)

        mock_driver.on_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_callback_invokes_injected_fn(self) -> None:
        """The wrapper registered via ``@driver.on_startup`` should call
        the injected ``on_startup`` callback."""
        mock_driver = MagicMock()
        on_startup = AsyncMock()
        on_shutdown = AsyncMock()

        register_lifecycle(mock_driver, on_startup=on_startup, on_shutdown=on_shutdown)

        # Extract the wrapper function that @driver.on_startup decorated
        wrapper = mock_driver.on_startup.call_args[0][0]
        await wrapper()
        on_startup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_callback_invokes_injected_fn(self) -> None:
        mock_driver = MagicMock()
        on_startup = AsyncMock()
        on_shutdown = AsyncMock()

        register_lifecycle(mock_driver, on_startup=on_startup, on_shutdown=on_shutdown)

        wrapper = mock_driver.on_shutdown.call_args[0][0]
        await wrapper()
        on_shutdown.assert_awaited_once()


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestRun:
    @patch("lingxuan.adapters.onebot.lifecycle.nonebot")
    def test_delegates_to_nonebot_run(self, mock_nb: MagicMock) -> None:
        run()
        mock_nb.run.assert_called_once()
