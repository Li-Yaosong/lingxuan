# P3-02 · migrate-memory — JSON→DB 一次性迁移

## 目标
实现 `lingxuan migrate-memory`：把旧 `data/memory` 的 JSON 数据幂等导入 SQLite，支持 dry-run 与迁移报告。

## 前置依赖
- P2-04~P2-07（各 Repository）、P2-02/03（schema 就绪）、P3-01（CLI 框架）。

## 需创建或修改的文件
- 新增 `src/lingxuan/migration/from_json.py`（迁移核心）
- 修改 `src/lingxuan/cli.py`：接入 `migrate-memory` 子命令。

## 详细规格
命令：`lingxuan migrate-memory [--dry-run] [--source data/memory] [--report report.json]`

读取源（对齐 `00-common-context.md` 4.2/4.3）：
- 会话：`{source}/*.json`（`private_*.json` / `group_*.json`）→ sessions + session_messages + session_entities（meta.entities）+ summary。
  - 兼容旧格式：纯 list 的 history 视为 v2 history（无 summary/meta）。
- 用户档案：`{source}/users/*.json`（stem 为纯数字）→ user_profiles + user_facts（保留 active/软删除状态、id、learned_at）。
- 社会图：`{source}/social_graph.json` → social_edges（四元组去重）+ name_index。

要求：
- **幂等**：用主键/唯一约束 upsert（session_id / user_id / fact.id / 四元组 / name）；重复运行不产生重复行。
- **dry-run**：只扫描+校验，输出将写入的各表行数与冲突/异常清单，不触碰 DB。
- **顺序**：sessions → messages → entities → user_profiles → user_facts → social_edges → name_index（尊重外键）。
- **迁移报告**（JSON）：每域计数、跳过项与原因、耗时、源目录、时间戳。
- **不删除源 JSON**：迁移成功后可将源目录改名为 `data/memory.imported/`（可选，通过参数控制；默认保留原样并提示）。
- 错误处理：单条坏数据记录到报告并跳过，不中断整体（除非严重错误）。

## 验收标准
- 对样例 JSON（含旧格式、软删除 fact、重复边）迁移后各表行数正确。
- 二次运行无新增（幂等）。
- dry-run 不写库但报告准确。

## 测试要求
`tests/migration/test_from_json.py`：构造样例 `data/memory` 目录 → 迁移到临时库 → 断言各表行数与关键字段；再跑一次断言无新增；dry-run 断言库为空但报告非空。

## 约束
迁移是一次性路径；不做 JSON↔DB 双写。源数据只读（除可选归档改名）。
