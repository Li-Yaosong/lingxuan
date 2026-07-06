/** Session detail page: messages + summary. */

import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/context";
import { dataApi, type MessageItem, type SessionSummaryResponse } from "../api/client";
import ConfirmModal from "../components/ConfirmModal";
import { formatTime } from "../utils/format";

export default function SessionDetailPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId: string }>();

  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [summary, setSummary] = useState<SessionSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const sid = sessionId ?? "";

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [msgResp, sumResp] = await Promise.all([
        dataApi.sessionMessages(sid),
        dataApi.sessionSummary(sid),
      ]);
      setMessages(msgResp.items);
      setHasMore(msgResp.has_more);
      setSummary(sumResp);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载会话详情失败");
    } finally {
      setLoading(false);
    }
  }, [sid]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const loadMoreMessages = useCallback(async () => {
    if (messages.length === 0) return;
    const last = messages[messages.length - 1]!;
    setLoadingMore(true);
    try {
      const resp = await dataApi.sessionMessages(sid, 50, last.seq);
      setMessages((prev) => [...prev, ...resp.items]);
      setHasMore(resp.has_more);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载更多消息失败");
    } finally {
      setLoadingMore(false);
    }
  }, [sid, messages]);

  const handleDelete = useCallback(async () => {
    setConfirmOpen(false);
    try {
      await dataApi.deleteSession(sid);
      navigate("/data");
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  }, [sid, navigate]);

  if (loading) {
    return <p className="loading-text">加载中…</p>;
  }

  return (
    <>
      <button type="button" className="back-link" onClick={() => navigate("/data")}>
        ← 返回会话列表
      </button>

      {error && <p className="form-error">{error}</p>}

      {/* Summary */}
      {summary && (
        <fieldset className="config-group">
          <legend>会话摘要</legend>
          <div className="detail-grid">
            <span className="detail-label">ID</span>
            <span>{summary.id}</span>
            <span className="detail-label">类型</span>
            <span>
              <span
                className={`badge ${summary.kind === "private" ? "badge-kind-private" : "badge-kind-group"}`}
              >
                {summary.kind === "private" ? "私聊" : "群聊"}
              </span>
            </span>
            {summary.nickname && (
              <>
                <span className="detail-label">昵称</span>
                <span>{summary.nickname}</span>
              </>
            )}
            {summary.group_id !== null && summary.group_id !== undefined && (
              <>
                <span className="detail-label">群号</span>
                <span>{summary.group_id}</span>
              </>
            )}
          </div>
          {summary.summary && (
            <div style={{ marginTop: "0.75rem" }}>
              <span className="detail-label">摘要</span>
              <div className="summary-block">{summary.summary}</div>
            </div>
          )}
          {Object.keys(summary.entities).length > 0 && (
            <div style={{ marginTop: "0.75rem" }}>
              <span className="detail-label">实体</span>
              <div className="detail-grid" style={{ marginTop: "0.3rem" }}>
                {Object.entries(summary.entities).map(([name, uid]) => (
                  <span key={name}>
                    {name} → {uid}
                  </span>
                ))}
              </div>
            </div>
          )}
        </fieldset>
      )}

      {/* Messages */}
      <fieldset className="config-group">
        <legend>消息历史 ({messages.length})</legend>
        <div className="data-table-wrap" style={{ maxHeight: "60vh", overflowY: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: "50px" }}>#</th>
                <th style={{ width: "70px" }}>角色</th>
                <th>内容</th>
                <th style={{ width: "70px" }}>UID</th>
                <th style={{ width: "130px" }}>时间</th>
              </tr>
            </thead>
            <tbody>
              {messages.map((m) => (
                <tr key={m.seq}>
                  <td>{m.seq}</td>
                  <td>
                    <span className={`msg-role-badge ${roleClass(m.role)}`}>
                      {m.role}
                    </span>
                  </td>
                  <td>
                    <div className="msg-content">{m.content}</div>
                  </td>
                  <td>{m.user_id ?? "—"}</td>
                  <td>{formatTime(m.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </fieldset>

      {/* Load more */}
      <div className="paginator">
        {hasMore ? (
          <button
            className="btn-outline btn-sm"
            disabled={loadingMore}
            onClick={loadMoreMessages}
          >
            {loadingMore ? "加载中…" : "加载更多"}
          </button>
        ) : (
          messages.length > 0 && (
            <span className="paginator-muted">已全部加载</span>
          )
        )}
      </div>

      {/* Admin: delete */}
      {isAdmin && (
        <div style={{ marginTop: "1rem" }}>
          <button className="btn-danger btn-sm" onClick={() => setConfirmOpen(true)}>
            删除此会话
          </button>
        </div>
      )}

      <ConfirmModal
        open={confirmOpen}
        title="删除会话"
        message={`确认删除会话 ${sid}？此操作不可撤销。`}
        onConfirm={handleDelete}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}

function roleClass(role: string): string {
  if (role === "user") return "role-user";
  if (role === "assistant") return "role-assistant";
  return "role-system";
}
