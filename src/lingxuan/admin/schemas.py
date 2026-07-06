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


# ---------------------------------------------------------------------------
# Data management schemas (P5-05)
# ---------------------------------------------------------------------------


class SessionItem(BaseModel):
    """Session summary in list responses."""

    id: str
    kind: str
    last_active_at: str | None = None
    message_count: int = 0


class SessionListResponse(BaseModel):
    """Response for GET /sessions."""

    items: list[SessionItem]
    has_more: bool


class MessageItem(BaseModel):
    """One message in session history."""

    seq: int
    role: str
    content: str
    user_id: int | None = None
    created_at: str = ""


class MessageListResponse(BaseModel):
    """Response for GET /sessions/{id}/messages."""

    items: list[MessageItem]
    has_more: bool


class SessionSummaryResponse(BaseModel):
    """Response for GET /sessions/{id}/summary."""

    id: str
    kind: str
    summary: str
    nickname: str = ""
    group_id: int | None = None
    entities: dict[str, int] = {}


class UserProfileItem(BaseModel):
    """User profile summary in list responses."""

    user_id: int
    preferred_name: str = ""
    stage: str = "stranger"
    interaction_count: int = 0


class UserProfileListResponse(BaseModel):
    """Response for GET /users."""

    items: list[UserProfileItem]
    has_more: bool


class UserFactItem(BaseModel):
    """One fact in user profile detail."""

    id: str
    content: str
    category: str = "general"
    active: bool = True
    learned_at: str = ""


class UserProfileDetailResponse(BaseModel):
    """Response for GET /users/{uid}."""

    user_id: int
    preferred_name: str = ""
    aliases: list[str] = []
    group_cards: dict[str, str] = {}
    stage: str = "stranger"
    first_met_at: str | None = None
    last_seen_at: str | None = None
    interaction_count: int = 0
    last_group_id: int | None = None
    seen_in_private: bool = False
    seen_in_group: bool = False
    impression: str = ""
    cognition_summary: str = ""
    facts: list[UserFactItem] = []


class SocialEdgeItem(BaseModel):
    """One edge in the social graph."""

    from_user_id: int
    to_user_id: int
    relation: str
    label: str = ""
    evidence: str = ""
    group_id: int | None = None
    learned_at: str = ""


class SocialGraphResponse(BaseModel):
    """Response for GET /social-graph."""

    edges: list[SocialEdgeItem]
    name_index: dict[str, int]


class ExportData(BaseModel):
    """Full data export structure for GET /export."""

    sessions: list[dict]
    messages: list[dict]
    entities: list[dict]
    user_profiles: list[dict]
    user_facts: list[dict]
    social_edges: list[dict]
    name_index: dict[str, int]
    settings: dict[str, object]
    plugin_configs: dict[str, tuple[bool, dict]]


class ImportRequest(BaseModel):
    """Body for POST /import — must include confirm=true."""

    confirm: bool = False
    data: ExportData


# ---------------------------------------------------------------------------
# Plugin schemas (P5-06)
# ---------------------------------------------------------------------------


class PluginItem(BaseModel):
    """Plugin info returned by GET /plugins."""

    name: str
    version: str
    enabled: bool
    hooks: list[str]
    config: dict = {}
    config_reload_strategy: str = "hot"
    """How config changes take effect: 'hot' = immediate via on_config_change, 'reload' = requires plugin re-setup."""


class PluginListResponse(BaseModel):
    """Response for GET /plugins."""

    items: list[PluginItem]


class PluginUpdateRequest(BaseModel):
    """Body for PUT /plugins/{name}."""

    enabled: bool | None = None
    config: dict | None = None


class PluginUpdateResponse(BaseModel):
    """Response for PUT /plugins/{name}."""

    name: str
    enabled: bool
    config: dict = {}
    config_reload_strategy: str = "hot"


# ---------------------------------------------------------------------------
# Audit schemas (P5-06)
# ---------------------------------------------------------------------------


class AuditEntryItem(BaseModel):
    """One audit log entry."""

    id: int
    actor: str
    action: str
    target: str = ""
    detail: dict = {}
    ip: str = ""
    success: bool = True
    created_at: str = ""


class AuditListResponse(BaseModel):
    """Response for GET /audit."""

    items: list[AuditEntryItem]
    has_more: bool
