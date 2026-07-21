# 灵轩 v3 编码提示词集（规划索引）

> 完整方案见 [`docs/architecture-v3.md`](../architecture-v3.md)。具体任务提示词将在各 Phase 启动时按 v2 模式拆分追加到本目录。

## 执行顺序（规划）

### Phase 0 · 文档与安全债
- 敏感配置 at-rest 加密
- README / 运维文档更新
- `lingxuan doctor` 安全检查
- Playwright E2E 脚手架

### Phase 1 · 管理端 P2
- 受限终端 API + WS + 页面
- `DATA_ROOT` 文件沙箱 API + 页面

### Phase 2 · NapCat Web 运维
- NapCat 状态/日志/启停 API
- 仪表盘 Bot 链路可视化

### Phase 3 · 部署与可观测性
- Docker Compose / systemd
- Metrics / deep health / 日志导出

### Phase 4 · PostgreSQL（可选）
- 存储 Adapter 双实现 + 契约测试

### Phase 5 · 智能增强（可选）
- 多模型路由、Prompt 调试 API、GDPR 导出

### Phase 6+ · 远期
- 插件沙箱、RAG Protocol、消息类型扩展

## 约定

- 共享上下文仍使用 [`docs/prompts/00-common-context.md`](../prompts/00-common-context.md)，v3 增量规则见 `architecture-v3.md` 第二节「v2 架构不变式」。
- 冲突时以具体任务提示词为准，其次是 `architecture-v3.md`。
