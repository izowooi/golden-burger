"""Korean markdown rendering of a report dict (reports.build). Deterministic: same dict, same text."""

from __future__ import annotations

from polylab.reports import games as games_mod
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


SPORT_KO = {"soccer": "축구", "mlb": "MLB", "nba": "NBA", "nfl": "NFL", "nhl": "NHL"}
SIDE_KO = {"home": "홈", "draw": "무", "away": "원정"}
RESULT_KO = {"home": "홈 승", "away": "원정 승", "draw": "무승부"}


def _p(v) -> str:
    return "–" if v is None else f"{v:.2f}"


def _swing(s: dict | None, sport: str) -> str:
    if not s:
        return "–"
    return f"{s['delta']:+.2f} ({kst(_ts(s['at']), '%H:%M')}, {games_mod.when(s, sport)})"


def _game_result(g: dict) -> str:
    score = "–" if g["home_score"] is None or g["away_score"] is None else f"{g['home_score']}–{g['away_score']}"
    if g["result"]:
        res = RESULT_KO[g["result"]]
    else:
        res = "진행중" if g["end_source"] == "running" else "미정산"
    return f"{score} {res}"


def _flags(g: dict) -> str:
    out = []
    if g["upset"]:
        out.append("업셋")
    if g["notable_swing"]:
        out.append(f"급변≥{games_mod.NOTABLE_SWING:.2f}")
    if g["traded"]:
        out.append("전략거래")
    return ", ".join(out)


def section_games(r: dict) -> list[str]:
    gm = r.get("games")
    if gm is None:
        return []
    w = gm["window"]
    lines = ["## 지난 24시간 경기와 확률 움직임", "",
             f"기간 {kst(_ts(w['since']))} ~ {kst(_ts(w['until']))} KST에 진행·종료된 추적 경기(5개 종목, 축구는 주요 리그 "
             f"+ 전략이 거래한 경기). 가격 = 1분 canonical(poll_mid > ws_last > history), 확률 = 해당 결과 토큰 가격.", ""]
    if gm.get("error"):
        return lines + [f"경기 데이터 없음({gm['error']}).", ""]
    rows = gm["games"]
    excluded = gm.get("excluded_out_of_scope") or {}
    ex_txt = (f" · 범위 밖 축구 제외 {sum(excluded.values())}경기("
              + ", ".join(f"{k} {n}" for k, n in excluded.items()) + ")") if excluded else ""
    if not rows:
        return lines + [f"해당 경기 없음{ex_txt}.", ""]
    lines += ["### 종목별 요약", ""]
    lines += _table(["종목", "경기", "정산", "정배 승률 (n)", "정배 평균 경기전가", "평균 최대 10분 스윙", "업셋", "급변"],
                    [[SPORT_KO.get(s["sport"], s["sport"]), s["games"], s["resolved"],
                      f"{_pct(s['favourite_win_rate'])} ({s['favourites_resolved']})",
                      _p(s["favourite_avg_pre_price"]), _p(s["avg_max_swing_10m"]), s["upsets"], s["notable_swings"]]
                     for s in gm["summary_by_sport"]])
    lines += ["", "정배 승률 vs 정배 평균 경기전가 = calibration 힌트(승률 > 가격이면 정배 과소평가). 업셋 = 경기전 최고가 "
                  f"결과가 이기지 못함(축구 무승부 포함). 급변 = 경기 중 10분 내 |Δ| ≥ {gm['notable_swing']:.2f}{ex_txt}.", ""]
    shown = games_mod.top_games(rows)
    omitted = len(rows) - len(shown)
    lines += ["### 경기별 확률 움직임", "",
              "경기전 = 킥오프 직전 마지막 가격, 경기중 = 킥오프~종료 사이 최저–최고, 최종 = 정산 직전 가격. "
              "스윙은 경기 중 최대 변화(부호 = 방향), 괄호는 도달 시각(KST)과 경기분·피리어드(없으면 킥오프 후 경과분 +Nm). "
              "종료 10분 이내 0/1 수렴(결과 확정)은 스윙에서 제외.", ""]
    for sport in (s["sport"] for s in gm["summary_by_sport"]):
        leagues = sorted({g["league"] or "–" for g in shown if g["sport"] == sport})
        for league in leagues:
            games = [g for g in shown if g["sport"] == sport and (g["league"] or "–") == league]
            lines += [f"#### {SPORT_KO.get(sport, sport)} · {league}", ""]
            table = []
            for g in games:
                first = True
                outs = g["outcomes"] or [None]
                for o in outs:
                    head = [kst(_ts(g["start_time"])), g["title"], _game_result(g), _flags(g)] if first \
                        else ["", "", "", ""]
                    first = False
                    if o is None:
                        table.append(head + ["–"] * 6)
                        continue
                    mark = " ✓" if o["won"] else ""
                    table.append(head + [f"{SIDE_KO[o['side']]} {o['label'] or ''}{mark}", _p(o["pre_price"]),
                                         f"{_p(o['min_price'])}–{_p(o['max_price'])}", _p(o["final_price"]),
                                         _swing(o["swing_1m"], g["sport"]), _swing(o["swing_10m"], g["sport"])])
            lines += _table(["킥오프(KST)", "경기", "스코어(홈–원정)·결과", "플래그", "결과 토큰", "경기전", "경기중",
                             "최종", "최대 1분 Δ", "최대 10분 Δ"], table)
            lines.append("")
    if omitted:
        lines += [f"외 {omitted}경기 생략(업셋·급변·전략거래 우선, 다음 거래량 순 상위 {len(shown)}경기 표시). "
                  "전체는 대시보드 `latest/games_24h.json`.", ""]
    return lines


