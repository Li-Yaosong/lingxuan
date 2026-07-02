# P2-03 · Alembic 初始化与 0001_init 迁移

## 目标
接入 Alembic 管理 schema 版本，生成初始迁移 `0001_init` 建全部表。

## 前置依赖
- P2-02（ORM 模型作为 metadata 来源）、P0-05（config 提供 DB_URL）。

## 需创建或修改的文件
- 新增 `alembic.ini`（项目根）
- 新增 `alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/0001_init.py`

## 详细规格
- `alembic/env.py`：
  - `target_metadata = orm.Base.metadata`。
  - DB URL 从 `ConfigProvider`/环境读取（默认 `sqlite+aiosqlite:///data/lingxuan.db`）；async 引擎需用 Alembic 的 async 模板（`run_async_migrations`）或将 async URL 转同步（`sqlite:///...`）执行离线/在线迁移。建议：迁移用同步驱动（`sqlite:///data/lingxuan.db`）以简化，运行时用 async；确保两者指向同一文件。
  - 开启 `render_as_batch=True`（SQLite ALTER 限制，便于后续变更）。
- `0001_init.py`：`upgrade()` 建第八节全部 11 张表 + 索引 + 约束；`downgrade()` 反向 drop。可用 autogenerate 生成后人工校对（尤其唯一约束/索引命名）。
- CLI 封装（与 P3-01 协调）：`lingxuan db upgrade` = `alembic upgrade head`；`lingxuan db revision -m` = autogenerate。

## 验收标准
- `alembic upgrade head` 在空目录能建出与 ORM 一致的 schema。
- `alembic downgrade base` 能清空。
- autogenerate 对已迁移 schema 显示「no changes」（模型与迁移一致）。

## 测试要求
`tests/adapters/storage/test_migrations.py`：对临时 db 跑 `upgrade head`，用 inspector 断言表齐全；再跑 autogenerate 比对无差异（或跳过 autogenerate，仅断言表结构）。

## 约束
迁移是 schema 唯一事实源（运行时不用 `create_all` 建正式库）。禁止手改已发布迁移；后续变更新增 revision。
