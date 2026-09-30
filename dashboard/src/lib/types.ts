// Mirrors docs/contracts/dashboard-json.md. Unknown values are null, never 0.
export type Num = number | null;
export type Iso = string | null;

export type JobStatus = "ok" | "failing" | "stale" | string;
export type Mode = "live" | "paper" | "off" | string;

export interface Job {
  name: string;
  last_run_at: Iso;
  last_ok_at: Iso;
  status: JobStatus;
  detail?: string | null;
}

export interface Collector {
  last_poll_at: Iso;
  live_games: Num;
  tracked_markets: Num;
  ws_last_message_at: Iso;
  core_db_mb: Num;
  books_db_mb: Num;
  disk_free_gb: Num;
  backfill_progress?: { games_done: Num; games_total: Num } | null;
}

export interface AiStatus {
  last_retro_at: Iso;
  last_retro_kind: string | null;
  last_retro_ok: boolean | null;
  proposals_applied_7d: Num;
}

export interface Account {
  alias: string;
  variant_id: string | null;
  cash_usdc: Num;
  positions_value_usdc: Num;
  equity_usdc: Num;
  redeemable_usdc: Num;
  updated_at: Iso;
}

export interface Ladder {
  status: "hold" | "promote_ready" | "demote_warning" | string;
  trades_at_tier: Num;
  needed: Num;
  roi_ci_lo: Num;
}

export interface Pnl {
  today: Num;
  d7: Num;
  d30: Num;
  all: Num;
}

export interface StrategySummary {
  id: string;
  family: string | null;
  hypothesis: string | null;
  mode: Mode;
  account: string | null;
  sports: string[] | null;
  stake_usdc: Num;
  next_stake_usdc: Num;
  ladder: Ladder | null;
  params: Record<string, unknown> | null;
  open_positions: Num;
  open_cost_usdc: Num;
  pnl: Pnl | null;
  /** Which ledger pnl/trades/win_rate/roi come from. */
  pnl_mode?: "live" | "paper" | string | null;
  trades: { all: Num; wins: Num; losses: Num } | null;
  win_rate: Num;
  roi: Num;
  last_trade_at: Iso;
  last_change: { at: Iso; summary: string | null } | null;
}

export interface Alert {
  level: "warn" | "error" | string;
  at: Iso;
  message: string;
}

export interface Overview {
  generated_at: Iso;
  git_commit: string | null;
  system: { jobs: Job[] | null; collector: Collector | null; ai: AiStatus | null } | null;
  portfolio: {
    total_equity_usdc: Num;
    total_cash_usdc: Num;
    total_positions_value_usdc: Num;
    accounts: Account[] | null;
  } | null;
  strategies: StrategySummary[] | null;
  alerts: Alert[] | null;
}

export interface ParamVersion {
  version: number;
  at: Iso;
  params: Record<string, unknown> | null;
  stake_usdc: Num;
  mode: Mode | null;
  author: string | null;
  rationale: string | null;
}

export interface StakeEvent {
  at: Iso;
  from_usdc: Num;
  to_usdc: Num;
  reason: string | null;
  from_mode?: Mode | null;
  to_mode?: Mode | null;
  evidence?: Record<string, unknown> | null;
}

export interface OpenPosition {
  opened_at: Iso;
  sport: string | null;
  league: string | null;
  title: string | null;
  outcome: string | null;
  entry_price: Num;
  shares: Num;
  cost_usdc: Num;
  mark_price: Num;
  unrealized_pnl: Num;
  game_minute: Num;
  status?: string | null;
  mode?: Mode | null;
}

export interface ClosedPosition {
  opened_at: Iso;
  closed_at: Iso;
  sport: string | null;
  title: string | null;
  outcome: string | null;
  entry_price: Num;
  exit_price: Num;
  exit_reason: string | null;
  realized_pnl: Num;
  stake_usdc: Num;
}

export interface BreakdownRow {
  key: string;
  n: Num;
  pnl: Num;
  win_rate?: Num;
  roi?: Num;
}

export interface StrategyDetail {
  id: string;
  generated_at: Iso;
  variant: Record<string, unknown> | null;
  param_history: ParamVersion[] | null;
  stake_events: StakeEvent[] | null;
  equity_curve: { at: string; cum_pnl: number | null }[] | null;
  equity_mode?: "live" | "paper" | string | null;
  open_positions: OpenPosition[] | null;
  recent_positions: ClosedPosition[] | null;
  breakdown: {
    by_sport: BreakdownRow[] | null;
    by_entry_minute: BreakdownRow[] | null;
    by_stake: BreakdownRow[] | null;
    by_day?: BreakdownRow[] | null;
  } | null;
}

export interface CalibrationBucket {
  p_lo: number;
  p_hi: number;
  n: Num;
  mean_price: Num;
  win_rate: Num;
  ci_lo: Num;
  ci_hi: Num;
  gap: Num;
}

