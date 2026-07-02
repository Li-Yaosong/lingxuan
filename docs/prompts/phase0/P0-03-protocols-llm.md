# P0-03 · protocols/llm.py — LLMProvider 接口

## 目标
定义 LLM 调用抽象接口与聊天消息类型。纯接口，无实现。

## 前置依赖
- P0-01。

## 需创建或修改的文件
- 新增 `src/lingxuan/protocols/llm.py`

## 详细规格
仅依赖标准库。定义：

```python
@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

class LLMProvider(Protocol):
    async def chat(
        self, messages: list[ChatMessage], *,
        max_tokens: int = 1024, temperature: float = 0.7, timeout: float = 30.0,
    ) -> str: ...

    def chat_stream(
        self, messages: list[ChatMessage], *,
        max_tokens: int = 1024, temperature: float = 0.7,
    ) -> AsyncIterator[str]: ...

    async def judge(
        self, prompt: str, *, timeout: float = 5.0, default: bool = False,
    ) -> bool: ...
```

语义对齐 MVP：
- `chat` = 现 `call_llm_raw`/`chat` 的非流式生成。
- `chat_stream` = 现 `chat_stream`/`chat_in_group_stream` 的逐 token 产出（返回异步迭代器；注意签名不是 `async def` 而是返回 `AsyncIterator[str]` 的普通方法，便于 `async for`）。
- `judge` = 现 `should_reply_in_group`：输入完整 prompt，内部让模型输出 yes/no，解析为 bool；超时或异常返回 `default`。
- prompt 拼装**不在** provider 内（在 core/prompting）。

## 验收标准
- 可 import；无 openai 依赖。
- `mypy` 通过。

## 测试要求
无需运行时测试（纯接口）。可加一个 `tests/protocols/test_llm.py` 断言 `ChatMessage` 可构造。

## 约束
不 import `openai`；不写实现。
