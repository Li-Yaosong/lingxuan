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

// ── Log types ──────────────────────────────────────────────────────

export interface LogRecordItem {
  ts: string;
  level: string;
  logger: string;
  msg: string;
  extra: Record<string, unknown>;
}

export interface LogsResponse {
  records: LogRecordItem[];
  total: number;
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

export const logsApi = {
  history: (limit = 200, level?: string, keyword?: string) => {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (level) params.set("level", level);
    if (keyword) params.set("keyword", keyword);
    return api.get<LogsResponse>(`/admin/api/logs?${params.toString()}`);
  },
};

// ── Data types ──────────────────────────────────────────────────────────

export interface SessionItem {
  id: string;
  kind: string;
  last_active_at: string | null;
  message_count: number;
}

export interface SessionListResponse {
  items: SessionItem[];
  has_more: boolean;
}

export interface MessageItem {
  seq: number;
  role: string;
  content: string;
  user_id: number | null;
  created_at: string;
}

export interface MessageListResponse {
  items: MessageItem[];
  has_more: boolean;
}

export interface SessionSummaryResponse {
  id: string;
  kind: string;
  summary: string;
  nickname: string;
  group_id: number | null;
  entities: Record<string, number>;
}

export interface UserProfileItem {
  user_id: number;
  preferred_name: string;
  stage: string;
  interaction_count: number;
}

export interface UserProfileListResponse {
  items: UserProfileItem[];
  has_more: boolean;
}

export interface UserFactItem {
  id: string;
  content: string;
  category: string;
  active: boolean;
  learned_at: string;
}

export interface UserProfileDetailResponse {
  user_id: number;
  preferred_name: string;
  aliases: string[];
  group_cards: Record<string, string>;
  stage: string;
  first_met_at: string | null;
  last_seen_at: string | null;
  interaction_count: number;
  last_group_id: number | null;
  seen_in_private: boolean;
  seen_in_group: boolean;
  impression: string;
  cognition_summary: string;
  facts: UserFactItem[];
}

export interface SocialEdgeItem {
  from_user_id: number;
  to_user_id: number;
  relation: string;
  label: string;
  evidence: string;
  group_id: number | null;
  learned_at: string;
}

export interface SocialGraphResponse {
  edges: SocialEdgeItem[];
  name_index: Record<string, number>;
}

export interface ImportResponse {
  status: string;
  imported: Record<string, number>;
}

// ── Plugin types ────────────────────────────────────────────────────────

export interface PluginItem {
  name: string;
  version: string;
  enabled: boolean;
  hooks: string[];
  config: Record<string, unknown>;
  config_reload_strategy: string;
}

export interface PluginListResponse {
  items: PluginItem[];
}

export interface PluginUpdateResponse {
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  config_reload_strategy: string;
}

// ── Audit types ─────────────────────────────────────────────────────────

export interface AuditEntryItem {
  id: number;
  actor: string;
  action: string;
  target: string;
  detail: Record<string, unknown>;
  ip: string;
  success: boolean;
  created_at: string;
}

export interface AuditListResponse {
  items: AuditEntryItem[];
  has_more: boolean;
}

// ── Data API calls ──────────────────────────────────────────────────────

export const dataApi = {
  sessions: (limit = 50, beforeId?: string) => {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (beforeId) params.set("before_id", beforeId);
    return api.get<SessionListResponse>(`/admin/api/data/sessions?${params.toString()}`);
  },

  sessionMessages: (sessionId: string, limit = 50, beforeSeq?: number) => {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (beforeSeq !== undefined) params.set("before_seq", String(beforeSeq));
    return api.get<MessageListResponse>(
      `/admin/api/data/sessions/${encodeURIComponent(sessionId)}/messages?${params.toString()}`,
    );
  },

  sessionSummary: (sessionId: string) =>
    api.get<SessionSummaryResponse>(
      `/admin/api/data/sessions/${encodeURIComponent(sessionId)}/summary`,
    ),

  deleteSession: (sessionId: string) =>
    api.del(`/admin/api/data/sessions/${encodeURIComponent(sessionId)}`),

  users: (limit = 50, beforeUserId?: number) => {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (beforeUserId !== undefined) params.set("before_user_id", String(beforeUserId));
    return api.get<UserProfileListResponse>(`/admin/api/data/users?${params.toString()}`);
  },

  userDetail: (uid: number) =>
    api.get<UserProfileDetailResponse>(`/admin/api/data/users/${uid}`),

  deleteUser: (uid: number) =>
    api.del(`/admin/api/data/users/${uid}`),

  deleteAllUsers: () =>
    api.del("/admin/api/data/users"),

  socialGraph: () =>
    api.get<SocialGraphResponse>("/admin/api/data/social-graph"),

  deleteSocialGraph: () =>
    api.del("/admin/api/data/social-graph"),

  exportData: () =>
    api.get<unknown>("/admin/api/data/export"),

  importData: (data: unknown) =>
    api.post<ImportResponse>("/admin/api/data/import", { confirm: true, data }),
};

// ── Plugins API calls ───────────────────────────────────────────────────

export const pluginsApi = {
  list: () =>
    api.get<PluginListResponse>("/admin/api/plugins"),

  update: (name: string, body: { enabled?: boolean; config?: Record<string, unknown> }) =>
    api.put<PluginUpdateResponse>(
      `/admin/api/plugins/${encodeURIComponent(name)}`,
      body,
    ),
};

// ── Audit API calls ─────────────────────────────────────────────────────

export const auditApi = {
  query: (params: { actor?: string; action?: string; limit?: number; beforeId?: number }) => {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.beforeId !== undefined) qs.set("before_id", String(params.beforeId));
    if (params.actor) qs.set("actor", params.actor);
    if (params.action) qs.set("action", params.action);
    return api.get<AuditListResponse>(`/admin/api/audit?${qs.toString()}`);
  },
};
