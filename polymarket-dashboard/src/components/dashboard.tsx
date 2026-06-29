"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  buildChartRows,
  getDateBounds,
  getPerformance,
  getPortfolioPerformance,
  subtractDays,
} from "@/lib/analytics";
import type {
  AlgorithmAccount,
  BalanceMetric,
  ChartMode,
  PerformanceSummary,
  PortfolioResponse,
} from "@/lib/types";

const ACCOUNT_COLORS: Record<string, string> = {
  "golden-apple-1": "#f7b955",
  "golden-banana": "#f2db5b",
  "golden-cherry": "#ff7380",
  "golden-apple-2": "#a98cff",
};

const METRICS: { value: BalanceMetric; label: string }[] = [
  { value: "total_value", label: "총 잔고" },
  { value: "position_value", label: "포지션" },
  { value: "cash_value", label: "현금" },
];

const DEFAULT_RANGE_DAYS = 30;

const amount = new Intl.NumberFormat("en-US", {
  style: "decimal",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const compactAmount = new Intl.NumberFormat("en-US", {
  style: "decimal",
  notation: "compact",
  maximumFractionDigits: 1,
});

const shortDate = new Intl.DateTimeFormat("ko-KR", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const COIN_EMOJIS = ["💎", "🟡", "⭐"];
const DEFAULT_COIN_EMOJI = "🟡";
const COIN_STORAGE_KEY = "pb-coin-emoji";
const CoinEmojiContext = createContext(DEFAULT_COIN_EMOJI);

const coinEmojiListeners = new Set<() => void>();

function subscribeCoinEmoji(listener: () => void) {
  coinEmojiListeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    coinEmojiListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function getCoinEmojiSnapshot() {
  try {
    const stored = window.localStorage.getItem(COIN_STORAGE_KEY);
    return stored && COIN_EMOJIS.includes(stored) ? stored : DEFAULT_COIN_EMOJI;
  } catch {
    return DEFAULT_COIN_EMOJI;
  }
}

function setStoredCoinEmoji(emoji: string) {
  try {
    window.localStorage.setItem(COIN_STORAGE_KEY, emoji);
  } catch {
    // localStorage 사용 불가(시크릿 모드 등) — 캐싱은 생략한다
  }
  coinEmojiListeners.forEach((listener) => listener());
}

export function Dashboard() {
  const [data, setData] = useState<PortfolioResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [metric, setMetric] = useState<BalanceMetric>("total_value");
  const [chartMode, setChartMode] = useState<ChartMode>("balance");
  const coinEmoji = useSyncExternalStore(
    subscribeCoinEmoji,
    getCoinEmojiSnapshot,
    () => DEFAULT_COIN_EMOJI,
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await fetchPortfolio();
      setData(payload);
      setSelectedAccountIds(payload.accounts.map((account) => account.account_id));
      const { minDate, maxDate } = getDateBounds(payload.totals);
      setStartDate(defaultStartDate(minDate, maxDate));
      setEndDate(maxDate);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "알 수 없는 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchPortfolio(controller.signal)
      .then((payload) => {
        setData(payload);
        setSelectedAccountIds(payload.accounts.map((account) => account.account_id));
        const { minDate, maxDate } = getDateBounds(payload.totals);
        setStartDate(defaultStartDate(minDate, maxDate));
        setEndDate(maxDate);
      })
      .catch((caught) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "알 수 없는 오류가 발생했습니다.");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const bounds = useMemo(
    () => getDateBounds(data?.totals ?? []),
    [data?.totals],
  );
  const accountMap = useMemo(
    () => new Map((data?.accounts ?? []).map((account) => [account.account_id, account])),
    [data?.accounts],
  );
  const selectedAccounts = useMemo(
    () =>
      (data?.accounts ?? []).filter((account) =>
        selectedAccountIds.includes(account.account_id),
      ),
    [data?.accounts, selectedAccountIds],
  );
  const performances = useMemo(
    () =>
      selectedAccounts
        .map((account) =>
          getPerformance(data?.balances ?? [], account.account_id, startDate, endDate),
        )
        .filter((value): value is PerformanceSummary => value !== null),
    [data?.balances, endDate, selectedAccounts, startDate],
  );
  const performanceMap = useMemo(
    () => new Map(performances.map((performance) => [performance.accountId, performance])),
    [performances],
  );
  const portfolioPerformance = useMemo(
    () => getPortfolioPerformance(data?.totals ?? [], startDate, endDate),
    [data?.totals, endDate, startDate],
  );
  const chartRows = useMemo(
    () =>
      buildChartRows(
        data?.balances ?? [],
        selectedAccountIds,
        startDate,
        endDate,
        metric,
        chartMode,
      ),
    [chartMode, data?.balances, endDate, metric, selectedAccountIds, startDate],
  );

  function toggleAccount(accountId: string) {
    setSelectedAccountIds((current) =>
      current.includes(accountId)
        ? current.filter((value) => value !== accountId)
        : [...current, accountId],
    );
  }

  function applyPreset(days: number | "all") {
    if (!bounds.minDate || !bounds.maxDate) return;
    setEndDate(bounds.maxDate);
    setStartDate(
      days === "all"
        ? bounds.minDate
        : maxDate(bounds.minDate, subtractDays(bounds.maxDate, days - 1)),
    );
  }

  const rangeIsValid = Boolean(startDate && endDate && startDate <= endDate);

  return (
    <CoinEmojiContext.Provider value={coinEmoji}>
    <main className="dashboard-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            PB
          </div>
          <div>
            <p className="eyebrow">POLYMARKET BOT / PERFORMANCE DESK</p>
            <h1>Strategy Monitor</h1>
          </div>
        </div>
        <div className="status-cluster">
          <span className="status-dot" />
          <span>Supabase live</span>
          {bounds.maxDate && <span className="status-date">최근 {formatDate(bounds.maxDate)}</span>}
          <CoinPicker value={coinEmoji} onChange={setStoredCoinEmoji} />
          <button className="refresh-button" type="button" onClick={() => void loadData()}>
            새로고침
          </button>
        </div>
      </header>

      <section className="hero-row">
        <div>
          <p className="section-kicker">PORTFOLIO OBSERVATORY</p>
          <h2>네 개 전략의 잔고와 수익률을<br />한 화면에서 비교합니다.</h2>
        </div>
        <p className="hero-note">
          수익률은 선택 기간의 첫 잔고와 마지막 잔고를 비교합니다.
          입금·출금 보정은 적용하지 않았으므로 자금 이동일은 기간에서 제외하세요.
        </p>
      </section>

      {loading && <LoadingState />}
      {!loading && error && <ErrorState message={error} onRetry={loadData} />}

      {!loading && !error && data && (
        <>
          <section className="control-panel" aria-label="조회 조건">
            <div className="control-group strategy-control">
              <span className="control-label">전략 선택</span>
              <div className="strategy-toggles">
                {data.accounts.map((account) => {
                  const active = selectedAccountIds.includes(account.account_id);
                  const color = ACCOUNT_COLORS[account.account_id] ?? "#8de0c1";
                  return (
                    <button
                      key={account.account_id}
                      type="button"
                      className={`strategy-toggle ${active ? "active" : ""}`}
                      aria-pressed={active}
                      onClick={() => toggleAccount(account.account_id)}
                      style={{ "--strategy-color": color } as React.CSSProperties}
                    >
                      <span className="strategy-indicator" />
                      <span>{displayName(account)}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="control-divider" />

            <div className="control-group date-control">
              <div className="control-heading">
                <span className="control-label">조회 기간</span>
                <div className="preset-row">
                  {[7, 30, 90].map((days) => (
                    <button key={days} type="button" onClick={() => applyPreset(days)}>
                      {days}D
                    </button>
                  ))}
                  <button type="button" onClick={() => applyPreset("all")}>전체</button>
                </div>
              </div>
              <div className="date-inputs">
                <label>
                  <span>시작</span>
                  <input
                    type="date"
                    min={bounds.minDate}
                    max={endDate || bounds.maxDate}
                    value={startDate}
                    onChange={(event) => setStartDate(event.target.value)}
                  />
                </label>
                <span className="date-separator">→</span>
                <label>
                  <span>종료</span>
                  <input
                    type="date"
                    min={startDate || bounds.minDate}
                    max={bounds.maxDate}
                    value={endDate}
                    onChange={(event) => setEndDate(event.target.value)}
                  />
                </label>
              </div>
              {!rangeIsValid && <p className="field-error">올바른 기간을 선택하세요.</p>}
            </div>
          </section>

          <section className="kpi-grid" aria-label="포트폴리오 요약">
            <KpiCard
              label="현재 전체 자산"
              value={<Money value={portfolioPerformance?.last.total_value} />}
              detail={portfolioPerformance ? formatDate(portfolioPerformance.last.report_date) : "-"}
            />
            <KpiCard
              label="기간 손익"
              value={<Money value={portfolioPerformance?.changeValue} signed />}
              detail={dateSpan(portfolioPerformance?.first.report_date, portfolioPerformance?.last.report_date)}
              tone={tone(portfolioPerformance?.changeValue)}
            />
            <KpiCard
              label="기간 수익률"
              value={formatPercent(portfolioPerformance?.returnRate)}
              detail="입출금 미보정"
              tone={tone(portfolioPerformance?.returnRate)}
            />
            <KpiCard
              label="관측 데이터"
              value={`${portfolioPerformance?.points ?? 0}일`}
              detail={`${selectedAccountIds.length} / ${data.accounts.length} 전략 활성`}
            />
          </section>

          <section className="chart-panel">
            <div className="panel-header">
              <div>
                <p className="section-kicker">COMPARATIVE TRAJECTORY</p>
                <h3>{chartMode === "balance" ? "전략별 잔고 추이" : "기간 시작 대비 수익률"}</h3>
              </div>
              <div className="chart-controls">
                <div className="segmented" aria-label="차트 모드">
                  <button
                    type="button"
                    className={chartMode === "balance" ? "selected" : ""}
                    onClick={() => setChartMode("balance")}
                  >
                    잔고
                  </button>
                  <button
                    type="button"
                    className={chartMode === "return" ? "selected" : ""}
                    onClick={() => setChartMode("return")}
                  >
                    수익률
                  </button>
                </div>
                {chartMode === "balance" && (
                  <div className="segmented metric-selector" aria-label="잔고 항목">
                    {METRICS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        className={metric === item.value ? "selected" : ""}
                        onClick={() => setMetric(item.value)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="chart-wrap">
              {chartRows.length && selectedAccounts.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartRows} margin={{ top: 16, right: 16, left: 4, bottom: 4 }}>
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
                      stroke="#6f8580"
                      tickLine={false}
                      axisLine={false}
                      width={76}
                      tickFormatter={(value) =>
                        chartMode === "return" ? `${Number(value).toFixed(0)}%` : compactAmount.format(Number(value))
                      }
                    />
                    {chartMode === "return" && <ReferenceLine y={0} stroke="#55706a" />}
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
                        chartMode === "return"
                          ? formatPercent(Number(value))
                          : <Money value={Number(value)} />,
                        accountMap.get(String(name))?.jenkins_name ?? String(name),
                      ]}
                    />
                    {selectedAccounts.map((account) => (
                      <Line
                        key={account.account_id}
                        type="monotone"
                        dataKey={account.account_id}
                        name={account.account_id}
                        stroke={ACCOUNT_COLORS[account.account_id] ?? "#8de0c1"}
                        strokeWidth={2.6}
                        dot={false}
                        activeDot={{ r: 5, strokeWidth: 2, fill: "#0b1311" }}
                        connectNulls
                        isAnimationActive={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="empty-chart">표시할 전략과 기간을 선택하세요.</div>
              )}
            </div>
          </section>

          <section className="performance-section">
            <div className="section-heading-row">
              <div>
                <p className="section-kicker">STRATEGY SCORECARDS</p>
                <h3>전략별 기간 성과</h3>
              </div>
              <p>{dateSpan(startDate, endDate)}</p>
            </div>
            <div className="performance-grid">
              {selectedAccounts.map((account) => (
                <PerformanceCard
                  key={account.account_id}
                  account={account}
                  performance={performanceMap.get(account.account_id)}
                />
              ))}
              {!selectedAccounts.length && (
                <div className="empty-card">왼쪽 위에서 비교할 전략을 켜주세요.</div>
              )}
            </div>
          </section>

          <section className="table-panel">
            <div className="panel-header compact-header">
              <div>
                <p className="section-kicker">PERIOD BREAKDOWN</p>
                <h3>기간 상세 비교</h3>
              </div>
              <span className="rows-caption">총 잔고 기준 수익률</span>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>전략</th>
                    <th>시작 잔고</th>
                    <th>종료 잔고</th>
                    <th>손익</th>
                    <th>수익률</th>
                    <th>현재 포지션</th>
                    <th>현재 현금</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedAccounts.map((account) => {
                    const item = performanceMap.get(account.account_id);
                    return (
                      <tr key={account.account_id}>
                        <td>
                          <span
                            className="table-dot"
                            style={{ background: ACCOUNT_COLORS[account.account_id] }}
                          />
                          <strong>{displayName(account)}</strong>
                        </td>
                        <td><Money value={item?.startValue} /></td>
                        <td><Money value={item?.endValue} /></td>
                        <td className={tone(item?.changeValue)}><Money value={item?.changeValue} signed /></td>
                        <td className={tone(item?.returnRate)}>{formatPercent(item?.returnRate)}</td>
                        <td><Money value={item?.latestPosition} /></td>
                        <td><Money value={item?.latestCash} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <footer>
            <span>PB Strategy Monitor</span>
            <span>데이터 생성 {formatTimestamp(data.generated_at)}</span>
          </footer>
        </>
      )}
    </main>
    </CoinEmojiContext.Provider>
  );
}

function KpiCard({
  label,
  value,
  detail,
  tone: valueTone = "",
}: {
  label: string;
  value: React.ReactNode;
  detail: string;
  tone?: string;
}) {
  return (
    <article className="kpi-card">
      <span>{label}</span>
      <strong className={valueTone}>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function PerformanceCard({
  account,
  performance,
}: {
  account: AlgorithmAccount;
  performance?: PerformanceSummary;
}) {
  const color = ACCOUNT_COLORS[account.account_id] ?? "#8de0c1";
  return (
    <article className="performance-card" style={{ "--strategy-color": color } as React.CSSProperties}>
      <div className="performance-card-top">
        <div>
          <span className="strategy-indicator" />
          <strong>{displayName(account)}</strong>
        </div>
        <span className="account-id">{account.account_id}</span>
      </div>
      <div className="return-display">
        <strong className={tone(performance?.returnRate)}>{formatPercent(performance?.returnRate)}</strong>
        <span>기간 수익률</span>
      </div>
      <div className="performance-values">
        <div><span>종료 잔고</span><strong><Money value={performance?.endValue} /></strong></div>
        <div><span>기간 손익</span><strong className={tone(performance?.changeValue)}><Money value={performance?.changeValue} signed /></strong></div>
      </div>
      <div className="balance-split">
        <div>
          <span>POSITION</span>
          <strong><Money value={performance?.latestPosition} /></strong>
        </div>
        <div>
          <span>CASH</span>
          <strong><Money value={performance?.latestCash} /></strong>
        </div>
      </div>
    </article>
  );
}

function LoadingState() {
  return (
    <div className="loading-state" aria-live="polite">
      <span className="loading-ring" />
      <p>전략 데이터를 불러오는 중입니다.</p>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="error-state" role="alert">
      <strong>데이터 연결을 확인하세요.</strong>
      <p>{message}</p>
      <button type="button" onClick={onRetry}>다시 시도</button>
    </div>
  );
}

function displayName(account: AlgorithmAccount) {
  return account.jenkins_name.replace("GOLDEN-", "");
}

function Money({ value, signed = false }: { value?: number | null; signed?: boolean }) {
  const coinEmoji = useContext(CoinEmojiContext);
  if (value == null || Number.isNaN(value)) return <>—</>;
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "-" : "") : "";
  const magnitude = signed ? amount.format(Math.abs(value)) : amount.format(value);
  return (
    <span className="coin-amount">
      {sign}
      <span className="coin-emoji" aria-hidden="true">
        {coinEmoji}
      </span>
      {magnitude}
    </span>
  );
}

function CoinPicker({ value, onChange }: { value: string; onChange: (emoji: string) => void }) {
  return (
    <div className="coin-picker" role="radiogroup" aria-label="통화 표시 아이콘">
      {COIN_EMOJIS.map((emoji) => (
        <button
          key={emoji}
          type="button"
          role="radio"
          aria-checked={value === emoji}
          aria-label={`통화 아이콘 ${emoji}`}
          className={value === emoji ? "coin-option selected" : "coin-option"}
          onClick={() => onChange(emoji)}
        >
          {emoji}
        </button>
      ))}
    </div>
  );
}

function formatPercent(value?: number | null) {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function tone(value?: number | null) {
  if (value == null || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}

function formatDate(value: string) {
  if (!value) return "—";
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

function dateSpan(start?: string, end?: string) {
  if (!start || !end) return "—";
  return `${formatDate(start)} → ${formatDate(end)}`;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function maxDate(left: string, right: string) {
  return left > right ? left : right;
}

function defaultStartDate(minDate: string, maxDateValue: string) {
  if (!minDate || !maxDateValue) return minDate;
  return maxDate(minDate, subtractDays(maxDateValue, DEFAULT_RANGE_DAYS - 1));
}

async function fetchPortfolio(signal?: AbortSignal) {
  const response = await fetch("/api/portfolio", {
    cache: "no-store",
    signal,
  });
  const payload = (await response.json()) as PortfolioResponse & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error || "데이터를 불러오지 못했습니다.");
  }
  return payload;
}
