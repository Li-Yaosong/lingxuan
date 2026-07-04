/** Status page: bot online state, features, memory stats, LLM check. */

import { useState, useEffect, useCallback, useRef } from "react";
import { getAccessToken } from "../auth/tokens";
import {
  statusApi,
  type StatusResponse,
  type LLMCheckResponse,
} from "../api/client";

export default function StatusPage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // LLM check state
  const [llmChecking, setLlmChecking] = useState(false);
  const [llmResult, setLlmResult] = useState<LLMCheckResponse | null>(null);

  // WebSocket for real-time updates
  const wsRef = useRef<WebSocket | null>(null);
  const [wsConnected, setWsConnected] = useState(false);

  // ── Load initial status ─────────────────────────────────────────────

  const loadStatus = useCallback(async () => {
    try {
      const data = await statusApi.get();
      setStatus(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载状态失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  // ── WebSocket for live updates ──────────────────────────────────────

  useEffect(() => {
    // Try to connect to the status WebSocket
    const connectWs = () => {
      const token = getAccessToken();
      if (!token) return; // No auth token — rely on polling
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = window.location.host;
      const wsUrl = `${proto}//${host}/admin/ws/status?token=${encodeURIComponent(token)}`;

      try {
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onopen = () => setWsConnected(true);
        ws.onclose = () => {
          setWsConnected(false);
          wsRef.current = null;
        };
        ws.onerror = () => {
          // WS failed — fall back to polling below
          ws.close();
        };
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === "status") {
              setStatus(msg);
            }
          } catch {
            // ignore malformed messages
          }
        };
      } catch {
        // WS not available, polling will handle it
      }
    };

    connectWs();

    // Fallback: poll every 15s if WS is not connected
    const poll = setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        loadStatus();
      }
    }, 15000);

    return () => {
      clearInterval(poll);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [loadStatus]);

  // ── LLM check ──────────────────────────────────────────────────────

  const handleLlmCheck = useCallback(async () => {
    setLlmChecking(true);
    setLlmResult(null);
    try {
      const result = await statusApi.llmCheck();
      setLlmResult(result);
    } catch (e) {
      setLlmResult({
        ok: false,
        latency_ms: 0,
        error: e instanceof Error ? e.message : "请求失败",
      });
    } finally {
      setLlmChecking(false);
    }
  }, []);

  // ── Render ──────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="page">
        <h1>服务状态</h1>
        <p className="loading-text">加载中…</p>
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="page">
        <h1>服务状态</h1>
        <p className="form-error">{error}</p>
      </div>
    );
  }

  if (!status) return null;

  return (
    <div className="page">
      <div className="page-header">
        <h1>服务状态</h1>
        <span className={`ws-indicator ${wsConnected ? "ws-on" : "ws-off"}`}>
          {wsConnected ? "● 实时" : "○ 轮询"}
        </span>
      </div>

      {/* Bot online & model */}
      <div className="status-overview">
        <div className="status-card">
          <div className="status-card-label">Bot 连接</div>
          <div className={`status-card-value ${status.bot_online ? "val-ok" : "val-err"}`}>
            {status.bot_online ? "在线" : "离线"}
          </div>
        </div>
        <div className="status-card">
          <div className="status-card-label">模型</div>
          <div className="status-card-value">{status.model}</div>
        </div>
        <div className="status-card">
          <div className="status-card-label">LLM 测试</div>
          <div className="status-card-value">
            <button
              className="btn-primary btn-sm"
              disabled={llmChecking}
              onClick={handleLlmCheck}
            >
              {llmChecking ? "测试中…" : "测试 LLM"}
            </button>
            {llmResult && (
              <span className={`llm-result ${llmResult.ok ? "val-ok" : "val-err"}`}>
                {llmResult.ok
                  ? `✓ ${llmResult.latency_ms.toFixed(0)} ms`
                  : `✗ ${llmResult.error || "失败"}`}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Feature flags */}
      <fieldset className="config-group">
        <legend>功能开关</legend>
        <div className="feature-grid">
          {Object.entries(status.features).map(([key, enabled]) => (
            <div key={key} className="feature-item">
              <span className={`feature-dot ${enabled ? "dot-on" : "dot-off"}`} />
              <span className="feature-name">{featureLabel(key)}</span>
              <span className="feature-status">{enabled ? "启用" : "禁用"}</span>
            </div>
          ))}
        </div>
      </fieldset>

      {/* Memory stats */}
      <fieldset className="config-group">
        <legend>记忆统计</legend>
        <div className="stats-grid">
          <StatItem label="会话数" value={status.memory_stats.sessions} />
          <StatItem label="消息数" value={status.memory_stats.messages} />
          <StatItem label="用户数" value={status.memory_stats.users} />
          <StatItem label="活跃 Facts" value={status.memory_stats.active_facts} />
          <StatItem label="社会图边" value={status.memory_stats.edges} />
        </div>
      </fieldset>

      {/* Group observe states */}
      {status.observe_states.length > 0 && (
        <fieldset className="config-group">
          <legend>群观察状态</legend>
          <div className="observe-table-wrap">
            <table className="observe-table">
              <thead>
                <tr>
                  <th>群号</th>
                  <th>缓冲消息</th>
                  <th>最近判断</th>
                  <th>冷却中</th>
                  <th>冷却剩余</th>
                  <th>观察中</th>
                </tr>
              </thead>
              <tbody>
                {status.observe_states.map((s) => (
                  <tr key={s.group_id}>
                    <td>{s.group_id}</td>
                    <td>{s.buffer_len}</td>
                    <td>{s.last_judge_result || "—"}</td>
                    <td>{s.in_cooldown ? "是" : "否"}</td>
                    <td>{s.cooldown_remaining > 0 ? `${s.cooldown_remaining.toFixed(1)}s` : "—"}</td>
                    <td>{s.observe_in_flight ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </fieldset>
      )}
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────

function StatItem({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-item">
      <div className="stat-value">{value.toLocaleString()}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────

const FEATURE_LABELS: Record<string, string> = {
  ENABLE_PRIVATE_CHAT: "私聊",
  ENABLE_GROUP_CHAT: "群聊回复",
  ENABLE_GROUP_OBSERVE: "群观察",
  ENABLE_MEMORY_SUMMARY: "记忆摘要",
  ENABLE_USER_MEMORY: "用户记忆",
  ENABLE_USER_COGNITION_REFINE: "认知整合",
  ENABLE_STREAM_CHUNK: "分片发送",
};

function featureLabel(key: string): string {
  return FEATURE_LABELS[key] || key;
}
