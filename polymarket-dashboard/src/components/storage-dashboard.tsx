"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  buildStorageChartRows,
  getStorageSummaries,
  STORAGE_CRITICAL_PERCENT,
  STORAGE_WARNING_PERCENT,
  type StorageSummary,
} from "@/lib/storage";
import type { HostStorageResponse } from "@/lib/types";

const SERIES_COLORS = ["#8de0c1", "#f0c36a", "#a98cff", "#57a8f5", "#ff7b86"];

const shortDate = new Intl.DateTimeFormat("ko-KR", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

export function StorageDashboard() {
  const [data, setData] = useState<HostStorageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await fetchStorage());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "알 수 없는 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchStorage(controller.signal)
      .then(setData)
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "알 수 없는 오류가 발생했습니다.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const summaries = useMemo(
    () => getStorageSummaries(data?.snapshots ?? [], data?.generated_at ?? new Date(0).toISOString()),
    [data],
  );
  const chartRows = useMemo(
    () => buildStorageChartRows(data?.snapshots ?? []),
    [data?.snapshots],
  );
  const summaryByKey = useMemo(
    () => new Map(summaries.map((summary) => [summary.key, summary])),
    [summaries],
  );
  const worst = summaries.reduce<StorageSummary | null>(
    (current, summary) =>
      !current || summary.utilizationPercent > current.utilizationPercent ? summary : current,
    null,
  );
  const leastFree = summaries.reduce<StorageSummary | null>(
    (current, summary) =>
      !current || summary.latest.available_bytes < current.latest.available_bytes
        ? summary
        : current,
    null,
  );
  const oldestLatest = summaries.reduce<string | null>(
    (current, summary) =>
      !current || summary.latest.reported_at < current ? summary.latest.reported_at : current,
    null,
  );
  const projectedDays = summaries
    .map((summary) => summary.projectedDaysToFull)
    .filter((value): value is number => value != null && Number.isFinite(value));
  const earliestProjectedDays = projectedDays.length ? Math.min(...projectedDays) : null;
  const hasCritical = summaries.some((summary) => summary.status === "critical");
  const hasWarning = summaries.some((summary) => summary.status === "warning");
  const hasStale = summaries.some((summary) => summary.stale);
  const statusClass = hasStale || hasCritical ? "stale" : hasWarning ? "warning" : "fresh";
  const statusLabel = !summaries.length
    ? "용량 보고 없음"
    : hasStale
      ? "용량 보고 지연"
      : hasCritical
        ? "용량 위험"
        : hasWarning
          ? "용량 주의"
          : "용량 정상";

  return (
    <main className="dashboard-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">PB</div>
          <div>
            <p className="eyebrow">POLYMARKET BOT / OPERATIONS DESK</p>
            <h1>Strategy Monitor</h1>
          </div>
        </div>
        <div className="topbar-actions">
          <nav className="monitor-nav" aria-label="대시보드 화면">
            <Link href="/">성과</Link>
            <Link className="selected" href="/storage" aria-current="page">저장공간</Link>
            <Link href="/strategies">전략 단계</Link>
          </nav>
          <div className="status-cluster">
            <span className={`status-dot ${loading ? "pending" : error ? "stale" : statusClass}`} />
            <span>{loading ? "데이터 확인 중" : error ? "데이터 연결 오류" : statusLabel}</span>
            {oldestLatest && (
              <span className="status-date">보고 {formatTimestamp(oldestLatest)}</span>
            )}
            <button className="refresh-button" type="button" onClick={() => void loadData()}>
              새로고침
            </button>
          </div>
        </div>
      </header>

      <section className="hero-row storage-hero">
        <div>
          <p className="section-kicker">HOST CAPACITY OBSERVATORY</p>
          <h2>Mac mini와 외장 디스크의<br />남은 공간을 매일 확인합니다.</h2>
        </div>
        <p className="hero-note">
          80%부터 주의, 90%부터 위험으로 표시합니다. 최근 30일의 실제 사용량 증가율로
          예상 소진 시점을 계산하며, 수집이 36시간 넘게 끊기면 지연으로 판정합니다.
        </p>
      </section>

      {loading && <StorageLoading />}
      {!loading && error && <StorageError message={error} onRetry={loadData} />}

      {!loading && !error && data && (
        <>
          {!summaries.length ? (
            <section className="storage-empty">
              <strong>아직 저장공간 관측값이 없습니다.</strong>
              <p>Supabase migration 적용 후 Mac mini의 일일 Jenkins 수집기를 한 번 실행하세요.</p>
            </section>
          ) : (
            <>
              <section className="kpi-grid" aria-label="저장공간 요약">
                <StorageKpi
                  label="모니터링 볼륨"
                  value={`${summaries.length}개`}
                  detail={`${new Set(summaries.map((summary) => summary.latest.host_id)).size}개 host`}
                />
                <StorageKpi
                  label="최고 사용률"
                  value={worst ? formatPercent(worst.utilizationPercent) : "—"}
                  detail={worst ? seriesLabel(worst) : "관측 없음"}
                  tone={worst?.status === "critical" ? "negative" : worst?.status === "warning" ? "storage-warning-text" : "positive"}
                />
                <StorageKpi
                  label="최소 여유 공간"
                  value={leastFree ? formatBytes(leastFree.latest.available_bytes) : "—"}
                  detail={leastFree ? seriesLabel(leastFree) : "관측 없음"}
                  tone={leastFree?.status === "critical" ? "negative" : "neutral"}
                />
                <StorageKpi
                  label="가장 빠른 예상 소진"
                  value={formatProjectedDays(earliestProjectedDays)}
                  detail="최근 최대 30일 사용량 증가율 기준"
                  tone={earliestProjectedDays != null && earliestProjectedDays < 30 ? "negative" : "neutral"}
                />
              </section>

              <section className="storage-volume-grid">
                {summaries.map((summary) => (
                  <VolumeCard key={summary.key} summary={summary} />
                ))}
              </section>

              <section className="chart-panel storage-chart-panel">
                <div className="panel-header">
                  <div>
                    <p className="section-kicker">CAPACITY TRAJECTORY</p>
                    <h3>파일시스템 사용률 추이</h3>
                  </div>
                  <span className="rows-caption">결측일은 선을 연결하지 않습니다</span>
                </div>
                <div className="chart-wrap">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartRows} margin={{ top: 16, right: 18, left: 4, bottom: 4 }}>
                      <CartesianGrid stroke="#20312d" strokeDasharray="2 6" vertical={false} />
                      <XAxis
                        dataKey="date"
                        stroke="#6f8580"
                        tickLine={false}
                        axisLine={false}
                        minTickGap={34}
                        tickFormatter={(value) => formatShortDate(String(value))}
                      />
                      <YAxis
                        domain={[0, 100]}
                        stroke="#6f8580"
                        tickLine={false}
                        axisLine={false}
                        width={54}
                        tickFormatter={(value) => `${Number(value).toFixed(0)}%`}
                      />
                      <ReferenceLine y={STORAGE_WARNING_PERCENT} stroke="#f0c36a" strokeDasharray="4 5" />
                      <ReferenceLine y={STORAGE_CRITICAL_PERCENT} stroke="#ff7b86" strokeDasharray="4 5" />
                      <Tooltip
                        cursor={{ stroke: "#55706a", strokeDasharray: "3 5" }}
                        contentStyle={{
                          background: "#0f1a18",
                          border: "1px solid #2a3b37",
                          borderRadius: 12,
                          color: "#edf5f2",
                        }}
                        labelFormatter={(value) => formatDate(String(value))}
                        formatter={(value, name) => [
                          formatPercent(Number(value)),
                          summaryByKey.has(String(name))
                            ? seriesLabel(summaryByKey.get(String(name))!)
                            : String(name),
                        ]}
                      />
                      <Legend
                        formatter={(value) =>
                          summaryByKey.has(String(value))
                            ? seriesLabel(summaryByKey.get(String(value))!)
                            : String(value)
                        }
                      />
                      {summaries.map((summary, index) => (
                        <Line
                          key={summary.key}
                          type="monotone"
                          dataKey={summary.key}
                          name={summary.key}
                          stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
                          strokeWidth={2.6}
                          dot={false}
                          activeDot={{ r: 5, strokeWidth: 2, fill: "#0b1311" }}
                          connectNulls={false}
                          isAnimationActive={false}
                        />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </section>

              <section className="table-panel storage-table-panel">
                <div className="panel-header compact-header">
                  <div>
                    <p className="section-kicker">LATEST FILESYSTEM SNAPSHOT</p>
                    <h3>볼륨별 최신 상태</h3>
                  </div>
                  <span className="rows-caption">일별 최신 관측값</span>
                </div>
                <div className="table-scroll">
                  <table className="storage-table">
                    <thead>
                      <tr>
                        <th>Host / 볼륨</th>
                        <th>경로</th>
                        <th>전체</th>
                        <th>사용</th>
                        <th>여유</th>
                        <th>사용률</th>
                        <th>일평균 변화</th>
                        <th>최신 관측</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summaries.map((summary) => (
                        <tr key={summary.key}>
                          <td><strong>{seriesLabel(summary)}</strong></td>
                          <td className="mount-path-cell">{summary.latest.mount_path}</td>
                          <td>{formatBytes(summary.latest.total_bytes)}</td>
                          <td>{formatBytes(summary.latest.used_bytes)}</td>
                          <td>{formatBytes(summary.latest.available_bytes)}</td>
                          <td className={storageTone(summary)}>{formatPercent(summary.utilizationPercent)}</td>
                          <td>{formatGrowth(summary.dailyGrowthBytes)}</td>
                          <td>{formatTimestamp(summary.latest.reported_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}

          <footer>
            <span>PB Storage Monitor</span>
            <span>
              가장 오래된 최신 보고 {oldestLatest ? `${formatTimestamp(oldestLatest)} KST` : "—"} · API 조회{" "}
              {formatTimestamp(data.generated_at)} KST
            </span>
          </footer>
        </>
      )}
    </main>
  );
}

function StorageKpi({
  label,
  value,
  detail,
  tone = "",
}: {
  label: string;
  value: React.ReactNode;
  detail: string;
  tone?: string;
}) {
  return (
    <article className="kpi-card">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function VolumeCard({ summary }: { summary: StorageSummary }) {
  return (
    <article className={`storage-volume-card storage-${summary.status} ${summary.stale ? "storage-stale" : ""}`}>
      <div className="storage-volume-heading">
        <div>
          <span>{summary.latest.host_id}</span>
          <h3>{summary.latest.mount_label}</h3>
        </div>
        <strong>{summary.stale ? "보고 지연" : statusName(summary.status)}</strong>
      </div>
      <p className="storage-mount-path">{summary.latest.mount_path}</p>
      <div className="capacity-row">
        <strong>{formatPercent(summary.utilizationPercent)}</strong>
        <span>{formatBytes(summary.latest.available_bytes)} 여유</span>
      </div>
      <div className="capacity-track" aria-label={`사용률 ${formatPercent(summary.utilizationPercent)}`}>
        <span style={{ width: `${Math.min(100, Math.max(0, summary.utilizationPercent))}%` }} />
      </div>
      <div className="storage-volume-stats">
        <div><span>사용</span><strong>{formatBytes(summary.latest.used_bytes)}</strong></div>
        <div><span>전체</span><strong>{formatBytes(summary.latest.total_bytes)}</strong></div>
        <div><span>일평균 변화</span><strong>{formatGrowth(summary.dailyGrowthBytes)}</strong></div>
        <div><span>예상 소진</span><strong>{formatProjectedDays(summary.projectedDaysToFull)}</strong></div>
      </div>
      <small>
        {summary.observations}일 관측 · 최신 {formatTimestamp(summary.latest.reported_at)} KST
        {summary.stale ? ` · ${formatAge(summary.ageHours)} 경과` : ""}
      </small>
    </article>
  );
}

function StorageLoading() {
  return (
    <section className="loading-state">
      <div className="loading-ring" />
      <p>저장공간 관측값을 불러오는 중입니다.</p>
    </section>
  );
}

function StorageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="error-state">
      <strong>저장공간 데이터를 불러오지 못했습니다.</strong>
      <p>{message}</p>
      <button type="button" onClick={onRetry}>다시 시도</button>
    </section>
  );
}

function seriesLabel(summary: StorageSummary) {
  return `${summary.latest.host_id} / ${summary.latest.mount_label}`;
}

function statusName(status: StorageSummary["status"]) {
  if (status === "critical") return "위험";
  if (status === "warning") return "주의";
  return "정상";
}

function storageTone(summary: StorageSummary) {
  if (summary.status === "critical") return "negative";
  if (summary.status === "warning") return "storage-warning-text";
  return "positive";
}

function formatBytes(value: number) {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let size = Math.abs(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const sign = value < 0 ? "-" : "";
  const digits = unit === 0 ? 0 : size >= 100 ? 0 : size >= 10 ? 1 : 2;
  return `${sign}${size.toFixed(digits)} ${units[unit]}`;
}

function formatGrowth(value: number | null) {
  if (value == null || !Number.isFinite(value)) return "표본 부족";
  if (Math.abs(value) < 1) return "변화 없음";
  return `${value > 0 ? "+" : ""}${formatBytes(value)}/일`;
}

function formatProjectedDays(value: number | null) {
  if (value == null || !Number.isFinite(value) || value <= 0) return "증가 추세 없음";
  if (value < 1) return "1일 미만";
  if (value > 3650) return "10년 이상";
  return `약 ${Math.ceil(value).toLocaleString("ko-KR")}일`;
}

function formatPercent(value: number) {
  return Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

function formatShortDate(value: string) {
  return shortDate.format(new Date(`${value}T00:00:00Z`));
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(new Date(value));
}

function formatAge(ageHours: number) {
  if (ageHours < 48) return `${Math.round(ageHours)}시간`;
  return `${Math.floor(ageHours / 24)}일 ${Math.round(ageHours % 24)}시간`;
}

async function fetchStorage(signal?: AbortSignal) {
  const response = await fetch("/api/storage", { cache: "no-store", signal });
  const payload = (await response.json()) as HostStorageResponse & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error || "저장공간 데이터를 불러오지 못했습니다.");
  }
  return payload;
}
