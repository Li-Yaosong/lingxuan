"""Guard test: no module under src/lingxuan/ may import legacy module-level constants.

The old ``lingxuan.config`` module that exported ``BOT_NAME``, ``ENABLE_*``,
``settings`` singleton, etc. has been deleted.  The current ``lingxuan.config``
only exports bridge helpers (``_cfg``, ``set_global_config``, ``mask_api_key``).

This test ensures no module re-introduces or imports the old constant pattern.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "lingxuan"

# Symbols that should NOT be imported from lingxuan.config (legacy constants)
_FORBIDDEN_SYMBOLS = frozenset({
    "settings",
    "Settings",
    "DRIVER",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "BOT_NAME",
    "BOT_PERSONA",
    "BOT_ADMINS",
    "MEMORY_WINDOW",
    "GROUP_OBSERVE_WINDOW",
    "GROUP_OBSERVE_DELAY",
    "GROUP_OBSERVE_COOLDOWN",
    "GROUP_BURST_MERGE_WINDOW",
    "GROUP_FOLLOWUP_WINDOW",
    "GROUP_CHAT_CONTEXT",
    "GROUP_CHAT_MAX_TOKENS",
    "ENABLE_STREAM_CHUNK",
    "GROUP_MSG_CHUNK_MAX",
    "GROUP_MSG_CHUNK_MIN",
    "GROUP_MSG_CHUNK_LIMIT",
    "GROUP_CHUNK_DELAY_MIN",
    "GROUP_CHUNK_DELAY_MAX",
    "ENABLE_PRIVATE_CHAT",
    "ENABLE_GROUP_CHAT",
    "ENABLE_GROUP_OBSERVE",
    "ENABLE_MEMORY_SUMMARY",
    "ENABLE_USER_MEMORY",
    "USER_MEMORY_BURST_MERGE",
    "USER_MEMORY_MAX_FACTS",
    "ENABLE_USER_COGNITION_REFINE",
    "USER_COGNITION_REFINE_INTERVAL",
    "USER_COGNITION_REFINE_DELAY",
    "USER_COGNITION_MAX_CHARS",
    "DATA_DIR",
    "MEMORY_DIR",
    "BASE_DIR",
})


def _imports_forbidden_symbols(filepath: Path) -> list[str]:
    """Parse a .py file and check for imports of legacy constants from lingxuan.config."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "lingxuan.config":
            for alias in node.names:
                if alias.name in _FORBIDDEN_SYMBOLS:
                    violations.append(f"from lingxuan.config import {alias.name}")
    return violations


def test_no_legacy_config_imports() -> None:
    violations: list[str] = []
    for py_file in SRC_DIR.rglob("*.py"):
        rel = py_file.relative_to(SRC_DIR)
        for line in _imports_forbidden_symbols(py_file):
            violations.append(f"{rel}: {line}")

    assert not violations, (
        "Found imports of legacy constants from lingxuan.config "
        "(should use _cfg() or inject ConfigProvider):\n"
        + "\n".join(violations)
    )
