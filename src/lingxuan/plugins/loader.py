"""PluginLoader: discovers and registers built-in and entry_point plugins.

Security note
~~~~~~~~~~~~~
Plugins run **in-process** with no sandbox.  Only load plugins from trusted
sources.  A plugin whose import or ``setup()`` fails is caught and logged;
the failure does **not** prevent other plugins or the application from
starting.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from typing import TYPE_CHECKING, Any

if sys.version_info >= (3, 12):
    from importlib.metadata import entry_points as _entry_points
else:
    # Python 3.10/11: entry_points(group=...) is available from 3.10+
    from importlib.metadata import entry_points as _entry_points

from lingxuan.protocols.plugins import Plugin

if TYPE_CHECKING:
    from lingxuan.protocols.plugins import PluginHost
    from lingxuan.protocols.repositories import PluginConfigRepository

logger = logging.getLogger(__name__)

# The importlib.metadata entry-point group for third-party lingxuan plugins.
_ENTRY_POINT_GROUP = "lingxuan.plugins"


def _extract_plugin(module: Any) -> Plugin | None:
    """Try to extract a Plugin from a module (``plugin`` attribute or ``get_plugin()``)."""
    candidate = getattr(module, "plugin", None)
    if candidate is not None:
        return candidate  # type: ignore[no-any-return]
    factory = getattr(module, "get_plugin", None)
    if callable(factory):
        return factory()  # type: ignore[no-any-return]
    return None


class PluginLoader:
    """Discover and register plugins with the host.

    Parameters
    ----------
    host:
        The ``PluginHost`` that plugins are registered against.
    plugin_configs:
        Repository for per-plugin ``(enabled, config)`` persistence.
    services:
        Aggregate services object passed to ``plugin.setup()``.
    log:
        Logger instance.
    """

    def __init__(
        self,
        host: PluginHost,
        plugin_configs: PluginConfigRepository,
        services: Any = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._host = host
        self._configs = plugin_configs
        self._services = services
        self._log = log or logger

    # ── public API ────────────────────────────────────────────────────────────

    async def discover_and_register(self) -> None:
        """Scan built-in modules and entry_points, then register each plugin.

        For every discovered plugin:

        1. Read ``(enabled, config)`` from ``plugin_configs``.
           If no record exists, default to ``enabled=True`` with an empty
           config dict, and *upsert* those defaults so the record is persisted.
        2. Call ``host.register(plugin, config=config)``.
        3. Call ``host.enable(name)`` or ``host.disable(name)`` according to
           the persisted ``enabled`` flag.

        A plugin whose import or setup fails is logged and skipped; it does
        **not** prevent other plugins or the application from starting.
        """
        plugins = self._discover_builtin() + self._discover_entry_points()
        for plugin in plugins:
            await self._register_one(plugin)

    # ── discovery ─────────────────────────────────────────────────────────────

    def _discover_builtin(self) -> list[Plugin]:
        """Scan ``lingxuan.plugins.builtin`` sub-modules for plugins."""
        results: list[Plugin] = []
        try:
            import lingxuan.plugins.builtin as _pkg  # noqa: F401 — ensure package is importable
        except Exception:
            self._log.exception("Failed to import lingxuan.plugins.builtin package")
            return results

        package_path = _pkg.__path__
        package_name = _pkg.__name__

        for importer, mod_name, is_pkg in pkgutil.iter_modules(package_path, prefix=f"{package_name}."):
            # Skip sub-packages — only flat modules under builtin/
            if is_pkg:
                continue
            try:
                module = importlib.import_module(mod_name)
            except Exception:
                self._log.exception("Failed to import built-in plugin module: %s", mod_name)
                continue
            try:
                plugin = _extract_plugin(module)
            except Exception:
                self._log.exception("Failed to extract plugin from module: %s", mod_name)
                continue
            if plugin is not None:
                results.append(plugin)
            else:
                self._log.warning("Built-in module %s exposes no 'plugin' or 'get_plugin()'; skipping", mod_name)

        return results

    def _discover_entry_points(self) -> list[Plugin]:
        """Load plugins declared via the ``lingxuan.plugins`` entry-point group."""
        results: list[Plugin] = []
        try:
            eps = _entry_points(group=_ENTRY_POINT_GROUP)
        except Exception:
            self._log.exception("Failed to read entry_points for group '%s'", _ENTRY_POINT_GROUP)
            return results

        for ep in eps:
            try:
                plugin = ep.load()
            except Exception:
                self._log.exception("Failed to load entry-point plugin: %s", ep.name)
                continue
            # The entry-point may point to a Plugin directly or a factory callable
            if callable(plugin) and not hasattr(plugin, "name"):
                try:
                    plugin = plugin()
                except Exception:
                    self._log.exception("Failed to call entry-point factory: %s", ep.name)
                    continue
            if hasattr(plugin, "name") and hasattr(plugin, "version"):
                results.append(plugin)  # type: ignore[arg-type]
            else:
                self._log.warning(
                    "Entry-point '%s' did not resolve to a Plugin (missing name/version); skipping",
                    ep.name,
                )

        return results

    # ── registration ──────────────────────────────────────────────────────────

    async def _register_one(self, plugin: Plugin) -> None:
        """Register a single plugin, respecting persisted enabled/config state."""
        name = plugin.name
        try:
            record = await self._configs.get(name)
        except Exception:
            self._log.exception("Failed to read config for plugin '%s'; using defaults", name)
            record = None

        if record is not None:
            enabled, config = record
        else:
            enabled, config = True, {}
            # Persist the defaults so the admin UI can see them
            try:
                await self._configs.upsert(name, enabled=enabled, config=config)
            except Exception:
                self._log.exception("Failed to upsert default config for plugin '%s'", name)

        try:
            self._host.register(plugin, config=config)
        except Exception:
            self._log.exception("Failed to register plugin '%s'; skipping", name)
            return

        # Sync enabled state after registration
        try:
            if enabled:
                self._host.enable(name)
            else:
                self._host.disable(name)
        except Exception:
            self._log.exception("Failed to sync enabled state for plugin '%s'", name)
