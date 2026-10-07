"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import {
  BandChart, BandLegend, BandTable, CalibChart, CalibLegend, DistChart, MarketCharts, MarketLegend,
} from "@/components/ou05-charts";
import { Generated, Loading, LoadState } from "@/components/ui";
import { kst, num, pct, usd } from "@/lib/format";
import type { Ou05Index, Ou05Market, Ou05Summary, Quantiles } from "@/lib/ou05";
import { OU05_CID } from "@/lib/ou05";
import { useJson } from "@/lib/storage";

const q3 = (q: Quantiles) => (q ? `${q.p50.toFixed(3)} (${q.p25.toFixed(2)}–${q.p75.toFixed(2)})` : "—");

function href(tier: string, m?: string) {
  const p = new URLSearchParams({ tier });
  if (m) p.set("m", m);
  return `/ou05?${p.toString()}`;
}

export default function Ou05View() {
  const sp = useSearchParams();
  const sum = useJson<Ou05Summary>("latest/ou05/summary.json");
  const idx = useJson<Ou05Index>("latest/ou05/markets_index.json");
  const tiers = sum?.state === "ok" ? sum.data.tiers : [];
  const tier = tiers.some((t) => t.key === sp.get("tier")) ? sp.get("tier") ?? "major" : "major";
  const markets = idx?.state === "ok" ? idx.data.markets : [];
  const asked = sp.get("m") ?? "";
  const pick = OU05_CID.test(asked) && markets.some((x) => x.condition_id === asked) ? asked
    : (markets.find((x) => x.poll_rows > 0 && x.major) ?? markets.find((x) => x.poll_rows > 0) ?? markets[0])?.condition_id;
  const market = useJson<Ou05Market>(pick ? `latest/ou05/markets/${pick}.json` : null);
  if (!sum || !idx) return <><h1>0.5 Over 추이</h1><Loading /></>;

  return (
    <>
      <h1>0.5 Over 추이</h1>
      <p className="sub">
        축구 합계 0.5골 Over/Under 시장의 양쪽 호가를 시장이 열린 순간부터 정산까지 1분 단위로 기록합니다.
        overround = over_ask + under_ask − 1. 모든 시각은 KST.
      </p>
      {sum.state !== "ok" ? <LoadState result={sum} emptyText="아직 집계 전입니다 (수집 잡과 publish 가 돌면 1시간 안에 생깁니다)." /> : (
        <>
          <p className="scope">
            시장 <strong>{num(sum.data.scope.markets)}</strong> (열림 {num(sum.data.scope.open)} · 정산 {num(sum.data.scope.resolved)})
            · poll 행 {num(sum.data.scope.poll_rows)} · 과거 행 {num(sum.data.scope.history_rows)} · {kst(sum.data.scope.from)} ~ {kst(sum.data.scope.to)}
          </p>
          <p className="note">
            Over·Under 호가창은 서로의 거울입니다(under_ask = 1 − over_bid). 그래서 <b>sum_ask − 1 은 Over 의 bid-ask 스프레드</b>이고,
            한쪽만 비싸다는 뜻이 아닙니다. 어느 쪽이 비싼지는 ③ 보정(Over 가격 vs 실제 0:0 빈도)으로 봅니다.
          </p>
          <nav className="filters chips" aria-label="등급" style={{ marginTop: 14 }}>
            {tiers.map((t) => (
              <Link key={t.key} href={href(t.key, pick)} scroll={false} className={t.key === tier ? "chip on" : "chip"} aria-current={t.key === tier ? "page" : undefined}>{t.label}</Link>
            ))}
          </nav>
          <Summary d={sum.data} tier={tier} />
        </>
      )}
      <section className="section card">
        <h2>⑤ 시장 브라우저 — 생애 전체 호가</h2>
        {!markets.length ? <p className="muted">시장 목록 없음</p> : (
          <form className="filters" method="get" action="/ou05">
            <input type="hidden" name="tier" value={tier} />
            <label className="game-pick">
              시장 (KST 킥오프 · 리그 · 결과)
              <select name="m" defaultValue={pick}>
                {markets.map((x) => (
                  <option key={x.condition_id} value={x.condition_id}>
                    {kst(x.game_start)} · {x.title} [{x.league ?? "?"}]{x.final_score ? ` (${x.final_score})` : ""}{x.resolved_over !== null ? (x.resolved_over ? " · Over" : " · Under") : ""}{x.poll_rows ? "" : " · 과거만"}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit">보기</button>
          </form>
        )}
        {pick && !market && <Loading />}
        {market && market.state !== "ok" && <LoadState result={market} />}
        {market && market.state === "ok" && (
          <>
            <h3 style={{ marginTop: 12 }}>
              {market.data.title} <span className="muted">· {market.data.league ?? "?"} · 생성 {kst(market.data.created_at)} · 킥오프 {kst(market.data.game_start)}
                {market.data.final_score ? ` · ${market.data.final_score}` : ""}{market.data.resolved_over !== null ? ` · ${market.data.resolved_over ? "Over" : "Under(0:0)"} 정산` : ""}
                · 거래량 {usd(market.data.volume, 0)}</span>
            </h3>
            <MarketLegend g={market.data} />
            <MarketCharts g={market.data} />
            <p className="figure-caption">
              poll 이전 구간은 과거 중간가(prices-history)만 있어 bid/ask·합계가 없습니다. 끊긴 선 = 수집 공백.
              득점선은 메인 수집기가 경기 상태를 기록한 경기만 표시됩니다.
            </p>
          </>
        )}
      </section>
      {sum.state === "ok" && (
        <section className="section card">
          <h2>정의</h2>
          <ul className="notes">{sum.data.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      )}
      <Generated at={sum.state === "ok" ? sum.data.generated_at : null} extra={<> · 1시간마다 갱신</>} />
    </>
  );
}

function LeagueTable({ rows }: { rows: Ou05Summary["leagues"] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>리그</th><th className="n">시장</th><th className="n">거래량 중앙값</th><th className="n">킥오프 전</th><th className="n">마지막 1시간</th><th className="n">경기 중</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.league}><td>{r.league}{r.tier === "major" ? " ★" : ""}</td><td className="n">{num(r.markets)}</td>
            <td className="n">{usd(r.volume_median, 0)}</td><td className="n">{q3(r.pre)}</td><td className="n">{q3(r.last1h)}</td><td className="n">{q3(r.inplay)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function Summary({ d, tier }: { d: Ou05Summary; tier: string }) {
  const rows = d.overround.filter((r) => r.tier === tier);
  const dist = d.distribution.series.filter((s) => s.tier === tier);
  const calTier = tier === "major" || tier === "other" ? tier : "all";
  const cal = d.calibration.filter((r) => r.tier === calTier);
  const tierLabel = d.tiers.find((t) => t.key === tier)?.label ?? tier;
  return (
    <>
      <section className="section card">
        <h2>① 킥오프까지 시간별 overround — {tierLabel}</h2>
        <p className="caption">구간마다 over_ask + under_ask 의 시간가중 분포. 1.00 에 가까울수록 스프레드가 좁습니다. 점 위에 마우스를 올리면 시장 수·양쪽 호가 비율이 보입니다.</p>
        <BandLegend />
        <BandChart bands={d.bands} rows={rows} />
        <details><summary>표로 보기</summary><BandTable bands={d.bands} rows={rows} /></details>
      </section>

      <section className="section card">
        <h2>② sum_ask 분포 — {tierLabel}</h2>
        {!dist.length ? <p className="muted">데이터 없음</p> : (
          <div className="grid grid-2">{dist.map((s) => <DistChart key={s.phase} edges={d.distribution.edges} s={s} />)}</div>
        )}
      </section>

      <section className="section card">
        <h2>③ Over 가격 보정 — 시장 가격 vs 실제 Over 비율 ({calTier === "all" ? "전체" : calTier === "major" ? "주요 리그" : "기타 리그"})</h2>
        <p className="caption">정산된 시장의 구간별 시간가중 Over 가격(스프레드 ≤ 0.10 인 호가 중간가, 없으면 과거 중간가). 빨간 점이 파란 선보다 아래면 Over 가 비쌌던(0:0 과소평가) 구간입니다. 킥오프 수일 전 구간은 대부분 과거 중간가(호가가 얇아 스프레드를 알 수 없음)라 확률로 읽기 어렵습니다.</p>
        <CalibLegend />
        <CalibChart bands={d.bands} rows={cal} />
        <details><summary>표로 보기</summary>
          <div className="table-wrap">
            <table>
              <thead><tr><th>구간</th><th className="n">시장</th><th className="n">Over 가격</th><th className="n">실제 Over</th><th className="n">95% CI</th><th className="n">gap</th><th className="n">0:0 비율</th></tr></thead>
              <tbody>{d.bands.map((b) => {
                const r = cal.find((x) => x.band === b.key);
                return r ? (
                  <tr key={b.key}><td>{b.label}</td><td className="n">{num(r.n)}</td><td className="n">{r.mean_over.toFixed(3)}</td>
                    <td className="n">{pct(r.over_rate)}</td><td className="n">{pct(r.ci_lo)}–{pct(r.ci_hi)}</td>
                    <td className={`n ${r.gap > 0 ? "pos" : "neg"}`}>{(r.gap * 100).toFixed(1)}pp</td><td className="n">{pct(r.nil_rate)}</td></tr>
                ) : null;
              })}</tbody>
            </table>
          </div>
        </details>
      </section>

      <section className="section grid grid-2">
        <div className="card">
          <h2>④ 리그 비교</h2>
          <p className="caption">시장 5개 이상 리그(★ 주요 리그). sum_ask 중앙값 (25–75%). 주요 리그와 시장 수 상위 기타 리그만 표에, 나머지는 펼쳐 보기.</p>
          <LeagueTable rows={d.leagues.filter((r, i, a) => r.tier === "major" || a.filter((x) => x.tier !== "major" && x.markets > r.markets).length < 10)} />
          <details><summary>모든 리그 ({d.leagues.length})</summary><LeagueTable rows={d.leagues} /></details>
        </div>
        <div className="card">
          <h2>④ 거래량·유동성과 overround</h2>
          <p className="caption">{d.volume_relation.basis ?? ""} · 시장 {num(d.volume_relation.markets)} · Spearman ρ 거래량 {d.volume_relation.spearman_volume?.toFixed(2) ?? "—"}, 유동성 {d.volume_relation.spearman_liquidity?.toFixed(2) ?? "—"}</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>기준</th><th>구간 (USDC)</th><th className="n">시장</th><th className="n">p25</th><th className="n">p50</th><th className="n">p75</th></tr></thead>
              <tbody>{d.volume_relation.rows.map((r, i) => (
                <tr key={i}><td>{r.metric === "volume" ? "거래량" : "유동성"}</td><td>{num(r.lo)}–{r.hi === null ? "" : num(r.hi)}</td>
                  <td className="n">{num(r.markets)}</td><td className="n">{r.p25?.toFixed(3) ?? "—"}</td><td className="n">{r.p50?.toFixed(3) ?? "—"}</td><td className="n">{r.p75?.toFixed(3) ?? "—"}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      </section>
      {Object.keys(d.quality).length > 0 && (
        <p className="figure-caption">
          수집 품질(7일): {Object.entries(d.quality).map(([k, v]) => `${k} ${v.events_7d}회`).join(" · ")}
        </p>
      )}
    </>
  );
}
