/** Audit log page: filtered keyset-paginated access (admin-only). */

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../auth/context";
import { auditApi, type AuditEntryItem } from "../api/client";
import { formatTime } from "../utils/format";

const ACTION_OPTIONS = [
  { value: "", label: "全部操作" },
  { value: "data.export", label: "data.export" },
  { value: "data.import", label: "data.import" },
  { value: "data.delete_session", label: "data.delete_session" },
  { value: "data.delete_user", label: "data.delete_user" },
  { value: "data.delete_all_users", label: "data.delete_all_users" },
  { value: "data.delete_social_graph", label: "data.delete_social_graph" },
  { value: "plugin.update", label: "plugin.update" },
  { value: "auth.change_password", label: "auth.change_password" },
  { value: "auth.create_user", label: "auth.create_user" },
];

export default function AuditPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [entries, setEntries] = useState<AuditEntryItem[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [actorInput, setActorInput] = useState("");
  const [actionInput, setActionInput] = useState("");
  const [appliedActor, setAppliedActor] = useState("");
  const [appliedAction, setAppliedAction] = useState("");

  // Detail expansion
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const loadEntries = useCallback(
    async (beforeId?: number) => {
      const isMore = beforeId !== undefined;
      if (isMore) setLoadingMore(true);
      else setLoading(true);
      setError(null);
      try {
        const resp = await auditApi.query({
          actor: appliedActor || undefined,
          action: appliedAction || undefined,
          limit: 50,
          beforeId,
        });
        if (isMore) {
          setEntries((prev) => [...prev, ...resp.items]);
        } else {
          setEntries(resp.items);
        }
        setHasMore(resp.has_more);
      } catch (e) {
        setError(e instanceof Error ? e.message : "加载审计日志失败");
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [appliedActor, appliedAction],
  );

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  const handleFilter = useCallback(() => {
    setAppliedActor(actorInput.trim());
    setAppliedAction(actionInput);
  }, [actorInput, actionInput]);

  // ── Permission check ────────────────────────────────────────────

  if (!isAdmin) {
    return (
      <div className="page">
        <h1>审计日志</h1>
        <div className="no-permission">
          <p>无权限访问此页面</p>
          <p>仅管理员可查看审计日志。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>审计日志</h1>
      </div>

      {/* Filter bar */}
      <div className="audit-filter-bar">
        <input
          className="audit-filter-input"
          type="text"
          placeholder="操作者"
          value={actorInput}
          onChange={(e) => setActorInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleFilter();
          }}
        />
        <select
          className="audit-filter-input"
          value={actionInput}
          onChange={(e) => setActionInput(e.target.value)}
        >
          {ACTION_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <button className="btn-outline btn-sm" onClick={handleFilter}>
          筛选
        </button>
      </div>

      {error && <p className="form-error">{error}</p>}

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: "140px" }}>时间</th>
              <th>操作者</th>
              <th>操作</th>
              <th>目标</th>
              <th style={{ width: "60px" }}>结果</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 && !loading ? (
              <tr>
                <td colSpan={6} className="data-table-empty">
                  暂无审计记录
                </td>
              </tr>
            ) : (
              entries.map((e) => (
                <tr key={e.id}>
                  <td>{formatTime(e.created_at, true)}</td>
                  <td>{e.actor}</td>
                  <td>{e.action}</td>
                  <td>{e.target || "—"}</td>
                  <td>
                    <span className={`badge ${e.success ? "badge-ok" : "badge-err"}`}>
                      {e.success ? "✓" : "✗"}
                    </span>
                  </td>
                  <td>
                    {Object.keys(e.detail).length > 0 ? (
                      <>
                        <button
                          className="audit-detail-toggle"
                          onClick={() =>
                            setExpandedId(expandedId === e.id ? null : e.id)
                          }
                        >
                          {expandedId === e.id ? "收起" : "展开"}
                        </button>
                        {expandedId === e.id && (
                          <pre className="audit-detail-pre">
                            {JSON.stringify(e.detail, null, 2)}
                          </pre>
                        )}
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="paginator">
        {hasMore ? (
          <button
            className="btn-outline btn-sm"
            disabled={loadingMore}
            onClick={() => {
              const last = entries[entries.length - 1];
              if (last) loadEntries(last.id);
            }}
          >
            {loadingMore ? "加载中…" : "加载更多"}
          </button>
        ) : (
          entries.length > 0 && (
            <span className="paginator-muted">已全部加载</span>
          )
        )}
      </div>
    </div>
  );
}
