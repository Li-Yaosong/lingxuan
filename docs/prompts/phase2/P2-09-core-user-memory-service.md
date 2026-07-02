# P2-09 · core/user_memory.py — UserMemoryService 切 Repository

## 目标
把 MVP `user_memory.py` 迁为基于 `UserProfileRepository` + `SocialGraphRepository` 的 UserMemoryService，复现全部语义（档案更新、fact 截断/软删除、社会图、认知整合、prompt 上下文格式化、记忆抽取调度），**不再直接读写 JSON**。

## 前置依赖
- P0-04（两个 Repository）、P0-07（core/models：compute_stage 等）、P1-03（prompting，可选）、P0-03（LLM）、P0-05/06。

## 需创建或修改的文件
- 新增/替换 `src/lingxuan/core/user_memory.py`

## 详细规格
`class UserMemoryService`，注入 `profiles: UserProfileRepository`、`graph: SocialGraphRepository`、`llm`、`config`、`clock`、`log`。

迁移以下能力（对齐 MVP，逐项保持行为）：
- `touch_user(user_id, *, nickname, group_id, is_private)`：更新 relationship（interaction_count+1、last_seen_at、seen_in_*、last_group_id、first_met_at 初次），`stage = compute_stage(...)`；upsert。
- `set_preferred_name`、别名/群名片维护（identity）。
- `add_fact(...)`：去重（active 且 content 同则跳过）；新增后执行**截断**：active facts > `USER_MEMORY_MAX_FACTS` → 按 learned_at 升序把最旧多余的 `deactivate_facts`。
- identity fact 变更：新增 identity 时把旧 identity active facts 置 inactive。
- `add_social_edge(...)`：四元组去重（Repository 保证）。
- `index_name` / `resolve_name` / `sync_entity_to_graph`。
- `apply_rule_extraction(...)`：规则抽取（昵称/介绍/自称）→ 写边 + name_index + 触发认知整合调度。
- `on_user_message(...)`：私聊/群聊入口（touch + 规则抽取 + 调度记忆抽取）。
- `schedule_memory_extract(...)`：debounce `USER_MEMORY_BURST_MERGE`（3s）合并后 LLM 抽取 facts/edges/impression_delta（`_llm_extract_memory`）。
- 认知整合：`should_refine_cognition`、`refine_user_cognition`（LLM 生成 summary，截断 `USER_COGNITION_MAX_CHARS`=150，更新 cognition_*），`schedule_cognition_refine`（debounce `USER_COGNITION_REFINE_DELAY`=2s）。触发条件对齐 MVP（间隔 5 次互动 / 称呼变更 / 首次 / 有最近对话）。
- Prompt 格式化：`format_user_context_for_prompt(...)`、`format_user_brief(user_id)`、`format_user_profile_summary(user_id)`（供 admin_commands 与 prompting 使用）。
- 初始化/迁移辅助：`ensure_user_memory_initialized()`（Phase 2 里对应 DB 就绪检查；JSON→DB 迁移在 Phase 3 的 migrate-memory，不在此处）。

debounce 任务用注入的 clock/asyncio 管理，避免模块级全局。

## 验收标准
- 关系阶段/交互计数/fact 截断/软删除/边去重/认知整合触发与 MVP 一致。
- prompt 上下文文本格式与 MVP 一致（供回归对齐）。
- 无文件 IO；全部经 Repository。

## 测试要求
`tests/core/test_user_memory_service.py`（InMemory repos + FakeLLM + FakeClock）：
- touch_user 计数与 stage 演进。
- add_fact 去重 + 超 30 条截断（保留最新，旧的 active=0）。
- identity 变更旧 identity 失活。
- 认知整合触发条件（间隔达标/称呼变更）与截断长度。
- 社会边去重、name 解析。

## 约束
Core 层，禁止文件 IO / nonebot / sqlalchemy；时间与调度经 clock/注入。
