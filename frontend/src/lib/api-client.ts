export {
  parseApiError,
  formatSupportMessage,
  buildSupportError,
  type ApiErrorInfo,
} from "@/lib/api-error";

import { buildSupportError } from "@/lib/api-error";

export async function fetchJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw new Error(await buildSupportError(response, `Request failed: ${response.status}`));
  }
  return (await response.json()) as T;
}

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw new Error(await buildSupportError(response, `Request failed: ${response.status}`));
  }
  return response;
}
