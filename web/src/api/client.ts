import { ApiError, type ApiErrorKind } from "@/types";

const API_BASE = (((import.meta as unknown as { env?: { VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL) ?? "").replace(/\/$/, "");
const DEFAULT_TIMEOUT_MS = 10_000;

export interface ApiRequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 404) return "not_found";
  if (status === 422) return "validation";
  if (status === 503) return "unavailable";
  return "network";
}

export async function apiGet<T>(path: string, params?: Record<string, string | number | null | undefined>, opts: ApiRequestOptions = {}): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const controller = new AbortController();
  const timeout = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = window.setTimeout(() => controller.abort(), timeout);
  const signal = opts.signal ?? controller.signal;
  let res: Response;
  try {
    res = await fetch(url.toString(), { signal });
  } catch (e) {
    clearTimeout(timer);
    if ((e as Error)?.name === "AbortError") throw new ApiError("timeout", 0, "Request timed out");
    throw new ApiError("network", 0, (e as Error)?.message ?? "Network error");
  }
  clearTimeout(timer);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); if (typeof j?.detail === "string") detail = j.detail; } catch { /* ignore */ }
    throw new ApiError(kindForStatus(res.status), res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = { get: apiGet };