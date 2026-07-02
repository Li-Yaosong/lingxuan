# 任务：为「灵轩」QQ AI 机器人制定 v2 分层架构与分阶段实施计划

你是一位资深 Python 后端架构师，擅长领域驱动设计、可演进单体（modular monolith）、以及运维型 Web 管理台设计。请基于我提供的代码仓库，输出一份**可落地、可分期合并、可回滚**的架构方案。**本任务只产出设计与计划，不要写实现代码。**

---

## 一、项目背景

**灵轩**是一个**自研对话引擎**的拟人化 QQ 助手，不是简单的 LLM 套壳。大模型仅作生成后端；何时说话、记住什么、怎么回，均由自研核心逻辑决定。

### 当前技术栈（MVP 已跑通）

| 层级 | 技术 |
|------|------|
| QQ 协议 | NapCat（OneBot v11 反向 WebSocket，外部黑盒） |
| 机器人框架 | NoneBot2 + nonebot-adapter-onebot |
| Web 驱动 | FastAPI（NoneBot driver `~fastapi`，默认 8080） |
| 大模型 | OpenAI 兼容 API（默认 DeepSeek） |
| 语言 | Python ≥ 3.10 |
| 存储 | 本地 JSON 文件（`data/memory/`） |
| 配置 | `.env` + `config.py` 模块级全局常量 |
| 管理 | QQ 内 `/灵轩` 文本命令（`admin.py`），无自研 Web 管理端 |

### 数据流（现状）

```
QQ NT ←→ NapCat ←→ ws://127.0.0.1:8080/onebot/v11/ws ←→ NoneBot2 ←→ handlers ←→ 业务模块
```

### 现有模块清单（`src/lingxuan/`）

| 文件 | 职责 |
|------|------|
| `bot.py` | NoneBot 入口、adapter 注册、生命周期钩子 |
| `config.py` | 环境变量加载、Settings dataclass、模块级导出常量 |
| `startup.py` | 启动/关闭检查 |
| `handlers/private.py` | 私聊消息处理 |
| `handlers/group.py` | 群聊 @ 直回、观察调度、命令路由（高度耦合） |
| `group_observer.py` | 群观察缓冲、防抖、冷却、judge 调度 |
| `group_entities.py` | 群成员昵称与关系学习 |
| `llm.py` | LLM 调用、上下文拼装、judge、摘要（依赖 nonebot.logger） |
| `persona.py` | 人设 system prompt |
| `memory.py` | 会话级对话记忆（JSON 文件） |
| `user_memory.py` | 跨会话用户档案、社会关系图、认知整合 |
| `message_chunk.py` | 流式分段发送、段间延迟 |
| `admin.py` | QQ 管理员命令（status、重置记忆等） |

### 已验证的核心业务能力（v2 必须保留，不可回归）

1. **私聊**：直接对话，注入用户人际记忆，超长自动摘要
2. **群聊 @ 回复**：流式多气泡分段，首条 @ 目标，段间随机延迟
3. **群聊被动观察**：规则短路 → LLM judge → 冷却/防抖协同
4. **三层记忆**：会话记忆 / 用户档案 / 社会关系图
5. **群实体学习**：昵称↔QQ、人物介绍关系
6. **可配置人设**：环境变量覆盖
7. **管理员命令**：status、reset_memory、user_memory、observe 等

### 已知架构问题（请在你的方案中逐一回应）

1. **handlers 与框架强耦合**：`handlers/group.py` 直接依赖 NoneBot 事件类型、Bot 对象，并串联 10+ 业务模块
2. **全局配置单例**：`config.py` 导出 `BOT_NAME`、`ENABLE_*` 等模块级常量，难以测试与热更新
3. **存储无抽象**：`memory.py` / `user_memory.py` 直接读写 JSON 路径，无 Repository 接口
4. **LLM 层渗透**：`llm.py` 同时承担 provider 调用、prompt 拼装、业务编排
5. **观察引擎状态内聚在模块级**：`group_observer.py` 全局可变状态，无清晰生命周期边界
6. **管理面分裂**：运维能力仅在 QQ 命令，无法 Web 配置/看日志/管数据
7. **无插件扩展点**：功能全在单体模块，无法安全扩展
8. **日志非结构化**：依赖 nonebot 默认日志，无管理端可消费的日志 API

