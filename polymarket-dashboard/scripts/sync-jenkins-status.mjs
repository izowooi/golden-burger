#!/usr/bin/env node

import { createClient } from "@supabase/supabase-js";

const COLLECTOR_NAME = "polymarket-dashboard-jenkins-lan-v1";
const CONCURRENCY = 6;

const supabaseUrl = requiredEnv("SUPABASE_URL");
const supabaseSecretKey = requiredEnv("SUPABASE_SECRET_KEY");
const jenkinsUrl = normalizeBaseUrl(requiredEnv("JENKINS_URL"));
const jenkinsUser = process.env.JENKINS_USER?.trim() || null;
const jenkinsApiToken = process.env.JENKINS_API_TOKEN?.trim() || null;
const timeoutMs = positiveInteger(process.env.JENKINS_REQUEST_TIMEOUT_MS, 10_000);

if (Boolean(jenkinsUser) !== Boolean(jenkinsApiToken)) {
  throw new Error("JENKINS_USER와 JENKINS_API_TOKEN은 둘 다 설정하거나 둘 다 생략해야 합니다.");
}

const supabase = createClient(supabaseUrl, supabaseSecretKey, {
  auth: {
    persistSession: false,
    autoRefreshToken: false,
    detectSessionInUrl: false,
  },
  global: {
    headers: { "X-Client-Info": "polymarket-dashboard-jenkins-collector/1.0" },
  },
});

const startedAt = new Date().toISOString();
const { data: jobs, error: jobsError } = await supabase
  .from("pd_jenkins_jobs")
  .select("job_name")
  .order("job_name", { ascending: true });

if (jobsError) throw new Error(`Jenkins job registry read failed: ${jobsError.message}`);

const expected = jobs?.length ?? 0;
const { data: syncRun, error: syncRunError } = await supabase
  .from("pd_sync_runs")
  .insert({
    collector_name: COLLECTOR_NAME,
    started_at: startedAt,
    status: "RUNNING",
    jobs_expected: expected,
  })
  .select("sync_run_id")
  .single();

if (syncRunError || !syncRun) {
  throw new Error(`Sync run start failed: ${syncRunError?.message ?? "missing run id"}`);
}

let observed = 0;
const failures = [];

try {
  await mapWithConcurrency(jobs ?? [], CONCURRENCY, async ({ job_name: jobName }) => {
    try {
      const payload = await fetchJob(jobName);
      const lastBuild = payload.lastBuild ?? payload.lastCompletedBuild ?? null;
      const color = stringOrNull(payload.color);
      const buildable = booleanOrNull(payload.buildable);
      const enabled = buildable !== false && !color?.toLowerCase().startsWith("disabled");
      const observedAt = new Date().toISOString();
      const { error } = await supabase
        .from("pd_jenkins_jobs")
        .update({
          buildable,
          enabled,
          in_queue: booleanOrNull(payload.inQueue),
          building: booleanOrNull(lastBuild?.building),
          job_color: color,
          last_build_number: integerOrNull(lastBuild?.number),
          last_build_status: lastBuild?.building ? "BUILDING" : stringOrNull(lastBuild?.result),
          last_build_started_at: epochToIso(lastBuild?.timestamp),
          last_build_duration_ms: nonNegativeIntegerOrNull(lastBuild?.duration),
          observed_at: observedAt,
        })
        .eq("job_name", jobName);
      if (error) throw new Error(`database update failed: ${error.message}`);
      observed += 1;
    } catch (error) {
      failures.push(`${jobName}: ${safeMessage(error)}`);
    }
  });

  const finishedAt = new Date().toISOString();
  const status = failures.length === 0 ? "SUCCESS" : observed === 0 ? "FAILED" : "PARTIAL";
  const { error: finishError } = await supabase
    .from("pd_sync_runs")
    .update({
      finished_at: finishedAt,
      status,
      jobs_observed: observed,
      jobs_failed: failures.length,
      error_summary: failures.length ? failures.slice(0, 20).join("\n").slice(0, 4_000) : null,
    })
    .eq("sync_run_id", syncRun.sync_run_id);
  if (finishError) throw new Error(`Sync run finish failed: ${finishError.message}`);

  const elapsedMs = Date.parse(finishedAt) - Date.parse(startedAt);
  console.log(`Jenkins metadata sync ${status}: ${observed}/${expected} observed, ${failures.length} failed, ${elapsedMs}ms`);
  if (failures.length) {
    failures.forEach((failure) => console.error(failure));
    process.exitCode = 1;
  }
} catch (error) {
  const finishedAt = new Date().toISOString();
  await supabase
    .from("pd_sync_runs")
    .update({
      finished_at: finishedAt,
      status: "FAILED",
      jobs_observed: observed,
      jobs_failed: Math.min(expected - observed, Math.max(0, failures.length)),
      error_summary: safeMessage(error).slice(0, 4_000),
    })
    .eq("sync_run_id", syncRun.sync_run_id);
  throw error;
}

async function fetchJob(jobName) {
  const url = jobApiUrl(jenkinsUrl, jobName);
  const headers = { Accept: "application/json" };
  if (jenkinsUser && jenkinsApiToken) {
    headers.Authorization = `Basic ${Buffer.from(`${jenkinsUser}:${jenkinsApiToken}`).toString("base64")}`;
  }

  const response = await fetch(url, {
    headers,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) throw new Error(`Jenkins HTTP ${response.status}`);
  return response.json();
}

function jobApiUrl(baseUrl, jobName) {
  const url = new URL(baseUrl);
  const basePath = url.pathname.replace(/\/$/, "");
  const jobPath = jobName
    .split("/")
    .map((part) => `/job/${encodeURIComponent(part)}`)
    .join("");
  url.pathname = `${basePath}${jobPath}/api/json`;
  url.search = new URLSearchParams({
    tree: "name,url,color,buildable,inQueue,lastBuild[number,building,result,timestamp,duration],lastCompletedBuild[number,building,result,timestamp,duration]",
  }).toString();
  return url;
}

async function mapWithConcurrency(items, concurrency, worker) {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(concurrency, Math.max(1, items.length)) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      await worker(items[index]);
    }
  });
  await Promise.all(workers);
}

function normalizeBaseUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("JENKINS_URL은 http(s) URL이어야 합니다.");
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

function requiredEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} 환경변수가 필요합니다.`);
  return value;
}

function positiveInteger(value, fallback) {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) throw new Error("JENKINS_REQUEST_TIMEOUT_MS는 양의 정수여야 합니다.");
  return parsed;
}

function booleanOrNull(value) {
  return typeof value === "boolean" ? value : null;
}

function stringOrNull(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function integerOrNull(value) {
  return Number.isSafeInteger(value) ? value : null;
}

function nonNegativeIntegerOrNull(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function epochToIso(value) {
  return Number.isFinite(value) && value > 0 ? new Date(value).toISOString() : null;
}

function safeMessage(error) {
  if (error instanceof Error) return error.message.replace(/sb_secret_[A-Za-z0-9._-]+/g, "[REDACTED]");
  return String(error).slice(0, 500);
}
