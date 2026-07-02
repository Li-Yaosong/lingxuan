# P5-02 · plugins/loader.py — 插件发现与加载

## 目标
实现插件发现/加载：内置 `plugins/builtin/` 目录 + Python `entry_points`；结合 `PluginConfigRepository` 决定启停与配置。

## 前置依赖
- P5-01（PluginHost）、P2-07（PluginConfigRepository）。

## 需创建或修改的文件
- 新增 `src/lingxuan/plugins/loader.py`
- 新增 `src/lingxuan/plugins/builtin/__init__.py`

## 详细规格
`class PluginLoader`，注入 `host: PluginHost`、`plugin_configs: PluginConfigRepository`、`services`（容器聚合的服务对象）、`log`：
- `async def discover_and_register()`：
  1. 扫描 `plugins/builtin/` 下的内置插件模块（约定每个模块暴露一个 `plugin` 实例或 `get_plugin()`）。
  2. 扫描 `entry_points`（group 如 `lingxuan.plugins`）加载第三方插件（可选，本机可信）。
  3. 对每个插件：从 `plugin_configs` 读 `(enabled, config)`（无则用默认 enabled=True、空 config，并 upsert 初值）；`host.register(plugin, config)`；据 enabled 调 `host.enable/disable`。
- 安全说明（写入 docstring）：同进程、无沙箱；仅加载可信来源；加载/ setup 异常需捕获并记录，不影响启动。

## 验收标准
- 能发现并注册内置插件；enabled 状态遵循 PluginConfigRepository。
- 单个插件加载失败不影响其它与启动。

## 测试要求
`tests/plugins/test_loader.py`：放一个假内置插件，验证被发现、注册、按配置启停；构造一个加载即抛错的插件，验证被隔离。

## 约束
plugins 层；entry_points 加载失败要健壮；不做远程下载。
