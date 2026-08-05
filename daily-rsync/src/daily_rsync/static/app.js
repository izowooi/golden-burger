"use strict";

const state = {
  jobs: [],
  selected: null,
  plan: null,
  currentTask: null,
  bundlePath: null,
  jobsRequestId: 0,
};

const $ = (id) => document.getElementById(id);
const terminalStates = new Set(["SUCCESS", "PARTIAL", "FAILED"]);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload;
  try { payload = await response.json(); }
  catch { payload = {detail: `HTTP ${response.status}`}; }
  if (!response.ok) throw new Error(payload.detail || JSON.stringify(payload));
  return payload;
}

function post(path, payload) {
  return api(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

function bytes(value = 0) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = Number(value);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function relativeTime(value) {
  if (!value) return "아직 없음";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "방금 전";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86400)}일 전`;
}

function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = "toast"; }, 3600);
}

function setBusy(button, busy, text = "처리 중…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = text;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

async function loadStatus() {
  try {
    const payload = await api("/api/status");
    const artifacts = payload.artifacts || [];
    const total = artifacts.reduce((sum, item) => sum + item.count, 0);
    const db = artifacts.filter((x) => x.kind.startsWith("database")).reduce((s,x) => s+x.count, 0);
    const logs = artifacts.filter((x) => x.kind.includes("log") || x.kind === "jenkins_console")
      .reduce((s,x) => s+x.count, 0);
    $("metricJobs").textContent = payload.jobs;
    $("metricArtifacts").textContent = total.toLocaleString();
    $("metricBreakdown").textContent = `DB ${db} · 로그 ${logs.toLocaleString()}`;
    $("metricFree").textContent = bytes(payload.free_bytes);
    $("metricRun").textContent = payload.latest_run?.status || "—";
    $("metricRunTime").textContent = relativeTime(payload.latest_run?.finished_at);
  } catch (error) {
    toast(`로컬 상태를 읽지 못했습니다: ${error.message}`, true);
  }
}

async function doctor(button) {
  setBusy(button, true, "확인 중…");
  try {
    const value = await api("/api/doctor");
    $("connectionDot").className = "status-dot ok";
    $("connectionText").textContent = `${value.ssh_host} 연결됨`;
    toast(`Mac mini 연결 성공 · 여유 ${bytes(value.free_bytes)}`);
  } catch (error) {
    $("connectionDot").className = "status-dot error";
    $("connectionText").textContent = "연결 실패";
    toast(error.message, true);
  } finally { setBusy(button, false); }
}

async function loadJobs(button) {
  const requestId = ++state.jobsRequestId;
  setBusy(button, true, "불러오는 중…");
  $("jobList").innerHTML = '<div class="empty compact-empty">Mac mini에서 Job을 찾는 중…</div>';
  try {
    const jobs = (await api("/api/jobs")).filter((job) => job.name.startsWith("polybot-"));
    if (requestId !== state.jobsRequestId) return;
    const selectedName = state.selected?.name || null;
    const selectedCurrentStrategy = state.selected?.current_strategy || null;
    const selectedStrategy = $("strategySelect").value || null;
    state.jobs = jobs;
    const selectionState = reconcileSelectedJob(
      selectedName, selectedCurrentStrategy, selectedStrategy,
    );
    await loadStatus();
    const suffix = selectionState === "removed" ? " · 이전 선택은 원격에서 사라져 해제했습니다." : "";
    toast(`${state.jobs.length}개 Job을 불러왔습니다.${suffix}`, selectionState === "removed");
  } catch (error) {
    if (requestId !== state.jobsRequestId) return;
    $("jobList").innerHTML = `<div class="notice error">${error.message}</div>`;
    toast(error.message, true);
  } finally { setBusy(button, false); }
}

function reconcileSelectedJob(selectedName, previousCurrentStrategy, selectedStrategy) {
  if (!selectedName) {
    renderJobs();
    return "none";
  }
  const refreshed = state.jobs.find((job) => job.name === selectedName);
  if (!refreshed) {
    clearSelection();
    renderJobs();
    return "removed";
  }
  const strategyChanged = previousCurrentStrategy !== refreshed.current_strategy;
  selectJob(refreshed, {
    preferredStrategy: strategyChanged ? refreshed.current_strategy : selectedStrategy,
  });
  return "updated";
}

function renderJobs() {
  const query = $("jobSearch").value.trim().toLowerCase();
  const values = state.jobs.filter((job) => {
    const evidence = job.strategy_evidence || {};
    const evidenceTerms = [
      evidence.configured_strategy,
      evidence.latest_build_strategy,
      evidence.latest_database_strategy,
    ].filter(Boolean).join(" ");
    return `${job.name} ${job.current_strategy || ""} ${evidenceTerms}`.toLowerCase().includes(query);
  });
  $("jobList").replaceChildren();
  if (!values.length) {
    $("jobList").innerHTML = '<div class="empty compact-empty">조건에 맞는 Job이 없습니다.</div>';
    return;
  }
  for (const job of values) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `job-item${state.selected?.name === job.name ? " active" : ""}`;
    const name = document.createElement("strong");
    name.textContent = job.name;
    const strategy = document.createElement("span");
    strategy.textContent = job.current_strategy || "전략 미분류";
    const builds = document.createElement("small");
    builds.className = "job-build";
    builds.textContent = `${Number(job.build_count).toLocaleString()} builds`;
    button.append(name, strategy, builds);
    button.addEventListener("click", () => selectJob(job));
    $("jobList").append(button);
  }
}

function randomJob() {
  if (!state.jobs.length) {
    toast("먼저 Job 목록을 불러오세요.", true);
    return;
  }
  selectJob(state.jobs[Math.floor(Math.random() * state.jobs.length)]);
}

function selectJob(job, {preferredStrategy = null} = {}) {
  state.selected = job;
  state.plan = null;
  state.bundlePath = null;
  renderJobs();
  $("selectionEmpty").classList.add("hidden");
  $("selectionPanel").classList.remove("hidden");
  $("planPanel").classList.add("hidden");
  $("selectedJob").textContent = job.name;
  $("selectedStrategy").textContent = job.current_strategy || "미분류";
  $("selectedBuilds").textContent = Number(job.build_count).toLocaleString();
  const strategies = [...new Set([job.current_strategy, ...(job.strategies || [])].filter(Boolean))];
  $("strategySelect").replaceChildren();
  for (const value of strategies) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    $("strategySelect").append(option);
  }
  const nextStrategy = preferredStrategy && strategies.includes(preferredStrategy)
    ? preferredStrategy
    : (job.current_strategy && strategies.includes(job.current_strategy) ? job.current_strategy : strategies[0]);
  if (nextStrategy) $("strategySelect").value = nextStrategy;
  renderStrategyEvidence(job);
  if (nextStrategy) loadCatalog();
  else $("catalogList").innerHTML = '<div class="empty compact-empty">원격 inventory에 전략이 없습니다.</div>';
  loadEpochs();
}

function clearSelection() {
  state.selected = null;
  state.plan = null;
  state.bundlePath = null;
  $("strategySelect").replaceChildren();
  $("selectionPanel").classList.add("hidden");
  $("selectionEmpty").classList.remove("hidden");
  $("planPanel").classList.add("hidden");
  $("selectionBadge").className = "badge neutral";
  $("selectionBadge").textContent = "Job 미선택";
  $("selectedJob").textContent = "—";
  $("selectedStrategy").textContent = "—";
  $("selectedBuilds").textContent = "—";
  $("catalogList").innerHTML = '<div class="empty compact-empty">Job을 선택하세요.</div>';
  $("epochList").replaceChildren();
}

function evidenceValue(value) {
  return typeof value === "string" && value.trim() ? value : "근거 없음";
}

function evidenceSourceLabel(value) {
  const labels = {
    config: "Jenkins 설정 (config.xml)",
    config_xml: "Jenkins 설정 (config.xml)",
    configured_strategy: "Jenkins 설정 (config.xml)",
    jenkins_config: "Jenkins 설정 (config.xml)",
    latest_build: "최신 완료 Build",
    latest_build_strategy: "최신 완료 Build",
    structured_build: "최신 완료 Build",
    structured_run_audit: "최신 완료 Build (RUN_AUDIT)",
    database: "최신 canonical DB",
    latest_database: "최신 canonical DB",
    latest_database_strategy: "최신 canonical DB",
    database_run_audit: "최신 canonical DB (run_audits)",
    unknown: "근거 없음",
  };
  return labels[value] || evidenceValue(value);
}

function renderStrategyEvidence(job) {
  const evidence = job.strategy_evidence && typeof job.strategy_evidence === "object"
    ? job.strategy_evidence
    : {};
  const stateValue = evidenceValue(evidence.state);
  const stateName = String(evidence.state || "").toUpperCase();
  const conflict = evidence.conflict === true
    || String(evidence.conflict).toLowerCase() === "true"
    || stateName === "CONFLICT";
  const evidenceKnown = Boolean(evidence.state) && stateName !== "UNKNOWN";
  $("evidenceConfigured").textContent = evidenceValue(evidence.configured_strategy);
  $("evidenceBuild").textContent = evidenceValue(evidence.latest_build_strategy);
  $("evidenceDatabase").textContent = evidenceValue(evidence.latest_database_strategy);
  $("strategyEvidenceSource").textContent = evidenceSourceLabel(evidence.current_source);
  $("strategyEvidenceState").textContent = stateValue;
  $("strategyEvidenceBadge").className = `badge ${conflict ? "warning" : evidenceKnown ? "success" : "neutral"}`;
  $("strategyEvidenceBadge").textContent = conflict ? "전략 근거 충돌" : evidenceKnown ? "근거 확인" : "근거 미확인";
  $("selectionBadge").className = `badge ${conflict ? "warning" : "success"}`;
  $("selectionBadge").textContent = conflict ? "확인 필요" : "선택됨";
}

function requireSelection() {
  if (!state.selected || !$("strategySelect").value) {
    toast("먼저 Jenkins Job과 전략을 선택하세요.", true);
    return false;
  }
  return true;
}

async function makePlan(button) {
  if (!requireSelection()) return;
  setBusy(button, true, "원격 조사 중…");
  $("planPanel").classList.add("hidden");
  try {
    state.plan = await post("/api/plans", {
      job: state.selected.name,
      strategy: $("strategySelect").value,
      days: Number($("daysSelect").value),
      include_safety_databases: $("safetyDatabase").checked,
    });
    const artifacts = state.plan.artifacts || [];
    $("planTransfer").textContent = artifacts.length.toLocaleString();
    $("planSkipped").textContent = Number(state.plan.skipped_unchanged).toLocaleString();
    $("planBytes").textContent = bytes(state.plan.estimated_bytes);
    $("planNotice").className = `notice ${artifacts.length ? "safe" : "neutral"}`;
    $("planNotice").textContent = artifacts.length
      ? "변경된 자료만 전송합니다. 실행 중인 DB는 online snapshot으로 보호됩니다."
      : "모든 자료가 최신입니다. 전송할 파일이 없습니다.";
    renderPlanArtifacts(artifacts);
    $("syncButton").disabled = artifacts.length === 0;
    $("planPanel").classList.remove("hidden");
  } catch (error) {
    toast(error.message, true);
  } finally { setBusy(button, false); }
}

function renderPlanArtifacts(artifacts) {
  $("planArtifacts").replaceChildren();
  for (const item of artifacts) {
    const row = document.createElement("div");
    row.className = "artifact-row";
    const kind = document.createElement("span");
    kind.textContent = item.kind;
    const size = document.createElement("span");
    size.textContent = bytes(item.size_bytes);
    const path = document.createElement("span");
    path.textContent = item.remote_path;
    path.title = item.remote_path;
    row.append(kind, size, path);
    $("planArtifacts").append(row);
  }
}

async function startSync(button) {
  if (!state.plan) return;
  setBusy(button, true, "등록 중…");
  try {
    const task = await post(`/api/plans/${state.plan.plan_id}/sync`);
    monitorTask(task.task_id);
  } catch (error) {
    toast(error.message, true);
    setBusy(button, false);
  }
}

async function startVerify(button) {
  if (!requireSelection()) return;
  setBusy(button, true, "등록 중…");
  try {
    const query = new URLSearchParams({
      job: state.selected.name,
      strategy: $("strategySelect").value,
    });
    const task = await post(`/api/verify?${query}`);
    monitorTask(task.task_id);
  } catch (error) {
    toast(error.message, true);
    setBusy(button, false);
  }
}

async function monitorTask(taskId) {
  state.currentTask = taskId;
  $("taskCard").classList.remove("hidden");
  $("taskProgress").style.width = "3%";
  let done = false;
  while (!done && state.currentTask === taskId) {
    try {
      const task = await api(`/api/tasks/${taskId}`);
      renderTask(task);
      done = terminalStates.has(task.status);
      if (!done) await new Promise((resolve) => setTimeout(resolve, 1200));
      else {
        toast(task.status === "SUCCESS" ? "작업이 완료되었습니다." : `작업 ${task.status}`, task.status === "FAILED");
        document.querySelectorAll("button[data-original-text]").forEach((button) => setBusy(button, false));
        await Promise.all([loadStatus(), loadRuns(), loadCatalog()]);
        if (task.result?.path) {
          state.bundlePath = task.result.path;
          $("bundleResult").className = "notice safe";
          $("bundleResult").textContent = task.result.path;
          $("openBundleButton").classList.remove("hidden");
        }
      }
    } catch (error) {
      toast(error.message, true);
      break;
    }
  }
}

function renderTask(task) {
  $("taskTitle").textContent = task.label || "작업 진행";
  $("taskStatus").textContent = task.status;
  $("taskStatus").className = `badge ${
    task.status === "SUCCESS" ? "success" : task.status === "FAILED" ? "failed" : "working"}`;
  const latest = task.latest || {};
  const total = Number(latest.total || 0);
  const completed = Number(latest.completed || (terminalStates.has(task.status) ? total : 0));
  const percent = total ? Math.max(3, Math.round(completed / total * 100)) : (terminalStates.has(task.status) ? 100 : 8);
  $("taskProgress").style.width = `${percent}%`;
  $("taskPhase").textContent = phaseLabel(latest.phase, latest);
  $("taskCount").textContent = total ? `${completed.toLocaleString()} / ${total.toLocaleString()}` : task.status;
  $("taskRaw").textContent = JSON.stringify(task, null, 2);
}

function phaseLabel(phase, payload) {
  const labels = {
    start: "작업 시작", console_batch: `Jenkins 로그 묶음 ${payload.batch || 1}/${payload.batches || 1}`,
    artifact: payload.kind?.startsWith("database") ? "SQLite snapshot 동기화" : "로그 동기화",
    progress: "검증 및 저장", finished: "작업 완료",
  };
  return labels[phase] || "백그라운드 처리 중";
}

async function loadCatalog() {
  if (!requireSelection()) return;
  const query = new URLSearchParams({job: state.selected.name, strategy: $("strategySelect").value});
  try {
    const rows = await api(`/api/catalog?${query}`);
    renderCatalog(rows);
  } catch (error) { toast(error.message, true); }
}

function renderCatalog(rows) {
  $("catalogList").replaceChildren();
  if (!rows.length) {
    $("catalogList").innerHTML = '<div class="empty compact-empty">아직 동기화된 자료가 없습니다.</div>';
    return;
  }
  const sorted = [...rows].sort((a,b) => (a.kind || "").localeCompare(b.kind || ""));
  for (const row of sorted) {
    const item = document.createElement("div");
    item.className = "data-row";
    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${row.kind} · ${row.runtime_job || row.build_number || "job"}`;
    const path = document.createElement("span");
    path.textContent = row.local_path || "local file 없음";
    path.title = row.local_path || "";
    const meta = document.createElement("small");
    meta.textContent = `${row.status} · ${bytes(row.remote_size_bytes)} · ${relativeTime(row.synced_at)}`;
    info.append(title, path, meta);
    const actions = document.createElement("div");
    actions.className = "data-actions";
    if (row.local_path) {
      actions.append(smallButton("Finder", () => openPath(row.local_path)));
    }
    if (row.kind.startsWith("database") && row.status === "SYNCED") {
      actions.append(smallButton("Pin", () => pinDatabase(row.source_key)));
    }
    item.append(info, actions);
    $("catalogList").append(item);
  }
}

