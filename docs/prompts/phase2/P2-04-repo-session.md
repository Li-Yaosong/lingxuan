# P2-04 · SessionRepository（SQLite 实现）

## 目标
实现 `SessionRepository`（Protocol 见 P0-04），基于 ORM + async session，精确复现 MVP 会话记忆语义（含裁剪）。

## 前置依赖
- P0-04（接口 + DTO）、P2-01（db）、P2-02（orm）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/storage/repositories.py`（本任务先实现 SessionRepository，其余 Repository 由 P2-05/06/07 追加到同文件或分文件）。

## 详细规格
`class SqlSessionRepository(SessionRepository)`，注入 `db: Database`。实现全部接口方法：
- `ensure`：不存在则插入 `sessions` 行（kind 由 SessionId.kind，created_at=now）。
- `append_message`：seq 取该会话当前 max(seq)+1（或用行级计数）；插入 `session_messages`；更新 `sessions.last_active_at`。
- `load_history(limit)`：`ORDER BY id DESC LIMIT limit` 再反转为正序；无 limit 返回全部（受裁剪上限约束）。
- `count_messages`。
- `trim_to_last(keep_last)`：删除该会话除最新 keep_last 行外的所有行（`DELETE ... WHERE session_id=? AND id NOT IN (SELECT id ... ORDER BY id DESC LIMIT keep_last)`）；返回删除数。**用于复现 MVP 两处裁剪**（40 上限、摘要后减半）——注意具体触发时机在 MemoryService（P2-08），Repository 只提供能力。
- `get_summary`/`set_summary`。
- `clear`：删除该 session 行（级联删 messages/entities）。
- `update_meta`：更新 nickname/group_id/last_active_at。
- `merge_entity`：upsert `session_entities`（同 name 覆盖 user_id）。
- `get_entities`：返回 `{name: user_id}`。
- `list_sessions(limit, before_id)`：keyset 分页（供管理端）。

事务：每个方法用 `async with db.session() as s: ... await s.commit()`；批量操作在同一事务。

## 验收标准
- 通过与 InMemory 版**同一套契约测试**（见测试要求）。
- trim_to_last 删最旧、保留最新 keep_last。
- clear 级联删除 messages/entities。

## 测试要求
建立**契约测试** `tests/adapters/storage/test_session_repo_contract.py`：参数化对 `InMemorySessionRepository` 与 `SqlSessionRepository`（临时 db + upgrade head）跑同一组断言：append/load 顺序、count、trim、summary、entities、clear 级联。

## 约束
Adapter 层，可用 sqlalchemy；语义必须与 InMemory 版及 MVP 一致。
