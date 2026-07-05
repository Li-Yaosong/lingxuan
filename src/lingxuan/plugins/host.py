"""DefaultPluginHost: plugin registry, hook subscription, enable/disable, dispatch with exception isolation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lingxuan.protocols.plugins import (
    HookHandler,
    HookType,
    Plugin,
    PluginContext,
    PluginInfo,
)

logger = logging.getLogger(__name__)


@dataclass
class PluginRecord:
    """Internal bookkeeping for a registered plugin."""

    plugin: Plugin
    enabled: bool
    hooks: list[HookType] = field(default_factory=list)
    config: dict = field(default_factory=dict)


class DefaultPluginHost:
    """Concrete PluginHost with registry, subscription, enable/disable, and safe dispatch."""

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or logger
        self._plugins: dict[str, PluginRecord] = {}
        self._subscriptions: dict[HookType, list[tuple[str, HookHandler]]] = {
            h: [] for h in HookType
        }
        self._setting_up: str | None = None

    # ── registration ──────────────────────────────────────────────────────

    def register(self, plugin: Plugin, *, config: dict | None = None) -> None:
        """Register a plugin, call its setup(), and record subscriptions.

        ``plugin.setup()`` may call ``self.subscribe()``; the host tracks which
        plugin is currently being set up so that subscriptions are correctly
        attributed.
        """
        name = plugin.name
        if name in self._plugins:
            raise ValueError(f"Plugin already registered: {name}")

        cfg = config or {}
        self._setting_up = name

        try:
            plugin.setup(self, cfg, services=None)
        except Exception:
            # Roll back any subscriptions added during the failed setup
            for hook in HookType:
                self._subscriptions[hook] = [
                    (n, h) for n, h in self._subscriptions[hook] if n != name
                ]
            raise
        finally:
            self._setting_up = None

        # Determine initial enabled state
        enabled = cfg.get("enabled", True)

        record = PluginRecord(plugin=plugin, enabled=enabled, config=cfg)
        # Collect which hooks this plugin subscribed to
        for hook_type, handlers in self._subscriptions.items():
            for handler_name, _ in handlers:
                if handler_name == name and hook_type not in record.hooks:
                    record.hooks.append(hook_type)

        self._plugins[name] = record
        self._log.info("Plugin registered: %s v%s (enabled=%s)", name, plugin.version, enabled)

    # ── subscription ──────────────────────────────────────────────────────

    def subscribe(self, hook: HookType, handler: HookHandler) -> None:
        """Subscribe a handler for a hook. Must be called during plugin setup()."""
        if self._setting_up is None:
            raise RuntimeError("subscribe() must be called from within plugin.setup()")
        self._subscriptions[hook].append((self._setting_up, handler))

    # ── enable / disable ──────────────────────────────────────────────────

    def enable(self, name: str) -> None:
        """Enable a registered plugin so its handlers participate in dispatch."""
        record = self._plugins.get(name)
        if record is None:
            raise KeyError(f"Plugin not found: {name}")
        record.enabled = True
        self._log.info("Plugin enabled: %s", name)

    def disable(self, name: str) -> None:
        """Disable a registered plugin; its handlers are skipped during dispatch."""
        record = self._plugins.get(name)
        if record is None:
            raise KeyError(f"Plugin not found: {name}")
        record.enabled = False
        self._log.info("Plugin disabled: %s", name)

    # ── registry ──────────────────────────────────────────────────────────

    def registry(self) -> list[PluginInfo]:
        """Return info for all registered plugins."""
        result: list[PluginInfo] = []
        for record in self._plugins.values():
            result.append(
                PluginInfo(
                    name=record.plugin.name,
                    version=record.plugin.version,
                    enabled=record.enabled,
                    hooks=list(record.hooks),
                )
            )
        return result

    # ── dispatch ──────────────────────────────────────────────────────────

    async def dispatch(self, ctx: PluginContext) -> PluginContext:
        """Dispatch *ctx* to all enabled handlers for ``ctx.hook``.

        - Handlers are awaited sequentially in registration order.
        - A handler returning a ctx replaces the ctx for subsequent handlers.
        - If a handler raises, the exception is logged and does **not** affect
          other handlers or the return value.
        - If ``ctx.cancelled`` becomes ``True``, dispatch stops early.
        """
        handlers = self._subscriptions.get(ctx.hook, [])
        for plugin_name, handler in handlers:
            record = self._plugins.get(plugin_name)
            if record is None or not record.enabled:
                continue
            try:
                result = await handler(ctx)
                if result is not None:
                    ctx = result
            except Exception:
                self._log.exception(
                    "Plugin handler error: plugin=%s hook=%s", plugin_name, ctx.hook.value
                )
            if ctx.cancelled:
                break
        return ctx