---

## 二、v2 架构目标

在**不推翻 MVP 业务能力**的前提下，重构为**四层解耦**的可演进单体：

```
┌─────────────────────────────────────────────────────┐
│  Bootstrap / Entry（进程启动、DI、路由、生命周期）      │
├─────────────────────────────────────────────────────┤
│  Adapter（OneBot/NoneBot、OpenAI、Storage、Clock）    │
├─────────────────────────────────────────────────────┤
│  Protocol（领域事件、插件契约、管理 API schema）       │
├─────────────────────────────────────────────────────┤
│  Domain / Core（对话调度、观察、记忆、人设、发送策略）  │
└─────────────────────────────────────────────────────┘
```

### 分层语义（请严格按此设计，勿混淆）

| 层 | 职责 | 依赖规则 |
|----|------|----------|
| **Domain/Core** | 纯业务规则与用例编排 | **零依赖** NoneBot、OneBot 类型、FastAPI、具体存储实现 |
| **Protocol** | 抽象接口（Protocol/ABC）、领域事件、API schema、插件 Hook 定义 | 仅依赖标准库 + 类型注解 |
| **Adapter** | 接口的具体实现：OneBot 消息进出、OpenAI client、数据库 Repository | 依赖 Core 定义的接口 |
| **Bootstrap/Entry** | `main()`、依赖注入容器、挂载管理端路由、注册 adapter | 组装各层，是唯一「知道一切」的地方 |

### 核心原则

- Core 通过 `InboundMessage` / `OutboundMessage` 等领域类型与外部世界交互，不出现 `GroupMessageEvent`
- NoneBot **保留为 Adapter**，不在 Core 中 `import nonebot`
- NapCat 仍为外部黑盒，不重写 QQ 协议
- 个人非商业、**单机部署**为主；不引入微服务 / K8s
- `.env` 配置项**向后兼容**（可 deprecate，不可无声删除）

---

## 三、自研 Web 管理端需求

管理端是**灵轩自身运维台**，不是 NapCat WebUI 的替代品。

### 功能优先级

| 优先级 | 功能 | 说明 |
|--------|------|------|
| **P0** | 登录鉴权 | 初始 bootstrap token / 密码，首次登录强制改密 |
| **P0** | 运行时配置 | 查看与修改配置（对应现有 `.env` 项），持久化 + 可选热加载 |
| **P0** | 服务状态 | Bot 连接状态、LLM 可达性、功能开关、记忆统计 |
| **P0** | 结构化日志 | 实时 tail + 级别过滤 + 关键词搜索（WebSocket 推送） |
| **P1** | 记忆/数据管理 | 浏览会话记忆、用户档案、社会关系图；导出/重置（对齐现有 admin 命令） |
| **P1** | 插件管理 | 启用/禁用、配置插件、查看 Hook 注册表 |
| **P2** | 受限终端 | 本机 admin only；**禁止任意 shell**；白名单命令或只读 PTY；全量审计 |
| **P2** | 文件管理 | 限定 `DATA_ROOT` sandbox；防路径穿越；上传/下载/删除需确认 |

### 管理端技术栈偏好（可论证调整）

- 后端 API：FastAPI（与 NoneBot 同进程或独立子应用挂载）
- 前端：**React + Vite + TypeScript**（现代化 SPA）
- 实时：WebSocket（日志流、状态推送）
- 认证：JWT 或 secure session cookie

---

## 四、插件系统设计边界

### 首期范围（务实）

- Python `entry_points` 或内置 Hook 注册表
- Hook 类型示例：`on_inbound_message`、`on_before_reply`、`on_after_reply`、`on_memory_extract`、`on_config_change`
- 插件配置：每个插件独立 JSON/YAML 配置段
- **同进程、无沙箱**（明确记录安全风险与后续演进路径）

### 明确不做（除非单独论证）

- 远程插件市场
- 任意代码热加载
- 子进程/WASM 沙箱（标为 Phase 4+ 可选）

---

## 五、数据存储设计约束

> **维护者明确要求：v2 采用数据库存储，不再以 JSON 文件作为运行时持久化方案。**

### 技术选型（请按此方向设计，可论证微调）