function smallButton(text, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button ghost compact";
  button.textContent = text;
  button.addEventListener("click", handler);
  return button;
}

async function pinDatabase(sourceKey) {
  try {
    const result = await post("/api/pins", {source_key: sourceKey});
    toast(`DB snapshot 고정 완료: ${result.path}`);
    await loadStatus();
  } catch (error) { toast(error.message, true); }
}

async function createBundle(button) {
  if (!requireSelection()) return;
  if (!$("bundleFrom").value || !$("bundleTo").value) {
    toast("Bundle 시작일과 종료일을 선택하세요.", true);
    return;
  }
  setBusy(button, true, "생성 중…");
  try {
    const task = await post("/api/bundles", {
      job: state.selected.name,
      strategy: $("strategySelect").value,
      from_date: $("bundleFrom").value,
      to_date: $("bundleTo").value,
    });
    monitorTask(task.task_id);
  } catch (error) {
    toast(error.message, true);
    setBusy(button, false);
  }
}

async function openPath(path) {
  if (!path) return;
  try { await post("/api/open", {path}); }
  catch (error) { toast(error.message, true); }
}

async function saveEpoch(button) {
  if (!requireSelection()) return;
  const alias = $("accountAlias").value.trim();
  if (!alias) { toast("계좌 별칭을 입력하세요.", true); return; }
  setBusy(button, true, "저장 중…");
  try {
    await post("/api/account-epochs", {
      job: state.selected.name,
      strategy: $("strategySelect").value,
      account_alias: alias,
      first_build: Number($("firstBuild").value),
    });
    toast("계좌 deployment epoch를 저장했습니다.");
    await loadEpochs();
  } catch (error) { toast(error.message, true); }
  finally { setBusy(button, false); }
}

