/** Auth context: provides user info, login/logout, and route guard logic. */

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import {
  authApi,
  type MeResponse,
  type BootstrapInfoResponse,
} from "../api/client";
import {
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
  clearTokens,
} from "./tokens";

export interface AuthState {
  user: MeResponse | null;
  bootstrapInfo: BootstrapInfoResponse | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  bootstrapLogin: (
    bootstrapToken: string,
    username: string,
    password: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [bootstrapInfo, setBootstrapInfo] = useState<BootstrapInfoResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    try {
      const me = await authApi.me();
      setUser(me);
    } catch {
      setUser(null);
    }
  }, []);

  // On mount: if we have tokens, try to fetch /me; also check bootstrap info
  useEffect(() => {
    const init = async () => {
      setLoading(true);
      try {
        // Check bootstrap info first (no auth required)
        const info = await authApi.bootstrapInfo();
        setBootstrapInfo(info);

        // If we have stored tokens, try to restore session
        if (getAccessToken() || getRefreshToken()) {
          // If access token is missing but refresh exists, try refresh
          if (!getAccessToken() && getRefreshToken()) {
            try {
              const tokens = await authApi.refresh(getRefreshToken()!);
              setAccessToken(tokens.access_token);
              setRefreshToken(tokens.refresh_token);
            } catch {
              clearTokens();
            }
          }
          if (getAccessToken()) {
            await refreshUser();
          }
        }
      } catch {
        // bootstrap info fetch failed (server down?)
      } finally {
        setLoading(false);
      }
    };
    init();
  }, [refreshUser]);

  const login = useCallback(
    async (username: string, password: string) => {
      const tokens = await authApi.login(username, password);
      setAccessToken(tokens.access_token);
      setRefreshToken(tokens.refresh_token);
      await refreshUser();
      // After login, bootstrap is no longer needed
      setBootstrapInfo(null);
    },
    [refreshUser],
  );

  const bootstrapLogin = useCallback(
    async (bootstrapToken: string, username: string, password: string) => {
      const tokens = await authApi.bootstrapLogin(
        bootstrapToken,
        username,
        password,
      );
      setAccessToken(tokens.access_token);
      setRefreshToken(tokens.refresh_token);
      await refreshUser();
      setBootstrapInfo(null);
    },
    [refreshUser],
  );

  const logout = useCallback(async () => {
    const refresh = getRefreshToken();
    try {
      if (refresh) await authApi.logout(refresh);
    } catch {
      // Ignore logout errors
    }
    clearTokens();
    setUser(null);
  }, []);

  const changePassword = useCallback(
    async (oldPassword: string, newPassword: string) => {
      await authApi.changePassword(oldPassword, newPassword);
      await refreshUser();
    },
    [refreshUser],
  );

  return (
    <AuthContext.Provider
      value={{
        user,
        bootstrapInfo,
        loading,
        login,
        bootstrapLogin,
        logout,
        changePassword,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
