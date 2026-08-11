import assert from "node:assert/strict";
import test from "node:test";

import {
  buildStorageChartRows,
  getStorageSummaries,
  storageSeriesKey,
} from "./storage";
import type { HostStorageSnapshot } from "./types";

function row(
  reportDate: string,
  usedBytes: number,
  overrides: Partial<HostStorageSnapshot> = {},
): HostStorageSnapshot {
  return {
    report_date: reportDate,
    host_id: "macmini-m5",
    mount_id: "internal",
    mount_label: "Mac mini internal",
    mount_path: "/System/Volumes/Data",
    total_bytes: 1_000,
    used_bytes: usedBytes,
    available_bytes: 1_000 - usedBytes,
    reported_at: `${reportDate}T00:00:00Z`,
    ...overrides,
  };
}

test("summarizes latest capacity, daily growth, and projected exhaustion", () => {
  const summaries = getStorageSummaries(
    [row("2026-08-01", 700), row("2026-08-11", 800)],
    "2026-08-11T12:00:00Z",
  );

  assert.equal(summaries.length, 1);
  assert.equal(summaries[0].utilizationPercent, 80);
  assert.equal(summaries[0].dailyGrowthBytes, 10);
  assert.equal(summaries[0].projectedDaysToFull, 20);
  assert.equal(summaries[0].status, "warning");
  assert.equal(summaries[0].stale, false);
});

test("does not project growth across a material filesystem capacity change", () => {
  const summaries = getStorageSummaries(
    [row("2026-08-01", 700), row("2026-08-11", 900, { total_bytes: 2_000 })],
    "2026-08-11T12:00:00Z",
  );

  assert.equal(summaries[0].dailyGrowthBytes, null);
  assert.equal(summaries[0].projectedDaysToFull, null);
});

test("marks an old latest observation as stale", () => {
  const summaries = getStorageSummaries(
    [row("2026-08-01", 700, { reported_at: "2026-08-01T00:00:00Z" })],
    "2026-08-03T00:00:01Z",
  );

  assert.equal(summaries[0].stale, true);
  assert.ok(summaries[0].ageHours > 48);
});

test("chart rows preserve missing calendar days as null gaps", () => {
  const first = row("2026-08-01", 700);
  const last = row("2026-08-03", 800);
  const key = storageSeriesKey(first);
  const rows = buildStorageChartRows([first, last]);

  assert.deepEqual(rows, [
    { date: "2026-08-01", [key]: 70 },
    { date: "2026-08-02", [key]: null },
    { date: "2026-08-03", [key]: 80 },
  ]);
});
