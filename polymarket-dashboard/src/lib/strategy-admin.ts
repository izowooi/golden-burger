import {
  getDashboardStage,
  type DashboardStage,
} from "@/lib/strategy-lifecycle";
import type {
  AttentionLevel,
  LifecycleStage,
  OperatingStatus,
  StrategyLifecycle,
} from "@/lib/types";

export const ADMIN_SESSION_COOKIE = "pb_strategy_admin";
export const ADMIN_SESSION_TTL_SECONDS = 8 * 60 * 60;

export const ADMIN_OPERATING_STATUSES: OperatingStatus[] = [
  "ACTIVE",
  "PAUSED",
  "CLOSE_ONLY",
  "INACTIVE",
  "CLOSED",
];

export const ADMIN_ATTENTION_LEVELS: AttentionLevel[] = [
  "NONE",
  "INFO",
  "WATCH",
  "CRITICAL",
];

export interface StrategyAdminUpdate {
  lifecycle_stage: LifecycleStage;
  operating_status: OperatingStatus;
  attention_level: AttentionLevel;
  attention_note: string | null;
  hidden_by_default: boolean;
  phase_started_at: string | null;
}

export interface StrategyAdminCredential {
  password_salt: string;
  password_hash: string;
  iterations: number;
}

export class StrategyAdminValidationError extends Error {}

export async function matchesAdminPassword(
  candidate: string,
  credential: StrategyAdminCredential,
) {
  if (!candidate || candidate.length > 256 || !isValidCredential(credential)) return false;
  const candidateHash = await deriveAdminPasswordHash(
    candidate,
    credential.password_salt,
    credential.iterations,
  );
  return constantTimeEqual(
    new TextEncoder().encode(candidateHash),
    new TextEncoder().encode(credential.password_hash.toLocaleLowerCase("en-US")),
  );
}

export async function deriveAdminPasswordHash(
  password: string,
  saltHex: string,
  iterations: number,
) {
  if (!password || password.length > 256 || !/^[0-9a-f]{32}$/i.test(saltHex)) {
    throw new StrategyAdminValidationError("관리자 암호 설정이 올바르지 않습니다.");
  }
  if (!Number.isInteger(iterations) || iterations < 100_000 || iterations > 1_000_000) {
    throw new StrategyAdminValidationError("관리자 암호 반복 횟수가 올바르지 않습니다.");
  }
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: hexToBytes(saltHex),
      iterations,
    },
    keyMaterial,
    256,
  );
  return bytesToHex(new Uint8Array(bits));
}

export async function createAdminSessionToken(
  secret: string,
  now = Date.now(),
  nonce = crypto.randomUUID(),
) {
  const expiresAt = now + ADMIN_SESSION_TTL_SECONDS * 1000;
  const payload = `${expiresAt}.${nonce}`;
  const signature = await sign(payload, secret);
  return `${payload}.${signature}`;
}

export async function verifyAdminSessionToken(
  token: string | undefined,
  secret: string,
  now = Date.now(),
) {
  if (!token || !secret || token.length > 512) return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  const [expiresAtValue, nonce, signature] = parts;
  const expiresAt = Number(expiresAtValue);
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= now || !nonce || !signature) return false;
  const expectedSignature = await sign(`${expiresAtValue}.${nonce}`, secret);
  return constantTimeEqual(
    new TextEncoder().encode(signature),
    new TextEncoder().encode(expectedSignature),
  );
}

export function normalizeStrategyAdminUpdate(
  input: unknown,
  current: Pick<
    StrategyLifecycle,
    "lifecycle_stage" | "operating_status" | "attention_level" | "phase_started_at"
  >,
  now = new Date(),
): StrategyAdminUpdate {
  if (!isRecord(input)) throw new StrategyAdminValidationError("수정할 값이 올바르지 않습니다.");

  const stage = parseEnum(input.stage, [
    "UNKNOWN",
    "SIMULATION",
    "VALIDATION",
    "STABILIZATION",
    "CLOSED",
  ] satisfies DashboardStage[], "단계");
  const operatingStatus = parseEnum(input.operating_status, ADMIN_OPERATING_STATUSES, "운영 상태");
  const attentionLevel = parseEnum(input.attention_level, ADMIN_ATTENTION_LEVELS, "확인 표시");
  const attentionNote = parseAttentionNote(input.attention_note);

  if (stage === "CLOSED" && !["CLOSE_ONLY", "CLOSED"].includes(operatingStatus)) {
    throw new StrategyAdminValidationError("폐쇄 단계는 청산 전용 또는 폐쇄 상태여야 합니다.");
  }
  if (stage !== "CLOSED" && operatingStatus === "CLOSED") {
    throw new StrategyAdminValidationError("폐쇄 상태를 사용하려면 단계도 폐쇄로 선택해야 합니다.");
  }

  const lifecycleStage = lifecycleStageForDashboardStage(stage, current.lifecycle_stage);
  const stageChanged = lifecycleStage !== current.lifecycle_stage;

  return {
    lifecycle_stage: lifecycleStage,
    operating_status: operatingStatus,
    attention_level: attentionLevel,
    attention_note: attentionLevel === "NONE" ? null : attentionNote,
    hidden_by_default: stage === "CLOSED",
    phase_started_at: stageChanged ? now.toISOString() : current.phase_started_at,
  };
}

function lifecycleStageForDashboardStage(
  stage: DashboardStage,
  current: LifecycleStage,
): LifecycleStage {
  if (stage === "UNKNOWN") {
    return getDashboardStage(current) === "UNKNOWN" ? current : "IMPLEMENTED";
  }
  if (stage === "SIMULATION") return "SIMULATION";
  if (stage === "VALIDATION") return "LIVE_VALIDATION";
  if (stage === "STABILIZATION") return "STABILIZATION";
  return "CLOSED";
}

function parseAttentionNote(value: unknown) {
  if (value == null) return null;
  if (typeof value !== "string") {
    throw new StrategyAdminValidationError("확인 메모가 올바르지 않습니다.");
  }
  const normalized = value.trim();
  if (normalized.length > 500) {
    throw new StrategyAdminValidationError("확인 메모는 500자 이하여야 합니다.");
  }
  return normalized || null;
}

function parseEnum<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new StrategyAdminValidationError(`${label} 값이 올바르지 않습니다.`);
  }
  return value as T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function sign(payload: string, secret: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload)),
  );
  return bytesToHex(signature);
}

function constantTimeEqual(left: Uint8Array, right: Uint8Array) {
  let difference = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return difference === 0;
}

function isValidCredential(credential: StrategyAdminCredential) {
  return /^[0-9a-f]{32}$/i.test(credential.password_salt)
    && /^[0-9a-f]{64}$/i.test(credential.password_hash)
    && Number.isInteger(credential.iterations)
    && credential.iterations >= 100_000
    && credential.iterations <= 1_000_000;
}

function hexToBytes(value: string) {
  return Uint8Array.from(value.match(/.{2}/g) ?? [], (part) => Number.parseInt(part, 16));
}

function bytesToHex(value: Uint8Array) {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
