"use client";


import { Loading, LoadState } from "@/components/ui";
import { REPORT_PATH, useJson } from "@/lib/storage";
import type { ReportEntry } from "@/lib/types";

const KIND_LABEL: Record<string, string> = { daily: "일간", weekly: "주간", monthly: "월간" };
const SLOT_LABEL: Record<string, string> = { morning: "아침", evening: "저녁", dawn: "새벽" };

function reportHref(path: string) {
  const rel = path.replace(/^reports\//, "").replace(/\.md$/, "");
  return REPORT_PATH.test(rel) ? `/reports/${rel}` : null;
}

export default function ReportsView() {
  const res = useJson<ReportEntry[]>("reports/index.json");
  if (!res) return <><h1>리포트</h1><Loading /></>;
  const list = res.state === "ok" && Array.isArray(res.data) ? res.data : [];
  return (
    <>
      <h1>리포트</h1>
      <p className="sub">일간·주간·월간 회고 (결정적 표 + AI 서술 + 적용된 변경)</p>
      {res.state !== "ok" ? <LoadState result={res} /> : !list.length ? <LoadState result={{ state: "missing", path: "reports/index.json" }} /> : (
        <section className="card">
          <div className="table-wrap">
            <table>
              <thead><tr><th>날짜</th><th>종류</th><th>슬롯</th><th>제목</th><th>AI</th></tr></thead>
              <tbody>
                {list.map((r) => {
                  const href = reportHref(r.path);
                  return (
                    <tr key={r.path}>
                      <td className="mono">{r.date}</td>
                      <td><span className="badge">{KIND_LABEL[r.kind] ?? r.kind}</span></td>
                      <td className="muted">{r.slot ? SLOT_LABEL[r.slot] ?? r.slot : "—"}</td>
                      <td className="wrap">{href ? <a href={href}>{r.title}</a> : r.title}</td>
                      <td className="muted">{r.ai ? r.engine ?? "AI" : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
