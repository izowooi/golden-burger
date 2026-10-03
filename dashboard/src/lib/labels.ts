// Korean labels shared by pages. EXIT_KO mirrors src/polylab/reports/render.py so the dashboard and reports agree.
export const EXIT_KO: Record<string, string> = {
  take_profit: "익절(TP)",
  stop_loss: "손절(SL)",
  time_exit: "시간청산",
  resolution_win: "정산 승",
  resolution_loss: "정산 패",
  manual: "수동",
  hold: "보유",
};

export function exitLabel(reason: string | null | undefined) {
  if (!reason) return "—";
  return EXIT_KO[reason] ?? reason;
}

/** Families that belong to the 0:0-avoidance study's automated track (docs/research/llm-forecast-study.md). */
const TRACK1_FAMILIES = new Set(["goal_over", "llm_nil"]);

export function isTrack1(family: string | null | undefined) {
  return !!family && TRACK1_FAMILIES.has(family);
}

function pctText(v: number) {
  return `${Number((v * 100).toFixed(2))}%`;
}

/** goal_over-style exit rule from params (relative TP/SL + hold-above). null when none of those params is set. */
export function exitRuleSummary(params: Record<string, unknown> | null | undefined): string | null {
  if (!params) return null;
  const n = (k: string) => (typeof params[k] === "number" && Number.isFinite(params[k]) ? (params[k] as number) : null);
  const parts: string[] = [];
  const tp = n("take_profit_pct"), sl = n("stop_loss_pct"), hold = n("hold_above_price");
  if (tp !== null) parts.push(`익절 +${pctText(tp)}`);
  if (sl !== null) parts.push(`손절 −${pctText(Math.abs(sl))}`);
  if (hold !== null) parts.push(`${hold} 이상 보유`);
  return parts.length ? parts.join(" · ") : null;
}

export type ManualResultTone = "win" | "loss" | "sold" | "open" | "other";

export const MANUAL_RESULT: Record<string, { label: string; tone: ManualResultTone }> = {
  resolved_win: { label: "승", tone: "win" },
  redeemed: { label: "승", tone: "win" },
  resolved_loss: { label: "패", tone: "loss" },
  closed_sell: { label: "매도", tone: "sold" },
  open: { label: "보유", tone: "open" },
  resolved_split: { label: "분할 정산", tone: "other" },
  quarantined: { label: "격리", tone: "other" },
};
