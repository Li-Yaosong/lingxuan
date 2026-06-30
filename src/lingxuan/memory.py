from __future__ import annotations

import json
from pathlib import Path

from lingxuan.config import MEMORY_DIR, MEMORY_WINDOW

_MEMORY_DIR = Path(MEMORY_DIR)


def _memory_path(session_id: str) -> Path:
    return _MEMORY_DIR / f"{session_id}.json"


def _ensure_dir() -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def load_history(session_id: str) -> list[dict[str, str]]:
    path = _memory_path(session_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(session_id: str, history: list[dict[str, str]]) -> None:
    _ensure_dir()
    trimmed = history[-MEMORY_WINDOW * 2:]
    path = _memory_path(session_id)
    path.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_message(session_id: str, role: str, content: str) -> None:
    history = load_history(session_id)
    history.append({"role": role, "content": content})
    save_history(session_id, history)


def clear_history(session_id: str) -> None:
    path = _memory_path(session_id)
    if path.exists():
        path.unlink()


def user_session(user_id: int) -> str:
    return f"private_{user_id}"


def group_session(group_id: int) -> str:
    return f"group_{group_id}"
