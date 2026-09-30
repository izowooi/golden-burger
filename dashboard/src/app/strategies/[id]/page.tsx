import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { EquityChart, InlineBar } from "@/components/charts";
import { TxGroups, TxLegend, TxTotalsTable } from "@/components/transactions";
import { Generated, LoadState, ModeBadge } from "@/components/ui";
import { kst, num, paramValue, pct, signedPct, signedUsd, tone, usd } from "@/lib/format";
import { loadJson, STRATEGY_ID } from "@/lib/storage";
import type { BreakdownRow, Overview, ParamVersion, StakeEvent, StrategyDetail, StrategySummary, Transaction } from "@/lib/types";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return { title: id };
}

export default async function StrategyPage({ params }: Props) {
  const { id } = await params;
  if (!STRATEGY_ID.test(id)) notFound();
  const [ov, det, tx] = await Promise.all([
    loadJson<Overview>("latest/overview.json"),
    loadJson<StrategyDetail>(`latest/strategies/${id}.json`),
    loadJson<Transaction[]>("latest/transactions_24h.json"),
  ]);
  const myTx = tx.state === "ok" && Array.isArray(tx.data) ? tx.data.filter((t) => t.variant_id === id) : [];
  const summary = ov.state === "ok" ? ov.data.strategies?.find((s) => s.id === id) ?? null : null;
  const detail = det.state === "ok" ? det.data : null;
  const variant = detail?.variant ?? null;
  const hypothesis = summary?.hypothesis ?? (typeof variant?.hypothesis === "string" ? variant.hypothesis : null);
  const params_ = summary?.params ?? (variant?.params as Record<string, unknown> | undefined) ?? null;

  return (
    <>
      <p className="sub" style={{ margin: "16px 0 0" }}><Link href="/">← 개요</Link></p>
      <h1 style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        {id} {summary && <ModeBadge mode={summary.mode} />}
      </h1>
      <p className="sub">
        {summary ? <>{summary.family ?? "—"} · 계정 {summary.account ?? "—"} · {summary.sports?.join(", ") || "—"} · 스테이크 {usd(summary.stake_usdc, 0)}</> : "개요에 없는 전략"}
      </p>

      {summary && <SummaryTiles s={summary} />}

      <section className="section grid grid-2">
        <div className="card">
          <h2>가설</h2>
          {hypothesis ? <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{hypothesis}</p> : <p className="muted">—</p>}
        </div>
        <div className="card">
          <h2>현재 파라미터</h2>
          {params_ && Object.keys(params_).length ? (
            <dl className="kv">
              {Object.entries(params_).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{paramValue(v)}</dd></div>
              ))}
            </dl>
          ) : <p className="muted">—</p>}
        </div>
      </section>

      {det.state !== "ok" ? (
        <div className="section"><LoadState result={det} /></div>
      ) : (
        <DetailBody d={det.data} />
      )}
      <section className="section card">
        <h2>지난 24시간 거래</h2>
        {tx.state !== "ok" ? <LoadState result={tx} /> : !myTx.length ? <p className="muted" style={{ margin: 0 }}>지난 24시간 거래 없음</p> : (
          <>
            <TxTotalsTable rows={myTx} />
            <TxLegend />
            <TxGroups rows={myTx} showVariant={false} />
          </>
        )}
      </section>
      {detail && variant && (
        <section className="section card">
          <details>
            <summary>전체 variant 설정 (JSON)</summary>
            <pre className="mono" style={{ overflowX: "auto", margin: "8px 0 0" }}>{JSON.stringify(variant, null, 2)}</pre>
          </details>
        </section>
      )}
      <Generated at={detail?.generated_at ?? (ov.state === "ok" ? ov.data.generated_at : null)} />
    </>
  );
}

