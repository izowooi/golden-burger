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


def due(report: dict, now: int) -> list[dict]:
    """[{id, title, detail, severity, slack}] active at `now` for this report."""
    out = []
    today = _day(now)
    for r in DATED:
        start = dt.date.fromisoformat(r["start"])
        if today < start:
            continue
        detail = storage_detail((report.get("health") or {}).get("storage")) if r["kind"] == "storage" else ""
        out.append({"id": r["id"], "title": r["title"], "detail": detail, "severity": "decide",
                    "slack": (today - start).days < int(r.get("slack_days", 0))})
    for v in report.get("variants") or []:
        req = ACCOUNT_REQUESTS.get(v.get("id"))
        if req and not v.get("account") and v.get("mode") != "off":
            out.append({"id": req[0], "title": req[1], "detail": req[2], "severity": "decide", "slack": False})
    return out
