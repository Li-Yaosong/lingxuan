"""Global ConfigProvider singleton for legacy MVP modules.

Set once during bootstrap via ``set_global_config()``.  All old MVP modules
import ``_cfg()`` from here instead of importing constants from
``lingxuan.config``.

New code (core / adapters / admin) should inject ``ConfigProvider`` via
constructor — this module exists solely for the Phase 1 bridge period where
MVP modules remain module-level functions.
"""

from __future__ import annotations

from lingxuan.protocols.config import ConfigProvider

_GLOBAL_CONFIG: ConfigProvider | None = None


def set_global_config(config: ConfigProvider) -> None:
    """Set the global ConfigProvider.  Must be called exactly once during bootstrap."""
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is not None:
        raise RuntimeError(
            "Global ConfigProvider already set; call set_global_config() only once"
        )
    _GLOBAL_CONFIG = config


def _cfg() -> ConfigProvider:
    """Return the global ConfigProvider.  Raises if not yet initialized."""
    if _GLOBAL_CONFIG is None:
        raise RuntimeError(
            "Global ConfigProvider not initialized; call set_global_config() first"
        )
    return _GLOBAL_CONFIG


def mask_api_key(key: str) -> str:
    """Mask a secret value for display.  Migrated from lingxuan.config."""
    if not key:
        return "(未配置)"
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"
