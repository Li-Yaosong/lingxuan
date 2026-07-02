# P2-10 · 容器装配 SQLite 实现 + ConfigProvider 接 DB

## 目标
把 Phase 1 的临时存储实现替换为 SQLite Repository，接入 Container；ConfigProvider 接上 ConfigRepository（DB 优先）。Phase 2 结束后运行时读写走 SQLite。

## 前置依赖
- P2-01~P2-09 全部完成；P1-14（Container/bootstrap）。

## 需创建或修改的文件
- 修改 `src/lingxuan/container.py`
- 修改 `src/lingxuan/bootstrap.py`（启动初始化 DB）
- 修改 `src/lingxuan/adapters/config_provider.py`（若需要，接 db_repo）

## 详细规格
- Container 新增：`db = Database(config.get_str("DB_URL"))`；构建 `SqlSessionRepository/SqlUserProfileRepository/SqlSocialGraphRepository/SqlConfigRepository/SqlAuditRepository/SqlPluginConfigRepository/SqlAdminUserRepository`。
- 用 SQLite 版 MemoryService/UserMemoryService（P2-08/09）替换 Phase 1 临时实现，注入对应 Repository。
- ObservationService/DialogueService/AdminCommandService 改为依赖新的 MemoryService/UserMemoryService（接口不变，实现替换）。
- ConfigProvider：注入 `ConfigRepository`，启动时 `await load()`（DB 值并入优先级：DB > env > default）；`set` 时 `await config_repo.set(...)` 落库 + 审计（注入 AuditRepository）。
- bootstrap `_startup`：确保 DB 可用（Phase 3 会在此加自动迁移；Phase 2 可先要求已手动 `lingxuan db upgrade`，或调用 `alembic upgrade head`）。首次无 admin 用户的处理放 Phase 4。

## 验收标准
- 启动后消息处理的记忆读写落到 SQLite（可查 db 文件表数据）。
- 配置 `set` 后重启仍生效（已落 DB）。
- 全部 Core 单测仍绿；契约测试绿。

## 测试要求
`tests/test_container_sqlite.py`：用临时 db + upgrade head 构建 Container，跑一条私聊消息端到端，断言 `session_messages` 有落库行。

## 约束
只做装配与实现替换，不改业务语义；保持 MVP 行为。JSON 文件此后不再作为运行时读写路径（迁移在 Phase 3）。
