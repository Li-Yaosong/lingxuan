# P3-03 · backup / restore — 备份与恢复

## 目标
实现 `lingxuan backup` 与 `lingxuan restore`：SQLite 文件快照（+ 打包源 JSON），用于迁移前保护与失败回滚。

## 前置依赖
- P3-01（CLI）、P2-01（db 路径）。

## 需创建或修改的文件
- 新增 `src/lingxuan/migration/backup.py`
- 修改 `src/lingxuan/cli.py`：接入 `backup` / `restore`。

## 详细规格
- `lingxuan backup [--out data/backups]`：
  - 生成时间戳目录 `data/backups/{YYYYMMDD-HHMMSS}/`。
  - 复制 `lingxuan.db`（用 SQLite 在线备份 API 或在无写入时直接复制 db + wal + shm；建议用 `sqlite3` backup API 确保一致性）。
  - 若存在 `data/memory` 源 JSON，打包为 `memory.zip` 一并放入。
  - 写 `manifest.json`（时间、db 大小、包含项）。
  - 返回备份目录路径。
- `lingxuan restore --from <备份目录>`：
  - 校验 manifest；停机前置检查（提示确保进程未运行）。
  - 用备份 db 覆盖当前 db（覆盖前对当前 db 再做一次自动快照，防误操作）。
  - 可选恢复 memory.zip。
- 二次确认：restore 需 `--yes` 或交互确认。

## 验收标准
- backup 生成可用快照 + manifest。
- restore 能把 db 恢复到快照状态（数据一致）。
- restore 前自动对现状再快照。

## 测试要求
`tests/migration/test_backup.py`：建临时库写数据 → backup → 改动库 → restore → 断言数据回到快照；manifest 字段正确。

## 约束
恢复是破坏性操作，必须有确认与前置自动快照；不做双写。
