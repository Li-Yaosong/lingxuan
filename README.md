# 灵轩 — AI QQ 聊天机器人

灵轩是一个**从零自研对话引擎**的拟人化 QQ 助手，而非简单的 LLM 套壳插件。项目在 NapCat + NoneBot2 之上独立实现了群聊行为调度、记忆体系与回复策略等核心逻辑；大模型仅作为生成后端，何时说话、记住什么、怎么回，均由自研模块决定。

**自研核心能力：**

- **群聊观察引擎** — 规则短路、LLM judge、防抖与冷却协同调度，自主判断何时在群里插话
- **双层记忆系统** — 会话级历史摘要 + 跨群用户档案与社会关系图，支持认知总结持续整合
- **拟真回复策略** — 流式多气泡分段发送，段间随机延迟，首条 @ 目标用户
- **群实体学习** — 从聊天中自动积累昵称↔QQ 映射与人物介绍关系，注入后续对话上下文
- **可配置人设** — 内置完整性格与群聊差异化 prompt，支持通过环境变量完全覆盖

底层接入 NapCat（OneBot v11）与 OpenAI 兼容 API，但上述行为逻辑均为 `src/lingxuan/` 中的原创实现。

## 功能

### 私聊

- 直接给机器人发消息即可对话，无需额外命令
- 自动注入用户人际记忆（称呼、印象、事实、认知总结）
- 对话超过记忆窗口后，由 LLM 自动摘要压缩历史，保留长期上下文

### 群聊 @ 回复

- @ 机器人，或回复机器人的消息，会立即触发回复
- 回复采用流式分段发送：首条 @ 目标用户，后续以多条气泡连发，段间带有随机延迟，模拟真人打字节奏

### 群聊被动观察

除了 @ 直回，灵轩还会「看着群聊」，在合适时机主动插话，行为更接近真人群友：

1. **规则短路**（不调用 judge，直接回复）：消息 @ 了机器人、回复了机器人、叫机器人名字、明显的求助/诉苦、介绍他人、以及机器人回复后的跟进对话等
2. **LLM judge**：其余情况由轻量模型判断当前是否适合插话（yes/no）
3. **冷却**：被动回复后默认 30 秒内不再插话（@ 和跟进对话可绕过冷却）
4. **防抖**：群消息缓冲 1.5 秒后统一判断，避免对每条消息都反应

若不需要被动插话，可在 `.env` 中设置 `ENABLE_GROUP_OBSERVE=false`。

### 记忆与人设

**人设**：默认性格为温柔、偶尔调皮的数字生命，口语化、不自称 AI。完整默认人设见 [src/lingxuan/persona.py](src/lingxuan/persona.py)，可通过环境变量 `BOT_PERSONA` 覆盖。

**三层记忆**：

| 类型 | 存储位置 | 说明 |
|------|----------|------|
| 会话记忆 | `data/memory/{session_id}.json` | 私聊 `private_{uid}` / 群聊 `group_{gid}` 的对话历史与摘要 |
| 用户档案 | `data/memory/users/{uid}.json` | 跨会话的用户称呼、印象、事实、认知总结 |
| 社会关系图 | `data/memory/social_graph.json` | 群聊中学习的昵称↔QQ 映射与介绍关系 |

群聊中还会从消息内容学习成员昵称与人物关系，用于后续对话的上下文理解。

### 管理员命令

命令前缀为 `/{BOT_NAME} `（默认为 `/灵轩 `），仅 `BOT_ADMINS` 中配置的 QQ 号可用，私聊和群聊均支持。

| 命令 | 说明 |
|------|------|
| `status` / `状态` | 查看模型、功能开关、记忆条数、观察状态等 |
| `reset_memory` / `重置记忆` | 清空当前会话记忆；加 `all` 同时清空所有用户档案 |
| `user_memory` / `用户记忆` | 查看用户档案；可指定 QQ 号，如 `/灵轩 用户记忆 123456` |
| `reset_user_memory` / `重置用户记忆` | 清空档案；`all` 清空全部，`graph` 仅清空社会关系图 |
| `observe` / `观察` | 仅群聊：查看观察缓冲与最近 judge 结果 |

