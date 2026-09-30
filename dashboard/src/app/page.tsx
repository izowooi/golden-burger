import Link from "next/link";

import { TxTotalsTable } from "@/components/transactions";
import { Generated, LoadState, ModeBadge, StatusDot, type Health } from "@/components/ui";
import { ageMinutes, ago, kst, num, pct, signedUsd, tone, usd } from "@/lib/format";
import { loadJson } from "@/lib/storage";
import type { Overview, StrategySummary, Transaction } from "@/lib/types";

export const dynamic = "force-dynamic";

function freshness(iso: string | null | undefined, warnMin: number, critMin: number): Health {
  const a = ageMinutes(iso);
  if (a === null) return "none";
  return a > critMin ? "critical" : a > warnMin ? "warning" : "good";
}

const MODE_ORDER: Record<string, number> = { live: 0, paper: 1, off: 2 };

export default async function OverviewPage() {
  const [res, tx] = await Promise.all([
    loadJson<Overview>("latest/overview.json"),
    loadJson<Transaction[]>("latest/transactions_24h.json"),
  ]);
  return (
    <>
      <h1>개요</h1>
      <p className="sub">시스템 상태 · 포트폴리오 · 전략 성과 (KST)</p>
      {res.state !== "ok" ? <LoadState result={res} /> : <OverviewBody o={res.data} />}
      <section className="section card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <h2>지난 24시간 거래</h2>
          <Link href="/transactions" style={{ fontSize: 13 }}>경기별 전체 내역 →</Link>
        </div>
        {tx.state !== "ok" ? <LoadState result={tx} /> : !Array.isArray(tx.data) || !tx.data.length ? (
          <p className="muted" style={{ margin: 0 }}>지난 24시간 거래 없음</p>
        ) : <TxTotalsTable rows={tx.data} compact />}
      </section>
      {res.state === "ok" && <Generated at={res.data.generated_at} extra={<> · {ago(res.data.generated_at)} · commit <span className="mono">{res.data.git_commit ?? "—"}</span></>} />}
    </>
  );
}

function OverviewBody({ o }: { o: Overview }) {
  const strategies = [...(o.strategies ?? [])].sort(
    (a, b) => (MODE_ORDER[a.mode] ?? 3) - (MODE_ORDER[b.mode] ?? 3) || a.id.localeCompare(b.id),
  );
  return (
    <>
      <HealthStrip o={o} />
      <Portfolio o={o} />
      <section className="section card">
        <h2>전략 ({strategies.length})</h2>
        {strategies.length ? <StrategyTable rows={strategies} /> : <p className="muted">아직 데이터 없음</p>}
      </section>
      <Alerts o={o} />
    </>
  );
}

function HealthStrip({ o }: { o: Overview }) {
  const jobs = o.system?.jobs ?? [];
  const c = o.system?.collector ?? null;
  const ai = o.system?.ai ?? null;
  const jobHealth: Health = !jobs.length
    ? "none"
    : jobs.some((j) => j.status === "failing") ? "critical" : jobs.some((j) => j.status !== "ok") ? "warning" : "good";
  const badJobs = jobs.filter((j) => j.status !== "ok");
  const disk = c?.disk_free_gb ?? null;
  const diskHealth: Health = disk === null ? "none" : disk < 50 ? "critical" : disk < 150 ? "warning" : "good";
  const aiHealth: Health = !ai?.last_retro_at ? "none" : ai.last_retro_ok === false ? "critical" : freshness(ai.last_retro_at, 36 * 60, 72 * 60);
  const bf = c?.backfill_progress;
  const bfPct = bf && bf.games_done !== null && bf.games_total ? bf.games_done / bf.games_total : null;

  return (
    <section className="grid grid-tiles">
      <div className="tile">
        <div className="label">데이터 발행</div>
        <div className="value">{ago(o.generated_at)}</div>
        <div className="meta"><StatusDot health={freshness(o.generated_at, 15, 60)} /></div>
      </div>
      <div className="tile">
        <div className="label">잡</div>
        <div className="value">{jobs.length ? `${jobs.length - badJobs.length}/${jobs.length} 정상` : "—"}</div>
        <div className="meta">
          <StatusDot health={jobHealth} label={badJobs.length ? badJobs.map((j) => `${j.name}: ${j.status}`).join(", ") : undefined} />
        </div>
      </div>
      <div className="tile">
        <div className="label">수집기</div>
        <div className="value">{ago(c?.last_poll_at)}</div>
        <div className="meta">
          <StatusDot health={freshness(c?.last_poll_at, 5, 30)} label={`경기 ${num(c?.live_games)} · 마켓 ${num(c?.tracked_markets)} · WS ${ago(c?.ws_last_message_at)}`} />
        </div>
      </div>
      <div className="tile">
        <div className="label">디스크 여유</div>
        <div className="value">{disk === null ? "—" : `${num(disk, 0)} GB`}</div>
        <div className="meta">
          <StatusDot health={diskHealth} label={`core ${num(c?.core_db_mb, 0)}MB · books ${num(c?.books_db_mb, 0)}MB`} />
        </div>
      </div>
      <div className="tile">
        <div className="label">백필</div>
        <div className="value">{pct(bfPct, 0)}</div>
        <div className="meta">{bf ? `${num(bf.games_done)} / ${num(bf.games_total)} 경기` : "—"}</div>
      </div>
      <div className="tile">
        <div className="label">AI 회고</div>
        <div className="value">{ago(ai?.last_retro_at)}</div>
        <div className="meta">
          <StatusDot health={aiHealth} label={`${ai?.last_retro_kind ?? "—"} · 7일 적용 ${num(ai?.proposals_applied_7d)}건`} />
        </div>
      </div>
    </section>
  );
}

