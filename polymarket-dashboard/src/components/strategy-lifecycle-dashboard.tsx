"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import {
  DASHBOARD_STAGES,
  DASHBOARD_STAGE_LABELS,
  getCheckpointState,
  getDashboardStage,
  getDaysElapsed,
  getEvaluationProgress,
  getHoursUntil,
  getJenkinsHealth,
  getNextCheckpoint,
  getStrategyHealth,
  isStrategyVisibleByClosedToggle,
  type DashboardStage,
  type DynamicCheckpointState,
  type JenkinsHealth,
  type StrategyHealth,
} from "@/lib/strategy-lifecycle";
import type {
  StrategyCheckpoint,
  StrategyJenkinsJob,
  StrategyLifecycle,
  StrategyLifecycleResponse,
} from "@/lib/types";

const STRATEGY_HEALTH_LABELS: Record<StrategyHealth, string> = {
  HEALTHY: "정상",
  ATTENTION: "확인 필요",
  PAUSED: "중지",
  CLOSED: "폐쇄",
  UNKNOWN: "관측 전",
};

const JOB_HEALTH_LABELS: Record<JenkinsHealth, string> = {
  HEALTHY: "성공",
  BUILDING: "실행 중",
  FAILED: "실패",
  STALE: "관측 지연",
  DISABLED: "중지",
  UNKNOWN: "관측 전",
};

const CHECKPOINT_LABELS: Record<DynamicCheckpointState, string> = {
  UPCOMING: "예정",
  DUE: "24시간 이내",
  OVERDUE: "기한 지남",
  UNSCHEDULED: "일정 미정",
  COMPLETED: "완료",
  BLOCKED: "보류",
  CANCELLED: "취소",
};

const MODE_LABELS: Record<StrategyJenkinsJob["mode"], string> = {
  LIVE: "실거래",
  SIMULATION: "시뮬레이션",
  SHADOW: "자료 수집",
  CLOSE_ONLY: "청산 전용",
  RESEARCH: "자료 수집",
};

const dateFormatter = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "numeric",
  day: "numeric",
  timeZone: "Asia/Seoul",
});

const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Seoul",
});