示例：

```
/灵轩 状态
/灵轩 重置记忆
/灵轩 重置记忆 all
/灵轩 用户记忆
/灵轩 重置用户记忆 graph
```

## 快速开始

### 1. 环境要求

- Python ≥ 3.10
- 本机安装 QQ NT 版

### 2. 配置灵轩

```bash
# 复制环境变量模板
cp .env.example .env   # Windows 可手动复制

# 编辑 .env，至少填入以下两项：
# OPENAI_API_KEY=your_api_key_here
# BOT_ADMINS=你的QQ号

# 安装依赖
pip install -e .
```

### 3. 安装并配置 NapCat

按照 [docs/napcat.md](docs/napcat.md) 安装 NapCat，配置反向 WebSocket。关键配置参考 [docs/onebot11.lingxuan.example.json](docs/onebot11.lingxuan.example.json)：

- 类型：反向 WebSocket 客户端（`websocketClients`）
- 地址：`ws://127.0.0.1:8080/onebot/v11/ws`
- `reportSelfMessage`：`false`（避免机器人自身消息干扰群观察）

### 4. 启动

**先启动灵轩，再启动 NapCat：**

```bash
python -m lingxuan.bot
# 或安装后使用 CLI：
lingxuan
```

然后运行 NapCat（`napcat.bat` 或 `launcher.bat`），扫码登录 QQ。

### 5. 验证

灵轩控制台应出现类似日志：

```
[INFO] nonebot | OneBot v11 | Bot <QQ号> connected
```

此时私聊发消息，或在群内 @ 机器人，即可触发回复。

## 配置说明

完整变量列表见 [.env.example](.env.example)，以下为分组摘要。默认值与 [src/lingxuan/config.py](src/lingxuan/config.py) 保持一致。

### 必填

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | LLM API Key，未配置则无法生成回复 |
| `BOT_ADMINS` | 管理员 QQ 号，逗号分隔；未配置则管理员命令不可用 |

### API 与机器人

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DRIVER` | `~fastapi` | NoneBot 驱动，默认监听 8080 端口 |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | API 地址，可换任意兼容服务 |
| `OPENAI_MODEL` | `deepseek-chat` | 模型名称 |
| `BOT_NAME` | `灵轩` | 机器人名称，影响 @ 检测与命令前缀 |
| `BOT_PERSONA` | 空 | 自定义人设，空则使用内置默认 |
| `MEMORY_WINDOW` | `20` | 会话记忆窗口（轮） |

### 群聊观察

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GROUP_OBSERVE_WINDOW` | `20` | 观察缓冲条数 |
| `GROUP_OBSERVE_DELAY` | `1.5` | 防抖秒数 |
| `GROUP_OBSERVE_COOLDOWN` | `30` | 被动回复后冷却秒数 |
| `GROUP_BURST_MERGE_WINDOW` | `10` | 同人连发合并窗口（秒） |
| `GROUP_FOLLOWUP_WINDOW` | `60` | 机器人回复后的跟进窗口（秒） |
| `GROUP_CHAT_CONTEXT` | `6` | 群聊 LLM 上下文条数 |
| `GROUP_CHAT_MAX_TOKENS` | `512` | 群聊最大生成 token |

### 流式分段发送

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_STREAM_CHUNK` | `true` | 是否启用流式分段发送 |
| `GROUP_MSG_CHUNK_MAX` | `35` | 单段最大字符数 |
| `GROUP_MSG_CHUNK_MIN` | `6` | 最短合并长度 |
| `GROUP_MSG_CHUNK_LIMIT` | `6` | 最多发送段数 |
| `GROUP_CHUNK_DELAY_MIN` | `0.4` | 段间最小延迟（秒） |
| `GROUP_CHUNK_DELAY_MAX` | `1.2` | 段间最大延迟（秒） |

### 功能开关

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_PRIVATE_CHAT` | `true` | 私聊 |
| `ENABLE_GROUP_CHAT` | `true` | 群聊 |
| `ENABLE_GROUP_OBSERVE` | `true` | 群聊被动观察 |
| `ENABLE_MEMORY_SUMMARY` | `true` | 超长会话自动摘要 |
| `ENABLE_USER_MEMORY` | `true` | 用户人际记忆 |
| `ENABLE_USER_COGNITION_REFINE` | `true` | 用户认知总结整合 |

