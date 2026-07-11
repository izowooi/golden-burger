import type {
  AlgorithmBalance,
  BalanceMetric,
  ChartMode,
  ChartRow,
  PerformanceSummary,
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

export function getSelectedPortfolioPerformance(
  balances: AlgorithmBalance[],
  selectedAccountIds: string[],
  start: string,
  end: string,
) {
  const selected = new Set(selectedAccountIds);
  if (!selected.size) return null;

  const byDate = new Map<string, Map<string, AlgorithmBalance>>();
  for (const row of balances) {
    if (!selected.has(row.account_id) || !inDateRange(row.report_date, start, end)) {
      continue;
    }
    const rows = byDate.get(row.report_date) ?? new Map<string, AlgorithmBalance>();
    rows.set(row.account_id, row);
    byDate.set(row.report_date, rows);
  }

  const points = [...byDate.entries()]
    .filter(([, rows]) => selectedAccountIds.every((accountId) => rows.has(accountId)))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([reportDate, rows]) => ({
      report_date: reportDate,
      total_value: [...rows.values()].reduce((sum, row) => sum + row.total_value, 0),
      position_value: [...rows.values()].reduce(
        (sum, row) => sum + row.position_value,
        0,
      ),
      cash_value: [...rows.values()].reduce((sum, row) => sum + row.cash_value, 0),
    }));
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
  const filtered = balances
    .filter(
      (row) =>
        selected.has(row.account_id) && inDateRange(row.report_date, start, end),
    )
    .sort(
      (left, right) =>
        left.report_date.localeCompare(right.report_date) ||
        left.account_id.localeCompare(right.account_id),
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

  const dates = [...byDate.keys()].sort();
  if (!dates.length) return [];
  const rangeStart = start || dates[0];
  const rangeEnd = end || dates.at(-1) || rangeStart;

  return listCalendarDates(rangeStart, rangeEnd).map((date) => {
    const row = byDate.get(date) ?? { date };
    for (const accountId of selectedAccountIds) {
      if (!(accountId in row)) row[accountId] = null;
    }
    return row;
  });
}

export function subtractDays(date: string, days: number) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - days);
  return value.toISOString().slice(0, 10);
}

function listCalendarDates(start: string, end: string) {
  const dates: string[] = [];
  const cursor = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);

  while (cursor <= last) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}