function SummaryTiles({ s }: { s: StrategySummary }) {
  const ledger = s.pnl_mode ? `${s.pnl_mode} 원장` : "";
  const tiles: [string, string, string][] = [
    [`오늘 ${ledger}`, signedUsd(s.pnl?.today), tone(s.pnl?.today)],
    ["7일", signedUsd(s.pnl?.d7), tone(s.pnl?.d7)],
    ["30일", signedUsd(s.pnl?.d30), tone(s.pnl?.d30)],
    ["누적", signedUsd(s.pnl?.all), tone(s.pnl?.all)],
    ["승률", `${pct(s.win_rate)}`, ""],
    ["ROI", signedPct(s.roi), tone(s.roi)],
    ["거래", `${num(s.trades?.all)} (${num(s.trades?.wins)}승 ${num(s.trades?.losses)}패)`, ""],
    ["오픈", `${num(s.open_positions)} · ${usd(s.open_cost_usdc)}`, ""],
  ];
  return (
    <section className="grid grid-tiles">
      {tiles.map(([l, v, t]) => (
        <div className="tile" key={l}><div className="label">{l}</div><div className={`value ${t}`}>{v}</div></div>
      ))}
    </section>
  );
}

function diffParams(cur: Record<string, unknown> | null, prev: Record<string, unknown> | null) {
  if (!cur) return [];
  if (!prev) return Object.entries(cur).map(([k, v]) => `${k}=${paramValue(v)}`);
  const keys = new Set([...Object.keys(cur), ...Object.keys(prev)]);
  const out: string[] = [];
  for (const k of keys) {
    const a = JSON.stringify(prev[k]), b = JSON.stringify(cur[k]);
    if (a !== b) out.push(`${k} ${paramValue(prev[k])}→${paramValue(cur[k])}`);
  }
  return out;
}

type TimelineItem = { at: string | null; kind: "param"; v: ParamVersion; changes: string[] } | { at: string | null; kind: "stake"; e: StakeEvent };

function Timeline({ d }: { d: StrategyDetail }) {
  const versions = [...(d.param_history ?? [])].sort((a, b) => a.version - b.version);
  const items: TimelineItem[] = [
    ...versions.map((v, i) => ({ at: v.at, kind: "param" as const, v, changes: diffParams(v.params, i ? versions[i - 1].params : null) })),
    ...(d.stake_events ?? []).map((e) => ({ at: e.at, kind: "stake" as const, e })),
  ].sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""));
  if (!items.length) return <p className="muted">아직 데이터 없음</p>;
  return (
    <ul className="timeline">
      {items.map((it, i) =>
        it.kind === "param" ? (
          <li key={i}>
            <div className="when">{kst(it.at)} · v{it.v.version} · {it.v.author ?? "—"} {it.v.mode && <ModeBadge mode={it.v.mode} />}</div>
            <div className="mono">{it.changes.length ? it.changes.join(", ") : "파라미터 변경 없음"}{it.v.stake_usdc !== null && ` · stake ${usd(it.v.stake_usdc, 0)}`}</div>
            {it.v.rationale && <div className="muted" style={{ fontSize: 12 }}>{it.v.rationale}</div>}
          </li>
        ) : (
          <li key={i} className="stake">
            <div className="when">{kst(it.at)} · 스테이크 변경</div>
            <div>
              <strong>{usd(it.e.from_usdc, 0)} → {usd(it.e.to_usdc, 0)}</strong>
              {(it.e.from_mode || it.e.to_mode) && it.e.from_mode !== it.e.to_mode && (
                <span className="muted"> · {it.e.from_mode ?? "—"} → {it.e.to_mode ?? "—"}</span>
              )}
            </div>
            {it.e.reason && <div className="muted" style={{ fontSize: 12 }}>{it.e.reason}</div>}
          </li>
        ),
      )}
    </ul>
  );
}