### 用户记忆

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `USER_MEMORY_BURST_MERGE` | `3.0` | 记忆抽取防抖合并窗口（秒） |
| `USER_MEMORY_MAX_FACTS` | `30` | 单用户最大事实条数 |
| `USER_COGNITION_REFINE_INTERVAL` | `5` | 每 N 次互动触发认知整合 |
| `USER_COGNITION_REFINE_DELAY` | `2.0` | 认知整合延迟（秒） |
| `USER_COGNITION_MAX_CHARS` | `150` | 认知总结最大字数 |

## 项目结构

```
qq-bot/
├── src/lingxuan/           # 核心业务代码
│   ├── bot.py              # NoneBot 入口
│   ├── config.py           # 环境变量加载与校验
│   ├── startup.py          # 启动/关闭检查
│   ├── handlers/
│   │   ├── private.py      # 私聊消息处理
│   │   └── group.py        # 群聊消息、@ 直回、观察调度
│   ├── llm.py              # 大模型调用与上下文拼装
│   ├── persona.py          # 人设 prompt
│   ├── memory.py           # 会话级对话记忆
│   ├── user_memory.py      # 跨会话用户人际记忆
│   ├── group_observer.py   # 群聊观察缓冲、防抖、冷却、judge
│   ├── group_entities.py   # 群成员昵称与关系学习
│   ├── message_chunk.py    # 流式分段发送
│   └── admin.py            # 管理员命令
├── docs/
│   ├── napcat.md           # NapCat 对接详解
│   ├── onebot11.lingxuan.example.json
│   └── webui.lingxuan.example.json
├── .env.example            # 环境变量模板
├── pyproject.toml          # 包定义（lingxuan v0.1.0）
├── data/                   # 运行时数据（git 忽略）
└── napcat/                 # NapCat 解压目录（自行下载，git 忽略）
```

## 技术栈

| 层级 | 技术 |
|------|------|
| QQ 协议 | [NapCat](https://github.com/NapNeko/NapCatQQ)（OneBot v11 反向 WebSocket） |
| 机器人框架 | [NoneBot2](https://nonebot.dev/) + nonebot-adapter-onebot |
| 大模型 | OpenAI 兼容 API（默认 DeepSeek） |
| 语言 | Python ≥ 3.10 |

## 架构

```
QQ NT 客户端  ←→  NapCat（官方 Release，本机黑盒运行）
                      ↓ 反向 WS
              ws://127.0.0.1:8080/onebot/v11/ws
                      ↓
              灵轩（NoneBot2 + onebot-adapter-onebot）
```

本仓库只包含灵轩的 Python 业务代码（`src/lingxuan/`），不包含 NapCat 源码。NapCat 需自行从[官方 Release](https://github.com/NapNeko/NapCatQQ/releases)下载，对接细节见 [docs/napcat.md](docs/napcat.md)。

## 数据与隐私

- 所有对话记忆、用户档案、社会关系图均以 JSON 文件存储在本地 `data/memory/` 目录
- 数据不会上传到第三方服务，但发送给 LLM API 的 prompt 中会包含相关上下文
- 可通过管理员命令重置记忆，或直接删除 `data/` 目录

## 许可与合规

- **灵轩**（`src/lingxuan/`）为独立 Python 项目，仅消费标准 OneBot v11 协议
- **NapCat** 采用限制性许可：禁止商业使用、未经授权不得修改或再分发源码。详见 [docs/napcat.md](docs/napcat.md) 中的许可说明
- 请遵守当地法律法规，仅用于个人非商业场景

## 相关链接

- [NapCat 官方文档](https://napneko.github.io/)
- [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases)
- [NoneBot2 文档](https://nonebot.dev/)
