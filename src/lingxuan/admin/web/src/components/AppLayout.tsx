/** App layout shell with sidebar navigation. */

import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/context";

export default function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-brand">灵轩</div>
        <nav className="sidebar-nav">
          <NavLink to="/" end className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
            仪表盘
          </NavLink>
          <NavLink to="/config" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
            配置
          </NavLink>
          <NavLink to="/status" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
            状态
          </NavLink>
          <NavLink to="/data" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
            数据
          </NavLink>
          <NavLink to="/plugins" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
            插件
          </NavLink>
          <NavLink to="/logs" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
            日志
          </NavLink>
          {user?.role === "admin" && (
            <NavLink to="/audit" className={({ isActive }) => isActive ? "nav-link active" : "nav-link"}>
              审计
            </NavLink>
          )}
        </nav>
        <div className="sidebar-footer">
          <span className="user-info">{user?.username}</span>
          <button className="btn-link" onClick={handleLogout}>
            退出
          </button>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