function Portfolio({ o }: { o: Overview }) {
  const p = o.portfolio;
  const accounts = p?.accounts ?? [];
  return (
    <section className="section card">
      <h2>포트폴리오</h2>
      {!p ? (
        <p className="muted">아직 데이터 없음</p>
      ) : (
        <>
          <div style={{ display: "flex", gap: 28, flexWrap: "wrap", alignItems: "baseline", marginBottom: 12 }}>
            <div>
              <div className="muted" style={{ fontSize: 12 }}>총 자산</div>
              <div className="hero">{usd(p.total_equity_usdc)}</div>
            </div>
            <div><span className="muted">현금 </span>{usd(p.total_cash_usdc)}</div>
            <div><span className="muted">포지션 </span>{usd(p.total_positions_value_usdc)}</div>
          </div>
          {accounts.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>계정</th><th>전략</th><th className="n">자산</th><th className="n">현금</th>
                    <th className="n">포지션</th><th className="n">상환 대기</th><th>갱신</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((a) => (
                    <tr key={a.alias}>
                      <td>{a.alias}</td>
                      <td>{a.variant_id ? <Link href={`/strategies/${a.variant_id}`}>{a.variant_id}</Link> : "—"}</td>
                      <td className="n"><strong>{usd(a.equity_usdc)}</strong></td>
                      <td className="n">{usd(a.cash_usdc)}</td>
                      <td className="n">{usd(a.positions_value_usdc)}</td>
                      <td className="n">{usd(a.redeemable_usdc)}</td>
                      <td className="muted">{ago(a.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">계정 없음</p>
          )}
        </>
      )}
    </section>
  );
}

function Ladder({ s }: { s: StrategySummary }) {
  const l = s.ladder;
  const prog = l && l.trades_at_tier !== null && l.needed ? Math.min(1, l.trades_at_tier / l.needed) : null;
  const statusLabel: Record<string, string> = { hold: "유지", promote_ready: "승급 가능", demote_warning: "강등 경고" };
  return (
    <div className="ladder">
      <span>{usd(s.stake_usdc, 0)}</span>
      {s.next_stake_usdc !== null && <span className="muted">→ {usd(s.next_stake_usdc, 0)}</span>}
      {prog !== null && (
        <span className="ladder-bar" title={`${l?.trades_at_tier}/${l?.needed} 거래 · ROI CI 하한 ${pct(l?.roi_ci_lo)}`}>
          <span style={{ width: `${prog * 100}%` }} />
        </span>
      )}
      {l && <span className="muted">{num(l.trades_at_tier)}/{num(l.needed)}</span>}
      {l && l.status !== "hold" && <span className={`badge ${l.status}`}>{statusLabel[l.status] ?? l.status}</span>}
    </div>
  );
}

function StrategyTable({ rows }: { rows: StrategySummary[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>전략</th><th>패밀리</th><th>모드</th><th>계정</th><th>종목</th><th>스테이크 / 래더</th>
            <th className="n">오픈</th><th className="n" title="pnl_mode 원장 기준">오늘</th><th className="n">7일</th><th className="n">30일</th>
            <th className="n">누적</th><th className="n">승률</th><th>최근 변경</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td><Link href={`/strategies/${s.id}`}><strong>{s.id}</strong></Link></td>
              <td className="muted">{s.family ?? "—"}</td>
              <td>
                <ModeBadge mode={s.mode} />
                {s.pnl_mode && s.pnl_mode !== s.mode && <span className="muted" style={{ fontSize: 11 }}> {s.pnl_mode} 원장</span>}
              </td>
              <td>{s.account ?? "—"}</td>
              <td>{s.sports?.length ? s.sports.join(", ") : "—"}</td>
              <td><Ladder s={s} /></td>
              <td className="n">{num(s.open_positions)}{s.open_cost_usdc !== null && <span className="muted"> · {usd(s.open_cost_usdc, 0)}</span>}</td>
              {(["today", "d7", "d30", "all"] as const).map((k) => (
                <td key={k} className={`n ${tone(s.pnl?.[k])}`}>{signedUsd(s.pnl?.[k])}</td>
              ))}
              <td className="n">
                {pct(s.win_rate)}
                {s.trades?.all !== null && s.trades?.all !== undefined && <span className="muted"> ({num(s.trades.wins)}/{num(s.trades.all)})</span>}
              </td>
              <td className="wrap">
                {s.last_change ? (
                  <>
                    <span className="muted">{kst(s.last_change.at)}</span> {s.last_change.summary ?? ""}
                  </>
                ) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Alerts({ o }: { o: Overview }) {
  const alerts = o.alerts ?? [];
  return (
    <section className="section card">
      <h2>알림</h2>
      {alerts.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>활성 알림 없음</p>
      ) : (
        alerts.map((a, i) => (
          <div className="alert-row" key={i}>
            <StatusDot health={a.level === "error" ? "critical" : "warning"} label={a.level === "error" ? "오류" : "경고"} />
            <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{kst(a.at)}</span>
            <span>{a.message}</span>
          </div>
        ))
      )}
    </section>
  );
}
