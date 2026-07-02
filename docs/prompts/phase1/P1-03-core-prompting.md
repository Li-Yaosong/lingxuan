# P1-03 · core/prompting.py — prompt 拼装纯逻辑

## 目标
把 MVP `llm.build_context_messages` 及相关 prompt 组装逻辑迁为 Core 纯函数/服务，产出 `list[ChatMessage]`，不做任何 LLM 调用、不碰框架。

## 前置依赖
- P0-02、P0-03、P0-04、P1-02（PersonaService）。
- 注意：本任务依赖「历史/摘要/用户上下文/群实体」这些数据。Phase 1 阶段这些仍来自旧存储，因此**通过参数传入**，不直接读存储（保持纯函数）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/prompting.py`

## 详细规格
类 `PromptBuilder`（或一组纯函数），构造注入 `persona: PersonaService`、`config: ConfigProvider`。

核心方法 `build_context_messages(...) -> list[ChatMessage]`，复现 MVP 组装顺序：
1. system：`persona.get_system_prompt(is_group)`
2. system（可选）：`【此前对话摘要】\n{summary}`（summary 非空时）
3. system（`ENABLE_USER_MEMORY` 为真时）：用户人际上下文文本（作为参数 `user_context_text` 传入；由调用方从 UserMemoryService 生成）
4. system（群聊）：群实体文本（参数 `entities_text` 传入）
5. 历史消息：`history`（参数传入，`list[StoredMessage]`；群聊按 `GROUP_CHAT_CONTEXT` 截尾）
6. user（可选）：`extra_user`

参数签名建议：
```python
def build_context_messages(
    *, is_group: bool, history: list[StoredMessage], summary: str = "",
    user_context_text: str = "", entities_text: str = "",
    extra_user: str | None = None, history_limit: int | None = None,
) -> list[ChatMessage]: ...
```

另迁移：
- judge prompt 构造 `build_judge_prompt(observation_text, user_brief="") -> str`（对齐 MVP `should_reply_in_group` 内的 user prompt 文案与 yes/no 规则）。
- 摘要 prompt 构造 `build_summary_prompt(history, identity_note="") -> str`（对齐 MVP `summarize_session`）。
- 群聊回复 user 文案 `build_group_reply_user(observation: str) -> str`：`【当前群聊观察】\n{observation}\n\n请根据...`（对齐 MVP `chat_in_group[_stream]`）。
- 本地规则短路 `should_skip_reply_locally(text) -> bool`（对齐 MVP：≤6 字且无问号/「吗」，且含调侃词）。

所有文案从 MVP `llm.py` 原样搬运，保证生成 prompt 与现状一致。

## 验收标准
- 给定 history/summary/上下文，产出的 messages 顺序与角色与 MVP 一致。
- 群聊 `history_limit` 生效（截尾到 GROUP_CHAT_CONTEXT）。
- `should_skip_reply_locally` 与 MVP 同输入同输出。

## 测试要求
`tests/core/test_prompting.py`：
- 组装顺序断言（有/无 summary、有/无 user_context、私聊/群聊）。
- `should_skip_reply_locally` 若干边界用例。

## 约束
Core 纯逻辑，无 IO、无 LLM 调用、无 nonebot。数据一律参数传入。
