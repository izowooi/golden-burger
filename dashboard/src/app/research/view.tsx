"use client";


import { GroupedBars, InlineBar, Legend, ReliabilityDiagram, reliabilityDomainLo, SENS_SERIES, STATE_SERIES } from "@/components/charts";
import { Generated, Loading, LoadState } from "@/components/ui";
import { kst, num, pct, signedPct, signedUsd, tone, usd } from "@/lib/format";
import { useJson } from "@/lib/storage";
import type { Research } from "@/lib/types";

const PHASES = ["all", "pre", "early", "mid", "late", "final"];
const PHASE_LABEL: Record<string, string> = { all: "전체", pre: "경기 전", early: "초반 <30%", mid: "중반 <60%", late: "후반 <85%", final: "막판 ≥85%" };
const phaseLabel = (p: string) => PHASE_LABEL[p] ?? p;
const phaseRank = (p: string) => (PHASES.indexOf(p) < 0 ? 99 : PHASES.indexOf(p));
const bucketStart = (b: string) => parseFloat(b) || 0;

function groupBy<T>(xs: T[], key: (x: T) => string) {
  const m = new Map<string, T[]>();
  for (const x of xs) m.set(key(x), [...(m.get(key(x)) ?? []), x]);
  return m;
}

export default function ResearchView() {
  const res = useJson<Research>("latest/research.json");
  if (!res) return <><h1>연구</h1><Loading /></>;
  return (
    <>
      <h1>연구</h1>
      <p className="sub">스포츠 인게임 가격의 보정(calibration)·이벤트 민감도·스테이크 안정성</p>
      {res.state !== "ok" ? <LoadState result={res} /> : <ResearchBody r={res.data} />}
    </>
  );
}

