# P4-06 · admin — 日志历史 API + WebSocket（日志流 + 状态推送）

## 目标
实现结构化日志历史查询 REST，以及两个 WebSocket：实时日志流（级别/关键词过滤）与状态周期推送 + 配置变更广播（对应 P0 结构化日志 + 状态推送）。

## 前置依赖
- P4-01（LogSink）、P4-02（app）、P4-03（鉴权）、P4-05（状态数据来源）、P1-01（config subscribe）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/routes/logs.py`（REST）
- 新增 `src/lingxuan/admin/ws.py`（WS）

## 详细规格

### REST
- `GET /admin/api/logs`（readonly）：query `limit`、`level`、`keyword`；调用 `log_sink.tail(...)`；返回结构化记录列表。

### WS `/admin/ws/logs`
- 连接时校验 token（query 参数或子协议携带 access token；校验失败关闭）。
- 客户端可发 `{"type":"filter","level":...,"keyword":...}` 动态调整过滤。
- 服务端：`log_sink.subscribe(cb)`，cb 内按当前过滤条件把 `LogRecord` 序列化为 `{"type":"log", ...}` 推送；连接关闭时取消订阅。
- 背压：推送失败/慢连接需丢弃或断开，避免阻塞 emit（用队列 + 超时）。

### WS `/admin/ws/status`
- 校验 token。
- 周期（如每 3–5s）推送 `{"type":"status", ...}`（复用 P4-05 的状态聚合）。
- 订阅 `config.subscribe`，配置变更时推 `{"type":"config_changed","key":...,"value":<脱敏>}`。
- 连接关闭清理定时任务与订阅。

WS 鉴权注意：浏览器 WS 无法自定义 header，token 走 query（`?token=`）或首帧握手消息；校验后再进入循环。

## 验收标准
- GET /logs 过滤正确。
- WS logs：连接后能收到实时日志；filter 生效；无 token 拒绝。
- WS status：周期收到状态；改配置收到 config_changed（脱敏）。
- 慢/断连不阻塞主流程日志。

## 测试要求
`tests/admin/test_ws.py`：用 httpx/starlette 的 WS 测试客户端验证鉴权、日志推送、filter、status 推送、config_changed。

## 约束
WS 必鉴权；订阅回调异常隔离；敏感配置广播脱敏。
