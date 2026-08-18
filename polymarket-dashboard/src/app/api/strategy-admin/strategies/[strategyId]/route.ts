import { NextRequest, NextResponse } from "next/server";

import {
  normalizeStrategyAdminUpdate,
  StrategyAdminValidationError,
} from "@/lib/strategy-admin";
import {
  getStrategyAdminSessionSecret,
  isSameOriginRequest,
  isStrategyAdminRequest,
  noStoreHeaders,
} from "@/lib/strategy-admin-server";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import type { StrategyLifecycle } from "@/lib/types";

export const dynamic = "force-dynamic";

const STRATEGY_COLUMNS = "strategy_id,display_name,codename,thesis,strategy_kind,lifecycle_stage,operating_status,evaluation_started_at,phase_started_at,evaluation_horizon_days,current_summary,attention_note,attention_level,source_ref,hidden_by_default,sort_order,updated_at";

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ strategyId: string }> },
) {
  if (!isSameOriginRequest(request)) {
    return NextResponse.json({ error: "허용되지 않은 요청입니다." }, { status: 403, headers: noStoreHeaders() });
  }

  const secret = getStrategyAdminSessionSecret();
  if (!secret || !(await isStrategyAdminRequest(request, secret))) {
    return NextResponse.json({ error: "관리자 로그인이 필요합니다." }, { status: 401, headers: noStoreHeaders() });
  }

  const { strategyId } = await context.params;
  if (!/^golden-[a-z0-9-]+$/.test(strategyId)) {
    return NextResponse.json({ error: "전략 이름이 올바르지 않습니다." }, { status: 400, headers: noStoreHeaders() });
  }

  try {
    const supabase = createServerSupabaseClient();
    const currentResult = await supabase
      .from("pd_strategies")
      .select(STRATEGY_COLUMNS)
      .eq("strategy_id", strategyId)
      .maybeSingle();

    if (currentResult.error) throw new Error(currentResult.error.message);
    if (!currentResult.data) {
      return NextResponse.json({ error: "전략을 찾을 수 없습니다." }, { status: 404, headers: noStoreHeaders() });
    }

    const payload = await request.json();
    const update = normalizeStrategyAdminUpdate(payload, currentResult.data as StrategyLifecycle);
    const updatedResult = await supabase
      .from("pd_strategies")
      .update(update)
      .eq("strategy_id", strategyId)
      .select(STRATEGY_COLUMNS)
      .single();

    if (updatedResult.error) throw new Error(updatedResult.error.message);
    return NextResponse.json(
      { strategy: updatedResult.data as StrategyLifecycle },
      { headers: noStoreHeaders() },
    );
  } catch (error) {
    if (error instanceof StrategyAdminValidationError || error instanceof SyntaxError) {
      return NextResponse.json(
        { error: error instanceof SyntaxError ? "요청 형식이 올바르지 않습니다." : error.message },
        { status: 400, headers: noStoreHeaders() },
      );
    }
    console.error("Strategy admin update failed", { strategyId, error });
    return NextResponse.json(
      { error: "전략 상태를 저장하지 못했습니다." },
      { status: 500, headers: noStoreHeaders() },
    );
  }
}
