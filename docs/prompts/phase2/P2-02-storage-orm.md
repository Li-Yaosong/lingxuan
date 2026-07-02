# P2-02 · adapters/storage/orm.py — SQLAlchemy 2.0 ORM 映射

## 目标
定义与 `docs/architecture-v2.md` 第八节表结构一致的 ORM 模型（DeclarativeBase + Mapped）。

## 前置依赖
- P2-01（Database/Base 约定）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/storage/orm.py`

## 详细规格
用 SQLAlchemy 2.0 `DeclarativeBase` + `Mapped[...]` / `mapped_column`。时间统一 `String`（ISO8601 UTC 文本）或 `DateTime`（择一，建议 String 以对齐旧 JSON），布尔用 `Boolean`/`Integer`。定义下列表（字段/类型/索引/约束严格对齐第八节）：

- `sessions`：`session_id PK(str)`, `kind`, `group_id(int|null)`, `summary(default '')`, `nickname(default '')`, `last_active_at`, `created_at`。可选 `meta_json`（迁移到未知 meta 键时用）。
- `session_messages`：`id PK autoincrement`, `session_id FK→sessions ON DELETE CASCADE`, `seq(int)`, `role`, `content`, `user_id(int|null)`, `created_at`。约束 `UNIQUE(session_id, seq)`；索引 `(session_id, id)`。
- `session_entities`：`session_id FK ON DELETE CASCADE`, `name`, `user_id`。`PRIMARY KEY(session_id, name)`。
- `user_profiles`：`user_id PK`, `preferred_name`, `aliases_json(default '[]')`, `group_cards_json(default '{}')`, `stage(default 'stranger')`, `first_met_at`, `last_seen_at`, `interaction_count(default 0)`, `last_group_id`, `seen_in_private(bool)`, `seen_in_group(bool)`, `impression(default '')`, `cognition_summary(default '')`, `cognition_updated_at`, `cognition_interaction_at_update(default 0)`, `version(default 2)`。
- `user_facts`：`id PK(str)`, `user_id FK→user_profiles ON DELETE CASCADE`, `content`, `category(default 'general')`, `source_user_id(default 0)`, `learned_at`, `confidence(float default 1.0)`, `active(bool default True)`, `supersedes(str|null)`。索引 `(user_id, active, learned_at)`。
- `social_edges`：`id PK autoincrement`, `from_user_id`, `to_user_id`, `relation`, `label(default '')`, `evidence(default '')`, `group_id(int|null)`, `learned_at`。`UNIQUE(from_user_id, to_user_id, relation, label)`；索引 from/to。
- `name_index`：`name PK`, `user_id`, `updated_at`。
- `settings`：`key PK`, `value_json`, `group_name`, `is_secret(bool default False)`, `updated_at`, `updated_by`。
- `admin_users`：`id PK`, `username UNIQUE`, `password_hash`, `role(default 'admin')`, `must_change_password(bool default True)`, `created_at`, `last_login_at`。
- `audit_logs`：`id PK`, `actor`, `action`, `target`, `detail_json`, `ip`, `success(bool)`, `created_at`。索引 `created_at`、`actor`。
- `plugin_configs`：`name PK`, `enabled(bool default True)`, `config_json(default '{}')`, `updated_at`。

## 验收标准
- `Base.metadata` 含全部 11 张表，字段/约束/索引与第八节一致。
- 能对临时 db `create_all` 成功建表。

## 测试要求
`tests/adapters/storage/test_orm.py`：`create_all` 后用 inspector 断言表名、关键约束（唯一约束、外键）存在。

## 约束
仅 ORM 映射，不写 Repository 逻辑；不写业务。
