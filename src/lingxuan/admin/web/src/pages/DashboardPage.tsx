/** Dashboard placeholder page. */

import { useAuth } from "../auth/context";

export default function DashboardPage() {
  const { user } = useAuth();

  return (
    <div className="page">
      <h1>仪表盘</h1>
      <p>
        欢迎回来，<strong>{user?.username}</strong>
      </p>
      <div className="dashboard-cards">
        <div className="card">
          <h3>配置</h3>
          <p>查看和修改运行时配置</p>
        </div>
        <div className="card">
          <h3>状态</h3>
          <p>查看服务运行状态</p>
        </div>
        <div className="card">
          <h3>日志</h3>
          <p>查看实时日志流</p>
        </div>
      </div>
    </div>
  );
}
