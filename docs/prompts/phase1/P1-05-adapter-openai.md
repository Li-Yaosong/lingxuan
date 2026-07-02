# P1-05 · adapters/openai/provider.py — LLMProvider 实现

## 目标
用 `openai.AsyncOpenAI` 实现 `LLMProvider`（chat / chat_stream / judge），把 MVP `llm.py` 中的 provider 调用部分迁出，**不含 prompt 拼装、不含业务编排**。

## 前置依赖
- P0-03（LLMProvider 接口）、P0-05（ConfigProvider）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/openai/provider.py`

## 详细规格
类 `OpenAIProvider(LLMProvider)`，构造注入 `config: ConfigProvider`（读取 `OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL`）与可选 `log`（LogSink 或 logger 抽象）。

- client：懒创建 `AsyncOpenAI(api_key=..., base_url=...)`；无 key 时 `chat` 返回 fallback 文案（对齐 MVP `FALLBACK_NO_KEY`），`judge` 返回 `default`。key 变更（配置热更新）后应能重建 client——可订阅 config 变更或每次读取时比对。
- `chat(messages, *, max_tokens, temperature, timeout)`：调用 `chat.completions.create`（非流式），异常/超时返回 fallback（对齐 MVP `FALLBACK_REPLY`）。`messages` 是 `list[ChatMessage]`，转成 openai 所需 dict。
- `chat_stream(messages, ...)`：返回 `AsyncIterator[str]`，`stream=True` 逐 token yield `delta.content`；异常时结束流（记录日志）。
- `judge(prompt, *, timeout, default)`：单条 user 消息调用（低 max_tokens、短 timeout=JUDGE 默认 5s），解析输出：包含「yes/是/需要」等判为 True，「no/否」判 False；无法解析或异常返回 `default`（对齐 MVP `should_reply_in_group` 的 yes/no 解析，fallback "no"）。

常量：`LLM_TIMEOUT=30.0`、`JUDGE_TIMEOUT=5.0`、fallback 文案从 MVP 搬运。

## 验收标准
- 无 key 时不抛异常，返回 fallback。
- `chat`/`chat_stream`/`judge` 签名与 Protocol 一致。
- 不 import 任何 core/业务模块；不做 prompt 拼装。

## 测试要求
`tests/adapters/test_openai_provider.py`：用 mock（monkeypatch `AsyncOpenAI` 或注入假 client）验证：
- 无 key 返回 fallback；
- judge 对 "yes"/"no"/乱码 的解析结果；
- chat_stream 能把假 delta 序列拼出。

## 约束
Adapter 层，可 import `openai`；禁止 import core 业务或 nonebot。
