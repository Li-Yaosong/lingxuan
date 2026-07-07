"""Fake config provider: dict-backed, supports subscribe and mask_secrets."""

from __future__ import annotations

from collections.abc import Callable

from lingxuan.config.defaults import SETTINGS, SETTINGS_BY_KEY, SettingSpec
from lingxuan.protocols.config import ConfigChangeCallback, Unsubscribe, mask_secret


class FakeConfigProvider:
    """Implements ConfigProvider protocol backed by a plain dict."""

    def __init__(self, overrides: dict[str, object] | None = None) -> None:
        self._data: dict[str, object] = {s.key: s.default for s in SETTINGS}
        if overrides:
            self._data.update(overrides)
        self._subscribers: list[ConfigChangeCallback] = []

    def get(self, key: str) -> object:
        if key not in self._data:
            raise KeyError(key)
        return self._data[key]

    def get_str(self, key: str) -> str:
        return str(self.get(key))

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def get_float(self, key: str) -> float:
        return float(self.get(key))

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def get_int_list(self, key: str) -> list[int]:
        val = self.get(key)
        if isinstance(val, list):
            return [int(v) for v in val]
        raise TypeError(f"{key} is not a list: {type(val)}")

    async def set(self, key: str, value: object, *, actor: str = "system") -> None:
        if key not in self._data:
            raise KeyError(key)
        self._data[key] = value
        for cb in self._subscribers:
            cb(key, value)

    async def get_all(self, *, mask_secrets: bool = True) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in self._data.items():
            if mask_secrets:
                spec = SETTINGS_BY_KEY.get(key)
                if spec and spec.is_secret:
                    result[key] = mask_secret(str(value))
                    continue
            result[key] = value
        return result

    def subscribe(self, callback: ConfigChangeCallback) -> Unsubscribe:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            self._subscribers.remove(callback)

        return _unsubscribe
