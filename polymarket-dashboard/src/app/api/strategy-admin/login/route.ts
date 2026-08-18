import { NextRequest, NextResponse } from "next/server";

import {
  createAdminSessionToken,
  matchesAdminPassword,
} from "@/lib/strategy-admin";
import {
  getStrategyAdminCredential,
  getStrategyAdminSessionSecret,
  isSameOriginRequest,
  noStoreHeaders,
  setStrategyAdminCookie,
} from "@/lib/strategy-admin-server";

export const dynamic = "force-dynamic";

const FAILURE_WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILURES = 5;
const failedAttempts = new Map<string, { count: number; resetAt: number }>();

export async function POST(request: NextRequest) {
  if (!isSameOriginRequest(request)) {
    return NextResponse.json({ error: "허용되지 않은 요청입니다." }, { status: 403, headers: noStoreHeaders() });
  }

  const sessionSecret = getStrategyAdminSessionSecret();
  let credential: Awaited<ReturnType<typeof getStrategyAdminCredential>> = null;
  try {
    credential = await getStrategyAdminCredential();
  } catch {
    return NextResponse.json(
      { error: "관리자 설정을 확인하지 못했습니다." },
      { status: 503, headers: noStoreHeaders() },
    );
  }
  if (!sessionSecret || !credential) {
    return NextResponse.json(
      { error: "관리자 모드가 아직 설정되지 않았습니다." },
      { status: 503, headers: noStoreHeaders() },
    );
  }

  const clientKey = getClientKey(request);
  if (isRateLimited(clientKey)) {
    return NextResponse.json(
      { error: "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요." },
      { status: 429, headers: noStoreHeaders() },
    );
  }

  let password = "";
  try {
    const payload = await request.json() as { password?: unknown };
    password = typeof payload.password === "string" ? payload.password : "";
  } catch {
    return NextResponse.json({ error: "요청 형식이 올바르지 않습니다." }, { status: 400, headers: noStoreHeaders() });
  }

  if (!(await matchesAdminPassword(password, credential))) {
    recordFailure(clientKey);
    return NextResponse.json(
      { error: "암호가 올바르지 않습니다." },
      { status: 401, headers: noStoreHeaders() },
    );
  }

  failedAttempts.delete(clientKey);
  const response = NextResponse.json(
    { authenticated: true, configured: true },
    { headers: noStoreHeaders() },
  );
  setStrategyAdminCookie(response, await createAdminSessionToken(sessionSecret));
  return response;
}

function getClientKey(request: NextRequest) {
  return request.headers.get("cf-connecting-ip")
    ?? request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    ?? "unknown";
}

function isRateLimited(clientKey: string) {
  const state = failedAttempts.get(clientKey);
  if (!state) return false;
  if (state.resetAt <= Date.now()) {
    failedAttempts.delete(clientKey);
    return false;
  }
  return state.count >= MAX_FAILURES;
}

function recordFailure(clientKey: string) {
  const now = Date.now();
  const state = failedAttempts.get(clientKey);
  if (!state || state.resetAt <= now) {
    failedAttempts.set(clientKey, { count: 1, resetAt: now + FAILURE_WINDOW_MS });
    return;
  }
  state.count += 1;
}
