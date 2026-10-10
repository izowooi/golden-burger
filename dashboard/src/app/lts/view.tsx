"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import {
  CalibChart, CalibLegend, FillChart, FillLegend, HeatLegend, LtsHeatmap,
} from "@/components/lts-charts";
import { Generated, Loading, LoadState } from "@/components/ui";
import { kst, num, pct } from "@/lib/format";
import type { ArmSelection, LtsCell, LtsGrid, LtsMetric, LtsSport, LtsSummary } from "@/lib/lts";
import { ARM_LABEL, LTS_SPORT_LABEL, LTS_SPORTS, LTS_WAIT_LABEL, LTS_WAITS, METRICS } from "@/lib/lts";
import { useJson } from "@/lib/storage";

const roi = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`);
const RULES = ["R1", "R2", "R3", "R4", "R5", "R6"] as const;

function href(q: { sport: string; wait: string; metric: string; t: string }) {
  return `/lts?${new URLSearchParams(q).toString()}`;
}

export default function LtsView() {
  const sp = useSearchParams();
  const one = (k: string) => sp.get(k) ?? "";
  const sport: LtsSport = (LTS_SPORTS as readonly string[]).includes(one("sport")) ? (one("sport") as LtsSport) : "nfl";
  const wait = (LTS_WAITS as readonly string[]).includes(one("wait")) ? one("wait") : "end";
  const metric: LtsMetric = METRICS.some((m) => m.key === one("metric")) ? (one("metric") as LtsMetric) : "maker_roi";
  const tSel = ["0.6", "0.7", "0.8", "0.9"].includes(one("t")) ? one("t") : "0.9";
  const sum = useJson<LtsSummary>("latest/lts/summary.json");
  const grid = useJson<LtsGrid>(`latest/lts/grid-${sport}.json`);
  const q = { sport, wait, metric, t: tSel };
  if (!sum || !grid) return <><h1>후반 안정성 (LTS)</h1><Loading /></>;

  return (
    <>
      <h1>후반 안정성 (LTS)</h1>
      <p className="sub">
        경기가 일정 진행 시점 T 를 지난 뒤 선두 가격이 임계값 Y 에 들어오면, 다시 Y 아래로 떨어지거나 끝내 역전되는 경우가 얼마나 드문가?
        그리고 수수료 없는 지정가(매수호가보다 한 틱 아래)로 사서 정산까지 들고 가면 돈이 되는가? 사전 등록·결과는
        <span className="mono"> docs/research/hypothesis-lts.md</span>. 과거 재생 결과이며 실현 손익이 아닙니다.
      </p>
      <section className="section card">
        <h2>먼저 읽기 — 왜 ‘최종 승자 vs 패자’ 그림으로는 판단할 수 없나</h2>
        <p className="caption">
          시각화 페이지의 <b>③ 가격 경로 — 최종 승자 vs 패자</b>는 경기를 <b>결과로 먼저 나눈 뒤</b> 평균 경로를 그린 것입니다(사후 정보).
          승자 선은 늘 올라가 보이지만, 경기 중에는 누가 최종 승자인지 모르므로 그 그림으로는 거래할 수 없습니다.
          거래 판단에 맞는 질문은 <b>“(종목, 진행 시점, 지금 가격)이 주어졌을 때 실제 승률이 가격보다 높은가”</b>(보정)이고,
          지정가로 살 때는 여기에 <b>“내 주문이 체결되는 경기가 어떤 경기인가”</b>(역선택)가 더해집니다. 이 페이지는 그 두 가지를 잽니다.
        </p>
      </section>
      <nav className="filters chips" aria-label="종목">
        {LTS_SPORTS.map((s) => (
          <Link key={s} href={href({ ...q, sport: s })} className={s === sport ? "chip on" : "chip"} aria-current={s === sport ? "page" : undefined}>{LTS_SPORT_LABEL[s]}</Link>
        ))}
      </nav>
      {sum.state !== "ok" ? <LoadState result={sum} emptyText="아직 집계 전입니다 (주간 회고 뒤 analyze lts 가 돌면 생깁니다)." /> : (
        <Overview d={sum.data} sport={sport} />
      )}
      {grid.state !== "ok" ? <LoadState result={grid} /> : sum.state === "ok" && (
        <Grid d={grid.data} s={sum.data} sport={sport} wait={wait} metric={metric} tSel={tSel} q={q} />
      )}
      {sum.state === "ok" && (
        <section className="section card">
          <h2>정의</h2>
          <ul className="notes">{sum.data.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      )}
      <Generated at={sum.state === "ok" ? sum.data.generated_at : null} extra={<> · 주 1회(월요일 주간 회고 뒤) 갱신</>} />
    </>
  );
}

function Overview({ d, sport }: { d: LtsSummary; sport: LtsSport }) {
  const s = d.sports[sport];
  if (!s || !s.games) return <p className="muted">이 종목 표본 없음</p>;
  return (
    <>
      <p className="scope">
        경기 <strong>{num(s.games)}</strong> · {kst(s.first_start)} ~ {kst(s.last_start)} · H1/H2 경계 {kst(s.split)}
        · 최소 체결 {s.rule?.backtest_min_n}건(반기 {s.rule?.backtest_min_half_n}) · 중앙 경기 길이 {s.median_game_min_used}분
        · 120칸 중 R1 통과 {num(s.r1_cells)} · R1–R6 통과 {num(s.passing_cells)}
      </p>
      <section className="section card">
        <h2>A/B 선택 칸 (H1 데이터만으로 고름 → H2 로 평가)</h2>
        <p className="caption">
          arm A(king)는 진행 80–90%, arm B(queen)는 60–70% 층에서 H1 의 이웃 포함 maker ROI 가 가장 높은 칸입니다. live 는 R1–R6 통과,
          다중 비교 점검(H1 상위 {s.mc?.top ?? 20}칸의 H2 중앙값 ≥ 0), 엔진 재생(R7), 경기 중 지정가 확인이 모두 될 때만입니다.
          다중 비교 점검: H2 중앙값 <b className={(s.mc?.h2_median ?? -1) >= 0 ? "pos" : "neg"}>{roi(s.mc?.h2_median)}</b>
          ({s.mc?.h2_nonneg ?? 0}/{s.mc?.h2_n ?? 0}칸 H2 ≥ 0).
        </p>
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>arm</th><th>T</th><th className="n">Y</th><th>대기</th><th className="n">신호</th><th className="n">보정 gap</th>
              <th className="n">체결 n</th><th className="n">체결률 승/패</th><th className="n">maker ROI</th><th className="n">H1 / H2</th>
              <th className="n">taker ROI</th><th>규칙</th>
            </tr></thead>
            <tbody>{Object.entries(s.arms ?? {}).map(([arm, a]) => <ArmRow key={arm} arm={arm} a={a} />)}</tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function ArmRow({ arm, a }: { arm: string; a: ArmSelection | null }) {
  if (!a) return <tr><td>{ARM_LABEL[arm] ?? arm}</td><td colSpan={11} className="muted">후보 칸 없음</td></tr>;
  const c = a.cell, m = c.main;
  return (
    <tr>
      <td>{ARM_LABEL[arm] ?? arm}</td>
      <td>{Math.round(a.progress * 100)}% ({a.t_min}분)</td>
      <td className="n">{a.y.toFixed(2)}</td>
      <td>{LTS_WAIT_LABEL[a.wait] ?? a.wait}</td>
      <td className="n">{num(c.signals)}</td>
      <td className="n">{c.gap === null ? "—" : `${(c.gap * 100).toFixed(1)}pp`}</td>
      <td className="n">{num(m.n)}</td>
      <td className="n">{pct(m.fill_rate_win)} / {pct(m.fill_rate_loss)}</td>
      <td className={`n ${(m.roi ?? 0) >= 0 ? "pos" : "neg"}`}>{roi(m.roi)}</td>
      <td className="n">{roi(m.h1.roi)} / {roi(m.h2.roi)}</td>
      <td className="n">{roi(c.taker.roi)}</td>
      <td>{RULES.map((k) => <span key={k} className={a.rules[k] ? "rules-ok" : "rules-no"}>{k}{a.rules[k] ? "✓" : "✗"} </span>)}</td>
    </tr>
  );
}

function Grid({ d, s, sport, wait, metric, tSel, q }: {
  d: LtsGrid; s: LtsSummary; sport: LtsSport; wait: string; metric: LtsMetric; tSel: string;
  q: { sport: string; wait: string; metric: string; t: string };
}) {
  const cells = d.cells.filter((c) => c.wait === wait);
  const minFills = s.sports[sport]?.rule?.backtest_min_half_n ?? 10;
  const meta = METRICS.find((m) => m.key === metric);
  const tCells = cells.filter((c) => String(c.progress) === tSel);
  return (
    <>
      <section className="section card">
        <h2>① 진행 시점 × 임계값 히트맵 — {LTS_SPORT_LABEL[sport]}</h2>
        <nav className="filters chips" aria-label="지표">
          {METRICS.map((m) => (
            <Link key={m.key} href={href({ ...q, metric: m.key })} scroll={false} className={m.key === metric ? "chip on" : "chip"}>{m.label}</Link>
          ))}
        </nav>
        <nav className="filters chips" aria-label="대기 시간" style={{ marginTop: 6 }}>
          {LTS_WAITS.map((w) => (
            <Link key={w} href={href({ ...q, wait: w })} scroll={false} className={w === wait ? "chip on" : "chip"}>{LTS_WAIT_LABEL[w]}</Link>
          ))}
        </nav>
        <p className="caption" style={{ marginTop: 8 }}>{meta?.hint}. 칸 위에 마우스를 올리면 신호 수, 실현 승률, 체결률(승/패), H1/H2, taker 비교가 보입니다.</p>
        <HeatLegend metric={metric} minFills={minFills} />
        <LtsHeatmap cells={cells} metric={metric} progress={s.constants.progress} thresholds={s.constants.thresholds}
          minFills={minFills} label={`${LTS_SPORT_LABEL[sport]} ${meta?.label} 히트맵`} />
      </section>
      <nav className="filters chips" aria-label="진행 시점">
        {s.constants.progress.map((p) => (
          <Link key={p} href={href({ ...q, t: String(p) })} scroll={false} className={String(p) === tSel ? "chip on" : "chip"}>
            T {Math.round(p * 100)}%
          </Link>
        ))}
      </nav>
      <section className="section grid grid-2">
        <div className="card">
          <h2>② 보정 — 가격 vs 실현 승률 (T {Math.round(Number(tSel) * 100)}%, {LTS_WAIT_LABEL[wait]})</h2>
          <p className="caption">
            파란 점이 대각선 위면 그 신호의 선두는 가격보다 자주 이겼습니다(과소평가). 주황 사각형은 <b>실제로 체결된</b> 지정가 거래만의 승률입니다.
            주황이 파랑보다 한참 아래면, 싸게 사도 체결이 지는 경기에 몰린다(역선택)는 뜻입니다.
          </p>
          <CalibLegend />
          <CalibChart cells={tCells} label="가격 대비 실현 승률" />
        </div>
        <div className="card">
          <h2>③ 역선택 — 결과별 체결률</h2>
          <p className="caption">
            매수호가 한 틱 아래 지정가는 가격이 내려와야 체결됩니다. 선두가 끝내 지는 경기는 가격이 0 으로 내려가므로 거의 모두 체결되고,
            이기는 경기는 일부만 체결됩니다. 두 점 사이가 멀수록 maker 손익이 taker 보다 나빠집니다.
          </p>
          <FillLegend />
          <FillChart cells={tCells} thresholds={s.constants.thresholds} label="결과별 체결률" />
        </div>
      </section>
      <section className="section card">
        <details>
          <summary>표로 보기 (T {Math.round(Number(tSel) * 100)}%, {LTS_WAIT_LABEL[wait]})</summary>
          <CellTable cells={tCells} />
        </details>
      </section>
    </>
  );
}

function CellTable({ cells }: { cells: LtsCell[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>
          <th className="n">Y</th><th className="n">신호</th><th className="n">평균 가격</th><th className="n">실현 승률 (95% CI)</th>
          <th className="n">재이탈</th><th className="n">역전</th><th className="n">낙폭 중앙/p90</th><th className="n">체결 n</th>
          <th className="n">체결률 승/패</th><th className="n">maker ROI</th><th className="n">H1 / H2</th><th className="n">taker ROI</th>
          <th className="n">스트레스(0.02 / 엄격)</th><th>규칙</th>
        </tr></thead>
        <tbody>{cells.map((c) => (
          <tr key={c.y}>
            <td className="n">{c.y.toFixed(2)}</td><td className="n">{num(c.signals)}</td><td className="n">{c.mean_price?.toFixed(3) ?? "—"}</td>
            <td className="n">{pct(c.win_rate)} ({pct(c.win_ci[0])}–{pct(c.win_ci[1])})</td>
            <td className="n">{pct(c.rebreak_rate)}</td><td className="n">{pct(c.reversal_rate)}</td>
            <td className="n">{c.mdd_median ?? "—"} / {c.mdd_p90 ?? "—"}</td><td className="n">{num(c.main.n)}</td>
            <td className="n">{pct(c.main.fill_rate_win)} / {pct(c.main.fill_rate_loss)}</td>
            <td className={`n ${(c.main.roi ?? 0) >= 0 ? "pos" : "neg"}`}>{roi(c.main.roi)}</td>
            <td className="n">{roi(c.main.h1.roi)} / {roi(c.main.h2.roi)}</td><td className="n">{roi(c.taker.roi)}</td>
            <td className="n">{roi(c.spread02.roi)} / {roi(c.strict.roi)}</td>
            <td>{c.rules ? RULES.filter((k) => c.rules![k]).join(" ") || "—" : "—"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
