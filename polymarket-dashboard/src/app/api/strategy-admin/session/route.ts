import { NextRequest, NextResponse } from "next/server";

import { ADMIN_SESSION_COOKIE } from "@/lib/strategy-admin";
import {
  clearStrategyAdminCookie,
  getStrategyAdminSessionSecret,
  isStrategyAdminConfigured,
  isStrategyAdminRequest,
  noStoreHeaders,
} from "@/lib/strategy-admin-server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const sessionSecret = getStrategyAdminSessionSecret();
  let configured = false;
  try {
    configured = await isStrategyAdminConfigured();
  } catch {
    return NextResponse.json(
      { authenticated: false, configured: false },
      { status: 503, headers: noStoreHeaders() },
    );
  }
  if (!sessionSecret || !configured) {
    return NextResponse.json(
      { authenticated: false, configured: false },
      { headers: noStoreHeaders() },
    );
  }

  const authenticated = await isStrategyAdminRequest(request, sessionSecret);
  const response = NextResponse.json(
    { authenticated, configured: true },
    { headers: noStoreHeaders() },
  );
  if (!authenticated && request.cookies.has(ADMIN_SESSION_COOKIE)) {
    clearStrategyAdminCookie(response);
  }
  return response;
}
