import "server-only";

import type { NextRequest, NextResponse } from "next/server";

import {
  ADMIN_SESSION_COOKIE,
  ADMIN_SESSION_TTL_SECONDS,
  verifyAdminSessionToken,
  type StrategyAdminCredential,
} from "@/lib/strategy-admin";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export function getStrategyAdminSessionSecret() {
  const value = process.env.SUPABASE_SECRET_KEY;
  return value && value.trim() ? `strategy-admin-session-v1:${value}` : null;
}

export async function getStrategyAdminCredential(): Promise<StrategyAdminCredential | null> {
  const result = await createServerSupabaseClient()
    .from("pd_admin_credentials")
    .select("password_salt,password_hash,iterations")
    .eq("credential_id", "strategy-dashboard")
    .maybeSingle();
  if (result.error) throw new Error(result.error.message);
  return result.data as StrategyAdminCredential | null;
}

export async function isStrategyAdminRequest(request: NextRequest, secret: string) {
  return verifyAdminSessionToken(request.cookies.get(ADMIN_SESSION_COOKIE)?.value, secret);
}

export function isSameOriginRequest(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    const requestUrl = new URL(request.url);
    const host = firstForwardedValue(
      request.headers.get("x-forwarded-host") ?? request.headers.get("host") ?? requestUrl.host,
    );
    const protocol = firstForwardedValue(
      request.headers.get("x-forwarded-proto") ?? requestUrl.protocol,
    ).replace(/:$/, "");
    const originUrl = new URL(origin);
    return originUrl.host === host && originUrl.protocol === `${protocol}:`;
  } catch {
    return false;
  }
}

function firstForwardedValue(value: string) {
  return value.split(",")[0].trim();
}

export function setStrategyAdminCookie(response: NextResponse, token: string) {
  response.cookies.set({
    name: ADMIN_SESSION_COOKIE,
    value: token,
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: ADMIN_SESSION_TTL_SECONDS,
  });
}

export function clearStrategyAdminCookie(response: NextResponse) {
  response.cookies.set({
    name: ADMIN_SESSION_COOKIE,
    value: "",
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
}

export function noStoreHeaders() {
  return { "Cache-Control": "private, no-store, max-age=0" };
}
