# 灵轩 v2 编码提示词集

本目录把 `docs/architecture-v2.md` 的实施计划拆成一系列**细粒度、可独立执行**的编码任务提示词，供逐个复制给 GLM 5.1（或其他编码模型）完成。

## 怎么用

1. 每次开新会话时，先把 `00-common-context.md` 全文作为背景粘贴给模型。
2. 再粘贴一个具体任务文件（如 `phase1/P1-06-*.md`）。
3. 按任务里「前置依赖」标注的顺序推进：先做完依赖任务，再做下游任务。
4. 每个任务都自带「验收标准」与「测试要求」，完成后按其验证。

> 提示：同一个 Phase 内、彼此无依赖的任务可以并行分给不同会话；跨 Phase 一般需按序。

## 目录与执行顺序

### Phase 0 · 骨架（不改行为）
- `phase0/P0-01-scaffold.md` — 依赖与目录脚手架、包结构
- `phase0/P0-02-protocols-messaging.md` — 消息领域类型与 MessageTransport 接口
- `phase0/P0-03-protocols-llm.md` — LLMProvider 接口与 ChatMessage
- `phase0/P0-04-protocols-repositories.md` — 各 Repository 接口与数据类
- `phase0/P0-05-protocols-config.md` — ConfigProvider 接口
- `phase0/P0-06-protocols-plugins-logging-clock.md` — PluginHost/LogSink/Clock 接口
- `phase0/P0-07-core-models.md` — core/models.py 领域值对象与辅助
- `phase0/P0-08-settings-defaults.md` — settings_defaults.py 配置单一事实源
- `phase0/P0-09-test-scaffold.md` — pytest 脚手架与 fakes

### Phase 1 · NoneBot 收敛为 Adapter + 配置全量切 ConfigProvider
- `phase1/P1-01-config-provider.md` — ConfigProvider 实现（env + 内存）
- `phase1/P1-02-core-persona.md` — PersonaService
- `phase1/P1-03-core-prompting.md` — prompt 拼装纯逻辑
- `phase1/P1-04-core-reply-planner.md` — 分段/节奏纯策略
- `phase1/P1-05-adapter-openai.md` — OpenAI LLMProvider 实现
- `phase1/P1-06-core-observation-state.md` — 观察运行时状态对象
- `phase1/P1-07-core-observation-service.md` — ObservationService（含 judge 编排）
- `phase1/P1-08-core-dialogue-service.md` — DialogueService（私聊 + 群 @ 直回）
- `phase1/P1-09-core-admin-commands.md` — AdminCommandService
- `phase1/P1-10-adapter-onebot-mapping.md` — Event ↔ 领域类型映射
- `phase1/P1-11-adapter-onebot-transport.md` — MessageTransport(onebot) 收发
- `phase1/P1-12-adapter-onebot-lifecycle.md` — nonebot 生命周期与注册
- `phase1/P1-13-adapter-logging-clock.md` — 临时 LogSink + SystemClock
- `phase1/P1-14-container-bootstrap.md` — DI 容器与 bootstrap 装配
- `phase1/P1-15-migrate-config-usages.md` — 全量替换直接 import 常量的用法

### Phase 2 · 存储切 SQLite
- `phase2/P2-01-storage-db.md` — async engine/session、PRAGMA、WAL
- `phase2/P2-02-storage-orm.md` — SQLAlchemy 2.0 ORM 映射
- `phase2/P2-03-alembic-init.md` — Alembic 初始化与 0001_init
- `phase2/P2-04-repo-session.md` — SessionRepository(SQLite)
- `phase2/P2-05-repo-user-profile.md` — UserProfileRepository(SQLite)
- `phase2/P2-06-repo-social-graph.md` — SocialGraphRepository(SQLite)
- `phase2/P2-07-repo-config-audit-plugin-admin.md` — 其余 Repository(SQLite)
- `phase2/P2-08-core-memory-service.md` — MemoryService 切 Repository
- `phase2/P2-09-core-user-memory-service.md` — UserMemoryService 切 Repository
- `phase2/P2-10-wire-repos.md` — 容器装配 SQLite 实现

### Phase 3 · 数据迁移与备份
- `phase3/P3-01-cli-framework.md` — CLI 框架（run 等子命令）
- `phase3/P3-02-migrate-memory.md` — JSON→DB 一次性迁移
- `phase3/P3-03-backup-restore.md` — 备份/恢复
- `phase3/P3-04-auto-migrate-bootstrap.md` — 启动自动迁移流程

### Phase 4 · 管理端 P0
- `phase4/P4-01-log-sink.md` — 结构化 LogSink（ring buffer + 订阅）
- `phase4/P4-02-admin-app-skeleton.md` — FastAPI 子应用与独立端口
- `phase4/P4-03-auth-jwt-rbac.md` — JWT 认证 + RBAC + 首登改密
- `phase4/P4-04-api-config.md` — 配置读写 API（脱敏/热更新）
- `phase4/P4-05-api-status.md` — 状态 API + LLM 探测
- `phase4/P4-06-api-logs-ws.md` — 日志历史 API + WS 日志/状态流
- `phase4/P4-07-spa-scaffold.md` — React/Vite/TS 骨架 + 鉴权前端
- `phase4/P4-08-spa-config-status.md` — 配置页 + 状态页
- `phase4/P4-09-spa-logs.md` — 日志实时页

### Phase 5 · 插件系统 + 管理端 P1
- `phase5/P5-01-plugin-host.md` — PluginHost 注册表与分发
- `phase5/P5-02-plugin-loader.md` — 发现/加载（内置 + entry_points）
- `phase5/P5-03-hook-integration.md` — Core 各 Hook 插入点接入
- `phase5/P5-04-group-entities-plugin.md` — group_entities 改内置插件
- `phase5/P5-05-api-memory-data.md` — 会话/用户/社会图/导入导出 API
- `phase5/P5-06-api-plugins-audit.md` — 插件管理 + 审计 API
- `phase5/P5-07-spa-data-plugins.md` — 数据管理页 + 插件管理页

### Phase 6+（可选，本集暂不展开）
受限终端、文件管理、PostgreSQL 适配、插件沙箱——已纳入 **v3 规划**，见 [`docs/architecture-v3.md`](../architecture-v3.md) 与 [`docs/prompts-v3/README.md`](../prompts-v3/README.md)。

## v3 后续

v2 提示词集（本目录 Phase 0–5）完成后，v3 任务将拆至 `docs/prompts-v3/`。请先阅读 `docs/architecture-v3.md` 了解 Phase 0–6+ 范围与优先级。

## 约定
- 所有任务共享 `00-common-context.md` 的架构规则与 MVP 事实，任务文件里不再重复长背景。
- 冲突时以任务文件的显式要求为准，其次是 `docs/architecture-v2.md`。
