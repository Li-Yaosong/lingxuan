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


class ConfigUpdateResponse(BaseModel):
    """Response for PUT /config."""

    results: list[ConfigUpdateResultItem]
