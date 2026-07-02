# P1-08 · core/dialogue.py — DialogueService（私聊 + 群 @ 直回）

## 目标
把 MVP `handlers/private.py` 与 `handlers/group.py` 的**业务编排**（去框架化）迁为 Core 的 DialogueService：私聊对话、群 @ 直回、管理员命令分流。handler 只负责把 event 转成 InboundMessage 后调用本服务。

## 前置依赖
- P1-03（prompting）、P1-04（reply_planner）、P1-09（AdminCommandService）、P0-02/03/05/06、记忆与用户记忆服务接口（Phase 1 注入占位/或旧逻辑封装，Phase 2 换 Repository 实现）。
- 与 P1-07 共享「群回复生成并发送」helper（`GroupReplyExecutor`）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/dialogue.py`

## 详细规格
类 `DialogueService`，构造注入：`config`、`llm`、`prompt`、`planner`、`transport`、`memory`（会话记忆服务）、`user_memory`（用户记忆服务）、`admin_commands`、`persona`、`observation: ObservationService`、`clock`、`plugin_host`（可选，Phase 5 接 Hook）。

方法：
- `async def handle_inbound(inbound: InboundMessage) -> None`：总入口，按 `session_id.kind` 分流到私聊/群聊。
- 私聊 `_handle_private(inbound)`（对齐 MVP `handle_private`）：
  1. `ENABLE_PRIVATE_CHAT` 关 → 返回；文本空 → 返回。
  2. 管理员（`inbound.actor.is_admin`）且 `parse_command` 命中 → `admin_commands.run(...)` → `transport.send`（纯文本）→ 返回。
  3. 否则：`user_memory.on_user_message(...)` → `memory.append` user → 组装 messages（`prompt.build_context_messages`，用户上下文来自 user_memory）→ `llm.chat`（**私聊非流式**，对齐 MVP）→ `memory.append` assistant → `transport.send`（单 chunk）→ `user_memory.schedule_cognition_refine` → `memory.schedule_summarize`。
- 群聊 `_handle_group(inbound)`（对齐 MVP `handle_group`）：
  1. 忽略自身消息（`actor.is_self`）；`ENABLE_GROUP_CHAT` 关 → 返回。
  2. 管理员命令分流同私聊（群作用域），命中即回复返回。
  3. 记忆写入/实体学习/`schedule_memory_extract`（Phase 5 由插件接管实体学习；Phase 1 先保留直接调用）。
  4. `at_bot`（含 reply_to_bot）→ **@ 直回**：`GroupReplyExecutor` 生成流式回复（首段 @ 目标），走 `transport.send_stream`；写记忆、`observation` 侧 mark（cooldown/last_trigger）、summarize、认知整合。
  5. 非 @ → `observation.on_group_message(inbound)`（进入观察调度）。

`GroupReplyExecutor`（与 P1-07 共享，放本文件或 helper 模块）：给定 session/observation_text/at_user_id/primary_user_id → 组 messages → `llm.chat_stream` → `planner.plan_stream`（首段注入 at）→ `transport.send_stream` → 返回已发送文本，供写记忆。

## 验收标准
- 私聊：普通消息走非流式 chat 并回复；管理员命令被正确分流。
- 群聊：@bot 走流式直回，首段 @ 目标、多气泡、段间延迟；非 @ 进入 observation。
- 行为与 MVP 对齐（顺序、开关、摘要/认知触发时机）。

## 测试要求
`tests/core/test_dialogue_service.py`（fakes）：
- 私聊普通消息：断言 memory.append 两次 + transport.send 一次 + summarize 调度。
- 私聊管理员命令：断言走 admin_commands 且不进 LLM。
- 群 @bot：断言流式发送 chunk 序列 + 记忆写回。
- 群非 @：断言转交 observation（可用 spy）。

## 约束
Core 层，禁止 import nonebot/openai/sqlalchemy。权限判断依据 `inbound.actor.is_admin`（由 adapter 填充），不在 Core 读 BOT_ADMINS 原始列表（Core 可通过 config 读，但判定放在 mapping 层更合适——统一在 mapping 设置 is_admin）。
