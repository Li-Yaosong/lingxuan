/** Logs page: history load + WebSocket real-time tail + level/keyword filter. */

import { useEffect, useRef, useCallback, useState } from "react";
import { logsApi, type LogRecordItem } from "../api/client";
import { useLogStream, type WsStatus } from "../hooks/useLogStream";
import { clientFilter } from "../utils/logsFilter";

// ── Level options for the UI ─────────────────────────────────────────────────

const LEVEL_OPTIONS = ["", "DEBUG", "INFO", "WARNING", "ERROR"] as const;

const LEVEL_LABELS: Record<string, string> = {
  "": "全部级别",
  DEBUG: "DEBUG",
  INFO: "INFO",
  WARNING: "WARNING",
  ERROR: "ERROR",
};

// ── Format timestamp for display ───────────────────────────────────────────

function formatTs(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

// ── Component ──────────────────────────────────────────────────────────────

export default function LogsPage() {
  const {
    logs,
    wsStatus,
    filter,
    setFilter,
    paused,
    setPaused,
    clearLogs,
    setHistory,
  } = useLogStream();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Local filter inputs (debounced for keyword)
  const [levelInput, setLevelInput] = useState(filter.level);
  const [keywordInput, setKeywordInput] = useState(filter.keyword);
  const keywordTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-scroll
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  // ── Load history on mount ──────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await logsApi.history(200);
        if (!cancelled) {
          setHistory(data.records);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "加载日志失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setHistory]);

  // ── Level filter: immediate ────────────────────────────────────────────

  const handleLevelChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const level = e.target.value;
      setLevelInput(level);
      setFilter({ level, keyword: filter.keyword });
    },
    [filter.keyword, setFilter],
  );

  // ── Keyword filter: debounced (300ms) ──────────────────────────────────

  const handleKeywordChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setKeywordInput(val);
      if (keywordTimerRef.current) clearTimeout(keywordTimerRef.current);
      keywordTimerRef.current = setTimeout(() => {
        setFilter({ level: filter.level, keyword: val });
      }, 300);
    },
    [filter.level, setFilter],
  );

  // ── Auto-scroll ────────────────────────────────────────────────────────

  // Clear debounce timer on unmount
  useEffect(() => {
    return () => {
      if (keywordTimerRef.current) clearTimeout(keywordTimerRef.current);
    };
  }, []);

  // Detect if user has scrolled up (disable auto-scroll)
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    autoScrollRef.current = atBottom;
  }, []);

  // Scroll to bottom when new logs arrive (if auto-scroll is on)
  useEffect(() => {
    if (autoScrollRef.current && !paused) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, paused]);

  // ── Apply client-side filter to all loaded logs ────────────────────────

  const filtered = clientFilter(logs, filter.level, filter.keyword);

  // ── Render ─────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="page">
        <h1>日志</h1>
        <p className="loading-text">加载中…</p>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>日志</h1>
        <div className="logs-header-actions">
          <WsStatusBadge status={wsStatus} />
          <button
            className={`btn-sm ${paused ? "btn-outline" : "btn-primary"}`}
            onClick={() => setPaused(!paused)}
          >
            {paused ? "▶ 恢复" : "⏸ 暂停"}
          </button>
          <button className="btn-sm btn-outline" onClick={clearLogs}>
            清空
          </button>
        </div>
      </div>

      {error && <p className="form-error">{error}</p>}

      {/* Filter bar */}
      <div className="logs-filter-bar">
        <select
          className="logs-level-select"
          value={levelInput}
          onChange={handleLevelChange}
        >
          {LEVEL_OPTIONS.map((l) => (
            <option key={l} value={l}>
              {LEVEL_LABELS[l]}
            </option>
          ))}
        </select>
        <input
          className="logs-keyword-input"
          type="text"
          placeholder="关键词搜索…"
          value={keywordInput}
          onChange={handleKeywordChange}
        />
        <span className="logs-count">
          {filtered.length} / {logs.length} 条
        </span>
      </div>

      {/* Log list */}
      <div
        className="logs-container"
        ref={containerRef}
        onScroll={handleScroll}
      >
        <table className="logs-table">
          <thead>
            <tr>
              <th className="col-ts">时间</th>
              <th className="col-level">级别</th>
              <th className="col-logger">Logger</th>
              <th className="col-msg">消息</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={4} className="logs-empty">
                  暂无日志
                </td>
              </tr>
            ) : (
              filtered.map((r, i) => (
                <tr key={`${r.ts}-${i}`} className={`log-row log-${r.level.toLowerCase()}`}>
                  <td className="col-ts">{formatTs(r.ts)}</td>
                  <td className="col-level">
                    <span className={`log-level-badge level-${r.level.toLowerCase()}`}>
                      {r.level}
                    </span>
                  </td>
                  <td className="col-logger" title={r.logger}>
                    {r.logger}
                  </td>
                  <td className="col-msg">{r.msg}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────

function WsStatusBadge({ status }: { status: WsStatus }) {
  const labels: Record<WsStatus, string> = {
    connecting: "○ 连接中",
    connected: "● 实时",
    disconnected: "○ 断开",
    reconnecting: "○ 重连",
  };
  const cls =
    status === "connected"
      ? "ws-on"
      : status === "disconnected"
        ? "ws-off"
        : "ws-reconnecting";
  return (
    <span className={`ws-indicator ${cls}`}>{labels[status]}</span>
  );
}