function Breakdown({ title, rows, keyLabel }: { title: string; rows: BreakdownRow[] | null | undefined; keyLabel: string }) {
  const list = rows ?? [];
  const showWin = list.some((r) => r.win_rate !== undefined && r.win_rate !== null);
  const showRoi = list.some((r) => r.roi !== undefined && r.roi !== null);
  const max = Math.max(0, ...list.map((r) => Math.abs(r.pnl ?? 0)));
  return (
    <div className="card">
      <h3>{title}</h3>
      {!list.length ? <p className="muted">아직 데이터 없음</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>{keyLabel}</th><th className="n">n</th><th className="n">손익</th>{showWin && <th className="n">승률</th>}{showRoi && <th className="n">ROI</th>}</tr></thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.key}>
                  <td>{r.key}</td>
                  <td className="n">{num(r.n)}</td>
                  <td className={`n ${tone(r.pnl)}`}>{signedUsd(r.pnl)}<InlineBar value={r.pnl} max={max} /></td>
                  {showWin && <td className="n">{pct(r.win_rate ?? null)}</td>}
                  {showRoi && <td className={`n ${tone(r.roi ?? null)}`}>{signedPct(r.roi ?? null)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DetailBody({ d }: { d: StrategyDetail }) {
  const open = d.open_positions ?? [];
  const closed = d.recent_positions ?? [];
  const curve = d.equity_curve ?? [];
  return (
    <>
      <section className="section card">
        <h2>누적 실현 손익 {d.equity_mode && <span className={`badge ${d.equity_mode}`}>{d.equity_mode} 원장</span>}</h2>
        {curve.length >= 2 ? <EquityChart points={curve} /> : <p className="muted">아직 데이터 없음</p>}
      </section>

      <section className="section card">
        <h2>파라미터 · 스테이크 이력</h2>
        <Timeline d={d} />
      </section>

      <section className="section card">
        <h2>오픈 포지션 ({open.length})</h2>
        {!open.length ? <p className="muted">오픈 포지션 없음</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>진입</th><th>상태</th><th>종목</th><th>경기</th><th>결과</th><th className="n">분</th><th className="n">진입가</th><th className="n">현재가</th><th className="n">수량</th><th className="n">원가</th><th className="n">평가손익</th></tr></thead>
              <tbody>
                {open.map((p, i) => (
                  <tr key={i}>
                    <td className="muted">{kst(p.opened_at)}</td>
                    <td>{p.mode && <ModeBadge mode={p.mode} />} <span className="muted">{p.status ?? ""}</span></td>
                    <td>{p.sport ?? "—"}{p.league && <span className="muted"> · {p.league}</span>}</td>
                    <td className="wrap">{p.title ?? "—"}</td>
                    <td>{p.outcome ?? "—"}</td>
                    <td className="n">{num(p.game_minute)}</td>
                    <td className="n">{num(p.entry_price, 3)}</td>
                    <td className="n">{num(p.mark_price, 3)}</td>
                    <td className="n">{num(p.shares, 2)}</td>
                    <td className="n">{usd(p.cost_usdc)}</td>
                    <td className={`n ${tone(p.unrealized_pnl)}`}>{signedUsd(p.unrealized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="section card">
        <h2>최근 청산 ({closed.length})</h2>
        {!closed.length ? <p className="muted">아직 데이터 없음</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>청산</th><th>종목</th><th>경기</th><th>결과</th><th className="n">진입가</th><th className="n">청산가</th><th>사유</th><th className="n">스테이크</th><th className="n">실현손익</th></tr></thead>
              <tbody>
                {closed.map((p, i) => (
                  <tr key={i}>
                    <td className="muted">{kst(p.closed_at)}</td>
                    <td>{p.sport ?? "—"}</td>
                    <td className="wrap">{p.title ?? "—"}</td>
                    <td>{p.outcome ?? "—"}</td>
                    <td className="n">{num(p.entry_price, 3)}</td>
                    <td className="n">{num(p.exit_price, 3)}</td>
                    <td className="muted">{p.exit_reason ?? "—"}</td>
                    <td className="n">{usd(p.stake_usdc, 0)}</td>
                    <td className={`n ${tone(p.realized_pnl)}`}>{signedUsd(p.realized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="section grid grid-3">
        <Breakdown title="종목별" rows={d.breakdown?.by_sport} keyLabel="종목" />
        <Breakdown title="진입 분(minute)별" rows={d.breakdown?.by_entry_minute} keyLabel="분" />
        <Breakdown title="스테이크 티어별" rows={d.breakdown?.by_stake} keyLabel="USDC" />
        <Breakdown title="일자별 (KST)" rows={[...(d.breakdown?.by_day ?? [])].sort((a, b) => b.key.localeCompare(a.key))} keyLabel="날짜" />
      </section>
    </>
  );
}

