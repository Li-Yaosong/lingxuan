"""WebSocket endpoints: /admin/ws/logs and /admin/ws/status.

Both endpoints require JWT authentication via the ``?token=`` query parameter
(browsers cannot set custom headers on WebSocket connections).  Invalid or
missing tokens cause the connection to be closed immediately with code 4001.

Design notes on back-pressure:
- ``/ws/logs`` uses an ``asyncio.Queue`` with ``put_nowait``; if the queue is
  full (slow consumer), the record is dropped rather than blocking the
  ``LogSink.emit()`` path.  The subscriber callback runs in a thread-safe
  wrapper (``call_soon_threadsafe``) because ``emit`` may originate from
  non-async threads (e.g. loguru).
- ``/ws/status`` pushes on a fixed interval (default 5 s) and subscribes to
  config changes.  Both are async-native so no thread bridge is needed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from lingxuan.admin.auth import InvalidTokenError, decode_token
from lingxuan.admin.deps import get_container
from lingxuan.protocols.logging import LogRecord
from lingxuan.settings_defaults import SETTINGS_BY_KEY, mask_secret

router = APIRouter(tags=["ws"])

# ── WS close codes ──────────────────────────────────────────────────────────
_CLOSE_AUTH = 4001  # authentication failure
_CLOSE_INTERNAL = 4002  # unexpected server error

# ── Queue / interval defaults ───────────────────────────────────────────────
_LOG_QUEUE_SIZE = 512
_STATUS_INTERVAL = 5.0  # seconds


# ---------------------------------------------------------------------------
# Shared auth helper
# ---------------------------------------------------------------------------


def _ws_auth(token: str | None) -> dict[str, Any]:
    """Validate a WS token and return the JWT payload.

    Raises ``WebSocketDisconnect`` with code 4001 on any failure.
    """
    if not token:
        raise WebSocketDisconnect(code=_CLOSE_AUTH)
    config = get_container().config
    try:
        payload = decode_token(config, token)
    except InvalidTokenError:
        raise WebSocketDisconnect(code=_CLOSE_AUTH)
    if payload.get("type") != "access":
        raise WebSocketDisconnect(code=_CLOSE_AUTH)
    username: str = payload.get("sub", "")
    if not username:
        raise WebSocketDisconnect(code=_CLOSE_AUTH)
    return payload


# ---------------------------------------------------------------------------
# WS /admin/ws/logs — real-time log stream
# ---------------------------------------------------------------------------


@router.websocket("/admin/ws/logs")
async def ws_logs(
    ws: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """Stream real-time log records.

    After auth, the client can send JSON messages to adjust filters::

        {"type": "filter", "level": "WARNING", "keyword": "error"}

    Server pushes ``{"type": "log", "ts": ..., "level": ..., ...}`` for each
    matching record.  Slow consumers have records dropped (never blocks emit).
    """
    await ws.accept()
    try:
        payload = _ws_auth(token)
    except WebSocketDisconnect:
        await ws.close(code=_CLOSE_AUTH)
        return

    # Only admin and readonly roles may access
    role = payload.get("role", "")
    if role not in ("admin", "readonly"):
        await ws.close(code=_CLOSE_AUTH)
        return

    log_sink = get_container().log
    loop = asyncio.get_running_loop()

    # Mutable filter state
    filter_level: str | None = None
    filter_keyword: str = ""

    # Queue bridges sync subscriber callback → async consumer loop
    queue: asyncio.Queue[LogRecord | None] = asyncio.Queue(maxsize=_LOG_QUEUE_SIZE)

    def _on_record(record: LogRecord) -> None:
        """Subscriber callback — runs in arbitrary thread (loguru)."""
        # Apply filters
        if filter_level is not None and record.level != filter_level:
            # Also allow "≥" semantics: use level rank for comparison
            from lingxuan.adapters.logging.sink import RingBufferLogSink

            if not RingBufferLogSink._level_gte(record.level, filter_level):
                return
        if filter_keyword and filter_keyword not in record.msg and filter_keyword not in record.logger:
            return
        # Thread-safe enqueue; drop on overflow
        try:
            loop.call_soon_threadsafe(queue.put_nowait, record)
        except asyncio.QueueFull:
            pass  # back-pressure: drop

    unsubscribe = log_sink.subscribe(_on_record)

    try:
        # Dual loop: consume queue + listen for client filter messages
        async def _sender() -> None:
            """Push queued records to the WebSocket."""
            while True:
                item = await queue.get()
                if item is None:
                    return
                try:
                    await ws.send_json({
                        "type": "log",
                        "ts": item.ts.isoformat(),
                        "level": item.level,
                        "logger": item.logger,
                        "msg": item.msg,
                        "extra": item.extra,
                    })
                except Exception:
                    return  # connection broken

        sender_task = asyncio.create_task(_sender())

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "filter":
                    filter_level = msg.get("level") or None
                    filter_keyword = msg.get("keyword", "")
        except WebSocketDisconnect:
            pass
        finally:
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
    finally:
        unsubscribe()


# ---------------------------------------------------------------------------
# WS /admin/ws/status — periodic status push + config change broadcast
# ---------------------------------------------------------------------------


@router.websocket("/admin/ws/status")
async def ws_status(
    ws: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """Push periodic status updates and config change notifications.

    Server messages::

        {"type": "status", "bot_online": ..., ...}   — every ~5s
        {"type": "config_changed", "key": ..., "value": <masked>}  — on change
    """
    await ws.accept()
    try:
        payload = _ws_auth(token)
    except WebSocketDisconnect:
        await ws.close(code=_CLOSE_AUTH)
        return

    role = payload.get("role", "")
    if role not in ("admin", "readonly"):
        await ws.close(code=_CLOSE_AUTH)
        return

    container = get_container()
    config = container.config

    # ── Config change subscription ───────────────────────────────────────
    # Queue bridges sync config callback → async consumer
    config_queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()

    def _on_config_change(key: str, value: object) -> None:
        """Callback from ConfigProvider — may run in any thread."""
        try:
            loop.call_soon_threadsafe(config_queue.put_nowait, (key, value))
        except asyncio.QueueFull:
            pass

    unsub_config = config.subscribe(_on_config_change)

    try:
        # ── Status push loop ─────────────────────────────────────────────
        async def _status_loop() -> None:
            """Periodically push full status snapshot."""
            while True:
                try:
                    data = await _build_status(container)
                    await ws.send_json({"type": "status", **data})
                except Exception:
                    return
                await asyncio.sleep(_STATUS_INTERVAL)

        # ── Config change push ───────────────────────────────────────────
        async def _config_loop() -> None:
            """Push masked config_changed events."""
            while True:
                item = await config_queue.get()
                if item is None:
                    return
                key, value = item
                # Mask secrets before broadcasting
                display_value = _mask_if_secret(key, value)
                try:
                    await ws.send_json({
                        "type": "config_changed",
                        "key": key,
                        "value": display_value,
                    })
                except Exception:
                    return

        status_task = asyncio.create_task(_status_loop())
        config_task = asyncio.create_task(_config_loop())

        try:
            # Keep connection alive; client can disconnect at any time
            while True:
                # We don't expect client messages on /ws/status, but we
                # need to detect disconnection.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            status_task.cancel()
            config_task.cancel()
            for t in (status_task, config_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
    finally:
        unsub_config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _build_status(container: Any) -> dict[str, Any]:
    """Build a status dict reusing the same logic as the REST endpoint."""
    from lingxuan.admin.routes.status import _FEATURE_KEYS

    transport = container.transport
    config = container.config
    stats_service = container.stats_service
    observation_store = container.observation_store

    bot_online = transport.is_connected()
    features = {key: config.get_bool(key) for key in _FEATURE_KEYS}
    model = config.get_str("OPENAI_MODEL")

    ms = await stats_service.memory_stats()

    observe_states: list[dict] = []
    for group_id in observation_store._buffers:
        state = observation_store.state(group_id)
        observe_states.append({
            "group_id": group_id,
            "buffer_len": observation_store.buffer_len(group_id),
            "last_judge_result": state.last_judge_result,
            "in_cooldown": state.cooldown_until > 0,
            "cooldown_remaining": max(0.0, state.cooldown_until),
            "observe_in_flight": state.observe_in_flight,
        })

    return {
        "bot_online": bot_online,
        "features": features,
        "model": model,
        "memory_stats": {
            "sessions": ms.sessions,
            "messages": ms.messages,
            "users": ms.users,
            "active_facts": ms.active_facts,
            "edges": ms.edges,
        },
        "observe_states": observe_states,
    }


def _mask_if_secret(key: str, value: object) -> object:
    """Mask the value if the key is marked as secret in settings_defaults."""
    spec = SETTINGS_BY_KEY.get(key)
    if spec and spec.is_secret:
        return mask_secret(str(value)) if value else "(未配置)"
    return value
