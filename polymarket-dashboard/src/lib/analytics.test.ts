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

test("selected portfolio KPI uses only dates observed by every selected account", () => {
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

  assert.equal(result?.first.report_date, "2026-07-02");
  assert.equal(result?.last.report_date, "2026-07-03");
  assert.equal(result?.first.total_value, 310);
  assert.equal(result?.last.total_value, 340);
  assert.equal(result?.changeValue, 30);
  assert.equal(result?.points, 2);
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
