// Types for latest/ou05/* (docs/contracts/dashboard-json.md, "latest/ou05" sections).

export const OU05_CID = /^0x[0-9a-f]{64}$/;

export type Quantiles = { p10?: number; p25: number; p50: number; p75: number; p90?: number } | null;

export interface Band { key: string; label: string; kind: "pre" | "inplay" }
export interface Tier { key: string; label: string }

export interface OverroundRow {
  band: string;
  tier: string;
  markets: number;
  minutes: number;
  rows: number;
  two_sided_share: number | null;
  sum_ask: Quantiles;
  sum_bid: Quantiles;
  spread: Quantiles;
}

export interface DistSeries { tier: string; phase: "pre" | "last24h" | "inplay"; markets: number; minutes: number; shares: number[] }

export interface LeagueRow {
  league: string; tier: string; markets: number; volume_median: number | null; minutes: number;
  pre: Quantiles; last1h: Quantiles; inplay: Quantiles;
}

export interface VolumeRow { metric: "volume" | "liquidity"; lo: number; hi: number | null; markets: number; p25: number | null; p50: number | null; p75: number | null }

export interface CalibRow {
  band: string; tier: string; n: number; n_poll: number; mean_over: number; over_rate: number;
  ci_lo: number | null; ci_hi: number | null; gap: number; nil_rate: number;
}

export interface Ou05Summary {
  generated_at: string;
  scope: { markets: number; open: number; resolved: number; poll_rows: number; history_rows: number; from: string | null; to: string | null };
  bin: number;
  heartbeat_s: number;
  bands: Band[];
  tiers: Tier[];
  overround: OverroundRow[];
  distribution: { edges: number[]; series: DistSeries[] };
  leagues: LeagueRow[];
  volume_relation: { basis?: string; markets: number; rows: VolumeRow[]; spearman_volume?: number | null; spearman_liquidity?: number | null };
  calibration: CalibRow[];
  quality: Record<string, { events_7d: number; last_at: string | null }>;
  notes: string[];
}

export interface Ou05MarketMeta {
  condition_id: string; league: string | null; title: string; major: boolean;
  created_at: string | null; game_start: string | null; closed_at: string | null; closed: boolean;
  resolved_over: boolean | null; final_score: string | null; volume: number | null; liquidity: number | null;
}

export interface Ou05IndexEntry extends Ou05MarketMeta { rows: number; poll_rows: number; from: string | null; to: string | null }
export interface Ou05Index { generated_at: string; max_points: number; markets: Ou05IndexEntry[] }

// [at, over_bid, over_ask, under_bid, under_ask, sum_ask_max, over_mid, src]
export type Ou05Point = [string, number | null, number | null, number | null, number | null, number | null, number | null, "p" | "h"];
export interface Ou05Market extends Ou05MarketMeta {
  goals: { at: string; scorer: string; home_score: number; away_score: number }[];
  columns: string[];
  points: Ou05Point[];
}

export const PHASE_LABEL: Record<string, string> = { pre: "킥오프 전 전체", last24h: "킥오프 전 24시간", inplay: "경기 중" };
