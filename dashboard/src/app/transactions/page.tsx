import type { Metadata } from "next";
import Link from "next/link";

import { TxGroups, TxLegend, TxTotalsTable } from "@/components/transactions";
import { LoadState } from "@/components/ui";
import { loadJson } from "@/lib/storage";
import type { Transaction } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "지난 24시간 거래" };

type Props = { searchParams: Promise<{ variant?: string | string[]; sport?: string | string[] }> };

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";

export default async function TransactionsPage({ searchParams }: Props) {
  const sp = await searchParams;
  const res = await loadJson<Transaction[]>("latest/transactions_24h.json");
  const all = res.state === "ok" && Array.isArray(res.data) ? res.data : [];
  const variants = [...new Set(all.map((t) => t.variant_id))].sort();
  const sports = [...new Set(all.map((t) => t.sport).filter((s): s is string => !!s))].sort();
  const variant = variants.includes(one(sp.variant)) ? one(sp.variant) : "";
  const sport = sports.includes(one(sp.sport)) ? one(sp.sport) : "";
  const rows = all.filter((t) => (!variant || t.variant_id === variant) && (!sport || t.sport === sport));

  return (
    <>
      <h1>지난 24시간 거래</h1>
      <p className="sub">경기 → 전략 → 주문·체결·정산 (KST). 모든 전략의 주문/체결/정산 기록.</p>
      {res.state !== "ok" ? <LoadState result={res} /> : !all.length ? (
        <div className="empty">지난 24시간 거래 없음</div>
      ) : (
        <>
          <form className="filters" method="get">
            <label>
              전략
              <select name="variant" defaultValue={variant}>
                <option value="">전체</option>
                {variants.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
            <label>
              종목
              <select name="sport" defaultValue={sport}>
                <option value="">전체</option>
                {sports.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <button type="submit">적용</button>
            {(variant || sport) && <Link href="/transactions" style={{ fontSize: 13, paddingBottom: 6 }}>초기화</Link>}
          </form>
          <section className="card">
            <h2>합계</h2>
            {rows.length ? <TxTotalsTable rows={rows} /> : <p className="muted">조건에 맞는 거래 없음</p>}
            <TxLegend />
          </section>
          {rows.length > 0 && (
            <section className="section">
              <TxGroups rows={rows} />
            </section>
          )}
        </>
      )}
    </>
  );
}
