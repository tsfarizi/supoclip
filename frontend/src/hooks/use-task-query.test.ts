import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import { useTaskQuery } from "./use-task-query";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clip(id: string, clipOrder: number) {
  return {
    id,
    filename: `${id}.mp4`,
    video_url: `/tasks/task-1/clips/${id}/file`,
    start_time: "0",
    end_time: "1",
    duration: 1,
    text: "text",
    relevance_score: 1,
    clip_order: clipOrder,
    created_at: "2026-01-01T00:00:00Z",
  };
}

const task = {
  id: "task-1",
  source_title: "Video",
  source_type: "youtube",
  status: "processing",
  clips_count: 2,
  created_at: "2026-01-01T00:00:00Z",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("useTaskQuery", () => {
  it("fetches the task and clips on mount with no-store", async () => {
    const clips = [clip("c1", 0), clip("c2", 1)];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ task, clips }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTaskQuery("task-1"));

    await waitFor(() => expect(result.current.task).toEqual(task));
    expect(result.current.clips).toEqual(clips);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/tasks/task-1", {
      cache: "no-store",
    });
  });

  it("keeps the error null on a 404 when retryOn404 is enabled", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ detail: "Task not found" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTaskQuery("task-1", { retryOn404: true })
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.task).toBeNull();
    expect(result.current.clips).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("sets a formatted error on a 404 when retryOn404 is disabled", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ detail: "Task not found" }, 404));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTaskQuery("task-1"));

    await waitFor(() => expect(result.current.error).toBe("Task not found"));
    expect(result.current.task).toBeNull();
    expect(result.current.clips).toEqual([]);
  });

  it("keeps longer existing clips when mergeIncremental is enabled", async () => {
    const fullClips = [clip("c1", 0), clip("c2", 1), clip("c3", 2)];
    const partialClips = [clip("c1", 0), clip("c2", 1)];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ task, clips: fullClips }))
      .mockResolvedValueOnce(jsonResponse({ task, clips: partialClips }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useTaskQuery("task-1", { mergeIncremental: true })
    );

    await waitFor(() => expect(result.current.clips).toHaveLength(3));

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.clips).toEqual(fullClips);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("replaces clips with a shorter response when mergeIncremental is disabled", async () => {
    const fullClips = [clip("c1", 0), clip("c2", 1), clip("c3", 2)];
    const partialClips = [clip("c1", 0), clip("c2", 1)];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ task, clips: fullClips }))
      .mockResolvedValueOnce(jsonResponse({ task, clips: partialClips }));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useTaskQuery("task-1"));

    await waitFor(() => expect(result.current.clips).toHaveLength(3));

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.clips).toEqual(partialClips);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
