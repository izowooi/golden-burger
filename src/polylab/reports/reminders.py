"""Owner reminders that switch on by date or by a missing resource (pure).

- Dated reminders: the owner asked to be told something on/after a date (2026-10-06 evening: report the 7-day
  storage steady state on/after 2026-10-13). From `start` they become an attention item (rule family `reminder`)
  until the owner answers it in reports/decisions.md, and for `slack_days` days a line in the retro Slack post.
- Account requests: a variant waiting for a funded account (late-leader-paper: no spare funded account on
  2026-10-06) keeps a `decide` item open while its yaml has no account.
"""

from __future__ import annotations

import datetime as dt

DATED = (
    {"id": "reminder:storage-7d-steady-state", "start": "2026-10-13", "slack_days": 3, "kind": "storage",
     "title": "저장 증가 7일 정상 상태 재확인 (예산 월 50GB)"},
    # 2026-10-10 owner: report the ai-ou05 (AI cross-checked O/U 0.5, paper) progress one week later
    {"id": "reminder:ai-ou05-1w", "start": "2026-10-17", "slack_days": 3, "kind": "ai_ou05",
     "title": "AI 교차검증 O/U 0.5(ai-ou05-red, paper) 1주 진행 상황"},
    # 2026-10-10 `hypothesis:lts-applied`: the only cell passing R1-R7 failed the pre-registered multiple-comparison check
    {"id": "decision:lts-nfl-king-live", "start": "2026-10-10", "slack_days": 2, "kind": "text",
     "title": "LTS NFL arm A(lts-king) live 여부 — 사전 등록 규칙 중 다중 비교 점검만 실패",
     "detail": "후반 임계 안정성(LTS) 사전 등록 grid 600칸 중 R1–R6 을 통과한 것은 NFL 막판(예정 시작 170분 = 진행 90%, 선두 0.85–0.93) "
               "칸뿐이다. lts-king NFL(170분·Y 0.90·경기 끝까지 대기, 매수호가 −1틱 지정가·정산 보유): 체결 131건 maker ROI +5.3%(H1 +8.1% / "
               "H2 +2.2%, 95% 하한 양수, 시즌 2024 +7.9%(65)·2025 +0.4%(51)·2026 +11.2%(15)), 엔진 재생(R7) 131건 +5.2% 로 재현. 그러나 "
               "사전 등록한 다중 비교 점검(H1 상위 20칸의 H2 ROI 중앙값)이 −5.1%(6/20)라 규칙상 '강건한 양수'가 아니어서 paper 로 두었다. "
               "같은 효과가 apricot-eco NFL(190분, live)·late-leader NFL(170분)에서도 나온 것이라 독립 증거가 아니다. 경기 중 post-only 지정가가 "
               "실제로 걸려 있는지는 2026-10-10 king 계좌로 직접 확인했다(docs/research/api-sources.md 'In-play maker'). 선택지: (a) paper 유지 — "
               "결정론 게이트가 새 paper 체결 15건 이상·80% 하한 > 0 이면 자동 전환(시즌당 약 40체결이라 수 주), (b) 지금 NFL 만 5 USDC live. "
               "근거 docs/research/hypothesis-lts.md. decisions.md 에 이 id 로 답하면 닫힌다."},
)
ACCOUNT_REQUESTS = {
    "late-leader-paper": ("account:late-leader-paper",
                          "late-leader-paper 실거래용 계좌가 필요합니다",
                          "막판 선두 수렴 가설(late_leader)은 계좌 없이 paper 로만 돈다. 2026-10-06 확인: 자금이 있는 여유 계좌가 없다"
                          "(bear·fox·wolf·eagle·orange 현금 0, yellow 현금 0, red 는 수동 트랙). 실거래를 원하면 계좌 하나에 USDC 를 넣고 "
                          "별칭을 알려 주세요(다음 작업에서 yaml 에 연결). 그 뒤에도 live 는 게이트를 통과한 종목만 5 USDC 다: 지금 후보는 "
                          "NFL 하나, NHL 은 현재 재생 규칙(n ≥ 40)에 닿지 않아 창 결정이 필요하다. 근거 "
                          "docs/research/hypothesis-late-leader-convergence.md"),
}


