import type { Metadata } from "next";
import Link from "next/link";

import { EquityChart } from "@/components/charts";
import { Generated, LoadState } from "@/components/ui";
import { ago, kst, num, pct, signedPct, signedUsd, tone, usd } from "@/lib/format";
import { MANUAL_RESULT } from "@/lib/labels";
import { loadJson } from "@/lib/storage";
import type { Manual, ManualAccount, ManualMoney, ManualPosition, ManualPredictionRow } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "수동 베팅" };

/** A settled bet losing at least this much (or a full resolution loss) is highlighted. */
const LARGE_LOSS_USDC = 100;

type Props = { searchParams: Promise<{ a?: string }> };

function isLargeLoss(p: ManualPosition) {
  return p.result === "resolved_loss" || (p.realized_pnl !== null && p.realized_pnl <= -LARGE_LOSS_USDC);
}

/** Full ledger when published; otherwise open + last-24h settled (deduplicated). */
function allPositions(m: Manual): { rows: ManualPosition[]; full: boolean } {
  if (Array.isArray(m.positions)) return { rows: m.positions, full: true };
  const seen = new Map<string, ManualPosition>();
  for (const p of [...(m.settled_24h ?? []), ...(m.open_positions ?? [])]) if (!seen.has(p.position_id)) seen.set(p.position_id, p);
  return { rows: [...seen.values()], full: false };
}

export default async function ManualPage({ searchParams }: Props) {
  const { a } = await searchParams;
  const res = await loadJson<Manual>("latest/manual.json");
  return (
    <>
      <h1>수동 베팅</h1>
      <p className="sub">트랙 2 · 공개 지갑을 관찰만 하는 기록(주소 비공개, 별칭만 표시) · 실현 손익은 완전 정산분만, 평가손익은 별도 · KST</p>
      {res.state !== "ok" ? <LoadState result={res} /> : <ManualBody m={res.data} selected={a ?? "all"} />}
      {res.state === "ok" && <Generated at={res.data.generated_at} extra={<> · {ago(res.data.generated_at)}</>} />}
    </>
  );
}

