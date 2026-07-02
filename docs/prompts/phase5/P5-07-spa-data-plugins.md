# P5-07 · admin/web — 数据管理页 + 插件管理页 + 审计页

## 目标
实现前端「数据管理」「插件管理」「审计」页面，对接 P5-05/P5-06 API。

## 前置依赖
- P4-07（SPA 骨架）、P5-05（data API）、P5-06（plugins/audit API）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/web/src/pages/DataPage.tsx`、`PluginsPage.tsx`、`AuditPage.tsx` 及相关组件。
- 更新前端路由与导航。

## 详细规格

### 数据管理页 `/data`
- 会话列表（分页）→ 点击查看历史（keyset 分页）与摘要。
- 用户档案列表 → 查看单档案（identity/relationship/facts/cognition）。
- 社会关系图：edges 表 + name_index（可简单表格；图可视化可选）。
- 危险操作（删除会话/用户/清空/清图）：admin 可见，二次确认弹窗。
- 导出（下载）/导入（上传 + 确认）。

### 插件管理页 `/plugins`
- 列表：name/version/enabled/hooks。
- 开关启停（admin）；查看/编辑 config（JSON 或按 schema 表单，若插件提供 schema）。
- 显示 Hook 注册表。

### 审计页 `/audit`
- 列表（分页）+ 按 actor/action 过滤；展示 time/actor/action/target/success。

## 验收标准
- 三页能读并操作对应 API；权限区分（readonly 只读、admin 可写）。
- 破坏性操作有确认。
- 导出/导入可用。

## 测试要求
手动冒烟为主；可选 vitest 覆盖分页与确认逻辑。

## 约束
文本转义渲染（防 XSS）；破坏性操作确认；相对 API 路径；不在前端暴露敏感明文。
