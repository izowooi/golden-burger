// /games: per-outcome price-path sparklines. Server-rendered SVG, fixed 0–1 y-domain and one x-domain per game
// so the outcomes of a game line up (score-change ticks sit at the same x on every row).
import { SIDE_COLOR } from "@/components/explore-charts";
import type { Game24h, Game24hOutcome } from "@/lib/types";

const hmFmt = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false });
const hm = (t: number) => hmFmt.format(new Date(t));
const ms = (iso: string | null | undefined) => {
  const t = iso ? new Date(iso).getTime() : NaN;
  return Number.isFinite(t) ? t : null;
};

export function gameDomain(g: Game24h): [number, number] | null {
  const ts = (g.outcomes ?? []).flatMap((o) => (o.path ?? []).map(([at]) => ms(at)).filter((t): t is number => t !== null));
  const start = ms(g.start_time);
  if (start !== null) ts.push(start);
  if (!ts.length) return null;
  const lo = Math.min(...ts), hi = Math.max(...ts);
  return hi > lo ? [lo, hi] : null;
}

export function Sparkline({ g, o, domain }: { g: Game24h; o: Game24hOutcome; domain: [number, number] | null }) {
  const W = 180, H = 36, pad = 3;
  const pts = (o.path ?? []).map(([at, p]) => ({ t: ms(at), p })).filter((q): q is { t: number; p: number } => q.t !== null && Number.isFinite(q.p));
  if (!domain || !pts.length) return <span className="muted spark-empty">경로 없음</span>;
  const [t0, t1] = domain;
  const x = (t: number) => pad + ((t - t0) / (t1 - t0)) * (W - 2 * pad);
  const y = (v: number) => pad + (1 - v) * (H - 2 * pad);
  const color = SIDE_COLOR[o.side] ?? "var(--muted)";
  const start = ms(g.start_time), end = ms(g.end_of_play);
  const last = pts[pts.length - 1];
  const d = pts.length === 1
    ? `M${(x(last.t) - 2).toFixed(1)},${y(last.p).toFixed(1)}h4`
    : pts.map((q, i) => `${i ? "L" : "M"}${x(q.t).toFixed(1)},${y(q.p).toFixed(1)}`).join("");
  const inRange = (t: number | null): t is number => t !== null && t >= t0 && t <= t1;
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img"
      aria-label={`${o.label ?? o.side} 가격 경로 ${hm(pts[0].t)}–${hm(last.t)} KST`}>
      <line className="spark-mid" x1={pad} x2={W - pad} y1={y(0.5)} y2={y(0.5)} />
      {inRange(start) && <line className="spark-kick" x1={x(start)} x2={x(start)} y1={0} y2={H} />}
      {end !== start && inRange(end) && g.end_source !== "running" && <line className="spark-kick" x1={x(end)} x2={x(end)} y1={0} y2={H} />}
      {(g.events ?? []).map((e, i) => {
        const t = ms(e.at);
        if (!inRange(t)) return null;
        return (
          <g key={i}>
            <line x1={x(t)} x2={x(t)} y1={0} y2={H} stroke={SIDE_COLOR[e.scorer] ?? "var(--muted)"} strokeWidth={1.5} opacity={0.55} />
            <rect className="hit" x={x(t) - 4} y={0} width={8} height={H}>
              <title>{`${hm(t)} KST · ${e.scorer === "home" ? g.home_team ?? "홈" : e.scorer === "away" ? g.away_team ?? "원정" : e.scorer} 득점 → ${e.home_score ?? "?"}–${e.away_score ?? "?"}${e.game_minute !== null ? ` · ${e.game_minute}′` : ""}`}</title>
            </rect>
          </g>
        );
      })}
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last.t)} cy={y(last.p)} r={2.2} fill={color} />
      <title>{`${o.label ?? o.side}: ${hm(pts[0].t)} ${pts[0].p.toFixed(2)} → ${hm(last.t)} ${last.p.toFixed(2)} KST (세로 실선 = 득점, 점선 = 시작/종료)`}</title>
    </svg>
  );
}
