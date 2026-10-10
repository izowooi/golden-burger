// SVG charts for /lts (late threshold stability). Same conventions as explore-charts.tsx: CSS tokens for
// light/dark, native <title> tooltips on enlarged hit targets, values in ink colors, identity never color-alone.
import { ticks } from "@/components/charts";
import { num, pct } from "@/lib/format";
import type { LtsCell, LtsMetric } from "@/lib/lts";
import { METRICS, metricValue } from "@/lib/lts";

const DIV_SCALE: Partial<Record<LtsMetric, number>> = { maker_roi: 0.15, taker_roi: 0.15, gap: 0.08 };
const pp = (v: number, d = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}`;

function fmt(m: LtsMetric, v: number | null) {
  if (v === null) return "—";
  if (m === "mdd") return v.toFixed(3);
  if (m === "maker_roi" || m === "taker_roi" || m === "gap") return `${pp(v)}%`;
  return `${(v * 100).toFixed(0)}%`;
}

function fill(m: LtsMetric, v: number | null) {
  if (v === null) return "var(--surface-2)";
  const kind = METRICS.find((x) => x.key === m)?.kind ?? "seq";
  if (kind === "div") {
    const t = Math.round(Math.min(1, Math.abs(v) / (DIV_SCALE[m] ?? 0.1)) * 100);
    return `color-mix(in oklab, var(${v >= 0 ? "--div-pos" : "--div-neg"}) ${t}%, var(--div-mid))`;
  }
  const t = Math.round(Math.max(0, Math.min(1, m === "mdd" ? v / 0.3 : v)) * 85);
  return `color-mix(in oklab, var(--s1) ${t}%, var(--surface-2))`;
}

export function HeatLegend({ metric, minFills }: { metric: LtsMetric; minFills: number }) {
  const meta = METRICS.find((x) => x.key === metric);
  return (
    <div className="legend explore-legend">
      {meta?.kind === "div" ? (
        <span className="div-scale">
          <span className="muted">음수</span><i className="div-bar" /><span className="muted">양수</span>
          <span className="muted">(색 포화 = ±{((DIV_SCALE[metric] ?? 0.1) * 100).toFixed(0)}%)</span>
        </span>
      ) : <span className="muted">진할수록 큼</span>}
      <span><i className="swatch-thin" />체결 n &lt; {minFills} (표본 부족)</span>
      <span>★ R1–R6 모두 통과</span>
    </div>
  );
}

export function LtsHeatmap({ cells, metric, progress, thresholds, minFills, label }: {
  cells: LtsCell[]; metric: LtsMetric; progress: number[]; thresholds: number[]; minFills: number; label: string;
}) {
  const W = 640, m = { t: 26, r: 8, b: 36, l: 92 };
  const cw = (W - m.l - m.r) / progress.length, ch = 30;
  const H = m.t + ch * thresholds.length + m.b;
  const at = new Map(cells.map((c) => [`${c.progress}|${c.y}`, c]));
  const x = (i: number) => m.l + i * cw;
  const y = (j: number) => m.t + (thresholds.length - 1 - j) * ch;     // high thresholds on top
  return (
    <div className="table-wrap">
      <svg className="chart heat lts-heat" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        {progress.map((p, i) => {
          const c = cells.find((x) => x.progress === p);
          return (
            <text key={p} x={x(i) + cw / 2} y={m.t - 9} textAnchor="middle">{Math.round(p * 100)}% ({c?.t_min ?? "?"}분)</text>
          );
        })}
        {thresholds.map((t, j) => (
          <text key={t} x={m.l - 8} y={y(j) + ch / 2} dy="0.32em" textAnchor="end">Y {t.toFixed(2)}</text>
        ))}
        <text x={m.l + (W - m.l - m.r) / 2} y={H - 8} textAnchor="middle">진행 시점 T (예정 시작 뒤 벽시계 분, 중앙 경기 길이 대비 %)</text>
        {progress.map((p, i) => thresholds.map((t, j) => {
          const c = at.get(`${p}|${t}`);
          if (!c) return null;
          const v = metricValue(c, metric);
          const low = (metric === "maker_roi" ? c.main.n : c.signals) < minFills;
          const cx = x(i) + 1, cy = y(j) + 1, w = cw - 2, h = ch - 2;
          const star = c.rules?.all_R1_R6;
          return (
            <g key={`${p}-${t}`} className="pt">
              <rect className="mark" x={cx} y={cy} width={w} height={h} rx={3} fill={low ? "var(--surface-2)" : fill(metric, v)} />
              <text x={cx + w / 2} y={cy + h / 2} dy="0.32em" textAnchor="middle" className={low ? "val low" : "val"}>
                {star ? "★ " : ""}{fmt(metric, v)}
              </text>
              <rect className="hit" x={cx} y={cy} width={w} height={h}>
                <title>{tooltip(c)}</title>
              </rect>
            </g>
          );
        }))}
      </svg>
    </div>
  );
}

export function tooltip(c: LtsCell) {
  const mk = c.main;
  return [
    `T ${Math.round(c.progress * 100)}% (${c.t_min}분) · Y ${c.y.toFixed(2)} [${c.band[0].toFixed(2)}–${c.band[1].toFixed(3)}] · ${c.wait}`,
    `신호 ${num(c.signals)} · 평균 가격 ${c.mean_price?.toFixed(3) ?? "—"} → 실현 승률 ${pct(c.win_rate)} (gap ${c.gap === null ? "—" : pp(c.gap)}pp)`,
    `재이탈 ${pct(c.rebreak_rate)} · 역전 ${pct(c.reversal_rate)} · 낙폭 중앙 ${c.mdd_median ?? "—"} / p90 ${c.mdd_p90 ?? "—"}`,
    `maker 체결 ${num(mk.n)} (${pct(mk.fill_rate)}; 승 ${pct(mk.fill_rate_win)} / 패 ${pct(mk.fill_rate_loss)}) · ROI ${mk.roi === null ? "—" : pp(mk.roi)}% (H1 ${mk.h1.roi === null ? "—" : pp(mk.h1.roi)} / H2 ${mk.h2.roi === null ? "—" : pp(mk.h2.roi)})`,
    `taker ROI ${c.taker.roi === null ? "—" : pp(c.taker.roi)}% · 신호당 maker 손익 ${mk.itt_pnl_per_signal?.toFixed(3) ?? "—"} USDC`,
    c.rules ? `규칙 ${(["R1", "R2", "R3", "R4", "R5", "R6"] as const).map((k) => `${k}${c.rules![k] ? "✓" : "✗"}`).join(" ")}` : "",
  ].filter(Boolean).join("\n");
}

/* ------------------------- calibration given (sport, T, price) ------------------------- */

export function CalibLegend() {
  return (
    <div className="legend">
      <span><i className="swatch" style={{ background: "var(--s1)" }} />신호 전체: 신호 가격 → 실현 승률 (95% CI)</span>
      <span><svg width={12} height={12} aria-hidden="true"><rect x={1} y={1} width={10} height={10} fill="var(--s2)" /></svg>maker 체결분: 지정가 → 체결된 경기의 승률</span>
    </div>
  );
}

export function CalibChart({ cells, label }: { cells: LtsCell[]; label: string }) {
  const W = 560, H = 420, m = { t: 12, r: 16, b: 42, l: 48 };
  const lo = 0.5, hi = 1.0;
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (v: number) => m.l + ((v - lo) / (hi - lo)) * iw;
  const y = (v: number) => m.t + ih - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * ih;
  const yt = ticks(lo, hi, 5), xt = ticks(lo, hi, 5);
  const sig = cells.filter((c) => c.signals > 0 && c.mean_price !== null && c.win_rate !== null);
  const fills = cells.filter((c) => c.main.n > 0 && c.main.mean_limit !== null && c.main.fill_win_rate !== null);
  return (
    <svg className="chart rel mid" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {yt.map((v) => (
        <g key={`y${v}`}>
          <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{v.toFixed(2)}</text>
        </g>
      ))}
      {xt.map((v) => (
        <g key={`x${v}`}>
          <line className="grid-line" x1={x(v)} x2={x(v)} y1={m.t} y2={m.t + ih} />
          <text x={x(v)} y={m.t + ih + 15} textAnchor="middle">{v.toFixed(2)}</text>
        </g>
      ))}
      <line className="ref" x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} />
      <text x={x(0.6) + 6} y={y(0.55)} textAnchor="start">가격 = 승률 (손익분기, 점선)</text>
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">매수 가격</text>
      <text transform={`translate(12 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">실현 승률</text>
      {sig.map((c) => (
        <g key={`s${c.y}`} className="pt">
          {c.win_ci[0] !== null && c.win_ci[1] !== null && (
            <line x1={x(c.mean_price!)} x2={x(c.mean_price!)} y1={y(c.win_ci[0])} y2={y(c.win_ci[1])} stroke="var(--s1)" strokeWidth={2} opacity={0.5} />
          )}
          <circle className="mark" cx={x(c.mean_price!)} cy={y(c.win_rate!)} r={5} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />
          <circle className="hit" cx={x(c.mean_price!)} cy={y(c.win_rate!)} r={12}><title>{tooltip(c)}</title></circle>
        </g>
      ))}
      {fills.map((c) => (
        <g key={`f${c.y}`} className="pt">
          <rect className="mark" x={x(c.main.mean_limit!) - 5} y={y(c.main.fill_win_rate!) - 5} width={10} height={10} fill="var(--s2)" stroke="var(--surface)" strokeWidth={2} />
          <rect className="hit" x={x(c.main.mean_limit!) - 12} y={y(c.main.fill_win_rate!) - 12} width={24} height={24}>
            <title>{`maker 체결분 · Y ${c.y.toFixed(2)}\n평균 지정가 ${c.main.mean_limit!.toFixed(3)} → 체결 경기 승률 ${pct(c.main.fill_win_rate)} (체결 ${num(c.main.n)})\nROI ${c.main.roi === null ? "—" : pp(c.main.roi)}%`}</title>
          </rect>
        </g>
      ))}
    </svg>
  );
}