def section_strategy_sport(r: dict) -> list[str]:
    rows = r.get("strategy_sport") or []
    title = "## 전략 × 종목 손익" + ("" if r["kind"] == "daily" else f" ({KIND_KO[r['kind']]})")
    lines = [title, ""]
    if not rows:
        return lines + ["포지션 없음.", ""]

    def fees(b):
        return _n(b["fees_usdc"], 4) + (f" (+미확인 {b['fees_unknown']})" if b["fees_unknown"] else "")

    def unreal(x):
        if not x["open"]:
            return "–"
        return _n(x["unrealized_pnl"], sign=True) + (f" (+평가불가 {x['unrealized_unknown']})"
                                                     if x["unrealized_unknown"] else "")
    lines += _table(["변형", "모드", "종목", "기간 진입", "기간 정산(승/패)", "기간 실현", "기간 수수료", "누적 정산(승/패)",
                     "누적 실현", "누적 수수료", "보유", "미실현(별도)"],
                    [[x["variant_id"], x["mode"], SPORT_KO.get(x["sport"], x["sport"]), x["entries_window"],
                      f"{x['window']['settled']} ({x['window']['wins']}/{x['window']['losses']})",
                      _n(x["window"]["realized_pnl"], sign=True), fees(x["window"]),
                      f"{x['all']['settled']} ({x['all']['wins']}/{x['all']['losses']})",
                      _n(x["all"]["realized_pnl"], sign=True), fees(x["all"]), x["open"], unreal(x)] for x in rows])
    live = [x for x in rows if x["mode"] == "live"]
    lines += ["", f"live 합계: 기간 실현 {_n(sum(x['window']['realized_pnl'] for x in live), sign=True)} USDC, 누적 실현 "
                  f"{_n(sum(x['all']['realized_pnl'] for x in live), sign=True)} USDC. 실현 = CONFIRMED 체결·확인된 정산으로 "
                  "청산된 포지션만(paper 행은 paper 원장, live와 합산하지 않음). 수수료 = CONFIRMED/PAPER 체결의 fee "
                  "(미확인은 0으로 채우지 않고 건수 표기). 미실현은 보유 포지션 평가손익이며 실현에 포함하지 않는다.", ""]
    return lines


EXIT_KO = {"take_profit": "익절", "stop_loss": "손절", "resolution_win": "정산 승", "resolution_loss": "정산 패",
           "time_exit": "시간 청산", "manual": "수동", "unknown": "미상"}


def section_exits(r: dict) -> list[str]:
    rows = []
    for v in r.get("variants", []):
        for e in v.get("exits") or []:
            rows.append([f"`{v['id']}`", v.get("primary_mode", v["mode"]), EXIT_KO.get(e["exit_reason"], e["exit_reason"]),
                         e["n_window"], _n(e["pnl_window"], sign=True), e["n_all"], _n(e["pnl_all"], sign=True)])
    if not rows:
        return []
    return ["## 청산 사유별 집계", "", "익절·손절 기준이 실제로 몇 번, 얼마의 손익으로 작동했는지 (정산 완료분만).", "",
            *_table(["변형", "원장", "청산 사유", "기간 건수", "기간 손익", "누적 건수", "누적 손익"], rows), ""]