async function loadEpochs() {
  if (!state.selected) return;
  try {
    const rows = await api(`/api/account-epochs?job=${encodeURIComponent(state.selected.name)}`);
    $("epochList").replaceChildren();
    for (const row of rows) {
      const item = document.createElement("div");
      item.className = "data-row";
      item.innerHTML = `<div><strong></strong><span></span></div>`;
      item.querySelector("strong").textContent = row.account_alias;
      item.querySelector("span").textContent =
        `${row.strategy} · build ${row.first_build}부터${row.last_build ? ` ${row.last_build}까지` : ""}`;
      $("epochList").append(item);
    }
    if (!rows.length) $("epochList").innerHTML = '<div class="empty compact-empty">등록된 구간이 없습니다.</div>';
  } catch (error) { toast(error.message, true); }
}

async function retentionPreview() {
  try {
    const value = await api("/api/retention");
    $("retentionResult").className = `notice ${value.candidates ? "warn" : "safe"}`;
    $("retentionResult").textContent =
      `${value.retention_days}일 초과 ${value.candidates.toLocaleString()}개 · 회수 가능 ${bytes(value.bytes_reclaimable)} · bundle 보호 ${value.protected_by_bundle}개`;
  } catch (error) { toast(error.message, true); }
}

async function retentionApply() {
  if (!confirm("미리보기에서 확인한 보존 기간 초과 로그를 실제로 정리할까요? 이 작업은 local 로그만 삭제합니다.")) return;
  try {
    const value = await post("/api/retention");
    $("retentionResult").className = "notice safe";
    $("retentionResult").textContent = `${value.candidates}개 로그를 정리했습니다.`;
    toast("보존 정책 정리가 완료되었습니다.");
    await Promise.all([loadStatus(), loadCatalog()]);
  } catch (error) { toast(error.message, true); }
}

