# P5-03 · Core 各 Hook 插入点接入 + services 聚合

## 目标
在 Core 的关键节点调用 `PluginHost.dispatch`，让插件能在 `on_inbound_message` / `on_before_reply` / `on_after_reply` / `on_memory_extract` / `on_config_change` 生效；并定义传给插件 setup 的 `services` 聚合对象。

## 前置依赖
- P5-01（Host）、P1-07（Observation）、P1-08（Dialogue）、P2-08/09（memory/user_memory）、P1-01（config subscribe）。

## 需创建或修改的文件
- 修改 `src/lingxuan/core/dialogue.py`、`src/lingxuan/core/observation.py`（插入 dispatch 调用）。
- 修改 `src/lingxuan/core/user_memory.py`（memory_extract hook）。
- 新增 `src/lingxuan/plugins/services.py`（`PluginServices` 聚合：暴露只读/受控的 session_repo、user_memory、social_graph、config 等给插件）。
- 修改 `src/lingxuan/container.py`：构建 host + loader + services 并注入 dialogue/observation。
- 修改 bootstrap `_startup`：`await loader.discover_and_register()`。

## 详细规格
插入点（严格按时机）：
- `on_inbound_message`：`DialogueService.handle_inbound` 早期（记忆写入前后择一，建议写入前允许改写/忽略）。`ctx.inbound` 传入；若 handler 置 `ctx.cancelled=True` 则跳过该消息后续处理。
- `on_before_reply`：决定回复后、调 LLM 前（私聊与群回复路径、observation 回复路径都要）。`ctx.reply_plan` 传入，允许调整。
- `on_after_reply`：回复发送完成后。`ctx.extra` 带发送结果（文本/target）。
- `on_memory_extract`：`UserMemoryService` 记忆抽取产出 facts/edges 后、落库前。`ctx.extra` 带候选，允许增删。
- `on_config_change`：`ConfigProvider.set` 触发时，由订阅桥接调用 host.dispatch（或直接由 config subscribe 回调触发插件）。`ctx.extra={key,value}`。

`PluginServices`：一个 dataclass/简单对象，聚合插件可用的能力（session repo、user_memory service、social graph repo、config、log）。传给 `plugin.setup(host, config, services)`。

## 验收标准
- 五个 Hook 均在正确时机被 dispatch。
- 插件可在 on_inbound 忽略消息、在 on_before_reply 改 plan、在 on_memory_extract 改候选。
- 无插件时行为与 Phase 4 末一致（Hook 分发空转无副作用）。

## 测试要求
`tests/core/test_hook_integration.py`（fakes + 假插件）：验证各 Hook 被调用且能影响流程（cancel 忽略、改 plan、改 memory 候选）。

## 约束
Core 只依赖 PluginHost 接口；dispatch 失败/插件异常不影响主流程（Host 已隔离）。保持 MVP 行为在无插件时不变。
