// Types for latest/lts/* (docs/contracts/dashboard-json.md, "latest/lts" section; producer
// src/polylab/analysis/lts.py, study docs/research/hypothesis-lts.md).

export const LTS_SPORTS = ["soccer", "mlb", "nba", "nfl", "nhl"] as const;
export type LtsSport = (typeof LTS_SPORTS)[number];
export const LTS_SPORT_LABEL: Record<LtsSport, string> = {
  soccer: "축구 (거래 리그)", mlb: "MLB", nba: "NBA", nfl: "NFL", nhl: "NHL",
};
export const LTS_WAITS = ["5m", "30m", "end"] as const;
export const LTS_WAIT_LABEL: Record<string, string> = { "5m": "5분 대기", "30m": "30분 대기", end: "경기 끝까지 대기" };
export const ARM_LABEL: Record<string, string> = { king: "A · lts-king (늦게: 80–90%)", queen: "B · lts-queen (이르게: 60–70%)" };

export interface Half { n: number; pnl: number; cost: number; roi: number | null }

export interface TradeStats extends Half {
  h1: Half;
  h2: Half;
  lo80: number | null;
  lo95: number | null;
}

export interface MakerStats extends TradeStats {
  fill_rate: number | null;
  fill_rate_win: number | null;
  fill_rate_loss: number | null;
  fill_win_rate: number | null;
  mean_limit: number | null;
  collapse_fills: number;
  fill_wait_median_min: number | null;
  itt_pnl_per_signal: number | null;
  seasons: Record<string, { n: number; roi: number | null }>;
}

export interface RuleRecord {
  R1: boolean; R2: boolean; R3: boolean; R4: boolean; R5: boolean; R6: boolean;
  sig95: boolean;
  all_R1_R6: boolean;
  neighbors: number;
  neighbors_ok: number;
  pooled_roi: number | null;
}

export interface LtsCell {
  progress: number;
  t_min: number;
  y: number;
  band: [number, number];
  wait: string;
  signals: number;
  mean_price: number | null;
  win_rate: number | null;
  win_ci: [number | null, number | null];
  gap: number | null;
  gap_ci: [number | null, number | null];
  rebreak_rate: number | null;
  reversal_rate: number | null;
  mdd_median: number | null;
  mdd_p90: number | null;
  mdd_win_median: number | null;
  mdd_win_p90: number | null;
  median_wall: number | null;
  taker: TradeStats;
  main: MakerStats;
  spread02: TradeStats;
  strict: TradeStats;
  rules?: RuleRecord;
}

export interface LtsGrid { generated_at: string; sport: LtsSport; cells: LtsCell[] }

export interface ArmSelection {
  arm: string;
  progress: number;
  t_min: number;
  y: number;
  wait: string;
  h1_score: number;
  h2_roi: number | null;
  rules: RuleRecord;
  cell: LtsCell;
}

export interface LtsSportSummary {
  games: number;
  first_start?: string;
  last_start?: string;
  split?: string;
  rule?: { backtest_min_n: number; backtest_min_half_n: number; lookback_days: number; reason: string };
  median_game_min_now?: number | null;
  median_game_min_used?: number;
  progress_minutes?: Record<string, number>;
  passing_cells?: number;
  r1_cells?: number;
  mc?: { top: number; h2_median: number | null; h2_nonneg: number; h2_n: number };
  arms?: Record<string, ArmSelection | null>;
}

export interface LtsSummary {
  generated_at: string;
  doc: string;
  seconds: number | null;
  notes: string[];
  constants: {
    progress: number[];
    thresholds: number[];
    waits: string[];
    delta: number;
    floor_c: number;
    half_spread: number;
    taker_fee_rate: number;
    stake_usdc: number;
    soccer_leagues: string[];
  };
  sports: Partial<Record<LtsSport, LtsSportSummary>>;
}

export type LtsMetric = "maker_roi" | "taker_roi" | "gap" | "fill_rate" | "adverse" | "rebreak" | "reversal" | "mdd";

export const METRICS: { key: LtsMetric; label: string; kind: "div" | "seq"; hint: string }[] = [
  { key: "maker_roi", label: "maker ROI", kind: "div", hint: "체결된 지정가(매수호가−1틱, 수수료 0)를 정산까지 보유한 체결당 ROI" },
  { key: "taker_roi", label: "taker ROI", kind: "div", hint: "같은 신호를 매도호가에 즉시 사고 수수료 0.05×p(1−p)를 낸 ROI (비교 기준)" },
  { key: "gap", label: "보정 gap", kind: "div", hint: "신호 순간 선두의 실현 승률 − 평균 가격 (양수 = 과소평가)" },
  { key: "fill_rate", label: "체결률", kind: "seq", hint: "신호 중 지정가가 엄격 관통으로 체결된 비율" },
  { key: "adverse", label: "역선택", kind: "seq", hint: "P(체결 | 선두 패) − P(체결 | 선두 승). 클수록 지는 경기에서만 체결된다" },
  { key: "rebreak", label: "재이탈률", kind: "seq", hint: "신호 뒤 선두 가격이 한 번이라도 Y 아래로 내려간 비율" },
  { key: "reversal", label: "역전률", kind: "seq", hint: "선두가 끝내 진 비율 (축구는 무승부 포함)" },
  { key: "mdd", label: "최대 낙폭 중앙값", kind: "seq", hint: "신호 가격 − 신호 뒤 최저 가격 (가격 포인트)" },
];

export function metricValue(c: LtsCell, m: LtsMetric): number | null {
  switch (m) {
    case "maker_roi": return c.main.roi;
    case "taker_roi": return c.taker.roi;
    case "gap": return c.gap;
    case "fill_rate": return c.main.fill_rate;
    case "adverse": return c.main.fill_rate_loss !== null && c.main.fill_rate_win !== null ? c.main.fill_rate_loss - c.main.fill_rate_win : null;
    case "rebreak": return c.rebreak_rate;
    case "reversal": return c.reversal_rate;
    case "mdd": return c.mdd_median;
  }
}
