"""Guard test: no module under src/lingxuan/ may import from lingxuan.config.

The only exception is lingxuan.config itself (it defines the deprecated
constants for reference).  All other modules must use ``_cfg()`` from
``lingxuan._config`` or inject ``ConfigProvider``.

This test ensures the P1-15 migration is complete and no regressions occur.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "lingxuan"

# lingxuan.config itself is excluded — it defines the legacy symbols.
_ALLOWLIST: set[str] = {"config.py"}


def _imports_from_lingxuan_config(filepath: Path) -> list[str]:
    """Parse a .py file and return list of ``from lingxuan.config import ...`` lines."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "lingxuan.config":
            names = ", ".join(a.name for a in node.names)
            violations.append(f"from lingxuan.config import {names}")
    return violations


def test_no_legacy_config_imports() -> None:
    violations: list[str] = []
    for py_file in SRC_DIR.rglob("*.py"):
        rel = py_file.relative_to(SRC_DIR)
        if str(rel) in _ALLOWLIST:
            continue
        for line in _imports_from_lingxuan_config(py_file):
            violations.append(f"{rel}: {line}")

    assert not violations, (
        "Found imports from lingxuan.config (should use _cfg() from lingxuan._config):\n"
        + "\n".join(violations)
    )
