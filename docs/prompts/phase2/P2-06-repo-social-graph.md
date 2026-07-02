# P2-06 · SocialGraphRepository（SQLite 实现）

## 目标
实现 `SocialGraphRepository`，复现 MVP 社会关系图（边四元组去重 + name_index）语义。

## 前置依赖
- P0-04、P2-01、P2-02。

## 需创建或修改的文件
- 追加到 `src/lingxuan/adapters/storage/repositories.py`：`class SqlSocialGraphRepository`。

## 详细规格
- `add_edge(edge)`：依赖 `UNIQUE(from_user_id, to_user_id, relation, label)`；用 `INSERT ... ON CONFLICT DO NOTHING`（SQLite）或先查后插；已存在返回 `False`，新增返回 `True`（对齐 MVP 四元组去重）。
- `index_name(name, user_id)`：upsert `name_index`（同 name 覆盖 user_id，更新 updated_at）。
- `resolve_name(name)`：查 `name_index`，返回 user_id 或 None。
- `edges_from(user_id)`：`WHERE from_user_id=?`。
- `all_names()`：返回整个 `{name: user_id}`（供管理端展示/迁移）。
- `clear()`：清空 social_edges 与 name_index 两表。

## 验收标准
- 相同四元组第二次 `add_edge` 返回 False，不新增行。
- name_index upsert 覆盖语义正确。
- clear 清空两表。

## 测试要求
契约测试 `tests/adapters/storage/test_social_graph_repo_contract.py`：对 InMemory 与 Sql 版跑：add_edge 去重、index/resolve、edges_from、clear。

## 约束
Adapter 层；去重与 MVP 完全一致。
