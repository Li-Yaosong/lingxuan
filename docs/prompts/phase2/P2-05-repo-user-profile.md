# P2-05 · UserProfileRepository（SQLite 实现）

## 目标
实现 `UserProfileRepository`，复现 MVP 用户档案与 fact 软删除/截断语义。

## 前置依赖
- P0-04、P2-01、P2-02。

## 需创建或修改的文件
- 追加到 `src/lingxuan/adapters/storage/repositories.py`：`class SqlUserProfileRepository`。

## 详细规格
- `get(user_id)`：读 `user_profiles` + 其 `user_facts`；`aliases_json`/`group_cards_json` 反序列化；组装 `UserProfile`（含 facts 列表）。
- `upsert(profile)`：插入或更新 `user_profiles`（identity/relationship/cognition 各列 + aliases/group_cards JSON）。**注意**：facts 不在此整体覆盖（facts 由 `add_fact`/`deactivate_facts` 管理），upsert 只处理 profile 主体字段。
- `add_fact(user_id, fact)`：插入 `user_facts`（若 fact.id 已存在则跳过/更新，保证幂等）。去重语义（active 且 content 相同不新增）可在 UserMemoryService 层判断，也可在此辅助。
- `list_active_facts(user_id, limit)`：`WHERE user_id=? AND active=1 ORDER BY learned_at DESC LIMIT ?`。
- `deactivate_facts(user_id, fact_ids)`：批量置 `active=0`（软删除，不物理删）。
- `list_user_ids`：所有 user_profiles 的 id。
- `delete(user_id)`：删 profile（级联删 facts）。
- `delete_all`：清空 user_profiles（连带 facts）；返回删除数。

> fact 截断（>USER_MEMORY_MAX_FACTS 按 learned_at 保留最新，其余 deactivate）与 identity 变更时旧 identity 置 inactive 的**触发逻辑在 UserMemoryService（P2-09）**；Repository 提供 add_fact/list_active_facts/deactivate_facts 能力即可。

## 验收标准
- 契约测试通过（与 InMemory 版一致）。
- facts 软删除后 `list_active_facts` 不返回；物理行仍在（可查 active=0）。
- delete 级联删 facts。

## 测试要求
契约测试 `tests/adapters/storage/test_user_profile_repo_contract.py`：对 InMemory 与 Sql 版跑同组断言：upsert/get 往返、add_fact/list_active、deactivate、delete 级联。

## 约束
Adapter 层；软删除不物理删除 fact。
