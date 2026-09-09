"use strict";
function drawS() {
  if (!state.match) return;
  const depth = $("sBasis").value === "depth";
  $("sMidLegend").hidden = depth;
  const normalization = state.match.normalization,
    root = $("sChart");
  root.replaceChildren();
  const series = normalization?.series || [],
    coverage = normalization?.coverage || {};
  const mids = series.filter((r) => r.valid && r.s_mid != null),
    last = mids.at(-1);
  $("sLatest").textContent = last ? Number(last.s_mid).toFixed(4) : "계산 불가";
  $("sDeviation").textContent = mids.length
    ? (
        Math.max(...mids.map((r) => Math.abs(Number(r.s_mid) - 1))) * 100
      ).toFixed(2) + "%p"
    : "—";
  $("sCoverage").textContent =
    `${coverage.mid_complete || 0} / ${coverage.groups || 0}`;
  $("sExplanation").textContent =
    `같은 실행에서 기록 시각 차이가 2초 이내인 분모만 계산합니다. 원본 HTTP 수신 시각까지 검증된 중간값 관측 ${coverage.verified_receipt_mid || 0}개이며, 나머지 과거 자료는 DB 기록 시각의 근사입니다. 호가 잔량·주문 가능 상태 확인 ${coverage.signal_eligible || 0}개, 왕복 수수료 근거까지 있는 관측 ${coverage.cost_evidence_eligible || 0}개. S ask가 1보다 큰 현상에는 스프레드가 포함되며, 실제 매도 가격은 bid입니다. ${coverage.reason || ""}`;
  if (!series.length) return;
  const curves = depth
    ? [
        ["s_ask", "5주 ask 합", "#b8723c"],
        ["s_bid", "5주 bid 합", "#4b78a1"],
      ]
    : [
        ["s_best_ask", "ask 합", "#b8723c"],
        ["s_mid", "중간값 합", "#286e52"],
        ["s_best_bid", "bid 합", "#4b78a1"],
      ];
  const values = series.flatMap((r) =>
    r.valid
      ? curves
          .map(([k]) => (r[k] == null ? null : Number(r[k])))
          .filter((v) => v != null)
      : [],
  );
  if (!values.length) return;
  const width = Math.max(300, root.parentElement.clientWidth),
    height = 220,
    left = 43,
    right = 12,
    top = 16,
    bottom = 34;
  root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const start = state.match.match.start,
    end = Math.max(state.match.match.end, start + 1),
    low = Math.min(1, ...values),
    high = Math.max(1, ...values),
    pad = Math.max(0.01, (high - low) * 0.08),
    min = low - pad,
    max = high + pad;
  const x = (t) =>
      left + ((t - start) / (end - start)) * (width - left - right),
    y = (v) => top + ((max - v) / (max - min)) * (height - top - bottom);
  for (let i = 0; i <= 4; i++) {
    const v = min + ((max - min) * i) / 4;
    root.append(
      svg("line", {
        x1: left,
        x2: width - right,
        y1: y(v),
        y2: y(v),
        stroke: "#e7ece6",
      }),
      svg(
        "text",
        { x: left - 6, y: y(v) + 4, "text-anchor": "end" },
        v.toFixed(3),
      ),
    );
  }
  root.append(
    svg("line", {
      x1: left,
      x2: width - right,
      y1: y(1),
      y2: y(1),
      stroke: "#193b36",
      "stroke-dasharray": "4 4",
    }),
    svg(
      "text",
      { x: width - right - 2, y: y(1) - 5, "text-anchor": "end" },
      "S = 1",
    ),
  );
  for (let i = 0; i < 5; i++) {
    const t = start + ((end - start) * i) / 4;
    root.append(
      svg(
        "text",
        {
          x: x(t),
          y: height - 10,
          "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle",
        },
        time(t).split(" ").at(-1),
      ),
    );
  }
  for (const [key, label, color] of curves) {
    let path = "",
      previous = null;
    for (const r of series) {
      const v = r[key] == null ? null : Number(r[key]);
      if (!r.valid || v == null) {
        previous = null;
        continue;
      }
      const connected =
        previous &&
        r.t - previous.t < 90 &&
        !state.match.gaps.some(
          (g) =>
            g.reason === "failed_or_incomplete_run" &&
            g.start < r.t &&
            g.end > previous.t,
        );
      path += `${connected ? "L" : "M"}${x(r.t)},${y(v)} `;
      if (!connected)
        root.append(
          svg("circle", { cx: x(r.t), cy: y(v), r: 1.8, fill: color }),
        );
      previous = r;
    }
    root.append(
      svg("path", {
        d: path,
        fill: "none",
        stroke: color,
        "stroke-width": 1.7,
      }),
    );
  }
  root.onpointermove = (e) => {
    const rect = root.getBoundingClientRect(),
      t =
        start +
        ((((e.clientX - rect.left) / rect.width) * width - left) /
          (width - left - right)) *
          (end - start),
      available = series.filter((r) => r.valid);
    if (!available.length) return;
    const row = available.reduce((a, b) =>
      Math.abs(a.t - t) < Math.abs(b.t - t) ? a : b,
    );
    const lines = curves.map(
      ([k, label]) =>
        `${label}: ${row[k] == null ? "미관측" : Number(row[k]).toFixed(5) + " (" + ((Number(row[k]) - 1) * 100).toFixed(2) + "%p)"}`,
    );
    if (row.ask_excess_from_mid != null)
      lines.push(
        `ask 초과분 = 중간값 ${(Number(row.ask_excess_from_mid) * 100).toFixed(2)} + 스프레드 ${(Number(row.ask_excess_from_spread) * 100).toFixed(2)} %p`,
      );
    lines.push(
      `분모 시각차 ${Number(row.skew_seconds).toFixed(3)}초 · ${row.timestamp_basis === "VERIFIED_RAW_RECEIPT" ? "원본 수신 시각" : "DB 기록 시각 근사"}${row.market_closed_observed ? " · 시장 종료 관측" : row.market_open_unknown ? " · 주문 가능 상태 미상" : ""}`,
    );
    $("sTooltip").textContent = time(row.t) + " KST\n" + lines.join("\n");
    $("sTooltip").hidden = false;
    $("sTooltip").style.left =
      Math.max(0, Math.min(e.clientX - rect.left + 8, rect.width - 290)) + "px";
    $("sTooltip").style.top = "12px";
  };
  root.onpointerleave = () => {
    $("sTooltip").hidden = true;
  };
}
