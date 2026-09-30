const KST = "Asia/Seoul";

const dtFmt = new Intl.DateTimeFormat("ko-KR", {
  timeZone: KST, year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const dFmt = new Intl.DateTimeFormat("ko-KR", { timeZone: KST, month: "2-digit", day: "2-digit" });

export const DASH = "—";

function valid(iso: string | null | undefined) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function kst(iso: string | null | undefined) {
  const d = valid(iso);
  return d ? dtFmt.format(d) : DASH;
}

export function kstDay(iso: string | null | undefined) {
  const d = valid(iso);
  return d ? dFmt.format(d) : DASH;
}

export function ago(iso: string | null | undefined, now = Date.now()) {
  const d = valid(iso);
  if (!d) return DASH;
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 0) return "방금";
  if (s < 90) return `${s}초 전`;
  if (s < 5400) return `${Math.round(s / 60)}분 전`;
  if (s < 172800) return `${Math.round(s / 3600)}시간 전`;
  return `${Math.round(s / 86400)}일 전`;
}

export function ageMinutes(iso: string | null | undefined, now = Date.now()) {
  const d = valid(iso);
  return d ? (now - d.getTime()) / 60000 : null;
}

export function usd(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function signedUsd(v: number | null | undefined) {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return v > 0 ? `+$${s}` : v < 0 ? `−$${s}` : `$${s}`;
}

export function pct(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  return `${(v * 100).toFixed(digits)}%`;
}

export function signedPct(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const s = `${Math.abs(v * 100).toFixed(digits)}%`;
  return v > 0 ? `+${s}` : v < 0 ? `−${s}` : s;
}

export function num(v: number | null | undefined, digits = 0) {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function tone(v: number | null | undefined) {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "pos" : "neg";
}

export function paramValue(v: unknown) {
  if (v === null || v === undefined) return DASH;
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
