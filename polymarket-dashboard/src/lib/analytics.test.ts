import assert from "node:assert/strict";
import test from "node:test";

import {
  buildChartRows,
  getPerformance,
  getSelectedPortfolioPerformance,
  subtractDays,
} from "@/lib/analytics";
import type { AlgorithmBalance } from "@/lib/types";

test("getPerformance sorts observations and calculates the first-to-last return", () => {
  const rows = [
    balance("2026-07-03", "alpha", 121),
    balance("2026-07-01", "alpha", 100),
    balance("2026-07-02", "alpha", 110),
  ];

  assert.deepEqual(getPerformance(rows, "alpha", "2026-07-01", "2026-07-03"), {
    accountId: "alpha",
    startDate: "2026-07-01",
    endDate: "2026-07-03",
    startValue: 100,
    endValue: 121,
    changeValue: 21,
    returnRate: 21,
    latestPosition: 60.5,
    latestCash: 60.5,
    points: 3,
  });
});

test("selected portfolio KPI compares earliest and latest actual daily row sums", () => {
  const rows = [
    balance("2026-07-01", "alpha", 100),
    balance("2026-07-02", "alpha", 110),
    balance("2026-07-03", "alpha", 120),
    balance("2026-07-02", "beta", 200),
    balance("2026-07-03", "beta", 220),
  ];

  const result = getSelectedPortfolioPerformance(
    rows,
    ["alpha", "beta"],
    "2026-07-01",
    "2026-07-03",
  );

  assert.equal(result?.first.report_date, "2026-07-01");
  assert.equal(result?.last.report_date, "2026-07-03");
  assert.equal(result?.first.total_value, 100);
  assert.equal(result?.last.total_value, 340);
  assert.equal(result?.first.accountCount, 1);
  assert.equal(result?.last.accountCount, 2);
  assert.equal(result?.changeValue, 240);
  assert.equal(result?.returnRate, 240);
  assert.equal(result?.points, 5);
  assert.equal(result?.accountCount, 2);
});

test("selected portfolio KPI excludes only selected accounts with no period observations", () => {
  const rows = [
    balance("2026-07-01", "alpha", 100),
    balance("2026-07-02", "alpha", 110),
    balance("2026-07-03", "beta", 200),
  ];

  const result = getSelectedPortfolioPerformance(
    rows,
    ["alpha", "beta", "not-started"],
    "2026-07-01",
    "2026-07-02",
  );

  assert.equal(result?.first.total_value, 100);
  assert.equal(result?.last.total_value, 110);
  assert.equal(result?.changeValue, 10);
  assert.equal(result?.accountCount, 1);
});

test("selected portfolio KPI matches production 30D boundary totals", () => {
  const rows = [
    balance("2026-06-13", "golden-apple-1", 1_695.72),
    balance("2026-06-13", "golden-apple-2", 12_785.55),
    balance("2026-06-13", "golden-banana", 28_324.42),
    balance("2026-06-13", "golden-cherry", 4_028.2),
    balance("2026-07-01", "golden-apple-1", 3_500),
    balance("2026-07-12", "golden-apple-1", 4_899.15),
    balance("2026-07-12", "golden-apple-2", 6_834.66),
    balance("2026-07-12", "golden-banana", 32_342.67),
    balance("2026-07-12", "golden-cherry", 4_111.79),
    balance("2026-07-12", "golden-eco", 2_970.02),
    balance("2026-07-12", "golden-fox", 2_933.84),
    balance("2026-06-13", "not-selected", 999_999),
  ];

  const result = getSelectedPortfolioPerformance(
    rows,
    [
      "golden-apple-1",
      "golden-apple-2",
      "golden-banana",
      "golden-cherry",
      "golden-eco",
      "golden-fox",
    ],
    "2026-06-13",
    "2026-07-12",
  );

  assert.equal(result?.first.report_date, "2026-06-13");
  assert.equal(result?.last.report_date, "2026-07-12");
  assert.ok(Math.abs((result?.first.total_value ?? 0) - 46_833.89) < 1e-9);
  assert.ok(Math.abs((result?.last.total_value ?? 0) - 54_092.13) < 1e-9);
  assert.ok(Math.abs((result?.changeValue ?? 0) - 7_258.24) < 1e-9);
  assert.ok(Math.abs((result?.returnRate ?? 0) - 15.497837143145695) < 1e-12);
  assert.equal(result?.first.accountCount, 4);
  assert.equal(result?.last.accountCount, 6);
});

test("chart rows are calendar-complete, gap-aware, and use the earliest baseline", () => {
  const rows = [
    balance("2026-07-03", "alpha", 120),
    balance("2026-07-01", "alpha", 100),
  ];

  const chartRows = buildChartRows(
    rows,
    ["alpha"],
    "2026-07-01",
    "2026-07-03",
    "total_value",
    "return",
  );

  assert.deepEqual(chartRows, [
    { date: "2026-07-01", alpha: 0 },
    { date: "2026-07-02", alpha: null },
    { date: "2026-07-03", alpha: 20 },
  ]);
});

test("subtractDays uses UTC calendar arithmetic", () => {
  assert.equal(subtractDays("2026-03-01", 1), "2026-02-28");
});

function balance(
  reportDate: string,
  accountId: string,
  totalValue: number,
): AlgorithmBalance {
  return {
    report_date: reportDate,
    account_id: accountId,
    total_value: totalValue,
    position_value: totalValue / 2,
    cash_value: totalValue / 2,
    reported_at: `${reportDate}T12:00:00+09:00`,
  };
}
