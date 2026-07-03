# 共享上下文（所有编码提示词的公共前置）

> 使用方法：把本文件内容作为「系统/背景」粘贴给 GLM 5.1，然后再粘贴某个具体任务提示词（`phaseX/PX-YY-*.md`）。每个任务提示词都假定你已读过本文件。完整架构方案见仓库 `docs/architecture-v2.md`，如有冲突以任务提示词中的显式要求为准。

## 1. 项目是什么

「灵轩」是一个自研对话引擎的拟人化 QQ 助手（不是简单的 LLM 套壳）。MVP 已跑通，现在要重构为 v2 四层解耦架构，并把存储从 JSON 文件迁移到 SQLite。

- 语言：Python ≥ 3.10
- 打包：`pyproject.toml` + hatchling，包名 `lingxuan`，源码在 `src/lingxuan/`
- QQ 协议：NapCat（OneBot v11 反向 WebSocket，外部黑盒，不改）
- 机器人框架：NoneBot2 + nonebot-adapter-onebot（v2 中降级为一个 Adapter）
- LLM：OpenAI 兼容 API（默认 DeepSeek），用 `openai` 官方异步 client
- 管理端（v2 新增）：FastAPI + React/Vite/TS，JWT 认证，独立端口

## 2. v2 四层架构与依赖规则（务必遵守）

```
Bootstrap/Entry  →  组装一切：main()、DI 容器、注册 adapter、挂载 admin、生命周期
Adapter          →  接口的具体实现：onebot、openai、storage、logging、clock；依赖 Protocol
Protocol         →  抽象接口(Protocol/ABC)、领域事件、API schema、插件 Hook；仅依赖标准库+typing
Domain/Core      →  纯业务规则与用例编排；零依赖 NoneBot/OneBot/FastAPI/具体存储实现
```

**依赖方向严格单向**：Bootstrap → (Adapter, Core, Admin, Plugins) → Protocol → 标准库。

铁律：
- **Core 层禁止 import**：`nonebot`、`nonebot.adapters.*`、`fastapi`、`openai`、`sqlalchemy`、`aiosqlite`、以及任何具体存储/网络实现。Core 只依赖 `protocols/` 与标准库。
- Core 通过领域类型（`InboundMessage`/`OutboundMessage`/`ReplyPlan`/`ObservationContext` 等）与外部交互，**不出现** `GroupMessageEvent`/`Bot` 等框架类型。
- 依赖通过构造函数注入（传入实现了 Protocol 的对象），不在 Core 内部直接实例化 Adapter。

## 3. 目标目录结构（v2 完成态）

```
src/lingxuan/
├── bootstrap.py            # 入口 main()：加载配置、构建 DI 容器、注册 adapter、挂载 admin、启动
├── container.py            # 轻量 DI 容器/工厂（手写，不引入重型框架）
├── cli.py                  # lingxuan CLI：run / migrate-memory / backup / restore / db upgrade / admin-passwd
├── protocols/              # 抽象层（仅标准库 + typing）
│   ├── messaging.py        # MessageTransport、InboundMessage、OutboundMessage、OutboundChunk、ReplyTarget、ReplyPlan、Actor、SessionId、ObservationEntry、ObservationContext
│   ├── llm.py              # LLMProvider、ChatMessage
│   ├── repositories.py     # SessionRepository / UserProfileRepository / SocialGraphRepository / ConfigRepository / AuditRepository / PluginConfigRepository / AdminUserRepository、及相关数据类
│   ├── config.py           # ConfigProvider
│   ├── plugins.py          # PluginHost、Plugin、HookType、PluginContext、PluginInfo
│   ├── logging.py          # LogSink、LogRecord
│   └── clock.py            # Clock
├── core/                   # 领域/业务（零框架、零 IO 实现）
│   ├── models.py           # 领域值对象/辅助
│   ├── dialogue.py         # DialogueService：私聊 & 群 @ 直回
│   ├── observation.py      # ObservationService：缓冲/防抖/冷却/规则短路/judge 编排
│   ├── observation_state.py# 观察运行时状态对象（取代模块级全局 dict）
│   ├── memory.py           # MemoryService：会话记忆、摘要触发与裁剪
│   ├── user_memory.py      # UserMemoryService：档案、facts、社会图、认知整合
│   ├── persona.py          # PersonaService：system prompt 组装
│   ├── reply_planner.py    # ReplyPlanner：分段/节奏纯策略
│   ├── prompting.py        # prompt 拼装（原 llm.build_context_messages 的纯逻辑）
│   └── admin_commands.py   # AdminCommandService：status/reset_memory/... 用例
├── adapters/
│   ├── onebot/{transport.py, mapping.py, lifecycle.py}
│   ├── openai/provider.py
│   ├── storage/{db.py, orm.py, repositories.py}
│   ├── logging/sink.py
│   └── clock.py
├── admin/                  # 运维台：app.py, auth.py, deps.py, routes/, ws.py, schemas.py, web/(React)
├── plugins/                # host.py, loader.py, builtin/group_entities.py
└── config/defaults.py      # 全部配置项：key/类型/默认值/分组/是否敏感/是否可热更新（单一事实源）
```

