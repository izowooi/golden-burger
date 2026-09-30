"""Thesis-oriented monthly research summary (docs/research/monthly/YYYY-MM.md), deterministic tables
plus the AI narrative when one exists."""

from __future__ import annotations

from polylab.reports.render import _n, _pct, _table

LIMITATIONS = [
    "가격은 1분 bar(canonical: poll_mid > ws_last > history)이므로 10초·30초 반응은 측정하지 못한다.",
    "calibration 표본은 토큰×구간당 첫 가격 1개로 제한해 경기 내 자기상관을 줄였지만, 같은 경기의 토큰들(홈/원정/무)은 여전히 서로 종속이다.",
    "경기 시간은 feed의 game_minute을 우선하고, 없으면 시작 시각 기준 경과 wall-clock으로 근사한다(MLB는 이닝 단위 가정).",
    "득점 이벤트는 game_states의 점수 변화로 검출하므로 feed 지연·정정이 섞일 수 있다. NBA/NFL은 1분 안에 여러 득점이 겹치는 경우가 많다(n_isolated 참고).",
    "실거래 손익은 CONFIRMED 체결과 확인된 정산만 포함한다. paper 결과는 체결 가능성을 과대평가할 수 있다.",
]


def render_monthly(report: dict, narrative: str | None) -> str:
    res = report["research"]
    lines = [f"# 월간 연구 요약 {report['name']}", "",
             f"생성 {report['generated_at']} · commit `{report.get('git_commit') or '–'}` · "
             f"calibration 기준 {res.get('calibration_generated_at') or '–'} · 이벤트 기준 {res.get('events_generated_at') or '–'}",
             "", "## 1. 연구 질문", "",
             "1. 경기 진행 시간(구간)에 따라 Polymarket 가격은 실현 확률 대비 얼마나 과대/과소 평가되는가?",
             "2. 같은 이벤트(득점)에 대한 가격 민감도는 경기 후반으로 갈수록 얼마나 커지는가? 이후 되돌림이 있는가?",
             "3. 이 편향을 이용할 때 어느 stake 단위(5→10→25→50→100 USDC)가 가장 안정적인 수익을 내는가?", "",
             "## 2. Calibration (종목 × 경기 구간)", ""]
    cal = [c for c in res.get("calibration", []) if c["phase"] != "all"]
    if cal:
        rows = [[c["sport"], c["phase"], f"{b['p_lo']:.2f}–{b['p_hi']:.2f}", b["n"], _n(b["mean_price"], 3),
                 _n(b["win_rate"], 3), f"{_n(b['ci_lo'], 3)}–{_n(b['ci_hi'], 3)}", _n(b["gap"], 3, True)]
                for c in cal for b in c["buckets"] if b["n"] >= 20]
        lines += _table(["종목", "구간", "가격대", "n", "평균가", "승률", "95% CI", "gap"], rows) if rows else \
            ["표본 20 이상인 버킷 없음."]
    else:
        lines.append("calibration 결과 없음.")
    lines += ["", "### Brier score", ""]
    brier = res.get("brier") or []
    lines += _table(["종목", "구간", "n", "Brier"], [[b["sport"], b["phase"], b["n"], _n(b["brier"], 4)] for b in brier]) \
        if brier else ["없음."]
    lines += ["", "## 3. 득점 이벤트 민감도 (경기 시간대별)", ""]
    ev = res.get("event_sensitivity") or []
    lines += _table(["종목", "이벤트", "경기분", "n", "고립 n", "|점프| 평균", "점프 중앙값", "되돌림 1분", "되돌림 5분", "되돌림 10분"],
                    [[e["sport"], e["event"], e["minute_bucket"], e["n"], e.get("n_isolated"), _n(e["mean_abs_jump"], 3),
                      _n(e["median_jump"], 3, True), _n(e.get("reversion_1m"), 3, True), _n(e["reversion_5m"], 3, True),
                      _n(e["reversion_10m"], 3, True)] for e in ev]) if ev else ["이벤트 결과 없음."]
    lines += ["", "## 4. Stake 단위별 안정성 (live 정산분)", ""]
    tiers = report.get("stake_tiers") or []
    lines += _table(["단위$", "변형", "거래", "손익", "ROI", "손익σ", "MDD", "sharpe-like"],
                    [[f"{t['tier_usdc']:g}", t["variants"], t["trades"], _n(t["pnl"], sign=True), _pct(t["roi"]),
                      _n(t["pnl_std"]), _n(t["max_drawdown"]), _n(t["sharpe_like"], 3)] for t in tiers]) \
        if tiers else ["live 정산 거래 없음."]
    lines += ["", "## 5. 해석 (AI)", "", (narrative or "AI 서술 없음 (결정론 요약만).").strip(), "",
              "## 6. 한계", "", *[f"- {x}" for x in LIMITATIONS], "",
              "## 7. 방법 메모", "", *[f"- {x}" for x in res.get("notes") or []], ""]
    return "\n".join(lines)
