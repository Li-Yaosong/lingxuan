/** API client: wraps fetch with auto Bearer auth and 401 refresh retry. */

import {
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
  clearTokens,
} from "../auth/tokens";

export interface ApiError {
  status: number;
  detail: string;
}

export class ApiClientError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

/** Refresh lock to prevent concurrent refresh calls. */
let _refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  // Deduplicate concurrent refresh attempts
  if (_refreshPromise) return _refreshPromise;

  _refreshPromise = _doRefresh();
  try {
    return await _refreshPromise;
  } finally {
    _refreshPromise = null;
  }
}

async function _doRefresh(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;

  try {
    const res = await fetch("/admin/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });

    if (!res.ok) {
      clearTokens();
      return false;
    }

    const data = await res.json();
    setAccessToken(data.access_token);
    setRefreshToken(data.refresh_token);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { body, ...init } = options;

  const headers = new Headers(init.headers);
  if (body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const access = getAccessToken();
  if (access) {
    headers.set("Authorization", `Bearer ${access}`);
  }

  const res = await fetch(path, {
    ...init,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  // 401 → try refresh once, then retry
  if (res.status === 401) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      const retryHeaders = new Headers(init.headers);
      if (body && !retryHeaders.has("Content-Type")) {
        retryHeaders.set("Content-Type", "application/json");
      }
      const newAccess = getAccessToken();
      if (newAccess) {
        retryHeaders.set("Authorization", `Bearer ${newAccess}`);
      }
      const retryRes = await fetch(path, {
        ...init,
        headers: retryHeaders,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (retryRes.ok) return retryRes.json();
      // Refresh succeeded but original request still failed
      const errData = await retryRes.json().catch(() => ({}));
      throw new ApiClientError(
        retryRes.status,
        errData.detail || retryRes.statusText,
      );
    }
    // Refresh failed → clear tokens, redirect handled by auth context
    clearTokens();
    throw new ApiClientError(401, "Session expired");
  }

  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new ApiClientError(res.status, errData.detail || res.statusText);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),

  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "POST", body }),

  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PUT", body }),

  del: <T>(path: string, options?: RequestOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
};

// ── Typed API calls ──────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
}

export interface MeResponse {
  username: string;
  role: string;
  must_change_password: boolean;
}

export interface BootstrapInfoResponse {
  bootstrap_required: boolean;
  bootstrap_token: string | null;
}

export interface MessageResponse {
  message: string;
}

// ── Config types ────────────────────────────────────────────────────────

export interface ConfigSchemaItem {
  key: string;
  type: "str" | "int" | "float" | "bool" | "int_list";
  default: unknown;
  group: string;
  is_secret: boolean;
  hot_reloadable: boolean;
  description: string;
}

export interface ConfigUpdateResultItem {
  key: string;
  success: boolean;
  error: string | null;
  needs_restart: boolean;
}

export interface ConfigUpdateResponse {
  results: ConfigUpdateResultItem[];
}

// ── Status types ────────────────────────────────────────────────────────

export interface MemoryStats {
  sessions: number;
  messages: number;
  users: number;
  active_facts: number;
  edges: number;
}

export interface GroupObserveState {
  group_id: number;
  buffer_len: number;
  last_judge_result: string;
  in_cooldown: boolean;
  cooldown_remaining: number;
  observe_in_flight: boolean;
}

export interface StatusResponse {
  bot_online: boolean;
  features: Record<string, boolean>;
  model: string;
  memory_stats: MemoryStats;
  observe_states: GroupObserveState[];
}

export interface LLMCheckResponse {
  ok: boolean;
  latency_ms: number;
  error: string | null;
}

// ── Typed API calls ──────────────────────────────────────────────────────

export const authApi = {
  login: (username: string, password: string) =>
    api.post<TokenResponse>("/admin/api/auth/login", { username, password }),

  bootstrapLogin: (bootstrap_token: string, username: string, password: string) =>
    api.post<TokenResponse>("/admin/api/auth/bootstrap-login", {
      bootstrap_token,
      username,
      password,
    }),

  bootstrapInfo: () =>
    api.get<BootstrapInfoResponse>("/admin/api/auth/bootstrap-info"),

  refresh: (refresh_token: string) =>
    api.post<TokenResponse>("/admin/api/auth/refresh", { refresh_token }),

  logout: (refresh_token: string) =>
    api.post<MessageResponse>("/admin/api/auth/logout", { refresh_token }),

  changePassword: (old_password: string, new_password: string) =>
    api.post<MessageResponse>("/admin/api/auth/change-password", {
      old_password,
      new_password,
    }),

  me: () => api.get<MeResponse>("/admin/api/auth/me"),
};

export const configApi = {
  schema: () =>
    api.get<ConfigSchemaItem[]>("/admin/api/config/schema"),

  get: () =>
    api.get<Record<string, unknown>>("/admin/api/config"),

  update: (changes: Record<string, unknown>) =>
    api.put<ConfigUpdateResponse>("/admin/api/config", changes),
};

export const statusApi = {
  get: () =>
    api.get<StatusResponse>("/admin/api/status"),

  llmCheck: () =>
    api.post<LLMCheckResponse>("/admin/api/status/llm-check"),
};