export function StrategyLifecycleDashboard() {
  const [data, setData] = useState<StrategyLifecycleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [hideClosed, setHideClosed] = useState(false);
  const [stageFilter, setStageFilter] = useState<DashboardStage | "ALL">("ALL");
  const [query, setQuery] = useState("");
  const [now, setNow] = useState(() => new Date());

  const loadData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchStrategyLifecycle(signal);
      setData(payload);
      setNow(new Date());
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : "알 수 없는 오류가 발생했습니다.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchStrategyLifecycle(controller.signal)
      .then((payload) => {
        setData(payload);
        setNow(new Date());
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "알 수 없는 오류가 발생했습니다.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const jobsByStrategy = useMemo(
    () => groupBy(data?.jobs ?? [], (job) => job.strategy_id),
    [data?.jobs],
  );
  const checkpointsByStrategy = useMemo(
    () => groupBy(data?.checkpoints ?? [], (checkpoint) => checkpoint.strategy_id),
    [data?.checkpoints],
  );

  const stageCounts = useMemo(() => {
    const counts = Object.fromEntries(DASHBOARD_STAGES.map((stage) => [stage, 0])) as Record<DashboardStage, number>;
    for (const strategy of data?.strategies ?? []) counts[getDashboardStage(strategy.lifecycle_stage)] += 1;
    return counts;
  }, [data?.strategies]);

  const visibleStrategies = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("ko-KR");
    return [...(data?.strategies ?? [])]
      .filter((strategy) => {
        if (!isStrategyVisibleByClosedToggle(strategy, hideClosed)) return false;
        if (stageFilter !== "ALL" && getDashboardStage(strategy.lifecycle_stage) !== stageFilter) return false;
        if (!normalizedQuery) return true;
        const jobs = jobsByStrategy.get(strategy.strategy_id) ?? [];
        return [
          strategy.strategy_id,
          strategy.thesis,
          strategy.current_summary,
          ...jobs.flatMap((job) => [
            job.job_name,
            job.treatment_label ?? "",
            job.purpose ?? "",
            job.test_size_label ?? "",
          ]),
        ].some((value) => value.toLocaleLowerCase("ko-KR").includes(normalizedQuery));
      })
      .sort((left, right) => left.strategy_id.localeCompare(right.strategy_id));
  }, [data?.strategies, hideClosed, jobsByStrategy, query, stageFilter]);

  const latestSync = data?.sync_runs[0] ?? null;
  const collectorState = getCollectorState(latestSync?.status, latestSync?.finished_at, now);
  const runningJobCount = (data?.jobs ?? []).filter((job) => job.enabled !== false).length;

  return (
    <main className="dashboard-shell lifecycle-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">PB</div>
          <div>
            <p className="eyebrow">POLYMARKET BOT / OPERATIONS</p>
            <h1>Strategy Monitor</h1>
          </div>
        </div>
        <div className="topbar-actions">
          <nav className="monitor-nav" aria-label="대시보드 화면">
            <Link href="/">성과</Link>
            <Link href="/storage">저장공간</Link>
            <Link className="selected" href="/strategies" aria-current="page">전략 현황</Link>
          </nav>
          <ThemeToggle />
          <div className="status-cluster">
            <span className={`status-dot ${loading ? "pending" : error || collectorState === "stale" ? "stale" : collectorState === "warning" ? "warning" : ""}`} />
            <span>{loading ? "확인 중" : error ? "연결 오류" : collectorLabel(collectorState)}</span>
            <button className="refresh-button" type="button" onClick={() => void loadData()}>
              새로고침
            </button>
          </div>
        </div>
      </header>

      <section className="lifecycle-intro">
        <div>
          <p className="section-kicker">21 STRATEGY OVERVIEW</p>
          <h2>전체 전략 현황</h2>
          <p>
            golden-apple부터 golden-strawberry까지 현재 단계, 연결된 Jenkins,
            검증 시작일과 다음 판단 시점을 한 화면에서 확인합니다.
          </p>
        </div>
        <div className="lifecycle-intro-stats" aria-label="등록 현황">
          <div><strong>{data?.strategies.length ?? 0}</strong><span>전략</span></div>
          <div><strong>{data?.jobs.length ?? 0}</strong><span>연결 잡</span></div>
          <div><strong>{runningJobCount}</strong><span>실행 가능</span></div>
        </div>
      </section>

      {loading && !data ? (
        <LifecycleLoading />
      ) : error && !data ? (
        <LifecycleError message={error} onRetry={() => void loadData()} />
      ) : data ? (
        <>
          {error && (
            <div className="lifecycle-inline-error" role="status">
              최신 데이터를 다시 읽지 못해 직전 화면을 유지합니다. {error}
            </div>
          )}

          <section className="lifecycle-controls" aria-label="전략 단계 필터">
            <div className="stage-filter-row">
              <button
                className={`stage-filter all ${stageFilter === "ALL" ? "selected" : ""}`}
                type="button"
                onClick={() => setStageFilter("ALL")}
              >
                <span>전체</span>
                <strong>{data.strategies.length}</strong>
              </button>
              {DASHBOARD_STAGES.map((stage) => (
                <button
                  key={stage}
                  className={`stage-filter stage-${stage.toLocaleLowerCase()} ${stageFilter === stage ? "selected" : ""}`}
                  type="button"
                  onClick={() => setStageFilter(stageFilter === stage ? "ALL" : stage)}
                >
                  <span>{DASHBOARD_STAGE_LABELS[stage]}</span>
                  <strong>{stageCounts[stage]}</strong>
                </button>
              ))}
            </div>
            <p className="stage-path" aria-label="전략 진행 순서">
              구현 완료 <span>→</span> 시뮬레이션 <span>→</span> 검증 <span>→</span> 안정화
              <small>검증 실패 시 폐쇄</small>
            </p>
            <div className="strategy-search-row">
              <label>
                <span className="sr-only">전략 또는 Jenkins 잡 검색</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="전략 또는 Jenkins 잡 검색"
                />
              </label>
              <label className="closed-toggle">
                <input
                  type="checkbox"
                  checked={hideClosed}
                  onChange={(event) => {
                    setHideClosed(event.target.checked);
                    if (event.target.checked && stageFilter === "CLOSED") setStageFilter("ALL");
                  }}
                />
                폐쇄 전략 숨기기
              </label>
              <span>{visibleStrategies.length}개 표시</span>
            </div>
          </section>

          <section className="strategy-map-grid" aria-label="전체 전략 현황판">
            {visibleStrategies.map((strategy) => (
              <StrategyTile
                key={strategy.strategy_id}
                strategy={strategy}
                jobs={jobsByStrategy.get(strategy.strategy_id) ?? []}
                checkpoints={checkpointsByStrategy.get(strategy.strategy_id) ?? []}
                jenkinsBaseUrl={data.jenkins_base_url}
                now={now}
              />
            ))}
          </section>

          {!visibleStrategies.length && (
            <section className="lifecycle-empty">
              <p>조건에 맞는 전략이 없습니다.</p>
              <button type="button" onClick={() => { setQuery(""); setStageFilter("ALL"); setHideClosed(false); }}>
                필터 초기화
              </button>
            </section>
          )}

          <footer>
            <span>전략 상태와 Jenkins 연결 정보는 Supabase에서 관리합니다.</span>
            <span>
              {latestSync?.finished_at ? `Jenkins 확인 ${formatDateTime(latestSync.finished_at)}` : "Jenkins 확인 기록 없음"}
              {` · 화면 생성 ${formatDateTime(data.generated_at)}`}
            </span>
          </footer>
        </>
      ) : null}
    </main>
  );
}

