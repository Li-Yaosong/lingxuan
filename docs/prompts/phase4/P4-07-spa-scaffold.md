# P4-07 · admin/web — React + Vite + TS 骨架与鉴权

## 目标
搭建管理端前端 SPA（React + Vite + TypeScript），实现登录/改密/路由守卫/API 客户端与 token 管理，构建产物输出到 `admin/web/dist` 供后端静态挂载。

## 前置依赖
- P4-02（静态挂载路径 `/admin`）、P4-03（auth API）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/web/`：Vite 项目（`package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`、`src/**`）。
- 构建输出配置到 `dist`（base 路径设为 `/admin/`）。

## 详细规格
- 技术：React 18 + Vite + TS；轻量 UI（可用 无组件库 + CSS，或极简引入，如 需要可用 Tailwind；避免过重）。路由用 `react-router`。
- API 客户端：封装 fetch，自动附带 `Authorization: Bearer <access>`；401 时用 refresh 换新，失败则跳登录。
- Token 管理：access 存内存，refresh 存 `localStorage`（注意 XSS，遵守第十节：不 `dangerouslySetInnerHTML` 未净化内容）。
- 页面骨架 + 路由：`/login`、`/change-password`、`/`（Dashboard 占位）、`/config`、`/status`、`/logs`（后三者由 P4-08/09 填充，先占位）。
- 路由守卫：未登录跳 `/login`；`must_change_password` 强制跳 `/change-password`。
- 登录页：用户名/密码 → 调 `/auth/login`；错误提示。
- 改密页：旧/新密码 → `/auth/change-password`。
- `vite.config.ts`：dev proxy 把 `/admin/api` 与 `/admin/ws` 代理到 `127.0.0.1:8081`；build `base:"/admin/"`、`outDir:"dist"`。
- 构建脚本：`npm run build` 产出 dist；文档说明后端如何挂载。

## 验收标准
- `npm install && npm run build` 成功产出 `dist`。
- dev（`npm run dev`）下能登录、改密、进入 Dashboard。
- 后端挂载 dist 后经 `/admin` 可访问。

## 测试要求
- 前端以能构建 + 手动冒烟为主；可选加 1–2 个组件测试（vitest）覆盖 API 客户端的 401 刷新逻辑。

## 约束
前端资源在 `admin/web/`；不把 node_modules/dist 纳入 git（更新 .gitignore）；遵守 XSS 防护。
