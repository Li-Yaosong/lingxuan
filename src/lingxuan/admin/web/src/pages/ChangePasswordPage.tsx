/** Change password page: forced when must_change_password is true. */

import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/context";
import { ApiClientError } from "../api/client";

export default function ChangePasswordPage() {
  const { changePassword, user } = useAuth();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const forced = !!user?.must_change_password;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");

    if (newPassword.length < 6) {
      setError("新密码至少 6 个字符");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }

    setSubmitting(true);
    try {
      await changePassword(oldPassword, newPassword);
      setSuccess(true);
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

  if (success) {
    return (
      <div className="login-page">
        <div className="login-card">
          <h1>密码修改成功</h1>
          <p>请使用新密码重新登录。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>修改密码</h1>
        {forced && (
          <p className="login-hint">
            首次登录需要修改密码才能继续使用。
          </p>
        )}
        <form onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="old-password">当前密码</label>
            <input
              id="old-password"
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>

          <div className="form-field">
            <label htmlFor="new-password">新密码</label>
            <input
              id="new-password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={6}
              autoComplete="new-password"
            />
          </div>

          <div className="form-field">
            <label htmlFor="confirm-password">确认新密码</label>
            <input
              id="confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
          </div>

          {error && <div className="form-error">{error}</div>}

          <button type="submit" disabled={submitting} className="btn-primary">
            {submitting ? "请稍候…" : "修改密码"}
          </button>
        </form>
      </div>
    </div>
  );
}
