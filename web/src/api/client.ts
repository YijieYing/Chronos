import type { ApiEnvelope } from "./contracts";

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(path, { cache: "no-store", ...init });
  let envelope: ApiEnvelope<T> | null = null;
  try {
    envelope = await response.json() as ApiEnvelope<T>;
  } catch {
    // Preserve the HTTP status when a stale local service returns HTML.
  }
  if (!response.ok || !envelope || envelope.error || envelope.data === null) {
    throw new Error(
      envelope?.error?.message ?? `${response.status} ${response.statusText}`,
    );
  }
  return envelope.data;
}
