# P1-07 · core/observation.py — ObservationService（群观察 + judge 编排）

## 目标
把 MVP 分散在 `group_observer.py`（规则）与 `handlers/group.py::_observe_group`（短路 + LLM judge 调用）两处的观察决策逻辑，**收敛到 Core 的单一 ObservationService**。含防抖、冷却、规则短路、LLM judge、回复触发编排。

## 前置依赖
- P1-03（prompting：judge prompt、should_skip_reply_locally）、P1-04（reply_planner）、P1-06（ObservationStore）、P0-03（LLMProvider）、P0-02、P0-05、P0-06。
- 记忆读写在 Phase 1 仍走旧模块/或经注入的 MemoryService 占位；建议通过注入接口调用，Phase 2 换实现。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/observation.py`

## 详细规格
类 `ObservationService`，构造注入：`store: ObservationStore`、`llm: LLMProvider`、`prompt: PromptBuilder`、`planner: ReplyPlanner`、`config`、`clock`、以及记忆/用户记忆服务接口（用于写回复、生成观察文本/用户 brief）、`transport: MessageTransport`（用于发送）。

迁移以下规则函数（从 MVP `group_observer.py`，改为方法或纯函数，时间走 clock）：
- `format_observation(group_id)`：缓冲格式化，同用户 `GROUP_BURST_MERGE_WINDOW` 秒内 burst 合并为 `" / "`，标注 `@BOT_NAME`/`@他人`。
- 规则判定：`is_knowledge_question`、`is_introducing_other`、`should_skip_observe`、`is_followup_after_bot`（用 `GROUP_FOLLOWUP_WINDOW`）、`is_directed_at_bot`、`is_seeking_engagement`、`should_bypass_cooldown`、`get_reply_target`、`latest_*` 系列。
- 冷却：`is_in_cooldown`、`mark_last_trigger`（`cooldown_until = now + GROUP_OBSERVE_COOLDOWN`）。
- 短路：合并 MVP handler 里的 `_should_shortcircuit_judge`（@bot/reply/名字/定向/求互动/介绍他人/跟进）到本服务。

核心编排方法：
- `async def on_group_message(inbound: InboundMessage) -> None`：入缓冲 + 记忆写入 + 触发 `schedule_observe`；@bot 或 reply_to_bot 走**直回路径**（见 DialogueService P1-08 或在此调用其群回复方法——二者边界见下）。
- 防抖 `schedule_observe(group_id)`：取消旧任务，`clock.sleep(GROUP_OBSERVE_DELAY)` 后若有新消息则跑 `_observe`。
- `async def _observe(group_id)`（对齐 MVP `_observe_group`）：
  1. `ENABLE_GROUP_OBSERVE` 关 → mark_observed 返回。
  2. `format_observation` 空 → 返回。
  3. `should_skip_observe` → 返回。
  4. 短路命中 → `should_reply=True`；否则若非 bypass 且 `is_in_cooldown` → 返回；否则 `should_skip_reply_locally` → 返回；否则 `llm.judge(build_judge_prompt(...))`。
  5. `should_reply` → 取 `get_reply_target` → 生成回复（流式 `chat_in_group_stream` 等价：`prompt` 造 messages + `llm.chat_stream` + `planner.plan_stream` + `transport.send_stream`）→ 写记忆、`mark_last_trigger`、触发 summarize/认知整合。
  - 并发：用 `store.lock(group_id)` 串行化；`observe_in_flight`/`pending_observe` 合并（对齐 MVP `run_observe_loop`）。

> 直回 vs 观察边界：建议群 @ 直回逻辑放 DialogueService（P1-08），ObservationService 只管被动观察；但二者共享「生成群回复并发送」的私有 helper。请在两文件间约定一个 `GroupReplyExecutor`（可放 core/dialogue.py 或单独 helper），避免重复。若合并到一个服务也可，但须保持职责清晰。

## 验收标准
- 规则短路矩阵行为与 MVP 一致（用例覆盖 @bot/reply/名字/求助/介绍/跟进）。
- 冷却与 bypass、防抖合并语义一致。
- judge 仅在非短路、非冷却、非本地跳过时被调用。
- 全程时间走 clock，可用 FakeClock 确定化测试。

## 测试要求
`tests/core/test_observation_service.py`（用 fakes：FakeClock/FakeLLM/FakeTransport/InMemory repos）：
- 短路命中不调用 llm.judge，直接回复。
- 冷却期内非 bypass 消息不回复；bypass（@bot）绕过冷却。
- judge 返回 no 不回复、yes 回复且发送 chunk 序列。
- 防抖：连发多条只观察一次。

## 约束
Core 层：禁止 import nonebot / openai / sqlalchemy。所有外部能力经注入接口。时间走 clock。
