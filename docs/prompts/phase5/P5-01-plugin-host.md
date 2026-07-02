# P5-01 · plugins/host.py — PluginHost 注册表与 Hook 分发

## 目标
实现 `PluginHost`：插件注册、Hook 订阅、启停、注册表查询、按 Hook 分发（异常隔离）。

## 前置依赖
- P0-06（PluginHost/Plugin/HookType/PluginContext 接口）。

## 需创建或修改的文件
- 新增 `src/lingxuan/plugins/__init__.py`
- 新增 `src/lingxuan/plugins/host.py`

## 详细规格
`class DefaultPluginHost(PluginHost)`，注入 `log`：
- 内部结构：`plugins: dict[str, PluginRecord]`（含 plugin 实例、enabled、hooks 列表、config）；`subscriptions: dict[HookType, list[(name, handler)]]`。
- `register(plugin, config)`：调用 `plugin.setup(self, config, services)`（services 由容器传入，见 P5-03）；记录；默认 enabled 依据 PluginConfigRepository（Phase 5 装配时传入初值）。
- `subscribe(hook, handler)`：由插件在 setup 内调用，记录订阅（关联当前正在 setup 的插件名）。
- `enable(name)/disable(name)`：切换 enabled；disable 时其 handler 不再被分发（保留订阅记录，enable 恢复）。
- `registry() -> list[PluginInfo]`。
- `async dispatch(ctx) -> ctx`：按 `ctx.hook` 顺序调用所有**已启用**插件的 handler；每个 handler `await`；**单个 handler 抛异常必须被捕获并记日志，不影响其它插件与主流程**；handler 可修改并返回 ctx（用返回值串联）；若 `ctx.cancelled` 变 True，可提前停止后续（对 on_inbound 语义：标记忽略该消息）。

`PluginRecord`/`PluginInfo` 按需定义。

## 验收标准
- 注册后 dispatch 能按 hook 调用对应 handler。
- disable 后该插件 handler 不被调用；enable 恢复。
- 一个 handler 抛异常不影响其它 handler 与 dispatch 返回。
- ctx 在 handler 间串联传递；cancelled 生效。

## 测试要求
`tests/plugins/test_host.py`：注册两个假插件，验证分发顺序、启停、异常隔离、ctx 修改串联、cancel。

## 约束
Core/plugins 层；不 import 框架实现；异常必隔离。
