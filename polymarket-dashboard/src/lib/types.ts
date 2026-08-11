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
