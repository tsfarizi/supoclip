import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";

import { useFonts } from "./use-fonts";

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

describe("useFonts", () => {
  it("fetches fonts on mount with no-store", async () => {
    const fonts = [
      { name: "TikTokSans-Regular", display_name: "TikTok Sans", scope: "system" },
      { name: "Inter-Bold", display_name: "Inter Bold", format: "ttf", scope: "user" },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ fonts }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useFonts());

    await waitFor(() => expect(result.current.fonts).toEqual(fonts));

    expect(fetchMock).toHaveBeenCalledWith("/api/fonts", { cache: "no-store" });
    expect(result.current.error).toBeNull();
  });

  it("defaults to an empty array when the fonts key is missing", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useFonts());

    await waitFor(() => expect(result.current.fonts).toEqual([]));
    expect(result.current.error).toBeNull();
  });

  it("sets an error and keeps fonts empty when the server responds with an error", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: "Unauthorized" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useFonts());

    await waitFor(() =>
      expect(result.current.error).toBe("Failed to load fonts (401)")
    );
    expect(result.current.fonts).toEqual([]);
  });

  it("sets the thrown error message when the network request rejects", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useFonts());

    await waitFor(() => expect(result.current.error).toBe("network down"));
    expect(result.current.fonts).toEqual([]);
  });
});
