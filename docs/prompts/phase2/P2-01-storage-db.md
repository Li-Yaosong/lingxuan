# P2-01 · adapters/storage/db.py — async engine/session 与 PRAGMA

## 目标
建立 SQLAlchemy 2.0 async 引擎与会话工厂，配置 aiosqlite + WAL 等 PRAGMA，供各 Repository 使用。

## 前置依赖
- P0-01（依赖已装）、P0-05（config，读取 DB_URL）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/storage/db.py`

## 详细规格
- `create_engine_and_sessionmaker(db_url: str) -> tuple[AsyncEngine, async_sessionmaker]`：
  - `create_async_engine(db_url, echo=False)`。
  - 通过 `event.listens_for(engine.sync_engine, "connect")` 或连接初始化，对每个连接执行 PRAGMA：`journal_mode=WAL`、`synchronous=NORMAL`、`foreign_keys=ON`、`busy_timeout=5000`。
  - `async_sessionmaker(engine, expire_on_commit=False)`。
- `class Database`：持有 engine + sessionmaker；提供：
  - `session()` async context manager（`async with db.session() as s:`）。
  - `async def create_all()`（仅测试/首次用，正式 schema 由 Alembic 管理）。
  - `async def dispose()`。
- DB 文件目录不存在时自动创建（从 DB_URL 解析出路径；`DATA_ROOT` 下）。

## 验收标准
- 用临时 sqlite 文件能建立连接并执行 `PRAGMA journal_mode` 返回 `wal`。
- `foreign_keys` 为 ON（级联删除可用）。

## 测试要求
`tests/adapters/storage/test_db.py`：临时文件 db，断言 WAL 与 foreign_keys 生效；session 上下文可用。

## 约束
Adapter 层，可 import sqlalchemy/aiosqlite；不含业务逻辑。
