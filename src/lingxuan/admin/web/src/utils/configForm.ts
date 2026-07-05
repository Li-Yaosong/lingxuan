/** Pure logic functions for the Config page, extracted for testability. */

/** Setting type alias matching the API schema. */
export type SettingType = "str" | "int" | "float" | "bool" | "int_list";

/** Convert a typed value to string for form binding. */
export function valueToString(val: unknown, type: SettingType): string {
  if (val === undefined || val === null) return "";
  if (type === "bool") return val ? "true" : "false";
  if (type === "int_list") {
    if (Array.isArray(val)) return val.join(", ");
    return String(val);
  }
  return String(val);
}

/** Coerce a form string value back to the typed value for the API. */
export function coerceFormValue(raw: string, type: SettingType): unknown {
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

/** Collect changed items: compare form values against originals, skipping
 *  secret fields with empty input. Returns the changes dict for PUT /config. */
export function collectChanges(
  formValues: Record<string, string>,
  _originalValues: Record<string, unknown>,
  dirtyKeys: Set<string>,
  schema: { key: string; type: SettingType; is_secret: boolean }[],
): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  for (const key of dirtyKeys) {
    const spec = schema.find((s) => s.key === key);
    if (!spec) continue;
    const raw = formValues[key];
    if (spec.is_secret && raw.trim() === "") continue;
    changes[key] = coerceFormValue(raw, spec.type);
  }
  return changes;
}

/** Mask a value for display (secrets shown as masked placeholder in the form). */
export function maskDisplay(val: string): string {
  // If the value looks like it's already a masked placeholder from the API
  // (contains "****"), treat it as the display value but allow the user to
  // clear it and type a new value.
  if (val.includes("****")) return "";
  return val;
}
