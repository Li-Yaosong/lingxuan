"""Audit log query route: filtered keyset-paginated access.

All endpoints are under ``/admin/api/audit``.
Admin-only access (audit may contain sensitive operational details).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from lingxuan.admin.deps import AuditRepoDep, RequireAdmin
from lingxuan.admin.schemas import AuditEntryItem, AuditListResponse


router = APIRouter(prefix="/audit", tags=["audit"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _dt_to_str(dt: object) -> str:
    """Convert datetime to ISO string, or empty string."""
    if dt is None:
        return ""
    return getattr(dt, "isoformat", lambda: str(dt))()


# ---------------------------------------------------------------------------
# GET /audit — query audit log with actor/action filters + keyset pagination
# ---------------------------------------------------------------------------


@router.get("", response_model=AuditListResponse)
async def query_audit(
    audit: AuditRepoDep,
    user: RequireAdmin,
    actor: str | None = Query(default=None, description="Filter by actor username"),
    action: str | None = Query(default=None, description="Filter by action type"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_id: int | None = Query(default=None, description="Keyset: return entries with id < before_id"),
) -> AuditListResponse:
    """Query audit log entries with optional filters and keyset pagination.

    Results are returned in descending order (newest first).
    Use ``before_id`` from the last entry's ``id`` to fetch the next page.
    ``has_more`` indicates whether more entries exist beyond this page.
    """
    # Fetch limit+1 to detect has_more
    rows = await audit.query(
        actor=actor,
        action=action,
        limit=limit + 1,
        before_id=before_id,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        AuditEntryItem(
            id=e.id,
            actor=e.actor,
            action=e.action,
            target=e.target,
            detail=e.detail,
            ip=e.ip,
            success=e.success,
            created_at=_dt_to_str(e.created_at),
        )
        for e in rows
    ]

    return AuditListResponse(items=items, has_more=has_more)