def _day(now: int) -> dt.date:
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).date()


def storage_detail(storage: dict | None) -> str:
    if not storage or storage.get("error"):
        return "저장 증가 측정값이 없다(polylab health 확인 필요)."
    areas = storage.get("areas") or {}
    days = [r.get("steady_days") for r in areas.values() if r.get("steady_days") is not None]
    parts = ", ".join(f"{n} {r['gb_30d']:g}" for n, r in sorted(areas.items(), key=lambda x: -(x[1].get("gb_30d") or 0))
                      if r.get("gb_30d"))
    proj = storage.get("projected_30d_gb")
    head = (f"정상 상태 예측 {proj:g}GB/월(최근 7일 일별 중앙값, 측정 일수 최소 {min(days) if days else 0}일), "
            if proj is not None else "정상 상태 예측: 아직 측정 중, ")
    return (head + f"현재 총 {storage.get('total_gb_now')}GB, 수준 {storage.get('level') or '-'}"
            + (f"; 영역별 GB/월: {parts}" if parts else "")
            + (f"; 일회성(백필) {storage['one_time_gb']:g}GB 제외" if storage.get("one_time_gb") else "")
            + ". 2026-10-06 잠정치는 약 5.7GB/월(표본 1일). 예산(월 50GB) 안이면 decisions.md 에 확인을 남기면 이 항목이 닫힌다.")


def ai_ou05_detail(p: dict | None) -> str:
    if not p or p.get("error"):
        return "진행 상황을 읽지 못했다(polylab report 확인 필요)."
    parts = []
    for side, label in (("over", "Over(0:0 아님)"), ("under", "Under(0:0)")):
        x = p.get(side) or {}
        hit = f"{x.get('picks_hit', 0)}/{x.get('picks_resolved', 0)}" if x.get("picks_resolved") else "정산 전"
        money = (f"체결 {x.get('filled', 0)}건(평균 매수가 {x['mean_entry']:.3f}) · 정산 {x.get('settled', 0)}건 "
                 f"{x.get('wins', 0)}승 · 손익 {x.get('pnl', 0):+.2f} USDC"
                 + (f"(ROI {x['roi']:+.1%})" if x.get("roi") is not None else "")) if x.get("filled") else "체결 0건"
        parts.append(f"{label}: 픽 {x.get('picks', 0)}경기, 픽 적중 {hit}, {money}")
    return ("; ".join(parts) + ". 체결은 매수호가 한 틱 아래 지정가(paper). 대시보드 /strategies/ai-ou05-red, "
            "근거 docs/research/ai-ou05-study.md. 확인 후 decisions.md 에 이 id 로 답하면 닫힌다(Over live 여부 포함).")


def due(report: dict, now: int) -> list[dict]:
    """[{id, title, detail, severity, slack}] active at `now` for this report."""
    out = []
    today = _day(now)
    for r in DATED:
        start = dt.date.fromisoformat(r["start"])
        if today < start:
            continue
        detail = storage_detail((report.get("health") or {}).get("storage")) if r["kind"] == "storage" \
            else ai_ou05_detail(report.get("ai_ou05")) if r["kind"] == "ai_ou05" \
            else r.get("detail", "")       # kind "text": a fixed owner question
        out.append({"id": r["id"], "title": r["title"], "detail": detail, "severity": "decide",
                    "slack": (today - start).days < int(r.get("slack_days", 0))})
    for v in report.get("variants") or []:
        req = ACCOUNT_REQUESTS.get(v.get("id"))
        if req and not v.get("account") and v.get("mode") != "off":
            out.append({"id": req[0], "title": req[1], "detail": req[2], "severity": "decide", "slack": False})
    return out
