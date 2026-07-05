"""group_entities built-in plugin: group entity learning via on_inbound_message hook.

Migrated from MVP ``group_entities.py``.  Subscribes to ``on_inbound_message``
and performs entity learning (nickname sync, introduction detection, rule
extraction) for group messages.  All writes go through PluginServices
(Repository / UserMemoryService); no direct file IO.

Can be enabled/disabled via the admin panel.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from lingxuan.protocols.plugins import HookType, PluginContext

if TYPE_CHECKING:
    from lingxuan.plugins.services import PluginServices
    from lingxuan.protocols.plugins import PluginHost

# Same regex as MVP group_entities.py / core/user_memory.py
_INTRO_NAME = re.compile(
    r"(?:这位就是|这就是|他(?:就)?是|她(?:就)?是|叫)([^，。！？\s]{1,12})"
)


class GroupEntitiesPlugin:
    """Built-in plugin that learns entity relationships from group messages."""

    name: str = "group_entities"
    version: str = "1.0"

    def __init__(self) -> None:
        self._services: PluginServices | None = None
        self._host: PluginHost | None = None
        # Configurable keywords for bot-name detection (default: "小堞宝")
        self._bot_name_keywords: list[str] = ["小堞宝"]

    # ── Plugin protocol ──────────────────────────────────────────────────

    def setup(self, host: PluginHost, config: dict, services: object) -> None:
        self._host = host
        self._services = services  # type: ignore[assignment]
        # Allow config override for bot-name keywords
        if config and "bot_name_keywords" in config:
            self._bot_name_keywords = list(config["bot_name_keywords"])
        host.subscribe(HookType.on_inbound_message, self.on_inbound)

    async def teardown(self) -> None:
        self._services = None
        self._host = None

    # ── Hook handler ─────────────────────────────────────────────────────

    async def on_inbound(self, ctx: PluginContext) -> PluginContext:
        """Handle on_inbound_message: learn entities from group messages."""
        inbound = ctx.inbound
        if inbound is None:
            return ctx

        # Only process group messages
        if inbound.session_id.kind != "group":
            return ctx

        # Skip bot's own messages and empty text
        if inbound.actor.is_self:
            return ctx
        if not inbound.text.strip():
            return ctx

        assert self._services is not None  # guaranteed after setup
        svc = self._services
        um = svc.user_memory
        group_id = inbound.group_id
        if group_id is None:
            return ctx

        user_id = inbound.actor.user_id
        nickname = inbound.actor.nickname or str(user_id)
        text = inbound.text
        session_id = inbound.session_id

        # 1. Sync speaker nickname to graph
        await um.sync_entity_to_graph(nickname, user_id, session_id.as_str())

        # 2. Per @'d user: bot-name detection + introduction extraction
        at_user_ids = inbound.at_user_ids
        for uid in at_user_ids:
            # Bot-name keyword detection (e.g. "小堞宝")
            for keyword in self._bot_name_keywords:
                if keyword in text:
                    await um.index_name(keyword, uid)
                    # merge_entity via UserMemoryService
                    await um.merge_entity(session_id, keyword, uid)
            # Introduction pattern: "这位就是X" / "他就是Y" etc.
            match = _INTRO_NAME.search(text)
            if match:
                name = match.group(1).strip().strip("的")
                if name and len(name) <= 12:
                    await um.index_name(name, uid)
                    await um.merge_entity(session_id, name, uid)

        # 3. Self-introduction fallback: "就是X" without @
        if "就是" in text and not at_user_ids:
            match = _INTRO_NAME.search(text)
            if match:
                name = match.group(1).strip()
                if name and len(name) <= 12:
                    await um.index_name(name, user_id)
                    await um.merge_entity(session_id, name, user_id)

        # 4. Rule extraction (name correction, call-me, social edges, cognition)
        await um.apply_rule_extraction(
            user_id,
            text,
            nickname=nickname,
            group_id=group_id,
            at_user_ids=at_user_ids,
            session_id=session_id.as_str(),
        )

        return ctx


plugin = GroupEntitiesPlugin()
