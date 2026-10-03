// Server-rendered SVG charts. Hover detail uses native <title> tooltips on enlarged hit targets,
// so no client JS ships for charts. Colors come from CSS tokens so light/dark both apply.
import { kstDay, kst, num, pct, signedPct, signedUsd } from "@/lib/format";

function niceStep(span: number, count: number) {
  const raw = span / Math.max(count, 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = norm > 5 ? 10 : norm > 2 ? 5 : norm > 1 ? 2 : 1;
  return step * mag;
}

export function ticks(min: number, max: number, count = 5) {
  if (!(max > min)) return [min];
  const step = niceStep(max - min, count);
  const out: number[] = [];
  for (let v = Math.ceil(min / step - 1e-9) * step; v <= max + step * 1e-6; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/* ------------------------------ Equity curve ------------------------------ */

export function EquityChart({ points }: { points: { at: string; cum_pnl: number | null }[] }) {
  const data = points
    .map((p) => ({ t: new Date(p.at).getTime(), y: p.cum_pnl, at: p.at }))
    .filter((p): p is { t: number; y: number; at: string } => Number.isFinite(p.t) && p.y !== null && Number.isFinite(p.y))
    .sort((a, b) => a.t - b.t);
  if (data.length < 2) return null;

  const t0 = data[0].t, t1 = data[data.length - 1].t;
  const ys = data.map((d) => d.y);
  let y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys);
  const pad = (y1 - y0) * 0.08 || 1;
  y0 -= pad; y1 += pad;
  const yt = ticks(y0, y1, 5);
  y0 = Math.min(y0, yt[0]); y1 = Math.max(y1, yt[yt.length - 1]);
  // Left margin grows with the widest tick label so large balances (e.g. −$4,000.00) are not clipped.
  const W = 960, H = 300, m = { t: 14, r: 16, b: 28, l: Math.max(64, 16 + 7.5 * Math.max(...yt.map((v) => signedUsd(v).length))) };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0 || 1)) * iw;
  const y = (v: number) => m.t + ih - ((v - y0) / (y1 - y0)) * ih;
  const path = data.map((d, i) => `${i ? "L" : "M"}${x(d.t).toFixed(1)},${y(d.y).toFixed(1)}`).join("");
  const last = data[data.length - 1];
  const xTickCount = 5;
  const xt = Array.from({ length: xTickCount }, (_, i) => t0 + ((t1 - t0) * i) / (xTickCount - 1));
  const stride = Math.max(1, Math.ceil(data.length / 160));

  return (
    <div className="table-wrap">
    <svg className="chart wide" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="누적 실현 손익 곡선">
      {yt.map((v) => (
        <g key={v}>
          <line className={v === 0 ? "zero" : "grid-line"} x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 8} y={y(v)} dy="0.32em" textAnchor="end">{signedUsd(v)}</text>
        </g>
      ))}
      {xt.map((t, i) => (
        <text key={i} x={x(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === xTickCount - 1 ? "end" : "middle"}>{kstDay(new Date(t).toISOString())}</text>
      ))}
      <path d={path} fill="none" stroke="var(--s1)" strokeWidth={2} strokeLinejoin="round" />
      <circle cx={x(last.t)} cy={y(last.y)} r={4} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />
      <text x={x(last.t) - 6} y={y(last.y) - 10} textAnchor="end" style={{ fill: "var(--ink)", fontWeight: 600 }}>{signedUsd(last.y)}</text>
      {data.filter((_, i) => i % stride === 0 || i === data.length - 1).map((d) => (
        <circle key={d.t} className="hit" cx={x(d.t)} cy={y(d.y)} r={8}>
          <title>{`${kst(d.at)} KST\n누적 ${signedUsd(d.y)}`}</title>
        </circle>
      ))}
    </svg>
    </div>
  );
}

/* ------------------------- Calibration reliability ------------------------ */

export interface ReliabilityBucket {
  p_lo: number;
  p_hi: number;
  n: number | null;
  mean_price: number | null;
  win_rate: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  gap: number | null;
}

/** Shared lower bound so every small-multiple panel uses the same scale. */
export function reliabilityDomainLo(values: number[]) {
  return Math.max(0, Math.floor(Math.min(...values) * 20) / 20);
}

