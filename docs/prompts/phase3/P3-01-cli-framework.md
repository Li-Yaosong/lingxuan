# P3-01 · cli.py — CLI 框架与子命令骨架

## 目标
建立 `lingxuan` CLI，聚合 run / db / migrate-memory / backup / restore / admin-passwd 等子命令。本任务先搭框架 + `run` + `db upgrade`，其余子命令由 P3-02/03、P4-03 填充。

## 前置依赖
- P1-14（bootstrap.main）、P2-03（alembic）、P2-01（db）。

## 需创建或修改的文件
- 新增 `src/lingxuan/cli.py`
- 修改 `pyproject.toml`：`[project.scripts]` `lingxuan = "lingxuan.cli:main"`（`run` 子命令等价旧行为）。

## 详细规格
用标准库 `argparse`（不引入 click，除非已有依赖）。子命令：
- `lingxuan run`：调用 `bootstrap.main()`（默认无参数等价 run，兼容直接 `lingxuan`）。
- `lingxuan db upgrade`：`alembic upgrade head`（用 alembic API 或子进程）。
- `lingxuan db revision -m "..."`：autogenerate。
- 预留（后续任务实现）：`migrate-memory`、`backup`、`restore`、`admin-passwd`——本任务先注册占位并给出 `--help`。
- 全局参数：`--data-root`、`--db-url` 覆盖配置（可选）。

`main()`：解析并分派；无子命令时默认 `run`（保持 `lingxuan` 直接启动的习惯）。

## 验收标准
- `lingxuan --help` 列出所有子命令。
- `lingxuan db upgrade` 能对空库建表。
- `lingxuan`（无参）等价 `lingxuan run`。

## 测试要求
`tests/test_cli.py`：`--help` 退出码 0 且包含子命令名；`db upgrade` 对临时库建表成功（可调用内部函数而非真跑子进程）。

## 约束
CLI 属 Bootstrap 层，可 import 各层；保持 `lingxuan` 直接启动兼容。
