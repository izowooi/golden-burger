// Types for latest/explore/* (docs/contracts/dashboard-json.md, "latest/explore" sections).

export const EXPLORE_SPORTS = ["soccer", "mlb", "nba", "nfl", "nhl"] as const;
export type ExploreSport = (typeof EXPLORE_SPORTS)[number];
export const SPORT_LABEL: Record<ExploreSport, string> = {
  soccer: "축구 (주요 리그)", mlb: "MLB", nba: "NBA", nfl: "NFL", nhl: "NHL",
};
export const GAME_KEY = /^[A-Za-z0-9_-]{1,64}$/;

export const PHASE_LABEL: Record<string, string> = {
  pre: "경기 전", early: "초반 <30%", mid: "중반 30–60%", late: "후반 60–85%", final: "막판 ≥85%",
};

export interface TimeCol { key: string; label: string; lo: number | null; hi: number | null }

export interface CalibCell {
  col?: string;
  b?: number;
  p_lo: number;
  p_hi: number;
  n: number;
  games: number;
  mean_price: number;
  win_rate: number;
  gap: number;
  ci_lo: number;
  ci_hi: number;
}

export interface TrajPoint { x: number; n: number; mean: number; p25: number; p50: number; p75: number }
export interface TrajSeries { key: string; label: string; tokens: number; points: TrajPoint[] }

export interface SwingRow {
  col: string;
  window: "1m" | "10m";
  n: number;
  counts: number[];
  p50: number;
  p90: number;
  p99: number;
  share_ge_5: number;
  share_ge_10: number;
}

export interface ExploreSportData {
  sport: ExploreSport;
  generated_at: string;
  scope: {
    games: number; tokens: number; bars: number; from: string | null; to: string | null;
    leagues: string[] | null; excluded_games: Record<string, number>;
  };
  min_n: number;
  heat_step: number;
  rel_step: number;
  time_cols: TimeCol[];
  phases: string[];
  swing_edges: number[];
  heatmap: (CalibCell & { col: string; b: number })[];
  reliability: { phase: string; points: CalibCell[] }[];
  trajectory: { grid: number[]; min_n: number; series: TrajSeries[] };
  swings: SwingRow[];
  notes: string[];
}

export interface GameIndexEntry {
  game_key: string;
  sport: string;
  league: string | null;
  title: string | null;
  start_time: string | null;
  ended_at: string | null;
  status: string | null;
  home_score: number | null;
  away_score: number | null;
  outcomes: number;
  score_events: number;
  positions: number;
}

export interface GamesIndex { generated_at: string; window_days: number; max_points: number; games: GameIndexEntry[] }

export interface GameOutcome { side: string; label: string | null; token_id: string; won: boolean | null; points: [string, number][] }
export interface ScoreEvent { at: string; scorer: string; points: number; home_score: number; away_score: number; game_minute: number | null }
export interface GamePosition {
  variant_id: string; mode: string; side: string | null; outcome: string | null; status: string;
  opened_at: string | null; entry_price: number | null; shares: number | null; stake_usdc: number | null;
  closed_at: string | null; exit_price: number | null; exit_reason: string | null; realized_pnl: number | null;
}
export interface GameDetail extends Omit<GameIndexEntry, "outcomes" | "score_events" | "positions"> {
  home_team: string | null;
  away_team: string | null;
  outcomes: GameOutcome[];
  score_events: ScoreEvent[];
  positions: GamePosition[];
}
