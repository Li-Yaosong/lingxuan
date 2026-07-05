"""Tests for DefaultPluginHost: registration, dispatch, enable/disable, exception isolation, ctx chaining, cancel."""

from __future__ import annotations

import logging

import pytest

from lingxuan.plugins.host import DefaultPluginHost
from lingxuan.protocols.plugins import HookType, PluginContext


# ── fake plugins ──────────────────────────────────────────────────────────


class FakePlugin:
    """Minimal plugin that subscribes to given hooks during setup."""

    def __init__(
        self,
        name: str,
        version: str = "1.0",
        hooks: list[HookType] | None = None,
        handler_prefix: str = "",
    ) -> None:
        self.name = name
        self.version = version
        self._hooks = hooks or []
        self._handler_prefix = handler_prefix or name
        self.setup_called = False
        self.setup_config: dict | None = None
        self.teardown_called = False
        self.call_log: list[str] = []

    def setup(self, host: object, config: dict, services: object) -> None:
        self.setup_called = True
        self.setup_config = config
        for hook in self._hooks:
            host.subscribe(hook, self._make_handler(hook))

    async def teardown(self) -> None:
        self.teardown_called = True

    def _make_handler(self, hook: HookType):
        prefix = self._handler_prefix

        async def handler(ctx: PluginContext) -> PluginContext:
            self.call_log.append(f"{prefix}:{hook.value}")
            ctx.extra[f"{prefix}_called"] = True
            return ctx

        return handler


class BrokenHandlerPlugin(FakePlugin):
    """Plugin whose handler raises on the first call."""

    _raise_count = 0

    def _make_handler(self, hook: HookType):
        prefix = self._handler_prefix

        async def handler(ctx: PluginContext) -> PluginContext:
            self.call_log.append(f"{prefix}:{hook.value}")
            raise RuntimeError(f"{prefix} boom")

        return handler


class CancelPlugin(FakePlugin):
    """Plugin that sets ctx.cancelled = True."""

    def _make_handler(self, hook: HookType):
        prefix = self._handler_prefix

        async def handler(ctx: PluginContext) -> PluginContext:
            self.call_log.append(f"{prefix}:{hook.value}")
            ctx.cancelled = True
            return ctx

        return handler


class MutatePlugin(FakePlugin):
    """Plugin that mutates ctx.extra with a specific value."""

    def __init__(self, name: str, version: str = "1.0", hooks: list[HookType] | None = None, mutate_value: str = "") -> None:
        super().__init__(name, version, hooks)
        self._mutate_value = mutate_value

    def _make_handler(self, hook: HookType):
        prefix = self._handler_prefix
        val = self._mutate_value

        async def handler(ctx: PluginContext) -> PluginContext:
            self.call_log.append(f"{prefix}:{hook.value}")
            ctx.extra["chain"] = ctx.extra.get("chain", "") + val
            return ctx

        return handler


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def log_sink() -> logging.Logger:
    """A logger that captures messages to a list for assertions."""
    logger = logging.getLogger("test_plugin_host")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.MemoryHandler(capacity=1000)
    logger.addHandler(handler)
    return logger


import logging.handlers  # noqa: E402 — needed for MemoryHandler above


@pytest.fixture
def host() -> DefaultPluginHost:
    return DefaultPluginHost()


# ── tests ─────────────────────────────────────────────────────────────────


