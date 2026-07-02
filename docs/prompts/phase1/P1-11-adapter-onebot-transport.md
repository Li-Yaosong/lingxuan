# P1-11 · adapters/onebot/transport.py — MessageTransport 实现

## 目标
实现 `MessageTransport`：注册 nonebot matcher 接收消息（转 InboundMessage 交给 Core），并实现发送（含流式多气泡 + 首段 @ + 段间延迟）。这里承接 MVP `handlers/*` 的注册与 `message_chunk.py` 的实际发送。

## 前置依赖
- P0-02（MessageTransport 接口）、P1-10（mapping）、P1-04（reply_planner 产出的 OutboundChunk）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/onebot/transport.py`

## 详细规格
类 `OneBotTransport(MessageTransport)`，注入 `config`、`mapping`（或直接用 mapping 函数）、`log`。

- `start(on_inbound)`：注册两个 matcher（对齐 MVP 优先级/block）：
  - 私聊：`nonebot.on_type(PrivateMessageEvent, priority=10, block=True)`；handler 内 `to_inbound_private` → `await on_inbound(inbound)`。
  - 群聊：`nonebot.on_type(GroupMessageEvent, priority=20, block=False)`；handler 内取 `self_id` → `to_inbound_group` → `await on_inbound(inbound)`。
  - 保存 `on_inbound` 引用。
- `resolve_self_id()`：从 `nonebot.get_bots()` 取当前 bot 的 self_id。
- 发送：
  - `send(out)`：单/多段发送。私聊用 `bot.send_private_msg`；群聊首段（若 at）`MessageSegment.at(uid) + " " + text`，逐段 `bot.send_group_msg`，段间按 `chunk.delay_before` `asyncio.sleep`。
  - `send_stream(target, chunks)`：消费 `AsyncIterator[OutboundChunk]`，逐段发送并按 `delay_before` sleep；返回拼接的完整文本（供 Core 写记忆）。
  - 取 bot 实例：优先用 matcher 注入的 bot；观察路径无 event 时用 `nonebot.get_bots()`（统一封装一个 `_current_bot()`，消除 MVP 双路径取 bot 不一致）。
- 发送目标解析：`target.session_id.kind` 决定 private/group；`peer_id` 为 uid/gid。

> 段间延迟已由 ReplyPlanner 写入 `chunk.delay_before`，transport 只需 sleep 该值（不要再自己随机，避免双重延迟）。

## 验收标准
- 私聊与群聊消息能被接收并转为 InboundMessage 交给 Core。
- 群回复：首段 @ 目标、多气泡、段间延迟；与 MVP 观感一致。
- 只此文件与 mapping/lifecycle 依赖 nonebot onebot 发送 API。

## 测试要求
- transport 发送逻辑可用 mock bot 断言 `send_group_msg` 调用序列（首段含 at、段数、sleep 调用）。真实 matcher 注册可在集成阶段验证。
- `tests/adapters/test_onebot_transport.py`：mock bot + 构造 OutboundChunk 流，断言调用顺序与内容。

## 约束
Adapter 层，可 import nonebot；不写业务决策；延迟只用 chunk.delay_before。
