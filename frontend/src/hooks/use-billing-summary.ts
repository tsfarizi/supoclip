"use client";

import { useCallback, useEffect, useState } from "react";

import { buildSupportError } from "@/lib/api-client";
import { BillingSummary } from "@/lib/task-types";

export function useBillingSummary(): {
  summary: BillingSummary | null;
  error: string | null;
  refresh: () => Promise<void>;
} {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch("/api/tasks/billing-summary", {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, `Failed to load billing summary (${response.status})`));
      }
      setSummary((await response.json()) as BillingSummary);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load billing summary"
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { summary, error, refresh };
}
