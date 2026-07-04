/** Config page: read / edit / save runtime configuration. */

import { useState, useEffect, useCallback, useMemo } from "react";
import { useAuth } from "../auth/context";
import {
  configApi,
  type ConfigSchemaItem,
  type ConfigUpdateResultItem,
} from "../api/client";

/** Human-readable group labels. */
const GROUP_LABELS: Record<string, string> = {
  api: "API 接口",
  bot: "机器人",
  observe: "群观察",
  chunk: "分片发送",
  feature: "功能开关",
  user_memory: "用户记忆",
  storage: "存储",
  admin: "管理端",
  security: "安全",
};

/** Group display order. */
const GROUP_ORDER = [
  "api",
  "bot",
  "observe",
  "chunk",
  "feature",
  "user_memory",
  "storage",
  "admin",
  "security",
];

/** Form state: maps key → current input value (always string for input binding). */
type FormValues = Record<string, string>;

/** Track which keys the user has explicitly edited. */
type DirtyKeys = Set<string>;

export default function ConfigPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [schema, setSchema] = useState<ConfigSchemaItem[]>([]);
  const [currentValues, setCurrentValues] = useState<Record<string, unknown>>({});
  const [formValues, setFormValues] = useState<FormValues>({});
  const [dirtyKeys, setDirtyKeys] = useState<DirtyKeys>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ConfigUpdateResultItem[] | null>(null);

  // ── Load schema + current values ────────────────────────────────────

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const [schemaData, valuesData] = await Promise.all([
        configApi.schema(),
        configApi.get(),
      ]);
      setSchema(schemaData);
      setCurrentValues(valuesData);

      // Initialize form values from current values
      const init: FormValues = {};
      for (const spec of schemaData) {
        const val = valuesData[spec.key];
        init[spec.key] = valueToString(val, spec.type);
      }
      setFormValues(init);
      setDirtyKeys(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ── Change tracking ─────────────────────────────────────────────────

  const handleChange = useCallback(
    (key: string, value: string) => {
      setFormValues((prev) => ({ ...prev, [key]: value }));
      setDirtyKeys((prev) => new Set(prev).add(key));
    },
    [],
  );

  const handleReset = useCallback(
    (key: string) => {
      const spec = schema.find((s) => s.key === key);
      if (!spec) return;
      setFormValues((prev) => ({
        ...prev,
        [key]: valueToString(currentValues[key], spec.type),
      }));
      setDirtyKeys((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    },
    [schema, currentValues],
  );

  // ── Collect changes & save ──────────────────────────────────────────

  const handleSave = useCallback(async () => {
    if (!isAdmin) return;

    const changes: Record<string, unknown> = {};
    for (const key of dirtyKeys) {
      const spec = schema.find((s) => s.key === key);
      if (!spec) continue;
      const raw = formValues[key] ?? "";
      // For secret fields, empty means "don't change"
      if (spec.is_secret && raw.trim() === "") continue;
      changes[key] = coerceFormValue(raw, spec.type);
    }

    if (Object.keys(changes).length === 0) return;

    setSaving(true);
    setError(null);
    try {
      const resp = await configApi.update(changes);
      setResults(resp.results);
      // Reload to reflect persisted state
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }, [isAdmin, dirtyKeys, schema, formValues, loadData]);

  // ── Grouped schema ──────────────────────────────────────────────────

  const grouped = useMemo(() => {
    const map = new Map<string, ConfigSchemaItem[]>();
    for (const spec of schema) {
      const list = map.get(spec.group) || [];
      list.push(spec);
      map.set(spec.group, list);
    }
    // Sort by GROUP_ORDER
    const sorted: [string, ConfigSchemaItem[]][] = [];
    for (const g of GROUP_ORDER) {
      if (map.has(g)) sorted.push([g, map.get(g)!]);
    }
    // Include any groups not in GROUP_ORDER
    for (const [g, items] of map) {
      if (!GROUP_ORDER.includes(g)) sorted.push([g, items]);
    }
    return sorted;
  }, [schema]);

  // ── Render ──────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="page">
        <h1>配置管理</h1>
        <p className="loading-text">加载中…</p>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>配置管理</h1>
        {isAdmin && (
          <button
            className="btn-primary btn-sm"
            disabled={saving || dirtyKeys.size === 0}
            onClick={handleSave}
          >
            {saving ? "保存中…" : `保存更改 (${dirtyKeys.size})`}
          </button>
        )}
      </div>

      {error && <p className="form-error">{error}</p>}

      {/* Save results */}
      {results && (
        <div className="config-results">
          {results.map((r) => (
            <div
              key={r.key}
              className={`config-result-item ${r.success ? "result-ok" : "result-err"}`}
            >
              <span className="result-key">{r.key}</span>
              <span className="result-status">
                {r.success ? "✓ 已更新" : `✗ ${r.error || "失败"}`}
              </span>
              {r.needs_restart && (
                <span className="badge badge-warn">需重启生效</span>
              )}
            </div>
          ))}
        </div>
      )}

      {!isAdmin && (
        <p className="config-readonly-hint">
          当前为只读账户，仅可查看配置。
        </p>
      )}

      {grouped.map(([group, items]) => (
        <fieldset key={group} className="config-group">
          <legend>{GROUP_LABELS[group] || group}</legend>
          {items.map((spec) => (
            <ConfigField
              key={spec.key}
              spec={spec}
              value={formValues[spec.key] ?? ""}
              dirty={dirtyKeys.has(spec.key)}
              readOnly={!isAdmin}
              onChange={(v) => handleChange(spec.key, v)}
              onReset={() => handleReset(spec.key)}
            />
          ))}
        </fieldset>
      ))}
    </div>
  );
}

// ── ConfigField ────────────────────────────────────────────────────────

interface ConfigFieldProps {
  spec: ConfigSchemaItem;
  value: string;
  dirty: boolean;
  readOnly: boolean;
  onChange: (value: string) => void;
  onReset: () => void;
}

function ConfigField({
  spec,
  value,
  dirty,
  readOnly,
  onChange,
  onReset,
}: ConfigFieldProps) {
  const id = `cfg-${spec.key}`;
  const displayValue = spec.is_secret ? maskDisplay(value) : value;

  return (
    <div className={`config-field ${dirty ? "field-dirty" : ""}`}>
      <div className="field-header">
        <label htmlFor={id} className="field-label">
          {spec.key}
        </label>
        <div className="field-badges">
          {spec.is_secret && <span className="badge badge-secret">敏感</span>}
          {!spec.hot_reloadable && (
            <span className="badge badge-warn">需重启</span>
          )}
          {dirty && (
            <button
              type="button"
              className="btn-reset-field"
              onClick={onReset}
              title="还原"
            >
              ↩
            </button>
          )}
        </div>
      </div>
      {spec.description && (
        <p className="field-desc">{spec.description}</p>
      )}
      {renderControl(spec, id, displayValue, value, readOnly, onChange)}
    </div>
  );
}

/** Render the appropriate input control based on spec type. */
function renderControl(
  spec: ConfigSchemaItem,
  id: string,
  displayValue: string,
  rawValue: string,
  readOnly: boolean,
  onChange: (v: string) => void,
) {
  if (spec.type === "bool") {
    return (
      <label className="switch" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={rawValue === "true"}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.checked ? "true" : "false")}
        />
        <span className="switch-slider" />
        <span className="switch-label">
          {rawValue === "true" ? "启用" : "禁用"}
        </span>
      </label>
    );
  }

  if (spec.type === "int_list") {
    return (
      <input
        id={id}
        type="text"
        className="config-input"
        value={displayValue}
        disabled={readOnly}
        placeholder={readOnly ? "—" : "逗号分隔，如 123,456,789"}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (spec.type === "int" || spec.type === "float") {
    return (
      <input
        id={id}
        type="number"
        className="config-input"
        value={displayValue}
        disabled={readOnly}
        step={spec.type === "float" ? "any" : "1"}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  // str
  return (
    <input
      id={id}
      type={spec.is_secret ? "password" : "text"}
      className="config-input"
      value={displayValue}
      disabled={readOnly}
      placeholder={spec.is_secret ? "留空表示不修改" : ""}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// ── Helpers ────────────────────────────────────────────────────────────

/** Convert a typed value to string for form binding. */
function valueToString(val: unknown, type: ConfigSchemaItem["type"]): string {
  if (val === undefined || val === null) return "";
  if (type === "bool") return val ? "true" : "false";
  if (type === "int_list") {
    if (Array.isArray(val)) return val.join(", ");
    return String(val);
  }
  return String(val);
}

/** Coerce a form string value back to the typed value for the API. */
function coerceFormValue(raw: string, type: ConfigSchemaItem["type"]): unknown {
  switch (type) {
    case "str":
      return raw;
    case "int":
      return parseInt(raw, 10);
    case "float":
      return parseFloat(raw);
    case "bool":
      return raw.toLowerCase() === "true";
    case "int_list":
      return raw
        .split(/[,\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
        .map(Number);
    default:
      return raw;
  }
}

/** Mask a value for display (secrets shown as masked placeholder in the form). */
function maskDisplay(val: string): string {
  // If the value looks like it's already a masked placeholder from the API
  // (contains "****"), treat it as the display value but allow the user to
  // clear it and type a new value.
  if (val.includes("****")) return "";
  return val;
}
