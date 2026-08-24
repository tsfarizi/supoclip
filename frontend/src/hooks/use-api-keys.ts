"use client";

import { useCallback, useEffect, useState } from "react";

import { buildSupportError } from "@/lib/api-client";

export interface ApiKey {
  id: string;
  name: string;
  created_at: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

export interface CreateKeyResult {
  key?: string;
  error?: string;
}

export function useApiKeys(): {
  keys: ApiKey[];
  createKey: (name: string) => Promise<CreateKeyResult>;
  revokeKey: (id: string) => Promise<boolean>;
  error: string | null;
} {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadKeys = useCallback(async () => {
    setError(null);
    try {
      const response = await fetch("/api/api-keys", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, `Failed to load API keys (${response.status})`));
      }
      const data = (await response.json()) as { api_keys?: ApiKey[] };
      setKeys(data.api_keys ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    }
  }, []);

  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  const createKey = useCallback(
    async (name: string): Promise<CreateKeyResult> => {
      setError(null);
      try {
        const response = await fetch("/api/api-keys", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        const data = (await response.json().catch(() => ({}))) as {
          api_key?: { key?: string };
          key?: string;
          detail?: string;
          message?: string;
        };
        if (!response.ok) {
          const message = await buildSupportError(response, data.detail || data.message || "Failed to create API key");
          setError(message);
          return { error: message };
        }
        // Backend menaruh plaintext key di api_key.key; bentuk { api_key, key }
        // dari spesifikasi juga dipin untuk menutup kedua varian.
        const key = data.key ?? data.api_key?.key;
        await loadKeys();
        return key ? { key } : {};
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to create API key";
        setError(message);
        return { error: message };
      }
    },
    [loadKeys]
  );

  const revokeKey = useCallback(
    async (id: string): Promise<boolean> => {
      setError(null);
      try {
        const response = await fetch(`/api/api-keys/${id}`, {
          method: "DELETE",
        });
        if (!response.ok) {
          const message = await buildSupportError(response, "Failed to revoke API key");
          setError(message);
          return false;
        }
        await loadKeys();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to revoke API key");
        return false;
      }
    },
    [loadKeys]
  );

  return { keys, createKey, revokeKey, error };
}
