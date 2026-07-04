/** Login page: username/password or bootstrap first-run. */

import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/context";
import { ApiClientError } from "../api/client";

export default function LoginPage() {
  const { login, bootstrapLogin, bootstrapInfo } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [bootstrapToken, setBootstrapToken] = useState(
    bootstrapInfo?.bootstrap_token ?? "",
  );
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const isBootstrap = !!bootstrapInfo?.bootstrap_required;
  const title = isBootstrap ? "初始化管理员" : "登录";

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (isBootstrap) {
        await bootstrapLogin(bootstrapToken, username, password);
      } else {
        await login(username, password);
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.detail);
      } else {
        setError("网络错误，请重试");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>灵轩管理端</h1>
        <h2>{title}</h2>

        {isBootstrap && (
          <p className="login-hint">
            首次使用，请创建管理员账号。Bootstrap Token 已自动填入。
          </p>
        )}

        <form onSubmit={handleSubmit}>
          {isBootstrap && (
            <div className="form-field">
              <label htmlFor="bootstrap-token">Bootstrap Token</label>
              <input
                id="bootstrap-token"
                type="text"
                value={bootstrapToken}
                onChange={(e) => setBootstrapToken(e.target.value)}
                required
                autoComplete="off"
              />
            </div>
          )}

          <div className="form-field">
            <label htmlFor="username">用户名</label>
            <input
              id="username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoComplete="username"
            />
          </div>

          <div className="form-field">
            <label htmlFor="password">密码</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete={
                isBootstrap ? "new-password" : "current-password"
              }
            />
          </div>

          {error && <div className="form-error">{error}</div>}

          <button type="submit" disabled={submitting} className="btn-primary">
            {submitting ? "请稍候…" : title}
          </button>
        </form>
      </div>
    </div>
  );
}
