import { NextResponse } from "next/server";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import type {
  StrategyCheckpoint,
  StrategyJenkinsJob,
  StrategyLifecycle,
  StrategyLifecycleResponse,
  StrategySyncRun,
} from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const supabase = createServerSupabaseClient();
    const [strategiesResult, jobsResult, checkpointsResult, syncRunsResult] = await Promise.all([
      supabase
        .from("pd_strategies")
        .select(
          "strategy_id,display_name,codename,thesis,strategy_kind,lifecycle_stage,operating_status,evaluation_started_at,phase_started_at,evaluation_horizon_days,current_summary,attention_note,attention_level,source_ref,hidden_by_default,sort_order,updated_at",
        )
        .order("sort_order", { ascending: true })
        .order("strategy_id", { ascending: true }),
      supabase
        .from("pd_jenkins_jobs")
        .select(
          "job_name,strategy_id,runtime_job,mode,treatment_label,purpose,test_size_label,experiment_started_at,experiment_ends_at,schedule,expected_cadence_minutes,workspace_class,buildable,enabled,in_queue,building,job_color,last_build_number,last_build_status,last_build_started_at,last_build_duration_ms,config_sha256,observed_at,notes,updated_at",
        )
        .order("job_name", { ascending: true }),
      supabase
        .from("pd_strategy_checkpoints")
        .select(
          "checkpoint_id,strategy_id,checkpoint_type,title,due_at,status,completed_at,instructions,source_ref,updated_at",
        )
        .order("due_at", { ascending: true, nullsFirst: false }),
      supabase
        .from("pd_sync_runs")
        .select(
          "sync_run_id,collector_name,started_at,finished_at,status,jobs_expected,jobs_observed,jobs_failed,error_summary",
        )
        .order("started_at", { ascending: false })
        .limit(20),
    ]);

    for (const result of [strategiesResult, jobsResult, checkpointsResult, syncRunsResult]) {
      if (result.error) throw new Error(result.error.message);
    }

    const response: StrategyLifecycleResponse = {
      strategies: (strategiesResult.data ?? []) as StrategyLifecycle[],
      jobs: (jobsResult.data ?? []) as StrategyJenkinsJob[],
      checkpoints: (checkpointsResult.data ?? []) as StrategyCheckpoint[],
      sync_runs: (syncRunsResult.data ?? []) as StrategySyncRun[],
      jenkins_base_url: normalizeJenkinsBaseUrl(process.env.JENKINS_DASHBOARD_URL),
      generated_at: new Date().toISOString(),
    };

    return NextResponse.json(response, {
      headers: {
        "Cache-Control": "private, no-store, max-age=0",
      },
    });
  } catch (error) {
    console.error("Strategy lifecycle query failed", error);
    return NextResponse.json(
      { error: "전략 생애주기 데이터를 불러오지 못했습니다." },
      { status: 500 },
    );
  }
}

function normalizeJenkinsBaseUrl(value: string | undefined) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    return url.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}