def sports_cell(v: dict) -> str:
    """`soccer(L5)·nba(L5)·nhl(P5)` for per-sport variants, the plain sport list otherwise."""
    if not v.get("per_sport"):
        return ",".join(v["sports"])
    tag = {"live": "L", "paper": "P", "off": "off"}
    return "·".join(f"{d['sport']}({tag.get(d['mode'], d['mode'])}{d['stake_usdc']:g})"
                    for d in v.get("sports_detail") or [])


def section_execution(r: dict) -> list[str]:
    """Maker (fee-free resting order) execution per variant×mode; omitted when no variant ever rested an order."""
    rows = []
    for v in r["variants"]:
        for mode, e in sorted((v.get("execution") or {}).items()):
            rows.append([v["id"], mode, e["entries"], e["entries_active"], _pct(e["fill_rate"]),
                         "–" if e["avg_wait_min"] is None else f"{e['avg_wait_min']:g}분",
                         e["maker_fills"], _n(e["maker_fee_usdc"], 4), _n(e["taker_fee_est_usdc"], 4),
                         _n(e["fees_saved_usdc"], 4), f"{e['tp_filled']}/{e['tp_orders']}"])
    if not rows:
        return []
    return ["", "### 지정가(maker) 주문 체결", "",
            "체결률 = 끝난 지정가 진입 중 한 주라도 체결된 비율. 대기 = 첫 주문부터 첫 체결까지. 수수료 절감 = 같은 체결을 "
            "taker 로 했을 때의 추정 수수료 − 실제(거래소가 maker 로 보고한 체결만 0). paper 는 호가가 우리 가격을 "
            "관통할 때만 체결로 본 보수적 시뮬레이션이다.", "",
            *_table(["변형", "모드", "진입 주문", "대기 중", "체결률", "평균 대기", "maker 체결", "실수수료$",
                     "taker 추정$", "절감$", "익절 체결/주문"], rows)]


def section_variants(r: dict) -> list[str]:
    lines = ["## 전략 변형 현황", ""]
    rows = []
    for v in r["variants"]:
        s = v[v["primary_mode"]]
        lad = v["ladder"]
        lad_txt = "–" if lad.get("status") is None else \
            f"{lad['status']} ({_esc(lad.get('trades_at_tier'))}/{_esc(lad.get('needed'))}, ROI하한 {_n(lad.get('roi_ci_lo'), 3)})"
        rows.append([v["id"], v["mode"], f"{v['stake_usdc']:g}", v.get("account") or "–",
                     sports_cell(v), _n(s["pnl"]["today"], sign=True), _n(s["pnl"]["d7"], sign=True),
                     _n(s["pnl"]["d30"], sign=True), _n(s["pnl"]["all"], sign=True),
                     f"{s['trades']['all']} ({s['trades']['wins']}/{s['trades']['losses']})", _pct(s["win_rate"]),
                     _pct(s["roi"]), len(v["open"]), lad_txt])
    lines += _table(["변형", "모드", "단위$", "계좌", "종목", "오늘", "7일", "30일", "누적", "거래(승/패)", "승률",
                     "ROI", "보유", "ladder"], rows)
    lines += ["", "(paper 변형의 손익은 paper 원장 기준이며 실손익이 아니다. 종목 칸 L=live, P=paper, off=중지, 숫자=단위$.)"]
    per_sport = [v for v in r["variants"] if v.get("per_sport")]
    if per_sport:
        lines += ["", "### 종목별 모드·단위·ladder", ""]
        srows = []
        for v in per_sport:
            for d in v.get("sports_detail") or []:
                lad = d.get("ladder") or {}
                lad_txt = "–" if lad.get("status") is None else \
                    f"{lad['status']} ({_esc(lad.get('trades_at_tier'))}/{_esc(lad.get('needed'))})"
                srows.append([v["id"], d["sport"], d["mode"], f"{d['stake_usdc']:g}", d.get("trades", 0),
                              _n(d.get("pnl"), sign=True), _pct(d.get("roi")), lad_txt])
        lines += _table(["변형", "종목", "모드", "단위$", "정산", "손익", "ROI", "ladder"], srows)
    lines += section_execution(r)
    lines += ["", "### 파라미터", ""]
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


STORAGE_LEVEL_KO = {"ok": "예산 안", "warn": "경고(> 50GB/월)", "critical": "상한 초과(> 100GB/월)"}


