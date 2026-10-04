"""Attention inbox: what the (non-coding) thesis owner should know or decide, kept across retros.

State: reports/attention.json (committed, public: aliases only, scrubbed) rendered as reports/attention.md.
Items come from two sources:
- rule: deterministic checks over the report, health and ledgers (never depend on AI). Each item belongs to a
  rule family; when a run evaluates a family and no longer emits an item, the item auto-resolves.
- ai: at most AI_MAX_ITEMS per run from the engine's attention.json, validated and scrubbed here. AI items can
  never touch rule ids (namespaced `ai:`), cannot be critical and expire AI_TTL_S after they were last emitted.
Resolved items stay RESOLVED_KEEP_S (the collapsed "최근 해결" section), then are pruned.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from polylab.analysis import _common as C
from polylab.reports.brief import ATTENTION_URL
from polylab.reports.slack import scrub

SCHEMA = "polylab.attention/v1"
SEVERITIES = ("critical", "warn", "decide", "info")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEVERITY_KO = {"critical": "긴급", "warn": "경고", "decide": "결정 필요", "info": "참고"}
CATEGORIES = ("decision_needed", "risk", "data_quality", "research_finding", "system_change", "question")
CATEGORY_KO = {"decision_needed": "결정", "risk": "위험", "data_quality": "데이터 품질",
               "research_finding": "연구 발견", "system_change": "시스템 변경", "question": "질문"}
AI_CATEGORIES = tuple(c for c in CATEGORIES if c != "system_change")  # system changes are facts, not opinions
AI_MAX_ITEMS = 3
AI_TTL_S = 7 * 86400
RESOLVED_KEEP_S = 14 * 86400
TITLE_MAX, DETAIL_MAX, REF_MAX, REFS_MAX = 100, 500, 120, 3
THESIS_MIN_N, THESIS_MAX, THESIS_TEXT_MAX = 30, 3, 400

STAKE_LOOKBACK_S = 3 * 86400
DEAD_DAYS = 3
DISK_WARN_GB = 100.0
DISK_CRIT_GB = 20.0
QUALITY_INFO, QUALITY_WARN = 30, 300
BACKFILL_WARN = 50
PAPER_MIN_TRADES = 20
QUALITY_KO = {"live_gap": "라이브 경기 중 1분 가격 bar 공백", "poll_gap": "poll 수집 3분 이상 공백",
              "history_gap": "history 가격 공백", "live_history_mismatch": "라이브 가격과 history 가격 5c 이상 불일치",
              "crossed_book": "호가 역전(bid>ask)", "crossed_book_ws": "WS 호가 역전(bid>ask)",
              "discover_failed": "경기 탐색 실패"}
KIND_KO = {"daily": "일일", "weekly": "주간", "monthly": "월간"}
# Paper-only research experiments: they trade only when their niche markets exist and may never go live.
RESEARCH_ONLY = {"llm-nil-draw"}
MOVE_NOTE = "증액은 결정론 ladder 게이트를 통과했을 때만, 감액·paper 전환은 손실이나 표본 규칙으로 자동 적용된다."


# ------------------------------------------------------------------ helpers

def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def _kst(ts: int | None, fmt: str = "%m-%d %H:%M") -> str:
    return "–" if ts is None else dt.datetime.fromtimestamp(int(ts), C.KST).strftime(fmt)


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def _usd(v) -> str:
    return "–" if not isinstance(v, (int, float)) else f"{v:g}"


def clean_text(value, limit: int) -> str:
    """One line, no HTML/markdown structure (<, >, leading #), scrubbed, capped."""
    text = scrub(str(value))
    text = text.replace("<", "‹").replace(">", "›")
    text = re.sub(r"\s+", " ", text).strip().lstrip("#").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def item(id: str, rule: str, severity: str, category: str, title: str, detail: str = "",
         evidence_ref: str = "") -> dict:
    return {"id": id, "rule": rule, "severity": severity, "category": category, "title": title, "detail": detail,
            "evidence_ref": evidence_ref, "source": "rule"}


def empty_state() -> dict:
    return {"schema": SCHEMA, "generated_at": None, "url": ATTENTION_URL, "last_retro": None, "items": []}


def load(reports_dir: Path) -> dict:
    try:
        data = json.loads((reports_dir / "attention.json").read_text())
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return empty_state()
    return {**empty_state(), **data, "items": [i for i in data["items"] if isinstance(i, dict) and i.get("id")]}


def open_items(state: dict) -> list[dict]:
    """Most severe first, newest first within a severity."""
    items = [i for i in state["items"] if i.get("status") == "open"]
    items.sort(key=lambda i: i.get("updated_at") or "", reverse=True)
    return sorted(items, key=lambda i: SEVERITY_RANK.get(i.get("severity"), 9))


def resolved_items(state: dict) -> list[dict]:
    items = [i for i in state["items"] if i.get("status") == "resolved"]
    return sorted(items, key=lambda i: i.get("resolved_at") or "", reverse=True)


# ------------------------------------------------------------------ deterministic rules

def _families(kind: str, ai_enabled: bool) -> set[str]:
    fams = {"stake", "health", "disk", "quality", "backfill", "dead_variant", "paper", f"rejected:{kind}", "manual"}
    if ai_enabled:
        fams.add("ai_engine")
    if kind == "weekly":
        fams.add("params_7d")
    return fams


def _stake_items(report: dict, applied: list[dict], now: int) -> list[dict]:
    out = []
    for v in report["variants"]:
        for ev in v.get("stake_events") or []:
            if ev.get("from_usdc") is None or not ev.get("ts") or now - ev["ts"] > STAKE_LOOKBACK_S:
                continue  # init rows and old moves
            f, t, fm, tm = ev.get("from_usdc"), ev.get("to_usdc"), ev.get("from_mode"), ev.get("to_mode")
            up = (t or 0) > (f or 0) and fm == tm
            mode = f", 모드 {fm}→{tm}" if fm != tm else ""
            verb = "증액" if up else ("감액" if (t or 0) < (f or 0) else "모드 변경")
            out.append(item(f"stake:{v['id']}:{ev['ts']}", "stake", "info" if up else "warn", "system_change",
                            f"{v['id']} 단위 {verb}: {_usd(f)}→{_usd(t)} USDC{mode}",
                            f"{_kst(ev['ts'])} KST 자동 적용. 사유: {clean_text(ev.get('reason') or '–', 200)}. {MOVE_NOTE}",
                            f"strategies/{v['id']}.yaml"))
    seen = {i["id"] for i in out}
    for a in applied:  # this run's moves (their stake_events rows carry ts=now, so ids match next run)
        if a.get("change") not in ("stake", "mode", "retire") or f"stake:{a['variant_id']}:{now}" in seen:
            continue
        m = re.search(r"stake ([\d.]+)→([\d.]+)", a.get("summary") or "")
        up = a["change"] == "stake" and m is not None and float(m.group(2)) > float(m.group(1))
        out.append(item(f"stake:{a['variant_id']}:{now}", "stake", "info" if up else "warn", "system_change",
                        f"{a['variant_id']} 자동 변경: {a.get('summary')}",
                        f"{_kst(now)} KST 적용({a.get('source')}). 사유: {clean_text(a.get('rationale') or '–', 200)}. "
                        + MOVE_NOTE, f"strategies/{a['variant_id']}.yaml"))
    return out


def _health_items(report: dict, report_ref: str) -> list[dict]:
    from polylab.ops import health as health_mod  # noqa: PLC0415
    out = []
    for p in health_mod.checks(report["health"]):
        key = p["key"]
        if key in ("disk", "retro_failed"):  # disk has its own threshold here; AI status comes from this run
            continue
        if key == "kill_switch":
            out.append(item("kill_switch", "health", "critical", "risk", "킬스위치 활성: 모든 신규 진입 중단",
                            "state/KILL 파일(또는 POLYLAB_KILL)이 켜져 있다. 청산·대사는 계속된다. 의도한 중지가 아니면 "
                            "Mac mini 에서 파일을 지워야 거래가 재개된다.", report_ref))
        elif key.startswith("loss_stop:"):
            vid = key.split(":", 1)[1]
            out.append(item(f"loss_stop:{vid}", "health", "warn", "risk", f"{vid} 일일 손실 한도 도달",
                            f"{p['message']}. 오늘(UTC) 남은 시간 동안 이 변형은 신규 진입하지 않는다.", report_ref))
        else:
            sev = "critical" if p["level"] == "critical" else "warn"
            out.append(item(f"health:{key}", "health", sev, "data_quality", clean_text(p["message"], TITLE_MAX),
                            "수집·잡 상태 점검(polylab health) 결과. 공백 구간은 연구 표본과 전략 진입에서 빠진다.",
                            report_ref))
    return out


def _disk_items(report: dict, report_ref: str) -> list[dict]:
    free = (report.get("health") or {}).get("disk_free_gb")
    if free is None or free >= DISK_WARN_GB:
        return []
    sev = "critical" if free < DISK_CRIT_GB else "warn"
    return [item("disk", "disk", sev, "risk", f"외장 디스크 여유 {free:g}GB (< {DISK_WARN_GB:g}GB)",
                 "가격·호가 수집이 디스크를 계속 쓴다. 20GB 아래로 내려가면 수집이 멈출 수 있으니 정리나 증설이 필요하다.",
                 report_ref)]


def _quality_items(report: dict, report_ref: str) -> list[dict]:
    coll = (report.get("health") or {}).get("collector") or {}
    out = []
    for kind, n in (coll.get("quality_24h") or {}).items():
        if n < QUALITY_INFO:
            continue
        out.append(item(f"quality:{kind}", "quality", "warn" if n >= QUALITY_WARN else "info", "data_quality",
                        f"데이터 품질 이벤트 {kind} {n}건 (24시간)",
                        f"{QUALITY_KO.get(kind, kind)}. 해당 구간은 연구 표본에서 빠지며 양끝 가격으로 보간하지 않는다. "
                        f"{QUALITY_WARN}건 이상이면 경고로 올린다.", report_ref))
    failed = coll.get("backfill_failed_24h") or 0
    if failed:
        out.append(item("backfill_failed", "backfill", "warn" if failed >= BACKFILL_WARN else "info", "data_quality",
                        f"백필 실패 {failed}건 (24시간)",
                        "과거 가격·거래 백필 일부가 실패했다. 다음 백필 잡이 재시도하며, 실패가 계속되면 해당 경기는 연구 표본에서 빠진다.",
                        report_ref))
    return out


def _last_entry(paths, vid: str) -> int | None:
    conn = C.open_ro(paths.strategy_db(vid))
    if conn is None:
        return None
    try:
        return conn.execute("SELECT MAX(opened_at) FROM positions WHERE status != 'pending'").fetchone()[0]
    except Exception:
        return None
    finally:
        conn.close()


def idle_variants(report: dict, paths, now: int) -> dict[str, tuple[int | None, int]]:
    """{variant_id: (last_entry_ts, target_games)} for variants with 0 entries for DEAD_DAYS although
    games of their sports (and soccer leagues) were played. Also gates the daily backtest-backed retune."""
    if (report.get("health") or {}).get("kill_switch") or paths is None:
        return {}  # with the kill switch on every variant legitimately has 0 entries
    since = now - DEAD_DAYS * 86400
    core = C.open_ro(paths.core_db)
    if core is None:
        return {}
    out: dict[str, tuple[int | None, int]] = {}
    try:
        for v in report["variants"]:
            history = v.get("param_history") or []
            if v.get("id") in RESEARCH_ONLY or v["mode"] == "off" or not history or (history[0].get("ts") or now) > since:
                continue  # off, or younger than DEAD_DAYS
            last_entry = _last_entry(paths, v["id"])
            if last_entry is not None and last_entry >= since:
                continue
            leagues = [str(x).lower() for x in ((v.get("params") or {}).get("leagues") or [])]
            games = 0
            for sport in v.get("sports") or []:
                sql = "SELECT COUNT(*) FROM games WHERE sport=? AND start_time BETWEEN ? AND ?"
                args: list = [sport, since, now]
                if leagues and sport == "soccer":
                    sql += f" AND LOWER(league) IN ({','.join('?' * len(leagues))})"
                    args += leagues
                games += core.execute(sql, args).fetchone()[0]
            if games:
                out[v["id"]] = (last_entry, games)
    finally:
        core.close()
    return out


def _dead_variant_items(report: dict, paths, now: int) -> list[dict]:
    out = []
    sports = {v["id"]: v.get("sports") or [] for v in report["variants"]}
    for vid, (last_entry, games) in idle_variants(report, paths, now).items():
        last = f"마지막 진입 {_kst(last_entry)} KST" if last_entry else "진입 기록 없음"
        out.append(item(f"dead_variant:{vid}", "dead_variant", "decide", "decision_needed",
                        f"{vid} {DEAD_DAYS}일 이상 진입 0건 (대상 경기 {games}개 있었음)",
                        f"{last}. 같은 기간 대상 종목({', '.join(sports.get(vid, []))}) 경기는 {games}개였다. 진입 조건이 "
                        "지나치게 엄격하거나 버그일 수 있다. AI 회고가 백테스트 근거로 조건을 다시 맞추거나(retro 가 직접 "
                        "재생해 검증), 폐기(retire) 여부를 판단해야 한다.",
                        f"strategies/{vid}.yaml"))
    return out


def _paper_items(report: dict, applied: list[dict]) -> list[dict]:
    out = []
    for v in report["variants"]:
        if v["mode"] != "paper" or v.get("id") in RESEARCH_ONLY:
            continue
        p = v.get("paper") or {}
        n = (p.get("trades") or {}).get("all", 0)
        if n < PAPER_MIN_TRADES:
            out.append(item(f"paper:{v['id']}", "paper", "info", "system_change",
                            f"paper 변형 {v['id']} 증거 수집 중 ({n}/{PAPER_MIN_TRADES}건)",
                            f"가설: {clean_text(v.get('hypothesis') or '–', 200)}. paper 정산 {n}건, ROI {_pct(p.get('roi'))}"
                            f"(paper 원장, 실손익 아님). {PAPER_MIN_TRADES}건이 모이면 live 전환 여부를 사람이 결정한다"
                            "(AI는 live로 올릴 수 없다).", f"strategies/{v['id']}.yaml"))
        else:
            out.append(item(f"paper_ready:{v['id']}", "paper", "decide", "decision_needed",
                            f"paper 변형 {v['id']} 표본 {n}건 도달: live 전환 결정 필요",
                            f"paper 정산 {n}건, 승률 {_pct(p.get('win_rate'))}, ROI {_pct(p.get('roi'))}(paper 원장). "
                            f"live 전환은 사람만 할 수 있다: strategies/{v['id']}.yaml 의 mode 를 live 로, 계좌 alias 를 "
                            "지정해 커밋한다. 아니면 그대로 두거나 retire 한다.", f"strategies/{v['id']}.yaml"))
    for a in applied:
        if a.get("change") == "new_variant":
            vid = a["variant_id"]
            out.append(item(f"paper:{vid}", "paper", "info", "system_change",
                            f"새 paper 변형 {vid} 생성: 증거 수집 시작 (0/{PAPER_MIN_TRADES}건)",
                            f"{clean_text(a.get('summary') or '', 200)}. 근거: {clean_text(a.get('rationale') or '–', 200)}",
                            f"strategies/{vid}.yaml"))
    return out


def _rejected_items(kind: str, rejected: list[dict], report_ref: str) -> list[dict]:
    rows = [r for r in rejected if "not applied (--no-apply)" not in str(r.get("reason"))]
    if not rows:
        return []
    bad = any(k in str(r.get("reason")) for r in rows for k in ("reverted", "apply failed"))
    lines = [f"`{clean_text(r.get('variant_id'), 64)}` {clean_text(r.get('change'), 20)}: "
             f"{clean_text(r.get('reason') or '–', 120)} ({clean_text(r.get('source'), 60)})" for r in rows[:5]]
    more = f" 외 {len(rows) - 5}건." if len(rows) > 5 else ""
    return [item(f"rejected:{kind}", f"rejected:{kind}", "warn" if bad else "info", "system_change",
                 f"{KIND_KO[kind]} 회고 제안 {len(rows)}건 거부됨 (validator)",
                 "안전 규칙(표본·cooldown·bounds·ladder)에 걸린 제안은 적용하지 않는다: " + "; ".join(lines) + more,
                 report_ref)]


def _ai_engine_items(ai: dict, report_ref: str) -> list[dict]:
    tried = ai.get("tried") or []
    why = "; ".join(f"{t['engine']}: {t['reason']}" for t in tried if not t.get("ok"))
    if ai.get("ran"):
        if tried and not tried[0].get("ok"):
            return [item("ai_fallback", "ai_engine", "info", "system_change",
                         f"AI 회고 엔진 대체: {tried[0]['engine']} 실패 → {ai.get('engine')} 사용",
                         f"1순위 엔진 실패 사유: {clean_text(why or '–', 300)}. 회고는 정상 완료됐다.", report_ref)]
        return []
    if ai.get("failed"):
        return [item("ai_failed", "ai_engine", "warn", "risk", "AI 회고 실패: 결정론 리포트만 생성",
                     f"모든 엔진 실패({clean_text(why or ai.get('reason') or '–', 300)}). 단위 ladder·검증·리포트는 계속 "
                     "동작하지만 파라미터 개선 제안과 서술 회고가 빠진다. 사용량 한도면 다음 회차에 자동 재시도된다.", report_ref)]
    return [item("ai_unavailable", "ai_engine", "warn", "risk",
                 f"AI 회고 생략: {clean_text(ai.get('reason') or '엔진 없음', 60)}",
                 "AI 엔진을 쓸 수 없어 결정론 리포트만 만들었다(토큰 또는 CLI 없음). Mac mini 의 claude 토큰·codex 로그인을 "
                 "확인해야 한다.", report_ref)]


def _params_7d_items(report: dict, report_ref: str) -> list[dict]:
    rows = [c for c in report.get("changes") or [] if c.get("type") == "params" and "초기 버전" not in c["summary"]]
    if not rows:
        return []
    lines = [f"{_kst(_ts(c['at']))} {clean_text(c['summary'], 160)}" for c in rows[:10]]
    more = f" 외 {len(rows) - 10}건." if len(rows) > 10 else ""
    return [item("params_7d", "params_7d", "info", "system_change", f"지난 7일 파라미터 변경 {len(rows)}건",
                 "; ".join(lines) + more, report_ref)]


def _manual_items(report: dict, report_ref: str) -> list[dict]:
    """Track 2 manual bets: resolved losses (3-day lookback) and the -10% bankroll drawdown (manual/report.py)."""
    if (report.get("manual") or {}).get("error"):
        return []
    from polylab.manual.report import attention_items  # noqa: PLC0415
    return [{**i, "title": clean_text(i["title"], TITLE_MAX), "detail": clean_text(i["detail"], DETAIL_MAX)}
            for i in attention_items(report, report_ref)]


def rule_items(report: dict, *, kind: str, now: int, paths=None, applied=(), rejected=(), ai: dict | None = None,
               ai_enabled: bool = True) -> tuple[list[dict], set[str]]:
    """(items, evaluated families). A family missing from the set keeps its items untouched this run."""
    ref = f"reports/{kind}/{report['name']}.md"
    items = _stake_items(report, list(applied), now)
    if (report.get("health") or {}).get("storage_error"):
        return items + _health_items(report, ref), {"stake", "health"}
    items += _health_items(report, ref) + _disk_items(report, ref) + _quality_items(report, ref)
    items += _dead_variant_items(report, paths, now) + _paper_items(report, list(applied))
    items += _rejected_items(kind, list(rejected), ref)
    items += _manual_items(report, ref)
    if ai_enabled and ai is not None:
        items += _ai_engine_items(ai, ref)
    if kind == "weekly":
        items += _params_7d_items(report, ref)
    families = _families(kind, ai_enabled and ai is not None)
    if (report.get("manual") or {}).get("error"):
        families.discard("manual")          # a failed read must not auto-resolve open manual items
    return items, families


# ------------------------------------------------------------------ AI items

def read_ai(cwd: Path):
    try:
        return json.loads((cwd / "attention.json").read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _refs(value) -> str:
    refs = value if isinstance(value, list) else [value]
    out = [clean_text(r, REF_MAX) for r in refs if isinstance(r, str) and r.strip()]
    return ", ".join(out[:REFS_MAX])


def _slug(value, title: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(value or "").lower()).strip("-")[:40]
    return s or hashlib.sha1(title.encode()).hexdigest()[:10]


def sanitize_ai(raw, kind: str) -> tuple[list[dict], list[dict], list[str]]:
    """(items, thesis sentences, notes). Invalid or missing input yields nothing, never an exception."""
    notes: list[str] = []
    if not isinstance(raw, dict):
        return [], [], (["attention.json 형식 오류"] if raw is not None else [])
    items, ids = [], set()
    for x in raw.get("items") if isinstance(raw.get("items"), list) else []:
        if len(items) >= AI_MAX_ITEMS:
            notes.append(f"AI 항목 {AI_MAX_ITEMS}개 초과분 버림")
            break
        if not isinstance(x, dict):
            continue
        title = clean_text(x.get("title") or "", TITLE_MAX)
        refs = _refs(x.get("evidence_ref"))
        category = x.get("category")
        if not title or not refs or category not in AI_CATEGORIES:
            notes.append("AI 항목 거부: " + ("제목 없음" if not title else ("근거 없음" if not refs else f"분류 {category!r}")))
            continue
        severity = x.get("severity") if x.get("severity") in SEVERITIES else "info"
        if severity == "critical":
            severity = "warn"
        iid = f"ai:{_slug(x.get('id'), title)}"
        if iid in ids:
            continue
        ids.add(iid)
        items.append({"id": iid, "rule": "ai", "severity": severity, "category": category, "title": title,
                      "detail": clean_text(x.get("detail") or "", DETAIL_MAX), "evidence_ref": refs, "source": "ai"})
    thesis: list[dict] = []
    if kind in ("weekly", "monthly"):
        for t in raw.get("thesis_sentences") if isinstance(raw.get("thesis_sentences"), list) else []:
            if len(thesis) >= THESIS_MAX or not isinstance(t, dict):
                continue
            n, text, refs = t.get("n"), clean_text(t.get("text") or "", THESIS_TEXT_MAX), _refs(t.get("evidence_ref"))
            if not isinstance(n, int) or isinstance(n, bool) or n < THESIS_MIN_N or not text or not refs:
                notes.append(f"논문 문장 거부(n < {THESIS_MIN_N} 또는 근거 없음)")
                continue
            thesis.append({"text": text, "n": n, "evidence_ref": refs})
    return items, thesis, notes


# ------------------------------------------------------------------ merge

def merge(state: dict, emitted: list[dict], families: set[str], now: int) -> dict:
    """Dedupe by id, open/refresh emitted items, auto-resolve cleared rule items (only for evaluated families)
    and expired AI items, prune old resolved items. Pure: returns a new state."""
    iso = C.iso(now)
    by_id = {i["id"]: dict(i) for i in state.get("items", [])}
    emitted_ids = set()
    for e in emitted:
        if e["id"] in emitted_ids:
            continue
        emitted_ids.add(e["id"])
        cur = by_id.get(e["id"])
        content = {k: e[k] for k in ("rule", "severity", "category", "title", "detail", "evidence_ref", "source")}
        if e["source"] == "ai":
            content["expires_at"] = C.iso(now + AI_TTL_S)
        if cur is None or cur.get("status") != "open":
            by_id[e["id"]] = {"id": e["id"], "created_at": iso, "updated_at": iso, **content, "status": "open",
                              "resolved_at": None, "resolution": None}
        else:
            changed = any(cur.get(k) != v for k, v in content.items() if k != "expires_at")
            by_id[e["id"]] = {**cur, **content, "updated_at": iso if changed else cur.get("updated_at")}
    for iid, cur in by_id.items():
        if cur.get("status") != "open" or iid in emitted_ids:
            continue
        if cur.get("source") == "ai":
            exp = _ts(cur.get("expires_at"))
            if exp is not None and exp <= now:
                by_id[iid] = {**cur, "status": "resolved", "resolved_at": iso,
                              "resolution": f"자동 만료 (AI 항목, {AI_TTL_S // 86400}일 동안 재확인 없음)"}
        elif cur.get("rule") in families:
            by_id[iid] = {**cur, "status": "resolved", "resolved_at": iso, "resolution": "조건 해소 (자동)"}
    keep = [i for i in by_id.values()
            if i.get("status") == "open" or (_ts(i.get("resolved_at")) or 0) > now - RESOLVED_KEEP_S]
    return {**state, "schema": SCHEMA, "url": ATTENTION_URL, "generated_at": iso,
            "items": open_items({"items": keep}) + resolved_items({"items": keep})}


# ------------------------------------------------------------------ owner decisions

DECISIONS_FILE = "decisions.md"
_DECISION_HEAD = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
_DECISION_LINE = re.compile(r"^-\s+`([^`]+)`\s+[—-]+\s+(.+?)\s*$")


def load_decisions(reports_dir: Path) -> dict[str, dict]:
    """Owner answers to attention items, from reports/decisions.md (editable on GitHub):

        ## 2026-10-02
        - `ai:apricot-fruit-tick-inferior` — 더 나은 파라미터가 있으면 변경

    Later entries for the same id win. Unknown/garbled lines are ignored."""
    path = reports_dir / DECISIONS_FILE
    out: dict[str, dict] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    date = None
    for line in lines:
        if m := _DECISION_HEAD.match(line.strip()):
            date = m.group(1)
        elif (m := _DECISION_LINE.match(line.strip())) and date:
            out[m.group(1)] = {"date": date, "decision": clean_text(m.group(2), 400)}
    return out


def apply_decisions(state: dict, decisions: dict[str, dict], now: int) -> dict:
    """An item the owner has answered stays resolved, even if a rule or the AI emits it again."""
    if not decisions:
        return state
    iso = C.iso(now)
    items = []
    for i in state.get("items", []):
        d = decisions.get(i["id"])
        if d and i.get("status") == "open":
            i = {**i, "status": "resolved", "resolved_at": iso,
                 "resolution": f"사용자 결정 ({d['date']}): {d['decision']}"}
        items.append(i)
    return {**state, "items": open_items({"items": items}) + resolved_items({"items": items})}


def update(state: dict, report: dict, *, kind: str, now: int, paths=None, applied=(), rejected=(),
           ai: dict | None = None, ai_enabled: bool = True, ai_raw=None,
           decisions: dict | None = None) -> tuple[dict, dict]:
    """Returns (new state, run info {ai_items, thesis, notes})."""
    items, fams = rule_items(report, kind=kind, now=now, paths=paths, applied=applied, rejected=rejected, ai=ai,
                             ai_enabled=ai_enabled)
    ai_items, thesis, notes = sanitize_ai(ai_raw, kind)
    new = apply_decisions(merge(state, items + ai_items, fams, now), decisions or {}, now)
    new["last_retro"] = {"kind": kind, "name": report["name"], "at": C.iso(now),
                         "live_pnl_all": (report.get("totals") or {}).get("all")}
    return new, {"ai_items": ai_items, "thesis": thesis, "notes": notes}


# ------------------------------------------------------------------ output

def _md_item(i: dict) -> list[str]:
    src = "AI 판단" if i.get("source") == "ai" else "자동 규칙"
    lines = [f"### [{SEVERITY_KO.get(i['severity'], i['severity'])}] {i['title']}", "",
             f"- {CATEGORY_KO.get(i['category'], i['category'])} · {src} · 최초 {_kst(_ts(i.get('created_at')))} · "
             f"갱신 {_kst(_ts(i.get('updated_at')))} KST · id `{i['id']}`"]
    if i.get("evidence_ref"):
        lines.append(f"- 근거: `{i['evidence_ref']}`")
    if i.get("detail"):
        lines += ["", i["detail"]]
    return lines + [""]


def render_md(state: dict) -> str:
    opens, resolved = open_items(state), resolved_items(state)
    counts = {s: sum(1 for i in opens if i["severity"] == s) for s in SEVERITIES}
    lines = ["# polylab 확인·결정 목록 (attention inbox)", "",
             f"갱신 {_kst(_ts(state.get('generated_at')), '%Y-%m-%d %H:%M')} KST · 열린 항목 {len(opens)}건 "
             f"(긴급 {counts['critical']} · 경고 {counts['warn']} · 결정 필요 {counts['decide']} · 참고 {counts['info']})", "",
             "매 회고(일일 3회·주간·월간)가 자동으로 갱신한다. **자동 규칙** 항목은 조건이 풀리면 스스로 '최근 해결'로 옮겨지고, "
             f"**AI 판단** 항목은 {AI_TTL_S // 86400}일 동안 다시 나오지 않으면 만료된다. 근거 경로는 이 저장소 기준이며, "
             "`metrics/…` 같은 경로는 AI context pack(공개 사본 `reports/context/latest/`)을 가리킨다.", "",
             "**답하는 법**: 항목 id 와 결정을 [`reports/decisions.md`](decisions.md) 에 한 줄로 적거나(GitHub 웹 편집 가능) "
             "Claude 에게 말하면 기록된다. 다음 회고가 그 항목을 '사용자 결정'으로 닫고, AI 는 결정을 전제로 판단한다.", "",
             "## 열린 항목", ""]
    if not opens:
        lines += ["지금 확인하거나 결정할 항목이 없다.", ""]
    for i in opens:
        lines += _md_item(i)
    if resolved:
        lines += ["## 최근 해결", "", "<details>",
                  f"<summary>최근 {RESOLVED_KEEP_S // 86400}일 해결 {len(resolved)}건</summary>", ""]
        lines += [f"- **{i['title']}** — {i.get('resolution') or '해결'} ({_kst(_ts(i.get('resolved_at')))} KST)"
                  for i in resolved]
        lines += ["", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def save(reports_dir: Path, state: dict) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "attention.json").write_text(scrub(json.dumps(state, ensure_ascii=False, indent=1)))
    (reports_dir / "attention.md").write_text(scrub(render_md(state)))


def write_context(cwd: Path, state: dict, reports_dir: Path | None = None) -> None:
    """Currently open items for the AI (so it does not repeat them), the owner's decisions + MANIFEST lines."""
    public = [{k: i.get(k) for k in ("id", "severity", "category", "title", "detail", "evidence_ref", "source")}
              for i in open_items(state)]
    (cwd / "attention_open.json").write_text(scrub(json.dumps(public, ensure_ascii=False, indent=1)))
    manifest = cwd / "MANIFEST.md"
    if manifest.exists():
        manifest.write_text(manifest.read_text() + "- attention_open.json (이미 열린 확인·결정 항목, 같은 내용을 다시 쓰지 않는다)\n")
    src = (reports_dir / DECISIONS_FILE) if reports_dir else None
    if src and src.exists():
        (cwd / DECISIONS_FILE).write_text(scrub(src.read_text()))
        if manifest.exists():
            manifest.write_text(manifest.read_text() + "- decisions.md (연구자 결정 기록 — 이미 답한 질문은 다시 묻지 않고, 결정을 전제로 판단한다)\n")
