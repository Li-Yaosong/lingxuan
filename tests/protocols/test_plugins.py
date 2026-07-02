"""Tests for lingxuan.protocols.plugins — HookType values and PluginContext construction."""

from lingxuan.protocols.plugins import HookType, PluginContext


class TestHookType:
    def test_on_inbound_message_value(self) -> None:
        assert HookType.on_inbound_message.value == "on_inbound_message"

    def test_all_hook_values(self) -> None:
        expected = [
            "on_inbound_message",
            "on_before_reply",
            "on_after_reply",
            "on_memory_extract",
            "on_config_change",
        ]
        assert [h.value for h in HookType] == expected


class TestPluginContext:
    def test_default_construction(self) -> None:
        ctx = PluginContext(hook=HookType.on_inbound_message)
        assert ctx.hook is HookType.on_inbound_message
        assert ctx.inbound is None
        assert ctx.reply_plan is None
        assert ctx.extra == {}
        assert ctx.cancelled is False

    def test_with_reply_plan(self) -> None:
        from lingxuan.protocols.messaging import ReplyPlan

        plan = ReplyPlan(should_reply=True, reason="judge_yes")
        ctx = PluginContext(hook=HookType.on_before_reply, reply_plan=plan)
        assert ctx.reply_plan is plan
        assert ctx.reply_plan.should_reply is True

    def test_cancel_flag(self) -> None:
        ctx = PluginContext(hook=HookType.on_inbound_message, cancelled=True)
        assert ctx.cancelled is True

    def test_extra_payload(self) -> None:
        ctx = PluginContext(hook=HookType.on_config_change, extra={"key": "BOT_NAME"})
        assert ctx.extra["key"] == "BOT_NAME"
