# P4-08 · admin/web — 配置页 + 状态页

## 目标
实现前端「运行时配置」页与「服务状态」页，对接 P4-04/P4-05 API。

## 前置依赖
- P4-07（SPA 骨架、API 客户端）、P4-04（config API）、P4-05（status API）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/web/src/pages/ConfigPage.tsx`、`StatusPage.tsx` 及相关组件/hooks。

## 详细规格

### 配置页 `/config`
- 拉 `GET /config/schema` + `GET /config`，按 `group` 分组渲染表单。
- 字段按 type 渲染控件（str→input、int/float→number、bool→switch、int_list→逗号输入）。
- 敏感项显示为脱敏占位，允许输入新值覆盖（留空表示不改）。
- `hot_reloadable=false` 的项标注「需重启生效」。
- 保存：收集改动项 `PUT /config`；显示每项成功/失败与「需重启」提示。
- 仅 admin 可编辑；readonly 只读展示（依据 `/auth/me` 的 role）。

### 状态页 `/status`
- 拉 `GET /status` 展示：bot 在线、模型、功能开关、记忆统计（卡片/表格）。
- 「测试 LLM」按钮 → `POST /status/llm-check`，显示延迟/错误。
- 可选：接 `/admin/ws/status` 实时刷新（若 P4-06 已完成），否则轮询。

## 验收标准
- 配置页能读、改、存并反映热更新；敏感项脱敏；权限区分。
- 状态页展示正确统计；LLM 测试可用。

## 测试要求
手动冒烟为主；可选 vitest 覆盖表单收集改动项的逻辑。

## 约束
遵守 XSS 防护；错误处理友好；不硬编码后端地址（用相对 `/admin/api`）。
