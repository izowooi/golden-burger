import type {
  AlgorithmAccount,
  AlgorithmBalance,
  PortfolioTotal,
} from "@/lib/types";

export const STALE_AFTER_HOURS = 36;
export const TOTAL_MISMATCH_TOLERANCE = 0.01;

export interface AccountObservation {
  accountId: string;
  firstDate: string | null;
  lastDate: string | null;
  points: number;
  missingDates: string[];
}

export interface TotalMismatch {
  date: string;
  totalValue: number;
  accountSum: number;
  delta: number;
}

export interface DataQualityReport {
  latestReportAt: string | null;
  latestReportDate: string | null;
  ageHours: number | null;
  stale: boolean;
  accountObservations: AccountObservation[];
  totalMismatches: TotalMismatch[];
  orphanBalanceDates: string[];
  missingCalendarDays: number;
  hasIssues: boolean;
}

export function getDataQuality(
  accounts: AlgorithmAccount[],
  balances: AlgorithmBalance[],
  totals: PortfolioTotal[],
  now: Date | string = new Date(),
): DataQualityReport {
  const latestTotal = [...totals]
    .filter((row) => isValidTimestamp(row.reported_at))
    .sort(
      (left, right) =>
        new Date(left.reported_at).getTime() - new Date(right.reported_at).getTime(),
    )
    .at(-1);
  const fallbackBalance = [...balances]
    .filter((row) => isValidTimestamp(row.reported_at))
    .sort(
      (left, right) =>
        new Date(left.reported_at).getTime() - new Date(right.reported_at).getTime(),
    )
    .at(-1);
  const latestReportAt = latestTotal?.reported_at ?? fallbackBalance?.reported_at ?? null;
  const latestReportDate = maxDate([
    ...totals.map((row) => row.report_date),
    ...balances.map((row) => row.report_date),
  ]);
  const nowMs = new Date(now).getTime();
  const reportMs = latestReportAt ? new Date(latestReportAt).getTime() : Number.NaN;
  const ageHours =
    Number.isFinite(nowMs) && Number.isFinite(reportMs)
      ? Math.max(0, (nowMs - reportMs) / 3_600_000)
      : null;
  const stale = ageHours === null || ageHours > STALE_AFTER_HOURS;

  const datesByAccount = new Map<string, Set<string>>();
  const sumsByDate = new Map<string, number>();
  for (const row of balances) {
    const dates = datesByAccount.get(row.account_id) ?? new Set<string>();
    dates.add(row.report_date);
    datesByAccount.set(row.account_id, dates);
    sumsByDate.set(
      row.report_date,
      (sumsByDate.get(row.report_date) ?? 0) + row.total_value,
    );
  }

  const accountObservations = accounts.map((account) => {
    const observed = datesByAccount.get(account.account_id) ?? new Set<string>();
    const dates = [...observed].sort();
    const firstDate = dates[0] ?? null;
    const lastDate = dates.at(-1) ?? null;
    const expectedDates =
      firstDate && latestReportDate
        ? listCalendarDates(firstDate, latestReportDate)
        : [];
    return {
      accountId: account.account_id,
      firstDate,
      lastDate,
      points: dates.length,
      missingDates: expectedDates.filter((date) => !observed.has(date)),
    };
  });

  const totalDates = new Set(totals.map((row) => row.report_date));
  const totalMismatches = [...totals]
    .sort((left, right) => left.report_date.localeCompare(right.report_date))
    .flatMap((row) => {
      const accountSum = sumsByDate.get(row.report_date) ?? 0;
      const delta = row.total_value - accountSum;
      return Math.abs(delta) - TOTAL_MISMATCH_TOLERANCE > 1e-9
        ? [
            {
              date: row.report_date,
              totalValue: row.total_value,
              accountSum,
              delta,
            },
          ]
        : [];
    });
  const orphanBalanceDates = [...sumsByDate.keys()]
    .filter((date) => !totalDates.has(date))
    .sort();
  const missingCalendarDays = accountObservations.reduce(
    (sum, observation) => sum + observation.missingDates.length,
    0,
  );
  const missingAccounts = accountObservations.some(
    (observation) => observation.points === 0,
  );

  return {
    latestReportAt,
    latestReportDate,
    ageHours,
    stale,
    accountObservations,
    totalMismatches,
    orphanBalanceDates,
    missingCalendarDays,
    hasIssues:
      stale ||
      missingAccounts ||
      missingCalendarDays > 0 ||
      totalMismatches.length > 0 ||
      orphanBalanceDates.length > 0,
  };
}

function isValidTimestamp(value: string) {
  return Number.isFinite(new Date(value).getTime());
}

function maxDate(dates: string[]) {
  return dates.length ? [...dates].sort().at(-1) ?? null : null;
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
