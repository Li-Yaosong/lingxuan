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
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
