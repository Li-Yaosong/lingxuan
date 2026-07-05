import { describe, it, expect } from "vitest";
import { valueToString, coerceFormValue, collectChanges } from "../src/utils/configForm";

// ── Tests ──────────────────────────────────────────────────────────────

describe("valueToString", () => {
  it("converts bool true/false", () => {
    expect(valueToString(true, "bool")).toBe("true");
    expect(valueToString(false, "bool")).toBe("false");
  });

  it("converts int_list arrays", () => {
    expect(valueToString([1, 2, 3], "int_list")).toBe("1, 2, 3");
    expect(valueToString([], "int_list")).toBe("");
  });

  it("converts string and numeric values", () => {
    expect(valueToString("hello", "str")).toBe("hello");
    expect(valueToString(42, "int")).toBe("42");
    expect(valueToString(3.14, "float")).toBe("3.14");
  });

  it("handles null/undefined", () => {
    expect(valueToString(null, "str")).toBe("");
    expect(valueToString(undefined, "int")).toBe("");
  });
});

describe("coerceFormValue", () => {
  it("parses int from string", () => {
    expect(coerceFormValue("42", "int")).toBe(42);
    expect(coerceFormValue("0", "int")).toBe(0);
  });

  it("parses float from string", () => {
    expect(coerceFormValue("3.14", "float")).toBeCloseTo(3.14);
    expect(coerceFormValue("0.5", "float")).toBeCloseTo(0.5);
  });

  it("parses bool from string", () => {
    expect(coerceFormValue("true", "bool")).toBe(true);
    expect(coerceFormValue("false", "bool")).toBe(false);
    expect(coerceFormValue("TRUE", "bool")).toBe(true);
  });

  it("parses int_list from comma-separated string", () => {
    expect(coerceFormValue("123, 456, 789", "int_list")).toEqual([123, 456, 789]);
    expect(coerceFormValue("1", "int_list")).toEqual([1]);
    expect(coerceFormValue("", "int_list")).toEqual([]);
  });

  it("returns string as-is for str type", () => {
    expect(coerceFormValue("hello world", "str")).toBe("hello world");
  });
});

describe("collectChanges", () => {
  const schema = [
    { key: "BOT_NAME", type: "str" as const, is_secret: false },
    { key: "MEMORY_WINDOW", type: "int" as const, is_secret: false },
    { key: "ENABLE_PRIVATE_CHAT", type: "bool" as const, is_secret: false },
    { key: "OPENAI_API_KEY", type: "str" as const, is_secret: true },
    { key: "BOT_ADMINS", type: "int_list" as const, is_secret: false },
  ];

  it("collects only dirty keys with correct coercion", () => {
    const formValues: Record<string, string> = {
      BOT_NAME: "新名字",
      MEMORY_WINDOW: "30",
      ENABLE_PRIVATE_CHAT: "false",
      OPENAI_API_KEY: "sk-new-key",
      BOT_ADMINS: "111, 222",
    };
    const originalValues: Record<string, unknown> = {
      BOT_NAME: "灵轩",
      MEMORY_WINDOW: 20,
      ENABLE_PRIVATE_CHAT: true,
      OPENAI_API_KEY: "sk-****-old",
      BOT_ADMINS: [111],
    };
    const dirtyKeys = new Set(["BOT_NAME", "MEMORY_WINDOW", "ENABLE_PRIVATE_CHAT", "OPENAI_API_KEY", "BOT_ADMINS"]);

    const changes = collectChanges(formValues, originalValues, dirtyKeys, schema);

    expect(changes).toEqual({
      BOT_NAME: "新名字",
      MEMORY_WINDOW: 30,
      ENABLE_PRIVATE_CHAT: false,
      OPENAI_API_KEY: "sk-new-key",
      BOT_ADMINS: [111, 222],
    });
  });

  it("skips secret fields with empty input", () => {
    const formValues: Record<string, string> = {
      BOT_NAME: "灵轩",
      OPENAI_API_KEY: "",
    };
    const dirtyKeys = new Set(["BOT_NAME", "OPENAI_API_KEY"]);

    const changes = collectChanges(formValues, {}, dirtyKeys, schema);

    expect(changes).toEqual({ BOT_NAME: "灵轩" });
    expect(changes).not.toHaveProperty("OPENAI_API_KEY");
  });

  it("returns empty dict when no dirty keys", () => {
    const changes = collectChanges({}, {}, new Set(), schema);
    expect(changes).toEqual({});
  });

  it("ignores dirty keys not in schema", () => {
    const formValues: Record<string, string> = { UNKNOWN_KEY: "value" };
    const dirtyKeys = new Set(["UNKNOWN_KEY"]);

    const changes = collectChanges(formValues, {}, dirtyKeys, schema);
    expect(changes).toEqual({});
  });
});
