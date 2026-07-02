# P1-15 · 全量替换「直接 import 大写常量」为 ConfigProvider（决策 5）

## 目标
执行已定决策：把所有业务模块从 `from lingxuan.config import BOT_NAME, ENABLE_...` 的**模块级常量直读**，改为经注入的 `ConfigProvider` 读取；移除 `config.py` 的模块级大写常量与其直接用法，**不保留 shim**。`.env` 环境变量项全部保留（仍由 ConfigProvider 作为默认值来源）。

## 前置依赖
- P1-01（ConfigProvider 实现）。
- 理论上应在 Core 各 Service（P1-02~P1-09）已改为注入 config 之后收尾；本任务负责**清理残留**与**删除旧常量**。

## 需创建或修改的文件
- 修改/删除 `src/lingxuan/config.py` 的模块级大写常量导出部分。
- 审查并修正所有仍直接 import 这些常量的地方（MVP 遗留：`llm.py`、`group_observer.py`、`persona.py`、`memory.py`、`user_memory.py`、`message_chunk.py`、`handlers/*`、`admin.py`、`startup.py`、`bot.py`）。

## 详细规格
1. 全仓搜索对以下符号的 import/使用：`BOT_NAME, BOT_PERSONA, BOT_ADMINS, DRIVER, OPENAI_*, MEMORY_WINDOW, GROUP_*, ENABLE_*, USER_*, DATA_DIR, MEMORY_DIR` 及 `settings` 单例、`get_runtime_config/is_feature_enabled/get_llm_config/mask_api_key/validate_config/get_admin_ids`。
2. 对已迁移到 Core/Adapter 的新模块：确保它们只经注入的 `ConfigProvider` 读取，无残留旧 import。
3. 对仍存在的旧模块（若某些 Phase 1 未迁移完的辅助逻辑）：要么迁移，要么改为接收 config 参数。**目标是 `config.py` 不再导出模块级大写常量**。
4. 保留/迁移这些辅助函数的等价实现到合适位置（如 `mask_api_key` → 供 ConfigProvider 脱敏使用；`validate_config` → bootstrap `_startup` 用，读 config 校验必填项）。
5. `config.py` 可保留 `Settings`/`from_env` 供迁移期参考，但**删除**「一次性快照为大写常量」的代码块；若彻底不需要则整文件删除（确保无引用后）。
6. 更新 `.env.example`：补充新增项（DB_URL/DATA_ROOT/AUTO_MIGRATE/ADMIN_HOST/ADMIN_PORT/SECRET_KEY/JWT_ACCESS_TTL/JWT_REFRESH_TTL），并加注释说明「运行时以管理端/DB 配置为准，.env 为首次默认值」。**不要删除任何现有项**。

## 验收标准
- 全仓无对 `lingxuan.config` 模块级大写常量的 import（可用 grep 验证）。
- 所有配置读取路径统一经 `ConfigProvider`。
- `.env.example` 含全部旧项 + 新增项。
- 应用启动与全部行为不回退。

## 测试要求
- 增加一个「守卫测试」`tests/test_no_legacy_config_imports.py`：用 AST 或正则扫描 `src/lingxuan/`，断言不存在从旧 config 常量的 import（新代码只允许 import `ConfigProvider`/`settings_defaults`）。
- 回归运行既有 Core 测试全绿。

## 约束
这是 Phase 1 收尾、风险较高的大改，需配合充分测试；`.env` 变量项不得删除；仅删除内部模块级常量（非配置项）。