export interface Research {
  generated_at: Iso;
  dataset: {
    games: Num;
    markets: Num;
    price_bars: Num;
    from: Iso;
    to: Iso;
    by_sport: { sport: string; games: Num }[] | null;
  } | null;
  calibration: { sport: string; phase: string; buckets: CalibrationBucket[] }[] | null;
  brier: { sport: string; phase: string; n: Num; brier: Num }[] | null;
  event_sensitivity: {
    sport: string;
    event: string;
    minute_bucket: string;
    n: Num;
    n_isolated?: Num;
    mean_abs_jump: Num;
    median_jump: Num;
    mean_jump?: Num;
    mean_pre_price?: Num;
    reversion_1m?: Num;
    reversion_5m: Num;
    reversion_10m: Num;
    mean_peak_minute?: Num;
  }[] | null;
  event_by_score_state?: ScoreStateRow[] | null;
  analysis_generated_at?: { calibration: Iso; events: Iso } | null;
  stake_tiers: {
    tier_usdc: Num;
    variants: Num;
    trades: Num;
    roi: Num;
    pnl_std: Num;
    max_drawdown: Num;
    sharpe_like: Num;
  }[] | null;
  notes: string[] | null;
}

export interface ReportEntry {
  kind: string;
  date: string;
  slot: string | null;
  title: string;
  path: string;
  ai: boolean | null;
  engine?: "claude" | "codex" | string | null;
  generated_at?: Iso;
}

export type TxSide = "BUY" | "SELL" | "RESOLVE" | string;

export interface Transaction {
  at: Iso;
  variant_id: string;
  account: string | null;
  mode: Mode | null;
  sport: string | null;
  league: string | null;
  game_title: string | null;
  outcome: string | null;
  side: TxSide;
  price: Num;
  shares: Num;
  usdc: Num;
  fee_usdc: Num;
  status: string | null;
  position_id: string | null;
  position_status: string | null;
  exit_reason: string | null;
  realized_pnl: Num;
}

export interface ScoreStateRow {
  sport: string;
  event: string;
  minute_bucket: string;
  score_state: "trailing" | "level" | "leading" | string;
  n: Num;
  n_isolated?: Num;
  mean_abs_jump: Num;
  median_jump?: Num;
  mean_jump?: Num;
  mean_pre_price?: Num;
  reversion_1m?: Num;
  reversion_5m: Num;
  reversion_10m: Num;
  mean_peak_minute?: Num;
}

export type AttentionSeverity = "critical" | "warn" | "decide" | "info" | string;

export interface AttentionItem {
  id: string;
  created_at: Iso;
  updated_at: Iso;
  severity: AttentionSeverity;
  category: string | null;
  title: string | null;
  detail: string | null;
  evidence_ref: string | null;
  source: "rule" | "ai" | string | null;
  status: "open" | "resolved" | string;
  resolved_at: Iso;
  resolution: string | null;
}

export interface Attention {
  generated_at: Iso;
  url: string | null;
  open: AttentionItem[] | null;
  resolved: AttentionItem[] | null;
}

export interface GameSwing {
  delta: Num;
  from_price: Num;
  to_price: Num;
  at: Iso;
  from_at: Iso;
  game_minute: Num;
  period: string | null;
  elapsed_min: Num;
  sources: string | null;
}

export interface Game24hOutcome {
  side: "home" | "draw" | "away" | string;
  label: string | null;
  token_id: string | null;
  condition_id?: string | null;
  volume_usd: Num;
  pre_price: Num;
  min_price: Num;
  max_price: Num;
  final_price: Num;
  won: boolean | null;
  in_play_bars: Num;
  swing_1m: GameSwing | null;
  swing_10m: GameSwing | null;
  path: [string, number][] | null;
}

export interface Game24hEvent {
  at: string;
  game_minute: Num;
  minute_source: string | null;
  scorer: "home" | "away" | string;
  home_score: Num;
  away_score: Num;
}

export interface Game24h {
  game_key: string;
  sport: string;
  league: string | null;
  title: string | null;
  home_team: string | null;
  away_team: string | null;
  start_time: Iso;
  end_of_play: Iso;
  end_source: "ended_at" | "game_state" | "nominal" | "running" | string | null;
  status: string | null;
  home_score: Num;
  away_score: Num;
  resolved: boolean | null;
  result: "home" | "draw" | "away" | string | null;
  volume_usd: Num;
  traded: boolean | null;
  favourite: string | null;
  favourite_pre_price: Num;
  upset: boolean | null;
  notable_swing: boolean | null;
  max_swing_10m: Num;
  outcomes: Game24hOutcome[] | null;
  events: Game24hEvent[] | null;
}

export interface Games24hSportSummary {
  sport: string;
  games: Num;
  resolved: Num;
  favourites_resolved: Num;
  favourite_win_rate: Num;
  favourite_avg_pre_price: Num;
  avg_max_swing_10m: Num;
  upsets: Num;
  notable_swings: Num;
}

export interface Games24h {
  generated_at: Iso;
  window: { since: Iso; until: Iso } | null;
  scope?: { sports?: string[] | null; soccer_leagues?: string[] | null; note?: string | null } | null;
  notable_swing?: Num;
  excluded_out_of_scope?: Record<string, number> | null;
  summary_by_sport?: Games24hSportSummary[] | null;
  games: Game24h[] | null;
  error?: string | null;
}
