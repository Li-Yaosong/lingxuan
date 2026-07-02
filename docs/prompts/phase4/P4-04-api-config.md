# P4-04 · admin/routes/config.py — 运行时配置读写 API

## 目标
实现配置查看/修改 API（对应 P0 运行时配置）：读全部（脱敏）、读 schema、批量更新（落 DB + 审计 + 热更新）。

## 前置依赖
- P4-02（app）、P4-03（鉴权）、P1-01/P2-10（ConfigProvider 接 DB）、P0-08（settings_defaults 提供 schema）、P2-07（AuditRepository）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/routes/config.py`
- 修改 `src/lingxuan/admin/schemas.py`（配置相关 Pydantic 模型）

## 详细规格
路由（前缀 `/admin/api`）：
- `GET /config`（readonly）：`config.get_all(mask_secrets=True)`，返回 `{key: value}`（敏感项脱敏）。
- `GET /config/schema`（readonly）：由 `settings_defaults.SETTINGS` 生成 `[{key,type,default,group,is_secret,hot_reloadable,description}]`。
- `PUT /config`（admin）：body `{key: value, ...}`；对每项校验 key 存在与类型（用 settings_defaults）；`config.set(key, value, actor=current_user)`（落 DB + 触发订阅热更新）；写审计（action="config.update"，detail 含改动 key 列表，敏感值不落明文）；返回更新结果，并对 `hot_reloadable=False` 的项在响应中标注「需重启生效」。

要点：
- 敏感项更新：允许写入新值，但响应回显脱敏；不在审计 detail 里存明文。
- 部分失败：逐项校验，返回每项成功/失败，整体用事务或逐项提交（注明策略）。

## 验收标准
- GET /config 敏感项脱敏。
- PUT /config 改 `BOT_NAME` 后即时生效（热更新），改 `ADMIN_PORT` 提示需重启。
- 非 admin PUT 返回 403。
- 审计有记录且不含敏感明文。

## 测试要求
`tests/admin/test_api_config.py`：GET 脱敏、PUT 生效+审计、类型校验失败 422、权限 403。

## 约束
写操作必审计；敏感值不入日志/审计明文。
