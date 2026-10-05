// Server-rendered SVG charts for /ou05 (O/U 0.5 market-life study). Same conventions as explore-charts.tsx:
// CSS tokens for light/dark, native <title> tooltips on enlarged hit targets, no client JS.
import { ticks } from "@/components/charts";
import { num, pct } from "@/lib/format";
import type { Band, CalibRow, DistSeries, Ou05Market, OverroundRow } from "@/lib/ou05";
import { PHASE_LABEL } from "@/lib/ou05";

const f2 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(2));
const f3 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(3));
const pp = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}pp`;

/* ------------------------- 1. overround by band -------------------------- */

export function BandLegend() {
  return (
    <div className="legend">
      <span><i className="line" style={{ background: "var(--s1)" }} />sum_ask 중앙값</span>
      <span><i style={{ background: "var(--s1)", opacity: 0.32 }} />25–75%</span>
      <span><i style={{ background: "var(--s1)", opacity: 0.12 }} />10–90%</span>
      <span><i className="tick" />킥오프</span>
    </div>
  );
}

export function BandChart({ bands, rows }: { bands: Band[]; rows: OverroundRow[] }) {
  const by = new Map(rows.map((r) => [r.band, r]));
  const vals = rows.flatMap((r) => (r.sum_ask ? [r.sum_ask.p90 ?? r.sum_ask.p75] : []));
  if (!vals.length) return <p className="muted">양쪽 호가가 있는 poll 데이터 없음</p>;
  const yMax = Math.min(2, Math.max(1.05, Math.ceil(Math.max(...vals) * 20) / 20));
  const W = 900, H = 320, m = { t: 14, r: 12, b: 64, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const step = iw / bands.length;
  const x = (i: number) => m.l + step * (i + 0.5);
  const y = (v: number) => m.t + ih - ((Math.min(v, yMax) - 1) / (yMax - 1)) * ih;
  const kickIdx = bands.findIndex((b) => b.kind === "inplay");
  const pts = bands.map((b, i) => ({ i, b, r: by.get(b.key) })).filter((p) => p.r?.sum_ask);
  const area = (lo: "p10" | "p25", hi: "p90" | "p75") => {
    const up = pts.map((p) => `${x(p.i).toFixed(1)},${y(p.r!.sum_ask![hi] ?? p.r!.sum_ask!.p75).toFixed(1)}`);
    const dn = [...pts].reverse().map((p) => `${x(p.i).toFixed(1)},${y(p.r!.sum_ask![lo] ?? p.r!.sum_ask!.p25).toFixed(1)}`);
    return `M${up.join("L")}L${dn.join("L")}Z`;
  };
  return (
    <div className="table-wrap">
      <svg className="chart wide" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="킥오프까지 시간 구간별 sum_ask 분포">
        {ticks(1, yMax, 5).map((v) => (
          <g key={v}>
            <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
            <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{v.toFixed(2)}</text>
          </g>
        ))}
        {kickIdx > 0 && <>
          <line className="axis-line" x1={m.l + step * kickIdx} x2={m.l + step * kickIdx} y1={m.t} y2={m.t + ih} strokeDasharray="4 3" />
          <text x={m.l + step * kickIdx + 4} y={m.t + 10}>킥오프</text>
        </>}
        {bands.map((b, i) => (
          <text key={b.key} transform={`translate(${x(i)} ${m.t + ih + 10}) rotate(40)`} textAnchor="start" className="tiny">{b.label}</text>
        ))}
        {pts.length > 1 && <path d={area("p10", "p90")} fill="var(--s1)" opacity={0.12} />}
        {pts.length > 1 && <path d={area("p25", "p75")} fill="var(--s1)" opacity={0.28} />}
        <path d={pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.r!.sum_ask!.p50).toFixed(1)}`).join("")}
          fill="none" stroke="var(--s1)" strokeWidth={2} strokeLinejoin="round" />
        {pts.map(({ i, b, r }) => (
          <g key={b.key} className="pt">
            <circle className="mark" cx={x(i)} cy={y(r!.sum_ask!.p50)} r={4} fill="var(--s1)" stroke="var(--surface)" strokeWidth={2} />
            <rect className="hit" x={x(i) - step / 2} y={m.t} width={step} height={ih}>
              <title>{`${b.label}\nsum_ask 중앙값 ${f3(r!.sum_ask!.p50)} (25–75% ${f3(r!.sum_ask!.p25)}–${f3(r!.sum_ask!.p75)})\n스프레드 중앙값 ${f3(r!.spread?.p50)} · sum_bid ${f3(r!.sum_bid?.p50)}\n시장 ${num(r!.markets)} · ${num(r!.minutes)}분 · 양쪽 호가 ${pct(r!.two_sided_share)}`}</title>
            </rect>
          </g>
        ))}
        <text transform={`translate(12 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">over_ask + under_ask</text>
      </svg>
    </div>
  );
}

export function BandTable({ bands, rows }: { bands: Band[]; rows: OverroundRow[] }) {
  const by = new Map(rows.map((r) => [r.band, r]));
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>구간</th><th className="n">시장</th><th className="n">분</th><th className="n">양쪽 호가</th><th className="n">sum_ask p25</th><th className="n">p50</th><th className="n">p75</th><th className="n">p90</th><th className="n">스프레드 p50</th><th className="n">sum_bid p50</th></tr></thead>
        <tbody>
          {bands.map((b) => {
            const r = by.get(b.key);
            if (!r) return null;
            return (
              <tr key={b.key}>
                <td>{b.label}</td><td className="n">{num(r.markets)}</td><td className="n">{num(r.minutes)}</td>
                <td className="n">{pct(r.two_sided_share)}</td>
                <td className="n">{f3(r.sum_ask?.p25)}</td><td className="n">{f3(r.sum_ask?.p50)}</td>
                <td className="n">{f3(r.sum_ask?.p75)}</td><td className="n">{f3(r.sum_ask?.p90)}</td>
                <td className="n">{f3(r.spread?.p50)}</td><td className="n">{f3(r.sum_bid?.p50)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------- 2. distribution ---------------------------- */

function bucketLabels(edges: number[]) {
  return [`<${f2(edges[0])}`, ...edges.slice(0, -1).map((e, i) => `${f2(e)}–${f2(edges[i + 1])}`), `≥${f2(edges[edges.length - 1])}`];
}

export function DistChart({ edges, s }: { edges: number[]; s: DistSeries }) {
  const labels = bucketLabels(edges);
  const W = 420, H = 210, m = { t: 22, r: 8, b: 40, l: 38 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const top = Math.max(0.05, Math.max(...s.shares));
  const bw = iw / s.shares.length;
  const y = (v: number) => m.t + ih - (v / top) * ih;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${PHASE_LABEL[s.phase]} sum_ask 분포`}>
      <text className="title" x={m.l} y={12}>{PHASE_LABEL[s.phase]} <tspan className="tiny">· 시장 {num(s.markets)} · {num(s.minutes)}분</tspan></text>
      {ticks(0, top, 3).map((v) => (
        <g key={v}>
          <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
          <text x={m.l - 4} y={y(v)} dy="0.32em" textAnchor="end">{pct(v, 0)}</text>
        </g>
      ))}
      {s.shares.map((v, i) => (
        <g key={i} className="pt">
          {v > 0 && <rect className="mark" x={m.l + i * bw + 1} y={y(v)} width={Math.max(bw - 2, 1)} height={m.t + ih - y(v)} rx={2} fill="var(--s4)" />}
          <rect className="hit" x={m.l + i * bw} y={m.t} width={bw} height={ih}><title>{`sum_ask ${labels[i]}: 시간 비중 ${pct(v)}`}</title></rect>
        </g>
      ))}
      {[[1, "1.00"], [11, "1.10"], [21, "1.20"], [s.shares.length - 1, "≥2.00"]].map(([i, t]) => (
        <text key={i} x={m.l + (Number(i) + (Number(i) === s.shares.length - 1 ? 1 : 0)) * bw} y={m.t + ih + 14}
          textAnchor={Number(i) === s.shares.length - 1 ? "end" : "start"} className="tiny">{t}</text>
      ))}
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle">sum_ask (1.00–1.20 은 0.01 간격, 그 위 1.3·1.5·2.0 경계)</text>
    </svg>
  );
}

