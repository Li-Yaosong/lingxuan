# P4-03 · admin/auth.py — JWT 认证 + RBAC + 首登改密（决策 3）

## 目标
实现管理端认证：JWT（access 短时效 + refresh 可吊销）、RBAC（admin/readonly）、首次 bootstrap token 与首登强制改密。

## 前置依赖
- P2-07（AdminUserRepository）、P4-02（app/deps）、P0-05（config：SECRET_KEY/JWT_*）。

## 需创建或修改的文件
- 新增 `src/lingxuan/admin/auth.py`
- 新增 `src/lingxuan/admin/routes/auth.py`
- 修改 `src/lingxuan/admin/deps.py`：加当前用户/角色守卫。
- 修改 `src/lingxuan/cli.py`：加 `admin-passwd` 子命令（创建/重置管理员密码）。

## 详细规格

### 密码与首次引导
- 密码哈希：`passlib` argon2（或 bcrypt）。
- 首次启动无 admin 用户时：生成一次性 **bootstrap token**（随机串），打印到控制台/写入 `data/bootstrap_token.txt`（权限受限）；用它可完成首个 admin 的创建/登录，随后 `must_change_password=True` 强制改密。
- `lingxuan admin-passwd --username admin [--role admin]`：交互式设置密码（创建或重置），清除 must_change_password。

### JWT
- `SECRET_KEY` 缺失时管理端拒绝启动（或仅 health 可用并告警）。
- access token：`JWT_ACCESS_TTL`（默认 900s），claims 含 sub(username)、role、exp、type=access。
- refresh token：`JWT_REFRESH_TTL`（默认 7d），存储可吊销标记（可存 DB 或内存黑名单；简单起见记录 refresh jti 到 DB/内存，logout 时吊销）。
- 签发/校验用 `python-jose`。

### 路由（`/admin/api/auth`）
- `POST /login`：用户名+密码 → 返回 access+refresh；错误限速（失败计数 + 短暂锁定）。
- `POST /refresh`：refresh → 新 access。
- `POST /logout`：吊销当前 refresh。
- `POST /change-password`：需登录；旧密码校验；改密后清 must_change_password。
- `GET /me`：返回 username/role/must_change_password。

### RBAC 守卫（deps）
- `require_user`：校验 access token，返回当前用户（含 role）。
- `require_admin`：role==admin 否则 403。
- `require_readonly_ok`：任意登录用户（readonly 可读）。
- 若 `must_change_password=True`，除 change-password/me/logout 外的接口返回 428（需先改密）。

## 验收标准
- 首次引导 → 创建 admin → 首登强制改密流程可走通。
- readonly 用户访问写接口 403。
- access 过期后用 refresh 换新；logout 后 refresh 失效。
- SECRET_KEY 缺失时拒绝启动/告警。

## 测试要求
`tests/admin/test_auth.py`（httpx + 临时 db）：
- login/refresh/logout/change-password 正常流。
- 错误密码限速；readonly 403；must_change_password 拦截；过期 token 拒绝。

## 约束
安全关键；密码只存 hash；敏感值不进日志；遵守第十节安全清单。
