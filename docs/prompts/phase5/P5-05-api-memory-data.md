# P5-05 · admin — 记忆/数据管理 API（P1）

## 目标
实现管理端数据管理 REST：会话浏览、用户档案、社会关系图、导出/导入（对应 P1 记忆/数据管理，语义对齐现有 admin 命令）。

## 前置依赖
- P4-02/03（app + 鉴权）、P2-04~P2-06（Repository）、P3-02/03（导入/备份复用）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/routes/data.py`
- 修改 `src/lingxuan/admin/schemas.py`

## 详细规格
路由（前缀 `/admin/api`，读=readonly，写=admin）：
- `GET /sessions`：分页（keyset，`limit`/`before_id`），列会话（id/kind/最后活跃/消息数）。
- `GET /sessions/{id}/messages`：keyset 分页历史。
- `GET /sessions/{id}/summary`。
- `DELETE /sessions/{id}`（admin）：清会话记忆（↔ `reset_memory`）；写审计。
- `GET /users`：用户档案列表（分页）。
- `GET /users/{uid}`：单档案 + active facts（↔ `user_memory <QQ>`）。
- `DELETE /users/{uid}`（admin）：清某用户（↔ `reset_user_memory <QQ>`）。
- `DELETE /users`（admin）：清全部用户档案（↔ `reset_user_memory all`）。
- `GET /social-graph`：edges + name_index。
- `DELETE /social-graph`（admin）：清空（↔ `reset_user_memory graph`）。
- `GET /export`（admin）：导出全库为 JSON（或触发 `backup`），返回下载。
- `POST /import`（admin）：从 JSON 备份导入（复用 migrate 逻辑；需确认参数 `confirm=true`）；写审计。

所有写操作写 `audit_logs`。敏感字段（若有）脱敏。

## 验收标准
- 各 GET 分页正确；DELETE 语义与 admin 命令对齐并审计。
- export/import 可用（import 需确认）。
- 权限区分（readonly 不能删）。

## 测试要求
`tests/admin/test_api_data.py`：临时库塞数据，覆盖列表分页、单查、各删除语义、export/import、权限 403、审计记录。

## 约束
写必审计；破坏性操作需确认；经 Repository，不直接 SQL 拼接用户输入。