| 项 | 要求 |
|----|------|
| **默认引擎** | **SQLite**（单文件 `data/lingxuan.db`，适合个人单机部署） |
| **访问层** | Repository 抽象 + **SQLAlchemy 2.0**（async 优先） |
| **迁移** | **Alembic** 管理 schema 版本 |
| **JSON 定位** | 仅用于 MVP 数据**一次性导入**与**导出备份**，不作为长期读写路径 |
| **远期可选** | PostgreSQL（多机/远程部署时）；向量列或 pgvector（仅当明确需要 RAG 时） |

### 演进路径

```
Phase 1: 定义 Repository 接口 + 设计 ER / 表结构 + Alembic 初始迁移
Phase 2: 实现 SQLite Repository，Core 全面切到数据库读写
Phase 3: 提供 JSON → DB 一次性迁移 CLI（`lingxuan migrate-memory`）
Phase 4+: 管理端数据浏览/导出/备份基于 DB 查询实现
```

**禁止**：长期保留 JSON 与 DB 双写；禁止 Core 层直接 `open()` 文件读写业务数据。

### 需覆盖的数据域（全部入库）

| 数据域 | 现状（JSON） | v2 要求（数据库） |
|--------|-------------|------------------|
| 会话记忆 | `data/memory/{session_id}.json` | `sessions` + `messages` 表（或 JSON 列存 history，需论证） |
| 会话摘要 | 存在 session JSON 内 | `sessions.summary` 或独立 `session_summaries` |
| 用户档案 | `data/memory/users/{uid}.json` | `user_profiles` + `user_facts`（事实条数可索引/截断） |
| 社会关系图 | `data/memory/social_graph.json` | `social_edges` / `nickname_mappings` 等关系表 |
| 群实体学习 | 散落在 session meta / social | 可归入关系表或 `group_entities` |
| 运行时配置 | `.env` | `settings` 表（key-value 或分组 JSON 列）；`.env` 仅作首次 bootstrap 默认值 |
| 审计日志 | 无 | `audit_logs`（管理端操作、终端命令、配置变更） |
| 插件配置 | 无 | `plugin_configs` 表 |
| 管理端用户 | 无 | `admin_users` + 角色/权限 |

### 必须回答的问题

- 完整 **ER 图** 与表结构（含索引、外键、软删除策略）
- `messages` 等大表的分页查询与 memory_window 裁剪策略
- **JSON → DB 迁移 CLI** 设计：幂等、可 dry-run、迁移报告
- 备份/恢复：SQLite 文件快照 + 可选 SQL/JSON 导出
- 与现有 admin 命令（`reset_memory`、`reset_user_memory` 等）的 SQL 语义对齐
- 事务边界：记忆写入 + 用户档案更新是否同一事务
- 连接管理：单进程 async SQLite（`aiosqlite`）的连接池与 WAL 模式配置

---

## 六、安全设计（必须写入方案，不可省略）

| 领域 | 要求 |
|------|------|
| 网络 | 管理端默认 `127.0.0.1`；生产通过反向代理 + TLS |
| 认证 | 禁止默认弱密码长期有效；密钥/API Key 展示脱敏 |
| 授权 | RBAC：至少 `admin` / `readonly` |
| 终端 | 白名单或只读；每次操作写审计日志 |
| 文件 API | `DATA_ROOT` 沙箱；拒绝 `..` 路径穿越 |
| CSRF/XSS | 管理端 SPA 标准防护 |
| 密钥存储 | 环境变量优先；持久化配置中加密 at-rest |
| LLM 数据 | 明确哪些记忆字段会进入 prompt（隐私告知） |

---

## 七、约束与禁止项

### 约束

- Python 3.10+，保持 `pyproject.toml` + hatchling 打包
- 单进程部署为主（NoneBot + 管理 API 可同进程）
- 保持现有 NapCat 对接方式（反向 WS）
- 参考架构思路时可提及 AstrBot、Koishi、LangBot，但**不照搬**

### 禁止

- 不要写实现代码
- 不要设计微服务拆分
- 不要一次性详设所有 P2 功能（只写接口预留与风险）
- 不要推荐无充分理由的重型框架（需给出轻量替代方案）
- 不要删除或静默废弃现有 `.env` 配置项

---

## 八、请按以下结构输出交付物

