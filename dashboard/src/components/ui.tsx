import type { Loaded } from "@/lib/storage";
import { DASH, kst } from "@/lib/format";

export function Empty({ children = "아직 데이터 없음" }: { children?: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function LoadError({ path, message }: { path: string; message: string }) {
  return (
    <div className="error" role="alert">
      <strong>데이터를 불러오지 못했습니다.</strong> <span className="mono">{path}</span> — {message}
    </div>
  );
}

/** Renders the missing/error state of a Loaded value; returns null when ok. */
export function LoadState({ result, emptyText }: { result: Loaded<unknown>; emptyText?: React.ReactNode }) {
  if (result.state === "missing") return <Empty>{emptyText ?? <>아직 데이터 없음 <span className="mono">({result.path})</span></>}</Empty>;
  if (result.state === "error") return <LoadError path={result.path} message={result.message} />;
  return null;
}

export function ModeBadge({ mode }: { mode: string | null | undefined }) {
  const m = mode ?? "off";
  const label = m === "live" ? "LIVE" : m === "paper" ? "PAPER" : m === "off" ? "OFF" : m;
  return <span className={`badge ${m}`}>{label}</span>;
}

export type Health = "good" | "warning" | "critical" | "none";
const HEALTH_LABEL: Record<Health, string> = { good: "정상", warning: "주의", critical: "장애", none: "정보 없음" };

export function StatusDot({ health, label }: { health: Health; label?: string }) {
  return (
    <span className="status">
      <span className={`dot ${health}`} aria-hidden="true" />
      {label ?? HEALTH_LABEL[health]}
    </span>
  );
}

export function Generated({ at, extra }: { at: string | null | undefined; extra?: React.ReactNode }) {
  return (
    <p className="footer">
      데이터 생성 {at ? kst(at) : DASH} KST{extra}
    </p>
  );
}

export function Loading() {
  return <div className="empty">불러오는 중…</div>;
}
