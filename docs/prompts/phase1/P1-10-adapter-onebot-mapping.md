# P1-10 · adapters/onebot/mapping.py — Event ↔ 领域类型映射

## 目标
实现 NoneBot/OneBot 事件与领域类型的双向转换：把 `PrivateMessageEvent`/`GroupMessageEvent` 映射为 `InboundMessage`；把 `OutboundMessage`/`OutboundChunk` 映射为 OneBot 发送参数。这是 nonebot 类型被允许出现的唯一区域之一。

## 前置依赖
- P0-02（领域消息类型）、P0-05（config，用于 BOT_NAME/BOT_ADMINS 判定）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/onebot/mapping.py`

## 详细规格
纯函数集合，输入 nonebot event，输出领域类型（不做业务）：

- `to_inbound_private(event: PrivateMessageEvent, *, config) -> InboundMessage`：
  - `session_id=SessionId("private", user_id)`；`actor=Actor(user_id, nickname, is_admin=user_id in BOT_ADMINS)`。
  - `text` = 纯文本（`event.get_plaintext()` 或去段处理）；`raw_text`=raw。
- `to_inbound_group(event: GroupMessageEvent, *, self_id, config) -> InboundMessage`：
  - `session_id=SessionId("group", group_id)`、`group_id`。
  - `actor`：user_id、群名片/昵称、is_admin、`is_self = (user_id == self_id)`。
  - `at_bot`：`event.to_me` 或 message 段含 at self 或 raw 兜底（对齐 MVP `_is_at_bot`）。
  - `reply_to_bot`：reply 段指向 bot（对齐 MVP 判定）。
  - `at_user_ids`：所有 at 段目标（不含 bot 自身可按 MVP 处理）。
  - `text`：去掉 at 段后的纯文本。
  - `command`：若 `actor.is_admin`，用 `admin_commands.parse_command(text, BOT_NAME)` 预解析填入（可选，也可留给 Core）。
- 发送映射：
  - `outbound_to_segments(out: OutboundMessage) -> list[发送指令]`：首段若 `at_user_id` 非空 → `MessageSegment.at(uid) + " " + text`，其余段纯文本。供 transport 逐段发送。

BOT_ADMINS/BOT_NAME 从注入的 `config` 读取。

## 验收标准
- 群消息 @bot 检测、reply_to_bot、at_user_ids、去 @ 文本与 MVP 一致。
- is_admin/is_self 正确。
- 首段 at 段拼装正确。

## 测试要求
`tests/adapters/test_onebot_mapping.py`：用构造/mock 的 event 对象（或最小假对象）验证映射字段。若难以构造真实 nonebot event，可用鸭子类型的假对象覆盖关键属性。

## 约束
Adapter 层，可 import nonebot onebot 类型；不做业务编排；不发送消息（只产出映射结果）。
