import assert from "node:assert/strict";
import test from "node:test";

import {
  getCheckpointState,
  getDashboardStage,
  getDaysElapsed,
  getEvaluationProgress,
  getJenkinsHealth,
  getNextCheckpoint,
  getStrategyHealth,
  DEFAULT_DASHBOARD_STAGES,
  DASHBOARD_STAGES,
} from "./strategy-lifecycle";
import type {
  StrategyCheckpoint,
  StrategyJenkinsJob,
  StrategyLifecycle,
} from "./types";

const NOW = new Date("2026-08-18T12:00:00Z");

test("세부 lifecycle 값은 미정을 포함한 다섯 단계로 묶인다", () => {
  assert.deepEqual(DASHBOARD_STAGES, ["UNKNOWN", "SIMULATION", "VALIDATION", "STABILIZATION", "CLOSED"]);
  assert.deepEqual(DEFAULT_DASHBOARD_STAGES, ["SIMULATION", "VALIDATION", "STABILIZATION"]);
  assert.equal(getDashboardStage("IDEA"), "UNKNOWN");
  assert.equal(getDashboardStage("IMPLEMENTING"), "UNKNOWN");
  assert.equal(getDashboardStage("IMPLEMENTED"), "UNKNOWN");
  assert.equal(getDashboardStage("PROFITABILITY"), "VALIDATION");
  assert.equal(getDashboardStage("PRODUCTION"), "STABILIZATION");
  assert.equal(getDashboardStage("CLOSED"), "CLOSED");
});

test("checkpoint 상태는 현재 시각에 따라 동적으로 계산된다", () => {
  assert.equal(getCheckpointState(checkpoint({ due_at: "2026-08-18T11:00:00Z" }), NOW), "OVERDUE");
  assert.equal(getCheckpointState(checkpoint({ due_at: "2026-08-19T11:00:00Z" }), NOW), "DUE");
  assert.equal(getCheckpointState(checkpoint({ due_at: "2026-08-20T12:00:00Z" }), NOW), "UPCOMING");
  assert.equal(getCheckpointState(checkpoint({ due_at: null }), NOW), "UNSCHEDULED");
  assert.equal(
    getCheckpointState(checkpoint({ status: "COMPLETED", completed_at: "2026-08-17T00:00:00Z" }), NOW),
    "COMPLETED",
  );
});

test("평가 경과일과 100% capped progress를 계산한다", () => {
  assert.equal(getDaysElapsed("2026-08-12T12:00:00Z", NOW), 6);
  assert.equal(
    getEvaluationProgress(strategy({ evaluation_started_at: "2026-07-01T00:00:00Z", evaluation_horizon_days: 30 }), NOW),
    100,
  );
});

test("Jenkins health는 build failure와 관측 stale을 구분한다", () => {
  assert.equal(getJenkinsHealth(job({ last_build_status: "SUCCESS" }), NOW), "HEALTHY");
  assert.equal(getJenkinsHealth(job({ last_build_status: "FAILURE" }), NOW), "FAILED");
  assert.equal(
    getJenkinsHealth(job({ observed_at: "2026-08-18T10:00:00Z", last_build_status: "SUCCESS" }), NOW),
    "STALE",
  );
  assert.equal(getJenkinsHealth(job({ enabled: false }), NOW), "DISABLED");
  assert.equal(
    getJenkinsHealth(job({ building: true, last_build_status: "BUILDING", last_build_started_at: "2026-08-18T11:45:00Z" }), NOW),
    "STALE",
  );
});

test("attention note는 성공 build보다 우선하는 전략 경고다", () => {
  assert.equal(
    getStrategyHealth(strategy({ attention_note: "lifecycle blocker", attention_level: "CRITICAL" }), [job()], NOW),
    "ATTENTION",
  );
  assert.equal(getStrategyHealth(strategy(), [job()], NOW), "HEALTHY");
});

test("다음 검토는 완료 항목을 제외한 가장 이른 pending due다", () => {
  const next = getNextCheckpoint([
    checkpoint({ checkpoint_id: "done", due_at: "2026-08-17T00:00:00Z", status: "COMPLETED", completed_at: "2026-08-17T01:00:00Z" }),
    checkpoint({ checkpoint_id: "later", due_at: "2026-08-20T00:00:00Z" }),
    checkpoint({ checkpoint_id: "first", due_at: "2026-08-19T00:00:00Z" }),
  ]);
  assert.equal(next?.checkpoint_id, "first");
});

function strategy(overrides: Partial<StrategyLifecycle> = {}): StrategyLifecycle {
  return {
    strategy_id: "golden-test",
    display_name: "Golden Test",
    codename: "Test",
    thesis: "test",
    strategy_kind: "LIVE_TRADING",
    lifecycle_stage: "LIVE_VALIDATION",
    operating_status: "ACTIVE",
    evaluation_started_at: "2026-08-12T12:00:00Z",
    phase_started_at: "2026-08-12T12:00:00Z",
    evaluation_horizon_days: 30,
    current_summary: "test",
    attention_note: null,
    attention_level: "NONE",
    source_ref: "test.md",
    hidden_by_default: false,
    sort_order: 1,
    updated_at: "2026-08-18T00:00:00Z",
    ...overrides,
  };
}

function job(overrides: Partial<StrategyJenkinsJob> = {}): StrategyJenkinsJob {
  return {
    job_name: "polybot-test",
    strategy_id: "golden-test",
    runtime_job: "test",
    mode: "LIVE",
    treatment_label: null,
    purpose: null,
    test_size_label: null,
    experiment_started_at: null,
    experiment_ends_at: null,
    schedule: "H/5 * * * *",
    expected_cadence_minutes: 5,
    workspace_class: "INTERNAL",
    buildable: true,
    enabled: true,
    in_queue: false,
    building: false,
    job_color: "blue",
    last_build_number: 1,
    last_build_status: "SUCCESS",
    last_build_started_at: "2026-08-18T11:55:00Z",
    last_build_duration_ms: 3_000,
    config_sha256: null,
    observed_at: "2026-08-18T11:59:00Z",
    notes: null,
    updated_at: "2026-08-18T11:59:00Z",
    ...overrides,
  };
}

function checkpoint(overrides: Partial<StrategyCheckpoint> = {}): StrategyCheckpoint {
  return {
    checkpoint_id: "test:day7",
    strategy_id: "golden-test",
    checkpoint_type: "DAY_7_REVIEW",
    title: "test",
    due_at: "2026-08-20T00:00:00Z",
    status: "PENDING",
    completed_at: null,
    instructions: "test",
    source_ref: "test.md",
    updated_at: "2026-08-18T00:00:00Z",
    ...overrides,
  };
}
