import type { Metadata } from "next";
import Link from "next/link";

import { SIDE_COLOR } from "@/components/explore-charts";
import { gameDomain, Sparkline } from "@/components/games";
import { Generated, LoadState } from "@/components/ui";
import type { GamesIndex } from "@/lib/explore";
import { EXPLORE_SPORTS, SPORT_LABEL } from "@/lib/explore";
import { DASH, kst, num, pct, usd } from "@/lib/format";
import { loadJson } from "@/lib/storage";
import type { Game24h, Game24hOutcome, Games24h } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "24h 경기" };

const SPORT_ORDER = ["soccer", "mlb", "nba", "nfl", "nhl"];
const sportLabel = (s: string) => (s === "soccer" ? "축구" : (SPORT_LABEL as Record<string, string>)[s] ?? s.toUpperCase());
const SIDE_LABEL: Record<string, string> = { home: "홈", away: "원정", draw: "무승부" };
const p2 = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? DASH : v.toFixed(2));
const pp = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? DASH : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(0)}pp`;

export default async function GamesPage() {
  const [res, idx] = await Promise.all([
    loadJson<Games24h>("latest/games_24h.json"),
    loadJson<GamesIndex>("latest/explore/games_index.json"),
  ]);
  const explorable = new Set(idx.state === "ok" ? (idx.data.games ?? []).map((g) => g.game_key) : []);
  return (
    <>
      <h1>24h 경기</h1>
      <p className="sub">지난 24시간 진행·종료된 추적 경기와 결과별 승률(가격) 움직임. 모든 시각은 KST.</p>
      {res.state !== "ok" ? <LoadState result={res} /> : <Body d={res.data} explorable={explorable} />}
      {res.state === "ok" && <Generated at={res.data.generated_at} extra={<> · 5분마다 갱신</>} />}
    </>
  );
}

function Body({ d, explorable }: { d: Games24h; explorable: Set<string> }) {
  const games = d.games ?? [];
  const excluded = Object.entries(d.excluded_out_of_scope ?? {});
  const bySport = new Map<string, Map<string, Game24h[]>>();
  for (const g of games) {
    const leagues = bySport.get(g.sport) ?? new Map<string, Game24h[]>();
    const key = g.league ?? g.sport;
    leagues.set(key, [...(leagues.get(key) ?? []), g]);
    bySport.set(g.sport, leagues);
  }
  const sports = [...bySport.keys()].sort((a, b) => (SPORT_ORDER.indexOf(a) + 1 || 99) - (SPORT_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b));
  const summary = new Map((d.summary_by_sport ?? []).map((s) => [s.sport, s]));
  return (
    <>
      <p className="scope">
        {d.window ? <>{kst(d.window.since)} ~ {kst(d.window.until)}</> : "기간 정보 없음"} · 경기 <strong>{num(games.length)}</strong>
        {d.notable_swing !== null && d.notable_swing !== undefined && <span className="muted"> · 급변 기준 10분 |Δ| ≥ {pct(d.notable_swing, 0)}</span>}
        {excluded.length > 0 && <span className="muted"> · 범위 밖 축구 제외 {excluded.map(([k, v]) => `${k} ${v}`).join(", ")}</span>}
      </p>
      {d.scope?.note && <p className="scope"><span className="muted">{d.scope.note}</span></p>}
      {d.error && <div className="error section" role="alert"><strong>발행 오류:</strong> {d.error}</div>}
      {summary.size > 0 && (
        <section className="section card">
          <h2>종목 요약</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>종목</th><th className="n">경기</th><th className="n">정산</th><th className="n">우세팀 승률</th>
                  <th className="n">우세팀 경기 전 평균</th><th className="n">평균 10분 최대 변동</th><th className="n">이변</th><th className="n">급변</th>
                </tr>
              </thead>
              <tbody>
                {[...summary.values()].map((s) => (
                  <tr key={s.sport}>
                    <td>{sportLabel(s.sport)}</td><td className="n">{num(s.games)}</td><td className="n">{num(s.resolved)}</td>
                    <td className="n">{pct(s.favourite_win_rate, 0)}{s.favourites_resolved ? <span className="muted"> (n={num(s.favourites_resolved)})</span> : null}</td>
                    <td className="n">{p2(s.favourite_avg_pre_price)}</td><td className="n">{pct(s.avg_max_swing_10m, 0)}</td>
                    <td className="n">{num(s.upsets)}</td><td className="n">{num(s.notable_swings)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {!games.length && !d.error && <div className="empty section">지난 24시간 추적 경기 없음</div>}
      {sports.map((sport) => (
        <section className="section card" key={sport}>
          <h2>{sportLabel(sport)} <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>{num([...bySport.get(sport)!.values()].reduce((a, l) => a + l.length, 0))}경기</span></h2>
          {[...bySport.get(sport)!.entries()].map(([league, list]) => (
            <div key={league}>
              {(sport === "soccer" || league !== sport) && <div className="g-league">{league.toUpperCase()}</div>}
              <div className="tx-games">
                {[...list].sort((a, b) => (a.start_time ?? "").localeCompare(b.start_time ?? "")).map((g) => (
                  <GameCard key={g.game_key} g={g} explorable={explorable.has(g.game_key) && (EXPLORE_SPORTS as readonly string[]).includes(g.sport)} />
                ))}
              </div>
            </div>
          ))}
        </section>
      ))}
      <p className="figure-caption">
        가격 = 결과별 1분 대표 가격(= 내재 확률). 경기 전 = 킥오프 직전 마지막 가격, 최저–최고·10분 변동 = 경기 중 가격만(종료 후 0/1 수렴 제외),
        최종 = 정산 직전(미정산이면 기준 시각) 가격. 스파크라인은 킥오프 1시간 전부터이며 세로 실선 = 득점(색 = 득점 팀), 점선 = 시작/종료, 가운데 선 = 0.5.
      </p>
    </>
  );
}

function resultText(g: Game24h) {
  if (g.result === "draw") return "무승부";
  if (g.result === "home") return `${g.home_team ?? "홈"} 승`;
  if (g.result === "away") return `${g.away_team ?? "원정"} 승`;
  return g.end_source === "running" ? "진행 중" : "미정산";
}

function GameCard({ g, explorable }: { g: Game24h; explorable: boolean }) {
  const outcomes = g.outcomes ?? [];
  const domain = gameDomain(g);
  const score = g.home_score !== null && g.away_score !== null ? `${g.home_score}–${g.away_score}` : null;
  const title = g.title ?? g.game_key;
  return (
    <div className="tx-game">
      <div className="g-head">
        {explorable
          ? <Link className="g-title" href={`/explore?sport=${encodeURIComponent(g.sport)}&phase=late&game=${encodeURIComponent(g.game_key)}`}>{title}</Link>
          : <span className="g-title">{title}</span>}
        <span className="muted">{kst(g.start_time)} 시작</span>
        {score && <strong>{score}</strong>}
        <span>{resultText(g)}</span>
        {g.end_source === "running" && <span className="badge running">LIVE</span>}
        {g.upset && <span className="badge upset" title="경기 전 우세 팀이 이기지 못함 (축구 무승부 포함)">이변</span>}
        {g.notable_swing && <span className="badge swing" title="10분 내 최대 가격 변동">급변 {pp(g.max_swing_10m)}</span>}
        {g.traded && <span className="badge paper">전략 거래</span>}
        {g.volume_usd !== null && <span className="muted">거래량 {usd(g.volume_usd, 0)}</span>}
      </div>
      {!outcomes.length ? <p className="muted" style={{ margin: 0 }}>결과 토큰 없음</p> : (
        <div className="table-wrap">
          <table className="g-table">
            <thead>
              <tr>
                <th>결과</th><th>가격 경로</th><th className="n">경기 전</th><th className="n">최저–최고</th><th className="n">최종</th>
                <th className="n" title="10분 이내 최대 변동 (부호 = 방향)">10분 최대 Δ</th>
              </tr>
            </thead>
            <tbody>
              {outcomes.map((o) => <OutcomeRow key={o.side} g={g} o={o} domain={domain} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function OutcomeRow({ g, o, domain }: { g: Game24h; o: Game24hOutcome; domain: [number, number] | null }) {
  const s = o.swing_10m;
  const swingTitle = s
    ? `${p2(s.from_price)} → ${p2(s.to_price)} · ${kst(s.at)} KST${s.game_minute !== null ? ` · ${s.game_minute}′` : ""}${s.period ? ` (${s.period})` : ""}${s.sources ? ` · ${s.sources}` : ""}`
    : undefined;
  return (
    <tr>
      <td className={o.won ? "won" : undefined}>
        <span style={{ color: SIDE_COLOR[o.side] ?? "var(--muted)" }} aria-hidden="true">●</span>{" "}
        {o.label ?? SIDE_LABEL[o.side] ?? o.side}
        {g.favourite === o.side && <span className="muted"> · 우세</span>}
        {o.won === true && <span className="pos"> ✓</span>}
      </td>
      <td><Sparkline g={g} o={o} domain={domain} /></td>
      <td className="n">{p2(o.pre_price)}</td>
      <td className="n">{o.min_price === null && o.max_price === null ? DASH : `${p2(o.min_price)}–${p2(o.max_price)}`}</td>
      <td className="n">{p2(o.final_price)}</td>
      <td className="n" title={swingTitle}>{pp(s?.delta)}</td>
    </tr>
  );
}
