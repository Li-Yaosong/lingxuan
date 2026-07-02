# P1-14 · container.py + bootstrap.py — DI 容器与入口装配

## 目标
提供轻量 DI 容器组装各层实例，并用新的 `bootstrap.main()` 作为进程入口，替换 MVP 的 `bot.main`。Phase 1 结束后，`lingxuan` 命令走新入口，MVP 行为不变。

## 前置依赖
- Phase 1 的 P1-01~P1-13 全部完成（各 Service/Adapter 已就绪）。
- Phase 1 阶段存储仍是旧 JSON：MemoryService/UserMemoryService 可先用「封装旧 memory.py/user_memory.py 逻辑」的临时实现注入（Phase 2 换 SQLite Repository 版）。

## 需创建或修改的文件
- 新增 `src/lingxuan/container.py`
- 新增 `src/lingxuan/bootstrap.py`
- 修改 `pyproject.toml`：`[project.scripts]` 的 `lingxuan` 指向 `lingxuan.bootstrap:main`（保留旧 `bot:main` 一个 release 作为回滚入口，可加 `lingxuan-legacy = "lingxuan.bot:main"`）。

## 详细规格

### container.py
`class Container`：按依赖顺序惰性构建并缓存单例：
- `config = EnvConfigProvider()`
- `clock = SystemClock()`
- `log = BridgeLogSink()`
- `llm = OpenAIProvider(config, log)`
- `persona = PersonaService(config)`
- `prompt = PromptBuilder(persona, config)`
- `planner = ReplyPlanner(config)`
- `memory = <MemoryService 临时实现>`（Phase 1）
- `user_memory = <UserMemoryService 临时实现>`（Phase 1）
- `observation_store = ObservationStore(config, clock)`
- `observation = ObservationService(...)`
- `admin_commands = AdminCommandService(...)`
- `transport = OneBotTransport(config, ...)`
- `dialogue = DialogueService(...)`

暴露 `container.dialogue`、`container.transport` 等属性。允许后续 Phase 覆盖某些工厂（如换 SQLite repos）。

### bootstrap.py
`def main() -> None`：
1. 构建 `Container`。
2. `driver = init_nonebot(config)`。
3. `register_lifecycle(driver, on_startup=_startup, on_shutdown=_shutdown)`：
   - `_startup`：`validate_config`（校验必填如 OPENAI_API_KEY/BOT_ADMINS，缺失告警）、初始化用户记忆、打印配置摘要（对齐 MVP startup_check）。Phase 2 起在此做 DB 初始化/自动迁移（P3-04）。
   - `_shutdown`：下线日志。
4. `container.transport.start(container.dialogue.handle_inbound)`（注册 matcher）。
5. `run()`（`nonebot.run()`）。

## 验收标准
- `lingxuan`（新入口）能启动到「等待连接」，私聊/群聊/@ 直回/观察/命令与 MVP 行为一致（手动或集成验证）。
- Core 层无 nonebot import；只有 adapters/onebot/* 与 bootstrap 接触 nonebot。
- `lingxuan-legacy` 仍可运行旧入口（回滚保障）。

## 测试要求
`tests/test_container.py`：构建 Container 不抛异常，关键属性类型正确（用假 env）。
集成 smoke：可选，mock nonebot 启动流程。

## 约束
container/bootstrap 是唯一「知道一切」的地方，允许 import 各层；其它模块不得反向依赖 bootstrap。
