"""Korean markdown rendering of a report dict (reports.build). Deterministic: same dict, same text."""

from __future__ import annotations

from polylab.reports.build import KIND_KO, kst

EXIT_KO = {"take_profit": "익절(TP)", "stop_loss": "손절(SL)", "time_exit": "시간청산", "resolution_win": "정산 승",
           "resolution_loss": "정산 패", "manual": "수동"}
POS_STATUS_KO = {"open": "보유", "pending": "대기", "closing": "청산중", "closed": "청산", "resolved": "정산",
                 "quarantined": "격리"}


def _n(v, nd=2, sign=False) -> str:
    if v is None:
        return "–"
    try:
        return f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def _esc(v) -> str:
    return "–" if v is None or v == "" else str(v).replace("|", "/").replace("\n", " ")


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(_esc(c) for c in r) + " |" for r in rows]
    return out


def _outcome(row: dict) -> str:
    reason = row.get("exit_reason")
    if reason:
        return EXIT_KO.get(reason, reason)
    return POS_STATUS_KO.get(row.get("position_status"), row.get("position_status") or "–")


def section_summary(r: dict) -> list[str]:
    t = r["totals"]
    lines = ["## 요약", ""]
    lines += _table(["항목", "값"], [
        ["실현손익 오늘(KST)", f"{_n(t['today'], sign=True)} USDC"],
        ["실현손익 7일 / 30일", f"{_n(t['d7'], sign=True)} / {_n(t['d30'], sign=True)} USDC"],
        ["실현손익 누적", f"{_n(t['all'], sign=True)} USDC"],
        ["기간 내 정산 포지션", f"{t['settled_24h']}건 ({_n(t['settled_pnl_24h'], sign=True)} USDC)"],
        ["보유 포지션 (미실현, 실현손익 미포함)", f"{t['open_positions']}개 ({_n(t['unrealized_pnl'], sign=True)} USDC)"],
        ["변형 live / paper", f"{t['live_variants']} / {t['paper_variants']}"],
    ])
    lines += ["", "실현손익은 CONFIRMED 체결 + 수수료 + 확인된 정산으로 청산된 live 포지션만 집계한다 "
                  "(paper·평가손익·미확정 제외).", ""]
    return lines


def section_variants(r: dict) -> list[str]:
    lines = ["## 전략 변형 현황", ""]
    rows = []
    for v in r["variants"]:
        s = v[v["primary_mode"]]
        lad = v["ladder"]
        lad_txt = "–" if lad.get("status") is None else \
            f"{lad['status']} ({_esc(lad.get('trades_at_tier'))}/{_esc(lad.get('needed'))}, ROI하한 {_n(lad.get('roi_ci_lo'), 3)})"
        rows.append([v["id"], v["mode"], f"{v['stake_usdc']:g}", v.get("account") or "–",
                     ",".join(v["sports"]), _n(s["pnl"]["today"], sign=True), _n(s["pnl"]["d7"], sign=True),
                     _n(s["pnl"]["d30"], sign=True), _n(s["pnl"]["all"], sign=True),
                     f"{s['trades']['all']} ({s['trades']['wins']}/{s['trades']['losses']})", _pct(s["win_rate"]),
                     _pct(s["roi"]), len(v["open"]), lad_txt])
    lines += _table(["변형", "모드", "단위$", "계좌", "종목", "오늘", "7일", "30일", "누적", "거래(승/패)", "승률",
                     "ROI", "보유", "ladder"], rows)
    lines += ["", "(paper 변형의 손익은 paper 원장 기준이며 실손익이 아니다.)", "", "### 파라미터", ""]
    for v in r["variants"]:
        params = ", ".join(f"{k}={val}" for k, val in sorted(v["params"].items()))
        lines.append(f"- `{v['id']}` ({v['family']}): {params or '–'}")
        excluded = {k: n for k, n in (v.get("excluded") or {}).items() if n}
        if excluded:
            lines.append(f"  - 집계 제외: {', '.join(f'{k} {n}' for k, n in excluded.items())}")
    return lines + [""]


