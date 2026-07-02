# P2-07 · ConfigRepository / AuditRepository / PluginConfigRepository / AdminUserRepository（SQLite）

## 目标
实现其余四个 Repository（配置、审计、插件配置、管理端用户），供 ConfigProvider 持久化、管理端与审计使用。

## 前置依赖
- P0-04、P2-01、P2-02。

## 需创建或修改的文件
- 追加到 `src/lingxuan/adapters/storage/repositories.py`：`SqlConfigRepository`、`SqlAuditRepository`、`SqlPluginConfigRepository`、`SqlAdminUserRepository`。

## 详细规格

### SqlConfigRepository
- `get_all()`：读 `settings` 表，`value_json` 反序列化，返回 `{key: value}`。
- `set(key, value)`：upsert 一行（value_json = json 序列化，updated_at=now）。`is_secret`/`group_name` 可从 `settings_defaults` 补充。
- `bulk_set(items)`：单事务批量 upsert。

### SqlAuditRepository
- `record(...)`：插入 `audit_logs`（detail_json 序列化，created_at=now）。
- `query(actor, action, limit, before_id)`：keyset 分页倒序。

### SqlPluginConfigRepository
- `get(name)`：返回 `(enabled, config)` 或 None。
- `upsert(name, enabled, config)`：upsert。
- `all()`：`{name: (enabled, config)}`。

### SqlAdminUserRepository
- `get_by_username`、`create`、`set_password`（可清 must_change_password）、`touch_login`（last_login_at=now）、`count`。

## 验收标准
- 各方法契约测试通过（与 InMemory 版一致）。
- ConfigRepository 的 value 往返（含 int/bool/list/str）正确。

## 测试要求
契约测试 `tests/adapters/storage/test_misc_repos_contract.py`：对四个 Repository 的 InMemory 与 Sql 版分别跑基本 CRUD 与分页/往返断言。

## 约束
Adapter 层；JSON 序列化用标准库 json。