/** Price bucket (x) vs realized win rate (y) with Wilson-style CI whiskers and a y=x reference. */
export function ReliabilityDiagram({ buckets, title, domainLo }: { buckets: ReliabilityBucket[]; title: string; domainLo?: number }) {
  const rows = buckets.filter((b) => b.win_rate !== null);
  if (!rows.length) return null;
  const W = 300, H = 280, m = { t: 26, r: 12, b: 36, l: 42 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const lows = rows.flatMap((b) => [b.p_lo, b.ci_lo ?? b.win_rate ?? 1, b.win_rate ?? 1]);
  const lo = domainLo ?? reliabilityDomainLo(lows);
  const hi = 1;
  const s = (v: number) => (v - lo) / (hi - lo);
  const x = (v: number) => m.l + s(v) * iw;
  const y = (v: number) => m.t + ih - s(v) * ih;
  const tk = ticks(lo, hi, 4);
  const fmt = (v: number) => (hi - lo <= 0.2 ? v.toFixed(2) : v.toFixed(1));
  const maxN = Math.max(...rows.map((b) => b.n ?? 0), 1);

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title} 보정 신뢰도 다이어그램`}>
      <text className="title" x={m.l} y={14}>{title}</text>
      {tk.map((v) => (
        <g key={v}>
          <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <line className="grid-line" y1={m.t} y2={m.t + ih} x1={x(v)} x2={x(v)} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{fmt(v)}</text>
          <text x={x(v)} y={m.t + ih + 14} textAnchor="middle">{fmt(v)}</text>
        </g>
      ))}
      <line className="axis-line" x1={m.l} x2={W - m.r} y1={m.t + ih} y2={m.t + ih} />
      <line className="ref" x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} />
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">시장가격 (implied p)</text>
      <text transform={`translate(10 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">실현 승률</text>
      {rows.map((b) => {
        const px = b.mean_price ?? (b.p_lo + b.p_hi) / 2;
        const wr = b.win_rate as number;
        const r = 3 + 3 * Math.sqrt((b.n ?? 0) / maxN);
        return (
          <g key={`${b.p_lo}-${b.p_hi}`} className="pt">
            {b.ci_lo !== null && b.ci_hi !== null && (
              <line x1={x(px)} x2={x(px)} y1={y(Math.max(lo, b.ci_lo))} y2={y(Math.min(hi, b.ci_hi))} stroke="var(--s1)" strokeWidth={1.5} strokeLinecap="round" opacity={0.55} />
            )}
            <circle className="mark" cx={x(px)} cy={y(Math.max(lo, wr))} r={r} fill="var(--s1)" stroke="var(--surface)" strokeWidth={1.5} />
            <circle className="hit" cx={x(px)} cy={y(Math.max(lo, wr))} r={Math.max(10, r + 4)}>
              <title>{`가격 ${b.p_lo.toFixed(2)}–${b.p_hi.toFixed(2)} (평균 ${b.mean_price?.toFixed(3) ?? "—"})\n실현 승률 ${pct(wr)} [${pct(b.ci_lo)}, ${pct(b.ci_hi)}]\ngap ${signedPct(b.gap)} · n=${num(b.n)}`}</title>
            </circle>
          </g>
        );
      })}
    </svg>
  );
}

/* --------------------------- Event sensitivity ---------------------------- */

export interface SeriesDef {
  key: string;
  label: string;
  color: string;
}

export interface GroupRow {
  bucket: string;
  n: number | null;
  values: Record<string, number | null | undefined>;
}

export const SENS_SERIES: SeriesDef[] = [
  { key: "mean_abs_jump", label: "평균 |점프|", color: "var(--s1)" },
  { key: "reversion_5m", label: "5분 되돌림", color: "var(--s2)" },
  { key: "reversion_10m", label: "10분 되돌림", color: "var(--s3)" },
];

export const STATE_SERIES: SeriesDef[] = [
  { key: "trailing", label: "뒤짐", color: "var(--s1)" },
  { key: "level", label: "동점", color: "var(--s2)" },
  { key: "leading", label: "앞섬", color: "var(--s3)" },
];

export function Legend({ series }: { series: SeriesDef[] }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
      ))}
    </div>
  );
}

/** Grouped bars per minute bucket. Every series shares one unit (probability points), so one axis. */
export function GroupedBars({ rows, series, title }: { rows: GroupRow[]; series: SeriesDef[]; title: string }) {
  if (!rows.length) return null;
  const W = 360, H = 250, m = { t: 26, r: 8, b: 30, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const vals = rows.flatMap((r) => series.map((s) => r.values[s.key])).filter((v): v is number => typeof v === "number");
  if (!vals.length) return null;
  let y0 = Math.min(0, ...vals), y1 = Math.max(0, ...vals);
  const tk = ticks(y0, y1, 4);
  y0 = Math.min(y0, tk[0]); y1 = Math.max(y1, tk[tk.length - 1]);
  const y = (v: number) => m.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
  const band = iw / rows.length;
  const gap = 2;
  const bw = Math.min(18, (band * 0.72 - gap * (series.length - 1)) / series.length);
  const groupW = bw * series.length + gap * (series.length - 1);

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
      <text className="title" x={m.l} y={14}>{title}</text>
      {tk.map((v) => (
        <g key={v}>
          <line className={v === 0 ? "zero" : "grid-line"} x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{(v * 100).toFixed(0)}pp</text>
        </g>
      ))}
      {rows.map((r, i) => {
        const gx = m.l + band * i + (band - groupW) / 2;
        return (
          <g key={r.bucket}>
            <text x={m.l + band * i + band / 2} y={H - 10} textAnchor="middle">{r.bucket}′</text>
            {series.map((s, j) => {
              const v = r.values[s.key];
              if (typeof v !== "number") return null;
              const top = y(Math.max(v, 0)), bot = y(Math.min(v, 0));
              const h = Math.max(1, bot - top);
              const bx = gx + j * (bw + gap);
              return (
                <g key={s.key} className="pt">
                  <rect className="mark" x={bx} y={top} width={bw} height={h} rx={Math.min(3, bw / 2)} fill={s.color} />
                  <rect className="hit" x={bx - 1} y={m.t} width={bw + 2} height={ih}>
                    <title>{`${title} · ${r.bucket}′\n${s.label} ${signedPct(v)} · n=${num(r.n)}`}</title>
                  </rect>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}

/* ------------------------------ Inline bar ------------------------------- */

/** Small diverging bar for table cells: magnitude relative to the column's max |value|. */
export function InlineBar({ value, max }: { value: number | null; max: number }) {
  if (value === null || !max) return null;
  const w = Math.min(1, Math.abs(value) / max) * 60;
  return (
    <svg width={124} height={10} aria-hidden="true" style={{ verticalAlign: "middle", marginLeft: 6 }}>
      <line x1={62} x2={62} y1={0} y2={10} stroke="var(--axis)" />
      <rect x={value >= 0 ? 62 : 62 - w} y={2} width={w} height={6} rx={2} fill={value >= 0 ? "var(--s1)" : "var(--s2)"} />
    </svg>
  );
}
