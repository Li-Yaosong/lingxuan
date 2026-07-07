"""Runtime configuration provider protocol."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

Unsubscribe = Callable[[], None]
ConfigChangeCallback = Callable[[str, object], None]


class ConfigProvider(Protocol):
    """Runtime configuration read/write/subscribe interface.

    Conventions for implementers:
    - Resolution priority: DB ``settings`` > ``.env`` > ``settings_defaults.py``.
    - Keys use UPPER_SNAKE style, matching ``.env`` variable names
      (e.g. ``BOT_NAME``, ``ENABLE_GROUP_OBSERVE``).
    - ``set`` must trigger all subscribed callbacks and persist the value
      (DB write + audit log).
    - ``get_all(mask_secrets=True)`` should mask values whose
      ``is_secret`` flag is true in ``settings_defaults``.
    - Unknown keys raise ``KeyError`` from ``get`` / typed getters.
    """

    def get(self, key: str) -> object: ...

    def get_str(self, key: str) -> str: ...

    def get_int(self, key: str) -> int: ...

    def get_float(self, key: str) -> float: ...

    def get_bool(self, key: str) -> bool: ...

    def get_int_list(self, key: str) -> list[int]: ...  # e.g. BOT_ADMINS

    async def set(self, key: str, value: object, *, actor: str = "system") -> None: ...

    async def get_all(self, *, mask_secrets: bool = True) -> dict[str, object]: ...

    def subscribe(self, callback: ConfigChangeCallback) -> Unsubscribe: ...


def mask_secret(value: str) -> str:
    """Mask a secret value for display.

    - Empty → "(未配置)"
    - ≤4 chars → "****"
    - Otherwise → first 2 + **** + last 2
    """
    if not value:
        return "(未配置)"
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}****{value[-2:]}"