function StrategyTile({
  strategy,
  jobs,
  checkpoints,
  jenkinsBaseUrl,
  now,
}: {
  strategy: StrategyLifecycle;
  jobs: StrategyJenkinsJob[];
  checkpoints: StrategyCheckpoint[];
  jenkinsBaseUrl: string | null;
  now: Date;
}) {
  const stage = getDashboardStage(strategy.lifecycle_stage);
  const health = getStrategyHealth(strategy, jobs, now);
  const daysElapsed = getDaysElapsed(strategy.evaluation_started_at, now);
  const progress = getEvaluationProgress(strategy, now);
  const nextCheckpoint = getNextCheckpoint(checkpoints) ?? checkpoints.find((item) => item.status === "BLOCKED") ?? null;
  const plannedEnd = stage === "CLOSED"
    ? strategy.phase_started_at
    : latestTimestamp(jobs.map((job) => job.experiment_ends_at));
  const sortedJobs = [...jobs].sort((left, right) => left.job_name.localeCompare(right.job_name));

  return (
    <article className={`strategy-tile stage-${stage.toLocaleLowerCase()}`}>
      <div className="strategy-tile-accent" aria-hidden="true" />
      <div className="strategy-tile-header">
        <span className="stage-badge">{DASHBOARD_STAGE_LABELS[stage]}</span>
        <span className={`health-badge health-${health.toLocaleLowerCase()}`}>
          {STRATEGY_HEALTH_LABELS[health]}
        </span>
      </div>

      <h3>{strategy.strategy_id}</h3>
      <p className="strategy-thesis">{strategy.thesis}</p>

      <dl className="strategy-timeline">
        <div>
          <dt>검증 시작</dt>
          <dd>{strategy.evaluation_started_at ? formatDate(strategy.evaluation_started_at) : "시작 전"}</dd>
        </div>
        <div>
          <dt>진행</dt>
          <dd>{daysElapsed == null ? "—" : `${daysElapsed}일째`}</dd>
        </div>
        <div>
          <dt>{stage === "CLOSED" ? "폐쇄일" : "종료/판정"}</dt>
          <dd>{plannedEnd ? formatDate(plannedEnd) : nextCheckpoint?.due_at ? formatDate(nextCheckpoint.due_at) : "미정"}</dd>
        </div>
      </dl>

      {progress != null && strategy.evaluation_horizon_days != null && (
        <div className="strategy-progress" aria-label={`${strategy.evaluation_horizon_days}일 검증 중 ${Math.round(progress)}% 경과`}>
          <span style={{ width: `${progress}%` }} />
        </div>
      )}

      <div className="strategy-jobs">
        <div className="strategy-jobs-heading">
          <strong>Jenkins</strong>
          <span>{jobs.length ? `${jobs.length}개` : "연결 없음"}</span>
        </div>
        {sortedJobs.length ? sortedJobs.map((job) => (
          <CompactJob key={job.job_name} job={job} baseUrl={jenkinsBaseUrl} now={now} />
        )) : (
          <p className="no-jobs">현재 연결된 Jenkins 잡이 없습니다.</p>
        )}
      </div>

      <details className="strategy-details">
        <summary>설명과 다음 일정</summary>
        <p>{strategy.current_summary}</p>
        {sortedJobs.length > 0 && (
          <div className="job-purpose-list">
            {sortedJobs.map((job) => (
              <div key={job.job_name}>
                <strong>{job.job_name}</strong>
                <span>{job.purpose ?? "역할 설명 없음"}</span>
                <small>{formatJobWindow(job)} · {job.schedule ?? "수동 실행"}</small>
              </div>
            ))}
          </div>
        )}
        {strategy.attention_note && (
          <div className={`attention-note attention-${strategy.attention_level.toLocaleLowerCase()}`}>
            <strong>확인할 점</strong>
            <span>{strategy.attention_note}</span>
          </div>
        )}
        {nextCheckpoint && <CheckpointSummary checkpoint={nextCheckpoint} now={now} />}
      </details>
    </article>
  );
}

