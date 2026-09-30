// Server-rendered SVG charts for /explore. Same conventions as charts.tsx: CSS tokens for light/dark,
// native <title> tooltips on enlarged hit targets, no client JS.
import Link from "next/link";

import { ticks } from "@/components/charts";
import { num, pct } from "@/lib/format";
import type { CalibCell, ExploreSportData, GameDetail, SwingRow, TrajSeries } from "@/lib/explore";
import { PHASE_LABEL } from "@/lib/explore";

const GAP_SCALE = 0.15; // |gap| at full diverging saturation, fixed so sports compare
const pp = (v: number, d = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}pp`;
const p2 = (v: number) => v.toFixed(2);
const significant = (c: CalibCell) => c.ci_lo > c.mean_price || c.ci_hi < c.mean_price;

function gapFill(gap: number) {
  const t = Math.round(Math.min(1, Math.abs(gap) / GAP_SCALE) * 100);
  return `color-mix(in oklab, var(${gap >= 0 ? "--div-pos" : "--div-neg"}) ${t}%, var(--div-mid))`;
}

/* ------------------------------ 1. Heatmap ------------------------------- */

export function GapLegend({ minN }: { minN: number }) {
  return (
    <div className="legend explore-legend">
      <span className="div-scale">
        <span className="muted">과대평가 (가격 &gt; 실현)</span>
        <i className="div-bar" />
        <span className="muted">과소평가 (실현 &gt; 가격)</span>
      </span>
      <span className="muted">색 포화 = ±{GAP_SCALE * 100}pp</span>
      <span><i className="swatch-thin" />n &lt; {minN} (표본 부족)</span>
      <span><i className="swatch-dot" />95% CI가 평균 가격을 벗어남</span>
    </div>
  );
}

export function CalibrationHeatmap({ d }: { d: ExploreSportData }) {
  const cols = d.time_cols;
  const nb = Math.round(1 / d.heat_step);
  const W = 780, m = { t: 12, r: 8, b: 44, l: 86 };
  const cw = (W - m.l - m.r) / cols.length, ch = 17;
  const H = m.t + ch * nb + m.b;
  const colIdx = new Map(cols.map((c, i) => [c.key, i]));
  const x = (i: number) => m.l + i * cw;
  const y = (b: number) => m.t + (nb - 1 - b) * ch; // high prices on top
  return (
    <div className="table-wrap">
      <svg className="chart heat" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="가격 구간 × 경기 진행률 보정 gap 히트맵">
        {Array.from({ length: nb }, (_, b) => b).filter((b) => b % 2 === 0).map((b) => (
          <text key={b} x={m.l - 6} y={y(b) + ch / 2} dy="0.32em" textAnchor="end">{p2(b * d.heat_step)}–{p2((b + 1) * d.heat_step)}</text>
        ))}
        {cols.map((c, i) => (
          <text key={c.key} x={x(i) + cw / 2} y={m.t + ch * nb + 16} textAnchor="middle">{c.label}</text>
        ))}
        <text x={m.l + (W - m.l - m.r) / 2} y={H - 6} textAnchor="middle">경기 진행률 (정규 시간 대비)</text>
        <text transform={`translate(14 ${m.t + (ch * nb) / 2}) rotate(-90)`} textAnchor="middle">시장가격 구간</text>
        {d.heatmap.map((c) => {
          const i = colIdx.get(c.col);
          if (i === undefined) return null;
          const low = c.n < d.min_n;
          const cx = x(i) + 1, cy = y(c.b) + 1, w = cw - 2, h = ch - 2;
          return (
            <g key={`${c.col}-${c.b}`} className="pt">
              <rect className="mark" x={cx} y={cy} width={w} height={h} rx={2}
                fill={low ? "var(--surface-2)" : gapFill(c.gap)} />
              {low && <line x1={cx + 3} x2={cx + w - 3} y1={cy + h / 2} y2={cy + h / 2} stroke="var(--axis)" strokeWidth={1} />}
              {!low && significant(c) && <circle cx={cx + w / 2} cy={cy + h / 2} r={2.2} fill="var(--ink)" opacity={0.7} />}
              <rect className="hit" x={cx} y={cy} width={w} height={h}>
                <title>{`${cols[i].label} · 가격 ${p2(c.p_lo)}–${p2(c.p_hi)}\n평균 가격 ${c.mean_price.toFixed(3)} → 실현 승률 ${pct(c.win_rate)} [${pct(c.ci_lo)}, ${pct(c.ci_hi)}]\ngap ${pp(c.gap)} · n=${num(c.n)} (경기 ${num(c.games)})${low ? "\n표본 부족" : ""}`}</title>
              </rect>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function TopGapTable({ d, limit = 12 }: { d: ExploreSportData; limit?: number }) {
  const label = new Map(d.time_cols.map((c) => [c.key, c.label]));
  const rows = d.heatmap.filter((c) => c.n >= d.min_n).sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap)).slice(0, limit);
  if (!rows.length) return <p className="muted">n ≥ {d.min_n} 셀 없음</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>진행률</th><th>가격</th><th className="n">평균 가격</th><th className="n">실현 승률</th><th className="n">95% CI</th><th className="n">gap</th><th className="n">n</th><th className="n">경기</th></tr></thead>
        <tbody>
          {rows.map((c) => (
            <tr key={`${c.col}-${c.b}`}>
              <td>{label.get(c.col)}</td><td>{p2(c.p_lo)}–{p2(c.p_hi)}</td>
              <td className="n">{c.mean_price.toFixed(3)}</td><td className="n">{pct(c.win_rate)}</td>
              <td className="n">{pct(c.ci_lo)}–{pct(c.ci_hi)}</td>
              <td className={`n ${c.gap > 0 ? "pos" : "neg"}`}>{pp(c.gap)}{significant(c) ? " *" : ""}</td>
              <td className="n">{num(c.n)}</td><td className="n">{num(c.games)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------- 2. Reliability ----------------------------- */

export const PHASE_COLOR: Record<string, string> = {
  pre: "var(--s1)", early: "var(--s2)", mid: "var(--s3)", late: "var(--s4)", final: "var(--s5)",
};

export function PhaseLegend({ phases, selected, href }: { phases: string[]; selected: string; href: (p: string) => string }) {
  return (
    <div className="legend chips" role="group" aria-label="단계 선택">
      {phases.map((p) => (
        <Link key={p} href={href(p)} scroll={false} className={p === selected ? "chip on" : "chip"} aria-current={p === selected ? "true" : undefined}>
          <i className="line" style={{ background: PHASE_COLOR[p] ?? "var(--muted)" }} />{PHASE_LABEL[p] ?? p}
        </Link>
      ))}
    </div>
  );
}

export function ReliabilityCurves({ d, selected }: { d: ExploreSportData; selected: string }) {
  const W = 560, H = 440, m = { t: 12, r: 16, b: 42, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (v: number) => m.l + v * iw;
  const y = (v: number) => m.t + ih - v * ih;
  const tk = ticks(0, 1, 5);
  const ordered = [...d.reliability].sort((a, b) => (a.phase === selected ? 1 : 0) - (b.phase === selected ? 1 : 0));
  return (
    <svg className="chart rel mid" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="단계별 가격 대비 실현 승률 신뢰도 곡선">
      {tk.map((v) => (
        <g key={v}>
          <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <line className="grid-line" x1={x(v)} x2={x(v)} y1={m.t} y2={m.t + ih} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{v.toFixed(1)}</text>
          <text x={x(v)} y={m.t + ih + 15} textAnchor="middle">{v.toFixed(1)}</text>
        </g>
      ))}
      <line className="ref" x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} />
      <text x={x(0.97)} y={y(0.97) + 14} textAnchor="end">가격 = 승률</text>
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">시장가격 (구간 평균)</text>
      <text transform={`translate(12 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">실현 승률</text>
      {ordered.map(({ phase, points }) => {
        const on = phase === selected;
        const color = PHASE_COLOR[phase] ?? "var(--muted)";
        const ok = points.filter((p) => p.n >= d.min_n).sort((a, b) => a.mean_price - b.mean_price);
        const line = ok.map((p, i) => `${i ? "L" : "M"}${x(p.mean_price).toFixed(1)},${y(p.win_rate).toFixed(1)}`).join("");
        const band = ok.length > 1
          ? ok.map((p, i) => `${i ? "L" : "M"}${x(p.mean_price).toFixed(1)},${y(p.ci_hi).toFixed(1)}`).join("")
            + [...ok].reverse().map((p) => `L${x(p.mean_price).toFixed(1)},${y(p.ci_lo).toFixed(1)}`).join("") + "Z"
          : "";
        return (
          <g key={phase} opacity={on ? 1 : 0.35}>
            {on && band && <path d={band} fill={color} opacity={0.16} />}
            {line && <path d={line} fill="none" stroke={color} strokeWidth={on ? 2 : 1.25} strokeLinejoin="round" />}
            {on && points.map((p) => {
              const low = p.n < d.min_n;
              return (
                <g key={p.p_lo} className="pt">
                  <circle className="mark" cx={x(p.mean_price)} cy={y(p.win_rate)} r={4}
                    fill={low ? "var(--surface)" : color} stroke={low ? color : "var(--surface)"} strokeWidth={low ? 1.5 : 2} />
                  <circle className="hit" cx={x(p.mean_price)} cy={y(p.win_rate)} r={12}>
                    <title>{`${PHASE_LABEL[phase] ?? phase} · 가격 ${p2(p.p_lo)}–${p2(p.p_hi)}\n평균 가격 ${p.mean_price.toFixed(3)} → 실현 ${pct(p.win_rate)} [${pct(p.ci_lo)}, ${pct(p.ci_hi)}]\ngap ${pp(p.gap)} · n=${num(p.n)} (경기 ${num(p.games)})${low ? "\n표본 부족 (선에서 제외)" : ""}`}</title>
                  </circle>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

export function ReliabilityTable({ d, phase }: { d: ExploreSportData; phase: string }) {
  const pts = d.reliability.find((r) => r.phase === phase)?.points ?? [];
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>가격</th><th className="n">평균 가격</th><th className="n">실현 승률</th><th className="n">95% CI</th><th className="n">gap</th><th className="n">n</th><th className="n">경기</th></tr></thead>
        <tbody>
          {pts.map((p) => (
            <tr key={p.p_lo} className={p.n < d.min_n ? "muted" : ""}>
              <td>{p2(p.p_lo)}–{p2(p.p_hi)}</td><td className="n">{p.mean_price.toFixed(3)}</td><td className="n">{pct(p.win_rate)}</td>
              <td className="n">{pct(p.ci_lo)}–{pct(p.ci_hi)}</td><td className="n">{pp(p.gap)}</td><td className="n">{num(p.n)}</td><td className="n">{num(p.games)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ----------------------------- 3. Trajectory ----------------------------- */

export interface TrajStyle { key: string; color: string; dash?: string; band?: boolean; label?: string }

export const TRAJ_WL: TrajStyle[] = [
  { key: "winner", color: "var(--s1)", band: true },
  { key: "loser", color: "var(--s2)", band: true },
];
export const TRAJ_FAV: TrajStyle[] = [
  { key: "fav_won", color: "var(--s1)" },
  { key: "dog_won", color: "var(--s1)", dash: "5 4" },
  { key: "fav_lost", color: "var(--s2)" },
  { key: "dog_lost", color: "var(--s2)", dash: "5 4" },
];

export function TrajLegend({ series, styles }: { series: TrajSeries[]; styles: TrajStyle[] }) {
  const byKey = new Map(series.map((s) => [s.key, s]));
  return (
    <div className="legend">
      {styles.map((st) => {
        const s = byKey.get(st.key);
        return (
          <span key={st.key}>
            <svg width={22} height={10} aria-hidden="true"><line x1={1} x2={21} y1={5} y2={5} stroke={st.color} strokeWidth={2} strokeDasharray={st.dash} /></svg>
            {s?.label ?? st.key} <span className="muted">({num(s?.tokens)})</span>
          </span>
        );
      })}
    </div>
  );
}

export function TrajectoryChart({ series, styles, minN, label }: { series: TrajSeries[]; styles: TrajStyle[]; minN: number; label: string }) {
  const byKey = new Map(series.map((s) => [s.key, s]));
  const shown = styles.map((st) => ({ st, s: byKey.get(st.key) })).filter((v): v is { st: TrajStyle; s: TrajSeries } => !!v.s && v.s.points.length > 0);
  if (!shown.length) return <p className="muted">데이터 없음</p>;
  const W = 560, H = 330, m = { t: 22, r: 16, b: 40, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const X0 = -0.2, X1 = 1.2;
  const x = (v: number) => m.l + ((v - X0) / (X1 - X0)) * iw;
  const y = (v: number) => m.t + ih - v * ih;
  const segs = (pts: TrajSeries["points"], val: (p: TrajSeries["points"][number]) => number) => {
    let d = "", prev: number | null = null;
    for (const p of pts) {
      if (p.n < minN) { prev = null; continue; }
      d += `${prev !== null && Math.abs(p.x - prev) < 0.021 ? "L" : "M"}${x(p.x).toFixed(1)},${y(val(p)).toFixed(1)}`;
      prev = p.x;
    }
    return d;
  };
  const bandPath = (pts: TrajSeries["points"]) => {
    const ok = pts.filter((p) => p.n >= minN);
    if (ok.length < 2) return "";
    return ok.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.p75).toFixed(1)}`).join("")
      + [...ok].reverse().map((p) => `L${x(p.x).toFixed(1)},${y(p.p25).toFixed(1)}`).join("") + "Z";
  };
  const grid = [...new Set(shown.flatMap(({ s }) => s.points.map((p) => p.x)))].sort((a, b) => a - b);
  const step = 0.02;
  const xt = [-0.2, 0, 0.2, 0.4, 0.6, 0.8, 1, 1.2];
  return (
    <div className="table-wrap"><svg className="chart mid" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{v.toFixed(2)}</text>
        </g>
      ))}
      {xt.map((v) => <text key={v} x={x(v)} y={m.t + ih + 15} textAnchor="middle">{`${Math.round(v * 100)}%`}</text>)}
      <line className="axis-line" x1={x(0)} x2={x(0)} y1={m.t - 6} y2={m.t + ih} />
      <line className="axis-line" x1={x(1)} x2={x(1)} y1={m.t - 6} y2={m.t + ih} />
      <text x={x(0) + 4} y={m.t - 8}>경기 시작</text>
      <text x={x(1) - 4} y={m.t - 8} textAnchor="end">정규 종료</text>
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">경기 진행률 (정규 시간 대비)</text>
      <text transform={`translate(12 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">가격 (implied p)</text>
      {shown.map(({ st, s }) => st.band && <path key={`b-${st.key}`} d={bandPath(s.points)} fill={st.color} opacity={0.14} />)}
      {shown.map(({ st, s }) => (
        <path key={st.key} d={segs(s.points, (p) => p.mean)} fill="none" stroke={st.color} strokeWidth={2} strokeDasharray={st.dash} strokeLinejoin="round" />
      ))}
      {grid.map((gx) => (
        <rect key={gx} className="hit" x={x(gx - step / 2)} y={m.t} width={Math.max(1, x(step) - x(0))} height={ih}>
          <title>{`진행률 ${Math.round(gx * 100)}%\n` + shown.map(({ s }) => {
            const p = s.points.find((q) => q.x === gx);
            return p ? `${s.label}: 평균 ${p.mean.toFixed(3)} · IQR ${p.p25.toFixed(2)}–${p.p75.toFixed(2)} · n=${num(p.n)}${p.n < minN ? " (표본 부족)" : ""}` : `${s.label}: —`;
          }).join("\n")}</title>
        </rect>
      ))}
    </svg></div>
  );
}

export function TrajTable({ series, keys }: { series: TrajSeries[]; keys: string[] }) {
  const at = [-0.1, 0, 0.25, 0.5, 0.75, 0.9, 1, 1.1];
  const rows = keys.map((k) => series.find((s) => s.key === k)).filter((s): s is TrajSeries => !!s);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>계열</th>{at.map((v) => <th key={v} className="n">{Math.round(v * 100)}%</th>)}</tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.key}>
              <td>{s.label}</td>
              {at.map((v) => {
                const p = s.points.find((q) => Math.abs(q.x - v) < 1e-6);
                return <td key={v} className="n">{p ? `${p.mean.toFixed(2)} (n=${num(p.n)})` : "—"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------ 4. Swings -------------------------------- */

const edgeLabel = (v: number) => (v >= 1 ? "" : `${+(v * 100).toFixed(1)}`);

export function SwingHeat({ d, window, title }: { d: ExploreSportData; window: "1m" | "10m"; title: string }) {
  const rows = d.swings.filter((r) => r.window === window);
  const byCol = new Map(rows.map((r) => [r.col, r]));
  const edges = d.swing_edges;
  const nb = edges.length - 1;
  const cols = d.time_cols;
  const W = 560, m = { t: 22, r: 8, b: 40, l: 62 }, ch = 16;
  const cw = (W - m.l - m.r) / cols.length;
  const H = m.t + nb * ch + m.b;
  const LOG_MIN = -4; // share 0.01% .. 100% on a log scale
  const fill = (share: number) => {
    if (share <= 0) return "var(--surface-2)";
    const t = Math.max(0, Math.min(1, (Math.log10(share) - LOG_MIN) / -LOG_MIN));
    return `color-mix(in oklab, var(--seq-hi) ${Math.round(8 + t * 92)}%, var(--seq-lo))`;
  };
  return (
    <div className="table-wrap"><svg className="chart mid" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <text className="title" x={m.l} y={14}>{title}</text>
      {Array.from({ length: nb }, (_, i) => i).map((i) => (
        <text key={i} x={m.l - 6} y={m.t + (nb - 1 - i) * ch + ch / 2} dy="0.32em" textAnchor="end">
          {i === nb - 1 ? `≥${edgeLabel(edges[i])}pp` : `${edgeLabel(edges[i])}–${edgeLabel(edges[i + 1])}`}
        </text>
      ))}
      {cols.map((c, j) => (
        <text key={c.key} x={m.l + j * cw + cw / 2} y={m.t + nb * ch + 14} textAnchor="middle" className="tiny">{c.key === "pre" ? "전" : c.key === "ot" ? "100+" : `${(j - 1) * 10}`}</text>
      ))}
      <text x={m.l + (W - m.l - m.r) / 2} y={H - 4} textAnchor="middle">경기 진행률 % (열 = 10% 구간)</text>
      {cols.map((c, j) => {
        const r = byCol.get(c.key);
        if (!r) return null;
        return r.counts.map((k, i) => {
          const share = r.n ? k / r.n : 0;
          const cx = m.l + j * cw + 1, cy = m.t + (nb - 1 - i) * ch + 1;
          return (
            <g key={`${c.key}-${i}`} className="pt">
              <rect className="mark" x={cx} y={cy} width={cw - 2} height={ch - 2} rx={2} fill={fill(share)} />
              <rect className="hit" x={cx} y={cy} width={cw - 2} height={ch - 2}>
                <title>{`${c.label} · |Δp| ${i === nb - 1 ? `≥${edgeLabel(edges[i])}pp` : `${edgeLabel(edges[i])}–${edgeLabel(edges[i + 1])}pp`}\n${num(k)} / ${num(r.n)} = ${pct(share, 3)}\n(열 p50 ${pp(r.p50)} · p90 ${pp(r.p90)} · p99 ${pp(r.p99)})`}</title>
              </rect>
            </g>
          );
        });
      })}
    </svg></div>
  );
}

export function SwingLegend() {
  return (
    <div className="legend explore-legend">
      <span className="div-scale"><span className="muted">0.01%</span><i className="seq-bar" /><span className="muted">100% (열 안 비율, 로그)</span></span>
    </div>
  );
}

const TAIL_SERIES = [
  { key: "1m-5", label: "1분 |Δp| ≥ 5pp", color: "var(--s1)", dash: undefined, pick: (r: SwingRow) => r.share_ge_5, w: "1m" },
  { key: "1m-10", label: "1분 |Δp| ≥ 10pp", color: "var(--s1)", dash: "5 4", pick: (r: SwingRow) => r.share_ge_10, w: "1m" },
  { key: "10m-5", label: "10분 |Δp| ≥ 5pp", color: "var(--s2)", dash: undefined, pick: (r: SwingRow) => r.share_ge_5, w: "10m" },
  { key: "10m-10", label: "10분 |Δp| ≥ 10pp", color: "var(--s2)", dash: "5 4", pick: (r: SwingRow) => r.share_ge_10, w: "10m" },
] as const;

export function TailLegend() {
  return (
    <div className="legend">
      {TAIL_SERIES.map((s) => (
        <span key={s.key}><svg width={22} height={10} aria-hidden="true"><line x1={1} x2={21} y1={5} y2={5} stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} /></svg>{s.label}</span>
      ))}
    </div>
  );
}

export function TailChart({ d }: { d: ExploreSportData }) {
  const cols = d.time_cols;
  const W = 560, H = 300, m = { t: 14, r: 16, b: 40, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const get = (w: string, col: string) => d.swings.find((r) => r.window === w && r.col === col);
  const vals = TAIL_SERIES.flatMap((s) => cols.map((c) => { const r = get(s.w, c.key); return r ? s.pick(r) : 0; }));
  const yMax = Math.max(0.01, ...vals);
  const yt = ticks(0, yMax, 4);
  const top = yt[yt.length - 1];
  const band = iw / cols.length;
  const x = (j: number) => m.l + band * j + band / 2;
  const y = (v: number) => m.t + ih - (v / top) * ih;
  return (
    <div className="table-wrap"><svg className="chart mid" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="경기 진행률별 큰 가격 변화 비율">
      {yt.map((v) => (
        <g key={v}>
          <line className={v === 0 ? "zero" : "grid-line"} x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{pct(v, 0)}</text>
        </g>
      ))}
      {cols.map((c, j) => <text key={c.key} x={x(j)} y={m.t + ih + 15} textAnchor="middle" className="tiny">{c.key === "pre" ? "전" : c.key === "ot" ? "100+" : `${(j - 1) * 10}`}</text>)}
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">경기 진행률 % (열 = 10% 구간)</text>
      {TAIL_SERIES.map((s) => {
        const pts = cols.map((c, j) => { const r = get(s.w, c.key); return r ? { j, v: s.pick(r), r } : null; }).filter((p) => p !== null);
        const d_ = pts.map((p, i) => `${i ? "L" : "M"}${x(p.j).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
        return (
          <g key={s.key}>
            <path d={d_} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} strokeLinejoin="round" />
            {pts.map((p) => (
              <g key={p.j} className="pt">
                <circle className="mark" cx={x(p.j)} cy={y(p.v)} r={3} fill={s.color} stroke="var(--surface)" strokeWidth={1.5} />
                <circle className="hit" cx={x(p.j)} cy={y(p.v)} r={9}>
                  <title>{`${cols[p.j].label} · ${s.label}\n${pct(p.v, 2)} (n=${num(p.r.n)})`}</title>
                </circle>
              </g>
            ))}
          </g>
        );
      })}
    </svg></div>
  );
}

export function SwingTable({ d }: { d: ExploreSportData }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>진행률</th><th>창</th><th className="n">n</th><th className="n">p50</th><th className="n">p90</th><th className="n">p99</th><th className="n">≥5pp</th><th className="n">≥10pp</th></tr></thead>
        <tbody>
          {d.time_cols.flatMap((c) => (["1m", "10m"] as const).map((w) => {
            const r = d.swings.find((q) => q.col === c.key && q.window === w);
            if (!r) return null;
            return (
              <tr key={`${c.key}-${w}`}>
                <td>{c.label}</td><td>{w === "1m" ? "1분" : "10분"}</td><td className="n">{num(r.n)}</td>
                <td className="n">{pp(r.p50)}</td><td className="n">{pp(r.p90)}</td><td className="n">{pp(r.p99)}</td>
                <td className="n">{pct(r.share_ge_5, 2)}</td><td className="n">{pct(r.share_ge_10, 2)}</td>
              </tr>
            );
          }))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------- 5. Game browser ---------------------------- */

const hmFmt = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false });
const hm = (t: number) => hmFmt.format(new Date(t));
export const SIDE_COLOR: Record<string, string> = { home: "var(--s1)", away: "var(--s2)", draw: "var(--s3)" };
const SIDE_LABEL: Record<string, string> = { home: "홈", away: "원정", draw: "무승부" };

export function GameLegend({ g }: { g: GameDetail }) {
  return (
    <div className="legend">
      {g.outcomes.map((o) => (
        <span key={o.side}><i className="line" style={{ background: SIDE_COLOR[o.side] ?? "var(--muted)" }} />{o.label ?? o.side} <span className="muted">({SIDE_LABEL[o.side] ?? o.side}{o.won === true ? " · 승" : o.won === false ? " · 패" : ""})</span></span>
      ))}
      {g.score_events.length > 0 && <span><i className="tick" />득점</span>}
      {g.positions.length > 0 && <span><span aria-hidden="true">▲▼</span>전략 진입·청산</span>}
    </div>
  );
}

export function GameChart({ g }: { g: GameDetail }) {
  const series = g.outcomes.map((o) => ({ o, pts: o.points.map(([at, p]) => ({ t: new Date(at).getTime(), p })).filter((q) => Number.isFinite(q.t)) }));
  const all = series.flatMap((s) => s.pts.map((q) => q.t));
  if (!all.length) return <p className="muted">가격 데이터 없음</p>;
  const t0 = Math.min(...all), t1 = Math.max(...all);
  const W = 960, H = 360, m = { t: 34, r: 16, b: 30, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0 || 1)) * iw;
  const y = (v: number) => m.t + ih - v * ih;
  const inRange = (t: number | null) => t !== null && t >= t0 && t <= t1;
  const ms = (iso: string | null) => (iso ? new Date(iso).getTime() : null);
  const start = ms(g.start_time), end = ms(g.ended_at);
  const xt = Array.from({ length: 6 }, (_, i) => t0 + ((t1 - t0) * i) / 5);
  // label a score change only when it clears the previous label; the tick and tooltip always remain
  const labelled = g.score_events.reduce<{ last: number; keep: Set<number> }>((acc, e, i) => {
    const t = ms(e.at);
    if (t !== null && inRange(t) && x(t) - acc.last >= 30) { acc.keep.add(i); acc.last = x(t); }
    return acc;
  }, { last: -Infinity, keep: new Set() }).keep;
  return (
    <div className="table-wrap">
      <svg className="chart wide" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${g.title ?? g.game_key} 결과별 가격 경로`}>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
            <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{v.toFixed(2)}</text>
          </g>
        ))}
        {xt.map((t, i) => <text key={i} x={x(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === 5 ? "end" : "middle"}>{hm(t)}</text>)}
        {inRange(start) && <>
          <line className="axis-line" x1={x(start!)} x2={x(start!)} y1={m.t} y2={m.t + ih} />
          <text x={x(start!) + 4} y={m.t + ih - 6}>시작 {hm(start!)}</text>
        </>}
        {inRange(end) && <>
          <line className="axis-line" x1={x(end!)} x2={x(end!)} y1={m.t} y2={m.t + ih} />
          <text x={x(end!) - 4} y={m.t + ih - 6} textAnchor="end">종료 {hm(end!)}</text>
        </>}
        {g.score_events.map((e, i) => {
          const t = ms(e.at);
          if (!inRange(t)) return null;
          return (
            <g key={i} className="pt">
              <line x1={x(t!)} x2={x(t!)} y1={m.t - 6} y2={m.t + ih} stroke={SIDE_COLOR[e.scorer] ?? "var(--muted)"} strokeWidth={1} opacity={0.35} />
              {labelled.has(i) && <text x={x(t!)} y={m.t - 10} textAnchor="middle" style={{ fill: "var(--ink-2)" }}>{e.home_score}–{e.away_score}</text>}
              <rect className="hit" x={x(t!) - 5} y={m.t - 22} width={10} height={ih + 22}>
                <title>{`${hm(t!)} KST · ${e.scorer === "home" ? g.home_team ?? "홈" : g.away_team ?? "원정"} 득점 (+${e.points})\n스코어 ${e.home_score}–${e.away_score}${e.game_minute !== null ? ` · ${e.game_minute}′` : ""}`}</title>
              </rect>
            </g>
          );
        })}
        {series.map(({ o, pts }) => (
          <path key={o.side} d={pts.map((q, i) => `${i ? "L" : "M"}${x(q.t).toFixed(1)},${y(q.p).toFixed(1)}`).join("")}
            fill="none" stroke={SIDE_COLOR[o.side] ?? "var(--muted)"} strokeWidth={2} strokeLinejoin="round" />
        ))}
        {series.map(({ o, pts }) => pts.filter((_, i) => i % 3 === 0).map((q) => (
          <circle key={`${o.side}-${q.t}`} className="hit" cx={x(q.t)} cy={y(q.p)} r={7}>
            <title>{`${hm(q.t)} KST · ${o.label ?? o.side} ${q.p.toFixed(3)}`}</title>
          </circle>
        )))}
        {g.positions.map((p, i) => {
          const color = SIDE_COLOR[p.side ?? ""] ?? "var(--ink)";
          const to = ms(p.opened_at), tc = ms(p.closed_at);
          const entry = inRange(to) && p.entry_price !== null ? { cx: x(to!), cy: y(p.entry_price) } : null;
          const exit = inRange(tc) && p.exit_price !== null ? { cx: x(tc!), cy: y(p.exit_price) } : null;
          const tip = `${p.variant_id} (${p.mode}) · ${p.outcome ?? p.side ?? ""}\n진입 ${p.opened_at ? hm(to!) : "—"} @ ${p.entry_price?.toFixed(3) ?? "—"} · $${p.stake_usdc ?? "—"}\n청산 ${tc ? hm(tc) : "—"} @ ${p.exit_price?.toFixed(3) ?? "—"} · ${p.exit_reason ?? p.status}\n실현 ${p.realized_pnl !== null ? `${p.realized_pnl >= 0 ? "+" : "−"}$${Math.abs(p.realized_pnl).toFixed(2)}` : "미확정"}`;
          return (
            <g key={i} className="pt">
              {entry && exit && <line x1={entry.cx} y1={entry.cy} x2={exit.cx} y2={exit.cy} stroke={color} strokeWidth={1.25} strokeDasharray="3 3" />}
              {entry && <path className="mark" d={`M${entry.cx},${entry.cy - 7}L${entry.cx + 6},${entry.cy + 4}L${entry.cx - 6},${entry.cy + 4}Z`} fill={color} stroke="var(--surface)" strokeWidth={2} />}
              {exit && <path className="mark" d={`M${exit.cx},${exit.cy + 7}L${exit.cx + 6},${exit.cy - 4}L${exit.cx - 6},${exit.cy - 4}Z`} fill="var(--surface)" stroke={color} strokeWidth={2} />}
              {entry && <circle className="hit" cx={entry.cx} cy={entry.cy} r={12}><title>{tip}</title></circle>}
              {exit && <circle className="hit" cx={exit.cx} cy={exit.cy} r={12}><title>{tip}</title></circle>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

