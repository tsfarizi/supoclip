"use client";

import { useCallback, useEffect, useState } from "react";

import { FontOption } from "@/lib/task-types";

export function useFonts(): {
  fonts: FontOption[];
  error: string | null;
} {
  const [fonts, setFonts] = useState<FontOption[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch("/api/fonts", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Failed to load fonts (${response.status})`);
      }
      const data = (await response.json()) as { fonts?: FontOption[] };
      setFonts(data.fonts ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load fonts");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { fonts, error };
}
