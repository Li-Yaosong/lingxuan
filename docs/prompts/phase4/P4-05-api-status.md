# P4-05 · admin/routes/status.py — 服务状态 API

## 目标
实现服务状态 API（对应 P0 服务状态）：Bot 连接、LLM 可达性、功能开关、记忆统计。

## 前置依赖
- P4-02、P4-03、P2-04~P2-07（Repository 统计）、P0-03（LLM 探测）、Container。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/routes/status.py`
- 修改 `src/lingxuan/admin/schemas.py`（Status 模型）

## 详细规格
- `GET /status`（readonly）：返回
  - `bot_online`：`nonebot.get_bots()` 是否有连接（经 transport 暴露的方法，避免 admin 直接 import nonebot——在 transport 加 `is_connected()`）。
  - `features`：各 `ENABLE_*` 当前值（从 config）。
  - `model`：OPENAI_MODEL。
  - `memory_stats`：session/message/user/active_fact/edge 计数（用第八节统计查询，经 Repository 提供的统计方法）。
  - `observe_states`：可选，各群冷却/最近 judge（从 ObservationStore）。
- `POST /status/llm-check`（readonly）：发一条极小的 `llm.chat`（或专用 ping）判断可达，返回 `{ok, latency_ms, error?}`；超时按失败。

为让 admin 不直接依赖 nonebot：在 `MessageTransport` 增补 `is_connected() -> bool`（onebot 实现用 get_bots），status 路由经 container.transport 调用。
为记忆统计：在各 Repository 增补 `count()` 或提供一个 `StatsService` 聚合（推荐 StatsService，注入各 Repository）。

## 验收标准
- GET /status 返回结构完整，计数与库一致。
- llm-check 在无 key/超时时返回 ok=false 且不抛。
- readonly 可访问。

## 测试要求
`tests/admin/test_api_status.py`：临时库塞数据后断言计数；llm-check 用 FakeLLM 成功/失败两路。

## 约束
admin 不直接 import nonebot（经 transport 抽象）；统计经 Repository/StatsService。
