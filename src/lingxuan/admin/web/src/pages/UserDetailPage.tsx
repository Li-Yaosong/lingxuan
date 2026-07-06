/** User profile detail page. */

import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/context";
import { dataApi, type UserProfileDetailResponse } from "../api/client";
import ConfirmModal from "../components/ConfirmModal";
import { formatTime, stageLabel } from "../utils/format";

export default function UserDetailPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();
  const { uid: uidParam } = useParams<{ uid: string }>();
  const uid = Number(uidParam);

  const [profile, setProfile] = useState<UserProfileDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loadProfile = useCallback(async () => {
    if (isNaN(uid)) {
      setError("无效的用户 ID");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await dataApi.userDetail(uid);
      setProfile(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载用户档案失败");
    } finally {
      setLoading(false);
    }
  }, [uid]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const handleDelete = useCallback(async () => {
    setConfirmOpen(false);
    try {
      await dataApi.deleteUser(uid);
      navigate("/data/users");
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  }, [uid, navigate]);

  if (loading) {
    return <p className="loading-text">加载中…</p>;
  }

  if (error && !profile) {
    return (
      <>
        <button type="button" className="back-link" onClick={() => navigate("/data/users")}>
          ← 返回用户列表
        </button>
        <p className="form-error">{error}</p>
      </>
    );
  }

  if (!profile) return null;

  return (
    <>
      <a className="back-link" onClick={() => navigate("/data/users")}>
        ← 返回用户列表
      </a>

      {error && <p className="form-error">{error}</p>}

      {/* Identity */}
      <div className="detail-section">
        <h3>身份信息</h3>
        <div className="detail-grid">
          <span className="detail-label">用户 ID</span>
          <span>{profile.user_id}</span>
          <span className="detail-label">昵称</span>
          <span>{profile.preferred_name || "—"}</span>
          <span className="detail-label">别名</span>
          <span>{profile.aliases.length > 0 ? profile.aliases.join(", ") : "—"}</span>
          <span className="detail-label">关系阶段</span>
          <span>
            <span className="badge badge-stage">{stageLabel(profile.stage)}</span>
          </span>
          <span className="detail-label">首次相遇</span>
          <span>{profile.first_met_at ? formatTime(profile.first_met_at) : "—"}</span>
          <span className="detail-label">最后见面</span>
          <span>{profile.last_seen_at ? formatTime(profile.last_seen_at) : "—"}</span>
          <span className="detail-label">交互次数</span>
          <span>{profile.interaction_count}</span>
          <span className="detail-label">最后群号</span>
          <span>{profile.last_group_id ?? "—"}</span>
          <span className="detail-label">见过私聊</span>
          <span>{profile.seen_in_private ? "是" : "否"}</span>
          <span className="detail-label">见过群聊</span>
          <span>{profile.seen_in_group ? "是" : "否"}</span>
        </div>
      </div>

      {/* Group cards */}
      {Object.keys(profile.group_cards).length > 0 && (
        <div className="detail-section">
          <h3>群名片</h3>
          <div className="detail-grid">
            {Object.entries(profile.group_cards).map(([gid, card]) => (
              <span key={gid}>
                群 {gid}: {card}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Impression */}
      {profile.impression && (
        <div className="detail-section">
          <h3>印象</h3>
          <div className="summary-block">{profile.impression}</div>
        </div>
      )}

      {/* Cognition */}
      {profile.cognition_summary && (
        <div className="detail-section">
          <h3>认知整合</h3>
          <div className="summary-block">{profile.cognition_summary}</div>
        </div>
      )}

      {/* Facts */}
      <div className="detail-section">
        <h3>Facts ({profile.facts.length})</h3>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>内容</th>
                <th>类别</th>
                <th>活跃</th>
                <th>学习时间</th>
              </tr>
            </thead>
            <tbody>
              {profile.facts.length === 0 ? (
                <tr>
                  <td colSpan={5} className="data-table-empty">
                    暂无 facts
                  </td>
                </tr>
              ) : (
                profile.facts.map((f) => (
                  <tr key={f.id}>
                    <td className="session-id">{f.id.slice(0, 8)}</td>
                    <td>{f.content}</td>
                    <td>
                      <span className="badge badge-info">{f.category}</span>
                    </td>
                    <td>
                      <span className={`badge ${f.active ? "badge-ok" : "badge-err"}`}>
                        {f.active ? "是" : "否"}
                      </span>
                    </td>
                    <td>{f.learned_at ? formatTime(f.learned_at) : "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Admin: delete */}
      {isAdmin && (
        <div style={{ marginTop: "1rem" }}>
          <button className="btn-danger btn-sm" onClick={() => setConfirmOpen(true)}>
            删除此用户
          </button>
        </div>
      )}

      <ConfirmModal
        open={confirmOpen}
        title="删除用户"
        message={`确认删除用户 ${uid}？此操作不可撤销。`}
        onConfirm={handleDelete}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}
