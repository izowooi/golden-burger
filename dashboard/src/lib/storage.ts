"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

// Pages are a static export rendered in the browser: the Cloudflare free plan allows ~10ms CPU per worker
// request, far below what server-rendering these pages costs. The worker (worker/index.ts) only streams
// Storage objects through /data/<path>, keeping the Supabase secret server-side.

export type Loaded<T> =
  | { state: "ok"; data: T }
  | { state: "missing"; path: string }
  | { state: "error"; path: string; message: string };

const DATA_BASE = process.env.NEXT_PUBLIC_DATA_BASE || "/data/";
const FIXTURES = process.env.NEXT_PUBLIC_DASHBOARD_FIXTURES === "1";
const TTL_MS = 45_000;
const memo = new Map<string, { at: number; value: Promise<Loaded<string>> }>();

function url(path: string) {
  // Fixtures mirror the bucket without the latest/ prefix (see fixtures/).
  return DATA_BASE + (FIXTURES ? path.replace(/^latest\//, "") : path);
}

async function fetchText(path: string): Promise<Loaded<string>> {
  try {
    const res = await fetch(url(path), { cache: "no-store" });
    if (res.status === 404) return { state: "missing", path };
    if (!res.ok) return { state: "error", path, message: (await res.text()).slice(0, 200) || `HTTP ${res.status}` };
    return { state: "ok", data: await res.text() };
  } catch (e) {
    return { state: "error", path, message: e instanceof Error ? e.message : "fetch 실패" };
  }
}

function loadText(path: string): Promise<Loaded<string>> {
  const hit = memo.get(path);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value;
  const value = fetchText(path);
  memo.set(path, { at: Date.now(), value });
  value.then((v) => { if (v.state === "error") memo.delete(path); });
  return value;
}

export async function loadJson<T>(path: string): Promise<Loaded<T>> {
  const text = await loadText(path);
  if (text.state !== "ok") return text;
  try {
    return { state: "ok", data: JSON.parse(text.data) as T };
  } catch {
    return { state: "error", path, message: "JSON 파싱 실패" };
  }
}

export const loadMarkdown = loadText;

function useLoaded<T>(path: string | null, load: (p: string) => Promise<Loaded<T>>): Loaded<T> | null {
  const [got, setGot] = useState<{ path: string; value: Loaded<T> } | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    load(path).then((value) => { if (live) setGot({ path, value }); });
    return () => { live = false; };
  }, [path, load]);
  return path && got?.path === path ? got.value : null;
}

/** null while loading (or when path is null). */
export function useJson<T>(path: string | null): Loaded<T> | null {
  return useLoaded<T>(path, loadJson as (p: string) => Promise<Loaded<T>>);
}

export function useMarkdown(path: string | null): Loaded<string> | null {
  return useLoaded(path, loadMarkdown);
}

export function isFixtureMode() {
  return FIXTURES;
}

export const STRATEGY_ID = /^[a-z0-9][a-z0-9_-]{0,80}$/;
export const REPORT_PATH = /^(daily|weekly|monthly)\/[A-Za-z0-9_-]{1,100}$/;

const noSubscribe = () => () => {};

/**
 * The URL path after /<prefix>/ (e.g. "goal-over-all" for /strategies/goal-over-all), falling back to ?p=.
 * undefined while prerendering: the shell HTML is shared by every id, so it is read from location in the browser.
 */
export function usePathSegments(prefix: string): string | null | undefined {
  const href = useSyncExternalStore(noSubscribe, () => window.location.pathname + window.location.search, () => undefined);
  if (href === undefined) return undefined;
  const u = new URL(href, "http://x");
  const m = u.pathname.match(new RegExp(`^/${prefix}/(.+?)/?$`));
  return m ? m[1] : u.searchParams.get("p");
}
