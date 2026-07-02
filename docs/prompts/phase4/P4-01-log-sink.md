# P4-01 · adapters/logging/sink.py — 结构化 LogSink（ring buffer + 订阅）

## 目标
把 Phase 1 的最小 LogSink 升级为完整实现：进程内 ring buffer（保存最近 N 条结构化日志）+ 订阅推送 + 桥接现有 nonebot/loguru 日志，供管理端历史查询与 WS 实时推送。

## 前置依赖
- P0-06（LogSink/LogRecord 接口）、P1-13（被替换的最小版）。

## 需创建或修改的文件
- 修改 `src/lingxuan/adapters/logging/sink.py`

## 详细规格
`class RingBufferLogSink(LogSink)`：
- 构造：`capacity=2000`（可配）；内部 `collections.deque(maxlen=capacity)`；订阅者集合。
- `emit(record)`：入队；同步通知所有订阅回调（回调异常需被捕获，不影响主流程）。也向 nonebot/loguru 输出（桥接，保留控制台日志）。
- `tail(limit, level, keyword)`：从 buffer 倒序过滤：level 精确匹配（或 ≥ 该级别，二选一并注明）、keyword 子串（msg + logger）；返回最多 limit 条。
- `subscribe(cb)`：加入订阅集合，返回取消函数。
- 线程/异步安全：单进程 async 内同步操作即可；若 loguru 从其它线程回调，需加锁或用线程安全结构。

**接入 loguru/nonebot**：添加一个 loguru sink（`logger.add(sink_func)`），把 loguru 记录转成 `LogRecord`（ts/level/logger/msg/extra）后 `emit`。确保灵轩自身日志（Core 通过注入的 LogSink 打的日志、以及 nonebot/loguru 的日志）都进入 buffer。

## 验收标准
- emit 后 tail 能取到；超过 capacity 丢弃最旧。
- level/keyword 过滤正确。
- subscribe 收到后续 emit 的记录；取消后不再收到。
- loguru 输出能被捕获进 buffer。

## 测试要求
`tests/adapters/logging/test_sink.py`：emit/tail/过滤/容量上限/订阅与取消；loguru 桥接可用 monkeypatch 或实际 add sink 验证。

## 约束
Adapter 层；订阅回调异常隔离；不阻塞 emit。
