import type { HostStorageSnapshot } from "@/lib/types";

export const STORAGE_STALE_AFTER_HOURS = 36;
export const STORAGE_WARNING_PERCENT = 80;
export const STORAGE_CRITICAL_PERCENT = 90;

const DAY_MS = 24 * 60 * 60 * 1000;

export type StorageStatus = "healthy" | "warning" | "critical";

export interface StorageSummary {
  key: string;
  latest: HostStorageSnapshot;
  observations: number;
  utilizationPercent: number;
  dailyGrowthBytes: number | null;
  projectedDaysToFull: number | null;
  stale: boolean;
  ageHours: number;
  status: StorageStatus;
}

export type StorageChartRow = {
  date: string;
  [series: string]: string | number | null;
};

export function storageSeriesKey(snapshot: Pick<HostStorageSnapshot, "host_id" | "mount_id">) {
  return `${snapshot.host_id}::${snapshot.mount_id}`;
}

export function getStorageSummaries(
  snapshots: HostStorageSnapshot[],
  generatedAt: string,
): StorageSummary[] {
  const groups = new Map<string, HostStorageSnapshot[]>();
  for (const snapshot of snapshots) {
    const key = storageSeriesKey(snapshot);
    const rows = groups.get(key) ?? [];
    rows.push(snapshot);
    groups.set(key, rows);
  }

  const generatedMs = new Date(generatedAt).getTime();
  return [...groups.entries()]
    .map(([key, rows]) => {
      rows.sort(compareSnapshots);
      const latest = rows.at(-1)!;
      const latestDateMs = dateMs(latest.report_date);
      const comparisonFloor = latestDateMs - 29 * DAY_MS;
      const comparison = rows.find((row) => dateMs(row.report_date) >= comparisonFloor) ?? rows[0];
      const elapsedDays = (latestDateMs - dateMs(comparison.report_date)) / DAY_MS;
      const capacityStable =
        Math.abs(latest.total_bytes - comparison.total_bytes) <=
        Math.max(1, latest.total_bytes * 0.01);
      const dailyGrowthBytes =
        elapsedDays > 0 && capacityStable
          ? (latest.used_bytes - comparison.used_bytes) / elapsedDays
          : null;
      const projectedDaysToFull =
        dailyGrowthBytes != null && dailyGrowthBytes > 0
          ? latest.available_bytes / dailyGrowthBytes
          : null;
      const utilizationPercent =
        latest.total_bytes > 0 ? (latest.used_bytes / latest.total_bytes) * 100 : 0;
      const ageHours = Math.max(
        0,
        (generatedMs - new Date(latest.reported_at).getTime()) / (60 * 60 * 1000),
      );

      return {
        key,
        latest,
        observations: rows.length,
        utilizationPercent,
        dailyGrowthBytes,
        projectedDaysToFull,
        stale: ageHours > STORAGE_STALE_AFTER_HOURS,
        ageHours,
        status:
          utilizationPercent >= STORAGE_CRITICAL_PERCENT
            ? "critical"
            : utilizationPercent >= STORAGE_WARNING_PERCENT
              ? "warning"
              : "healthy",
      } satisfies StorageSummary;
    })
    .sort((left, right) => left.key.localeCompare(right.key));
}

export function buildStorageChartRows(snapshots: HostStorageSnapshot[]): StorageChartRow[] {
  if (!snapshots.length) return [];
  const sorted = [...snapshots].sort(compareSnapshots);
  const minDate = sorted[0].report_date;
  const maxDate = sorted.at(-1)!.report_date;
  const seriesKeys = [...new Set(sorted.map(storageSeriesKey))];
  const rowsByDate = new Map<string, StorageChartRow>();

  for (const date of calendarDates(minDate, maxDate)) {
    const row: StorageChartRow = { date };
    for (const key of seriesKeys) row[key] = null;
    rowsByDate.set(date, row);
  }
  for (const snapshot of sorted) {
    const row = rowsByDate.get(snapshot.report_date)!;
    row[storageSeriesKey(snapshot)] =
      snapshot.total_bytes > 0 ? (snapshot.used_bytes / snapshot.total_bytes) * 100 : null;
  }
  return [...rowsByDate.values()];
}

function compareSnapshots(left: HostStorageSnapshot, right: HostStorageSnapshot) {
  return (
    left.report_date.localeCompare(right.report_date) ||
    left.reported_at.localeCompare(right.reported_at)
  );
}

function calendarDates(start: string, end: string) {
  const dates: string[] = [];
  for (let cursor = dateMs(start); cursor <= dateMs(end); cursor += DAY_MS) {
    dates.push(new Date(cursor).toISOString().slice(0, 10));
  }
  return dates;
}

function dateMs(value: string) {
  return new Date(`${value}T00:00:00Z`).getTime();
}
