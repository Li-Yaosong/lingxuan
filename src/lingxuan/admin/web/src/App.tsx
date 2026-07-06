import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AuthProvider } from "./auth/context";
import { RequireAuth, RedirectIfAuth } from "./auth/guard";
import AppLayout from "./components/AppLayout";
import LoginPage from "./pages/LoginPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import DashboardPage from "./pages/DashboardPage";
import ConfigPage from "./pages/ConfigPage";
import StatusPage from "./pages/StatusPage";
import LogsPage from "./pages/LogsPage";
import DataPage, { DataSessionsTab, DataUsersTab, DataSocialGraphTab } from "./pages/DataPage";
import SessionDetailPage from "./pages/SessionDetailPage";
import UserDetailPage from "./pages/UserDetailPage";
import PluginsPage from "./pages/PluginsPage";
import AuditPage from "./pages/AuditPage";
import "./styles/global.css";

export default function App() {
  return (
    <BrowserRouter basename="/admin">
      <AuthProvider>
        <Routes>
          {/* Public routes */}
          <Route
            path="/login"
            element={
              <RedirectIfAuth>
                <LoginPage />
              </RedirectIfAuth>
            }
          />

          {/* Protected routes */}
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<DashboardPage />} />
            <Route path="/change-password" element={<ChangePasswordPage />} />
            <Route path="/config" element={<ConfigPage />} />
            <Route path="/status" element={<StatusPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/data" element={<DataPage />}>
              <Route index element={<DataSessionsTab />} />
              <Route path="sessions/:sessionId" element={<SessionDetailPage />} />
              <Route path="users" element={<DataUsersTab />} />
              <Route path="users/:uid" element={<UserDetailPage />} />
              <Route path="social-graph" element={<DataSocialGraphTab />} />
            </Route>
            <Route path="/plugins" element={<PluginsPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
