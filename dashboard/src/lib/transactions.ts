import type { Transaction } from "@/lib/types";

// Fill statuses that count as executed volume. MATCHED/MINED are on their way but not confirmed,
// so they are counted separately instead of being mixed into confirmed volume.
const CONFIRMED = new Set(["CONFIRMED", "PAPER"]);
const PENDING = new Set(["MATCHED", "MINED"]);

export type TxClass = "confirmed" | "pending" | "failed" | "quarantined" | "resolve" | "other";

export function txClass(t: Transaction): TxClass {
  const s = (t.status ?? "").toUpperCase();
  if (t.side === "RESOLVE") return "resolve";
  if (CONFIRMED.has(s)) return "confirmed";
  if (PENDING.has(s)) return "pending";
  if (s === "QUARANTINED") return "quarantined";
  if (s === "FAILED" || s === "UNFILLED") return "failed";
  return "other";
}

export function positionKey(t: Transaction) {
  return t.position_id ?? `${t.variant_id}|${t.game_title ?? ""}|${t.outcome ?? ""}`;
}

export function gameKey(t: Transaction) {
  return `${t.sport ?? ""}|${t.league ?? ""}|${t.game_title ?? "(경기 미상)"}`;
}

export interface PositionSummary {
  key: string;
  outcome: string | null;
  status: string | null;
  exit_reason: string | null;
  realized_pnl: number | null;
  settled: boolean;
  buyUsdc: number;
  filled: boolean;
}

export interface Totals {
  rows: number;
  confirmed: number;
  pending: number;
  failed: number;
  quarantined: number;
  buyUsdc: number;
  sellUsdc: number;
  resolveUsdc: number;
  fees: number;
  feesUnknown: number;
  realized: number | null;
  settled: number;
  wins: number;
  losses: number;
  openPositions: number;
  openCost: number;
  games: number;
}

export function positions(rows: Transaction[]): PositionSummary[] {
  const m = new Map<string, PositionSummary>();
  // rows arrive newest first, so the first row seen carries the latest position state
  for (const t of rows) {
    const k = positionKey(t);
    let p = m.get(k);
    if (!p) {
      p = { key: k, outcome: t.outcome, status: t.position_status, exit_reason: t.exit_reason, realized_pnl: t.realized_pnl, settled: t.realized_pnl !== null, buyUsdc: 0, filled: false };
      m.set(k, p);
    }
    if (p.realized_pnl === null && t.realized_pnl !== null) {
      p.realized_pnl = t.realized_pnl;
      p.settled = true;
    }
    const c = txClass(t);
    if (c === "confirmed" || c === "pending" || c === "resolve") p.filled = true;
    if (t.side === "BUY" && c === "confirmed" && t.usdc !== null) p.buyUsdc += t.usdc;
  }
  return [...m.values()];
}

export function totals(rows: Transaction[]): Totals {
  const t: Totals = {
    rows: rows.length, confirmed: 0, pending: 0, failed: 0, quarantined: 0, buyUsdc: 0, sellUsdc: 0, resolveUsdc: 0,
    fees: 0, feesUnknown: 0, realized: null, settled: 0, wins: 0, losses: 0, openPositions: 0, openCost: 0,
    games: new Set(rows.map(gameKey)).size,
  };
  for (const r of rows) {
    const c = txClass(r);
    if (c === "confirmed") {
      t.confirmed++;
      if (r.side === "BUY") t.buyUsdc += r.usdc ?? 0;
      if (r.side === "SELL") t.sellUsdc += r.usdc ?? 0;
      if (r.fee_usdc === null) t.feesUnknown++;
      else t.fees += r.fee_usdc;
    } else if (c === "resolve") {
      t.resolveUsdc += r.usdc ?? 0;
    } else if (c === "pending") t.pending++;
    else if (c === "failed") t.failed++;
    else if (c === "quarantined") t.quarantined++;
  }
  for (const p of positions(rows)) {
    if (p.settled) {
      t.realized = (t.realized ?? 0) + (p.realized_pnl ?? 0);
      t.settled++;
      if ((p.realized_pnl ?? 0) > 0) t.wins++;
      else if ((p.realized_pnl ?? 0) < 0) t.losses++;
    } else if (p.buyUsdc > 0 && p.status !== "closed") {
      t.openPositions++;
      t.openCost += p.buyUsdc;
    }
  }
  return t;
}

export interface VariantGroup {
  variant_id: string;
  mode: string | null;
  account: string | null;
  rows: Transaction[];
}

export interface GameGroup {
  key: string;
  title: string;
  sport: string | null;
  league: string | null;
  latest: string | null;
  variants: VariantGroup[];
}

export function groupByGame(rows: Transaction[]): GameGroup[] {
  const games = new Map<string, GameGroup>();
  for (const r of rows) {
    const k = gameKey(r);
    let g = games.get(k);
    if (!g) {
      g = { key: k, title: r.game_title ?? "(경기 미상)", sport: r.sport, league: r.league, latest: r.at, variants: [] };
      games.set(k, g);
    }
    if ((r.at ?? "") > (g.latest ?? "")) g.latest = r.at;
    let v = g.variants.find((x) => x.variant_id === r.variant_id);
    if (!v) {
      v = { variant_id: r.variant_id, mode: r.mode, account: r.account, rows: [] };
      g.variants.push(v);
    }
    v.rows.push(r);
  }
  return [...games.values()].sort((a, b) => (b.latest ?? "").localeCompare(a.latest ?? ""));
}

export function byVariant(rows: Transaction[]) {
  const m = new Map<string, Transaction[]>();
  for (const r of rows) m.set(r.variant_id, [...(m.get(r.variant_id) ?? []), r]);
  return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

export function splitByMode(rows: Transaction[]) {
  return {
    live: rows.filter((r) => r.mode === "live"),
    paper: rows.filter((r) => r.mode !== "live"),
  };
}
