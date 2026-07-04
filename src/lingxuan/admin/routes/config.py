"""Config routes: read (masked), schema, batch update with audit + hot-reload.

All endpoints are under ``/admin/api/config``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status

from lingxuan.admin.deps import AuditRepoDep, ConfigDep, RequireAdmin, RequireReadonlyOk
from lingxuan.admin.schemas import (
    ConfigSchemaItem,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    ConfigUpdateResultItem,
)
from lingxuan.settings_defaults import SETTINGS, SETTINGS_BY_KEY, mask_secret


router = APIRouter(prefix="/config", tags=["config"])


# ---------------------------------------------------------------------------
# GET /config — all values, secrets masked
# ---------------------------------------------------------------------------


@router.get("", response_model=dict[str, Any])
async def get_config(
    config: ConfigDep,
    user: RequireReadonlyOk,
) -> dict[str, Any]:
    """Return all config values with sensitive items masked."""
    return await config.get_all(mask_secrets=True)


# ---------------------------------------------------------------------------
# GET /config/schema — setting specifications
# ---------------------------------------------------------------------------


@router.get("/schema", response_model=list[ConfigSchemaItem])
async def get_config_schema(
    user: RequireReadonlyOk,
) -> list[ConfigSchemaItem]:
    """Return the schema (type, default, group, flags) for every setting."""
    return [
        ConfigSchemaItem(
            key=s.key,
            type=s.type,
            default=s.default,
            group=s.group,
            is_secret=s.is_secret,
            hot_reloadable=s.hot_reloadable,
            description=s.description,
        )
        for s in SETTINGS
    ]


# ---------------------------------------------------------------------------
# PUT /config — batch update with validation, audit, hot-reload
# ---------------------------------------------------------------------------


@router.put("", response_model=ConfigUpdateResponse)
async def update_config(
    body: ConfigUpdateRequest,
    config: ConfigDep,
    audit_repo: AuditRepoDep,
    user: RequireAdmin,
) -> ConfigUpdateResponse:
    """Batch-update config values.

    Per-item validation and write; each item is committed independently
    (best-effort strategy: one failure does not block other items).
    Audit is recorded once with the list of changed keys; sensitive values
    are never stored in audit detail.
    """
    updates: dict[str, object] = body.root
    results: list[ConfigUpdateResultItem] = []
    succeeded_keys: list[str] = []
    # Track secret keys so we mask them in audit detail
    secret_keys: set[str] = set()

    for key, value in updates.items():
        # 1. Key must exist in SETTINGS
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            results.append(ConfigUpdateResultItem(
                key=key, success=False, error=f"Unknown config key: {key}",
            ))
            continue

        # 2. Type coercion / validation
        try:
            coerced = _coerce_value(spec, value)
        except (ValueError, TypeError) as exc:
            results.append(ConfigUpdateResultItem(
                key=key, success=False,
                error=f"Type validation failed: {exc}",
            ))
            continue

        # 3. Persist via ConfigProvider.set (writes DB + triggers subscribers)
        try:
            await config.set(key, coerced, actor=user["username"])
        except Exception as exc:
            results.append(ConfigUpdateResultItem(
                key=key, success=False, error=f"Write failed: {exc}",
            ))
            continue

        succeeded_keys.append(key)
        results.append(ConfigUpdateResultItem(
            key=key,
            success=True,
            needs_restart=not spec.hot_reloadable,
        ))
        if spec.is_secret:
            secret_keys.add(key)

    # 4. Audit: one record for the whole batch
    if succeeded_keys:
        # Never include sensitive values in audit detail
        safe_detail: dict[str, Any] = {"keys": succeeded_keys}
        # Mark which keys were secret so auditors know values exist but are redacted
        if secret_keys:
            safe_detail["secret_keys"] = sorted(secret_keys)
        await audit_repo.record(
            actor=user["username"],
            action="config.update",
            target="batch",
            detail=safe_detail,
            success=True,
        )

    return ConfigUpdateResponse(results=results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_value(spec: Any, value: object) -> object:
    """Coerce and validate *value* according to the SettingSpec type.

    Raises ValueError/TypeError on incompatible values.
    """
    from lingxuan.settings_defaults import parse_value

    target = spec.type

    if target == "str":
        return str(value)
    if target == "int":
        if isinstance(value, bool):
            raise ValueError(f"Expected int, got bool")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value.strip())
        raise TypeError(f"Cannot coerce {type(value).__name__} to int")
    if target == "float":
        if isinstance(value, bool):
            raise ValueError("Expected float, got bool")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return float(value.strip())
        raise TypeError(f"Cannot coerce {type(value).__name__} to float")
    if target == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        raise TypeError(f"Cannot coerce {type(value).__name__} to bool")
    if target == "int_list":
        if isinstance(value, list):
            return [int(v) for v in value]
        if isinstance(value, str):
            return parse_value(spec, value)
        raise TypeError(f"Cannot coerce {type(value).__name__} to int_list")

    raise ValueError(f"Unknown spec type: {target}")
