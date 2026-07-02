# P0-09 · 测试脚手架与 Fakes

## 目标
建立 pytest 基础设施与一组可复用的 in-memory / fake 实现，供 Core 单测（无 IO）使用。

## 前置依赖
- P0-02~P0-06（各 Protocol 已定义）。

## 需创建或修改的文件
- 新增 `pytest.ini` 或在 `pyproject.toml` 增 `[tool.pytest.ini_options]`（启用 `asyncio_mode = "auto"`）。
- 新增 `tests/conftest.py`
- 新增 `tests/fakes/__init__.py`
- 新增 `tests/fakes/clock.py`、`tests/fakes/llm.py`、`tests/fakes/config.py`、`tests/fakes/transport.py`、`tests/fakes/repositories.py`、`tests/fakes/logsink.py`

## 详细规格
实现以下 fakes（实现对应 Protocol，行为可控、无真实 IO）：

- `FakeClock(Clock)`：可手动设置/推进时间；`now()` 返回受控 UTC 时间；`monotonic()` 返回受控浮点；`sleep()` 记录被请求的秒数并立即返回（或按需推进虚拟单调时钟），**不真的等待**。
- `FakeLLMProvider(LLMProvider)`：可预设 `chat` 返回值/序列、`chat_stream` 的 token 列表、`judge` 的布尔序列；记录收到的 messages/prompt 以供断言。
- `FakeConfigProvider(ConfigProvider)`：用 dict 初始化（默认值取自 `settings_defaults`）；`get_*` 从 dict 取；`set` 更新并触发订阅回调；`get_all` 支持脱敏。
- `FakeTransport(MessageTransport)`：`send`/`send_stream` 把发出的 chunk 记录到列表；`start` 存下 handler 以便测试注入 inbound；`resolve_self_id` 返回预设值。
- In-memory repositories：`InMemorySessionRepository`、`InMemoryUserProfileRepository`、`InMemorySocialGraphRepository`、`InMemoryConfigRepository`、`InMemoryAuditRepository`、`InMemoryPluginConfigRepository`、`InMemoryAdminUserRepository`，实现各自 Protocol，用普通 dict/list 存储，**精确复现语义**（如 fact 软删除、边四元组去重、trim_to_last 删除最旧）。
- `FakeLogSink(LogSink)`：把 record 收集到列表；`tail` 支持过滤；`subscribe` 存回调。

`conftest.py` 提供 pytest fixtures 便捷获取上述 fakes。

## 验收标准
- `pytest` 能收集并运行；fakes 自身的小测试通过。
- fakes 只依赖 `protocols/` 与标准库。

## 测试要求
`tests/fakes/test_fakes.py`：
- `FakeClock` 推进后 `now/monotonic` 反映变化。
- `InMemorySessionRepository` append/load/trim 行为正确（trim 删最旧）。
- `InMemoryUserProfileRepository` fact 软删除超限（>30）按 learned_at 保留最新。
- `InMemorySocialGraphRepository` 相同四元组 `add_edge` 第二次返回 False。

## 约束
fakes 属于测试代码，放 `tests/`，不进 `src/`。语义要与真实实现（Phase 2）保持一致，未来会用同一套契约测试跑真实 SQLite 实现。
