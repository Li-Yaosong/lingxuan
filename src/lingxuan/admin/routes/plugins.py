"""Plugin management routes: list, enable/disable, config update.

All endpoints are under ``/admin/api/plugins``.
Read operations require readonly+ role; write operations require admin role.
All write operations record audit entries.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from lingxuan.admin.deps import (
    AuditRepoDep,
    PluginConfigRepoDep,
    PluginHostDep,
    RequireAdmin,
    RequireReadonlyOk,
)
from lingxuan.admin.schemas import (
    PluginItem,
    PluginListResponse,
    PluginUpdateRequest,
    PluginUpdateResponse,
)
from lingxuan.protocols.plugins import PluginHost


router = APIRouter(prefix="/plugins", tags=["plugins"])


def _determine_reload_strategy(hooks: list[str]) -> str:
    """Determine config reload strategy based on plugin's subscribed hooks.

    If the plugin subscribes to ``on_config_change``, config updates can be
    applied hot (dispatched immediately).  Otherwise the plugin must be
    re-setup to pick up new config — strategy is ``reload``.
    """
    return "hot" if "on_config_change" in hooks else "reload"


# ---------------------------------------------------------------------------
# GET /plugins — list all plugins with registry + persisted state
# ---------------------------------------------------------------------------


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    host: PluginHostDep,
    plugin_configs: PluginConfigRepoDep,
    user: RequireReadonlyOk,
) -> PluginListResponse:
    """Return all registered plugins with their enabled state, config, and hooks."""
    registry = host.registry()
    persisted = await plugin_configs.all()

    items: list[PluginItem] = []
    for info in registry:
        # Merge persisted config over the registry info
        record = persisted.get(info.name)
        config = record[1] if record else {}
        enabled = info.enabled
        hooks = [h.value for h in info.hooks]

        items.append(PluginItem(
            name=info.name,
            version=info.version,
            enabled=enabled,
            hooks=hooks,
            config=config,
            config_reload_strategy=_determine_reload_strategy(hooks),
        ))

    return PluginListResponse(items=items)


# ---------------------------------------------------------------------------
# PUT /plugins/{name} — enable/disable + config update
# ---------------------------------------------------------------------------


@router.put("/{name}", response_model=PluginUpdateResponse)
async def update_plugin(
    name: str,
    body: PluginUpdateRequest,
    host: PluginHostDep,
    plugin_configs: PluginConfigRepoDep,
    audit: AuditRepoDep,
    user: RequireAdmin,
) -> PluginUpdateResponse:
    """Update a plugin's enabled state and/or config.

    - ``enabled`` toggles the plugin on/off via ``host.enable/disable``.
    - ``config`` updates the persisted config and, if the plugin subscribes
      to ``on_config_change``, dispatches the hook for hot-reload.
      Otherwise the response indicates ``config_reload_strategy: "reload"``.
    - All changes are audited.
    """
    # Verify plugin exists in the registry
    registry = host.registry()
    plugin_info = next((p for p in registry if p.name == name), None)
    if plugin_info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin not found: {name}",
        )

    # Read current persisted state
    record = await plugin_configs.get(name)
    current_enabled = record[0] if record else plugin_info.enabled
    current_config = record[1] if record else {}

    # Apply changes
    new_enabled = body.enabled if body.enabled is not None else current_enabled
    new_config = body.config if body.config is not None else current_config

    # Persist to PluginConfigRepository
    await plugin_configs.upsert(name, enabled=new_enabled, config=new_config)

    # Sync enabled state on the host
    if body.enabled is not None:
        try:
            if body.enabled:
                host.enable(name)
            else:
                host.disable(name)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plugin not found in host: {name}",
            )

    # Dispatch on_config_change if config was updated and plugin subscribes
    hooks = [h.value for h in plugin_info.hooks]
    reload_strategy = _determine_reload_strategy(hooks)

    if body.config is not None and reload_strategy == "hot":
        from lingxuan.protocols.plugins import HookType, PluginContext

        ctx = PluginContext(
            hook=HookType.on_config_change,
            extra={"key": name, "value": new_config},
        )
        await host.dispatch(ctx)

    # Audit
    detail: dict = {"name": name}
    if body.enabled is not None:
        detail["enabled"] = new_enabled
    if body.config is not None:
        detail["config_updated"] = True
    await audit.record(
        actor=user["username"],
        action="plugin.update",
        target=name,
        detail=detail,
        success=True,
    )

    return PluginUpdateResponse(
        name=name,
        enabled=new_enabled,
        config=new_config,
        config_reload_strategy=reload_strategy,
    )
