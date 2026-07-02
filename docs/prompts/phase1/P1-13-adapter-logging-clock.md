# P1-13 · adapters/{logging,clock} — 临时 LogSink + SystemClock

## 目标
提供 Phase 1 可用的最小 `LogSink` 实现（桥接 nonebot.logger，Phase 4 再升级为 ring buffer + 订阅）与 `Clock` 的系统实现。

## 前置依赖
- P0-06（LogSink、Clock 接口）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/clock.py`
- 新增 `src/lingxuan/adapters/logging/sink.py`（Phase 1 最小版）

## 详细规格

### adapters/clock.py
`SystemClock(Clock)`：
- `now()` → `datetime.now(timezone.utc)`
- `monotonic()` → `time.monotonic()`
- `async sleep(s)` → `await asyncio.sleep(s)`

### adapters/logging/sink.py（Phase 1 最小版）
`BridgeLogSink(LogSink)`：
- `emit(record)`：转发到 `nonebot.logger`（按 level 调用对应方法），格式 `[logger] msg`。
- `tail(...)`：Phase 1 可返回空列表（尚无 buffer），留 TODO 指向 P4-01。
- `subscribe(cb)`：Phase 1 可返回 no-op 取消函数。

> Phase 4 的 P4-01 会用「ring buffer + 订阅 + 桥接」的完整实现替换本文件；此处仅保证 Core 能拿到一个可用的 LogSink，不阻塞 Phase 1。

## 验收标准
- `SystemClock` 三方法可用；`await sleep(0)` 正常返回。
- `BridgeLogSink.emit` 不抛异常，能输出到 nonebot 日志。

## 测试要求
`tests/adapters/test_clock.py`：`now()` tz-aware；`monotonic()` 单调；`sleep(0)` 可 await。
（LogSink 完整测试在 P4-01。）

## 约束
clock 只用标准库；logging sink 可 import nonebot（Adapter 层允许）。Core 不直接依赖这两个实现，只依赖其 Protocol。
