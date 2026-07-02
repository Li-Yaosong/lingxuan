# P4-02 · admin/app.py — FastAPI 子应用与独立端口

## 目标
搭建管理端 FastAPI 应用骨架，监听独立端口（默认 `127.0.0.1:8081`），与 bot 同进程；提供依赖注入接线、静态 SPA 挂载点、路由聚合。

## 前置依赖
- P1-14/P2-10（Container 可提供各 Service/Repository）、P4-01（LogSink）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/app.py`
- 新增 `src/lingxuan/admin/deps.py`
- 修改 `src/lingxuan/bootstrap.py`：在 nonebot 启动的同时，用 uvicorn 以独立端口跑 admin app（同进程另起一个 server task）。

## 详细规格
- `create_admin_app(container) -> FastAPI`：
  - 前缀：REST 挂 `/admin/api`，WS 挂 `/admin/ws`，静态 SPA 挂 `/admin`（`web/dist`，若存在）。
  - CORS：默认只允许同源/本机（管理端独立端口，SPA 若独立 dev server 需允许其源，配置化）。
  - 依赖注入：`deps.py` 提供 `get_container()`、各 Service/Repository 依赖、当前用户依赖（P4-03 填充）。
  - 路由聚合：include config/status/logs/（后续 data/plugins/audit）routers。
- 独立端口启动：bootstrap 中 `uvicorn.Server(Config(admin_app, host=ADMIN_HOST, port=ADMIN_PORT))` 作为 asyncio task 与 nonebot 一起跑（注意二者事件循环协调：nonebot 用其 driver 的 loop；可在 driver on_startup 里 `asyncio.create_task` 启动 uvicorn Server.serve()）。
  - 若与 nonebot 事件循环集成复杂，可退而用 nonebot fastapi driver 的 `server_app` 直接 `mount("/admin", admin_app)` 于同端口——但**决策为独立端口**，优先实现独立端口方案；如实现受阻，在代码注释记录并给出同端口 fallback 开关。
- 健康检查：`GET /admin/api/health` 返回 `{status:"ok"}`（无需鉴权）。

## 验收标准
- 启动后 `GET http://127.0.0.1:8081/admin/api/health` 返回 ok。
- 管理端与 OneBot ws（8080）互不干扰。
- SPA 静态目录存在时可访问 `/admin`。

## 测试要求
`tests/admin/test_app.py`：用 `httpx.ASGITransport`/`TestClient` 测 health 与路由挂载（不需真跑端口）。

## 约束
Bootstrap/Adapter 层；管理端只依赖 Container 暴露的 Service/Repository/Protocol。
