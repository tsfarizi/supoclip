"use client";

import { useEffect, useRef, useState } from "react";

export type TaskPollingMode = "task" | "edit";

export function useTaskPolling(
  taskId: string,
  mode: TaskPollingMode,
  onStatus?: (status: string) => void
): {
  progress: number;
  message: string;
  status: string;
  eventType: string | null;
  clipEvent: unknown | null;
} {
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("");
  const [eventType, setEventType] = useState<string | null>(null);
  const [clipEvent, setClipEvent] = useState<unknown | null>(null);

  // Kept in a ref so an inline onStatus callback does not reconnect the stream.
  const onStatusRef = useRef(onStatus);
  useEffect(() => {
    onStatusRef.current = onStatus;
  }, [onStatus]);

  useEffect(() => {
    if (!taskId) return;

    const eventSource = new EventSource(
      `/api/tasks/${taskId}/progress?mode=${mode}`
    );

    const applyProgress = (type: string, event: MessageEvent) => {
      const data = parseEventData(event);
      setEventType(type);
      setProgress(toNumber(data.progress));
      setMessage(typeof data.message === "string" ? data.message : "");
      const nextStatus = typeof data.status === "string" ? data.status : "";
      setStatus(nextStatus);
      return nextStatus;
    };

    eventSource.addEventListener("status", (event) => {
      const nextStatus = applyProgress("status", event as MessageEvent);
      // Task mode terminates on terminal statuses; edit mode stays open for
      // clip re-renders on completed tasks.
      if (mode === "task" && isTerminalStatus(nextStatus)) {
        eventSource.close();
      }
    });

    eventSource.addEventListener("progress", (event) => {
      const nextStatus = applyProgress("progress", event as MessageEvent);
      if (mode === "task" && isTerminalStatus(nextStatus)) {
        eventSource.close();
      }
    });

    eventSource.addEventListener("clip_render", (event) => {
      setEventType("clip_render");
      setClipEvent(parseEventData(event as MessageEvent));
    });

    eventSource.addEventListener("clip_ready", (event) => {
      setEventType("clip_ready");
      const data = parseEventData(event as MessageEvent);
      setClipEvent(data);
      const nextStatus = typeof data.status === "string" ? data.status : "";
      if (nextStatus) {
        onStatusRef.current?.(nextStatus);
      }
    });

    eventSource.addEventListener("close", (event) => {
      setEventType("close");
      const data = parseEventData(event as MessageEvent);
      if (typeof data.status === "string") {
        setStatus(data.status);
      }
      if (mode === "task") {
        eventSource.close();
      }
    });

    return () => {
      eventSource.close();
    };
  }, [taskId, mode]);

  return { progress, message, status, eventType, clipEvent };
}

function parseEventData(event: MessageEvent): Record<string, unknown> {
  try {
    return JSON.parse(event.data) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function toNumber(value: unknown): number {
  const num = Number(value ?? 0);
  return Number.isFinite(num) ? num : 0;
}

function isTerminalStatus(status: string): boolean {
  return status === "completed" || status === "error";
}