## 4. MVP 现状关键事实（重构时必须保持行为一致）

### 4.1 现有模块（`src/lingxuan/`，重构时逻辑迁移来源）
- `config.py`：`Settings` dataclass + `settings` 单例 + 33 个模块级大写常量（v2 移除常量，改 ConfigProvider）。
- `handlers/private.py`：`nonebot.on_type(PrivateMessageEvent, priority=10, block=True)`；私聊对话，非流式 `chat()`。
- `handlers/group.py`：`nonebot.on_type(GroupMessageEvent, priority=20, block=False)`；群 @ 直回（流式）+ 观察调度；管理员命令在 handler 入口判断 `user_id in BOT_ADMINS`。
- `group_observer.py`：7 个模块级全局 dict（`_buffers`/`_debounce_tasks`/`_observe_callbacks`/`_last_observe_len`/`_group_states`/`_group_locks`/`_user_nicknames`）；防抖/冷却/规则短路。
- `llm.py`：`AsyncOpenAI`；`chat`/`chat_stream`/`chat_in_group[_stream]`/`should_reply_in_group`(judge)/`summarize_session`；prompt 拼装 `build_context_messages`。
- `persona.py`：`DEFAULT_PERSONA` + `GROUP_PERSONA_SUFFIX` + `get_system_prompt(is_group)`；`BOT_PERSONA` 非空则完全替换默认，群聊仍追加 suffix。
- `memory.py`：会话记忆 JSON（见 4.2）。
- `user_memory.py`：用户档案 + 社会关系图 JSON（见 4.3）。
- `group_entities.py`：群实体学习编排（无独立存储，委托 memory/user_memory）。
- `message_chunk.py`：流式分段发送，直接调 `bot.send_group_msg`（首段 `MessageSegment.at(user_id)`）。
- `admin.py`：命令解析 `parse_command`（前缀 `/{BOT_NAME} `）+ `run_command`；无 nonebot 依赖。

### 4.2 会话记忆 JSON（`data/memory/{session_id}.json`，session_id = `private_{uid}` / `group_{gid}`）
```json
{ "version": 2,
  "history": [ {"role":"user","content":"[小明]: 你好","user_id":123}, {"role":"assistant","content":"你好呀~"} ],
  "summary": "摘要文本",
  "meta": {"last_active_at":"ISO8601","nickname":"小明","group_id":987,"entities":{"小堞宝":111}} }
```
- 裁剪策略（必须复现）：保存时硬保留 `history[-MEMORY_WINDOW*2:]`（默认 40 条）；摘要成功后 `trim_history_half`（保留后半）。
- 摘要触发：`ENABLE_MEMORY_SUMMARY` 且 `len(history) > MEMORY_WINDOW`；摘要 ≤200 字，失败 fallback 不保存不裁剪。
- 旧格式：纯 list 的旧 history 自动升级为 v2。

### 4.3 用户档案 JSON（`data/memory/users/{uid}.json`）与社会图（`data/memory/social_graph.json`）
用户档案字段：`version, user_id, identity{preferred_name, aliases[], group_cards{gid:card}}, relationship{stage, first_met_at, last_seen_at, interaction_count, last_group_id, seen_in_private, seen_in_group}, facts[]{id,content,category,source_user_id,learned_at,confidence,active,supersedes}, impression, cognition{summary, updated_at, interaction_at_update}`。
- fact 软删除：超 `USER_MEMORY_MAX_FACTS`（30）时按 `learned_at` 升序把最旧的置 `active=False`（不物理删）；identity 变更时旧 identity fact 置 `active=False`；active 且 content 相同不新增。
- 关系阶段 `_compute_stage`：`close`(interaction≥30) / `familiar`(私聊+群聊都见过 或 ≥10) / `acquaintance`(≥3 或有非 identity 的 active fact) / `stranger`。
- 认知整合：`ENABLE_USER_COGNITION_REFINE`，间隔 `USER_COGNITION_REFINE_INTERVAL`(5)，延迟 `USER_COGNITION_REFINE_DELAY`(2s)，输出截断 `USER_COGNITION_MAX_CHARS`(150)。

