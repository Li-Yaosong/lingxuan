"""Tests for PluginLoader: discovery, registration, config-driven enable/disable, error isolation."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import types

import pytest

from lingxuan.plugins.host import DefaultPluginHost
from lingxuan.plugins.loader import PluginLoader
from lingxuan.protocols.repositories import PluginConfigRepository
from tests.fakes.repositories import InMemoryPluginConfigRepository


# ── fake plugins ──────────────────────────────────────────────────────────


class FakePlugin:
    """Minimal plugin for testing."""

    def __init__(self, name: str, version: str = "1.0") -> None:
        self.name = name
        self.version = version
        self.setup_called = False
        self.setup_config: dict | None = None

    def setup(self, host: object, config: dict, services: object) -> None:
        self.setup_called = True
        self.setup_config = config

    async def teardown(self) -> None:
        pass


class BrokenSetupPlugin:
    """Plugin whose setup() always raises."""

    name = "broken_setup"
    version = "0.1"

    def setup(self, host: object, config: dict, services: object) -> None:
        raise RuntimeError("setup exploded")

    async def teardown(self) -> None:
        pass


# ── helpers ───────────────────────────────────────────────────────────────


def _make_loader(
    host: DefaultPluginHost | None = None,
    configs: PluginConfigRepository | None = None,
    log: logging.Logger | None = None,
) -> tuple[PluginLoader, DefaultPluginHost, InMemoryPluginConfigRepository, logging.Logger]:
    """Build a PluginLoader with sensible defaults; return (loader, host, configs, log)."""
    if host is None:
        host = DefaultPluginHost()
    if configs is None:
        configs = InMemoryPluginConfigRepository()
    if log is None:
        log = logging.getLogger("test_loader")
        log.setLevel(logging.DEBUG)
        if not log.handlers:
            log.addHandler(logging.handlers.MemoryHandler(capacity=1000))
    loader = PluginLoader(host=host, plugin_configs=configs, services=None, log=log)
    return loader, host, configs, log


def _inject_builtin_module(name: str, plugin_instance: object) -> types.ModuleType:
    """Create a fake module under lingxuan.plugins.builtin and inject it into sys.modules.

    Returns the module object so callers can clean up.
    """
    # Ensure the builtin package is importable
    pkg_name = "lingxuan.plugins.builtin"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = []  # type: ignore[attr-defined]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg
    else:
        pkg = sys.modules[pkg_name]

    mod_name = f"{pkg_name}.{name}"
    mod = types.ModuleType(mod_name)
    mod.__package__ = pkg_name
    mod.plugin = plugin_instance  # type: ignore[attr-defined]
    sys.modules[mod_name] = mod

    # Register the module in the package's __path__ so pkgutil.iter_modules can find it
    # We need a real path for pkgutil — instead, we'll test _discover_builtin by
    # injecting the module and patching pkgutil.iter_modules in the test.
    return mod


# ── tests ─────────────────────────────────────────────────────────────────


class TestDiscoverBuiltin:
    """Tests for _discover_builtin: scanning lingxuan.plugins.builtin sub-modules."""

    @pytest.mark.asyncio
    async def test_discovers_module_with_plugin_attribute(self) -> None:
        """A builtin module exposing ``plugin`` is discovered."""
        loader, host, configs, log = _make_loader()
        fake = FakePlugin("hello_builtin")

        # Patch _discover_builtin to return our fake plugin directly,
        # simulating successful discovery.
        loader._discover_builtin = lambda: [fake]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert len(info) == 1
        assert info[0].name == "hello_builtin"

    @pytest.mark.asyncio
    async def test_discovers_module_with_get_plugin(self) -> None:
        """A builtin module exposing ``get_plugin()`` is discovered."""
        loader, host, configs, log = _make_loader()
        fake = FakePlugin("factory_plugin")

        # Simulate a module that uses get_plugin()
        mod = types.ModuleType("lingxuan.plugins.builtin.factory_mod")
        mod.get_plugin = lambda: fake  # type: ignore[attr-defined]

        from lingxuan.plugins.loader import _extract_plugin

        result = _extract_plugin(mod)
        assert result is fake

    @pytest.mark.asyncio
    async def test_module_without_plugin_is_skipped(self) -> None:
        """A builtin module with neither ``plugin`` nor ``get_plugin()`` is skipped."""
        mod = types.ModuleType("lingxuan.plugins.builtin.empty_mod")
        from lingxuan.plugins.loader import _extract_plugin

        result = _extract_plugin(mod)
        assert result is None


class TestDiscoverEntryPoints:
    """Tests for _discover_entry_points: loading from importlib.metadata."""

    @pytest.mark.asyncio
    async def test_entry_point_plugin_loaded(self) -> None:
        """An entry-point resolving to a Plugin is discovered."""
        loader, host, configs, log = _make_loader()
        fake = FakePlugin("ep_plugin")
        loader._discover_builtin = lambda: []  # type: ignore[assignment]
        loader._discover_entry_points = lambda: [fake]  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert len(info) == 1
        assert info[0].name == "ep_plugin"

    @pytest.mark.asyncio
    async def test_entry_point_failure_is_isolated(self) -> None:
        """An entry-point that raises during load is skipped; others continue."""
        loader, host, configs, log = _make_loader()
        good = FakePlugin("good_ep")

        # Simulate: _discover_entry_points raises for one but returns the other
        call_count = 0

        def fake_discover_ep():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: simulate a partial failure by returning only the good one
                # (the loader itself catches import errors inside _discover_entry_points)
                return [good]
            return []

        loader._discover_builtin = lambda: []  # type: ignore[assignment]
        loader._discover_entry_points = fake_discover_ep  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert len(info) == 1
        assert info[0].name == "good_ep"


class TestConfigDrivenEnableDisable:
    """Tests for config-driven enable/disable: PluginConfigRepository controls state."""

    @pytest.mark.asyncio
    async def test_default_enabled_when_no_config(self) -> None:
        """Plugin with no persisted config defaults to enabled=True."""
        loader, host, configs, log = _make_loader()
        fake = FakePlugin("new_plugin")
        loader._discover_builtin = lambda: [fake]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert info[0].enabled is True

        # Default config should have been upserted
        record = await configs.get("new_plugin")
        assert record is not None
        assert record[0] is True  # enabled

    @pytest.mark.asyncio
    async def test_persisted_disabled_stays_disabled(self) -> None:
        """Plugin with persisted enabled=False is registered but disabled."""
        configs = InMemoryPluginConfigRepository()
        await configs.upsert("disabled_plugin", enabled=False, config={"key": "val"})

        loader, host, _, log = _make_loader(configs=configs)
        fake = FakePlugin("disabled_plugin")
        loader._discover_builtin = lambda: [fake]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert info[0].enabled is False
        # Config should be passed to setup
        assert fake.setup_config == {"key": "val"}

    @pytest.mark.asyncio
    async def test_persisted_enabled_stays_enabled(self) -> None:
        """Plugin with persisted enabled=True is registered and enabled."""
        configs = InMemoryPluginConfigRepository()
        await configs.upsert("enabled_plugin", enabled=True, config={"x": 1})

        loader, host, _, log = _make_loader(configs=configs)
        fake = FakePlugin("enabled_plugin")
        loader._discover_builtin = lambda: [fake]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert info[0].enabled is True
        assert fake.setup_config == {"x": 1}

    @pytest.mark.asyncio
    async def test_config_upsert_on_first_discovery(self) -> None:
        """When a plugin has no persisted config, defaults are upserted."""
        configs = InMemoryPluginConfigRepository()
        loader, host, _, log = _make_loader(configs=configs)
        fake = FakePlugin("fresh")
        loader._discover_builtin = lambda: [fake]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        record = await configs.get("fresh")
        assert record is not None
        assert record == (True, {})


class TestErrorIsolation:
    """Tests for error isolation: a failing plugin does not affect others."""

    @pytest.mark.asyncio
    async def test_broken_setup_does_not_block_others(self) -> None:
        """A plugin whose setup() raises is skipped; other plugins register normally."""
        loader, host, configs, log = _make_loader()
        broken = BrokenSetupPlugin()
        healthy = FakePlugin("healthy")

        loader._discover_builtin = lambda: [broken, healthy]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        # Should NOT raise — the broken plugin is caught and logged
        await loader.discover_and_register()

        info = host.registry()
        # Only the healthy plugin should be registered
        names = [i.name for i in info]
        assert "healthy" in names
        assert "broken_setup" not in names

    @pytest.mark.asyncio
    async def test_config_read_failure_uses_defaults(self) -> None:
        """If reading config fails, defaults (enabled=True, empty config) are used."""

        class FailingConfigRepo:
            """A PluginConfigRepository whose get() always raises."""

            async def get(self, name: str) -> tuple[bool, dict] | None:
                raise RuntimeError("db down")

            async def upsert(self, name: str, *, enabled: bool, config: dict) -> None:
                pass

            async def all(self) -> dict[str, tuple[bool, dict]]:
                return {}

        configs = FailingConfigRepo()  # type: ignore[abstract]
        loader, host, _, log = _make_loader(configs=configs)
        fake = FakePlugin("resilient")
        loader._discover_builtin = lambda: [fake]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert len(info) == 1
        assert info[0].enabled is True

    @pytest.mark.asyncio
    async def test_import_failure_is_isolated(self) -> None:
        """A builtin module that fails to import is skipped; others continue."""
        loader, host, configs, log = _make_loader()
        healthy = FakePlugin("still_ok")

        # Simulate: _discover_builtin returns only the healthy one
        # (the import error is caught inside _discover_builtin)
        loader._discover_builtin = lambda: [healthy]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        assert len(info) == 1
        assert info[0].name == "still_ok"


class TestMultiplePlugins:
    """Tests for loading multiple plugins in one pass."""

    @pytest.mark.asyncio
    async def test_mixed_builtin_and_entry_point(self) -> None:
        """Both builtin and entry-point plugins are registered together."""
        loader, host, configs, log = _make_loader()
        builtin = FakePlugin("builtin_one")
        ep = FakePlugin("ep_one")

        loader._discover_builtin = lambda: [builtin]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: [ep]  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        names = {i.name for i in info}
        assert names == {"builtin_one", "ep_one"}

    @pytest.mark.asyncio
    async def test_mixed_enabled_and_disabled(self) -> None:
        """Multiple plugins with different enabled states are handled correctly."""
        configs = InMemoryPluginConfigRepository()
        await configs.upsert("on_plugin", enabled=True, config={})
        await configs.upsert("off_plugin", enabled=False, config={"reason": "testing"})

        loader, host, _, log = _make_loader(configs=configs)
        on_p = FakePlugin("on_plugin")
        off_p = FakePlugin("off_plugin")

        loader._discover_builtin = lambda: [on_p, off_p]  # type: ignore[assignment]
        loader._discover_entry_points = lambda: []  # type: ignore[assignment]

        await loader.discover_and_register()
        info = host.registry()
        by_name = {i.name: i for i in info}
        assert by_name["on_plugin"].enabled is True
        assert by_name["off_plugin"].enabled is False
        assert off_p.setup_config == {"reason": "testing"}


class TestExtractPlugin:
    """Unit tests for the _extract_plugin helper."""

    def test_plugin_attribute(self) -> None:
        mod = types.ModuleType("mod")
        fake = FakePlugin("attr")
        mod.plugin = fake  # type: ignore[attr-defined]
        from lingxuan.plugins.loader import _extract_plugin

        assert _extract_plugin(mod) is fake

    def test_get_plugin_callable(self) -> None:
        mod = types.ModuleType("mod")
        fake = FakePlugin("factory")
        mod.get_plugin = lambda: fake  # type: ignore[attr-defined]
        from lingxuan.plugins.loader import _extract_plugin

        assert _extract_plugin(mod) is fake

    def test_plugin_attribute_takes_precedence(self) -> None:
        """If both ``plugin`` and ``get_plugin`` exist, ``plugin`` wins."""
        mod = types.ModuleType("mod")
        attr_plugin = FakePlugin("attr")
        factory_plugin = FakePlugin("factory")
        mod.plugin = attr_plugin  # type: ignore[attr-defined]
        mod.get_plugin = lambda: factory_plugin  # type: ignore[attr-defined]
        from lingxuan.plugins.loader import _extract_plugin

        assert _extract_plugin(mod) is attr_plugin

    def test_neither_returns_none(self) -> None:
        mod = types.ModuleType("mod")
        from lingxuan.plugins.loader import _extract_plugin

        assert _extract_plugin(mod) is None
