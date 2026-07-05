import { describe, it, expect } from "vitest";
import { clientFilter, parseWsMessage, LEVEL_RANK, type LogRecord } from "../src/utils/logsFilter";

// ── Test data ──────────────────────────────────────────────────────────────

const SAMPLE_LOGS: LogRecord[] = [
  { ts: "2026-07-05T10:00:00", level: "DEBUG", logger: "lingxuan.core.dialogue", msg: "Processing message", extra: {} },
  { ts: "2026-07-05T10:00:01", level: "INFO", logger: "lingxuan.admin.auth", msg: "User logged in", extra: {} },
  { ts: "2026-07-05T10:00:02", level: "WARNING", logger: "lingxuan.adapters.openai", msg: "Rate limit approaching", extra: {} },
  { ts: "2026-07-05T10:00:03", level: "ERROR", logger: "lingxuan.adapters.storage", msg: "Database connection lost", extra: {} },
  { ts: "2026-07-05T10:00:04", level: "INFO", logger: "lingxuan.core.observation", msg: "Observation triggered for group 123", extra: {} },
];

// ── Tests ──────────────────────────────────────────────────────────────────

describe("clientFilter", () => {
  it("returns all records when no filter is set", () => {
    expect(clientFilter(SAMPLE_LOGS, "", "")).toEqual(SAMPLE_LOGS);
  });

  it("filters by level with ≥ semantics", () => {
    const result = clientFilter(SAMPLE_LOGS, "WARNING", "");
    expect(result).toHaveLength(2);
    expect(result[0]!.level).toBe("WARNING");
    expect(result[1]!.level).toBe("ERROR");
  });

  it("filters by INFO level (includes WARNING and ERROR)", () => {
    const result = clientFilter(SAMPLE_LOGS, "INFO", "");
    expect(result).toHaveLength(4);
    expect(result.every((r) => LEVEL_RANK[r.level]! >= LEVEL_RANK["INFO"]!)).toBe(true);
  });

  it("filters by keyword in msg", () => {
    const result = clientFilter(SAMPLE_LOGS, "", "logged");
    expect(result).toHaveLength(1);
    expect(result[0]!.msg).toContain("logged");
  });

  it("filters by keyword in logger", () => {
    const result = clientFilter(SAMPLE_LOGS, "", "openai");
    expect(result).toHaveLength(1);
    expect(result[0]!.logger).toContain("openai");
  });

  it("keyword search is case-insensitive", () => {
    const result = clientFilter(SAMPLE_LOGS, "", "DATABASE");
    expect(result).toHaveLength(1);
    expect(result[0]!.level).toBe("ERROR");
  });

  it("combines level and keyword filters", () => {
    const result = clientFilter(SAMPLE_LOGS, "WARNING", "rate");
    // Only the WARNING about rate limit matches both
    expect(result).toHaveLength(1);
    expect(result[0]!.level).toBe("WARNING");
  });

  it("returns empty when no records match", () => {
    const result = clientFilter(SAMPLE_LOGS, "ERROR", "logged");
    expect(result).toHaveLength(0);
  });

  it("handles empty input array", () => {
    expect(clientFilter([], "INFO", "test")).toEqual([]);
  });

  it("handles unknown level gracefully (rank 0)", () => {
    const custom: LogRecord[] = [
      { ts: "2026-07-05T10:00:00", level: "CUSTOM", logger: "test", msg: "hello", extra: {} },
    ];
    // "CUSTOM" has rank 0, so filtering by "DEBUG" (rank 10) excludes it
    expect(clientFilter(custom, "DEBUG", "")).toHaveLength(0);
    // No level filter includes it
    expect(clientFilter(custom, "", "")).toHaveLength(1);
  });
});

describe("parseWsMessage", () => {
  it("parses a valid log message", () => {
    const data = JSON.stringify({
      type: "log",
      ts: "2026-07-05T10:00:00",
      level: "INFO",
      logger: "lingxuan.test",
      msg: "Hello world",
      extra: { key: "value" },
    });
    const result = parseWsMessage(data);
    expect(result).not.toBeNull();
    expect(result!.type).toBe("log");
    expect(result!.record).toEqual({
      ts: "2026-07-05T10:00:00",
      level: "INFO",
      logger: "lingxuan.test",
      msg: "Hello world",
      extra: { key: "value" },
    });
  });

  it("handles log message without extra field", () => {
    const data = JSON.stringify({
      type: "log",
      ts: "2026-07-05T10:00:00",
      level: "DEBUG",
      logger: "test",
      msg: "ping",
    });
    const result = parseWsMessage(data);
    expect(result!.record!.extra).toEqual({});
  });

  it("returns type for non-log messages", () => {
    const data = JSON.stringify({ type: "filter", level: "WARNING" });
    const result = parseWsMessage(data);
    expect(result).toEqual({ type: "filter" });
  });

  it("returns null for invalid JSON", () => {
    expect(parseWsMessage("not json")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(parseWsMessage("")).toBeNull();
  });
});
