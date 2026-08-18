import assert from "node:assert/strict";
import test from "node:test";

import {
  createAdminSessionToken,
  normalizeStrategyAdminUpdate,
  StrategyAdminValidationError,
  verifyAdminSessionToken,
} from "./strategy-admin";

const NOW = new Date("2026-08-18T12:00:00Z");

test("관리자 세션은 서명과 만료 시각을 검증한다", async () => {
  const now = NOW.getTime();
  const token = await createAdminSessionToken("test-only-secret", now, "fixed-nonce");
  assert.equal(await verifyAdminSessionToken(token, "test-only-secret", now + 1_000), true);
  assert.equal(await verifyAdminSessionToken(`${token}broken`, "test-only-secret", now + 1_000), false);
  assert.equal(await verifyAdminSessionToken(token, "test-only-secret", now + 9 * 60 * 60 * 1000), false);
});

test("관리자 수정은 표시 단계를 DB 세부 단계로 안전하게 변환한다", () => {
  const update = normalizeStrategyAdminUpdate(
    {
      stage: "STABILIZATION",
      operating_status: "ACTIVE",
      attention_level: "WATCH",
      attention_note: "  다음 실행을 확인  ",
    },
    current(),
    NOW,
  );

  assert.deepEqual(update, {
    lifecycle_stage: "STABILIZATION",
    operating_status: "ACTIVE",
    attention_level: "WATCH",
    attention_note: "다음 실행을 확인",
    hidden_by_default: false,
    phase_started_at: NOW.toISOString(),
  });
});

test("기존 미정 세부 단계는 미정으로 저장할 때 보존한다", () => {
  const update = normalizeStrategyAdminUpdate(
    {
      stage: "UNKNOWN",
      operating_status: "INACTIVE",
      attention_level: "NONE",
      attention_note: "지워질 메모",
    },
    current({ lifecycle_stage: "IDEA", operating_status: "INACTIVE" }),
    NOW,
  );

  assert.equal(update.lifecycle_stage, "IDEA");
  assert.equal(update.phase_started_at, "2026-08-01T00:00:00Z");
  assert.equal(update.attention_note, null);
});

test("폐쇄 단계와 운영 상태의 모순은 거부한다", () => {
  assert.throws(
    () => normalizeStrategyAdminUpdate(
      {
        stage: "CLOSED",
        operating_status: "ACTIVE",
        attention_level: "NONE",
        attention_note: null,
      },
      current(),
      NOW,
    ),
    StrategyAdminValidationError,
  );
});

function current(overrides: Record<string, unknown> = {}) {
  return {
    lifecycle_stage: "LIVE_VALIDATION" as const,
    operating_status: "ACTIVE" as const,
    attention_level: "NONE" as const,
    phase_started_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}