def section_transactions(r: dict) -> list[str]:
    title = "## 지난 24시간 거래 내역" if r["kind"] == "daily" else f"## 기간 거래 요약 ({KIND_KO[r['kind']]})"
    lines = [title, "", f"기간: {kst(_ts(r['window']['tx_since']))} ~ {kst(r['now'])} KST", ""]
    by_var: dict[str, list[dict]] = {}
    for tx in r["transactions"]:
        by_var.setdefault(tx["variant_id"], []).append(tx)
    totals = r["tx_by_variant"]
    if not by_var and not totals:
        return lines + ["거래 없음.", ""]
    for vid in sorted(set(by_var) | set(totals)):
        rows = by_var.get(vid, [])
        account = rows[0]["account"] if rows else next((v["account"] for v in r["variants"] if v["id"] == vid), None)
        lines += [f"### {vid} (계좌 {account or '–'})", ""]
        if r["kind"] == "daily" and rows:
            lines += _table(["시각(KST)", "종목/리그", "경기", "토큰", "구분", "가격", "수량", "USDC", "수수료", "상태",
                             "포지션 결과", "실현손익"],
                            [[kst(x["ts"]), f"{x['sport'] or '–'}/{x['league'] or '–'}", x["game_title"], x["outcome"],
                              x["side"], _n(x["price"], 3), _n(x["shares"], 2), _n(x["usdc"], 2),
                              _n(x["fee_usdc"], 4), x["status"] if x["mode"] == "live" else f"{x['status']}(paper)",
                              _outcome(x), _n(x["realized_pnl"], sign=True)] for x in rows])
            lines.append("")
        t = totals.get(vid)
        if t:
            fees = _n(t["fees_usdc"], 4) + (f" (+미확인 {t['fees_unknown']}건)" if t["fees_unknown"] else "")
            lines.append(f"- 합계: 거래 {t['tx']}건, 매수 {_n(t['confirmed_buy_usdc'])} / 매도 {_n(t['confirmed_sell_usdc'])} "
                         f"USDC, 수수료 {fees}, 미체결 {t['unfilled']}, 격리 {t['quarantined']}, "
                         f"정산 {t['settled']}건 실현 {_n(t['realized_pnl'], sign=True)} USDC")
        lines.append("")
    all_pnl = sum(t["realized_pnl"] for t in totals.values())
    all_settled = sum(t["settled"] for t in totals.values())
    lines += [f"**전체 합계**: 거래 {len(r['transactions'])}건, 정산 {all_settled}건, 실현손익 {_n(all_pnl, sign=True)} USDC "
              "(paper 변형 포함 시 paper 원장 기준 표기)", ""]
    return lines


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    import datetime as dt
    return int(dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())


def section_open(r: dict) -> list[str]:
    lines = ["## 보유 포지션 (평가손익 = 미실현, 실현손익에 합산하지 않음)", ""]
    if not r["open_positions"]:
        return lines + ["보유 포지션 없음.", ""]
    lines += _table(["변형", "진입(KST)", "종목", "경기", "토큰", "진입가", "수량", "원가", "현재가", "미실현", "경기분", "상태"],
                    [[o["variant_id"], kst(_ts(o["opened_at"])), o["sport"], o["title"], o["outcome"],
                      _n(o["entry_price"], 3), _n(o["shares"]), _n(o["cost_usdc"]), _n(o["mark_price"], 3),
                      _n(o["unrealized_pnl"], sign=True), _n(o["game_minute"], 0), POS_STATUS_KO.get(o["status"], o["status"])]
                     for o in r["open_positions"]])
    return lines + [""]


def section_health(r: dict) -> list[str]:
    h = r["health"]
    c = h.get("collector") or {}
    now = r["now"]

    def age(ts):
        return "–" if not ts else f"{(now - ts) / 60:.0f}분 전"
    lines = ["## 데이터 수집 상태", ""]
    if h.get("storage_error"):
        return lines + [f"- 저장소 오류: {h['storage_error']}", ""]
    bp = c.get("backfill_progress") or {}
    lines += [f"- 마지막 poll: {age(c.get('last_poll_at'))}, WS: {age(c.get('ws_last_message_at'))}, "
              f"game state: {age(c.get('last_game_state_at'))}",
              f"- 라이브 경기 {_esc(c.get('live_games'))}개, 추적 마켓 {_esc(c.get('tracked_markets'))}개, "
              f"백필 {_esc(bp.get('games_done'))}/{_esc(bp.get('games_total'))} 경기",
              f"- 디스크 여유 {_esc(h.get('disk_free_gb'))}GB, core.db {_esc(h.get('core_db_mb'))}MB, "
              f"books {_esc(h.get('books_db_mb'))}MB, 전략 DB {_esc(h.get('strategies_db_mb'))}MB"]
    q = c.get("quality_24h") or {}
    lines.append("- 품질 이벤트(24h): " + (", ".join(f"{k} {n}" for k, n in q.items()) if q else "없음"))
    jobs = h.get("jobs") or []
    if jobs:
        lines += ["", *_table(["잡", "상태", "마지막 실행", "마지막 성공", "연속 실패"],
                              [[j["name"], j["status"], kst(_ts(j["last_run_at"])), kst(_ts(j["last_ok_at"])),
                                j.get("fail_streak", 0)] for j in jobs])]
    return lines + [""]


