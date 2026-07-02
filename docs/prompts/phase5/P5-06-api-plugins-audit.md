# P5-06 · admin — 插件管理 + 审计 API（P1）

## 目标
实现插件管理 REST（列表 + 启停 + 配置 + Hook 注册表）与审计日志查询 REST。

## 前置依赖
- P5-01/02（Host/Loader）、P2-07（PluginConfig/Audit Repository）、P4-02/03。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/routes/plugins.py`
- 新增 `src/lingxuan/admin/routes/audit.py`
- 修改 `src/lingxuan/admin/schemas.py`

## 详细规格

### 插件（前缀 `/admin/api`）
- `GET /plugins`（readonly）：`host.registry()` + 各插件当前 enabled/config（含 hooks 列表）。
- `PUT /plugins/{name}`（admin）：body `{enabled?: bool, config?: dict}`；更新 `PluginConfigRepository`；同步 `host.enable/disable`；config 变更通知插件（可通过重新 setup 或专用回调，注明策略）；写审计。

### 审计
- `GET /audit`（admin）：`AuditRepository.query`（actor/action 过滤 + keyset 分页）。

## 验收标准
- GET /plugins 反映真实注册表与启停态。
- PUT 切换 enabled 后 dispatch 行为随之变化（可配合 P5-04 验证）。
- GET /audit 分页与过滤正确；仅 admin 可访问。

## 测试要求
`tests/admin/test_api_plugins_audit.py`：注册假插件后列表/启停/配置更新 + 审计写入；audit 查询过滤/分页/权限。

## 约束
写必审计；插件 config 更新策略明确（热更 vs 需重载）并在响应标注。
