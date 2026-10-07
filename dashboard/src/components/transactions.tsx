
import { ModeBadge } from "@/components/ui";
import { DASH, kst, num, signedUsd, tone, usd } from "@/lib/format";
import { byVariant, groupByGame, positions, splitByMode, totals, txClass, type Totals } from "@/lib/transactions";
import type { Transaction } from "@/lib/types";

const STATUS_TONE: Record<string, string> = {
  confirmed: "good", resolve: "good", pending: "warning", quarantined: "warning", failed: "critical", other: "none",
};
const SIDE_LABEL: Record<string, string> = { BUY: "매수", SELL: "매도", RESOLVE: "정산" };
const POS_LABEL: Record<string, string> = { open: "보유 중", closed: "청산", quarantined: "격리", resolved: "정산" };

function TxStatus({ t }: { t: Transaction }) {
  const c = txClass(t);
  return (
    <span className="status">
      <span className={`dot ${STATUS_TONE[c]}`} aria-hidden="true" />
      {t.status ?? DASH}
    </span>
  );
}

function fees(t: Totals) {
  return (
    <>
      {usd(t.fees)}
      {t.feesUnknown > 0 && <span className="muted" title="fee 미확인 체결 수"> +{t.feesUnknown}건 미상</span>}
    </>
  );
}

function TotalsRow({ label, t, mode, href }: { label: React.ReactNode; t: Totals; mode?: string | null; href?: string }) {
  return (
    <tr>
      <td>{href ? <a href={href}><strong>{label}</strong></a> : <strong>{label}</strong>} {mode !== undefined && <ModeBadge mode={mode} />}</td>
      <td className="n">{num(t.games)}</td>
      <td className="n">
        {num(t.confirmed)}
        {t.pending > 0 && <span className="muted"> +{t.pending} 미확정</span>}
        {t.failed > 0 && <span className="muted"> · 실패 {t.failed}</span>}
        {t.quarantined > 0 && <span className="neg"> · 격리 {t.quarantined}</span>}
      </td>
      <td className="n">{usd(t.buyUsdc)}</td>
      <td className="n">{usd(t.sellUsdc + t.resolveUsdc)}</td>
      <td className="n">{fees(t)}</td>
      <td className={`n ${tone(t.realized)}`}><strong>{signedUsd(t.realized)}</strong>{t.settled > 0 && <span className="muted"> ({t.wins}승 {t.losses}패)</span>}</td>
      <td className="n">{t.openPositions ? <>{t.openPositions}개 · {usd(t.openCost)}</> : DASH}</td>
    </tr>
  );
}

/** Per-variant and overall totals. Live and paper are never summed together. */
export function TxTotalsTable({ rows, compact }: { rows: Transaction[]; compact?: boolean }) {
  const { live, paper } = splitByMode(rows);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>전략</th><th className="n">경기</th><th className="n">체결</th><th className="n">매수</th>
            <th className="n">매도·정산 수령</th><th className="n">수수료</th><th className="n">실현 손익</th>
            <th className="n">미실현: 보유 포지션 · 원가</th>
          </tr>
        </thead>
        <tbody>
          {byVariant(rows).map(([id, list]) => (
            <TotalsRow key={id} label={id} t={totals(list)} mode={list[0]?.mode ?? null} href={compact ? `/transactions?variant=${id}` : `/strategies/${id}`} />
          ))}
        </tbody>
        <tfoot>
          {live.length > 0 && <TotalsRow label="합계 · 실거래" t={totals(live)} />}
          {paper.length > 0 && <TotalsRow label="합계 · paper" t={totals(paper)} />}
        </tfoot>
      </table>
    </div>
  );
}

export function TxGroups({ rows, showVariant = true }: { rows: Transaction[]; showVariant?: boolean }) {
  const games = groupByGame(rows);
  return (
    <div className="tx-games">
      {games.map((g) => (
        <article key={g.key} className="tx-game">
          <header className="tx-game-head">
            <strong>{g.title}</strong>
            <span className="muted">{[g.sport, g.league].filter(Boolean).join(" · ") || DASH} · 최근 {kst(g.latest)}</span>
          </header>
          {g.variants.map((v) => {
            const t = totals(v.rows);
            const pos = positions(v.rows);
            const chrono = [...v.rows].sort((a, b) => (a.at ?? "").localeCompare(b.at ?? ""));
            return (
              <section key={v.variant_id} className="tx-variant">
                <div className="tx-variant-head">
                  {showVariant && <a href={`/strategies/${v.variant_id}`}><strong>{v.variant_id}</strong></a>}
                  <ModeBadge mode={v.mode} />
                  {v.account && <span className="muted">계정 {v.account}</span>}
                  <span className="tx-pos">
                    {pos.map((p) => (
                      <span key={p.key} className="badge" title={p.exit_reason ?? undefined}>
                        {p.outcome ?? DASH} · {!p.filled && !p.settled && p.status !== "quarantined" ? "미체결" : POS_LABEL[p.status ?? ""] ?? p.status ?? DASH}
                        {p.exit_reason && ` (${p.exit_reason})`}
                        {p.settled && <span className={tone(p.realized_pnl)}> {signedUsd(p.realized_pnl)}</span>}
                      </span>
                    ))}
                  </span>
                  <span className={`tx-sum ${tone(t.realized)}`}>
                    실현 {signedUsd(t.realized)}
                    {t.openPositions > 0 && <span className="muted"> · 미실현 {t.openPositions}개 {usd(t.openCost)}</span>}
                  </span>
                </div>
                <div className="table-wrap">
                  <table className="tx-table">
                    <colgroup>
                      <col style={{ width: "20%" }} /><col style={{ width: "8%" }} /><col style={{ width: "18%" }} /><col style={{ width: "10%" }} />
                      <col style={{ width: "10%" }} /><col style={{ width: "11%" }} /><col style={{ width: "9%" }} /><col style={{ width: "14%" }} />
                    </colgroup>
                    <thead>
                      <tr><th>시각 (KST)</th><th>구분</th><th>결과</th><th className="n">가격</th><th className="n">수량</th><th className="n">USDC</th><th className="n">수수료</th><th>상태</th></tr>
                    </thead>
                    <tbody>
                      {chrono.map((r, i) => (
                        <tr key={i} className={txClass(r) === "failed" ? "muted" : undefined}>
                          <td>{kst(r.at)}</td>
                          <td><span className={`side side-${r.side}`}>{SIDE_LABEL[r.side] ?? r.side}</span></td>
                          <td>{r.outcome ?? DASH}</td>
                          <td className="n">{num(r.price, 3)}</td>
                          <td className="n">{num(r.shares, 2)}</td>
                          <td className="n">{usd(r.usdc)}</td>
                          <td className="n">{r.fee_usdc === null ? DASH : usd(r.fee_usdc, 3)}</td>
                          <td><TxStatus t={r} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            );
          })}
        </article>
      ))}
    </div>
  );
}

export function TxLegend() {
  return (
    <p className="figure-caption" style={{ marginTop: 0 }}>
      실현 손익 = 정산된 포지션의 realized P&amp;L(포지션당 1회). 미실현 = 아직 정산되지 않은 보유 포지션의 매수 원가이며 손익에 포함하지 않는다.
      체결 수·매수·매도·수수료는 CONFIRMED/PAPER만 집계하고, MATCHED/MINED는 &lsquo;미확정&rsquo;으로 따로 표시한다. 실거래와 paper는 합산하지 않는다.
    </p>
  );
}
