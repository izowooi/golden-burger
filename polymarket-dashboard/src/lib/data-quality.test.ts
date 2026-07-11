import assert from "node:assert/strict";
import test from "node:test";

import { getDataQuality, STALE_AFTER_HOURS } from "@/lib/data-quality";
import type {
  AlgorithmAccount,
  AlgorithmBalance,
  PortfolioTotal,
} from "@/lib/types";

const accounts: AlgorithmAccount[] = [
  account("alpha", 1),
  account("beta", 2),
];

test("data quality reports freshness, observation gaps, and total mismatches", () => {
  const balances = [
    balance("2026-07-01", "alpha", 100),
    balance("2026-07-03", "alpha", 110),
    balance("2026-07-02", "beta", 200),
    balance("2026-07-03", "beta", 190),
  ];
  const totals = [
    total("2026-07-01", 100),
    total("2026-07-02", 200),
    total("2026-07-03", 305),
  ];

  const quality = getDataQuality(
    accounts,
    balances,
    totals,
    "2026-07-04T00:00:00+09:00",
  );

  assert.equal(quality.latestReportAt, "2026-07-03T12:00:00+09:00");
  assert.equal(quality.latestReportDate, "2026-07-03");
  assert.equal(quality.ageHours, 12);
  assert.equal(quality.stale, false);
  assert.deepEqual(quality.accountObservations, [
    {
      accountId: "alpha",
      firstDate: "2026-07-01",
      lastDate: "2026-07-03",
      points: 2,
      missingDates: ["2026-07-02"],
    },
    {
      accountId: "beta",
      firstDate: "2026-07-02",
      lastDate: "2026-07-03",
      points: 2,
      missingDates: [],
    },
  ]);
  assert.equal(quality.missingCalendarDays, 1);
  assert.deepEqual(quality.totalMismatches, [
    {
      date: "2026-07-03",
      totalValue: 305,
      accountSum: 300,
      delta: 5,
    },
  ]);
  assert.equal(quality.hasIssues, true);
});

test("one-cent rounding differences are tolerated", () => {
  const quality = getDataQuality(
    [account("alpha", 1)],
    [balance("2026-07-01", "alpha", 100)],
    [total("2026-07-01", 100.01)],
    "2026-07-01T13:00:00+09:00",
  );

  assert.deepEqual(quality.totalMismatches, []);
});

test("missing or older-than-threshold report timestamps are stale", () => {
  assert.equal(getDataQuality([], [], []).stale, true);

  const quality = getDataQuality(
    [account("alpha", 1)],
    [balance("2026-07-01", "alpha", 100)],
    [total("2026-07-01", 100)],
    new Date(
      new Date("2026-07-01T12:00:00+09:00").getTime() +
        (STALE_AFTER_HOURS + 1) * 3_600_000,
    ),
  );
  assert.equal(quality.stale, true);
});

function account(accountId: string, sortOrder: number): AlgorithmAccount {
  return {
    account_id: accountId,
    jenkins_name: accountId.toUpperCase(),
    algorithm_code: accountId,
    instance_no: null,
    sort_order: sortOrder,
  };
}

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

function total(reportDate: string, totalValue: number): PortfolioTotal {
  return {
    report_date: reportDate,
    total_value: totalValue,
    position_value: totalValue / 2,
    cash_value: totalValue / 2,
    reported_at: `${reportDate}T12:00:00+09:00`,
  };
}
