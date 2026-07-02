# P0-02 · protocols/messaging.py — 消息领域类型与 MessageTransport

## 目标
定义 Core 与外界交互的消息领域类型，以及消息收发抽象接口。**纯类型/接口定义，无实现**。

## 前置依赖
- P0-01（包结构已就绪）。

## 需创建或修改的文件
- 新增 `src/lingxuan/protocols/messaging.py`

## 详细规格
只依赖标准库（`dataclasses`、`datetime`、`typing`、`collections.abc`）。定义以下类型（字段与语义严格按此）：

```python
@dataclass(frozen=True)
class SessionId:
    kind: Literal["private", "group"]
    peer_id: int                       # private=user_id, group=group_id
    def as_str(self) -> str: ...       # "private_{id}" / "group_{id}"，兼容旧文件名
    @classmethod
    def parse(cls, s: str) -> "SessionId": ...  # 反向解析

@dataclass(frozen=True)
class Actor:
    user_id: int
    nickname: str = ""
    is_admin: bool = False
    is_self: bool = False

@dataclass
class InboundMessage:
    session_id: SessionId
    actor: Actor
    text: str                          # 已去 @ 段的纯文本
    raw_text: str = ""
    at_bot: bool = False
    reply_to_bot: bool = False
    at_user_ids: list[int] = field(default_factory=list)
    group_id: int | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    command: tuple[str, list[str]] | None = None

@dataclass(frozen=True)
class ReplyTarget:
    session_id: SessionId
    at_user_id: int | None = None

@dataclass
class OutboundChunk:
    text: str
    at_user_id: int | None = None      # 仅首段可能非空
    delay_before: float = 0.0

@dataclass
class OutboundMessage:
    target: ReplyTarget
    chunks: list[OutboundChunk]

@dataclass
class ReplyPlan:
    should_reply: bool
    reason: str = ""                   # shortcircuit / judge_yes / cooldown / disabled ...
    stream: bool = True
    observation_text: str = ""
    primary_user_id: int | None = None

@dataclass
class ObservationEntry:               # 对齐现有 group_observer.ObservationEntry
    user_id: int
    nickname: str
    text: str
    at_bot: bool = False
    reply_to_bot: bool = False
    at_user_ids: list[int] = field(default_factory=list)
    is_bot: bool = False
    ts: float = field(default_factory=time.time)

@dataclass
class ObservationContext:
    session_id: SessionId
    group_id: int
    buffer: list[ObservationEntry]
    last_bot_reply_at: float = 0.0
    cooldown_until: float = 0.0
    self_id: int = 0
```

MessageTransport 接口：
```python
InboundHandler = Callable[[InboundMessage], Awaitable[None]]

class MessageTransport(Protocol):
    async def send(self, out: OutboundMessage) -> None: ...
    async def send_stream(self, target: ReplyTarget, chunks: AsyncIterator[OutboundChunk]) -> str: ...
    def start(self, on_inbound: InboundHandler) -> None: ...
    async def resolve_self_id(self) -> int: ...
```

## 验收标准
- 模块可被 `import`，`SessionId.parse(SessionId(...).as_str())` 往返一致。
- 无对 nonebot/openai/sqlalchemy 的任何 import。
- `mypy` 通过。

## 测试要求
`tests/protocols/test_messaging.py`：测 `SessionId.as_str/parse` 往返（private 与 group 两种）。

## 约束
仅定义类型与 Protocol，不写任何业务逻辑或 IO。