/* ----------------------------- 3. calibration ---------------------------- */

export function CalibLegend() {
  return (
    <div className="legend">
      <span><i className="line" style={{ background: "var(--s1)" }} />시장 Over 가격(평균)</span>
      <span><i style={{ background: "var(--s2)", borderRadius: 999 }} />실제 Over 비율 · 95% CI</span>
    </div>
  );
}

export function CalibChart({ bands, rows }: { bands: Band[]; rows: CalibRow[] }) {
  const by = new Map(rows.map((r) => [r.band, r]));
  const pts = bands.map((b, i) => ({ i, b, r: by.get(b.key) })).filter((p) => p.r);
  if (!pts.length) return <p className="muted">정산된 시장 없음</p>;
  const lo = Math.max(0, Math.floor(Math.min(...pts.flatMap((p) => [p.r!.mean_over, p.r!.ci_lo ?? p.r!.over_rate])) * 10) / 10);
  const W = 900, H = 300, m = { t: 14, r: 12, b: 64, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const step = iw / bands.length;
  const x = (i: number) => m.l + step * (i + 0.5);
  const y = (v: number) => m.t + ih - ((v - lo) / (1 - lo || 1)) * ih;
  return (
    <div className="table-wrap">
      <svg className="chart wide" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="구간별 Over 가격과 실제 Over 비율">
        {ticks(lo, 1, 5).map((v) => (
          <g key={v}>
            <line className="grid-line" x1={m.l} x2={W - m.r} y1={y(v)} y2={y(v)} />
            <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end">{v.toFixed(1)}</text>
          </g>
        ))}
        {bands.map((b, i) => (
          <text key={b.key} transform={`translate(${x(i)} ${m.t + ih + 10}) rotate(40)`} textAnchor="start" className="tiny">{b.label}</text>
        ))}
        <path d={pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.r!.mean_over).toFixed(1)}`).join("")}
          fill="none" stroke="var(--s1)" strokeWidth={2} />
        {pts.map(({ i, b, r }) => (
          <g key={b.key} className="pt">
            {r!.ci_lo !== null && r!.ci_hi !== null && <line x1={x(i)} x2={x(i)} y1={y(r!.ci_lo)} y2={y(r!.ci_hi)} stroke="var(--s2)" strokeWidth={1.5} />}
            <circle className="mark" cx={x(i)} cy={y(r!.over_rate)} r={4} fill="var(--s2)" stroke="var(--surface)" strokeWidth={2} />
            <rect className="hit" x={x(i) - step / 2} y={m.t} width={step} height={ih}>
              <title>{`${b.label} · 시장 ${num(r!.n)} (poll 가격 ${num(r!.n_poll)})\nOver 가격 평균 ${f3(r!.mean_over)} → 실제 Over ${pct(r!.over_rate)} [${pct(r!.ci_lo)}, ${pct(r!.ci_hi)}]\ngap ${pp(r!.gap)} · 0:0 비율 ${pct(r!.nil_rate)}`}</title>
            </rect>
          </g>
        ))}
      </svg>
    </div>
  );
}

/* --------------------------- 4. market browser --------------------------- */

const dtFmt = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
const dt = (t: number) => dtFmt.format(new Date(t));

const QUOTE_SERIES = [
  { idx: 2, label: "Over ask", color: "var(--s1)", dash: undefined },
  { idx: 1, label: "Over bid", color: "var(--s1)", dash: "4 3" },
  { idx: 4, label: "Under ask", color: "var(--s2)", dash: undefined },
  { idx: 3, label: "Under bid", color: "var(--s2)", dash: "4 3" },
  { idx: 6, label: "과거 Over 중간가", color: "var(--s3)", dash: undefined },
] as const;

export function MarketLegend({ g }: { g: Ou05Market }) {
  const hasHist = g.points.some((p) => p[7] === "h");
  return (
    <div className="legend">
      {QUOTE_SERIES.filter((s) => s.idx !== 6 || hasHist).map((s) => (
        <span key={s.label}><i className="line" style={{ background: s.dash ? `repeating-linear-gradient(90deg, ${s.color} 0 4px, transparent 4px 7px)` : s.color }} />{s.label}</span>
      ))}
      <span><i className="tick" />킥오프{g.goals.length ? " · 득점" : ""}</span>
    </div>
  );
}

function segments(pts: { t: number; v: number | null }[], gapMs: number) {
  // break the line where a value is missing or rows are further apart than the heartbeat window
  const out: string[] = [];
  let cur = "";
  let prev: number | null = null;
  for (const p of pts) {
    if (p.v === null || (prev !== null && p.t - prev > gapMs)) { if (cur) out.push(cur); cur = ""; }
    if (p.v !== null) cur += `${cur ? "L" : "M"}${p.t},${p.v}`;
    prev = p.t;
  }
  if (cur) out.push(cur);
  return out;
}

export function MarketCharts({ g }: { g: Ou05Market }) {
  const rows = g.points.map((p) => ({ t: new Date(p[0]).getTime(), p })).filter((r) => Number.isFinite(r.t));
  if (!rows.length) return <p className="muted">데이터 없음</p>;
  const t0 = rows[0].t, t1 = rows[rows.length - 1].t;
  const kick = g.game_start ? new Date(g.game_start).getTime() : null;
  const W = 960, m = { t: 16, r: 16, b: 28, l: 48 };
  const iw = W - m.l - m.r;
  const x = (t: number) => m.l + ((t - t0) / (t1 - t0 || 1)) * iw;
  // a gap longer than ~3 downsample bins (or 30 min) is drawn as a break
  const gapMs = Math.max(30 * 60_000, ((t1 - t0) / Math.max(rows.length, 1)) * 3);
  const xt = Array.from({ length: 5 }, (_, i) => t0 + ((t1 - t0) * i) / 4);
  const marks = (ih: number) => <>
    {kick !== null && kick >= t0 && kick <= t1 && <>
      <line className="axis-line" x1={x(kick)} x2={x(kick)} y1={m.t} y2={m.t + ih} strokeDasharray="4 3" />
      <text x={x(kick) + 4} y={m.t + 10}>킥오프</text>
    </>}
    {g.goals.map((e, i) => {
      const t = new Date(e.at).getTime();
      if (!(t >= t0 && t <= t1)) return null;
      return (
        <g key={i} className="pt">
          <line x1={x(t)} x2={x(t)} y1={m.t} y2={m.t + ih} stroke="var(--ink-2)" strokeWidth={1} opacity={0.5} />
          <rect className="hit" x={x(t) - 5} y={m.t} width={10} height={ih}><title>{`${dt(t)} KST 득점 → ${e.home_score}–${e.away_score}`}</title></rect>
        </g>
      );
    })}
    {xt.map((t, i) => <text key={i} x={x(t)} y={m.t + ih + 18} textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"}>{dt(t)}</text>)}
  </>;

  const H1 = 300, ih1 = H1 - m.t - m.b;
  const y1 = (v: number) => m.t + ih1 - v * ih1;
  const sums = rows.map((r) => r.p[5]).filter((v): v is number => v !== null);
  const sMax = sums.length ? Math.min(2, Math.max(1.05, Math.ceil(Math.max(...sums) * 20) / 20)) : 1.1;
  const H2 = 180, ih2 = H2 - m.t - m.b;
  const y2 = (v: number) => m.t + ih2 - ((Math.min(v, sMax) - 1) / (sMax - 1)) * ih2;
  const every = Math.max(1, Math.floor(rows.length / 120));
  return (
    <>
      <div className="table-wrap">
        <svg className="chart wide" viewBox={`0 0 ${W} ${H1}`} role="img" aria-label={`${g.title} Over·Under 호가 생애 전체`}>
          {[0, 0.25, 0.5, 0.75, 1].map((v) => (
            <g key={v}>
              <line className="grid-line" x1={m.l} x2={W - m.r} y1={y1(v)} y2={y1(v)} />
              <text x={m.l - 6} y={y1(v)} dy="0.32em" textAnchor="end">{v.toFixed(2)}</text>
            </g>
          ))}
          {marks(ih1)}
          {QUOTE_SERIES.map((s) => segments(rows.map((r) => ({ t: r.t, v: r.p[s.idx] as number | null })), gapMs).map((d, k) => (
            <path key={`${s.label}-${k}`} d={d.replace(/([ML])(\d+),([\d.]+)/g, (_, c, t, v) => `${c}${x(Number(t)).toFixed(1)},${y1(Number(v)).toFixed(1)}`)}
              fill="none" stroke={s.color} strokeWidth={s.idx === 6 ? 1.5 : 2} strokeDasharray={s.dash} strokeLinejoin="round" />
          )))}
          {rows.filter((_, i) => i % every === 0).map((r) => (
            <rect key={r.t} className="hit" x={x(r.t) - 4} y={m.t} width={8} height={ih1}>
              <title>{`${dt(r.t)} KST${r.p[7] === "h" ? " (과거 중간가)" : ""}\nOver bid/ask ${f3(r.p[1])} / ${f3(r.p[2])}\nUnder bid/ask ${f3(r.p[3])} / ${f3(r.p[4])}\nsum_ask ${f3(r.p[5])} · Over 중간가 ${f3(r.p[6])}`}</title>
            </rect>
          ))}
        </svg>
      </div>
      <div className="table-wrap">
        <svg className="chart wide" viewBox={`0 0 ${W} ${H2}`} role="img" aria-label={`${g.title} sum_ask 추이`}>
          <text className="title" x={m.l} y={10}>over_ask + under_ask (구간 최대)</text>
          {ticks(1, sMax, 3).map((v) => (
            <g key={v}>
              <line className="grid-line" x1={m.l} x2={W - m.r} y1={y2(v)} y2={y2(v)} />
              <text x={m.l - 6} y={y2(v)} dy="0.32em" textAnchor="end">{v.toFixed(2)}</text>
            </g>
          ))}
          {marks(ih2)}
          {sums.length === 0 && <text x={m.l + iw / 2} y={m.t + ih2 / 2} textAnchor="middle">poll 이전(과거 중간가만) — 호가 합 없음</text>}
          {segments(rows.map((r) => ({ t: r.t, v: r.p[5] })), gapMs).map((d, k) => (
            <path key={k} d={d.replace(/([ML])(\d+),([\d.]+)/g, (_, c, t, v) => `${c}${x(Number(t)).toFixed(1)},${y2(Number(v)).toFixed(1)}`)}
              fill="none" stroke="var(--s4)" strokeWidth={2} strokeLinejoin="round" />
          ))}
        </svg>
      </div>
    </>
  );
}
