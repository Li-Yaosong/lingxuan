# P0-01 · 项目脚手架与目录骨架

## 目标
为 v2 分层架构搭好包结构与依赖，**不改动任何 MVP 行为**，现有代码继续可运行。本任务纯新增。

## 前置依赖
无（这是第一个任务）。

## 需创建或修改的文件
- 修改 `pyproject.toml`：新增 v2 依赖（占位，后续 Phase 逐步用到）。
- 新增空包目录及 `__init__.py`：`src/lingxuan/protocols/`、`src/lingxuan/core/`、`src/lingxuan/adapters/`、`src/lingxuan/adapters/onebot/`、`src/lingxuan/adapters/openai/`、`src/lingxuan/adapters/storage/`、`src/lingxuan/adapters/logging/`、`src/lingxuan/config/`（注意：`config/` 与现有 `config.py` 会冲突，见下）。
- 新增 `tests/` 目录与 `tests/__init__.py`。

## 详细规格

### 依赖
在 `pyproject.toml` 的 `dependencies` 增补（保留现有项）：
- `sqlalchemy>=2.0`
- `aiosqlite>=0.19`
- `alembic>=1.13`
- `pydantic>=2.6`
- `python-jose[cryptography]>=3.3`（JWT，Phase 4 用）
- `passlib[argon2]>=1.7`（密码哈希，Phase 4 用）

新增可选开发依赖组 `[project.optional-dependencies].dev`：`pytest>=8`、`pytest-asyncio>=0.23`、`ruff`、`mypy`。

### `config.py` 与 `config/` 目录的冲突处理
现有 `src/lingxuan/config.py` 是模块。v2 需要 `src/lingxuan/config/` 包。**本任务先不要动 `config.py`**（避免破坏 MVP）。改为：新增 `src/lingxuan/defaults.py` 占位（Phase 0 的 config/defaults 内容将放这里，见 P0-08 会明确最终落点）。

> 重要：为避免 `config.py`（模块）与 `config/`（包）同名冲突，v2 最终形态里配置默认值放在 `src/lingxuan/settings_defaults.py`（单文件），不新建 `config/` 包。请据此创建，不要创建 `config/` 目录。README/架构文档中的 `config/defaults.py` 统一改指 `settings_defaults.py`。

因此本任务实际新建的包目录为：`protocols/`、`core/`、`adapters/`（含子包 `onebot/`、`openai/`、`storage/`、`logging/`），不建 `config/`。

### 包 `__init__.py`
各新增包的 `__init__.py` 留空或仅写模块 docstring。不要在 `__init__.py` 里写会触发 import 具体实现的代码（保持分层干净）。

## 验收标准
- `pip install -e ".[dev]"` 成功。
- `python -m lingxuan.bot`（现有入口）仍能启动到「等待连接」阶段（不因新目录报错）。
- `pytest`（此时无用例）能正常收集、0 失败。
- 新目录结构与 `00-common-context.md` 第 3 节一致（除用 `settings_defaults.py` 替代 `config/defaults.py`）。

## 测试要求
新增 `tests/test_imports.py`：`import lingxuan.protocols` 等空包可被导入不报错。

## 约束
- 不修改任何现有业务模块逻辑。
- 不删除现有 `config.py`、`handlers/`、`bot.py`。
