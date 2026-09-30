import "server-only";

export type Loaded<T> =
  | { state: "ok"; data: T }
  | { state: "missing"; path: string }
  | { state: "error"; path: string; message: string };

const TTL_MS = 45_000;
const memo = new Map<string, { at: number; value: Loaded<unknown> }>();

function fixturesEnabled() {
  return process.env.DASHBOARD_FIXTURES === "1";
}

async function readFixture(path: string): Promise<Loaded<string>> {
  const { readFile } = await import("node:fs/promises");
  const { join } = await import("node:path");
  try {
    return { state: "ok", data: await readFile(join(process.cwd(), "fixtures", path.replace(/^latest\//, "")), "utf8") };
  } catch {
    return { state: "missing", path };
  }
}

async function readRemote(path: string): Promise<Loaded<string>> {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) return { state: "error", path, message: "서버 환경변수(SUPABASE_URL/SUPABASE_SECRET_KEY)가 없습니다." };
  try {
    const res = await fetch(`${url.replace(/\/$/, "")}/storage/v1/object/polylab/${path}`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
      cache: "no-store",
    });
    // Supabase Storage answers a missing object with 400 {"error":"not_found"} as often as 404.
    if (res.status === 404) return { state: "missing", path };
    if (res.status === 400) {
      const body = await res.text();
      if (/bucket not found/i.test(body)) return { state: "error", path, message: "polylab 버킷 없음" };
      if (/NoSuchKey|not[_ ]?found/i.test(body)) return { state: "missing", path };
      return { state: "error", path, message: `HTTP 400` };
    }
    if (!res.ok) return { state: "error", path, message: `HTTP ${res.status}` };
    return { state: "ok", data: await res.text() };
  } catch (e) {
    return { state: "error", path, message: e instanceof Error ? e.message : "fetch 실패" };
  }
}

async function loadText(path: string): Promise<Loaded<string>> {
  const hit = memo.get(path);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value as Loaded<string>;
  const value = fixturesEnabled() ? await readFixture(path) : await readRemote(path);
  if (value.state !== "error") memo.set(path, { at: Date.now(), value });
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

export function isFixtureMode() {
  return fixturesEnabled();
}

export const STRATEGY_ID = /^[a-z0-9][a-z0-9_-]{0,80}$/;
export const REPORT_PATH = /^(daily|weekly|monthly)\/[A-Za-z0-9_-]{1,100}$/;
