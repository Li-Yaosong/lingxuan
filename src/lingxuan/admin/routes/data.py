"""Data management routes: sessions, users, social-graph, export/import.

All endpoints are under ``/admin/api/data``.
Read operations require readonly+ role; write operations require admin role.
All write operations record audit entries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from lingxuan.admin.deps import (
    AuditRepoDep,
    ConfigRepoDep,
    DatabaseDep,
    PluginConfigRepoDep,
    RequireAdmin,
    RequireReadonlyOk,
    SessionRepoDep,
    SocialGraphRepoDep,
    UserProfileRepoDep,
)
from lingxuan.admin.schemas import (
    ImportRequest,
    MessageItem,
    MessageListResponse,
    SessionItem,
    SessionListResponse,
    SessionSummaryResponse,
    SocialEdgeItem,
    SocialGraphResponse,
    UserFactItem,
    UserProfileDetailResponse,
    UserProfileItem,
    UserProfileListResponse,
)
from lingxuan.protocols.messaging import SessionId
from lingxuan.protocols.repositories import (
    SocialEdge,
    StoredMessage,
    UserFact,
    UserProfile,
)


router = APIRouter(prefix="/data", tags=["data"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _dt_to_str(dt: datetime | None) -> str | None:
    """Convert datetime to ISO string, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _parse_dt(val: str | None) -> datetime | None:
    """Parse an optional ISO datetime string."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _parse_sid(session_id: str) -> SessionId:
    """Parse a session_id string, raising 400 on invalid format."""
    try:
        return SessionId.parse(session_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id: {session_id!r}",
        ) from exc


# ---------------------------------------------------------------------------
# GET /sessions — list sessions (keyset pagination)
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    sessions: SessionRepoDep,
    user: RequireReadonlyOk,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_id: str | None = Query(default=None, description="Keyset: return sessions with id < before_id"),
) -> SessionListResponse:
    rows = await sessions.list_sessions(limit=limit + 1, before_id=before_id)
    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[SessionItem] = []
    for s in rows:
        msg_count = await sessions.count_messages(s.session_id)
        items.append(SessionItem(
            id=s.session_id.as_str(),
            kind=s.kind,
            last_active_at=_dt_to_str(s.last_active_at),
            message_count=msg_count,
        ))

    return SessionListResponse(items=items, has_more=has_more)


# ---------------------------------------------------------------------------
# GET /sessions/{id}/messages — keyset-paginated history
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/messages", response_model=MessageListResponse)
async def list_messages(
    session_id: str,
    sessions: SessionRepoDep,
    user: RequireReadonlyOk,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_seq: int | None = Query(default=None, description="Keyset: return messages with seq < before_seq"),
) -> MessageListResponse:
    sid = _parse_sid(session_id)
    session = await sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    rows = await sessions.load_history(sid, limit=limit + 1, before_seq=before_seq)
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        MessageItem(seq=m.seq, role=m.role, content=m.content,
                    user_id=m.user_id, created_at=_dt_to_str(m.created_at) or "")
        for m in rows
    ]
    return MessageListResponse(items=items, has_more=has_more)


# ---------------------------------------------------------------------------
# GET /sessions/{id}/summary
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/summary", response_model=SessionSummaryResponse)
async def get_session_summary(
    session_id: str,
    sessions: SessionRepoDep,
    user: RequireReadonlyOk,
) -> SessionSummaryResponse:
    sid = _parse_sid(session_id)
    session = await sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    summary = await sessions.get_summary(sid)
    entities = await sessions.get_entities(sid)

    return SessionSummaryResponse(
        id=session.session_id.as_str(),
        kind=session.kind,
        summary=summary,
        nickname=session.nickname,
        group_id=session.group_id,
        entities=entities,
    )


# ---------------------------------------------------------------------------
# DELETE /sessions/{id} — clear session (↔ reset_memory)
# ---------------------------------------------------------------------------


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    sessions: SessionRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> None:
    sid = _parse_sid(session_id)
    session = await sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    await sessions.clear(sid)
    await audit.record(
        actor=user["username"],
        action="data.delete_session",
        target=session_id,
        detail={"kind": session.kind},
        success=True,
    )


# ---------------------------------------------------------------------------
# GET /users — user profile list (keyset pagination)
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UserProfileListResponse)
async def list_users(
    profiles: UserProfileRepoDep,
    user: RequireReadonlyOk,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    before_user_id: int | None = Query(default=None),
) -> UserProfileListResponse:
    rows = await profiles.list_profiles(limit=limit + 1, before_user_id=before_user_id)
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        UserProfileItem(
            user_id=p.user_id,
            preferred_name=p.preferred_name,
            stage=p.stage,
            interaction_count=p.interaction_count,
        )
        for p in rows
    ]
    return UserProfileListResponse(items=items, has_more=has_more)


# ---------------------------------------------------------------------------
# GET /users/{uid} — single profile + active facts (↔ user_memory <QQ>)
# ---------------------------------------------------------------------------


@router.get("/users/{uid}", response_model=UserProfileDetailResponse)
async def get_user(
    uid: int,
    profiles: UserProfileRepoDep,
    user: RequireReadonlyOk,
) -> UserProfileDetailResponse:
    profile = await profiles.get(uid)
    if profile is None:
        raise HTTPException(status_code=404, detail="User not found")

    active_facts = [
        UserFactItem(
            id=f.id, content=f.content, category=f.category,
            active=f.active, learned_at=_dt_to_str(f.learned_at) or "",
        )
        for f in profile.facts
        if f.active
    ]

    return UserProfileDetailResponse(
        user_id=profile.user_id,
        preferred_name=profile.preferred_name,
        aliases=profile.aliases,
        group_cards=profile.group_cards,
        stage=profile.stage,
        first_met_at=_dt_to_str(profile.first_met_at),
        last_seen_at=_dt_to_str(profile.last_seen_at),
        interaction_count=profile.interaction_count,
        last_group_id=profile.last_group_id,
        seen_in_private=profile.seen_in_private,
        seen_in_group=profile.seen_in_group,
        impression=profile.impression,
        cognition_summary=profile.cognition_summary,
        facts=active_facts,
    )


# ---------------------------------------------------------------------------
# DELETE /users/{uid} — clear single user (↔ reset_user_memory <QQ>)
# ---------------------------------------------------------------------------


@router.delete("/users/{uid}", status_code=204)
async def delete_user(
    uid: int,
    profiles: UserProfileRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> None:
    deleted = await profiles.delete(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    await audit.record(
        actor=user["username"],
        action="data.delete_user",
        target=str(uid),
        success=True,
    )


# ---------------------------------------------------------------------------
# DELETE /users — clear all users + graph (↔ reset_user_memory all)
# ---------------------------------------------------------------------------


@router.delete("/users", status_code=204)
async def delete_all_users(
    profiles: UserProfileRepoDep,
    graph: SocialGraphRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> None:
    count = await profiles.delete_all()
    await graph.clear()
    await audit.record(
        actor=user["username"],
        action="data.delete_all_users",
        target="all",
        detail={"deleted_count": count},
        success=True,
    )


# ---------------------------------------------------------------------------
# GET /social-graph
# ---------------------------------------------------------------------------


@router.get("/social-graph", response_model=SocialGraphResponse)
async def get_social_graph(
    graph: SocialGraphRepoDep,
    user: RequireReadonlyOk,
) -> SocialGraphResponse:
    edges = await graph.all_edges()
    names = await graph.all_names()

    edge_items = [
        SocialEdgeItem(
            from_user_id=e.from_user_id,
            to_user_id=e.to_user_id,
            relation=e.relation,
            label=e.label,
            evidence=e.evidence,
            group_id=e.group_id,
            learned_at=_dt_to_str(e.learned_at) or "",
        )
        for e in edges
    ]

    return SocialGraphResponse(edges=edge_items, name_index=names)


# ---------------------------------------------------------------------------
# DELETE /social-graph — clear graph (↔ reset_user_memory graph)
# ---------------------------------------------------------------------------


@router.delete("/social-graph", status_code=204)
async def delete_social_graph(
    graph: SocialGraphRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> None:
    await graph.clear()
    await audit.record(
        actor=user["username"],
        action="data.delete_social_graph",
        target="social_graph",
        success=True,
    )


# ---------------------------------------------------------------------------
# GET /export — full JSON export (admin only)
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_data(
    sessions: SessionRepoDep,
    profiles: UserProfileRepoDep,
    graph: SocialGraphRepoDep,
    config_repo: ConfigRepoDep,
    plugin_configs: PluginConfigRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> JSONResponse:
    """Export the full database as a JSON download."""
    all_sessions = await sessions.list_all_sessions()

    # Messages per session (StoredMessage lacks session_id, so group by session)
    messages_data: list[dict[str, Any]] = []
    for s in all_sessions:
        history = await sessions.load_history(s.session_id)
        for m in history:
            messages_data.append({
                "session_id": s.session_id.as_str(),
                "seq": m.seq,
                "role": m.role,
                "content": m.content,
                "user_id": m.user_id,
                "created_at": _dt_to_str(m.created_at),
            })

    # Entities
    all_entities = await sessions.list_all_entities()
    entities_data = [
        {"session_id": sid, "name": name, "user_id": uid}
        for sid, name, uid in all_entities
    ]

    # User profiles + facts (facts carry user_id via the parent profile)
    all_profiles = await profiles.list_all_profiles()
    profiles_data: list[dict[str, Any]] = []
    facts_data: list[dict[str, Any]] = []
    for p in all_profiles:
        profiles_data.append({
            "user_id": p.user_id,
            "preferred_name": p.preferred_name,
            "aliases": p.aliases,
            "group_cards": p.group_cards,
            "stage": p.stage,
            "first_met_at": _dt_to_str(p.first_met_at),
            "last_seen_at": _dt_to_str(p.last_seen_at),
            "interaction_count": p.interaction_count,
            "last_group_id": p.last_group_id,
            "seen_in_private": p.seen_in_private,
            "seen_in_group": p.seen_in_group,
            "impression": p.impression,
            "cognition_summary": p.cognition_summary,
            "cognition_updated_at": _dt_to_str(p.cognition_updated_at),
            "cognition_interaction_at_update": p.cognition_interaction_at_update,
        })
        for f in p.facts:
            facts_data.append({
                "id": f.id,
                "user_id": p.user_id,
                "content": f.content,
                "category": f.category,
                "source_user_id": f.source_user_id,
                "learned_at": _dt_to_str(f.learned_at),
                "confidence": f.confidence,
                "active": f.active,
                "supersedes": f.supersedes,
            })

    # Social edges + name index
    all_edges = await graph.all_edges()
    edges_data = [
        {
            "from_user_id": e.from_user_id,
            "to_user_id": e.to_user_id,
            "relation": e.relation,
            "label": e.label,
            "evidence": e.evidence,
            "group_id": e.group_id,
            "learned_at": _dt_to_str(e.learned_at),
        }
        for e in all_edges
    ]
    name_index = await graph.all_names()

    # Settings — mask secrets
    raw_settings = await config_repo.get_all()
    safe_settings: dict[str, Any] = {}
    try:
        from lingxuan.settings_defaults import SETTINGS_BY_KEY
        for k, v in raw_settings.items():
            spec = SETTINGS_BY_KEY.get(k)
            if spec and spec.is_secret and isinstance(v, str) and v:
                safe_settings[k] = v[:3] + "***"
            else:
                safe_settings[k] = v
    except ImportError:
        safe_settings = dict(raw_settings)

    # Plugin configs
    plugin_data = await plugin_configs.all()

    payload: dict[str, Any] = {
        "sessions": [
            {
                "session_id": s.session_id.as_str(),
                "kind": s.kind,
                "group_id": s.group_id,
                "summary": s.summary,
                "nickname": s.nickname,
                "last_active_at": _dt_to_str(s.last_active_at),
            }
            for s in all_sessions
        ],
        "messages": messages_data,
        "entities": entities_data,
        "user_profiles": profiles_data,
        "user_facts": facts_data,
        "social_edges": edges_data,
        "name_index": name_index,
        "settings": safe_settings,
        "plugin_configs": {k: list(v) for k, v in plugin_data.items()},
    }

    await audit.record(
        actor=user["username"],
        action="data.export",
        target="full",
        detail={"sessions": len(payload["sessions"]), "users": len(profiles_data)},
        success=True,
    )

    return JSONResponse(content=payload)


# ---------------------------------------------------------------------------
# POST /import — full JSON import (admin only, requires confirm=true)
# ---------------------------------------------------------------------------


@router.post("/import")
async def import_data(
    body: ImportRequest,
    sessions: SessionRepoDep,
    profiles: UserProfileRepoDep,
    graph: SocialGraphRepoDep,
    config_repo: ConfigRepoDep,
    plugin_configs: PluginConfigRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> dict[str, Any]:
    """Import data from a JSON backup. Requires ``confirm=true``.

    Uses Repository upsert methods for idempotent import.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Destructive operation: set confirm=true to proceed",
        )

    data = body.data
    counts: dict[str, int] = {}

    # --- sessions ---
    count = 0
    for s in data.sessions:
        sid = SessionId.parse(s["session_id"])
        await sessions.ensure(
            sid,
            group_id=s.get("group_id"),
            nickname=s.get("nickname", ""),
        )
        if s.get("summary"):
            await sessions.set_summary(sid, s["summary"])
        count += 1
    counts["sessions"] = count

    # --- messages ---
    count = 0
    for m in data.messages:
        sid = SessionId.parse(m["session_id"])
        dt = _parse_dt(m.get("created_at")) or datetime.now(timezone.utc)
        msg = StoredMessage(
            role=m["role"],
            content=m["content"],
            user_id=m.get("user_id"),
            created_at=dt,
        )
        await sessions.append_message(sid, msg)
        count += 1
    counts["messages"] = count

    # --- entities ---
    count = 0
    for e in data.entities:
        sid = SessionId.parse(e["session_id"])
        await sessions.merge_entity(sid, e["name"], e["user_id"])
        count += 1
    counts["entities"] = count

    # --- user profiles ---
    count = 0
    for p in data.user_profiles:
        profile = UserProfile(
            user_id=p["user_id"],
            preferred_name=p.get("preferred_name", ""),
            aliases=p.get("aliases", []),
            group_cards=p.get("group_cards", {}),
            stage=p.get("stage", "stranger"),
            first_met_at=_parse_dt(p.get("first_met_at")),
            last_seen_at=_parse_dt(p.get("last_seen_at")),
            interaction_count=p.get("interaction_count", 0),
            last_group_id=p.get("last_group_id"),
            seen_in_private=p.get("seen_in_private", False),
            seen_in_group=p.get("seen_in_group", False),
            impression=p.get("impression", ""),
            cognition_summary=p.get("cognition_summary", ""),
            cognition_updated_at=_parse_dt(p.get("cognition_updated_at")),
            cognition_interaction_at_update=p.get("cognition_interaction_at_update", 0),
        )
        await profiles.upsert(profile)
        count += 1
    counts["user_profiles"] = count

    # --- user facts ---
    count = 0
    for f in data.user_facts:
        dt = _parse_dt(f.get("learned_at")) or datetime.now(timezone.utc)
        fact = UserFact(
            id=f["id"],
            content=f["content"],
            category=f.get("category", "general"),
            source_user_id=f.get("source_user_id", 0),
            learned_at=dt,
            confidence=f.get("confidence", 1.0),
            active=f.get("active", True),
            supersedes=f.get("supersedes"),
        )
        await profiles.add_fact(f["user_id"], fact)
        count += 1
    counts["user_facts"] = count

    # --- social edges ---
    count = 0
    for e in data.social_edges:
        dt = _parse_dt(e.get("learned_at")) or datetime.now(timezone.utc)
        edge = SocialEdge(
            from_user_id=e["from_user_id"],
            to_user_id=e["to_user_id"],
            relation=e["relation"],
            label=e.get("label", ""),
            evidence=e.get("evidence", ""),
            group_id=e.get("group_id"),
            learned_at=dt,
        )
        await graph.add_edge(edge)
        count += 1
    counts["social_edges"] = count

    # --- name index ---
    count = 0
    for name, uid in data.name_index.items():
        await graph.index_name(str(name), int(uid))
        count += 1
    counts["name_index"] = count

    # --- settings (skip secrets) ---
    count = 0
    for k, v in data.settings.items():
        try:
            from lingxuan.settings_defaults import SETTINGS_BY_KEY
            spec = SETTINGS_BY_KEY.get(k)
            if spec and spec.is_secret:
                continue
        except ImportError:
            pass
        await config_repo.set(k, v)
        count += 1
    counts["settings"] = count

    # --- plugin configs ---
    count = 0
    for name, cfg_tuple in data.plugin_configs.items():
        if isinstance(cfg_tuple, list) and len(cfg_tuple) == 2:
            enabled, config = bool(cfg_tuple[0]), cfg_tuple[1]
        elif isinstance(cfg_tuple, dict):
            enabled = cfg_tuple.get("enabled", True)
            config = cfg_tuple.get("config", {})
        else:
            continue
        await plugin_configs.upsert(str(name), enabled=enabled, config=config if isinstance(config, dict) else {})
        count += 1
    counts["plugin_configs"] = count

    await audit.record(
        actor=user["username"],
        action="data.import",
        target="full",
        detail=counts,
        success=True,
    )

    return {"status": "ok", "imported": counts}
