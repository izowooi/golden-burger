import type { Metadata } from "next";
import Link from "next/link";

import {
  CalibrationHeatmap, GameChart, GameLegend, GapLegend, PhaseLegend, ReliabilityCurves, ReliabilityTable, SwingHeat,
  SwingLegend, SwingTable, TailChart, TailLegend, TopGapTable, TRAJ_FAV, TRAJ_WL, TrajectoryChart, TrajLegend, TrajTable,
} from "@/components/explore-charts";
import { Generated, LoadState } from "@/components/ui";
import type { ExploreSport, ExploreSportData, GameDetail, GamesIndex } from "@/lib/explore";
import { EXPLORE_SPORTS, GAME_KEY, PHASE_LABEL, SPORT_LABEL } from "@/lib/explore";
import { kst, num, signedUsd } from "@/lib/format";
import { loadJson } from "@/lib/storage";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "시각화" };

type Props = { searchParams: Promise<{ sport?: string | string[]; phase?: string | string[]; game?: string | string[] }> };
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
const PHASES = ["pre", "early", "mid", "late", "final"];

function href(q: { sport: string; phase: string; game?: string }) {
  const p = new URLSearchParams({ sport: q.sport, phase: q.phase });
  if (q.game) p.set("game", q.game);
  return `/explore?${p.toString()}`;
}