class TestRegistration:
    def test_register_and_registry(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("alpha", "0.1", [HookType.on_inbound_message])
        host.register(p)
        info = host.registry()
        assert len(info) == 1
        assert info[0].name == "alpha"
        assert info[0].version == "0.1"
        assert info[0].enabled is True
        assert HookType.on_inbound_message in info[0].hooks

    def test_register_duplicate_raises(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("dup", hooks=[HookType.on_inbound_message])
        host.register(p)
        with pytest.raises(ValueError, match="already registered"):
            host.register(p)

    def test_register_with_config(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("cfg_plugin", hooks=[HookType.on_inbound_message])
        host.register(p, config={"enabled": False, "key": "val"})
        assert p.setup_config == {"enabled": False, "key": "val"}
        info = host.registry()
        assert info[0].enabled is False

    def test_register_default_enabled(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("def_enabled", hooks=[HookType.on_inbound_message])
        host.register(p)
        assert host.registry()[0].enabled is True

    def test_subscribe_outside_setup_raises(self, host: DefaultPluginHost) -> None:
        with pytest.raises(RuntimeError, match="must be called from within plugin.setup"):
            host.subscribe(HookType.on_inbound_message, async_lambda)

    def test_setup_failure_rolls_back_subscriptions(self, host: DefaultPluginHost) -> None:
        class FailPlugin:
            name = "fail"
            version = "1.0"

            def setup(self, host: object, config: dict, services: object) -> None:
                host.subscribe(HookType.on_inbound_message, async_lambda)
                raise RuntimeError("setup failed")

            async def teardown(self) -> None:
                pass

        with pytest.raises(RuntimeError, match="setup failed"):
            host.register(FailPlugin())

        # No subscriptions should remain
        assert all(len(v) == 0 for v in host._subscriptions.values())
        assert "fail" not in host._plugins


async def async_lambda(ctx: PluginContext) -> PluginContext:
    return ctx


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_calls_handler(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("p1", hooks=[HookType.on_inbound_message])
        host.register(p)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.extra.get("p1_called") is True
        assert p.call_log == ["p1:on_inbound_message"]

    @pytest.mark.asyncio
    async def test_dispatch_order(self, host: DefaultPluginHost) -> None:
        """Handlers are called in registration order."""
        p1 = MutatePlugin("first", hooks=[HookType.on_inbound_message], mutate_value="A")
        p2 = MutatePlugin("second", hooks=[HookType.on_inbound_message], mutate_value="B")
        host.register(p1)
        host.register(p2)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.extra["chain"] == "AB"

    @pytest.mark.asyncio
    async def test_dispatch_no_handlers_returns_ctx(self, host: DefaultPluginHost) -> None:
        ctx = PluginContext(hook=HookType.on_config_change)
        result = await host.dispatch(ctx)
        assert result is ctx

    @pytest.mark.asyncio
    async def test_dispatch_only_matching_hook(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("hooked", hooks=[HookType.on_inbound_message])
        host.register(p)
        ctx = PluginContext(hook=HookType.on_after_reply)
        await host.dispatch(ctx)
        assert p.call_log == []  # not called for different hook


class TestEnableDisable:
    @pytest.mark.asyncio
    async def test_disable_skips_handler(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("dis", hooks=[HookType.on_inbound_message])
        host.register(p)
        host.disable("dis")
        ctx = PluginContext(hook=HookType.on_inbound_message)
        await host.dispatch(ctx)
        assert p.call_log == []

    @pytest.mark.asyncio
    async def test_enable_restores_handler(self, host: DefaultPluginHost) -> None:
        p = FakePlugin("toggle", hooks=[HookType.on_inbound_message])
        host.register(p)
        host.disable("toggle")
        host.enable("toggle")
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.extra.get("toggle_called") is True

    @pytest.mark.asyncio
    async def test_disable_preserves_subscriptions(self, host: DefaultPluginHost) -> None:
        """Disabling keeps subscription records; re-enabling restores dispatch."""
        p = FakePlugin("keep", hooks=[HookType.on_inbound_message])
        host.register(p)
        host.disable("keep")
        # Subscription record still exists
        assert len(host._subscriptions[HookType.on_inbound_message]) == 1
        host.enable("keep")
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.extra.get("keep_called") is True

    def test_enable_unknown_raises(self, host: DefaultPluginHost) -> None:
        with pytest.raises(KeyError):
            host.enable("nonexistent")

    def test_disable_unknown_raises(self, host: DefaultPluginHost) -> None:
        with pytest.raises(KeyError):
            host.disable("nonexistent")


class TestExceptionIsolation:
    @pytest.mark.asyncio
    async def test_handler_exception_does_not_affect_others(self, host: DefaultPluginHost) -> None:
        broken = BrokenHandlerPlugin("broken", hooks=[HookType.on_inbound_message])
        healthy = FakePlugin("healthy", hooks=[HookType.on_inbound_message])
        host.register(broken)
        host.register(healthy)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        # healthy handler still ran
        assert result.extra.get("healthy_called") is True
        assert broken.call_log == ["broken:on_inbound_message"]
        assert healthy.call_log == ["healthy:on_inbound_message"]

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_affect_dispatch_return(self, host: DefaultPluginHost) -> None:
        broken = BrokenHandlerPlugin("boom", hooks=[HookType.on_inbound_message])
        host.register(broken)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        # dispatch still returns a valid ctx
        assert isinstance(result, PluginContext)


class TestCtxChaining:
    @pytest.mark.asyncio
    async def test_ctx_modified_by_handler_passed_to_next(self, host: DefaultPluginHost) -> None:
        """Handler A modifies ctx.extra; handler B sees the modification."""
        p1 = MutatePlugin("step1", hooks=[HookType.on_inbound_message], mutate_value="X")
        p2 = MutatePlugin("step2", hooks=[HookType.on_inbound_message], mutate_value="Y")
        host.register(p1)
        host.register(p2)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.extra["chain"] == "XY"


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_stops_dispatch(self, host: DefaultPluginHost) -> None:
        cancel = CancelPlugin("canceler", hooks=[HookType.on_inbound_message])
        after = FakePlugin("after", hooks=[HookType.on_inbound_message])
        host.register(cancel)
        host.register(after)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.cancelled is True
        # "after" should NOT have been called
        assert after.call_log == []

    @pytest.mark.asyncio
    async def test_cancel_on_inbound_semantics(self, host: DefaultPluginHost) -> None:
        """Simulating dialogue.py usage: cancelled ctx means skip message."""
        cancel = CancelPlugin("blocker", hooks=[HookType.on_inbound_message])
        host.register(cancel)
        ctx = PluginContext(hook=HookType.on_inbound_message)
        result = await host.dispatch(ctx)
        assert result.cancelled is True
        # Caller would check ctx.cancelled and return early
