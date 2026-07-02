# P1-04 · core/reply_planner.py — 分段/节奏纯策略

## 目标
把 MVP `message_chunk.py` 的**分段与节奏算法**迁为 Core 纯逻辑，产出 `OutboundChunk` 序列（含段间延迟），**不涉及发送**（发送在 onebot transport）。

## 前置依赖
- P0-02（OutboundChunk 等）、P0-05（ConfigProvider）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/reply_planner.py`

## 详细规格
类 `ReplyPlanner`，构造注入 `config: ConfigProvider`，读取 `GROUP_MSG_CHUNK_MAX/MIN/LIMIT`、`GROUP_CHUNK_DELAY_MIN/MAX`、`ENABLE_STREAM_CHUNK`。

迁移以下算法（与 MVP 逐字节对齐行为）：

1. `split_chunks(text, *, max_len, min_len, limit) -> list[str]`：
   - 按句末标点/换行切句（复用 MVP `_SENTENCE_END` 正则）。
   - 超长句硬切 `max_len`；短于 `min_len` 的片段并入前段（不超 `max_len`）；超过 `limit` 段时保留前 `limit-1` 段 + 尾部合并截断。

2. 流式增量 `take_emit_chunk(buffer, *, max_len, min_len) -> tuple[str | None, str]`：
   - 优先句末且 `>= min_len` 处切；否则 buffer ≥ max_len 硬切；否则返回 `(None, buffer)`。

3. `plan_static(text) -> list[OutboundChunk]`：对整段文本调用 `split_chunks`，生成带 `delay_before` 的 chunk 序列——首段 `delay_before=0`，后续段 `delay_before = random.uniform(DELAY_MIN, DELAY_MAX)`。首段 `at_user_id` 由调用方设置（planner 只产出 text 与 delay，at 由 transport 或调用方注入；建议 planner 接收 `at_user_id` 参数并只标在首段）。

4. `plan_stream(token_iter) -> AsyncIterator[OutboundChunk]`：消费 token 异步流，用 `take_emit_chunk` 增量产出段；`ENABLE_STREAM_CHUNK=False` 时先收齐再走 `plan_static`；每段上限 `LIMIT`；结束 flush 余量。同样在非首段设置随机 `delay_before`。

> 关于随机性：允许注入一个 `rng`（`random.Random`）以便测试确定化；默认用模块级 `random`。

## 验收标准
- `split_chunks` 对典型多句文本的切分结果与 MVP 一致（段数、长度约束）。
- `plan_stream` 段数不超过 LIMIT；首段无延迟，后续有延迟。
- `ENABLE_STREAM_CHUNK=False` 时 `plan_stream` 等价于收齐后 `plan_static`。

## 测试要求
`tests/core/test_reply_planner.py`：
- 用固定 seed 的 `Random` 断言分段与延迟。
- 流式：喂一串 token（含标点），断言产出的 chunk 文本序列。

## 约束
Core 纯逻辑；不 import nonebot；不发送消息。`at` 只出现在首段。
