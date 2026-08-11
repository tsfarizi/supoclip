import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import { useBillingSummary } from "./use-billing-summary";

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

describe("useBillingSummary", () => {
  it("fetches the billing summary on mount with no-store", async () => {
    const summary = {
      monetization_enabled: true,
      plan: "pro",
      subscription_status: "active",
      subscription_provider: "stripe",
      usage_count: 3,
      usage_limit: 100,
      remaining: 97,
      can_create_task: true,
      upgrade_required: false,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(summary));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useBillingSummary());

    await waitFor(() => expect(result.current.summary).toEqual(summary));

    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/billing-summary", {
      cache: "no-store",
    });
    expect(result.current.error).toBeNull();
  });

  it("sets an error and keeps summary null when the server responds with an error", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ message: "boom" }, 500));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useBillingSummary());

    await waitFor(() =>
      expect(result.current.error).toBe("Failed to load billing summary (500)")
    );
    expect(result.current.summary).toBeNull();
  });

  it("sets the thrown error message when the network request rejects", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useBillingSummary());

    await waitFor(() => expect(result.current.error).toBe("network down"));
    expect(result.current.summary).toBeNull();
  });

  it("refresh() reloads the summary", async () => {
    const first = {
      monetization_enabled: true,
      plan: "free",
      subscription_status: "inactive",
      subscription_provider: null,
      usage_count: 1,
      usage_limit: 5,
      remaining: 4,
      can_create_task: true,
      upgrade_required: false,
    };
    const second = {
      monetization_enabled: true,
      plan: "pro",
      subscription_status: "active",
      subscription_provider: "stripe",
      usage_count: 4,
      usage_limit: 100,
      remaining: 96,
      can_create_task: true,
      upgrade_required: false,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(first))
      .mockResolvedValueOnce(jsonResponse(second));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useBillingSummary());

    await waitFor(() => expect(result.current.summary).toEqual(first));

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.summary).toEqual(second);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
