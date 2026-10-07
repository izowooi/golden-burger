// Cloudflare Worker for poly.zowoo.uk. Pages are a Next static export served straight from assets (no worker
// CPU); this worker only runs for the routes listed in wrangler.jsonc `run_worker_first`. The free plan allows
// ~10ms CPU per request, so Storage bodies are streamed through untouched — never buffered or parsed here.

interface Env {
  ASSETS: { fetch(request: Request): Promise<Response> };
  SUPABASE_URL?: string;
  SUPABASE_SECRET_KEY?: string;
}

const DATA_PATH = /^(latest|reports)\/[A-Za-z0-9_\-/]{1,200}\.(json|md)$/;
const SHELLS: [RegExp, string][] = [
  [/^\/strategies\/[^/]+\/?$/, "/strategy"],
  [/^\/reports\/(daily|weekly|monthly)\/[^/]+\/?$/, "/report"],
];

function text(status: number, body: string) {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
}

async function data(path: string, env: Env): Promise<Response> {
  if (!DATA_PATH.test(path) || path.includes("..") || path.includes("//")) return text(400, "invalid path");
  if (!env.SUPABASE_URL || !env.SUPABASE_SECRET_KEY) return text(500, "서버 환경변수(SUPABASE_URL/SUPABASE_SECRET_KEY)가 없습니다.");
  const key = env.SUPABASE_SECRET_KEY;
  let upstream: Response;
  try {
    upstream = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}/storage/v1/object/polylab/${path}`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
  } catch {
    return text(502, "Storage fetch 실패");
  }
  // Supabase Storage answers a missing object with 400 {"error":"not_found"} as often as 404.
  if (upstream.status === 404 || upstream.status === 400) {
    const body = await upstream.text();
    if (/bucket not found/i.test(body)) return text(502, "polylab 버킷 없음");
    if (upstream.status === 404 || /NoSuchKey|not[_ ]?found/i.test(body)) return text(404, "not found");
    return text(502, "HTTP 400");
  }
  if (!upstream.ok) return text(502, `HTTP ${upstream.status}`);
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": path.endsWith(".md") ? "text/markdown; charset=utf-8" : "application/json; charset=utf-8",
      "cache-control": "private, max-age=30",
    },
  });
}

const worker = {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/data/")) {
      if (request.method !== "GET") return text(405, "method not allowed");
      return data(decodeURIComponent(url.pathname.slice("/data/".length)), env);
    }
    for (const [re, shell] of SHELLS) {
      if (re.test(url.pathname)) return env.ASSETS.fetch(new Request(new URL(shell, url), request));
    }
    return env.ASSETS.fetch(request);
  },
};

export default worker;
