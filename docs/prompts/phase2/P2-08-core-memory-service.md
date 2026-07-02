# P2-08 · core/memory.py — MemoryService 切 Repository

## 目标
实现（或把 Phase 1 临时实现替换为）基于 `SessionRepository` 的 MemoryService，复现 MVP `memory.py` + 摘要相关行为，全部经 Repository，**不再直接读写 JSON 文件**。

## 前置依赖
- P0-04（SessionRepository 接口）、P1-03（prompting 的摘要 prompt）、P0-03（LLM）、P0-05/06。

## 需创建或修改的文件
- 新增/替换 `src/lingxuan/core/memory.py`

## 详细规格
`class MemoryService`，注入 `sessions: SessionRepository`、`llm: LLMProvider`、`prompt: PromptBuilder`、`config`、`clock`、`log`。

方法（对齐 MVP `memory.py` + `llm.py` 摘要）：
- `async def append(sid, role, content, *, user_id=None)`：`sessions.ensure` + `append_message`；随后执行**持久化硬上限裁剪**：若 `count_messages > MEMORY_WINDOW*2` → `trim_to_last(keep_last=MEMORY_WINDOW*2)`。
- `async def load_history(sid, *, limit=None)`。
- `async def get_summary(sid)` / `set_summary`。
- `update_meta` / `merge_entity` / `get_entities`（透传 Repository）。
- `async def clear(sid, *, clear_user_profiles=False)`：清会话；`clear_user_profiles=True` 时另调 user_memory（对齐 MVP `clear_history(clear_user_profiles=True)` 语义——由调用方协调或注入 user_memory）。
- 摘要：
  - `async def maybe_summarize(sid)`：`ENABLE_MEMORY_SUMMARY` 且 `count_messages > MEMORY_WINDOW` 时执行 `summarize`。
  - `async def summarize(sid)`：取前 `MEMORY_WINDOW` 条 → `prompt.build_summary_prompt` → `llm.chat`（≤200字）→ 成功则 `set_summary` + `trim_to_last(keep_last=count//2)`（摘要后减半）；失败（fallback 文本）不保存不裁剪。
  - `def schedule_summarize(sid)`：`asyncio.create_task(maybe_summarize(sid))`（对齐 MVP fire-and-forget）。

## 验收标准
- append 后超过 40 条自动裁剪到 40。
- 摘要触发阈值、成功后减半、失败不动，与 MVP 一致。
- 无文件 IO；全部经 SessionRepository。

## 测试要求
`tests/core/test_memory_service.py`（用 InMemorySessionRepository + FakeLLM）：
- 追加到 41 条后裁剪为 40。
- 历史 > window 且开关开时 maybe_summarize 调用 LLM，成功后 summary 落库且历史减半；LLM 返回 fallback 时不变。

## 约束
Core 层，禁止文件 IO / nonebot / sqlalchemy；经注入接口。
