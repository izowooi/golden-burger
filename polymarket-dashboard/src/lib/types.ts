export interface AlgorithmAccount {
  account_id: string;
  jenkins_name: string;
  algorithm_code: string;
  instance_no: number | null;
  sort_order: number;
}

export interface AlgorithmBalance {
  report_date: string;
  account_id: string;
  total_value: number;
  position_value: number;
  cash_value: number;
  reported_at: string;
}

export interface PortfolioTotal {
  report_date: string;
  total_value: number;
  position_value: number;
  cash_value: number;
  reported_at: string;
}

export interface PortfolioResponse {
  accounts: AlgorithmAccount[];
  balances: AlgorithmBalance[];
  totals: PortfolioTotal[];
  range: {
    start: string | null;
    end: string | null;
  };
  generated_at: string;
}

export interface HostStorageSnapshot {
  report_date: string;
  host_id: string;
  mount_id: string;
  mount_label: string;
  mount_path: string;
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  reported_at: string;
}

export interface HostStorageResponse {
  snapshots: HostStorageSnapshot[];
  range: {
    start: string | null;
    end: string | null;
  };
  generated_at: string;
}

export type BalanceMetric = "total_value" | "position_value" | "cash_value";
export type ChartMode = "balance" | "return";

export interface PerformanceSummary {
  accountId: string;
  startDate: string;
  endDate: string;
  startValue: number;
  endValue: number;
  changeValue: number;
  returnRate: number | null;
  latestPosition: number;
  latestCash: number;
  points: number;
}

export type ChartRow = {
  date: string;
  [accountId: string]: string | number | null;
};

export type StrategyKind =
  | "LIVE_TRADING"
  | "SIMULATION_RESEARCH"
  | "INFRA_RESEARCH"
  | "LEGACY_TRADING";

export type LifecycleStage =
  | "IDEA"
  | "IMPLEMENTING"
  | "IMPLEMENTED"
  | "SIMULATION"
  | "LIVE_VALIDATION"
  | "STABILIZATION"
  | "PROFITABILITY"
  | "PRODUCTION"
  | "CLOSED";

export type OperatingStatus =
  | "ACTIVE"
  | "PAUSED"
  | "CLOSE_ONLY"
  | "INACTIVE"
  | "CLOSED";

export type JenkinsJobMode =
  | "LIVE"
  | "SIMULATION"
  | "SHADOW"
  | "CLOSE_ONLY"
  | "RESEARCH";

export type CheckpointStatus = "PENDING" | "COMPLETED" | "BLOCKED" | "CANCELLED";

export type CheckpointType =
  | "COLLECTION_HEALTH"
  | "DAY_7_REVIEW"
  | "DAY_30_REVIEW"
  | "MONTHLY_REVIEW"
  | "STABILITY_GATE"
  | "PROFITABILITY_REVIEW"
  | "DEPLOYMENT_DECISION";

export interface StrategyLifecycle {
  strategy_id: string;
  display_name: string;
  codename: string;
  thesis: string;
  strategy_kind: StrategyKind;
  lifecycle_stage: LifecycleStage;
  operating_status: OperatingStatus;
  evaluation_started_at: string | null;
  phase_started_at: string | null;
  evaluation_horizon_days: number | null;
  current_summary: string;
  attention_note: string | null;
  attention_level: "NONE" | "INFO" | "WATCH" | "CRITICAL";
  source_ref: string;
  hidden_by_default: boolean;
  sort_order: number;
  updated_at: string;
}

export interface StrategyJenkinsJob {
  job_name: string;
  strategy_id: string;
  runtime_job: string | null;
  mode: JenkinsJobMode;
  treatment_label: string | null;
  schedule: string | null;
  expected_cadence_minutes: number | null;
  workspace_class: "INTERNAL" | "EXTERNAL" | "UNKNOWN";
  buildable: boolean | null;
  enabled: boolean | null;
  in_queue: boolean | null;
  building: boolean | null;
  job_color: string | null;
  last_build_number: number | null;
  last_build_status: string | null;
  last_build_started_at: string | null;
  last_build_duration_ms: number | null;
  config_sha256: string | null;
  observed_at: string | null;
  notes: string | null;
  updated_at: string;
}

export interface StrategyCheckpoint {
  checkpoint_id: string;
  strategy_id: string;
  checkpoint_type: CheckpointType;
  title: string;
  due_at: string | null;
  status: CheckpointStatus;
  completed_at: string | null;
  instructions: string;
  source_ref: string;
  updated_at: string;
}

export interface StrategySyncRun {
  sync_run_id: number;
  collector_name: string;
  started_at: string;
  finished_at: string | null;
  status: "RUNNING" | "SUCCESS" | "PARTIAL" | "FAILED";
  jobs_expected: number;
  jobs_observed: number;
  jobs_failed: number;
  error_summary: string | null;
}

export interface StrategyLifecycleResponse {
  strategies: StrategyLifecycle[];
  jobs: StrategyJenkinsJob[];
  checkpoints: StrategyCheckpoint[];
  sync_runs: StrategySyncRun[];
  jenkins_base_url: string | null;
  generated_at: string;
}
