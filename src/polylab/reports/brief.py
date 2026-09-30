"""'오늘의 브리프': 3-6 Korean bullets at the top of every retro report and Slack message, for a reader who
only reads the retros. Deterministic from the report, this run's applied/rejected changes and the attention
inbox; the AI contributes at most the research highlight (its own research_finding item)."""

from __future__ import annotations

import datetime as dt
import re

from polylab.reports.build import KIND_KO, best_worst, kst

ATTENTION_URL = "https://github.com/izowooi/golden-burger/blob/main/reports/attention.md"
MAX_CHANGES = 3


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _s(v) -> str:
    return "–" if v is None else f"{v:+.2f}"


def pnl_line(report: dict, prev: dict | None) -> str:
    t = report["totals"]
    tail = f"누적 {_s(t['all'])} USDC · 기간 정산 {t['settled_24h']}건 · 보유 {t['open_positions']}개"
    best, worst = best_worst(report.get("strategy_sport") or [])
    if best and worst:
        tail += f" · 최고 `{best['variant_id']}` {_s(best['realized_pnl'])} / 최저 `{worst['variant_id']}` " \
                f"{_s(worst['realized_pnl'])}"
    elif best:
        tail += f" · `{best['variant_id']}` {_s(best['realized_pnl'])}"
    if prev and prev.get("live_pnl_all") is not None and _ts(prev.get("at")):
        delta = round((t["all"] or 0.0) - prev["live_pnl_all"], 4)
        return (f"**손익** 지난 회고({kst(_ts(prev['at']))} {KIND_KO.get(prev.get('kind'), '')}) 이후 live 실현 "
                f"{_s(delta)} USDC · {tail}")
    return f"**손익** live 실현 오늘 {_s(t['today'])} USDC · {tail}"


def change_line(report: dict, prev: dict | None, applied: list[dict], rejected: list[dict]) -> str:
    since = _ts((prev or {}).get("at")) or _ts(report["window"]["since"]) or 0
    rows = [f"`{a['variant_id']}` {a.get('summary')} ({a.get('source')})" for a in applied]
    rows += [c["summary"] for c in report.get("changes") or [] if (_ts(c.get("at")) or 0) > since]
    refused = sum(1 for r in rejected if "not applied (--no-apply)" not in str(r.get("reason")))
    tail = f" · 거부된 제안 {refused}건" if refused else ""
    if not rows:
        return f"**변경** 지난 회고 이후 파라미터·단위 변경 없음{tail}"
    more = f" 외 {len(rows) - MAX_CHANGES}건" if len(rows) > MAX_CHANGES else ""
    return "**변경** " + "; ".join(rows[:MAX_CHANGES]) + more + tail


def decision_line(opens: list[dict]) -> str:
    decide = [i for i in opens if i.get("severity") == "decide"]
    link = f"[attention.md]({ATTENTION_URL})"
    if not decide:
        return f"**결정 필요** 없음 · 열린 확인 항목 {len(opens)}건 → {link}"
    more = f" 외 {len(decide) - 1}건" if len(decide) > 1 else ""
    return f"**결정 필요 {len(decide)}건** {decide[0]['title']}{more} → {link}"


def risk_line(opens: list[dict]) -> str | None:
    hot = [i for i in opens if i.get("severity") in ("critical", "warn")]
    if not hot:
        return None
    label = "긴급" if any(i["severity"] == "critical" for i in hot) else "경고"
    more = f" 외 {len(hot) - 2}건" if len(hot) > 2 else ""
    return f"**{label} {len(hot)}건** " + "; ".join(i["title"] for i in hot[:2]) + more


def research_line(report: dict, ai_items: list[dict]) -> str:
    finding = next((i for i in ai_items if i.get("category") == "research_finding"), None)
    if finding:
        return f"**연구** {finding['title']} (AI 판단, 근거 `{finding['evidence_ref']}`)"
    top = next((b for b in (report.get("research") or {}).get("calibration_top") or [] if b.get("significant")), None)
    if top:
        side = "과소평가" if top["gap"] > 0 else "과대평가"
        return (f"**연구** {top['sport']} {top['phase']} 구간 가격 {top['p_lo']:.2f}–{top['p_hi']:.2f}: 실제 승률 "
                f"{top['win_rate']:.3f} vs 평균가 {top['mean_price']:.3f} (gap {top['gap']:+.3f}, n={top['n']}, "
                f"95% CI {top['ci_lo']:.3f}–{top['ci_hi']:.3f}) → {side}")
    return "**연구** 유의한 calibration gap 없음 (표본 부족 또는 분석 미실행)"


def build(report: dict, *, prev: dict | None, applied: list[dict], rejected: list[dict], opens: list[dict],
          ai_items: list[dict]) -> list[str]:
    lines = [pnl_line(report, prev), change_line(report, prev, applied, rejected), decision_line(opens)]
    risk = risk_line(opens)
    if risk:
        lines.append(risk)
    lines.append(research_line(report, ai_items))
    return lines


def to_mrkdwn(line: str) -> str:
    """Markdown bullet → Slack mrkdwn. Escape first so no dynamic text can form <!channel> or links."""
    text = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\[([^\]]+)\]\((https://[^)\s]+)\)", r"<\2|\1>", text)
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)