function ManualBody({ m, selected }: { m: Manual; selected: string }) {
  const accounts = m.accounts ?? [];
  if (!accounts.length) return <div className="empty">수동 기록 계좌 없음 (원장이 아직 없습니다)</div>;
  const idx = selected === "all" ? -1 : Number(selected);
  const acct = Number.isInteger(idx) && idx >= 0 && idx < accounts.length ? accounts[idx] : null;
  const { rows, full } = allPositions(m);
  const scoped = acct ? rows.filter((p) => p.account === acct.account) : rows;
  const track2 = scoped.filter((p) => p.track2);
  const other = scoped.filter((p) => !p.track2);
  const preds = new Map<string, ManualPredictionRow[]>();
  for (const r of m.predictions?.rows ?? []) {
    if (!r.position_id) continue;
    preds.set(r.position_id, [...(preds.get(r.position_id) ?? []), r]);
  }

  return (
    <>
      <nav className="tabs" aria-label="계좌 선택">
        <Link href="/manual" className={!acct ? "on" : ""}>전체</Link>
        {accounts.map((x, i) => (
          <Link key={x.account} href={`/manual?a=${i}`} className={acct === x ? "on" : ""}>{x.account}</Link>
        ))}
      </nav>

      <MoneyTiles money={acct ? acct.track2 : m.totals} acct={acct} />
      {!acct && <AccountTable accounts={accounts} />}

      {!full && (
        <p className="note">
          전체 베팅 이력(<span className="mono">positions</span>)이 아직 발행되지 않아 보유 중 포지션과 최근 24시간 정산분만 표시합니다.
          위 집계(정산 {num((acct ? acct.track2 : m.totals)?.settled)}건)는 전체 원장 기준입니다.
        </p>
      )}

      <section className="section card">
        <h2>누적 실현 손익 (트랙 2)</h2>
        <CumulativeChart rows={track2} full={full} />
      </section>

      <section className="section card">
        <BetTable title={`베팅 (트랙 2 · ${track2.length}건)`} rows={track2} showAccount={!acct} preds={preds} />
      </section>

      {!acct && !!m.by_stake?.length && (
        <section className="section card">
          <h2>스테이크 구간별 (트랙 2 정산)</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>구간 (USDC)</th><th className="n">n</th><th className="n">승/패</th><th className="n">승률</th><th className="n">원가</th><th className="n">손익</th><th className="n">ROI</th></tr></thead>
              <tbody>
                {m.by_stake.map((b) => (
                  <tr key={b.band}>
                    <td>{b.band}</td>
                    <td className="n">{num(b.n)}</td>
                    <td className="n">{num(b.wins)}/{num(b.losses)}</td>
                    <td className="n">{pct(b.win_rate)}</td>
                    <td className="n">{usd(b.cost)}</td>
                    <td className={`n ${tone(b.pnl)}`}>{signedUsd(b.pnl)}</td>
                    <td className={`n ${tone(b.roi)}`}>{signedPct(b.roi)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="section card">
        <h2>기타 (경기 미연결 · 트랙 2 집계 제외)</h2>
        {!acct && m.other && (
          <p className="muted" style={{ marginTop: 0 }}>
            정산 {num(m.other.settled)}건 · 실현 {signedUsd(m.other.realized_pnl)} · 보유 {num(m.other.open)}건
            {m.quarantined ? <> · 격리 {num(m.quarantined)}건(금액 제외)</> : null}
          </p>
        )}
        {acct && <p className="muted" style={{ marginTop: 0 }}>계좌 전체 실현 손익(기타 포함) {signedUsd(acct.all_realized_pnl)}</p>}
        {other.length ? <BetTable rows={other} showAccount={!acct} preds={preds} /> : <p className="muted" style={{ margin: 0 }}>표시할 기타 포지션 없음</p>}
      </section>

      <Trades24h m={m} acct={acct} />
    </>
  );
}

function MoneyTiles({ money, acct }: { money: ManualMoney | null; acct: ManualAccount | null }) {
  if (!money) return <div className="empty">트랙 2 집계 없음</div>;
  const r = money.realized_pnl;
  const tiles: { l: string; v: string; t?: string; meta?: string }[] = [
    { l: "포지션", v: `${num(money.settled)} 정산 · ${num(money.open)} 보유`, meta: money.sold ? `매도 청산 ${num(money.sold)}건 포함` : undefined },
    { l: "승 / 패", v: `${num(money.wins)}승 ${num(money.losses)}패`, meta: `매도 ${num(money.sold)}` },
    { l: "승률", v: pct(money.win_rate), meta: "매도 제외" },
    { l: "실현 손익", v: signedUsd(r?.all), t: tone(r?.all), meta: `오늘 ${signedUsd(r?.today)} · 7일 ${signedUsd(r?.d7)}` },
    { l: "ROI", v: signedPct(money.roi), t: tone(money.roi), meta: `정산 원가 ${usd(money.cost_settled, 0)}` },
    { l: "수수료", v: usd(money.fees_usdc), meta: money.fees_unknown ? `미확인 ${num(money.fees_unknown)}건` : undefined },
    { l: "보유 노출", v: usd(money.open_cost_usdc), meta: `${num(money.open)}건 원가` },
    { l: "평가손익 (별도)", v: signedUsd(money.unrealized_pnl), t: tone(money.unrealized_pnl), meta: money.unrealized_unknown ? `시세 없음 ${num(money.unrealized_unknown)}건` : "실현 손익에 미포함" },
  ];
  if (acct?.bankroll_usdc !== null && acct?.bankroll_usdc !== undefined) {
    tiles.push({ l: "뱅크롤", v: usd(acct.bankroll_usdc, 0), meta: acct.bankroll_first_seen_at ? `${kst(acct.bankroll_first_seen_at)} 고정` : undefined });
  }
  if (acct?.drawdown_pct !== null && acct?.drawdown_pct !== undefined) {
    tiles.push({ l: "뱅크롤 대비", v: signedPct(acct.drawdown_pct), t: acct.drawdown_pct <= -0.1 ? "neg" : tone(acct.drawdown_pct), meta: "−10% 이하면 확인 필요" });
  }
  return (
    <section className="grid grid-tiles">
      {tiles.map((x) => (
        <div className="tile" key={x.l}>
          <div className="label">{x.l}</div>
          <div className={`value ${x.t ?? ""}`}>{x.v}</div>
          {x.meta && <div className="meta">{x.meta}</div>}
        </div>
      ))}
    </section>
  );
}

function AccountTable({ accounts }: { accounts: ManualAccount[] }) {
  return (
    <section className="section card">
      <h2>계좌별 (트랙 2)</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>계좌</th><th className="n">정산</th><th className="n">승/패/매도</th><th className="n">승률</th><th className="n">실현 손익</th>
              <th className="n">ROI</th><th className="n">보유 노출</th><th className="n">평가손익</th><th className="n">뱅크롤 대비</th><th>동기화</th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((x, i) => {
              const t = x.track2;
              return (
                <tr key={x.account}>
                  <td><Link href={`/manual?a=${i}`}>{x.account}</Link></td>
                  <td className="n">{num(t?.settled)}</td>
                  <td className="n">{num(t?.wins)}/{num(t?.losses)}/{num(t?.sold)}</td>
                  <td className="n">{pct(t?.win_rate)}</td>
                  <td className={`n ${tone(t?.realized_pnl?.all)}`}><strong>{signedUsd(t?.realized_pnl?.all)}</strong></td>
                  <td className={`n ${tone(t?.roi)}`}>{signedPct(t?.roi)}</td>
                  <td className="n">{usd(t?.open_cost_usdc)}</td>
                  <td className={`n ${tone(t?.unrealized_pnl)}`}>{signedUsd(t?.unrealized_pnl)}</td>
                  <td className={`n ${tone(x.drawdown_pct)}`}>{signedPct(x.drawdown_pct)}</td>
                  <td className="muted">{ago(x.last_sync_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CumulativeChart({ rows, full }: { rows: ManualPosition[]; full: boolean }) {
  const closed = rows
    .filter((p): p is ManualPosition & { closed_at: string; realized_pnl: number } => !!p.closed_at && p.realized_pnl !== null)
    .sort((x, y) => x.closed_at.localeCompare(y.closed_at));
  if (!full) return <p className="muted" style={{ margin: 0 }}>전체 정산 이력이 발행되면 표시됩니다.</p>;
  if (closed.length < 2) return <p className="muted" style={{ margin: 0 }}>정산 2건 이상부터 표시</p>;
  const points: { at: string; cum_pnl: number }[] = [];
  for (const p of closed) {
    const prev = points.length ? points[points.length - 1].cum_pnl : 0;
    points.push({ at: p.closed_at, cum_pnl: Math.round((prev + p.realized_pnl) * 100) / 100 });
  }
  return <EquityChart points={points} />;
}

function ResultBadge({ result }: { result: string | null }) {
  if (!result) return <span className="muted">—</span>;
  const r = MANUAL_RESULT[result] ?? { label: result, tone: "other" };
  return <span className={`badge res-${r.tone}`}>{r.label}</span>;
}

function BetTable({ title, rows, showAccount, preds }: {
  title?: string; rows: ManualPosition[]; showAccount: boolean; preds: Map<string, ManualPredictionRow[]>;
}) {
  const sorted = [...rows].sort((x, y) => (y.opened_at ?? "").localeCompare(x.opened_at ?? ""));
  const showP00 = sorted.some((p) => p.implied_p00_at_entry !== null);
  const showAi = sorted.some((p) => preds.has(p.position_id));
  const big = sorted.filter((p) => p.closed_at && isLargeLoss(p));
  return (
    <>
      {title && <h2>{title}</h2>}
      {big.length > 0 && (
        <p className="loss-note">
          큰 손실 {big.length}건: {big.map((p) => `${p.game ?? "—"} ${signedUsd(p.realized_pnl)}`).join(" · ")}
        </p>
      )}
      {!sorted.length ? <p className="muted" style={{ margin: 0 }}>표시할 베팅 없음</p> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>진입 (KST)</th>{showAccount && <th>계좌</th>}<th>경기</th><th>리그</th><th>마켓</th>
                <th className="n">진입가</th><th className="n">USDC</th>
                {showP00 && <th className="n" title="1 − Over 진입가 (Under는 Under 가격)">시장 P(0:0)</th>}
                {showAi && <th className="n">AI P(0:0)</th>}
                <th>결과</th><th className="n">실현 손익</th><th className="n">평가손익</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => {
                const ai = preds.get(p.position_id) ?? [];
                return (
                  <tr key={p.position_id} className={p.closed_at && isLargeLoss(p) ? "big-loss" : undefined}>
                    <td className="muted">{kst(p.opened_at)}</td>
                    {showAccount && <td>{p.account}</td>}
                    <td className="wrap">
                      {p.game ?? "—"}
                      {p.kickoff && <div className="muted" style={{ fontSize: 12 }}>킥오프 {kst(p.kickoff)}</div>}
                    </td>
                    <td>{p.league ?? "—"}</td>
                    <td>{p.market ?? p.outcome ?? "—"}</td>
                    <td className="n">{num(p.entry_price, 3)}</td>
                    <td className="n">{usd(p.stake_usdc)}</td>
                    {showP00 && <td className="n">{pct(p.implied_p00_at_entry)}</td>}
                    {showAi && (
                      <td className="n">
                        {ai.length ? ai.map((r, i) => (
                          <div key={i}>{pct(r.ai_p00)} <span className="muted" style={{ fontSize: 11 }}>{r.engine ?? ""}{r.rank !== null ? ` #${r.rank}` : ""}</span></div>
                        )) : "—"}
                      </td>
                    )}
                    <td><ResultBadge result={p.result} /></td>
                    <td className={`n ${tone(p.realized_pnl)}`}><strong>{signedUsd(p.realized_pnl)}</strong></td>
                    <td className={`n muted`}>{p.result === "open" ? signedUsd(p.unrealized_pnl) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Trades24h({ m, acct }: { m: Manual; acct: ManualAccount | null }) {
  const rows = (m.trades_24h ?? []).filter((t) => !acct || t.account === acct.account);
  return (
    <section className="section card">
      <details>
        <summary>지난 24시간 체결 ({rows.length})</summary>
        {!rows.length ? <p className="muted">지난 24시간 체결 없음</p> : (
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table>
              <thead><tr><th>시각</th>{!acct && <th>계좌</th>}<th>경기</th><th>마켓</th><th>구분</th><th className="n">가격</th><th className="n">수량</th><th className="n">USDC</th><th className="n">수수료</th></tr></thead>
              <tbody>
                {rows.map((t, i) => (
                  <tr key={i}>
                    <td className="muted">{kst(t.at)}</td>
                    {!acct && <td>{t.account}</td>}
                    <td className="wrap">{t.game ?? "—"}</td>
                    <td>{t.market ?? "—"}</td>
                    <td>{t.side === "BUY" ? "매수" : t.side === "SELL" ? "매도" : t.side === "RESOLVE" ? "정산" : t.side}</td>
                    <td className="n">{num(t.price, 3)}</td>
                    <td className="n">{num(t.shares, 2)}</td>
                    <td className="n">{usd(t.usdc)}</td>
                    <td className="n">{usd(t.fee_usdc, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}
