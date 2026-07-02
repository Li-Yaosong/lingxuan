# P0-06 · protocols/{plugins,logging,clock}.py — 插件/日志/时钟接口

## 目标
定义插件 Hook 契约、结构化日志接收器、可测试时钟三组抽象接口。纯接口。

## 前置依赖
- P0-01、P0-02（PluginContext 引用消息领域类型）。

## 需创建或修改的文件
- 新增 `src/lingxuan/protocols/plugins.py`
- 新增 `src/lingxuan/protocols/logging.py`
- 新增 `src/lingxuan/protocols/clock.py`

## 详细规格

### plugins.py
```python
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
    extra: dict = field(default_factory=dict)     # Hook 相关的其它载荷（如 memory 候选、config 变更）
    cancelled: bool = False                        # on_inbound 可标记忽略该消息

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
    def setup(self, host: "PluginHost", config: dict, services: object) -> None: ...
    async def teardown(self) -> None: ...

class PluginHost(Protocol):
    def register(self, plugin: Plugin, *, config: dict) -> None: ...
    def subscribe(self, hook: HookType, handler: HookHandler) -> None: ...
    def enable(self, name: str) -> None: ...
    def disable(self, name: str) -> None: ...
    def registry(self) -> list[PluginInfo]: ...
    async def dispatch(self, ctx: PluginContext) -> PluginContext: ...
```
注：`services` 用 `object`（Phase 5 会传一个聚合了各 Service/Repository 的容器；这里不强类型耦合）。

### logging.py
```python
@dataclass
class LogRecord:
    ts: datetime
    level: str            # DEBUG/INFO/WARNING/ERROR
    logger: str
    msg: str
    extra: dict = field(default_factory=dict)

class LogSink(Protocol):
    def emit(self, record: LogRecord) -> None: ...
    def tail(self, *, limit: int = 200, level: str | None = None, keyword: str = "") -> list[LogRecord]: ...
    def subscribe(self, callback: Callable[[LogRecord], None]) -> Callable[[], None]: ...
```

### clock.py
```python
class Clock(Protocol):
    def now(self) -> datetime: ...            # tz-aware UTC
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...
```

## 验收标准
- 三个模块可 import；无第三方依赖。
- `mypy` 通过。

## 测试要求
`tests/protocols/test_plugins.py`：`HookType.on_inbound_message.value == "on_inbound_message"`；`PluginContext` 可构造。

## 约束
只定义接口/枚举/数据类。