async function loadRuns() {
  try {
    const rows = await api("/api/runs?limit=8");
    $("runList").replaceChildren();
    for (const row of rows) {
      const item = document.createElement("div");
      item.className = "data-row";
      const info = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `${row.jenkins_job} · ${row.strategy}`;
      const meta = document.createElement("span");
      meta.textContent = `${row.status} · 전송 ${row.transferred} · 건너뜀 ${row.skipped} · 실패 ${row.failed}`;
      const date = document.createElement("small");
      date.textContent = `${relativeTime(row.finished_at || row.started_at)} · ${bytes(row.bytes_written)}`;
      info.append(title, meta, date);
      const badge = document.createElement("span");
      badge.className = `badge ${row.status === "SUCCESS" ? "success" : "failed"}`;
      badge.textContent = row.status;
      item.append(info, badge);
      $("runList").append(item);
    }
    if (!rows.length) $("runList").innerHTML = '<div class="empty compact-empty">동기화 이력이 없습니다.</div>';
  } catch (error) { toast(error.message, true); }
}

function activateTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
  if (name === "catalog") loadCatalog();
  if (name === "epoch") loadEpochs();
  if (name === "retention") retentionPreview();
}

function setDefaultDates() {
  const today = new Date();
  const earlier = new Date(today);
  earlier.setDate(today.getDate() - 7);
  const iso = (value) => value.toISOString().slice(0, 10);
  $("bundleFrom").value = iso(earlier);
  $("bundleTo").value = iso(today);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const actions = {
    doctor: () => doctor(button),
    "load-jobs": () => loadJobs(button),
    "random-job": randomJob,
    "make-plan": () => makePlan(button),
    "start-sync": () => startSync(button),
    verify: () => startVerify(button),
    "load-catalog": loadCatalog,
    bundle: () => createBundle(button),
    "open-bundle": () => openPath(state.bundlePath),
    "save-epoch": () => saveEpoch(button),
    "load-epochs": loadEpochs,
    "retention-preview": retentionPreview,
    "retention-apply": retentionApply,
    "load-runs": loadRuns,
  };
  actions[button.dataset.action]?.();
});

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
$("jobSearch").addEventListener("input", renderJobs);
$("strategySelect").addEventListener("change", () => {
  state.plan = null;
  $("planPanel").classList.add("hidden");
  loadCatalog();
});

setDefaultDates();
Promise.all([loadStatus(), loadRuns(), doctor(null), loadJobs(null)]);
