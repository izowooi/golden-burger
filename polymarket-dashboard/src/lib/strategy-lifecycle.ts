import type {
  LifecycleStage,
  StrategyCheckpoint,
  StrategyJenkinsJob,
  StrategyLifecycle,
} from "@/lib/types";

export type DashboardStage =
  | "IMPLEMENTED"
  | "SIMULATION"
  | "VALIDATION"
  | "STABILIZATION"
  | "CLOSED";

export const DASHBOARD_STAGES: DashboardStage[] = [
  "IMPLEMENTED",
  "SIMULATION",
  "VALIDATION",
  "STABILIZATION",
  "CLOSED",
];

export const DASHBOARD_STAGE_LABELS: Record<DashboardStage, string> = {
  IMPLEMENTED: "구현 완료",
  SIMULATION: "시뮬레이션",
  VALIDATION: "검증",
  STABILIZATION: "안정화",
  CLOSED: "폐쇄",
};

/**
 * The database keeps the finer-grained values for historical and collector
 * compatibility. Operators only need the five decisions represented here.
 */
export function getDashboardStage(stage: LifecycleStage): DashboardStage {
  if (["IDEA", "IMPLEMENTING", "IMPLEMENTED"].includes(stage)) return "IMPLEMENTED";
  if (stage === "SIMULATION") return "SIMULATION";
  if (["LIVE_VALIDATION", "PROFITABILITY"].includes(stage)) return "VALIDATION";
  if (["STABILIZATION", "PRODUCTION"].includes(stage)) return "STABILIZATION";
  return "CLOSED";
}

export type DynamicCheckpointState =
  | "UPCOMING"
  | "DUE"
  | "OVERDUE"
  | "UNSCHEDULED"
  | "COMPLETED"
  | "BLOCKED"
  | "CANCELLED";

export type JenkinsHealth =
  | "HEALTHY"
  | "BUILDING"
  | "FAILED"
  | "STALE"
  | "DISABLED"
  | "UNKNOWN";

export type StrategyHealth = "HEALTHY" | "ATTENTION" | "PAUSED" | "CLOSED" | "UNKNOWN";

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

export function getCheckpointState(
  checkpoint: StrategyCheckpoint,
  now = new Date(),
): DynamicCheckpointState {
  if (checkpoint.status !== "PENDING") return checkpoint.status;
  if (!checkpoint.due_at) return "UNSCHEDULED";

  const due = Date.parse(checkpoint.due_at);
  const current = now.getTime();
  if (!Number.isFinite(due)) return "UNSCHEDULED";
  if (due < current) return "OVERDUE";
  if (due - current <= DAY_MS) return "DUE";
  return "UPCOMING";
}

export function getDaysElapsed(startedAt: string | null, now = new Date()) {
  if (!startedAt) return null;
  const start = Date.parse(startedAt);
  if (!Number.isFinite(start)) return null;
  return Math.max(0, Math.floor((now.getTime() - start) / DAY_MS));
}

export function getEvaluationProgress(strategy: StrategyLifecycle, now = new Date()) {
  const daysElapsed = getDaysElapsed(strategy.evaluation_started_at, now);
  if (daysElapsed == null || strategy.evaluation_horizon_days == null) return null;
  return Math.min(100, Math.max(0, (daysElapsed / strategy.evaluation_horizon_days) * 100));
}

export function getJenkinsHealth(job: StrategyJenkinsJob, now = new Date()): JenkinsHealth {
  if (job.enabled === false || job.buildable === false) return "DISABLED";
  if (job.building) {
    const startedAt = job.last_build_started_at ? Date.parse(job.last_build_started_at) : Number.NaN;
    const cadenceMinutes = job.expected_cadence_minutes ?? 5;
    if (Number.isFinite(startedAt) && now.getTime() - startedAt > cadenceMinutes * 2 * 60_000) {
      return "STALE";
    }
    return "BUILDING";
  }
  if (!job.observed_at) return "UNKNOWN";

  const observedAt = Date.parse(job.observed_at);
  const cadenceMinutes = job.expected_cadence_minutes ?? 5;
  const staleAfterMinutes = Math.max(30, cadenceMinutes * 4);
  if (!Number.isFinite(observedAt) || now.getTime() - observedAt > staleAfterMinutes * 60_000) {
    return "STALE";
  }

  const status = job.last_build_status?.toUpperCase();
  if (status === "SUCCESS") return "HEALTHY";
  if (status === "BUILDING") return "BUILDING";
  if (status && ["FAILURE", "UNSTABLE", "ABORTED", "NOT_BUILT"].includes(status)) {
    return "FAILED";
  }
  return "UNKNOWN";
}

export function getStrategyHealth(
  strategy: StrategyLifecycle,
  jobs: StrategyJenkinsJob[],
  now = new Date(),
): StrategyHealth {
  if (strategy.lifecycle_stage === "CLOSED") return "CLOSED";
  if (["PAUSED", "INACTIVE"].includes(strategy.operating_status)) return "PAUSED";
  if (["WATCH", "CRITICAL"].includes(strategy.attention_level)) return "ATTENTION";
  if (!jobs.length) return strategy.operating_status === "ACTIVE" ? "ATTENTION" : "UNKNOWN";

  const health = jobs.map((job) => getJenkinsHealth(job, now));
  if (health.some((value) => ["FAILED", "STALE", "DISABLED"].includes(value))) {
    return "ATTENTION";
  }
  if (health.every((value) => ["HEALTHY", "BUILDING"].includes(value))) return "HEALTHY";
  return "UNKNOWN";
}

export function getNextCheckpoint(
  checkpoints: StrategyCheckpoint[],
): StrategyCheckpoint | null {
  return (
    checkpoints
      .filter((checkpoint) => checkpoint.status === "PENDING" && checkpoint.due_at)
      .sort((left, right) => Date.parse(left.due_at!) - Date.parse(right.due_at!))[0] ?? null
  );
}

export function getHoursUntil(timestamp: string, now = new Date()) {
  return (Date.parse(timestamp) - now.getTime()) / HOUR_MS;
}

export function isStrategyVisibleByClosedToggle(
  strategy: StrategyLifecycle,
  hideClosed: boolean,
) {
  return !hideClosed || strategy.lifecycle_stage !== "CLOSED";
}
