import { NextRequest, NextResponse } from "next/server";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { HostStorageResponse, HostStorageSnapshot } from "@/lib/types";

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
    const snapshots = await fetchAllStorage(supabase, start, end);
    const response: HostStorageResponse = {
      snapshots,
      range: { start: start || null, end: end || null },
      generated_at: new Date().toISOString(),
    };
    return NextResponse.json(response, {
      headers: { "Cache-Control": "private, no-store, max-age=0" },
    });
  } catch (error) {
    console.error("Host storage query failed", error);
    return NextResponse.json(
      { error: "저장공간 데이터를 불러오지 못했습니다." },
      { status: 500 },
    );
  }
}

type SupabaseServerClient = ReturnType<typeof createServerSupabaseClient>;

async function fetchAllStorage(
  supabase: SupabaseServerClient,
  start: string | null,
  end: string | null,
) {
  const rows: HostStorageSnapshot[] = [];
  let offset = 0;

  while (true) {
    let query = supabase
      .from("pb_host_storage_daily")
      .select(
        "report_date,host_id,mount_id,mount_label,mount_path,total_bytes,used_bytes,available_bytes,reported_at",
      )
      .order("report_date", { ascending: true })
      .order("host_id", { ascending: true })
      .order("mount_id", { ascending: true })
      .range(offset, offset + PAGE_SIZE - 1);
    if (start) query = query.gte("report_date", start);
    if (end) query = query.lte("report_date", end);

    const { data, error } = await query;
    if (error) throw new Error(error.message);
    const page = (data ?? []).map(normalizeSnapshot);
    rows.push(...page);
    if (page.length < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }
  return rows;
}

function normalizeSnapshot(row: Record<string, unknown>): HostStorageSnapshot {
  return {
    report_date: String(row.report_date),
    host_id: String(row.host_id),
    mount_id: String(row.mount_id),
    mount_label: String(row.mount_label),
    mount_path: String(row.mount_path),
    total_bytes: Number(row.total_bytes),
    used_bytes: Number(row.used_bytes),
    available_bytes: Number(row.available_bytes),
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
