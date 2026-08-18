"use client";

import { useCallback, useEffect, useState } from "react";

import { formatSupportMessage, parseApiError } from "@/lib/api-error";
import { Clip, TaskDetails } from "@/lib/task-types";

export interface UseTaskQueryOptions {
  retryOn404?: boolean;
}

export function useTaskQuery(
  taskId: string | undefined,
  options?: UseTaskQueryOptions
): {
  task: TaskDetails | null;
  clips: Clip[];
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
} {
  const retryOn404 = options?.retryOn404 ?? false;

  const [task, setTask] = useState<TaskDetails | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!taskId) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/tasks/${taskId}`, { cache: "no-store" });

      // Detail pages poll before the task row is persisted; a 404 is expected
      // then, so it must not surface as a permanent error when opted in.
      if (response.status === 404 && retryOn404) {
        return;
      }

      if (!response.ok) {
        const info = await parseApiError(
          response,
          `Failed to fetch task: ${response.status}`
        );
        throw new Error(formatSupportMessage(info));
      }

      // The backend returns the task object with clips nested; the wrapper
      // shape is accepted as well to keep the hook resilient to both.
      const body = (await response.json()) as {
        task?: TaskDetails;
        clips?: Clip[];
      } & Partial<TaskDetails>;

      const nextTask = (body.task ?? body) as TaskDetails;
      const nextClips = body.clips ?? [];

      setTask(nextTask);
      setClips(nextClips);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load task");
    } finally {
      setIsLoading(false);
    }
  }, [taskId, retryOn404]);

  useEffect(() => {
    if (!taskId) return;
    void refresh();
  }, [taskId, refresh]);

  return { task, clips, isLoading, error, refresh };
}
