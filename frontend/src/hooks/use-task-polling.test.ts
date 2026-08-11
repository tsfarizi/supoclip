import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useTaskPolling } from "./use-task-polling";

class StubEventSource {
  static instances: StubEventSource[] = [];

  readonly url: string;
  closed = false;
  private listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    this.url = url;
    StubEventSource.instances.push(this);
  }

  addEventListener(
    type: string,
    listener: (event: MessageEvent<string>) => void
  ): void {
    const bucket = this.listeners.get(type) ?? new Set();
    bucket.add(listener);
    this.listeners.set(type, bucket);
  }

  removeEventListener(
    type: string,
    listener: (event: MessageEvent<string>) => void
  ): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

function lastStream(): StubEventSource {
  const streams = StubEventSource.instances;
  if (streams.length === 0) {
    throw new Error("No EventSource was created");
  }
  return streams[streams.length - 1];
}

beforeEach(() => {
  vi.stubGlobal("EventSource", StubEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  StubEventSource.instances = [];
});

describe("useTaskPolling", () => {
  it("opens the progress stream with the mode and applies status events", () => {
    const { result } = renderHook(() => useTaskPolling("task-1", "task"));

    const stream = lastStream();
    expect(stream.url).toBe("/api/tasks/task-1/progress?mode=task");

    act(() => {
      stream.emit("status", {
        status: "processing",
        progress: 42,
        message: "Working",
      });
    });

    expect(result.current.progress).toBe(42);
    expect(result.current.message).toBe("Working");
    expect(result.current.status).toBe("processing");
    expect(result.current.eventType).toBe("status");
  });

  it("closes the stream when a status event reports completed in task mode", () => {
    const { result } = renderHook(() => useTaskPolling("task-1", "task"));

    const stream = lastStream();
    act(() => {
      stream.emit("status", {
        status: "completed",
        progress: 100,
        message: "Done",
      });
    });

    expect(stream.closed).toBe(true);
    expect(result.current.status).toBe("completed");
  });

  it("closes the stream on an explicit close event in task mode", () => {
    const { result } = renderHook(() => useTaskPolling("task-1", "task"));

    const stream = lastStream();
    act(() => {
      stream.emit("close", { status: "completed" });
    });

    expect(stream.closed).toBe(true);
    expect(result.current.eventType).toBe("close");
  });

  it("keeps the stream open on completed status in edit mode", () => {
    const { result } = renderHook(() => useTaskPolling("task-1", "edit"));

    const stream = lastStream();
    expect(stream.url).toBe("/api/tasks/task-1/progress?mode=edit");

    act(() => {
      stream.emit("status", {
        status: "completed",
        progress: 100,
        message: "Done",
      });
    });

    expect(stream.closed).toBe(false);
    expect(result.current.status).toBe("completed");
  });

  it("delivers clip_render and clip_ready events in edit mode", () => {
    const onStatus = vi.fn();
    const { result } = renderHook(() =>
      useTaskPolling("task-1", "edit", onStatus)
    );

    const stream = lastStream();

    act(() => {
      stream.emit("clip_render", {
        clip_id: "c1",
        progress: 60,
        status: "processing",
      });
    });
    expect(result.current.eventType).toBe("clip_render");
    expect(result.current.clipEvent).toEqual({
      clip_id: "c1",
      progress: 60,
      status: "processing",
    });

    act(() => {
      stream.emit("clip_ready", {
        clip_index: 1,
        total_clips: 3,
        clip: { id: "c1" },
        status: "processing",
      });
    });
    expect(result.current.eventType).toBe("clip_ready");
    expect(result.current.clipEvent).toEqual({
      clip_index: 1,
      total_clips: 3,
      clip: { id: "c1" },
      status: "processing",
    });
    expect(onStatus).toHaveBeenCalledWith("processing");
  });

  it("does not call onStatus when clip_ready carries no status", () => {
    const onStatus = vi.fn();
    renderHook(() => useTaskPolling("task-1", "edit", onStatus));

    const stream = lastStream();
    act(() => {
      stream.emit("clip_ready", { clip_index: 1, clip: { id: "c1" } });
    });

    expect(onStatus).not.toHaveBeenCalled();
  });

  it("closes the stream on unmount", () => {
    const { unmount } = renderHook(() => useTaskPolling("task-1", "edit"));

    const stream = lastStream();
    expect(stream.closed).toBe(false);

    unmount();

    expect(stream.closed).toBe(true);
  });

  it("closes the previous stream and opens a new one when the task id changes", () => {
    const { rerender } = renderHook(({ id }) => useTaskPolling(id, "task"), {
      initialProps: { id: "task-1" },
    });

    const first = lastStream();
    rerender({ id: "task-2" });

    const second = lastStream();
    expect(first.closed).toBe(true);
    expect(second.closed).toBe(false);
    expect(second.url).toBe("/api/tasks/task-2/progress?mode=task");
  });
});
