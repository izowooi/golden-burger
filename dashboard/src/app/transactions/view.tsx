"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { TxGroups, TxLegend, TxTotalsTable } from "@/components/transactions";
import { Loading, LoadState } from "@/components/ui";
import { useJson } from "@/lib/storage";
import type { Transaction } from "@/lib/types";

export default function TransactionsView() {
  const sp = useSearchParams();
  const res = useJson<Transaction[]>("latest/transactions_24h.json");
  if (!res) return <><h1>지난 24시간 거래</h1><Loading /></>;
  const all = res.state === "ok" && Array.isArray(res.data) ? res.data : [];
  const variants = [...new Set(all.map((t) => t.variant_id))].sort();
  const sports = [...new Set(all.map((t) => t.sport).filter((s): s is string => !!s))].sort();
  const variant = variants.includes(sp.get("variant") ?? "") ? sp.get("variant") ?? "" : "";
  const sport = sports.includes(sp.get("sport") ?? "") ? sp.get("sport") ?? "" : "";
  const rows = all.filter((t) => (!variant || t.variant_id === variant) && (!sport || t.sport === sport));

  return (
    <>
      <h1>지난 24시간 거래</h1>
      <p className="sub">경기 → 전략 → 주문·체결·정산 (KST). 모든 전략의 주문/체결/정산 기록.</p>
      {res.state !== "ok" ? <LoadState result={res} /> : !all.length ? (
        <div className="empty">지난 24시간 거래 없음</div>
      ) : (
        <>
          <form className="filters" method="get" key={`${variant}|${sport}`}>
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