def section_research(r: dict) -> list[str]:
    res = r["research"]
    lines = ["## 연구 하이라이트", ""]
    if res["calibration_top"]:
        lines += [f"calibration (생성 {res['calibration_generated_at']}): gap = 실제승률 − 평균가격, 양수 = 과소평가", ""]
        lines += _table(["종목", "구간", "가격대", "n", "평균가", "승률", "95% CI", "gap", "유의"],
                        [[b["sport"], b["phase"], f"{b['p_lo']:.2f}–{b['p_hi']:.2f}", b["n"], _n(b["mean_price"], 3),
                          _n(b["win_rate"], 3), f"{_n(b['ci_lo'], 3)}–{_n(b['ci_hi'], 3)}", _n(b["gap"], 3, True),
                          "예" if b["significant"] else "아니오"] for b in res["calibration_top"]])
        lines.append("")
    else:
        lines += ["calibration 결과 없음(표본 부족 또는 `polylab analyze calibration` 미실행).", ""]
    if res["event_top"]:
        lines += [f"득점 이벤트 민감도 (측정 {res.get('events_measured')}건): 경기 시간대별 가격 점프", ""]
        lines += _table(["종목", "이벤트", "경기분", "n", "|점프| 평균", "점프 중앙값", "되돌림 5분", "되돌림 10분"],
                        [[e["sport"], e["event"], e["minute_bucket"], e["n"], _n(e["mean_abs_jump"], 3),
                          _n(e["median_jump"], 3, True), _n(e["reversion_5m"], 3, True), _n(e["reversion_10m"], 3, True)]
                         for e in res["event_top"]])
        lines.append("")
    tiers = r.get("stake_tiers") or []
    if tiers:
        lines += ["stake 단위별 안정성 (live 정산분)", ""]
        lines += _table(["단위$", "변형", "거래", "손익", "ROI", "손익σ", "MDD", "sharpe-like"],
                        [[f"{t['tier_usdc']:g}", t["variants"], t["trades"], _n(t["pnl"], sign=True), _pct(t["roi"]),
                          _n(t["pnl_std"]), _n(t["max_drawdown"]), _n(t["sharpe_like"], 3)] for t in tiers])
        lines.append("")
    return lines


def section_changes(r: dict) -> list[str]:
    lines = ["## 변경 사항 (파라미터·stake)", ""]
    if not r["changes"]:
        return lines + ["기간 내 변경 없음.", ""]
    lines += [f"- {kst(_ts(c['at']))} {c['summary']}" + (f" — {c['rationale']}" if c.get("rationale") else "")
              for c in r["changes"]]
    return lines + [""]


def section_alerts(r: dict) -> list[str]:
    if not r["alerts"]:
        return []
    return ["## 경고", "", *[f"- [{a['level']}] {a['message']}" for a in r["alerts"]], ""]


def render(r: dict, narrative: str | None = None, applied: list[dict] | None = None,
           rejected: list[dict] | None = None) -> str:
    lines = [f"# {r['title']}", "",
             f"- 생성: {kst(r['now'], '%Y-%m-%d %H:%M')} KST · commit `{r.get('git_commit') or '–'}` · "
             f"기간 {kst(_ts(r['window']['since']))} ~ {kst(_ts(r['window']['until']))} KST",
             f"- 대시보드: https://poly.zowoo.uk", ""]
    for section in (section_summary, section_variants, section_transactions, section_open, section_changes,
                    section_alerts, section_health, section_research):
        lines += section(r)
    ai = r.get("ai") or {}
    lines += ["## AI 회고", ""]
    if narrative:
        lines += [narrative.strip(), ""]
    elif ai.get("reason"):
        lines += [f"AI 회고 생략({ai['reason']}).", ""]
    else:
        lines += ["AI 회고 없음 (결정론 리포트).", ""]
    if applied is not None or rejected is not None:
        lines += ["## 자동 적용 결과", ""]
        for a in applied or []:
            lines.append(f"- 적용: `{a['variant_id']}` {a['change']} {a.get('summary', '')} ({a.get('source', 'ai')})")
        for x in rejected or []:
            lines.append(f"- 거부: `{x.get('variant_id')}` {x.get('change')} — {x.get('reason')} ({x.get('source', 'ai')})")
        if not applied and not rejected:
            lines.append("- 제안 없음")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
