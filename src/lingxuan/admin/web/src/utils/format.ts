/** Shared formatting utilities for admin SPA pages. */

/**
 * Format an ISO timestamp for display (zh-CN locale).
 * @param includeSeconds Whether to include seconds in the output.
 */
export function formatTime(iso: string, includeSeconds = false): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const opts: Intl.DateTimeFormatOptions = {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      ...(includeSeconds ? { second: "2-digit" } : {}),
    };
    return d.toLocaleString("zh-CN", opts);
  } catch {
    return iso;
  }
}

/** Map relationship stage to Chinese label. */
export function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    stranger: "陌生",
    acquaintance: "相识",
    familiar: "熟悉",
    close: "亲密",
  };
  return labels[stage] ?? stage;
}
