/** Route guard components: redirect unauthenticated / must_change_password users. */

import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/context";

/** Redirect to /login if not authenticated. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="loading">加载中…</div>;
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Force password change before accessing any other page
  if (user.must_change_password && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }

  return <>{children}</>;
}

/** Redirect to dashboard if already authenticated. */
export function RedirectIfAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="loading">加载中…</div>;
  }

  if (user) {
    // If must change password, send there instead
    if (user.must_change_password) {
      return <Navigate to="/change-password" replace />;
    }
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}