def storage_lines(st: dict | None) -> list[str]:
    """Data growth budget (ops/storage.py): 30-day projection per area from the last 7 days."""
    if not st or st.get("error"):
        return [f"- 데이터 증가 예산: 측정 실패 ({_esc((st or {}).get('error'))})"] if st else []
    areas = st.get("areas") or {}
    parts = [f"{n} {r['gb_30d']:g}" for n, r in sorted(areas.items(), key=lambda x: -(x[1].get("gb_30d") or 0))
             if r.get("gb_30d")]
    proj = st.get("projected_30d_gb")
    head = (f"- 데이터 증가 예상(정상 상태, 최근 7일 일별 중앙값) {proj:g}GB/월 ({STORAGE_LEVEL_KO.get(st.get('level'), '-')}, 현재 총 "
            f"{st.get('total_gb_now')}GB)" if proj is not None else
            f"- 데이터 증가 예상: 측정 중(표본 1일 미만), 현재 총 {st.get('total_gb_now')}GB")
    out = [head + (": " + ", ".join(parts) + " GB" if parts else "")]
    if st.get("one_time_gb"):
        notes = "; ".join(f"{e.get('day')} {e.get('area')} {e.get('note') or e.get('kind')}"
                          for e in st.get("one_time_events") or [])
        out.append(f"  - 일회성 증가(백필, 예측 제외) {st['one_time_gb']:g}GB" + (f": {_esc(notes)}" if notes else ""))
    if st.get("naive_projected_30d_gb") is not None and st.get("naive_projected_30d_gb") != proj:
        out.append(f"  - 참고: 단순 7일 평균 예측 {st['naive_projected_30d_gb']:g}GB/월(백필·설정 변경 전 날 포함)")
    if st.get("unmeasured_areas"):
        out.append("  - 아직 증가율을 모르는 영역: " + ", ".join(st["unmeasured_areas"]))
    return out


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
    lines += storage_lines(h.get("storage"))
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
    from polylab.reports.reminders import due  # noqa: PLC0415
    notes = [n for n in due(r, int(r.get("now") or 0)) if n["slack"]]
    out = ["## 알림", "", *[f"- {_esc(n['title'])}: {_esc(n['detail'])}" for n in notes], ""] if notes else []
    if not r["alerts"]:
        return out
    return ["## 경고", "", *[f"- [{a['level']}] {a['message']}" for a in r["alerts"]], "", *out]


def section_brief(r: dict) -> list[str]:
    """Top-of-report bullets (reports.brief) for a reader who only reads the retros."""
    if not r.get("brief"):
        return []
    return ["## 오늘의 브리프", "", *[f"- {b}" for b in r["brief"]], ""]


def section_thesis(r: dict) -> list[str]:
    """Weekly/monthly only, AI-drafted, omitted unless sentences passed the n/evidence gate in autopilot.attention."""
    rows = r.get("thesis_sentences") or []
    if r["kind"] == "daily" or not rows:
        return []
    return ["## 논문에 쓸 수 있는 문장 (AI 초안)", "",
            "AI가 작성한 초안이다. 표본 n과 근거 파일이 있는 문장만 싣지만, 인용 전 원자료로 다시 확인해야 한다.", "",
            *[f"- {t['text']} (n={t['n']}, 근거 `{t['evidence_ref']}`)" for t in rows], ""]


VERDICT_KO = {"supports": "가설 지지", "contradicts": "가설과 반대", "inconclusive": "불확실",
              "insufficient": "표본 부족"}
DIRECTION_KO = {"up": "킥오프로 갈수록 커짐", "down": "킥오프로 갈수록 작아짐", "flat": "뚜렷한 변화 없음"}
TIER_KO = {"all": "전체", "major": "주요 리그", "other": "기타 리그"}


def _ci(ci, sign: bool = True) -> str:
    if not ci or ci[0] is None:
        return "–"
    return f"{ci[0]:+.3f}~{ci[1]:+.3f}" if sign else f"{ci[0]:.3f}~{ci[1]:.3f}"


