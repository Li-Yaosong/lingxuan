"""Plugin hook contract: HookType, PluginContext, PluginInfo, Plugin & PluginHost protocols."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from lingxuan.protocols.messaging import InboundMessage, ReplyPlan


class HookType(str, Enum):
    on_inbound_message = "on_inbound_message"
    on_before_reply = "on_before_reply"
    on_after_reply = "on_after_reply"
    on_memory_extract = "on_memory_extract"
    on_config_change = "on_config_change"


@dataclass
class PluginContext:
    hook: HookType
    inbound: InboundMessage | None = None
    reply_plan: ReplyPlan | None = None
    extra: dict = field(default_factory=dict)
    cancelled: bool = False


@dataclass
class PluginInfo:
    name: str
    version: str
    enabled: bool
    hooks: list[HookType]


HookHandler = Callable[[PluginContext], Awaitable[PluginContext]]


class Plugin(Protocol):
    name: str
    version: str

    def setup(self, host: PluginHost, config: dict, services: object) -> None: ...

    async def teardown(self) -> None: ...


class PluginHost(Protocol):
    def register(self, plugin: Plugin, *, config: dict) -> None: ...

    def subscribe(self, hook: HookType, handler: HookHandler) -> None: ...

    def enable(self, name: str) -> None: ...

    def disable(self, name: str) -> None: ...

    def registry(self) -> list[PluginInfo]: ...

    async def dispatch(self, ctx: PluginContext) -> PluginContext: ...