export default async function ExplorePage({ searchParams }: Props) {
  const sp = await searchParams;
  const sport: ExploreSport = (EXPLORE_SPORTS as readonly string[]).includes(one(sp.sport)) ? (one(sp.sport) as ExploreSport) : "soccer";
  const phase = PHASES.includes(one(sp.phase)) ? one(sp.phase) : "late";
  const [agg, idx] = await Promise.all([
    loadJson<ExploreSportData>(`latest/explore/${sport}.json`),
    loadJson<GamesIndex>("latest/explore/games_index.json"),
  ]);
  const games = idx.state === "ok" ? idx.data.games.filter((g) => g.sport === sport) : [];
  const asked = one(sp.game);
  const pick = GAME_KEY.test(asked) && games.some((g) => g.game_key === asked) ? asked
    : (games.find((g) => g.positions > 0) ?? games.find((g) => g.ended_at) ?? games[0])?.game_key;
  const game = pick ? await loadJson<GameDetail>(`latest/explore/games/${pick}.json`) : null;

  return (
    <>
      <h1>시각화</h1>
      <p className="sub">가격이 실제 확률보다 싸거나 비싼 구간, 경기 시간에 따른 가격 경로와 변동성을 눈으로 확인합니다. 모든 시각은 KST.</p>
      <nav className="filters chips" aria-label="종목">
        {EXPLORE_SPORTS.map((s) => (
          <Link key={s} href={href({ sport: s, phase })} className={s === sport ? "chip on" : "chip"} aria-current={s === sport ? "page" : undefined}>{SPORT_LABEL[s]}</Link>
        ))}
      </nav>
      {agg.state !== "ok" ? <LoadState result={agg} /> : <Aggregates d={agg.data} sport={sport} phase={phase} />}
      <GameBrowser sport={sport} phase={phase} idx={idx.state === "ok" ? idx.data : null} games={games} pick={pick} game={game} />
      {agg.state === "ok" && (
        <section className="section card">
          <h2>정의</h2>
          <ul className="notes">{agg.data.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      )}
      <Generated at={agg.state === "ok" ? agg.data.generated_at : null}
        extra={idx.state === "ok" ? <> · 경기 목록 {kst(idx.data.generated_at)} KST · 1시간마다 갱신</> : null} />
    </>
  );
}

function Aggregates({ d, sport, phase }: { d: ExploreSportData; sport: ExploreSport; phase: string }) {
  const s = d.scope;
  const excluded = Object.values(s.excluded_games ?? {}).reduce((a, b) => a + b, 0);
  return (
    <>
      <p className="scope">
        정산된 경기 <strong>{num(s.games)}</strong> · 결과 토큰 {num(s.tokens)} · 1분 가격 {num(s.bars)} · {kst(s.from)} ~ {kst(s.to)}
        {excluded > 0 && <span className="muted"> · 종료 시각 없음/비정상 {num(excluded)}경기 제외</span>}
        {s.leagues && <span className="muted"> · 리그 {s.leagues.join(", ")}</span>}
      </p>

      <section className="section card">
        <h2>① 보정 gap 히트맵 — 가격 구간 × 경기 진행률</h2>
        <p className="caption">색 = 실현 승률 − 평균 가격. <b className="pos-ink">파랑</b>은 가격이 실제보다 낮았던(과소평가, 사면 이득) 구간, <b className="neg-ink">빨강</b>은 비쌌던 구간입니다. 칸 위에 마우스를 올리면 n과 95% CI가 보입니다.</p>
        <GapLegend minN={d.min_n} />
        <CalibrationHeatmap d={d} />
        <p className="figure-caption">
          토큰마다 열당 1개 가격(경기 전 = 마지막 가격, 그 외 = 열의 첫 가격). {sport === "soccer" ? "홈·원정·무승부 각 Yes 토큰이 들어갑니다." : "양 팀 토큰이 모두 들어가므로 지도는 위아래로 대칭이며, 독립 표본 수는 ‘경기’ 수입니다."}
        </p>
        <details><summary>|gap| 상위 셀 표 (n ≥ {d.min_n}, * = CI가 가격을 벗어남)</summary><TopGapTable d={d} /></details>
      </section>

      <section className="section card">
        <h2>② 단계별 신뢰도 곡선</h2>
        <p className="caption">대각선 위 = 과소평가, 아래 = 과대평가. 선택한 단계만 95% Wilson CI 띠와 점을 표시하고 나머지 단계는 옅게 겹칩니다. 속 빈 점은 n &lt; {d.min_n}.</p>
        <PhaseLegend phases={d.phases} selected={phase} href={(p) => href({ sport, phase: p })} />
        <div className="rel-wrap table-wrap"><ReliabilityCurves d={d} selected={phase} /></div>
        <details><summary>{PHASE_LABEL[phase]} 표로 보기</summary><ReliabilityTable d={d} phase={phase} /></details>
      </section>

      <section className="section">
        <h2>③ 가격 경로 — 최종 승자 vs 패자, 경기 전 우세 vs 열세</h2>
        <div className="grid grid-2">
          <div className="card">
            <h3>최종 결과별 평균 가격 (띠 = 25–75%)</h3>
            <TrajLegend series={d.trajectory.series} styles={TRAJ_WL} />
            <TrajectoryChart series={d.trajectory.series} styles={TRAJ_WL} minN={d.trajectory.min_n} label="최종 승자·패자 평균 가격 경로" />
          </div>
          <div className="card">
            <h3>경기 전 우세/열세 × 결과 (평균)</h3>
            <TrajLegend series={d.trajectory.series} styles={TRAJ_FAV} />
            <TrajectoryChart series={d.trajectory.series} styles={TRAJ_FAV} minN={d.trajectory.min_n} label="경기 전 우세·열세 팀의 결과별 평균 가격 경로" />
          </div>
        </div>
        <p className="figure-caption">무승부로 끝난 축구 경기는 제외. n &lt; {d.trajectory.min_n}인 구간은 선을 끊습니다. 100% 이후는 연장·추가시간이 길었던 경기만 남아 구성이 달라집니다.</p>
        <details><summary>표로 보기</summary><TrajTable series={d.trajectory.series} keys={["winner", "loser", "fav_won", "fav_lost", "dog_won", "dog_lost"]} /></details>
      </section>

      <section className="section card">
        <h2>④ 가격 변동 분포 — 1분·10분 |Δp|</h2>
        <p className="caption">열마다 |Δp| 크기별 비율(로그 색). 위쪽 칸이 짙어질수록 큰 점프가 잦다는 뜻입니다. 손절 없는 보유가 견뎌야 하는 흔들림의 크기를 봅니다.</p>
        <SwingLegend />
        <div className="grid grid-2">
          <SwingHeat d={d} window="1m" title="1분 변화 |p(t) − p(t−1분)|" />
          <SwingHeat d={d} window="10m" title="10분 변화 |p(t) − p(t−10분)|" />
        </div>
        <h3 style={{ marginTop: 16 }}>큰 변화의 비율</h3>
        <TailLegend />
        <div className="rel-wrap"><TailChart d={d} /></div>
        <p className="figure-caption">시계가 없는 과거 경기는 실제 경과 시간으로 진행률을 추정하므로 하프타임·인터미션이 특정 열(예: 축구 50–60%)에 몰려 변동이 낮게 보입니다.</p>
        <details><summary>표로 보기 (p50/p90/p99, 큰 변화 비율)</summary><SwingTable d={d} /></details>
      </section>
    </>
  );
}

function GameBrowser({ sport, phase, idx, games, pick, game }: {
  sport: ExploreSport; phase: string; idx: GamesIndex | null; games: GamesIndex["games"]; pick: string | undefined;
  game: Awaited<ReturnType<typeof loadJson<GameDetail>>> | null;
}) {
  return (
    <section className="section card">
      <h2>⑤ 경기 브라우저 — 최근 {idx?.window_days ?? 7}일</h2>
      {!games.length ? <p className="muted">최근 {SPORT_LABEL[sport]} 경기 없음</p> : (
        <form className="filters" method="get" action="/explore">
          <input type="hidden" name="sport" value={sport} />
          <input type="hidden" name="phase" value={phase} />
          <label className="game-pick">
            경기 (KST 시작 · 전략 포지션 수)
            <select name="game" defaultValue={pick}>
              {games.map((g) => (
                <option key={g.game_key} value={g.game_key}>
                  {kst(g.start_time)} · {g.title ?? g.game_key}{g.league && sport === "soccer" ? ` [${g.league}]` : ""}{g.home_score !== null ? ` (${g.home_score}–${g.away_score})` : ""}{g.positions ? ` · 포지션 ${g.positions}` : ""}
                </option>
              ))}
            </select>
          </label>
          <button type="submit">보기</button>
        </form>
      )}
      {game && game.state !== "ok" && <LoadState result={game} />}
      {game && game.state === "ok" && <GameView g={game.data} />}
    </section>
  );
}

function GameView({ g }: { g: GameDetail }) {
  return (
    <>
      <h3 style={{ marginTop: 12 }}>
        {g.title ?? g.game_key} <span className="muted">· {g.league ?? g.sport} · {kst(g.start_time)} 시작{g.status ? ` · ${g.status}` : ""}{g.home_score !== null ? ` · ${g.home_score}–${g.away_score}` : ""}</span>
      </h3>
      <GameLegend g={g} />
      <GameChart g={g} />
      <p className="figure-caption">
        결과별 1분 가격(구간별 최소·최대 보존 다운샘플). 세로선 = 득점(스포츠 WS 수집 이후 경기만), ▲ 진입 / ▽ 청산.
        {g.score_events.length === 0 && " 이 경기는 득점 기록이 없습니다."}
      </p>
      {g.positions.length > 0 && (
        <details open><summary>전략 포지션 {g.positions.length}건</summary>
          <div className="table-wrap">
            <table>
              <thead><tr><th>전략</th><th>결과</th><th>진입</th><th className="n">진입가</th><th>청산</th><th className="n">청산가</th><th>사유</th><th className="n">실현</th></tr></thead>
              <tbody>
                {g.positions.map((p, i) => (
                  <tr key={i}>
                    <td>{p.variant_id} <span className="muted">{p.mode}</span></td><td>{p.outcome ?? p.side}</td>
                    <td>{kst(p.opened_at)}</td><td className="n">{p.entry_price?.toFixed(3) ?? "—"}</td>
                    <td>{kst(p.closed_at)}</td><td className="n">{p.exit_price?.toFixed(3) ?? "—"}</td>
                    <td>{p.exit_reason ?? p.status}</td><td className="n">{signedUsd(p.realized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </>
  );
}
