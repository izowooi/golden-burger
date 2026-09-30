import { LoadState } from "@/components/ui";
import { kst } from "@/lib/format";
import type { Loaded } from "@/lib/storage";
import type { Attention, AttentionItem } from "@/lib/types";

const INBOX_URL = "https://github.com/izowooi/golden-burger/blob/main/reports/attention.md";
const SEVERITY_LABEL: Record<string, string> = { critical: "긴급", warn: "주의", decide: "결정 필요", info: "정보" };
const CATEGORY_LABEL: Record<string, string> = {
  decision_needed: "결정", risk: "위험", data_quality: "데이터 품질", research_finding: "연구 발견",
  system_change: "시스템 변경", question: "질문",
};

function Severity({ s }: { s: string }) {
  return <span className={`badge sev-${s}`}>{SEVERITY_LABEL[s] ?? s}</span>;
}

function Item({ i }: { i: AttentionItem }) {
  const resolved = i.status === "resolved";
  return (
    <li className="att-item">
      <div className="att-head">
        <Severity s={i.severity} />
        <span className="badge">{i.category ? CATEGORY_LABEL[i.category] ?? i.category : "—"}</span>
        {i.source === "ai" && <span className="badge">AI</span>}
        <strong className="att-title">{i.title ?? i.id}</strong>
      </div>
      {i.detail && <p className="att-detail">{i.detail}</p>}
      <div className="att-meta">
        <span>생성 {kst(i.created_at)}</span>
        <span>갱신 {kst(i.updated_at)}</span>
        {resolved && <span>해결 {kst(i.resolved_at)}{i.resolution ? ` · ${i.resolution}` : ""}</span>}
        {i.evidence_ref && <span className="mono att-ref">{i.evidence_ref.split(",").map((s) => s.trim()).join(" · ")}</span>}
      </div>
    </li>
  );
}

export function AttentionPanel({ result }: { result: Loaded<Attention> }) {
  const a = result.state === "ok" ? result.data : null;
  const open = a?.open ?? [];
  const resolved = a?.resolved ?? [];
  return (
    <section className={`card att${open.length ? " att-active" : ""}`}>
      <div className="att-top">
        <h2>확인 필요{open.length ? ` (${open.length})` : ""}</h2>
        <a href={a?.url || INBOX_URL} target="_blank" rel="noreferrer" style={{ fontSize: 13 }}>attention.md →</a>
      </div>
      {result.state !== "ok" ? <LoadState result={result} /> : (
        <>
          {open.length ? <ul className="att-list">{open.map((i) => <Item key={i.id} i={i} />)}</ul>
            : <p className="muted" style={{ margin: 0 }}>확인할 항목 없음</p>}
          {resolved.length > 0 && (
            <details>
              <summary>최근 해결 {resolved.length}건 (14일)</summary>
              <ul className="att-list">{resolved.map((i) => <Item key={i.id} i={i} />)}</ul>
            </details>
          )}
          {a?.generated_at && <p className="muted att-gen">회고 갱신 {kst(a.generated_at)} KST</p>}
        </>
      )}
    </section>
  );
}
