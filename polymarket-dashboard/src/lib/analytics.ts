import type {
  AlgorithmBalance,
  BalanceMetric,
  ChartMode,
  ChartRow,
  PerformanceSummary,
  PortfolioTotal,
} from "@/lib/types";

export function getDateBounds(rows: { report_date: string }[]) {
  if (!rows.length) return { minDate: "", maxDate: "" };
  const dates = rows.map((row) => row.report_date).sort();
  return { minDate: dates[0], maxDate: dates.at(-1) ?? dates[0] };
}

export function inDateRange(date: string, start: string, end: string) {
  return (!start || date >= start) && (!end || date <= end);
}

export function getPerformance(
  balances: AlgorithmBalance[],
  accountId: string,
  start: string,
  end: string,
): PerformanceSummary | null {
  const points = balances
    .filter(
      (row) =>
        row.account_id === accountId && inDateRange(row.report_date, start, end),
    )
    .sort((a, b) => a.report_date.localeCompare(b.report_date));
  const first = points[0];
  const last = points.at(-1);
  if (!first || !last) return null;

  const changeValue = last.total_value - first.total_value;
  return {
    accountId,
    startDate: first.report_date,
    endDate: last.report_date,
    startValue: first.total_value,
    endValue: last.total_value,
    changeValue,
    returnRate: first.total_value === 0 ? null : (changeValue / first.total_value) * 100,
    latestPosition: last.position_value,
    latestCash: last.cash_value,
    points: points.length,
  };
}

export function getPortfolioPerformance(
  totals: PortfolioTotal[],
  start: string,
  end: string,
) {
  const points = totals
    .filter((row) => inDateRange(row.report_date, start, end))
    .sort((a, b) => a.report_date.localeCompare(b.report_date));
  const first = points[0];
  const last = points.at(-1);
  if (!first || !last) return null;
  const changeValue = last.total_value - first.total_value;
  return {
    first,
    last,
    changeValue,
    returnRate: first.total_value === 0 ? null : (changeValue / first.total_value) * 100,
    points: points.length,
  };
}

export function buildChartRows(
  balances: AlgorithmBalance[],
  selectedAccountIds: string[],
  start: string,
  end: string,
  metric: BalanceMetric,
  mode: ChartMode,
): ChartRow[] {
  const selected = new Set(selectedAccountIds);
  const filtered = balances.filter(
    (row) => selected.has(row.account_id) && inDateRange(row.report_date, start, end),
  );
  const bases = new Map<string, number>();
  const byDate = new Map<string, ChartRow>();

  for (const row of filtered) {
    if (!bases.has(row.account_id)) bases.set(row.account_id, row.total_value);
    const chartRow = byDate.get(row.report_date) ?? { date: row.report_date };
    if (mode === "return") {
      const base = bases.get(row.account_id) ?? 0;
      chartRow[row.account_id] = base === 0 ? null : ((row.total_value - base) / base) * 100;
    } else {
      chartRow[row.account_id] = row[metric];
    }
    byDate.set(row.report_date, chartRow);
  }

  return [...byDate.values()].sort((a, b) =>
    String(a.date).localeCompare(String(b.date)),
  );
}

export function subtractDays(date: string, days: number) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - days);
  return value.toISOString().slice(0, 10);
}
