/** Plugin management page: list, toggle enable/disable, edit config. */

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../auth/context";
import { pluginsApi, type PluginItem } from "../api/client";
import ConfirmModal from "../components/ConfirmModal";

export default function PluginsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Config editing
  const [editingPlugin, setEditingPlugin] = useState<string | null>(null);
  const [configDraft, setConfigDraft] = useState("");
  const [configError, setConfigError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Toggle confirm
  const [toggleTarget, setToggleTarget] = useState<{
    name: string;
    enabled: boolean;
  } | null>(null);

  const loadPlugins = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await pluginsApi.list();
      setPlugins(resp.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载插件列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPlugins();
  }, [loadPlugins]);

  // ── Toggle enable/disable ───────────────────────────────────────

  const handleToggle = useCallback(async () => {
    if (!toggleTarget) return;
    setSaving(true);
    try {
      await pluginsApi.update(toggleTarget.name, {
        enabled: toggleTarget.enabled,
      });
      setToggleTarget(null);
      loadPlugins();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
      setToggleTarget(null);
    } finally {
      setSaving(false);
    }
  }, [toggleTarget, loadPlugins]);

  // ── Config editing ──────────────────────────────────────────────

  const openConfigEditor = useCallback(
    (plugin: PluginItem) => {
      setEditingPlugin(plugin.name);
      setConfigDraft(JSON.stringify(plugin.config, null, 2));
      setConfigError(null);
    },
    [],
  );

  const handleConfigSave = useCallback(async () => {
    if (!editingPlugin) return;
    try {
      const parsed = JSON.parse(configDraft);
      setConfigError(null);
      setSaving(true);
      await pluginsApi.update(editingPlugin, { config: parsed });
      setEditingPlugin(null);
      loadPlugins();
    } catch (e) {
      if (e instanceof SyntaxError) {
        setConfigError(`JSON 格式错误: ${e.message}`);
      } else {
        setError(e instanceof Error ? e.message : "保存配置失败");
      }
    } finally {
      setSaving(false);
    }
  }, [editingPlugin, configDraft, loadPlugins]);

  // ── Render ──────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="page">
        <h1>插件管理</h1>
        <p className="loading-text">加载中…</p>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>插件管理</h1>
      </div>

      {error && <p className="form-error">{error}</p>}

      {!isAdmin && (
        <p className="config-readonly-hint">
          当前为只读账户，仅可查看插件信息。
        </p>
      )}

      <div className="data-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>版本</th>
              <th>状态</th>
              <th>Hook 注册</th>
              <th>重载策略</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {plugins.length === 0 ? (
              <tr>
                <td colSpan={6} className="data-table-empty">
                  暂无插件
                </td>
              </tr>
            ) : (
              plugins.map((p) => (
                <PluginRow
                  key={p.name}
                  plugin={p}
                  isAdmin={isAdmin}
                  editing={editingPlugin === p.name}
                  configDraft={editingPlugin === p.name ? configDraft : ""}
                  configError={editingPlugin === p.name ? configError : null}
                  saving={saving}
                  onToggle={() =>
                    setToggleTarget({ name: p.name, enabled: !p.enabled })
                  }
                  onEditConfig={() => openConfigEditor(p)}
                  onCancelEdit={() => setEditingPlugin(null)}
                  onConfigChange={setConfigDraft}
                  onSaveConfig={handleConfigSave}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      <ConfirmModal
        open={!!toggleTarget}
        title={toggleTarget?.enabled ? "启用插件" : "禁用插件"}
        message={`确认${toggleTarget?.enabled ? "启用" : "禁用"}插件「${toggleTarget?.name ?? ""}」？`}
        danger={!toggleTarget?.enabled}
        onConfirm={handleToggle}
        onCancel={() => setToggleTarget(null)}
      />
    </div>
  );
}

// ── Plugin row (with inline config editor) ──────────────────────────────

interface PluginRowProps {
  plugin: PluginItem;
  isAdmin: boolean;
  editing: boolean;
  configDraft: string;
  configError: string | null;
  saving: boolean;
  onToggle: () => void;
  onEditConfig: () => void;
  onCancelEdit: () => void;
  onConfigChange: (v: string) => void;
  onSaveConfig: () => void;
}

function PluginRow({
  plugin,
  isAdmin,
  editing,
  configDraft,
  configError,
  saving,
  onToggle,
  onEditConfig,
  onCancelEdit,
  onConfigChange,
  onSaveConfig,
}: PluginRowProps) {
  return (
    <>
      <tr>
        <td className="session-id">{plugin.name}</td>
        <td>{plugin.version}</td>
        <td>
          <label className="switch">
            <input
              type="checkbox"
              checked={plugin.enabled}
              disabled={!isAdmin}
              onChange={onToggle}
            />
            <span className="switch-slider" />
            <span className="switch-label">
              {plugin.enabled ? "启用" : "禁用"}
            </span>
          </label>
        </td>
        <td>
          {plugin.hooks.length > 0 ? (
            plugin.hooks.map((h) => (
              <span key={h} className="badge badge-info" style={{ marginRight: "0.3rem" }}>
                {h}
              </span>
            ))
          ) : (
            "—"
          )}
        </td>
        <td>
          <span className={`badge ${plugin.config_reload_strategy === "hot" ? "badge-ok" : "badge-warn"}`}>
            {plugin.config_reload_strategy === "hot" ? "热更新" : "需重载"}
          </span>
        </td>
        <td>
          <button className="btn-outline btn-sm" onClick={onEditConfig}>
            配置
          </button>
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={6}>
            <div className="plugin-config-section">
              <textarea
                className="plugin-config-textarea"
                value={configDraft}
                disabled={!isAdmin}
                onChange={(e) => onConfigChange(e.target.value)}
              />
              {configError && (
                <div className="config-parse-error">{configError}</div>
              )}
              {isAdmin && (
                <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem" }}>
                  <button
                    className="btn-primary btn-sm"
                    disabled={saving}
                    onClick={onSaveConfig}
                  >
                    {saving ? "保存中…" : "保存配置"}
                  </button>
                  <button className="btn-cancel" onClick={onCancelEdit}>
                    取消
                  </button>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
