"""EnvConfigProvider: env + memory config with subscribe support.

Resolution priority (Phase 1): memory override (``set``) > DB repo > .env > defaults.
DB repo is optional; when provided, its values are loaded once at startup and
sit between env and defaults in priority.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from dotenv import load_dotenv

from lingxuan.protocols.config import ConfigChangeCallback, Unsubscribe
from lingxuan.protocols.repositories import ConfigRepository
from lingxuan.settings_defaults import SETTINGS, SETTINGS_BY_KEY, SettingSpec, mask_secret, parse_value


class EnvConfigProvider:
    """ConfigProvider backed by settings_defaults + .env + optional DB repo.

    Phase 1: DB persistence is optional. ``set`` updates memory and triggers
    callbacks; if ``db_repo`` is provided, it also persists to DB.
    Phase 2 will add full DB layering (P2-07/P2-10).
    """

    def __init__(
        self,
        *,
        db_repo: ConfigRepository | None = None,
        dotenv_path: str | None = None,
        _skip_dotenv: bool = False,
    ) -> None:
        # 1. Build defaults from SETTINGS
        self._values: dict[str, object] = {s.key: s.default for s in SETTINGS}

        # 2. Load .env (does not override existing os.environ entries)
        if not _skip_dotenv:
            load_dotenv(dotenv_path, override=False)

        # 3. Overlay env values
        for spec in SETTINGS:
            env_val = os.environ.get(spec.key)
            if env_val is not None:
                self._values[spec.key] = parse_value(spec, env_val)

        # 4. DB repo: load once at startup (Phase 1)
        self._db_repo = db_repo
        self._db_loaded = False

        # 5. Memory overrides (from ``set`` calls)
        self._overrides: dict[str, object] = {}

        self._subscribers: list[ConfigChangeCallback] = []

    async def _ensure_db_loaded(self) -> None:
        """Load DB values once on first access (lazy, async-safe in single-loop)."""
        if self._db_repo is None or self._db_loaded:
            return
        db_data = await self._db_repo.get_all()
        for key, value in db_data.items():
            if key in self._values:
                self._values[key] = value
        self._db_loaded = True

    def _resolve(self, key: str) -> object:
        """Resolve value with priority: override > (db already merged) > env > default."""
        if key in self._overrides:
            return self._overrides[key]
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]

    def _coerce(self, key: str, value: object, target_type: str) -> object:
        """Coerce a value to the expected type, using parse_value for strings."""
        if target_type == "str":
            return str(value)
        if target_type == "int":
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            return int(value)  # type: ignore[arg-type]
        if target_type == "float":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return float(value)  # type: ignore[arg-type]
        if target_type == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if target_type == "int_list":
            if isinstance(value, list):
                return [int(v) for v in value]
            if isinstance(value, str):
                spec = SETTINGS_BY_KEY.get(key)
                if spec:
                    return parse_value(spec, value)
                return [int(x.strip()) for x in value.split(",") if x.strip().isdigit()]
        return value

    # ── ConfigProvider interface ──────────────────────────────────────────

    def get(self, key: str) -> object:
        if key not in SETTINGS_BY_KEY:
            raise KeyError(key)
        return self._resolve(key)

    def get_str(self, key: str) -> str:
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(key)
        value = self._resolve(key)
        return str(self._coerce(key, value, spec.type))

    def get_int(self, key: str) -> int:
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(key)
        value = self._resolve(key)
        result = self._coerce(key, value, spec.type)
        return int(result)  # type: ignore[arg-type]

    def get_float(self, key: str) -> float:
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(key)
        value = self._resolve(key)
        result = self._coerce(key, value, spec.type)
        return float(result)  # type: ignore[arg-type]

    def get_bool(self, key: str) -> bool:
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(key)
        value = self._resolve(key)
        result = self._coerce(key, value, spec.type)
        return bool(result)

    def get_int_list(self, key: str) -> list[int]:
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(key)
        value = self._resolve(key)
        result = self._coerce(key, value, spec.type)
        if isinstance(result, list):
            return [int(v) for v in result]
        raise TypeError(f"{key} resolved to {type(result).__name__}, expected list")

    async def set(self, key: str, value: object, *, actor: str = "system") -> None:
        if key not in SETTINGS_BY_KEY:
            raise KeyError(key)
        self._overrides[key] = value
        if self._db_repo is not None:
            await self._db_repo.set(key, value)
        for cb in list(self._subscribers):
            cb(key, value)

    async def get_all(self, *, mask_secrets: bool = True) -> dict[str, object]:
        await self._ensure_db_loaded()
        result: dict[str, object] = {}
        for key in self._values:
            value = self._resolve(key)
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
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe
