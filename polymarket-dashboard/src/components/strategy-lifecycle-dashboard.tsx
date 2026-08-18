"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getCheckpointState,
  getDaysElapsed,
  getEvaluationProgress,
  getHoursUntil,
  getJenkinsHealth,
  getNextCheckpoint,
  getStrategyHealth,
  isStrategyVisibleByClosedToggle,
  LIFECYCLE_STAGES,
  LIFECYCLE_STAGE_LABELS,
  type DynamicCheckpointState,
  type JenkinsHealth,
  type StrategyHealth,
} from "@/lib/strategy-lifecycle";
import type {
  LifecycleStage,
  OperatingStatus,
  StrategyCheckpoint,
  StrategyJenkinsJob,
  StrategyLifecycle,
  StrategyLifecycleResponse,
} from "@/lib/types";

const STAGE_TONES: Record<LifecycleStage, string> = {
  IDEA: "border-slate-500/40 bg-slate-500/10 text-slate-300",
  IMPLEMENTING: "border-sky-500/40 bg-sky-500/10 text-sky-200",
  IMPLEMENTED: "border-cyan-500/40 bg-cyan-500/10 text-cyan-200",
  SIMULATION: "border-violet-500/40 bg-violet-500/10 text-violet-200",
  LIVE_VALIDATION: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  STABILIZATION: "border-orange-500/40 bg-orange-500/10 text-orange-200",
  PROFITABILITY: "border-lime-500/40 bg-lime-500/10 text-lime-200",
  PRODUCTION: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  CLOSED: "border-zinc-600/50 bg-zinc-700/20 text-zinc-400",
};

const STRATEGY_HEALTH_TONES: Record<StrategyHealth, string> = {
  HEALTHY: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  ATTENTION: "border-rose-500/40 bg-rose-500/10 text-rose-200",
  PAUSED: "border-slate-500/40 bg-slate-500/10 text-slate-300",
  CLOSED: "border-zinc-600/50 bg-zinc-700/20 text-zinc-400",
  UNKNOWN: "border-yellow-500/40 bg-yellow-500/10 text-yellow-200",
};

const STRATEGY_HEALTH_LABELS: Record<StrategyHealth, string> = {
  HEALTHY: "정상",
  ATTENTION: "확인 필요",
  PAUSED: "중지",
  CLOSED: "폐쇄",
  UNKNOWN: "관측 전",
};

const JOB_HEALTH_TONES: Record<JenkinsHealth, string> = {
  HEALTHY: "bg-emerald-400",
  BUILDING: "bg-sky-400 animate-pulse",
  FAILED: "bg-rose-400",
  STALE: "bg-orange-400",
  DISABLED: "bg-slate-500",
  UNKNOWN: "bg-yellow-400",
};

const JOB_HEALTH_LABELS: Record<JenkinsHealth, string> = {
  HEALTHY: "성공",
  BUILDING: "빌드 중",
  FAILED: "실패",
  STALE: "관측 지연",
  DISABLED: "중지",
  UNKNOWN: "관측 전",
};

const CHECKPOINT_TONES: Record<DynamicCheckpointState, string> = {
  UPCOMING: "border-[#345047] bg-[#10201c] text-[#b5cac3]",
  DUE: "border-amber-500/50 bg-amber-500/10 text-amber-200",
  OVERDUE: "border-rose-500/50 bg-rose-500/10 text-rose-200",
  UNSCHEDULED: "border-slate-600 bg-slate-700/10 text-slate-400",
  COMPLETED: "border-emerald-500/35 bg-emerald-500/8 text-emerald-300",
  BLOCKED: "border-orange-500/40 bg-orange-500/10 text-orange-200",
  CANCELLED: "border-zinc-700 bg-zinc-800/30 text-zinc-500",
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

const OPERATING_LABELS: Record<OperatingStatus, string> = {
  ACTIVE: "실행 중",
  PAUSED: "일시 중지",
  CLOSE_ONLY: "청산 전용",
  INACTIVE: "미배치",
  CLOSED: "종료",
};

const KIND_LABELS = {
  LIVE_TRADING: "LIVE TRADING",
  SIMULATION_RESEARCH: "SIMULATION RESEARCH",
  INFRA_RESEARCH: "DATA RESEARCH",
  LEGACY_TRADING: "LEGACY",
} as const;

const MODE_LABELS = {
  LIVE: "LIVE",
  SIMULATION: "SIM",
  SHADOW: "SHADOW",
  CLOSE_ONLY: "CLOSE",
  RESEARCH: "RESEARCH",
} as const;

const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Seoul",
});

const dateFormatter = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "Asia/Seoul",
});

export function StrategyLifecycleDashboard() {
  const [data, setData] = useState<StrategyLifecycleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  const [stageFilter, setStageFilter] = useState<LifecycleStage | "ALL">("ALL");
  const [statusFilter, setStatusFilter] = useState<OperatingStatus | "ALL">("ALL");
  const [query, setQuery] = useState("");
  const [now, setNow] = useState(() => new Date());

  const loadData = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/strategies", { signal, cache: "no-store" });
      const payload = (await response.json()) as StrategyLifecycleResponse | { error?: string };
      if (!response.ok || !("strategies" in payload)) {
        throw new Error("error" in payload && payload.error ? payload.error : "전략 데이터를 불러오지 못했습니다.");
      }
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

  const visibleStrategies = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("ko-KR");
    return (data?.strategies ?? []).filter((strategy) => {
      if (!isStrategyVisibleByClosedToggle(strategy, showClosed)) return false;
      if (stageFilter !== "ALL" && strategy.lifecycle_stage !== stageFilter) return false;
      if (statusFilter !== "ALL" && strategy.operating_status !== statusFilter) return false;
      if (!normalizedQuery) return true;
      const jobs = jobsByStrategy.get(strategy.strategy_id) ?? [];
      return [
        strategy.strategy_id,
        strategy.display_name,
        strategy.codename,
        strategy.thesis,
        ...jobs.flatMap((job) => [job.job_name, job.runtime_job ?? "", job.treatment_label ?? ""]),
      ].some((value) => value.toLocaleLowerCase("ko-KR").includes(normalizedQuery));
    });
  }, [data?.strategies, jobsByStrategy, query, showClosed, stageFilter, statusFilter]);

  const openStrategies = (data?.strategies ?? []).filter((strategy) => strategy.lifecycle_stage !== "CLOSED");
  const closedCount = (data?.strategies ?? []).length - openStrategies.length;
  const activeJobCount = (data?.jobs ?? []).filter((job) => job.enabled !== false).length;
  const attentionCount = openStrategies.filter(
    (strategy) => getStrategyHealth(strategy, jobsByStrategy.get(strategy.strategy_id) ?? [], now) === "ATTENTION",
  ).length;
  const pendingCheckpoints = (data?.checkpoints ?? []).filter((checkpoint) => {
    const strategy = data?.strategies.find((item) => item.strategy_id === checkpoint.strategy_id);
    return strategy?.lifecycle_stage !== "CLOSED" && checkpoint.status === "PENDING" && checkpoint.due_at;
  });
  const overdueCount = pendingCheckpoints.filter(
    (checkpoint) => getCheckpointState(checkpoint, now) === "OVERDUE",
  ).length;
  const latestSync = data?.sync_runs[0] ?? null;
  const collectorState = getCollectorState(latestSync?.status, latestSync?.finished_at, now);

  const reviewLane = [...pendingCheckpoints]
    .sort((left, right) => Date.parse(left.due_at!) - Date.parse(right.due_at!))
    .slice(0, 8);

  return (
    <main className="dashboard-shell lifecycle-shell overflow-x-clip">
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
            <Link href="/storage">저장공간</Link>
            <Link className="selected" href="/strategies" aria-current="page">전략 단계</Link>
          </nav>
          <div className="status-cluster">
            <span className={`status-dot ${loading ? "pending" : error || collectorState === "stale" ? "stale" : collectorState === "warning" ? "warning" : ""}`} />
            <span>{loading ? "데이터 확인 중" : error ? "데이터 연결 오류" : collectorLabel(collectorState)}</span>
            {latestSync?.finished_at && <span className="status-date">수집 {formatDateTime(latestSync.finished_at)}</span>}
            <button className="refresh-button" type="button" onClick={() => void loadData()}>
              새로고침
            </button>
          </div>
        </div>
      </header>

      <section className="grid gap-8 border-b border-[#192824] py-10 lg:grid-cols-[1.25fr_0.75fr] lg:items-end">
        <div className="min-w-0">
          <p className="section-kicker">STRATEGY LIFECYCLE CONTROL ROOM</p>
          <h2 className="mt-4 max-w-4xl break-words text-[2rem] font-semibold leading-[1.12] tracking-[-0.04em] text-[#edf5f2] sm:text-5xl">
            무엇을 만들고, 검증하고,<br className="hidden sm:block" /> 멈춰야 하는지 한눈에 봅니다.
          </h2>
        </div>
        <p className="min-w-0 max-w-xl text-sm leading-7 text-[#93a69f] lg:justify-self-end">
          전략 단계와 실행 상태는 별개입니다. 빌드가 성공해도 안정화 결함이 있으면 “확인 필요”로 남고,
          폐쇄 전략은 기본 화면에서 숨깁니다. 모든 날짜는 KST로 표시합니다.
        </p>
      </section>

      {loading && <LifecycleLoading />}
      {!loading && error && <LifecycleError message={error} onRetry={() => void loadData()} />}

      {!loading && !error && data && (
        <div className="space-y-8 py-8">
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="전략 운영 요약">
            <KpiCard label="기본 표시 전략" value={`${openStrategies.length}개`} detail={`폐쇄 ${closedCount}개 숨김`} />
            <KpiCard label="Jenkins 잡" value={`${activeJobCount}/${data.jobs.length}`} detail="enabled / 등록" tone="text-emerald-300" />
            <KpiCard label="확인 필요" value={`${attentionCount}개`} detail="전략 결함 또는 Jenkins 상태" tone={attentionCount ? "text-rose-300" : "text-emerald-300"} />
            <KpiCard label="기한 지난 검토" value={`${overdueCount}개`} detail={`${pendingCheckpoints.length}개 예정·대기`} tone={overdueCount ? "text-rose-300" : "text-emerald-300"} />
          </section>

          <section className="rounded-2xl border border-[#21332e] bg-[#0c1714]/90 p-5 shadow-[0_20px_80px_rgba(0,0,0,0.18)]">
            <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="section-kicker">PORTFOLIO PIPELINE</p>
                <h3 className="mt-2 text-lg font-semibold text-[#edf5f2]">현재 생애주기 분포</h3>
              </div>
              <button
                type="button"
                onClick={() => setStageFilter("ALL")}
                className="text-xs text-[#8da098] transition hover:text-[#edf5f2]"
              >
                전체 단계 보기
              </button>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-9">
              {LIFECYCLE_STAGES.map((stage, index) => {
                const count = data.strategies.filter((strategy) => strategy.lifecycle_stage === stage).length;
                const selected = stageFilter === stage;
                return (
                  <button
                    key={stage}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => {
                      setStageFilter(selected ? "ALL" : stage);
                      if (stage === "CLOSED") setShowClosed(true);
                    }}
                    className={`group relative min-h-28 rounded-xl border p-4 text-left transition ${STAGE_TONES[stage]} ${selected ? "ring-2 ring-[#8de0c1]/50" : "hover:-translate-y-0.5 hover:border-current/60"}`}
                  >
                    <span className="font-mono text-[10px] opacity-60">{String(index + 1).padStart(2, "0")}</span>
                    <strong className="mt-5 block text-2xl tabular-nums">{count}</strong>
                    <span className="mt-1 block text-xs font-medium">{LIFECYCLE_STAGE_LABELS[stage]}</span>
                  </button>
                );
              })}
            </div>
          </section>

          <section className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
            <div className="self-start rounded-2xl border border-[#21332e] bg-[#0c1714]/90 p-5">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <p className="section-kicker">REVIEW RADAR</p>
                  <h3 className="mt-2 text-lg font-semibold">다가오는 검토</h3>
                </div>
                <span className="font-mono text-[10px] text-[#71877f]">KST</span>
              </div>
              <div className="space-y-2">
                {reviewLane.length ? reviewLane.map((checkpoint) => {
                  const strategy = data.strategies.find((item) => item.strategy_id === checkpoint.strategy_id);
                  const state = getCheckpointState(checkpoint, now);
                  return (
                    <button
                      key={checkpoint.checkpoint_id}
                      type="button"
                      onClick={() => {
                        setQuery(strategy?.strategy_id ?? "");
                        setStageFilter("ALL");
                      }}
                      className="grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 rounded-xl border border-[#1b2b27] bg-[#0a1311] p-3 text-left transition hover:border-[#365149]"
                    >
                      <span className={`h-2 w-2 rounded-full ${state === "OVERDUE" ? "bg-rose-400" : state === "DUE" ? "bg-amber-400" : "bg-[#68c7a4]"}`} />
                      <span className="min-w-0">
                        <span className="block truncate text-xs font-semibold text-[#dbe7e3]">{strategy?.codename ?? checkpoint.strategy_id}</span>
                        <span className="mt-1 block truncate text-[11px] text-[#71877f]">{checkpoint.title}</span>
                      </span>
                      <span className={`rounded-md border px-2 py-1 text-[10px] ${CHECKPOINT_TONES[state]}`}>
                        {formatCountdown(checkpoint.due_at!, now)}
                      </span>
                    </button>
                  );
                }) : (
                  <p className="rounded-xl border border-dashed border-[#294038] p-5 text-sm text-[#71877f]">예정된 검토가 없습니다.</p>
                )}
              </div>
            </div>

            <div className="self-start rounded-2xl border border-[#21332e] bg-[#0c1714]/90 p-5">
              <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
                <div>
                  <p className="section-kicker">FILTER & FIND</p>
                  <h3 className="mt-2 text-lg font-semibold">전략 카탈로그</h3>
                </div>
                <span className="text-xs text-[#71877f]">현재 {visibleStrategies.length}개 표시</span>
              </div>
              <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
                <label className="relative">
                  <span className="sr-only">전략 또는 Jenkins 잡 검색</span>
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="전략, 가설, Jenkins 잡 검색"
                    className="h-11 w-full rounded-xl border border-[#294038] bg-[#08110f] px-4 text-sm text-[#edf5f2] outline-none transition placeholder:text-[#52665f] focus:border-[#68c7a4]"
                  />
                </label>
                <select
                  aria-label="운영 상태"
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value as OperatingStatus | "ALL")}
                  className="h-11 rounded-xl border border-[#294038] bg-[#08110f] px-3 text-xs text-[#c8d6d1] outline-none focus:border-[#68c7a4]"
                >
                  <option value="ALL">모든 운영 상태</option>
                  {Object.entries(OPERATING_LABELS).map(([status, label]) => <option key={status} value={status}>{label}</option>)}
                </select>
                <label className="flex h-11 cursor-pointer items-center gap-3 rounded-xl border border-[#294038] bg-[#08110f] px-4 text-xs text-[#a9bbb5]">
                  <input
                    type="checkbox"
                    checked={showClosed}
                    onChange={(event) => {
                      setShowClosed(event.target.checked);
                      if (!event.target.checked && stageFilter === "CLOSED") setStageFilter("ALL");
                    }}
                    className="h-4 w-4 accent-[#8de0c1]"
                  />
                  폐쇄 {closedCount}개 보기
                </label>
              </div>
            </div>
          </section>

          <section className="grid gap-4 xl:grid-cols-2" aria-label="전략 상세 목록">
            {visibleStrategies.map((strategy) => (
              <StrategyCard
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
            <section className="rounded-2xl border border-dashed border-[#294038] py-16 text-center">
              <p className="text-sm text-[#93a69f]">조건에 맞는 전략이 없습니다.</p>
              <button
                type="button"
                onClick={() => { setQuery(""); setStageFilter("ALL"); setStatusFilter("ALL"); }}
                className="mt-3 text-xs text-[#8de0c1] hover:underline"
              >
                필터 초기화
              </button>
            </section>
          )}

          <footer className="flex flex-wrap justify-between gap-3 border-t border-[#192824] pt-5 text-[11px] text-[#62766f]">
            <span>Supabase `pd_*` lifecycle registry · Jenkins metadata collector</span>
            <span>API 생성 {formatDateTime(data.generated_at)} · 수익성은 별도 strict evidence audit로 판정</span>
          </footer>
        </div>
      )}
    </main>
  );
}

