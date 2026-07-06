/** Data management page: sessions / users / social graph tabs + export/import. */

import { useState, useEffect, useCallback, useRef, type ChangeEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/context";
import { dataApi, type SessionItem, type UserProfileItem, type SocialGraphResponse, type ImportResponse } from "../api/client";
import ConfirmModal from "../components/ConfirmModal";
import { formatTime, stageLabel } from "../utils/format";

// ── Main page shell ─────────────────────────────────────────────────────

export default function DataPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  // Export / import state
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResponse | null>(null);
  const [importPreview, setImportPreview] = useState<unknown>(null);
  const [importConfirmOpen, setImportConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── Export ───────────────────────────────────────────────────────

  const handleExport = useCallback(async () => {
    setExporting(true);
    setError(null);
    try {
      const data = await dataApi.exportData();
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `lingxuan-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }, []);

  // ── Import ──────────────────────────────────────────────────────

  const handleFileSelect = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const parsed = JSON.parse(reader.result as string);
          setImportPreview(parsed);
          setImportConfirmOpen(true);
        } catch {
          setError("文件格式错误：无法解析 JSON");
        }
      };
      reader.readAsText(file);
      e.target.value = "";
    },
    [],
  );

  const handleImportConfirm = useCallback(async () => {
    if (!importPreview) return;
    setImportConfirmOpen(false);
    setImporting(true);
    setError(null);
    try {
      const result = await dataApi.importData(importPreview);
      setImportResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setImporting(false);
      setImportPreview(null);
    }
  }, [importPreview]);

  return (
    <div className="page">
      <div className="page-header">
        <h1>数据管理</h1>
        {isAdmin && (
          <div className="data-actions">
            <button
              className="btn-outline btn-sm"
              disabled={exporting}
              onClick={handleExport}
            >
              {exporting ? "导出中…" : "导出"}
            </button>
            <button
              className="btn-outline btn-sm"
              disabled={importing}
              onClick={() => fileInputRef.current?.click()}
            >
              {importing ? "导入中…" : "导入"}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              style={{ display: "none" }}
              onChange={handleFileSelect}
            />
          </div>
        )}
      </div>

      {error && <p className="form-error">{error}</p>}

      {importResult && (
        <div className="config-results">
          {Object.entries(importResult.imported).map(([key, count]) => (
            <div key={key} className="config-result-item result-ok">
              <span className="result-key">{key}</span>
              <span className="result-status">{count} 条</span>
            </div>
          ))}
        </div>
      )}

      {/* Tab bar */}
      <nav className="data-tabs">
        <NavLink
          to="/data"
          end
          className={({ isActive }) =>
            `data-tab${isActive ? " active" : ""}`
          }
        >
          会话
        </NavLink>
        <NavLink
          to="/data/users"
          className={({ isActive }) =>
            `data-tab${isActive ? " active" : ""}`
          }
        >
          用户
        </NavLink>
        <NavLink
          to="/data/social-graph"
          className={({ isActive }) =>
            `data-tab${isActive ? " active" : ""}`
          }
        >
          社会关系
        </NavLink>
      </nav>

      <Outlet />

      {/* Import confirm modal */}
      <ConfirmModal
        open={importConfirmOpen}
        title="确认导入"
        message="导入将覆盖现有数据，确认继续？"
        onConfirm={handleImportConfirm}
        onCancel={() => {
          setImportConfirmOpen(false);
          setImportPreview(null);
        }}
      />
    </div>
  );
}

// ── Sessions tab ────────────────────────────────────────────────────────

export function DataSessionsTab() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();

  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Delete confirm
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    kind: string;
  } | null>(null);

  const loadSessions = useCallback(async (beforeId?: string) => {
    const isMore = !!beforeId;
    if (isMore) setLoadingMore(true);
    else setLoading(true);
    setError(null);
    try {
      const resp = await dataApi.sessions(50, beforeId);
      if (isMore) {
        setSessions((prev) => [...prev, ...resp.items]);
      } else {
        setSessions(resp.items);
      }
      setHasMore(resp.has_more);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载会话失败");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    try {
      await dataApi.deleteSession(deleteTarget.id);
      setDeleteTarget(null);
      loadSessions();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
      setDeleteTarget(null);
    }
  }, [deleteTarget, loadSessions]);

  if (loading) {
    return <p className="loading-text">加载中…</p>;
  }

  return (
    <>
      {error && <p className="form-error">{error}</p>}

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>会话 ID</th>
              <th>类型</th>
              <th>最后活跃</th>
              <th>消息数</th>
              {isAdmin && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 ? (
              <tr>
                <td colSpan={isAdmin ? 5 : 4} className="data-table-empty">
                  暂无会话
                </td>
              </tr>
            ) : (
              sessions.map((s) => (
                <tr key={s.id} className="clickable-row">
                  <td>
                    <span
                      className="session-id"
                      onClick={() => navigate(`/data/sessions/${encodeURIComponent(s.id)}`)}
                    >
                      {s.id}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`badge ${s.kind === "private" ? "badge-kind-private" : "badge-kind-group"}`}
                    >
                      {s.kind === "private" ? "私聊" : "群聊"}
                    </span>
                  </td>
                  <td>{s.last_active_at ? formatTime(s.last_active_at) : "—"}</td>
                  <td>{s.message_count}</td>
                  {isAdmin && (
                    <td>
                      <button
                        className="btn-danger"
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.5rem" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteTarget({ id: s.id, kind: s.kind });
                        }}
                      >
                        删除
                      </button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Paginator */}
      <div className="paginator">
        {hasMore ? (
          <button
            className="btn-outline btn-sm"
            disabled={loadingMore}
            onClick={() => {
              const last = sessions[sessions.length - 1];
              if (last) loadSessions(last.id);
            }}
          >
            {loadingMore ? "加载中…" : "加载更多"}
          </button>
        ) : (
          sessions.length > 0 && <span className="paginator-muted">已全部加载</span>
        )}
      </div>

      <ConfirmModal
        open={!!deleteTarget}
        title="删除会话"
        message={`确认删除会话 ${deleteTarget?.id ?? ""}？此操作不可撤销。`}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}

// ── Users tab ───────────────────────────────────────────────────────────

export function DataUsersTab() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();

  const [users, setUsers] = useState<UserProfileItem[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<{
    type: "single" | "all";
    uid?: number;
  } | null>(null);

  const loadUsers = useCallback(async (beforeUserId?: number) => {
    const isMore = beforeUserId !== undefined;
    if (isMore) setLoadingMore(true);
    else setLoading(true);
    setError(null);
    try {
      const resp = await dataApi.users(50, beforeUserId);
      if (isMore) {
        setUsers((prev) => [...prev, ...resp.items]);
      } else {
        setUsers(resp.items);
      }
      setHasMore(resp.has_more);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载用户失败");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    try {
      if (deleteTarget.type === "all") {
        await dataApi.deleteAllUsers();
      } else if (deleteTarget.uid !== undefined) {
        await dataApi.deleteUser(deleteTarget.uid);
      }
      setDeleteTarget(null);
      loadUsers();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
      setDeleteTarget(null);
    }
  }, [deleteTarget, loadUsers]);

  if (loading) {
    return <p className="loading-text">加载中…</p>;
  }

  return (
    <>
      {error && <p className="form-error">{error}</p>}

      {isAdmin && users.length > 0 && (
        <div style={{ marginBottom: "0.75rem" }}>
          <button
            className="btn-danger btn-sm"
            onClick={() => setDeleteTarget({ type: "all" })}
          >
            清除所有用户
          </button>
        </div>
      )}

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>用户 ID</th>
              <th>昵称</th>
              <th>关系阶段</th>
              <th>交互次数</th>
              {isAdmin && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {users.length === 0 ? (
              <tr>
                <td colSpan={isAdmin ? 5 : 4} className="data-table-empty">
                  暂无用户
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr
                  key={u.user_id}
                  className="clickable-row"
                  onClick={() => navigate(`/data/users/${u.user_id}`)}
                >
                  <td>{u.user_id}</td>
                  <td>{u.preferred_name || "—"}</td>
                  <td>
                    <span className="badge badge-stage">{stageLabel(u.stage)}</span>
                  </td>
                  <td>{u.interaction_count}</td>
                  {isAdmin && (
                    <td>
                      <button
                        className="btn-danger"
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.5rem" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setDeleteTarget({ type: "single", uid: u.user_id });
                        }}
                      >
                        删除
                      </button>
                    </td>
                  )}
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
              const last = users[users.length - 1];
              if (last) loadUsers(last.user_id);
            }}
          >
            {loadingMore ? "加载中…" : "加载更多"}
          </button>
        ) : (
          users.length > 0 && <span className="paginator-muted">已全部加载</span>
        )}
      </div>

      <ConfirmModal
        open={!!deleteTarget}
        title={deleteTarget?.type === "all" ? "清除所有用户" : "删除用户"}
        message={
          deleteTarget?.type === "all"
            ? "确认清除所有用户？同时会删除社会关系图。此操作不可撤销。"
            : `确认删除用户 ${deleteTarget?.uid ?? ""}？此操作不可撤销。`
        }
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}

// ── Social graph tab ────────────────────────────────────────────────────

export function DataSocialGraphTab() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [graph, setGraph] = useState<SocialGraphResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loadGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await dataApi.socialGraph();
      setGraph(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载社会关系图失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  const handleDelete = useCallback(async () => {
    setConfirmOpen(false);
    try {
      await dataApi.deleteSocialGraph();
      loadGraph();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  }, [loadGraph]);

  if (loading) {
    return <p className="loading-text">加载中…</p>;
  }

  if (error && !graph) {
    return <p className="form-error">{error}</p>;
  }

  return (
    <>
      {error && <p className="form-error">{error}</p>}

      {isAdmin && graph && graph.edges.length > 0 && (
        <div style={{ marginBottom: "0.75rem" }}>
          <button
            className="btn-danger btn-sm"
            onClick={() => setConfirmOpen(true)}
          >
            清除社会关系图
          </button>
        </div>
      )}

      {graph && (
        <>
          {/* Edges table */}
          <fieldset className="config-group">
            <legend>关系边 ({graph.edges.length})</legend>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>发起</th>
                    <th>目标</th>
                    <th>关系</th>
                    <th>标签</th>
                    <th>证据</th>
                    <th>群号</th>
                    <th>学习时间</th>
                  </tr>
                </thead>
                <tbody>
                  {graph.edges.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="data-table-empty">
                        暂无关系边
                      </td>
                    </tr>
                  ) : (
                    graph.edges.map((e, i) => (
                      <tr key={i}>
                        <td>{e.from_user_id}</td>
                        <td>{e.to_user_id}</td>
                        <td>{e.relation}</td>
                        <td>{e.label || "—"}</td>
                        <td>{e.evidence || "—"}</td>
                        <td>{e.group_id ?? "—"}</td>
                        <td>{e.learned_at ? formatTime(e.learned_at) : "—"}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </fieldset>

          {/* Name index */}
          <fieldset className="config-group">
            <legend>名称索引 ({Object.keys(graph.name_index).length})</legend>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>用户 ID</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(graph.name_index).length === 0 ? (
                    <tr>
                      <td colSpan={2} className="data-table-empty">
                        暂无索引
                      </td>
                    </tr>
                  ) : (
                    Object.entries(graph.name_index).map(([name, uid]) => (
                      <tr key={name}>
                        <td>{name}</td>
                        <td>{uid}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </fieldset>
        </>
      )}

      <ConfirmModal
        open={confirmOpen}
        title="清除社会关系图"
        message="确认清除所有社会关系图数据？此操作不可撤销。"
        onConfirm={handleDelete}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}