function CompactJob({
  job,
  baseUrl,
  now,
}: {
  job: StrategyJenkinsJob;
  baseUrl: string | null;
  now: Date;
}) {
  const health = getJenkinsHealth(job, now);
  const body = (
    <div className="compact-job">
      <div className="compact-job-title">
        <span className={`job-dot job-${health.toLocaleLowerCase()}`} aria-hidden="true" />
        <strong>{job.job_name}</strong>
        <span>{JOB_HEALTH_LABELS[health]}</span>
      </div>
      <div className="compact-job-labels">
        <span>{job.treatment_label ?? MODE_LABELS[job.mode]}</span>
        {job.test_size_label && <b>{compactTestSize(job.test_size_label)}</b>}
      </div>
      {job.purpose && <p>{job.purpose}</p>}
      <div className="compact-job-meta">
        <span>{MODE_LABELS[job.mode]} · {job.expected_cadence_minutes ? `${job.expected_cadence_minutes}분마다` : "수동"}</span>
        <span>{formatJobWindow(job)}</span>
      </div>
    </div>
  );

  if (!baseUrl) return body;
  return (
    <a href={`${baseUrl}/job/${encodeURIComponent(job.job_name)}/`} target="_blank" rel="noreferrer">
      {body}
    </a>
  );
}

function CheckpointSummary({ checkpoint, now }: { checkpoint: StrategyCheckpoint; now: Date }) {
  const state = getCheckpointState(checkpoint, now);
  return (
    <div className={`checkpoint-summary checkpoint-${state.toLocaleLowerCase()}`}>
      <div>
        <strong>{checkpoint.title}</strong>
        <span>{CHECKPOINT_LABELS[state]}</span>
      </div>
      <p>{checkpoint.instructions}</p>
      <small>{checkpoint.due_at ? `${formatDateTime(checkpoint.due_at)} · ${formatCountdown(checkpoint.due_at, now)}` : "검토일 미정"}</small>
    </div>
  );
}

