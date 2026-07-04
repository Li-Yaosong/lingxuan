"""Status routes: service health, feature flags, memory stats, LLM reachability.

All endpoints are under ``/admin/api/status``.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter

from lingxuan.admin.deps import (
    ConfigDep,
    LLMDep,
    ObservationStoreDep,
    RequireReadonlyOk,
    StatsServiceDep,
    TransportDep,
)
from lingxuan.admin.schemas import (
    GroupObserveStateResponse,
    LLMCheckResponse,
    MemoryStatsResponse,
    StatusResponse,
)
from lingxuan.protocols.llm import ChatMessage


router = APIRouter(prefix="/status", tags=["status"])

# Feature flag keys exposed in the status response
_FEATURE_KEYS = (
    "ENABLE_PRIVATE_CHAT",
    "ENABLE_GROUP_CHAT",
    "ENABLE_GROUP_OBSERVE",
    "ENABLE_MEMORY_SUMMARY",
    "ENABLE_USER_MEMORY",
    "ENABLE_USER_COGNITION_REFINE",
    "ENABLE_STREAM_CHUNK",
)


# ---------------------------------------------------------------------------
# GET /status — full service status
# ---------------------------------------------------------------------------


@router.get("", response_model=StatusResponse)
async def get_status(
    config: ConfigDep,
    transport: TransportDep,
    stats_service: StatsServiceDep,
    observation_store: ObservationStoreDep,
    user: RequireReadonlyOk,
) -> StatusResponse:
    """Return comprehensive service status."""
    # Bot connectivity — via transport abstraction (no direct nonebot import)
    bot_online = transport.is_connected()

    # Feature flags from config
    features = {key: config.get_bool(key) for key in _FEATURE_KEYS}

    # Model name
    model = config.get_str("OPENAI_MODEL")

    # Memory stats via StatsService
    ms = await stats_service.memory_stats()
    memory_stats = MemoryStatsResponse(
        sessions=ms.sessions,
        messages=ms.messages,
        users=ms.users,
        active_facts=ms.active_facts,
        edges=ms.edges,
    )

    # Observation states — per-group with active buffers
    observe_states: list[GroupObserveStateResponse] = []
    for group_id in observation_store._buffers:
        state = observation_store.state(group_id)
        observe_states.append(GroupObserveStateResponse(
            group_id=group_id,
            buffer_len=observation_store.buffer_len(group_id),
            last_judge_result=state.last_judge_result,
            in_cooldown=state.cooldown_until > 0,
            cooldown_remaining=max(0.0, state.cooldown_until),
            observe_in_flight=state.observe_in_flight,
        ))

    return StatusResponse(
        bot_online=bot_online,
        features=features,
        model=model,
        memory_stats=memory_stats,
        observe_states=observe_states,
    )


# ---------------------------------------------------------------------------
# POST /status/llm-check — LLM reachability probe
# ---------------------------------------------------------------------------


@router.post("/llm-check", response_model=LLMCheckResponse)
async def llm_check(
    llm: LLMDep,
    config: ConfigDep,
    user: RequireReadonlyOk,
) -> LLMCheckResponse:
    """Probe LLM reachability with a minimal chat call.

    Returns ``{ok, latency_ms, error?}``.  Timeout or missing key
    returns ``ok=false`` without raising.
    """
    # Check if API key is configured
    api_key = config.get_str("OPENAI_API_KEY")
    if not api_key:
        return LLMCheckResponse(ok=False, latency_ms=0.0, error="API key not configured")

    start = time.monotonic()
    try:
        await llm.chat(
            [ChatMessage(role="user", content="ping")],
            max_tokens=5,
            temperature=0.0,
            timeout=10.0,
        )
        latency_ms = (time.monotonic() - start) * 1000
        return LLMCheckResponse(ok=True, latency_ms=round(latency_ms, 1))
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        return LLMCheckResponse(
            ok=False,
            latency_ms=round(latency_ms, 1),
            error=str(exc),
        )
