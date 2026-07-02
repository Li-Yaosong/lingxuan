# P4-09 · admin/web — 日志实时页

## 目标
实现前端「结构化日志」页：历史加载 + WebSocket 实时 tail + 级别过滤 + 关键词搜索。

## 前置依赖
- P4-07（SPA 骨架）、P4-06（logs REST + WS）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/web/src/pages/LogsPage.tsx` 及 WS hook（如 `useLogStream.ts`）。

## 详细规格
- 初次加载：`GET /admin/api/logs?limit=200` 填充历史。
- 实时：连接 `/admin/ws/logs?token=<access>`；收到 `{type:"log"}` 追加到列表（顶部或底部自动滚动，可暂停自动滚动）。
- 过滤：级别下拉（DEBUG/INFO/WARNING/ERROR）+ 关键词输入；变化时发送 `{type:"filter",...}` 给 WS，并对已加载历史本地过滤。
- 列表：显示 ts/level/logger/msg；level 用颜色区分（用 CSS class，不用内联危险 HTML）。
- 断线重连：WS 断开自动重连（退避），token 过期先刷新。
- 性能：前端保留上限（如最近 1000 条）防内存膨胀。

## 验收标准
- 页面加载显示历史日志。
- 实时日志滚动更新；级别/关键词过滤生效。
- 断线能重连；token 过期能续期。

## 测试要求
手动冒烟为主；可选 vitest 覆盖过滤逻辑与 WS 消息解析。

## 约束
日志文本一律转义渲染（防 XSS）；WS token 走 query；控制前端内存上限。
