# P1-12 · adapters/onebot/lifecycle.py — NoneBot 初始化与生命周期

## 目标
把 MVP `bot.py` + `startup.py` 的 nonebot 初始化、adapter 注册、启动/关闭钩子迁到 Adapter 层，供 bootstrap 调用。

## 前置依赖
- P0-05（config，读取 DRIVER）。

## 需创建或修改的文件
- 新增 `src/lingxuan/adapters/onebot/lifecycle.py`

## 详细规格
提供函数（供 bootstrap 编排）：
- `init_nonebot(config) -> Driver`：`nonebot.init(driver=config.get_str("DRIVER"), log_level="INFO")`；`driver = nonebot.get_driver()`；`driver.register_adapter(OneBotV11Adapter)`；返回 driver。
- `register_lifecycle(driver, *, on_startup, on_shutdown)`：把传入的两个 async 回调挂到 `@driver.on_startup` / `@driver.on_shutdown`（回调本身由 bootstrap 提供，内部做配置校验、DB 初始化、用户记忆初始化等）。
- `run() -> None`：`nonebot.run()`。

注意：
- handlers 的注册**不在此处**（由 transport.start 完成）；bootstrap 会在 init 之后、run 之前调用 `transport.start(dialogue.handle_inbound)`。
- 保留 MVP 启动日志文案（连接提示等）。

## 验收标准
- `init_nonebot` 能按 DRIVER 初始化并注册 onebot adapter。
- 生命周期回调被正确挂载。

## 测试要求
以能被 import 且函数签名正确为主；nonebot 真实启动在集成/手动验证。可加 smoke 测试 mock `nonebot.init/get_driver`。

## 约束
Adapter 层，可 import nonebot；不含业务逻辑；具体启动检查逻辑由 bootstrap 注入的回调实现。
