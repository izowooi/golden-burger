"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  index: { matches: [], sources: [], cohorts: [] },
  sources: [],
  sport: "soccer",
  match: null,
  hidden: new Set(),
};
const colors = [
  "#32785d",
  "#85a553",
  "#d69542",
  "#5479a6",
  "#9c6895",
  "#cb6d68",
];
function colorFor(t, i) {
  const slots = [
    "HOME:YES",
    "HOME:NO",
    "DRAW:YES",
    "DRAW:NO",
    "AWAY:YES",
    "AWAY:NO",
  ];
  const n = slots.indexOf(`${t.result_kind}:${t.outcome_side}`);
  return colors[(n < 0 ? i : n) % colors.length];
}
const names = [
  ["soccer", "축구"],
  ["mlb", "MLB"],
  ["nba", "NBA"],
  ["nfl", "NFL"],
  ["nhl", "NHL"],
  ["ufc", "UFC"],
  ["boxing", "복싱"],
];
const time = (t) =>
  new Date(t * 1000).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
const money = (v) =>
  v == null ? "확인 불가" : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(4)}`;
function el(tag, text, className) {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (className) e.className = className;
  return e;
}
async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "입력값을 확인하세요. 자료·기간을 선택하고 금액은 $0.01~$1,000으로 입력하세요.",
    );
  return data;
}
function error(e) {
  $("error").textContent = e.message || String(e);
}
function sourceFor(id) {
  return state.index.sources.find((s) => s.id === id) || {};
}
function renderSources() {
  const root = $("sourceList");
  root.replaceChildren();
  for (const s of state.sources) {
    const label = el("label", null, "source"),
      box = el("input");
    box.type = "checkbox";
    box.value = s.source_key;
    box.disabled = !s.available;
    const text = el(
      "span",
      `${s.jenkins_job} · ${s.strategy.replace("golden-", "")}`,
    );
    text.append(
      el(
        "small",
        `${s.runtime_job} · ${s.basename || ""} · ${s.available ? "로컬 DB 있음" : "동기화 필요"}`,
      ),
    );
    label.append(box, text);
    root.append(label);
  }
  if (!state.sources.length)
    root.append(el("p", "먼저 수동 동기화에서 Peach·Plum DB를 가져오세요."));
}
function renderIndex() {
  const idx = state.index;
  const count = new Set(idx.matches.map((m) => `${m.sport}:${m.event_id}`))
    .size;
  $("matchCount").textContent = count;
  $("freshness").textContent = idx.generated_at
    ? `목록 생성 ${time(Date.parse(idx.generated_at) / 1000)} KST`
    : "로컬 인덱스 없음";
  $("sportTabs").replaceChildren();
  for (const [id, label] of names) {
    const button = el("button", label, id === state.sport ? "active" : "");
    button.append(
      el(
        "small",
        String(
          new Set(
            idx.matches.filter((m) => m.sport === id).map((m) => m.event_id),
          ).size,
        ),
      ),
    );
    button.onclick = () => {
      state.sport = id;
      state.match = null;
      $("detail").hidden = true;
      $("empty").hidden = false;
      renderIndex();
      const first = state.index.matches.find(
        (m) =>
          m.sport === id &&
          (!$("sourceFilter").value ||
            m.source_id === $("sourceFilter").value) &&
          `${m.title} ${m.league}`
            .toLowerCase()
            .includes($("search").value.trim().toLowerCase()),
      );
      if (first) selectMatch(first.id).catch(error);
    };
    $("sportTabs").append(button);
  }
  const selected = $("sourceFilter").value;
  $("sourceFilter").replaceChildren(new Option("모든 자료", ""));
  for (const s of idx.sources)
    $("sourceFilter").add(
      new Option(`${s.jenkins_job} · ${s.runtime_job}`, s.id),
    );
  $("sourceFilter").value = selected;
  renderMatches();
}
function renderMatches() {
  const needle = $("search").value.trim().toLowerCase(),
    src = $("sourceFilter").value;
  const matches = state.index.matches.filter(
    (m) =>
      m.sport === state.sport &&
      (!src || m.source_id === src) &&
      `${m.title} ${m.league}`.toLowerCase().includes(needle),
  );
  $("matchList").replaceChildren();
  for (const m of matches) {
    const source = sourceFor(m.source_id);
    const button = el(
      "button",
      null,
      "match-card" + (state.match?.match.id === m.id ? " active" : ""),
    );
    button.append(
      el("small", `${time(m.start)} KST`),
      el("strong", m.title),
      el(
        "small",
        `${source.jenkins_job || ""} · ${m.token_count}개 호가 · ${m.point_count.toLocaleString()} 관측`,
      ),
    );
    button.onclick = () => selectMatch(m.id).catch(error);
    $("matchList").append(button);
  }
  if (!matches.length)
    $("matchList").append(
      el("p", "이 종목·필터에 해당하는 로컬 관측이 없습니다.", "chart-note"),
    );
}
async function selectMatch(id) {
  state.match = await api(`/api/sports/matches/${id}`);
  state.hidden = new Set();
  $("detail").hidden = false;
  $("empty").hidden = true;
  const m = state.match.match,
    s = sourceFor(m.source_id);
  $("matchTitle").textContent = m.title;
  $("matchLeague").textContent = `${m.league} / ${m.sport.toUpperCase()}`;
  $("matchMeta").textContent =
    `${time(m.start)} – ${time(m.end)} KST · ${s.jenkins_job} · ${m.token_count}/${m.expected_token_count}개 결과 · DB 동기화 ${s.synced_at ? time(Date.parse(s.synced_at) / 1000) : "미상"} KST`;
  $("coverage").textContent =
    `${m.point_count.toLocaleString()}개 관측 · 90초 이상 공백 ${m.gap_count}개 · 실패·불완전·식별/잔량 부족 ${m.invalid_count}개. 5분 미만의 짧은 누락은 선으로 보간하고 긴 공백과 실패 구간은 끊어 표시합니다.`;
  $("provenance").textContent = JSON.stringify(
    {
      source: s,
      cohort: state.index.cohorts.find((c) => c.id === m.cohort_id),
      range: state.index.range,
      semantics: state.index.semantics,
    },
    null,
    2,
  );
  $("tokenSelect").replaceChildren();
  $("legend").replaceChildren();
  state.match.tokens
    .map((t, i) => ({ t, i }))
    .sort((a, b) => {
      const slots = [
        "HOME:YES",
        "HOME:NO",
        "DRAW:YES",
        "DRAW:NO",
        "AWAY:YES",
        "AWAY:NO",
      ];
      return (
        slots.indexOf(`${a.t.result_kind}:${a.t.outcome_side}`) -
        slots.indexOf(`${b.t.result_kind}:${b.t.outcome_side}`)
      );
    })
    .forEach(({ t, i }) => {
      const label = tokenLabel(t);
      $("tokenSelect").add(new Option(label, i));
      const b = el("button");
      const dot = el("i");
      dot.style.background = colorFor(t, i);
      b.title = t.question || label;
      b.append(dot, document.createTextNode(label));
      b.onclick = () => {
        state.hidden.has(i) ? state.hidden.delete(i) : state.hidden.add(i);
        b.classList.toggle("off", state.hidden.has(i));
        draw();
      };
      $("legend").append(b);
    });
  renderMatches();
  renderPoints();
  draw();
  $("calcResult").replaceChildren(
    el("p", "A와 B를 선택해 당시 호가 잔량으로 손익을 계산하세요."),
  );
}
function tokenLabel(t) {
  if (!t.result_kind && t.outcome_side !== "DIRECT")
    return t.label || "식별 미상";
  const role = { HOME: "홈 승", DRAW: "무승부", AWAY: "원정 승" }[
    t.result_kind
  ];
  return t.outcome_side === "DIRECT"
    ? t.verified_team_name || t.label
    : `${role || t.result_kind || "?"} · ${t.outcome_side || t.label}`;
}
function renderPoints() {
  const token = Number($("tokenSelect").value);
  const rows = state.match.points
    .map((p, i) => ({ p, i }))
    .filter((x) => x.p[1] === token);
  for (const id of ["buyPoint", "sellPoint"]) {
    $(id).replaceChildren();
    for (const { p, i } of rows) {
      const price = p[id === "buyPoint" ? 2 : 3];
      $(id).add(
        new Option(
          `${time(p[0])} · ${price == null ? "잔량 부족" : price.toFixed(4)}${p[8] & 9 ? " · 근거 부족" : ""}`,
          i,
        ),
      );
    }
  }
  if (rows.length > 1) $("sellPoint").value = rows.at(-1).i;
  draw();
}
function svg(tag, attrs, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
function quoteView() {
  const mode = $("metric").value;
  if (!mode.startsWith("norm_"))
    return {
      points: state.match.points,
      metric: Number(mode),
      normalized: false,
    };
  const basis = mode.slice(5),
    points = [];
  for (const row of state.match.normalization?.series || []) {
    state.match.tokens.forEach((token, index) => {
      const value = (row.normalized[token.token_id] ||
        row.no_reference[token.token_id] ||
        {})[basis];
      points.push([
        row.t,
        index,
        value == null ? null : Number(value),
        null,
        null,
        null,
        null,
        null,
        row.valid ? 0 : 8,
        row.clock_index,
      ]);
    });
  }
  return { points, metric: 2, normalized: true };
}
function draw() {
  if (!state.match) return;
  const view = quoteView();
  $("showFailed").disabled = view.normalized;
  const root = $("chart"),
    m = state.match.match,
    points = view.points,
    metric = view.metric,
    width = Math.max(300, Math.min(1200, root.parentElement.clientWidth)),
    height = width < 500 ? 280 : 340,
    left = 35,
    right = 12,
    top = 16,
    bottom = 38;
  root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  root.replaceChildren();
  const min = m.start,
    max = Math.max(m.end, min + 1),
    x = (t) => left + ((t - min) / (max - min)) * (width - left - right),
    y = (p) => top + (1 - p) * (height - top - bottom);
  for (let i = 0; i <= 5; i++) {
    const p = i / 5;
    root.append(
      svg("line", {
        x1: left,
        y1: y(p),
        x2: width - right,
        y2: y(p),
        stroke: "#e7ece6",
        "stroke-width": 1,
      }),
      svg(
        "text",
        { x: left - 10, y: y(p) + 4, "text-anchor": "end" },
        p.toFixed(1),
      ),
    );
  }
  for (let i = 0; i < 5; i++) {
    const t = min + ((max - min) * i) / 4;
    root.append(
      svg(
        "text",
        {
          x: x(t),
          y: height - 13,
          "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle",
        },
        time(t).split(" ").slice(-1)[0],
      ),
    );
  }
  const failures = state.match.gaps.filter(
    (g) => g.reason === "failed_or_incomplete_run",
  );
  for (const g of failures) {
    const a = Math.max(min, g.start),
      b = Math.min(max, g.end);
    if (b > a)
      root.append(
        svg("rect", {
          x: x(a),
          y: top,
          width: Math.max(1, x(b) - x(a)),
          height: height - top - bottom,
          fill: "#f4ddd8",
          opacity: 0.5,
        }),
      );
  }
  state.match.tokens.forEach((token, ti) => {
    if (state.hidden.has(ti)) return;
    let path = "",
      previous = null;
    for (const p of points.filter((p) => p[1] === ti)) {
      const v = p[metric];
      if (v == null || (p[8] & 1 && !$("showFailed").checked)) {
        previous = null;
        continue;
      }
      const failedBetween =
        previous && failures.some((g) => g.start < p[0] && g.end > previous[0]);
      const connected =
        previous &&
        !(p[8] & 1) &&
        !(previous[8] & 1) &&
        p[0] - previous[0] < 300 &&
        !failedBetween;
      path += `${connected ? "L" : "M"}${x(p[0]).toFixed(2)},${y(v).toFixed(2)} `;
      if (!connected)
        root.append(
          svg("circle", {
            cx: x(p[0]),
            cy: y(v),
            r: 1.8,
            fill: colorFor(token, ti),
          }),
        );
      previous = p;
    }
    root.append(
      svg("path", {
        d: path,
        fill: "none",
        stroke: colorFor(token, ti),
        "stroke-width": 1.8,
        "stroke-linejoin": "round",
      }),
    );
  });
  for (const [id, label] of [
    ["buyPoint", "A"],
    ["sellPoint", "B"],
  ]) {
    const p = state.match.points[Number($(id).value)];
    if (p) {
      root.append(
        svg("line", {
          x1: x(p[0]),
          x2: x(p[0]),
          y1: top,
          y2: height - bottom,
          stroke: "#193b36",
          "stroke-dasharray": "3 5",
          opacity: 0.5,
        }),
        svg("text", { x: x(p[0]) + 4, y: top + 12 }, label),
      );
    }
  }
  root.onpointermove = (e) => {
    const rect = root.getBoundingClientRect(),
      px = ((e.clientX - rect.left) / rect.width) * width,
      target = min + ((px - left) / (width - left - right)) * (max - min);
    const available = points.filter(
      (p) =>
        !state.hidden.has(p[1]) &&
        p[metric] != null &&
        !(p[8] & 8) &&
        (!(p[8] & 1) || $("showFailed").checked),
    );
    if (!available.length) return;
    const p = available.reduce((a, b) =>
      Math.abs(a[0] - target) < Math.abs(b[0] - target) ? a : b,
    );
    const clock = state.match.clocks[p[9]] || {},
      raw = clock.source_sport_context?.fields || clock;
    const note = [raw.score, raw.period, raw.elapsed || raw.elapsed_raw]
      .filter((v) => v != null && typeof v !== "object")
      .join(" · ");
    const lines = state.match.tokens
      .map((t, i) => ({ t, i }))
      .filter(({ i }) => !state.hidden.has(i))
      .map(({ t, i }) => {
        const near = available.filter(
          (v) => v[1] === i && Math.abs(v[0] - p[0]) <= 2,
        );
        const sample = near.length
          ? near.reduce((a, b) =>
              Math.abs(a[0] - p[0]) < Math.abs(b[0] - p[0]) ? a : b,
            )
          : null;
        return `${tokenLabel(t)}  ${sample ? sample[metric].toFixed(4) : "해당 시각 관측 없음"}`;
      });
    $("tooltip").textContent =
      `${time(p[0])} KST · ${view.normalized ? "정규화 시장가격 / NO는 이론 보완값" : "±2초 원관측"}\n${lines.join("\n")}${note ? "\n" + note : ""}`;
    $("tooltip").hidden = false;
    $("tooltip").style.left =
      `${Math.max(0, Math.min(e.clientX - rect.left + 12, rect.width - 230))}px`;
    $("tooltip").style.top = "30px";
  };
  root.onpointerleave = () => {
    $("tooltip").hidden = true;
  };
  drawS();
}
async function calculate() {
  if (!state.match) return;
  $("calculate").disabled = true;
  try {
    const request = {
      view_version: state.match.view_version,
      buy_index: Number($("buyPoint").value),
      sell_index: Number($("sellPoint").value),
      amount: Number($("amount").value),
    };
    if ($("feeMode").value === "assumed")
      request.fee_rate = Number($("feeRate").value);
    const r = await api(
        `/api/sports/matches/${state.match.match.id}/calculate`,
        request,
      ),
      out = $("calcResult");
    out.replaceChildren();
    const grid = el("div", null, "result-grid");
    for (const [label, value] of [
      ["수수료 전 손익", r.gross_pnl],
      [
        r.fee_basis === "ASSUMED" ? "가정 순손익" : "수수료 반영 손익",
        r.net_pnl,
      ],
    ]) {
      const c = el("div", null, "result");
      c.append(
        el("small", label),
        el(
          "strong",
          money(value),
          value == null ? "" : value >= 0 ? "positive" : "negative",
        ),
      );
      grid.append(c);
    }
    const c = el("div", null, "result");
    c.append(
      el("small", "매도 대금"),
      el("strong", `$${r.proceeds.toFixed(4)}`),
    );
    grid.append(c);
    out.append(
      grid,
      el(
        "p",
        `매수 ${r.buy_vwap.toFixed(6)} × ${r.shares.toFixed(6)}주 → 매도 ${r.sell_vwap.toFixed(6)} × ${r.sell_shares.toFixed(2)}주. 미매도 잔량 ${r.dust_shares_excluded.toFixed(6)}주는 평가하지 않았습니다.`,
      ),
    );
    out.append(
      el(
        "p",
        `매수 수수료 ${r.buy_fee == null ? "미상" : "$" + r.buy_fee.toFixed(5)} · 매도 수수료 ${r.sell_fee == null ? "미상" : "$" + r.sell_fee.toFixed(5)}. 매수 원금은 수수료 별도 $${r.cost.toFixed(2)}입니다.`,
      ),
    );
    if (r.net_pnl == null)
      out.append(
        el(
          "p",
          "수수료 근거가 부족해 순손익은 확인할 수 없습니다. 가정을 입력하면 별도로 계산할 수 있습니다.",
        ),
      );
    if (r.market_status_unknown)
      out.append(
        el(
          "p",
          "과거 자료에 시장의 주문 가능 상태가 없어 호가 기준 계산만 가능합니다.",
        ),
      );
    if (r.intervening_gaps)
      out.append(
        el("p", `A~B 사이 공백/실패 구간 ${r.intervening_gaps}개가 있습니다.`),
      );
    out.append(el("p", r.note));
    $("error").textContent = "";
  } catch (e) {
    $("calcResult").replaceChildren(el("p", e.message));
  } finally {
    $("calculate").disabled = false;
  }
}
async function build() {
  const keys = [...$("sourceList").querySelectorAll("input:checked")].map(
    (e) => e.value,
  );
  $("buildIndex").disabled = true;
  try {
    const result = await api("/api/sports/import", {
      source_keys: keys,
      start: $("rangeStart").value + ":00Z",
      end: $("rangeEnd").value + ":00Z",
    });
    $("importStatus").textContent = "로컬 DB 검사·경기 목록 생성 중…";
    let done = false;
    while (!done) {
      await new Promise((r) => setTimeout(r, 1500));
      const task = await api(`/api/tasks/${result.task_id}`);
      if (task.status === "FAILED") throw Error(task.error);
      if (["SUCCESS", "PARTIAL"].includes(task.status)) {
        done = true;
        $("importStatus").textContent =
          `완료 · ${task.result.matches}개 경기/cohort`;
        state.index = await api("/api/sports/index");
        state.match = null;
        $("detail").hidden = true;
        $("empty").hidden = false;
        renderIndex();
      } else if (task.latest)
        $("importStatus").textContent =
          `${task.latest.source || ""} · ${task.latest.completed || 0}/${task.latest.total || keys.length} 자료 확인 중`;
    }
  } catch (e) {
    $("importStatus").textContent = e.message;
  } finally {
    $("buildIndex").disabled = false;
  }
}
window.addEventListener("resize", draw);
$("search").oninput = renderMatches;
$("sourceFilter").onchange = () => {
  const source = $("sourceFilter").value;
  const available = state.index.matches.filter(
    (m) => !source || m.source_id === source,
  );
  if (available.length && !available.some((m) => m.sport === state.sport))
    state.sport = available[0].sport;
  state.match = null;
  $("detail").hidden = true;
  $("empty").hidden = false;
  renderIndex();
  const first = available.find((m) => m.sport === state.sport);
  if (first) selectMatch(first.id).catch(error);
};
$("metric").onchange = draw;
$("showFailed").onchange = draw;
$("sBasis").onchange = drawS;
$("tokenSelect").onchange = renderPoints;
$("buyPoint").onchange = draw;
$("sellPoint").onchange = draw;
$("calculate").onclick = calculate;
$("buildIndex").onclick = build;
$("feeMode").onchange = () => {
  $("feeField").hidden = $("feeMode").value !== "assumed";
};
(async () => {
  const end = new Date(),
    start = new Date(end - 7 * 86400000);
  $("rangeStart").value = start.toISOString().slice(0, 16);
  $("rangeEnd").value = end.toISOString().slice(0, 16);
  [state.sources, state.index] = await Promise.all([
    api("/api/sports/sources"),
    api("/api/sports/index"),
  ]);
  renderSources();
  renderIndex();
  if (!state.index.matches.length) $("importBox").open = true;
  else {
    const first =
      state.index.matches.find((m) => m.sport === "soccer") ||
      state.index.matches[0];
    state.sport = first.sport;
    renderIndex();
    await selectMatch(first.id);
  }
})().catch(error);
