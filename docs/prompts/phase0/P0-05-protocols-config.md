# P0-05 · protocols/config.py — ConfigProvider 接口

## 目标
定义运行时配置读/写/订阅抽象接口。纯接口。

## 前置依赖
- P0-01。

## 需创建或修改的文件
- 新增 `src/lingxuan/protocols/config.py`

## 详细规格
仅依赖标准库。定义：

```python
Unsubscribe = Callable[[], None]
ConfigChangeCallback = Callable[[str, object], None]

class ConfigProvider(Protocol):
    def get(self, key: str) -> object: ...
    def get_str(self, key: str) -> str: ...
    def get_int(self, key: str) -> int: ...
    def get_float(self, key: str) -> float: ...
    def get_bool(self, key: str) -> bool: ...
    def get_int_list(self, key: str) -> list[int]: ...            # 如 BOT_ADMINS
    async def set(self, key: str, value: object, *, actor: str = "system") -> None: ...
    async def get_all(self, *, mask_secrets: bool = True) -> dict[str, object]: ...
    def subscribe(self, callback: ConfigChangeCallback) -> Unsubscribe: ...
```

约定（供实现遵守，写进 docstring）：
- 解析优先级：DB `settings` > `.env` > `settings_defaults.py` 默认值。
- key 用**大写下划线**风格，与 `.env` 变量名一致（如 `BOT_NAME`、`ENABLE_GROUP_OBSERVE`）。
- `set` 需触发 `subscribe` 回调并（由实现）落库 + 审计。
- `get_all(mask_secrets=True)` 对敏感项（见 settings_defaults 的 is_secret）脱敏。
- 未知 key `get` 抛 `KeyError`。

## 验收标准
- 可 import；无第三方依赖。
- `mypy` 通过。

## 测试要求
无需运行时测试。

## 约束
只定义接口。