### 1. 执行摘要（1 页以内）

一段话说明 v2 要解决什么、整体策略、预计工期量级。

### 2. 现状问题分析

对照上文「已知架构问题」逐条展开，补充你从代码中发现的额外耦合点（附文件路径）。

### 3. 目标架构

- **分层架构图**（Mermaid）
- **消息处理时序图**（私聊一条、群聊观察一条）
- **管理端通信图**（REST + WebSocket）

### 4. 目录结构提案

给出重构后 `src/lingxuan/`（或新包名）的完整目录树，每个目录一句话说明职责。

示例起点（可调整）：

```
src/lingxuan/
├── core/           # 领域模型、用例、服务
├── protocols/      # 抽象接口、事件、schema
├── adapters/       # onebot, openai, storage, clock
├── admin/          # 管理 API + 前端构建产物
├── plugins/        # 内置插件与加载器
└── bootstrap.py    # 入口组装
```

### 5. 核心抽象接口清单

用 Python `Protocol` 或 ABC 写出关键接口的**方法签名草案**（不需要实现），至少包括：

- `MessageTransport`（收/发消息）
- `LLMProvider`（chat / stream / judge）
- `SessionRepository` / `UserProfileRepository` / `SocialGraphRepository`
- `ConfigProvider`（读/写/订阅变更）
- `PluginHost`（注册与分发 Hook）
- `AuditLogger`

### 6. 领域模型草案

`InboundMessage`、`OutboundMessage`、`SessionId`、`ReplyPlan`、`ObservationContext` 等核心类型的字段定义。

### 7. 管理 API 草案

- REST 路由表（方法、路径、用途、权限）
- WebSocket 事件表（事件名、payload、方向）
- 与 P0/P1/P2 的对应关系

### 8. 数据模型

- 完整 **ER 图**（Mermaid erDiagram）
- **SQLite 表结构**（字段类型、索引、约束）
- **Alembic 迁移**版本策略（初始 migration + 后续变更流程）
- MVP JSON → DB **一次性迁移**方案（CLI 命令、幂等性、回滚）
- 关键查询示例（按 session 拉 history、按 user 拉 facts、社会关系图遍历）

### 9. 插件系统设计

- Hook 清单与调用时机
- 插件生命周期
- 配置 schema 约定
- 示例：如何将现有 `group_entities` 抽为内置插件

### 10. 安全设计摘要

按上文「安全设计」表格展开为可执行检查清单。

### 11. 分阶段迁移计划

| 阶段 | 目标 | 交付物 | 风险 | 回滚策略 |
|------|------|--------|------|----------|
| Phase 0 | ... | ... | ... | ... |
| Phase 1 | ... | ... | ... | ... |
| Phase 2 | ... | ... | ... | ... |
| Phase 3 | ... | ... | ... | ... |

要求：
- 每阶段可独立合并到 main
- 每阶段结束 MVP 功能仍可用
- 标明预估工作量（人天量级即可）

### 12. 关键取舍与风险

至少讨论：
- NoneBot 保留 vs 自写 OneBot WS 客户端
- 同进程管理端 vs 独立进程
- MVP JSON 数据迁移失败时的降级/回滚策略（非双写）
- 插件无沙箱的安全接受度

### 13. 测试策略

- 各层如何单测（Core 无 IO）
- 集成测试边界
- 管理端 E2E 范围

### 14. 开放问题

列出需要我（项目维护者）决策的 3–5 个问题，并给出你的推荐选项。

---

## 九、输出风格要求

- 使用**简体中文**
- 技术术语保留英文（Repository、Hook、JWT 等）
- 图表用 Mermaid
- 接口用 Python 类型注解
- 结论要**具体可执行**，避免空泛的「采用最佳实践」
- 对不确定的设计明确标注「待决策」并给出 2 个选项对比
- 总篇幅建议 8000–15000 字（详尽但不注水）

---

## 使用说明

1. **全选本文件内容**，粘贴到 Claude Opus 对话（或作为附件上传）。
2. **必须附上源码**：至少 `src/lingxuan/`、`README.md`、`.env.example`、`pyproject.toml`。
3. 可选开场白：「请严格按本文档第八节 14 项交付物输出方案；存储层按第五节要求以 SQLite 数据库为默认实现。」

---