function compactTestSize(label: string) {
  if (label.includes("신규 거래 없음")) return "청산만";
  if (label.includes("실거래 없음") && label.includes("$5")) return "가상 $5";
  if (label.includes("실거래 없음")) return "거래 없음";
  const amount = label.match(/(?:거래|가상)\s*(\$[\d,]+)/)?.[1];
  return amount ?? label;
}

function formatJobWindow(job: StrategyJenkinsJob) {
  if (job.experiment_started_at && job.experiment_ends_at) {
    return `${formatDate(job.experiment_started_at)} 시작 · ${formatDate(job.experiment_ends_at)} 종료`;
  }
  if (job.experiment_started_at) return `${formatDate(job.experiment_started_at)} 시작 · 상시`;
  if (job.experiment_ends_at) return `${formatDate(job.experiment_ends_at)} 종료`;
  return "일정 미정";
}

function latestTimestamp(values: Array<string | null>) {
  const valid = values.filter((value): value is string => Boolean(value));
  if (!valid.length) return null;
  return valid.sort((left, right) => Date.parse(right) - Date.parse(left))[0];
}

function groupBy<T>(items: T[], key: (item: T) => string) {
  const grouped = new Map<string, T[]>();
  for (const item of items) {
    const value = key(item);
    grouped.set(value, [...(grouped.get(value) ?? []), item]);
  }
  return grouped;
}

function formatDate(value: string) {
  return dateFormatter.format(new Date(value));
}

function formatDateTime(value: string) {
  return dateTimeFormatter.format(new Date(value));
}

function formatCountdown(value: string, now: Date) {
  const hours = getHoursUntil(value, now);
  if (!Number.isFinite(hours)) return "날짜 오류";
  if (hours < 0) {
    const overdueHours = Math.abs(hours);
    return overdueHours < 24 ? `${Math.ceil(overdueHours)}시간 지남` : `${Math.floor(overdueHours / 24)}일 지남`;
  }
  if (hours < 24) return `${Math.max(1, Math.ceil(hours))}시간 후`;
  return `${Math.ceil(hours / 24)}일 후`;
}

function getCollectorState(status: string | undefined, finishedAt: string | null | undefined, now: Date) {
  if (!status || !finishedAt) return "warning" as const;
  if (status === "FAILED" || status === "PARTIAL") return "stale" as const;
  if (now.getTime() - Date.parse(finishedAt) > 30 * 60_000) return "stale" as const;
  return "fresh" as const;
}

function collectorLabel(state: "fresh" | "warning" | "stale") {
  if (state === "fresh") return "Jenkins 최신";
  if (state === "stale") return "Jenkins 관측 지연";
  return "Jenkins 수집 전";
}

async function fetchStrategyLifecycle(signal?: AbortSignal) {
  const response = await fetch("/api/strategies", { signal, cache: "no-store" });
  const payload = (await response.json()) as StrategyLifecycleResponse | { error?: string };
  if (!response.ok || !("strategies" in payload)) {
    throw new Error("error" in payload && payload.error ? payload.error : "전략 데이터를 불러오지 못했습니다.");
  }
  return payload;
}

function LifecycleLoading() {
  return (
    <section className="strategy-map-grid lifecycle-loading" aria-label="전략 데이터 로딩 중">
      {Array.from({ length: 12 }, (_, index) => <div key={index} />)}
    </section>
  );
}

function LifecycleError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="lifecycle-error">
      <strong>전략 현황을 불러오지 못했습니다.</strong>
      <p>{message}</p>
      <button type="button" onClick={onRetry}>다시 시도</button>
    </section>
  );
}
