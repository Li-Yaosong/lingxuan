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
    Type-validation failures return 422 for the whole request; all other
    items are processed but only successful results are persisted.
    Audit is recorded once with the list of changed keys; sensitive values
    are never stored in audit detail.
    """
    updates: dict[str, object] = body.root
    results: list[ConfigUpdateResultItem] = []
    succeeded_keys: list[str] = []
    # Track secret keys so we mask them in audit detail
    secret_keys: set[str] = set()

    # Pre-validate all types first; reject the whole request on type errors
    type_errors: list[ConfigUpdateResultItem] = []
    valid_updates: list[tuple[str, object, object]] = []  # (key, coerced, raw)
    for key, value in updates.items():
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            type_errors.append(ConfigUpdateResultItem(
                key=key, success=False, error=f"Unknown config key: {key}",
            ))
            continue
        try:
            coerced = _coerce_value(spec, value)
            valid_updates.append((key, coerced, value))
        except (ValueError, TypeError) as exc:
            type_errors.append(ConfigUpdateResultItem(
                key=key, success=False,
                error=f"Type validation failed: {exc}",
            ))

    if type_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"type_errors": [r.model_dump() for r in type_errors]},
        )

    # All types valid — persist each item
    for key, coerced, _raw in valid_updates:
        spec = SETTINGS_BY_KEY[key]

        try:
            await config.set(key, coerced, actor=user["username"])
        except Exception as exc:
            results.append(ConfigUpdateResultItem(
                key=key, success=False, error=f"Write failed: {exc}",
            ))
            continue

        succeeded_keys.append(key)
        # Echo masked value for secret items; raw for non-secret
        display_value: str | None = None
        if spec.is_secret:
            display_value = mask_secret(str(coerced))
        results.append(ConfigUpdateResultItem(
            key=key,
            success=True,
            needs_restart=not spec.hot_reloadable,
            value=display_value if spec.is_secret else coerced,
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