社会图字段：`version, edges[]{from_user_id,to_user_id,relation,label,evidence,group_id,learned_at}, name_index{name:user_id}`。
- relation 枚举：`introduced_as` / `also_known_as` / `friend_of` / `self_identified_as`。
- 边去重：`(from_user_id, to_user_id, relation, label)` 四元组相同则跳过。

### 4.4 全部配置项（对应 .env，共 32 项，v2 全部保留为 bootstrap 默认值）
`DRIVER=~fastapi, OPENAI_API_KEY, OPENAI_BASE_URL=https://api.deepseek.com/v1, OPENAI_MODEL=deepseek-chat, BOT_NAME=灵轩, BOT_PERSONA=, BOT_ADMINS=(逗号分隔int), MEMORY_WINDOW=20, GROUP_OBSERVE_WINDOW=20, GROUP_OBSERVE_DELAY=1.5, GROUP_OBSERVE_COOLDOWN=30, GROUP_BURST_MERGE_WINDOW=10, GROUP_FOLLOWUP_WINDOW=60, GROUP_CHAT_CONTEXT=6, GROUP_CHAT_MAX_TOKENS=512, ENABLE_STREAM_CHUNK=true, GROUP_MSG_CHUNK_MAX=35, GROUP_MSG_CHUNK_MIN=6, GROUP_MSG_CHUNK_LIMIT=6, GROUP_CHUNK_DELAY_MIN=0.4, GROUP_CHUNK_DELAY_MAX=1.2, ENABLE_PRIVATE_CHAT=true, ENABLE_GROUP_CHAT=true, ENABLE_GROUP_OBSERVE=true, ENABLE_MEMORY_SUMMARY=true, ENABLE_USER_MEMORY=true, USER_MEMORY_BURST_MERGE=3.0, USER_MEMORY_MAX_FACTS=30, ENABLE_USER_COGNITION_REFINE=true, USER_COGNITION_REFINE_INTERVAL=5, USER_COGNITION_REFINE_DELAY=2.0, USER_COGNITION_MAX_CHARS=150`。
v2 新增配置项：`DB_URL=sqlite+aiosqlite:///data/lingxuan.db, DATA_ROOT=./data, AUTO_MIGRATE=true, ADMIN_HOST=127.0.0.1, ADMIN_PORT=8081, SECRET_KEY=(必填,用于JWT签名与配置加密), JWT_ACCESS_TTL=900, JWT_REFRESH_TTL=604800`。

## 5. 已定的关键决策（不要再改动方向）
1. 会话历史用**行表** `session_messages`（非 JSON 列）。
2. 管理端**同进程、独立端口**（默认 `127.0.0.1:8081`）。
3. 认证用 **JWT**（access 短时效 + refresh 可吊销；Bearer header）。
4. **启动自动迁移**：`AUTO_MIGRATE=true` 时自动 `alembic upgrade head` + 首次（DB 空且有旧 JSON）自动 `migrate-memory`，带自动 backup 与失败回滚。
5. 配置在 Phase 1 **一次性全量切 `ConfigProvider`**，移除模块级大写常量，不保留 shim。`.env` 环境变量项全部保留。

## 6. 通用编码约束（每个任务都适用）
- Python 3.10+ 语法，全量类型注解；公共接口用 `typing.Protocol`。
- 遵守分层依赖规则（见第 2 节）。Core 不碰框架/IO 实现。
- 不删除任何现有 `.env` 环境变量项；新增项要有合理默认。
- 保持 MVP 全部业务行为不回退（私聊、群 @ 直回流式多气泡、群被动观察、三层记忆、群实体学习、可配置人设、管理员命令）。
- 代码注释只解释「为什么」，不写复述代码的废话注释。
- 每个任务完成后，提供：新增/修改文件清单、必要的单元测试、以及如何验证（运行命令）。
- 风格：`ruff` 友好；关键模块尽量能通过 `mypy`。
- 除非任务明确要求，不要引入方案未提到的重型依赖。

## 7. 每个任务提示词的固定结构
每个 `PX-YY-*.md` 都包含：目标 / 前置依赖（依赖哪些已完成任务）/ 需创建或修改的文件 / 详细规格 / 验收标准 / 测试要求 / 约束。请严格按「需创建或修改的文件」产出，不要擅自改动其他模块。