def section_ou05_lifecycle(r: dict) -> list[str]:
    """Weekly/monthly: the Over 0.5 overpricing curve by time to kickoff (analysis.ou05.lifecycle)."""
    if r["kind"] == "daily":
        return []
    lc = r.get("ou05_lifecycle")
    head = ["## 0.5 Over 생애 과대평가", "",
            "연구자 가설(2026-10-05): 마켓은 열린 직후 혼돈, 약 하루 뒤 안정, 이후 킥오프까지 Over 과대평가가 커진다 → "
            "과대평가가 작을 때 사서 클 때 판다. 과대평가 = 평균 Over 가격 − 실제 Over 비율(양수 = Over 가 비싸다).", ""]
    if not lc:
        return head + ["집계 없음(`polylab analyze ou05` 결과가 아직 없다).", ""]
    if lc.get("error"):
        return head + [f"집계 실패: {lc['error']}", ""]
    ds = lc.get("data_status") or {}
    sc = lc.get("scope") or {}
    lines = head + [f"- 근거: 정산 시장 {_esc(sc.get('resolved'))}개, poll 행 {_esc(sc.get('poll_rows'))}, "
                    f"과거 중간가 행 {_esc(sc.get('history_rows'))} (집계 {lc.get('generated_at') or '–'})"]
    if ds.get("thin"):
        lines.append(f"- **데이터가 아직 얇다**: {ds.get('note')}")
    for c in lc.get("checks") or []:
        if c.get("verdict") == "insufficient":
            lines.append(f"- {TIER_KO.get(c['tier'], c['tier'])}: {VERDICT_KO['insufficient']}")
            continue
        lines.append(f"- {TIER_KO.get(c['tier'], c['tier'])}: {VERDICT_KO.get(c['verdict'], c['verdict'])} — "
                     f"{c['early_band']} {c['early_overpricing']:+.3f} → {c['late_band']} {c['late_overpricing']:+.3f} "
                     f"({DIRECTION_KO.get(c.get('direction'), '')}), 가장 쌈 {c['cheapest_band']}·가장 비쌈 "
                     f"{c['dearest_band']}, 가격 근거 {'/'.join(c.get('price_basis') or [])}")
    for tier in ("all", "major"):
        rows = [b for b in lc.get("bands") or [] if b["tier"] == tier]
        if not rows:
            continue
        lines += ["", f"### {TIER_KO[tier]}", ""]
        lines += _table(["킥오프까지", "정산 n", "평균 Over", "실제 Over (95% CI)", "과대평가 (95% CI)", "스프레드 p50",
                         "가격 근거"],
                        [[b["label"], b["n"], _n(b["mean_over"], 3),
                          "–" if b["over_rate"] is None else f"{b['over_rate']:.3f} ({_ci(b['over_rate_ci'], False)})",
                          "–" if b["overpricing"] is None else f"{b['overpricing']:+.3f} ({_ci(b['overpricing_ci'])})",
                          _n(b["spread_p50"], 3), b["price_basis"]] for b in rows])
    lines += ["", "(상장 후 경과 시간별 혼돈→안정은 이 표로 직접 측정하지 않는다. 같은 시장이 여러 구간에 반복되므로 "
              "구간 간 CI 비교는 대략적 신호다. 결정론 집계이며 AI 회고가 goal-over-all 진입 창·익절 폭 제안의 근거로 쓴다.)",
              ""]
    return lines


def section_llm_forecast(r: dict) -> list[str]:
    from polylab.research.llm_eval import render_lines  # noqa: PLC0415
    return render_lines(r.get("llm_forecast"))


def section_manual(r: dict) -> list[str]:
    """Track 2 manual AI bets (src/polylab/manual/report.py)."""
    sec = r.get("manual")
    if sec and sec.get("error"):
        return ["## 수동 AI 베팅 (트랙 2)", "", f"집계 실패: {sec['error']}", ""]
    from polylab.manual.report import render_lines as manual_lines  # noqa: PLC0415
    return manual_lines(sec, r["kind"])


def render(r: dict, narrative: str | None = None, applied: list[dict] | None = None,
           rejected: list[dict] | None = None) -> str:
    lines = [f"# {r['title']}", "",
             f"- 생성: {kst(r['now'], '%Y-%m-%d %H:%M')} KST · commit `{r.get('git_commit') or '–'}` · "
             f"기간 {kst(_ts(r['window']['since']))} ~ {kst(_ts(r['window']['until']))} KST",
             f"- 대시보드: https://poly.zowoo.uk", ""]
    for section in (section_brief, section_thesis, section_summary, section_games, section_strategy_sport, section_exits, section_variants, section_transactions,
                    section_open, section_manual, section_changes,
                    section_alerts, section_health, section_research, section_ou05_lifecycle, section_llm_forecast):
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