function ResearchBody({ r }: { r: Research }) {
  return (
    <>
      <Dataset r={r} />
      <Calibration r={r} />
      <Brier r={r} />
      <Sensitivity r={r} />
      <ScoreState r={r} />
      <StakeTiers r={r} />
      {r.notes?.length ? (
        <section className="section card">
          <h2>메모</h2>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{r.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      ) : null}
      <Generated
        at={r.generated_at}
        extra={r.analysis_generated_at ? <> · 보정 분석 {kst(r.analysis_generated_at.calibration)} · 이벤트 분석 {kst(r.analysis_generated_at.events)}</> : null}
      />
    </>
  );
}

function Dataset({ r }: { r: Research }) {
  const d = r.dataset;
  if (!d) return <section className="card"><h2>데이터셋</h2><p className="muted">아직 데이터 없음</p></section>;
  const sports = [...(d.by_sport ?? [])].sort((a, b) => (b.games ?? 0) - (a.games ?? 0));
  const maxGames = Math.max(0, ...sports.map((s) => s.games ?? 0));
  return (
    <section className="card">
      <h2>데이터셋 범위</h2>
      <div className="grid grid-tiles" style={{ marginBottom: 12 }}>
        <div className="tile"><div className="label">경기</div><div className="value">{num(d.games)}</div></div>
        <div className="tile"><div className="label">마켓</div><div className="value">{num(d.markets)}</div></div>
        <div className="tile"><div className="label">가격 바</div><div className="value">{num(d.price_bars)}</div></div>
        <div className="tile"><div className="label">기간 (KST)</div><div className="meta" style={{ fontSize: 13 }}>{kst(d.from)}<br />~ {kst(d.to)}</div></div>
      </div>
      {sports.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>종목</th><th className="n">경기</th><th className="n">비중</th></tr></thead>
            <tbody>
              {sports.map((s) => (
                <tr key={s.sport}>
                  <td>{s.sport}</td>
                  <td className="n">{num(s.games)}<InlineBar value={s.games} max={maxGames} /></td>
                  <td className="n">{pct(d.games && s.games !== null ? s.games / d.games : null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Calibration({ r }: { r: Research }) {
  const cal = r.calibration ?? [];
  const bySport = groupBy(cal, (c) => c.sport);
  const lows = cal.flatMap((c) => c.buckets.filter((b) => b.win_rate !== null).flatMap((b) => [b.p_lo, b.ci_lo ?? (b.win_rate as number), b.win_rate as number]));
  const domainLo = lows.length ? reliabilityDomainLo(lows) : 0;
  return (
    <section className="section card">
      <h2>보정 신뢰도 다이어그램</h2>
      <p className="sub" style={{ marginBottom: 12 }}>
        x = 가격 구간 평균 implied probability, y = 실현 승률(점 크기 ∝ 표본 수, 세로선 = Wilson 95% CI). 점선 y = x 위의 점은 시장이 승률을 과소평가(저평가)했음을 뜻한다. 국면 % = 정규 시간 대비 경과 비율.
      </p>
      {!cal.length ? <p className="muted">아직 데이터 없음</p> : (
        [...bySport.entries()].map(([sport, rows]) => (
          <div key={sport} style={{ marginBottom: 20 }}>
            <h3>{sport}</h3>
            <div className="small-multiples">
              {[...rows].sort((a, b) => phaseRank(a.phase) - phaseRank(b.phase)).map((c) => (
                <div key={c.phase}>
                  <ReliabilityDiagram title={`${sport} · ${phaseLabel(c.phase)}`} buckets={c.buckets} domainLo={domainLo} />
                </div>
              ))}
            </div>
          </div>
        ))
      )}
      {cal.length > 0 && (
        <details>
          <summary>표로 보기</summary>
          <div className="table-wrap">
            <table>
              <thead><tr><th>종목</th><th>국면</th><th>가격 구간</th><th className="n">n</th><th className="n">평균가</th><th className="n">실현 승률</th><th className="n">95% CI</th><th className="n">gap</th></tr></thead>
              <tbody>
                {cal.flatMap((c) => c.buckets.map((b) => (
                  <tr key={`${c.sport}-${c.phase}-${b.p_lo}`}>
                    <td>{c.sport}</td><td>{phaseLabel(c.phase)}</td><td>{b.p_lo.toFixed(2)}–{b.p_hi.toFixed(2)}</td>
                    <td className="n">{num(b.n)}</td><td className="n">{num(b.mean_price, 3)}</td><td className="n">{pct(b.win_rate)}</td>
                    <td className="n">{pct(b.ci_lo)} – {pct(b.ci_hi)}</td><td className={`n ${tone(b.gap)}`}>{signedPct(b.gap)}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

function Brier({ r }: { r: Research }) {
  const rows = r.brier ?? [];
  const phases = [...new Set(rows.map((b) => b.phase))].sort((a, b) => phaseRank(a) - phaseRank(b));
  const sports = [...new Set(rows.map((b) => b.sport))];
  const cell = new Map(rows.map((b) => [`${b.sport}|${b.phase}`, b]));
  return (
    <section className="section card">
      <h2>Brier score</h2>
      <p className="sub" style={{ marginBottom: 12 }}>낮을수록 가격이 결과를 잘 예측. 괄호는 표본 수.</p>
      {!rows.length ? <p className="muted">아직 데이터 없음</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>종목</th>{phases.map((p) => <th key={p} className="n">{phaseLabel(p)}</th>)}</tr></thead>
            <tbody>
              {sports.map((s) => (
                <tr key={s}>
                  <td>{s}</td>
                  {phases.map((p) => {
                    const b = cell.get(`${s}|${p}`);
                    return <td key={p} className="n">{b ? <>{num(b.brier, 4)} <span className="muted">({num(b.n)})</span></> : "—"}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Sensitivity({ r }: { r: Research }) {
  const rows = r.event_sensitivity ?? [];
  const groups = groupBy(rows, (e) => `${e.sport}|${e.event}`);
  return (
    <section className="section card">
      <h2>이벤트 민감도</h2>
      <p className="sub" style={{ marginBottom: 8 }}>
        이벤트(득점 등) 직후 가격 점프 크기와 5·10분 뒤 되돌림, 경기 분 구간별. 단위 pp(확률 퍼센트포인트). 음수 되돌림 = 점프 방향의 반대로 복귀.
      </p>
      {!rows.length ? <p className="muted">아직 데이터 없음</p> : (
        <>
          <Legend series={SENS_SERIES} />
          <div className="small-multiples">
            {[...groups.entries()].map(([k, list]) => {
              const [sport, event] = k.split("|");
              const sorted = [...list].sort((a, b) => bucketStart(a.minute_bucket) - bucketStart(b.minute_bucket));
              return (
                <GroupedBars
                  key={k}
                  title={`${sport} · ${event}`}
                  series={SENS_SERIES}
                  rows={sorted.map((e) => ({ bucket: e.minute_bucket, n: e.n, values: { mean_abs_jump: e.mean_abs_jump, reversion_5m: e.reversion_5m, reversion_10m: e.reversion_10m } }))}
                />
              );
            })}
          </div>
          <details>
            <summary>표로 보기</summary>
            <div className="table-wrap">
              <table>
                <thead><tr><th>종목</th><th>이벤트</th><th>분</th><th className="n">n</th><th className="n">고립 n</th><th className="n">직전가</th><th className="n">평균 |점프|</th><th className="n">평균 점프</th><th className="n">중앙 점프</th><th className="n">1분</th><th className="n">5분 되돌림</th><th className="n">10분 되돌림</th><th className="n">고점 분</th></tr></thead>
                <tbody>
                  {rows.map((e, i) => (
                    <tr key={i}>
                      <td>{e.sport}</td><td>{e.event}</td><td>{e.minute_bucket}</td><td className="n">{num(e.n)}</td>
                      <td className="n">{num(e.n_isolated ?? null)}</td><td className="n">{num(e.mean_pre_price ?? null, 3)}</td>
                      <td className="n">{pct(e.mean_abs_jump)}</td><td className="n">{signedPct(e.mean_jump ?? null)}</td><td className="n">{signedPct(e.median_jump)}</td>
                      <td className="n">{signedPct(e.reversion_1m ?? null)}</td>
                      <td className="n">{signedPct(e.reversion_5m)}</td><td className="n">{signedPct(e.reversion_10m)}</td>
                      <td className="n">{num(e.mean_peak_minute ?? null, 1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </section>
  );
}

function StakeTiers({ r }: { r: Research }) {
  const rows = [...(r.stake_tiers ?? [])].sort((a, b) => (a.tier_usdc ?? 0) - (b.tier_usdc ?? 0));
  const maxRoi = Math.max(0, ...rows.map((t) => Math.abs(t.roi ?? 0)));
  return (
    <section className="section card">
      <h2>스테이크 티어 안정성</h2>
      <p className="sub" style={{ marginBottom: 12 }}>스테이크 크기가 커져도 ROI·변동성이 유지되는지(체결 슬리피지·깊이 한계) 확인.</p>
      {!rows.length ? <p className="muted">아직 데이터 없음</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th className="n">티어</th><th className="n">전략 수</th><th className="n">거래</th><th className="n">ROI</th><th className="n">손익 표준편차</th><th className="n">최대 낙폭</th><th className="n">Sharpe-like</th></tr></thead>
            <tbody>
              {rows.map((t, i) => (
                <tr key={i}>
                  <td className="n"><strong>{usd(t.tier_usdc, 0)}</strong></td>
                  <td className="n">{num(t.variants)}</td>
                  <td className="n">{num(t.trades)}</td>
                  <td className={`n ${tone(t.roi)}`}>{signedPct(t.roi, 2)}<InlineBar value={t.roi} max={maxRoi} /></td>
                  <td className="n">{usd(t.pnl_std)}</td>
                  <td className="n">{t.max_drawdown === null ? "—" : signedUsd(-Math.abs(t.max_drawdown))}</td>
                  <td className="n">{num(t.sharpe_like, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const STATE_LABEL: Record<string, string> = { trailing: "뒤짐", level: "동점", leading: "앞섬" };

function ScoreState({ r }: { r: Research }) {
  const rows = r.event_by_score_state ?? [];
  const groups = groupBy(rows, (e) => `${e.sport}|${e.event}`);
  return (
    <section className="section card">
      <h2>득점 직전 스코어 상황별 점프</h2>
      <p className="sub" style={{ marginBottom: 8 }}>
        득점한 팀 기준, 득점 직전 스코어 상태(뒤짐·동점·앞섬)별 평균 |점프| (pp). 같은 분 구간 안에서 상태별로 비교한다.
      </p>
      {!rows.length ? <p className="muted">아직 데이터 없음</p> : (
        <>
          <Legend series={STATE_SERIES} />
          <div className="small-multiples">
            {[...groups.entries()].map(([k, list]) => {
              const [sport, event] = k.split("|");
              const buckets = groupBy(list, (e) => e.minute_bucket);
              const gr = [...buckets.entries()]
                .sort((a, b) => bucketStart(a[0]) - bucketStart(b[0]))
                .map(([bucket, xs]) => ({
                  bucket,
                  n: xs.reduce((acc, x) => acc + (x.n ?? 0), 0),
                  values: Object.fromEntries(xs.map((x) => [x.score_state, x.mean_abs_jump])),
                }));
              return <GroupedBars key={k} title={`${sport} · ${event}`} series={STATE_SERIES} rows={gr} />;
            })}
          </div>
          <details>
            <summary>표로 보기</summary>
            <div className="table-wrap">
              <table>
                <thead><tr><th>종목</th><th>이벤트</th><th>분</th><th>상태</th><th className="n">n</th><th className="n">평균 |점프|</th><th className="n">평균 점프</th><th className="n">5분 되돌림</th><th className="n">10분 되돌림</th></tr></thead>
                <tbody>
                  {rows.map((e, i) => (
                    <tr key={i}>
                      <td>{e.sport}</td><td>{e.event}</td><td>{e.minute_bucket}</td><td>{STATE_LABEL[e.score_state] ?? e.score_state}</td>
                      <td className="n">{num(e.n)}</td><td className="n">{pct(e.mean_abs_jump)}</td><td className="n">{signedPct(e.mean_jump ?? null)}</td>
                      <td className="n">{signedPct(e.reversion_5m)}</td><td className="n">{signedPct(e.reversion_10m)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
