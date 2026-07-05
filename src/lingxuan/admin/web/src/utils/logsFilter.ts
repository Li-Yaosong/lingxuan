/** Pure logic functions for the Logs page, extracted for testability. */

/** Log level rank for client-side ≥ filtering (mirrors backend). */
export const LEVEL_RANK: Record<string, number> = {
  DEBUG: 10,
  INFO: 20,
  WARNING: 30,
  ERROR: 40,
};

/** A structured log record. */
export interface LogRecord {
  ts: string;
  level: string;
  logger: string;
  msg: string;
  extra: Record<string, unknown>;
}

/** Client-side filter: level (≥) + keyword (case-insensitive on msg and logger). */
export function clientFilter(
  records: LogRecord[],
  level: string,
  keyword: string,
): LogRecord[] {
  return records.filter((r) => {
    if (level) {
      const recRank = LEVEL_RANK[r.level] ?? 0;
      const filterRank = LEVEL_RANK[level] ?? 0;
      if (recRank < filterRank) return false;
    }
    if (keyword) {
      const kw = keyword.toLowerCase();
      if (
        !r.msg.toLowerCase().includes(kw) &&
        !r.logger.toLowerCase().includes(kw)
      )
        return false;
    }
    return true;
  });
}

/** Parse a WS message string into a typed object. */
export function parseWsMessage(data: string): { type: string; record?: LogRecord } | null {
  try {
    const msg = JSON.parse(data);
    if (msg.type === "log") {
      return {
        type: "log",
        record: {
          ts: msg.ts,
          level: msg.level,
          logger: msg.logger,
          msg: msg.msg,
          extra: msg.extra ?? {},
        },
      };
    }
    return { type: msg.type };
  } catch {
    return null;
  }
}
