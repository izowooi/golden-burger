import { NextRequest, NextResponse } from "next/server";

import {
  clearStrategyAdminCookie,
  isSameOriginRequest,
  noStoreHeaders,
} from "@/lib/strategy-admin-server";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  if (!isSameOriginRequest(request)) {
    return NextResponse.json({ error: "허용되지 않은 요청입니다." }, { status: 403, headers: noStoreHeaders() });
  }
  const response = NextResponse.json({ authenticated: false }, { headers: noStoreHeaders() });
  clearStrategyAdminCookie(response);
  return response;
}
