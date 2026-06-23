import { NextRequest, NextResponse } from "next/server";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type {
  AlgorithmAccount,
  AlgorithmBalance,
  PortfolioResponse,
  PortfolioTotal,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 1000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export async function GET(request: NextRequest) {
  const start = normalizeDate(request.nextUrl.searchParams.get("start"));
  const end = normalizeDate(request.nextUrl.searchParams.get("end"));

  if (start === false || end === false) {
    return NextResponse.json(
      { error: "날짜는 YYYY-MM-DD 형식이어야 합니다." },
      { status: 400 },
    );
  }
  if (start && end && start > end) {
    return NextResponse.json(
      { error: "시작일은 종료일보다 늦을 수 없습니다." },
      { status: 400 },
    );
  }

  try {
    const supabase = createServerSupabaseClient();
    const [accountsResult, balances, totals] = await Promise.all([
      supabase
        .from("pb_algorithm_accounts")
        .select("account_id,jenkins_name,algorithm_code,instance_no,sort_order")
        .order("sort_order", { ascending: true }),
      fetchAllBalances(supabase, start, end),
      fetchAllTotals(supabase, start, end),
    ]);

    if (accountsResult.error) {
      throw new Error(accountsResult.error.message);
    }

    const response: PortfolioResponse = {
      accounts: (accountsResult.data ?? []) as AlgorithmAccount[],
      balances,
      totals,
      range: { start: start || null, end: end || null },
      generated_at: new Date().toISOString(),
    };

    return NextResponse.json(response, {
      headers: {
        "Cache-Control": "private, no-store, max-age=0",
      },
    });
  } catch (error) {
    console.error("Portfolio query failed", error);
    return NextResponse.json(
      { error: "포트폴리오 데이터를 불러오지 못했습니다." },
      { status: 500 },
    );
  }
}

type SupabaseServerClient = ReturnType<typeof createServerSupabaseClient>;

async function fetchAllBalances(
  supabase: SupabaseServerClient,
  start: string | null,
  end: string | null,
) {
  const rows: AlgorithmBalance[] = [];
  let cursor: { reportDate: string; accountId: string } | null = null;

  while (true) {
    let query = supabase
      .from("pb_daily_algorithm_balances")
      .select(
        "report_date,account_id,total_value,position_value,cash_value,reported_at",
      )
      .order("report_date", { ascending: true })
      .order("account_id", { ascending: true })
      .limit(PAGE_SIZE);

    if (start) query = query.gte("report_date", start);
    if (end) query = query.lte("report_date", end);
    if (cursor) {
      query = query.or(
        `report_date.gt.${cursor.reportDate},and(report_date.eq.${cursor.reportDate},account_id.gt.${cursor.accountId})`,
      );
    }

    const { data, error } = await query;
    if (error) throw new Error(error.message);
    const page = (data ?? []).map(normalizeBalance);
    rows.push(...page);
    if (page.length < PAGE_SIZE) break;

    const last = page.at(-1);
    if (!last) break;
    cursor = { reportDate: last.report_date, accountId: last.account_id };
  }
  return rows;
}

async function fetchAllTotals(
  supabase: SupabaseServerClient,
  start: string | null,
  end: string | null,
) {
  const rows: PortfolioTotal[] = [];
  let cursor: string | null = null;

  while (true) {
    let query = supabase
      .from("pb_daily_portfolio_totals")
      .select("report_date,total_value,position_value,cash_value,reported_at")
      .order("report_date", { ascending: true })
      .limit(PAGE_SIZE);

    if (start) query = query.gte("report_date", start);
    if (end) query = query.lte("report_date", end);
    if (cursor) query = query.gt("report_date", cursor);

    const { data, error } = await query;
    if (error) throw new Error(error.message);
    const page = (data ?? []).map(normalizeTotal);
    rows.push(...page);
    if (page.length < PAGE_SIZE) break;

    const last = page.at(-1);
    if (!last) break;
    cursor = last.report_date;
  }
  return rows;
}

function normalizeBalance(row: Record<string, unknown>): AlgorithmBalance {
  return {
    report_date: String(row.report_date),
    account_id: String(row.account_id),
    total_value: Number(row.total_value),
    position_value: Number(row.position_value),
    cash_value: Number(row.cash_value),
    reported_at: String(row.reported_at),
  };
}

function normalizeTotal(row: Record<string, unknown>): PortfolioTotal {
  return {
    report_date: String(row.report_date),
    total_value: Number(row.total_value),
    position_value: Number(row.position_value),
    cash_value: Number(row.cash_value),
    reported_at: String(row.reported_at),
  };
}

function normalizeDate(value: string | null): string | null | false {
  if (!value) return null;
  if (!ISO_DATE.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value
    ? false
    : value;
}
