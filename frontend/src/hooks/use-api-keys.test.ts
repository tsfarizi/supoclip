import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import { ApiKey, useApiKeys } from "./use-api-keys";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useApiKeys", () => {
  it("loads api keys on mount with no-store", async () => {
    const key1: ApiKey = {
      id: "key-1",
      name: "Laptop MCP",
      created_at: "2026-01-01T00:00:00Z",
      last_used_at: null,
      revoked_at: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ api_keys: [key1], total: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApiKeys());

    await waitFor(() => expect(result.current.keys).toEqual([key1]));

    expect(fetchMock).toHaveBeenCalledWith("/api/api-keys", {
      cache: "no-store",
    });
    expect(result.current.error).toBeNull();
  });

  it("createKey posts the name, returns the new key, and refreshes the list", async () => {
    const key1: ApiKey = {
      id: "key-1",
      name: "Laptop MCP",
      created_at: "2026-01-01T00:00:00Z",
    };
    const key2: ApiKey = {
      id: "key-2",
      name: "CI",
      created_at: "2026-02-01T00:00:00Z",
      last_used_at: null,
      revoked_at: null,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ api_keys: [key1], total: 1 }))
      .mockResolvedValueOnce(
        jsonResponse({
          api_key: { ...key2, key: "sk_test" },
          message: "API key created",
        })
      )
      .mockResolvedValueOnce(
        jsonResponse({ api_keys: [key2, key1], total: 2 })
      );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApiKeys());
    await waitFor(() => expect(result.current.keys).toHaveLength(1));

    let created: { key?: string; error?: string } = {};
    await act(async () => {
      created = await result.current.createKey("CI");
    });

    expect(created.key).toBe("sk_test");
    expect(created.error).toBeUndefined();
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/api-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "CI" }),
    });
    await waitFor(() => expect(result.current.keys).toEqual([key2, key1]));
  });

  it("createKey returns the server error when the request fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ api_keys: [], total: 0 }))
      .mockResolvedValueOnce(
        jsonResponse({ detail: "Key limit reached" }, 409)
      );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApiKeys());
    await waitFor(() => expect(result.current.keys).toEqual([]));

    let created: { key?: string; error?: string } = {};
    await act(async () => {
      created = await result.current.createKey("CI");
    });

    expect(created.error).toBe("Key limit reached");
    expect(created.key).toBeUndefined();
    expect(result.current.error).toBe("Key limit reached");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("revokeKey deletes the key and returns true", async () => {
    const key1: ApiKey = {
      id: "key-1",
      name: "Laptop MCP",
      created_at: "2026-01-01T00:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ api_keys: [key1], total: 1 }))
      .mockResolvedValueOnce(jsonResponse({ message: "API key revoked" }))
      .mockResolvedValueOnce(jsonResponse({ api_keys: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApiKeys());
    await waitFor(() => expect(result.current.keys).toHaveLength(1));

    let revoked = false;
    await act(async () => {
      revoked = await result.current.revokeKey("key-1");
    });

    expect(revoked).toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/api-keys/key-1", {
      method: "DELETE",
    });
    await waitFor(() => expect(result.current.keys).toEqual([]));
    expect(result.current.error).toBeNull();
  });

  it("revokeKey returns false when the delete fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ api_keys: [], total: 0 }))
      .mockResolvedValueOnce(
        jsonResponse({ detail: "API key not found" }, 404)
      );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApiKeys());
    await waitFor(() => expect(result.current.keys).toEqual([]));

    let revoked: boolean | undefined;
    await act(async () => {
      revoked = await result.current.revokeKey("missing");
    });

    expect(revoked).toBe(false);
    expect(result.current.error).toBe("API key not found");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
