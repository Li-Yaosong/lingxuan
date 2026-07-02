"""Tests for lingxuan.protocols.llm — ChatMessage construction."""

from lingxuan.protocols.llm import ChatMessage


class TestChatMessageConstruction:
    def test_system_message(self) -> None:
        msg = ChatMessage(role="system", content="You are a bot.")
        assert msg.role == "system"
        assert msg.content == "You are a bot."

    def test_user_message(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_assistant_message(self) -> None:
        msg = ChatMessage(role="assistant", content="Hi there!")
        assert msg.role == "assistant"
        assert msg.content == "Hi there!"
