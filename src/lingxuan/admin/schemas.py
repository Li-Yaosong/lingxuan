"""Admin Pydantic schemas: shared request/response models for admin routes."""

from __future__ import annotations

from pydantic import BaseModel, Field, RootModel


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class ConfigSchemaItem(BaseModel):
    """One setting specification returned by GET /config/schema."""

    key: str
    type: str
    default: object
    group: str
    is_secret: bool
    hot_reloadable: bool
    description: str = ""


class ConfigUpdateRequest(RootModel[dict[str, object]]):
    """Body for PUT /config: a flat dict of key→value updates."""


class ConfigUpdateResultItem(BaseModel):
    """Per-key result in PUT /config response."""

    key: str
    success: bool
    error: str | None = None
    needs_restart: bool = False
    value: object | None = None


class ConfigUpdateResponse(BaseModel):
    """Response for PUT /config."""

    results: list[ConfigUpdateResultItem]


# ---------------------------------------------------------------------------
# Status schemas
# ---------------------------------------------------------------------------


class MemoryStatsResponse(BaseModel):
    sessions: int
    messages: int
    users: int
    active_facts: int
    edges: int


class GroupObserveStateResponse(BaseModel):
    group_id: int
    buffer_len: int
    last_judge_result: str = ""
    in_cooldown: bool = False
    cooldown_remaining: float = 0.0
    observe_in_flight: bool = False


class StatusResponse(BaseModel):
    bot_online: bool
    features: dict[str, bool]
    model: str
    memory_stats: MemoryStatsResponse
    observe_states: list[GroupObserveStateResponse] = Field(default_factory=list)


class LLMCheckResponse(BaseModel):
    ok: bool
    latency_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Log schemas
# ---------------------------------------------------------------------------


class LogRecordResponse(BaseModel):
    """One structured log record returned by GET /logs or WS log push."""

    ts: str
    level: str
    logger: str
    msg: str
    extra: dict = Field(default_factory=dict)


class LogsResponse(BaseModel):
    """Response for GET /logs."""

    records: list[LogRecordResponse]
    total: int
