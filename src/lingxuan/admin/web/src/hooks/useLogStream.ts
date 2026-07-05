/** WebSocket log stream hook: real-time log tailing with reconnect + token refresh. */

import { useEffect, useRef, useCallback, useState } from "react";
import {
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
  clearTokens,
} from "../auth/tokens";
import type { LogRecordItem } from "../api/client";

// ── Constants ──────────────────────────────────────────────────────────────

const MAX_LOGS = 1000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const WS_CLOSE_AUTH = 4001;

// ── Types ──────────────────────────────────────────────────────────────────

export interface LogFilter {
  level: string; // "" means all
  keyword: string;
}

export type WsStatus = "connecting" | "connected" | "disconnected" | "reconnecting";

export interface UseLogStreamReturn {
  logs: LogRecordItem[];
  wsStatus: WsStatus;
  filter: LogFilter;
  setFilter: (f: LogFilter) => void;
  paused: boolean;
  setPaused: (p: boolean) => void;
  clearLogs: () => void;
  setHistory: (records: LogRecordItem[]) => void;
}

// ── Token refresh (reuses /admin/api/auth/refresh) ─────────────────────────

async function refreshAccessToken(): Promise<string | null> {
  const refresh = getRefreshToken();
  if (!refresh) return null;
  try {
    const res = await fetch("/admin/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) {
      clearTokens();
      return null;
    }
    const data = await res.json();
    setAccessToken(data.access_token);
    setRefreshToken(data.refresh_token);
    return data.access_token as string;
  } catch {
    clearTokens();
    return null;
  }
}

// ── Hook ───────────────────────────────────────────────────────────────────

export function useLogStream(): UseLogStreamReturn {
  const [logs, setLogs] = useState<LogRecordItem[]>([]);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const [filter, setFilterState] = useState<LogFilter>({ level: "", keyword: "" });
  const [paused, setPaused] = useState(false);

  // Refs for values needed inside WS callbacks without re-creating the WS
  const wsRef = useRef<WebSocket | null>(null);
  const filterRef = useRef(filter);
  const pausedRef = useRef(paused);
  const retryRef = useRef(0);
  const unmountedRef = useRef(false);

  // Keep refs in sync
  filterRef.current = filter;
  pausedRef.current = paused;

  // Append a log, enforcing the MAX_LOGS cap
  const appendLog = useCallback((record: LogRecordItem) => {
    setLogs((prev) => {
      const next = [...prev, record];
      return next.length > MAX_LOGS ? next.slice(next.length - MAX_LOGS) : next;
    });
  }, []);

  // Reset history with a batch (initial load)
  const setHistory = useCallback((records: LogRecordItem[]) => {
    setLogs(records.length > MAX_LOGS ? records.slice(records.length - MAX_LOGS) : records);
  }, []);

  // Send filter message to WS if connected
  const sendFilterToWs = useCallback((f: LogFilter) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "filter",
          level: f.level || null,
          keyword: f.keyword,
        }),
      );
    }
  }, []);

  // Build WS URL
  const buildWsUrl = useCallback((token: string) => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    return `${proto}//${host}/admin/ws/logs?token=${encodeURIComponent(token)}`;
  }, []);

  // ── Connect / reconnect ────────────────────────────────────────────────

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const token = getAccessToken();
    if (!token) {
      setWsStatus("disconnected");
      return;
    }

    setWsStatus(retryRef.current > 0 ? "reconnecting" : "connecting");

    const ws = new WebSocket(buildWsUrl(token));
    wsRef.current = ws;

    ws.onopen = () => {
      retryRef.current = 0;
      setWsStatus("connected");
      // Sync current filter to the new connection
      const f = filterRef.current;
      if (f.level || f.keyword) {
        ws.send(
          JSON.stringify({
            type: "filter",
            level: f.level || null,
            keyword: f.keyword,
          }),
        );
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "log") {
          if (!pausedRef.current) {
            appendLog({
              ts: msg.ts,
              level: msg.level,
              logger: msg.logger,
              msg: msg.msg,
              extra: msg.extra ?? {},
            });
          }
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = (ev) => {
      wsRef.current = null;
      if (unmountedRef.current) return;

      // Auth failure — try refresh then reconnect once
      if (ev.code === WS_CLOSE_AUTH) {
        refreshAccessToken().then((newToken) => {
          if (newToken && !unmountedRef.current) {
            retryRef.current = 0;
            connect();
          } else {
            setWsStatus("disconnected");
          }
        });
        return;
      }

      // Normal close or other — reconnect with backoff
      scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose will fire after onerror; reconnect logic is there
    };
  }, [appendLog, buildWsUrl]);

  const scheduleReconnect = useCallback(() => {
    if (unmountedRef.current) return;
    setWsStatus("reconnecting");

    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(2, retryRef.current),
      RECONNECT_MAX_MS,
    );
    retryRef.current += 1;

    setTimeout(() => {
      if (!unmountedRef.current) connect();
    }, delay);
  }, [connect]);

  // ── Lifecycle: connect on mount, disconnect on unmount ──────────────────

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Filter setter (updates both state and WS) ──────────────────────────

  const setFilter = useCallback(
    (f: LogFilter) => {
      setFilterState(f);
      sendFilterToWs(f);
    },
    [sendFilterToWs],
  );

  // ── Clear logs ─────────────────────────────────────────────────────────

  const clearLogs = useCallback(() => setLogs([]), []);

  return { logs, wsStatus, filter, setFilter, paused, setPaused, clearLogs, setHistory };
}