function StrategyCard({
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
  const health = getStrategyHealth(strategy, jobs, now);
  const daysElapsed = getDaysElapsed(strategy.evaluation_started_at, now);
  const progress = getEvaluationProgress(strategy, now);
  const nextCheckpoint = getNextCheckpoint(checkpoints);
  const blockedCheckpoint = checkpoints.find((checkpoint) => checkpoint.status === "BLOCKED");

  return (
    <article className={`overflow-hidden rounded-2xl border bg-[#0c1714]/95 shadow-[0_18px_70px_rgba(0,0,0,0.16)] ${health === "ATTENTION" ? "border-rose-500/35" : "border-[#21332e]"}`}>
      <div className="p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-md border px-2 py-1 font-mono text-[9px] tracking-wider ${STAGE_TONES[strategy.lifecycle_stage]}`}>
                {LIFECYCLE_STAGE_LABELS[strategy.lifecycle_stage]}
              </span>
              <span className="font-mono text-[9px] tracking-wider text-[#71877f]">{KIND_LABELS[strategy.strategy_kind]}</span>
            </div>
            <h3 className="mt-4 truncate text-2xl font-semibold tracking-[-0.03em] text-[#edf5f2]">{strategy.codename}</h3>
            <p className="mt-1 font-mono text-[10px] text-[#6f837d]">{strategy.strategy_id}</p>
          </div>
          <span className={`rounded-full border px-3 py-1 text-[10px] font-semibold ${STRATEGY_HEALTH_TONES[health]}`}>
            {STRATEGY_HEALTH_LABELS[health]}
          </span>
        </div>

        <p className="mt-5 text-sm leading-6 text-[#b5c5c0]">{strategy.thesis}</p>
        <p className="mt-3 text-xs leading-5 text-[#788d86]">{strategy.current_summary}</p>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <InfoCell label="운영 상태" value={OPERATING_LABELS[strategy.operating_status]} />
          <InfoCell label="검증 경과" value={daysElapsed == null ? "시작 전" : `${daysElapsed}일째`} />
          <InfoCell
            label="다음 검토"
            value={nextCheckpoint?.due_at ? formatCountdown(nextCheckpoint.due_at, now) : blockedCheckpoint ? "일정 보류" : "미정"}
          />
        </div>

        {progress != null && strategy.evaluation_horizon_days != null && (
          <div className="mt-4">
            <div className="mb-2 flex items-center justify-between font-mono text-[9px] text-[#6f837d]">
              <span>{strategy.evaluation_horizon_days}일 평가 구간</span>
              <span>{Math.round(progress)}%</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-[#182823]">
              <div className="h-full rounded-full bg-gradient-to-r from-[#4d9b7f] to-[#8de0c1]" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {strategy.attention_note && (
          <div className={`mt-5 rounded-xl border p-4 ${attentionPanelTone(strategy.attention_level)}`}>
            <p className="font-mono text-[9px] font-semibold tracking-wider">{attentionPanelLabel(strategy.attention_level)}</p>
            <p className="mt-2 text-xs leading-5 opacity-80">{strategy.attention_note}</p>
          </div>
        )}

        <div className="mt-5 border-t border-[#1a2a26] pt-5">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="font-mono text-[10px] font-semibold tracking-wider text-[#91a79f]">JENKINS JOBS</h4>
            <span className="text-[10px] text-[#60736d]">{jobs.length}개 연결</span>
          </div>
          {jobs.length ? (
            <div className="space-y-2">
              {jobs.map((job) => (
                <JobRow key={job.job_name} job={job} baseUrl={jenkinsBaseUrl} now={now} />
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-[#294038] px-4 py-5 text-xs text-[#657a73]">
              현재 연결된 Jenkins job이 없습니다.
            </div>
          )}
        </div>

        {(nextCheckpoint || blockedCheckpoint) && (
          <div className="mt-5 border-t border-[#1a2a26] pt-5">
            <h4 className="mb-3 font-mono text-[10px] font-semibold tracking-wider text-[#91a79f]">NEXT ACTION</h4>
            <CheckpointRow checkpoint={nextCheckpoint ?? blockedCheckpoint!} now={now} />
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#1a2a26] bg-[#091310] px-5 py-3 font-mono text-[9px] text-[#5f736c] sm:px-6">
        <span>근거: {strategy.source_ref}</span>
        <span>{strategy.phase_started_at ? `현재 단계 ${dateFormatter.format(new Date(strategy.phase_started_at))} 시작` : "단계 시작일 미확정"}</span>
      </div>
    </article>
  );
}

function JobRow({ job, baseUrl, now }: { job: StrategyJenkinsJob; baseUrl: string | null; now: Date }) {
  const health = getJenkinsHealth(job, now);
  const content = (
    <div className="grid gap-3 rounded-xl border border-[#1b2b27] bg-[#091310] p-3 transition hover:border-[#314a43] sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`h-2 w-2 rounded-full ${JOB_HEALTH_TONES[health]}`} />
          <strong className="truncate text-xs font-semibold text-[#d8e5e0]">{job.job_name}</strong>
          <span className="rounded bg-[#172621] px-1.5 py-0.5 font-mono text-[8px] text-[#8ca198]">{MODE_LABELS[job.mode]}</span>
          {job.workspace_class === "EXTERNAL" && <span className="font-mono text-[8px] text-violet-300">EXTERNAL</span>}
        </div>
        <p className="mt-1.5 truncate text-[10px] text-[#657a73]">
          {job.treatment_label ?? job.runtime_job ?? "default"} · {job.schedule ?? "스케줄 미확정"}
        </p>
      </div>
      <div className="flex items-center justify-between gap-4 sm:justify-end">
        <span className="text-[10px] text-[#7e928b]">{JOB_HEALTH_LABELS[health]}</span>
        <span className="min-w-20 text-right font-mono text-[9px] text-[#5f736c]">
          {job.last_build_number ? `#${job.last_build_number}` : "build —"}
          {job.observed_at ? ` · ${formatRelativeObservation(job.observed_at, now)}` : " · 미관측"}
        </span>
      </div>
    </div>
  );

  if (!baseUrl) return content;
  return (
    <a href={`${baseUrl}/job/${encodeURIComponent(job.job_name)}/`} target="_blank" rel="noreferrer" className="block">
      {content}
    </a>
  );
}

function CheckpointRow({ checkpoint, now }: { checkpoint: StrategyCheckpoint; now: Date }) {
  const state = getCheckpointState(checkpoint, now);
  return (
    <div className={`rounded-xl border p-4 ${CHECKPOINT_TONES[state]}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <strong className="text-xs">{checkpoint.title}</strong>
        <span className="rounded-md border border-current/20 px-2 py-1 font-mono text-[9px]">{CHECKPOINT_LABELS[state]}</span>
      </div>
      <p className="mt-2 text-[11px] leading-5 opacity-75">{checkpoint.instructions}</p>
      <p className="mt-3 font-mono text-[9px] opacity-60">
        {checkpoint.due_at ? `${formatDateTime(checkpoint.due_at)} · ${formatCountdown(checkpoint.due_at, now)}` : "검토일 미정"}
      </p>
    </div>
  );
}

function InfoCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[#1b2b27] bg-[#091310] px-3 py-3">
      <span className="block font-mono text-[8px] tracking-wider text-[#60736d]">{label}</span>
      <strong className="mt-1.5 block text-xs font-medium text-[#c9d7d2]">{value}</strong>
    </div>
  );
}

function KpiCard({ label, value, detail, tone = "text-[#edf5f2]" }: { label: string; value: string; detail: string; tone?: string }) {
  return (
    <div className="rounded-2xl border border-[#21332e] bg-[#0c1714]/90 p-5">
      <p className="font-mono text-[9px] tracking-wider text-[#6f837d]">{label}</p>
      <strong className={`mt-3 block text-3xl font-semibold tracking-[-0.04em] tabular-nums ${tone}`}>{value}</strong>
      <p className="mt-2 text-[11px] text-[#71877f]">{detail}</p>
    </div>
  );
}

function LifecycleLoading() {
  return (
    <section className="grid gap-4 py-8 sm:grid-cols-2 xl:grid-cols-4" aria-label="전략 데이터 로딩 중">
      {[0, 1, 2, 3].map((item) => <div key={item} className="h-32 animate-pulse rounded-2xl border border-[#21332e] bg-[#0c1714]" />)}
    </section>
  );
}

function LifecycleError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="my-8 rounded-2xl border border-rose-500/30 bg-rose-500/8 p-8 text-center">
      <strong className="text-sm text-rose-200">전략 생애주기를 불러오지 못했습니다.</strong>
      <p className="mt-2 text-xs text-rose-100/65">{message}</p>
      <button type="button" onClick={onRetry} className="mt-5 rounded-lg border border-rose-400/40 px-4 py-2 text-xs text-rose-100 hover:bg-rose-500/10">다시 시도</button>
    </section>
  );
}

function groupBy<T>(items: T[], key: (item: T) => string) {
  const grouped = new Map<string, T[]>();
  for (const item of items) {
    const value = key(item);
    grouped.set(value, [...(grouped.get(value) ?? []), item]);
  }
  return grouped;
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

function formatRelativeObservation(value: string, now: Date) {
  const minutes = Math.max(0, Math.floor((now.getTime() - Date.parse(value)) / 60_000));
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  return `${Math.floor(minutes / 60)}시간 전`;
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

function attentionPanelTone(level: StrategyLifecycle["attention_level"]) {
  if (level === "CRITICAL") return "border-rose-500/25 bg-rose-500/7 text-rose-200";
  if (level === "WATCH") return "border-amber-500/25 bg-amber-500/7 text-amber-200";
  return "border-sky-500/20 bg-sky-500/5 text-sky-200";
}

function attentionPanelLabel(level: StrategyLifecycle["attention_level"]) {
  if (level === "CRITICAL") return "ATTENTION";
  if (level === "WATCH") return "WATCH";
  return "CONTEXT";
}

async function fetchStrategyLifecycle(signal?: AbortSignal) {
  const response = await fetch("/api/strategies", { signal, cache: "no-store" });
  const payload = (await response.json()) as StrategyLifecycleResponse | { error?: string };
  if (!response.ok || !("strategies" in payload)) {
    throw new Error("error" in payload && payload.error ? payload.error : "전략 데이터를 불러오지 못했습니다.");
  }
  return payload;
}
