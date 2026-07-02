# P1-09 · core/admin_commands.py — AdminCommandService

## 目标
把 MVP `admin.py` 迁为 Core 的 AdminCommandService，命令解析与执行不变，但数据访问改为经注入的服务/Repository 接口（Phase 1 可先注入封装旧逻辑的适配，Phase 2 换 Repository）。

## 前置依赖
- P0-05（config）、记忆与用户记忆服务接口、P1-07（observation，用于 observe 命令）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/admin_commands.py`

## 详细规格
- `CommandContext` dataclass：`user_id, session_id: SessionId, is_group=False, group_id=None, nickname=""`（对齐 MVP）。
- `parse_command(text, bot_name) -> tuple[str, list[str]] | None`：前缀 `/{bot_name} `；空 rest → `("", [])`；中文别名映射（重置记忆→reset_memory、状态→status、观察→observe、用户记忆→user_memory、重置用户记忆→reset_user_memory）。bot_name 由 config 读取传入（不再用模块级常量）。
- 类 `AdminCommandService`，注入 `config`、`memory`、`user_memory`、`observation`。
- `async def run(cmd, args, ctx) -> str`：分派到各命令，返回文本回复。命令集与 MVP 对齐：
  - `""`：帮助列表。
  - `status`/状态：模型、功能开关（`is_feature_enabled`）、记忆条数、用户档案数；群聊附观察状态。
  - `reset_memory`/重置记忆：scope 默认 session / `all|全部` / `users|用户` / `user <QQ>`。
  - `user_memory`/用户记忆：无 args 查自己或列表；有 args 查指定 QQ。
  - `reset_user_memory`/重置用户记忆：无 args 清自己；`all`/`graph`/指定 QQ。
  - `observe`/观察：仅群（需 ctx.is_group && group_id）：缓冲 + 最近 judge。
- 数据访问通过注入服务：如 `memory.count/clear`、`user_memory.list/load/clear/clear_graph`、`observation.format_observation/state`。避免直接文件/DB 访问。

## 验收标准
- `parse_command` 别名与前缀行为与 MVP 一致。
- 各命令返回文本与 MVP 语义一致（内容可措辞一致，最好逐字对齐）。
- 无 nonebot 依赖。

## 测试要求
`tests/core/test_admin_commands.py`（fakes）：
- parse_command：命中/未命中/别名/空 rest。
- status/reset_memory(各 scope)/user_memory/reset_user_memory/observe 的调用与返回。

## 约束
Core 层，无框架依赖。权限（是否 admin）由调用方（DialogueService）在调用前判定，本服务不做鉴权。
