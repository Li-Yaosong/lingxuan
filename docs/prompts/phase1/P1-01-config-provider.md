# P1-01 · ConfigProvider 实现（env + 内存，可订阅）

## 目标
实现 `ConfigProvider`：以 `settings_defaults` 为基线，叠加 `.env`，运行时值可 `set` 并通知订阅者。Phase 1 阶段 DB 尚未接入，`set` 先只更新内存 + 触发回调（Phase 2 再叠加持久化，见 P2-07/P2-10）。

## 前置依赖
- P0-05（ConfigProvider 接口）、P0-08（settings_defaults）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/config_provider.py`

## 详细规格
类 `EnvConfigProvider(ConfigProvider)`：
- 构造：读取 `settings_defaults.SETTINGS` 建默认值 dict；用 `python-dotenv` 加载 `.env`；对每个 key，若 env 存在则 `parse_value` 覆盖默认。
- 值优先级（Phase 1）：内存 override（`set` 写入） > env > default。预留一个 `db_repo: ConfigRepository | None = None`，若提供则在启动时 `load` 一次并纳入优先级（DB > env > default），Phase 2 用。
- `get/get_str/get_int/...`：从合并后的值取，按类型返回；未知 key 抛 `KeyError`；类型不符尝试 `parse_value` 或转换。
- `set(key, value, actor)`：校验 key 存在；更新内存；若有 `db_repo` 则 `await db_repo.set(...)`；触发所有订阅回调 `(key, value)`；（审计在有 AuditRepository 时记录，Phase 1 可省略）。
- `get_all(mask_secrets=True)`：返回全部 key→value；敏感项（`spec.is_secret`）用脱敏（复用 MVP `mask_api_key` 风格：保留前后各若干位，中间打码）。
- `subscribe(cb)`：注册回调，返回取消函数。

线程/并发：单进程 async，普通 dict 即可；`set` 与回调在事件循环内同步执行。

## 验收标准
- 无 env 时所有 key 返回 defaults。
- 设置 env（如 `BOT_NAME=测试`）后 `get_str("BOT_NAME")=="测试"`。
- `set` 后 `get` 反映新值且订阅回调被调用。
- `get_all(mask_secrets=True)` 对 `OPENAI_API_KEY` 脱敏。

## 测试要求
`tests/adapters/test_config_provider.py`：defaults/env 覆盖/set+subscribe/脱敏 四组用例（用临时 env 或 monkeypatch）。

## 约束
Adapter 层，可依赖 `python-dotenv`；不 import 任何业务模块。