/* ------------------------------- adverse selection ------------------------------- */

export function FillLegend() {
  return (
    <div className="legend">
      <span><i className="swatch" style={{ background: "var(--s1)" }} />P(체결 | 선두 승)</span>
      <span><svg width={12} height={12} aria-hidden="true"><rect x={1} y={1} width={10} height={10} fill="var(--s2)" /></svg>P(체결 | 선두 패)</span>
    </div>
  );
}

export function FillChart({ cells, thresholds, label }: { cells: LtsCell[]; thresholds: number[]; label: string }) {
  const W = 560, H = 300, m = { t: 12, r: 12, b: 40, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const step = iw / thresholds.length;
  const x = (j: number) => m.l + step * (j + 0.5);
  const y = (v: number) => m.t + ih - v * ih;
  const by = new Map(cells.map((c) => [c.y, c]));
  return (
    <svg className="chart mid" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {ticks(0, 1, 5).map((v) => (
        <g key={v}>
          <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{(v * 100).toFixed(0)}%</text>
        </g>
      ))}
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">임계값 Y</text>
      {thresholds.map((t, j) => {
        const c = by.get(t);
        const w = c?.main.fill_rate_win, l = c?.main.fill_rate_loss;
        return (
          <g key={t} className="pt">
            <text x={x(j)} y={m.t + ih + 15} textAnchor="middle">{t.toFixed(2)}</text>
            {w != null && l != null && <line x1={x(j)} x2={x(j)} y1={y(w)} y2={y(l)} stroke="var(--axis)" strokeWidth={2} />}
            {w != null && <circle className="mark" cx={x(j)} cy={y(w)} r={5} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />}
            {l != null && <rect className="mark" x={x(j) - 5} y={y(l) - 5} width={10} height={10} fill="var(--s2)" stroke="var(--surface)" strokeWidth={2} />}
            {c && <rect className="hit" x={x(j) - step / 2} y={m.t} width={step} height={ih}><title>{tooltip(c)}</title></rect>}
          </g>
        );
      })}
    </svg>
  );
}
