from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingxuan.config import _cfg


@dataclass
class SessionData:
    version: int = 2
    history: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _memory_dir() -> Path:
    return Path(_cfg().get_str("DATA_ROOT")) / "memory"


def _memory_path(session_id: str) -> Path:
    return _memory_dir() / f"{session_id}.json"


def _ensure_dir() -> None:
    _memory_dir().mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_raw(data: Any) -> SessionData:
    if isinstance(data, list):
        return SessionData(history=data)
    if isinstance(data, dict):
        return SessionData(
            version=int(data.get("version", 2)),
            history=list(data.get("history", [])),
            summary=str(data.get("summary", "")),
            meta=dict(data.get("meta", {})),
        )
    return SessionData()


def load_session(session_id: str) -> SessionData:
    path = _memory_path(session_id)
    if not path.exists():
        return SessionData()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        session = _migrate_raw(data)
        if isinstance(data, list):
            save_session(session_id, session)
        return session
    except (json.JSONDecodeError, OSError):
        return SessionData()


def save_session(session_id: str, session: SessionData) -> None:
    _ensure_dir()
    memory_window = _cfg().get_int("MEMORY_WINDOW")
    session.history = session.history[-memory_window * 2 :]
    path = _memory_path(session_id)
    path.write_text(
        json.dumps(
            {
                "version": session.version,
                "history": session.history,
                "summary": session.summary,
                "meta": session.meta,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_history(session_id: str) -> list[dict[str, str]]:
    return load_session(session_id).history


def save_history(session_id: str, history: list[dict[str, str]]) -> None:
    session = load_session(session_id)
    session.history = history
    save_session(session_id, session)


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    user_id: int | None = None,
) -> None:
    session = load_session(session_id)
    msg: dict[str, Any] = {"role": role, "content": content}
    if user_id is not None:
        msg["user_id"] = user_id
    session.history.append(msg)
    save_session(session_id, session)


def clear_history(session_id: str, *, clear_user_profiles: bool = False) -> None:
    path = _memory_path(session_id)
    if path.exists():
        path.unlink()
    if clear_user_profiles:
        from lingxuan.user_memory import clear_user_profile, list_user_profiles

        for uid in list_user_profiles():
            clear_user_profile(uid)


def update_meta(session_id: str, **kwargs: Any) -> None:
    session = load_session(session_id)
    session.meta.update(kwargs)
    session.meta["last_active_at"] = _now_iso()
    save_session(session_id, session)


def get_session_meta(session_id: str) -> dict[str, Any]:
    return dict(load_session(session_id).meta)


def save_summary(session_id: str, summary: str) -> None:
    session = load_session(session_id)
    session.summary = summary
    save_session(session_id, session)


def get_summary(session_id: str) -> str:
    return load_session(session_id).summary


def trim_history_half(session_id: str) -> None:
    session = load_session(session_id)
    half = len(session.history) // 2
    session.history = session.history[half:]
    save_session(session_id, session)


def user_session(user_id: int) -> str:
    return f"private_{user_id}"


def group_session(group_id: int) -> str:
    return f"group_{group_id}"


def get_entities(session_id: str) -> dict[str, int]:
    raw = load_session(session_id).meta.get("entities", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for name, uid in raw.items():
        try:
            result[str(name)] = int(uid)
        except (TypeError, ValueError):
            continue
    return result


def merge_entity(session_id: str, name: str, user_id: int) -> None:
    name = name.strip()
    if not name or not user_id:
        return
    session = load_session(session_id)
    entities = session.meta.setdefault("entities", {})
    if not isinstance(entities, dict):
        entities = {}
        session.meta["entities"] = entities
    entities[name] = user_id
    save_session(session_id, session)


def format_entities_for_prompt(session_id: str) -> str:
    entities = get_entities(session_id)
    if not entities:
        return ""
    lines = [f"- {name}: QQ {uid}" for name, uid in entities.items()]
    return "【群成员昵称】\n" + "\n".join(lines)
