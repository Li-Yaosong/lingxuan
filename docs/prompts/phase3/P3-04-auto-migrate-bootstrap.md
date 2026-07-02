# P3-04 · 启动自动迁移流程（AUTO_MIGRATE，决策 4）

## 目标
在 bootstrap 启动流程中实现「自动 schema 升级 + 首次自动数据导入 + 失败回滚」，由 `AUTO_MIGRATE` 开关控制。

## 前置依赖
- P3-02（migrate-memory）、P3-03（backup/restore）、P2-03（alembic）、P1-14/P2-10（bootstrap 装配）。

## 需创建或修改的文件
- 修改 `src/lingxuan/bootstrap.py`（`_startup` 内接入自动迁移）
- 可新增 `src/lingxuan/migration/auto.py`（封装流程）

## 详细规格
`AUTO_MIGRATE=true`（默认）时，`_startup` 执行：
1. `alembic upgrade head`（schema 迁移）。
2. 判定是否需要**首次数据导入**：DB 业务表为空（如 `sessions`/`user_profiles` 均无行）**且**存在旧 `data/memory` JSON。
3. 若需要：
   a. `backup`（自动快照 db + 源 JSON）。
   b. `migrate-memory`（幂等导入）。
   c. 成功 → 归档源 JSON 到 `data/memory.imported/` + 写迁移报告到 `data/backups/.../report.json`。
   d. 失败 → `restore` 快照（删新 db/恢复）→ 记录错误日志 → 按配置**拒绝启动**（默认）或降级只读并告警。
4. 不需要（DB 非空或无旧 JSON）→ 正常启动，不导入。

`AUTO_MIGRATE=false` 时：只做 `alembic upgrade head`，数据导入交人工 CLI。

安全护栏（务必实现）：
- 仅当业务表为空时才自动导入（防重复/误迁移）。
- 导入前强制 backup。
- 失败即回滚，不带半迁移状态对外服务。
- 全流程结构化日志（供管理端日志页查看）。

参考流程见 `docs/architecture-v2.md` 8.6 的 Mermaid 图。

## 验收标准
- 空库 + 有旧 JSON + AUTO_MIGRATE=true：启动后数据自动入库，源 JSON 归档，报告生成。
- 非空库：启动不重复导入。
- 模拟迁移失败：自动 restore 回滚，启动中止（或降级）。
- AUTO_MIGRATE=false：仅升级 schema，不导入。

## 测试要求
`tests/migration/test_auto_migrate.py`：
- 空库+样例 JSON → 触发导入 → 断言入库 + 归档 + 报告。
- 非空库 → 不导入。
- 注入迁移异常 → 断言执行了 restore 且抛出/中止。

## 约束
启动关键路径，需健壮的异常处理与日志；不双写；默认行为安全（失败不带病启动）。
