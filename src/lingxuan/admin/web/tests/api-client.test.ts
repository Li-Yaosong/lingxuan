import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ── Test the 401 refresh retry logic in the API client ──────────
// We mock global fetch to simulate 401 → refresh → retry behavior.

describe("API client 401 refresh logic", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.resetModules();
    // Clear token state between tests
    localStorage.clear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("retries with new access token after 401 + successful refresh", async () => {
    // Import fresh module so token state is clean
    const { setAccessToken, setRefreshToken } = await import(
      "../src/auth/tokens"
    );
    const { api } = await import("../src/api/client");

    // Seed initial tokens
    setAccessToken("old-access");
    setRefreshToken("initial-refresh");

    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      callCount++;

      // Refresh endpoint
      if (url.endsWith("/auth/refresh")) {
        return new Response(
          JSON.stringify({
            access_token: "new-access",
            refresh_token: "new-refresh",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      // Protected endpoint: first call 401, second call success
      const authHeader = (init?.headers as Headers)?.get("Authorization");
      if (authHeader === "Bearer old-access") {
        return new Response(
          JSON.stringify({ detail: "Token expired" }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        );
      }

      return new Response(
        JSON.stringify({ data: "ok" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;

    const result = await api.get<{ data: string }>("/admin/api/test");

    expect(callCount).toBe(3); // original + refresh + retry
    expect(result.data).toBe("ok");
  });

  it("clears tokens and throws when refresh also fails", async () => {
    const { setAccessToken, setRefreshToken, getAccessToken } = await import(
      "../src/auth/tokens"
    );
    const { api, ApiClientError } = await import("../src/api/client");

    setAccessToken("expired-access");
    setRefreshToken("bad-refresh");

    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith("/auth/refresh")) {
        return new Response(
          JSON.stringify({ detail: "Invalid refresh token" }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(
        JSON.stringify({ detail: "Token expired" }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;

    await expect(api.get("/admin/api/test")).rejects.toThrow(ApiClientError);
    expect(getAccessToken()).toBeNull();
  });
});
