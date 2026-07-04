"""Log history REST endpoint: GET /admin/api/logs.

Query parameters: limit, level, keyword.  Calls ``log_sink.tail()`` and
returns a list of structured log records.  Requires readonly-or-above auth.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from lingxuan.admin.deps import LogDep, RequireReadonlyOk
from lingxuan.admin.schemas import LogRecordResponse, LogsResponse
from lingxuan.protocols.logging import LogRecord

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", response_model=LogsResponse)
async def get_logs(
    log: LogDep,
    user: RequireReadonlyOk,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    level: Annotated[str | None, Query(pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")] = None,
    keyword: Annotated[str, Query(max_length=200)] = "",
) -> LogsResponse:
    """Return recent log records with optional filtering."""
    records = log.tail(limit=limit, level=level, keyword=keyword)
    return LogsResponse(
        records=[_to_response(r) for r in records],
        total=len(records),
    )


def _to_response(rec: LogRecord) -> LogRecordResponse:
    return LogRecordResponse(
        ts=rec.ts.isoformat(),
        level=rec.level,
        logger=rec.logger,
        msg=rec.msg,
        extra=rec.extra,
    )
