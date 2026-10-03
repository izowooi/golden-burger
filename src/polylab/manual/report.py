"""Track 2 (manual AI bets) read model: report section, markdown, dashboard JSON, attention rules.

Read-only over `<data>/manual/*.db` (no network, no secrets: aliases/labels come from the ledgers' meta table).
Money rules: realised = fully settled positions only (confirmed sell / confirmed resolution / redeem cash);
unrealised (Data API mark) is shown separately; quarantined positions are counted, never zeroed.
Track 2 scope = positions linked to a sports game (`position_meta.track2`); the rest is shown as "기타".
Win rate counts resolution outcomes only (resolved win/redeemed vs resolved loss); early sells are separate.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pandas as pd

from polylab.analysis import _common as C
from polylab.analysis import performance
from polylab.manual import ledger
from polylab.manual import predictions as predictions_mod

STAKE_BANDS = ((0, 50, "≤50"), (50, 150, "50–150"), (150, 300, "150–300"), (300, 600, "300–600"),
               (600, float("inf"), "600+"))
LOSS_LOOKBACK_S = 3 * 86400
DRAWDOWN_PCT = -0.10
RESULT_KO = {"open": "보유", "closed_sell": "매도 청산", "resolved_win": "정산 승", "resolved_loss": "정산 패",
             "resolved_split": "정산 분할", "redeemed": "정산 승(redeem)", "quarantined": "격리(집계 제외)"}
SIDE_KO = {"over": "Over", "under": "Under", "home": "홈", "away": "원정", "draw": "무", "yes": "Yes", "no": "No"}
POS_SQL = """
SELECT p.position_id, p.mode, p.stake_usdc, p.sport, p.league, p.game_key, p.condition_id, p.token_id,
       p.outcome_label, p.opened_at, p.entry_price, p.shares, p.cost_usdc, p.entry_fee_usdc, p.exit_fee_usdc,
       p.status, p.closed_at, p.exit_reason, p.exit_price, p.proceeds_usdc, p.realized_pnl, p.settlement,
       p.game_minute_at_entry, p.param_version,
       m.result, m.track2, m.game_title, m.start_time, m.market_type, m.line, m.side, m.link_source, m.implied_p00,
       m.bought_shares, m.remaining_shares, m.payout_usdc, m.payout_source, m.mark_price, m.unrealized_pnl,
       m.quarantine_reason
FROM positions p JOIN position_meta m ON m.position_id = p.position_id
"""
FILL_SQL = """
SELECT f.ts, f.side, f.price, f.shares, f.fee_usdc, o.position_id, json_extract(f.raw, '$.usdc_size') AS usdc
FROM fills f JOIN orders o ON o.intent_id = f.intent_id WHERE f.ts >= ? AND f.ts < ?
"""


def ledger_paths(paths) -> list:
    d = ledger.manual_dir(paths)
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.db") if p.stem != "predictions")


def _r(v, nd=4):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else round(float(v), nd)


def load_account(path) -> dict | None:
    conn = C.open_ro(path)
    if conn is None:
        return None
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        df = C.read_sql(conn, POS_SQL)
        fills = [dict(r) for r in conn.execute(FILL_SQL, (0, 2 ** 40))]
        flows = {r[0]: r[1] for r in conn.execute("SELECT type, SUM(usdc_size) FROM activity WHERE type IN "
                                                  "('MAKER_REBATE','TAKER_REBATE','REWARD','YIELD') AND ts >= ? "
                                                  "GROUP BY type", (int(meta.get("since") or 0),))}
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    alias = meta.get("alias") or path.stem
    df["alias"] = alias
    return {"alias": alias, "label": meta.get("label") or None, "since": _int(meta.get("since")),
            "last_sync_at": _int(meta.get("last_sync_at")), "bankroll_usdc": _float(meta.get("bankroll_usdc")),
            "bankroll_first_seen_at": _int(meta.get("bankroll_first_seen_at")), "positions": df, "fills": fills,
            "unattributed_redeems": _int(meta.get("unattributed_redeems")) or 0,
            "credits": {k: _r(v) for k, v in flows.items()}}


def _int(v):
    try:
        return int(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


def _float(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


def settled(df: pd.DataFrame) -> pd.DataFrame:
    """Manual analogue of performance.settled (that one treats every non-live mode as paper)."""
    if df.empty:
        return df
    mask = df["status"].isin(["closed", "resolved"]) & df["realized_pnl"].notna() & df["closed_at"].notna() \
        & (df["mode"] == "manual") & df["settlement"].isin(performance.LIVE_SETTLEMENTS)
    return df[mask].sort_values("closed_at").reset_index(drop=True)


def stake_band(cost) -> str | None:
    if cost is None or pd.isna(cost):
        return None
    return next(label for lo, hi, label in STAKE_BANDS if lo <= float(cost) < hi or (lo == 0 and float(cost) <= hi))


def money(s: pd.DataFrame, opens: pd.DataFrame, now: int, fills: list[dict]) -> dict:
    """Track 2 money block. Fees come from the settled positions' fills (NULL fee = unknown, counted)."""
    base = performance.summary(s, now)
    wins = int(s["result"].isin(ledger.WIN_RESULTS).sum()) if not s.empty else 0
    losses = int((s["result"] == "resolved_loss").sum()) if not s.empty else 0
    sold = int((s["result"] == "closed_sell").sum()) if not s.empty else 0
    unknown_unreal = int(opens["unrealized_pnl"].isna().sum()) if not opens.empty else 0
    pids = set(s["position_id"]) if not s.empty else set()
    fees = [f["fee_usdc"] for f in fills if f["position_id"] in pids]
    return {"realized_pnl": base["pnl"], "settled": int(len(s)), "wins": wins, "losses": losses, "sold": sold,
            "win_rate": _r(wins / (wins + losses)) if wins + losses else None, "roi": base["roi"],
            "cost_settled": base["cost"], "open": int(len(opens)),
            "open_cost_usdc": _r(opens["cost_usdc"].sum(), 2) if not opens.empty else 0.0,
            "unrealized_pnl": _r(opens["unrealized_pnl"].dropna().sum()) if not opens.empty else 0.0,
            "unrealized_unknown": unknown_unreal,
            "fees_usdc": _r(sum(f for f in fees if f is not None and not pd.isna(f))),
            "fees_unknown": sum(1 for f in fees if f is None or pd.isna(f))}


def by_stake(s: pd.DataFrame) -> list[dict]:
    if s.empty:
        return []
    s = s.assign(band=s["cost_usdc"].map(stake_band))
    out = []
    for lo, hi, label in STAKE_BANDS:
        g = s[s["band"] == label]
        if g.empty:
            continue
        pnl = g["realized_pnl"].astype(float)
        wins, losses = int(g["result"].isin(ledger.WIN_RESULTS).sum()), int((g["result"] == "resolved_loss").sum())
        out.append({"band": label, "n": int(len(g)), "pnl": _r(pnl.sum()), "cost": _r(g["cost_usdc"].sum(), 2),
                    "roi": _r(pnl.sum() / g["cost_usdc"].sum()) if g["cost_usdc"].sum() > 0 else None,
                    "wins": wins, "losses": losses,
                    "win_rate": _r(wins / (wins + losses)) if wins + losses else None})
    return out


def _clean(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _market(r: dict) -> str:
    r = {k: _clean(v) for k, v in r.items()}
    mt, line, side = r.get("market_type"), r.get("line"), r.get("side")
    lab = SIDE_KO.get(side, r.get("outcome_label") or "–")
    if mt == "total" and line is not None:
        return f"O/U {line:g} {lab}"
    if mt in ("moneyline", "draw"):
        return f"승무패 {r.get('outcome_label') or lab}"
    return f"{mt or '기타'} {r.get('outcome_label') or lab}"


def _pos_row(acct: dict, r: dict) -> dict:
    r = {k: _clean(v) for k, v in r.items()}
    return {"account": acct["label"] or acct["alias"], "position_id": _pid(r["position_id"]), "sport": r.get("sport"),
            "league": r.get("league"), "game": r.get("game_title"), "kickoff": C.iso(_int(r.get("start_time"))),
            "market": _market(r), "market_type": r.get("market_type"), "line": _r(r.get("line"), 2),
            "side": r.get("side"), "outcome": r.get("outcome_label"), "track2": bool(r.get("track2")),
            "opened_at": C.iso(_int(r.get("opened_at"))), "closed_at": C.iso(_int(r.get("closed_at"))),
            "entry_price": _r(r.get("entry_price")), "shares": _r(r.get("bought_shares")),
            "stake_usdc": _r(r.get("cost_usdc"), 4), "entry_fee_usdc": _r(r.get("entry_fee_usdc"), 6),
            "result": r.get("result"), "proceeds_usdc": _r(r.get("proceeds_usdc")),
            "realized_pnl": _r(r.get("realized_pnl")), "mark_price": _r(r.get("mark_price")),
            "unrealized_pnl": _r(r.get("unrealized_pnl")), "implied_p00_at_entry": _r(r.get("implied_p00")),
            "link_source": r.get("link_source"), "quarantine_reason": r.get("quarantine_reason")}


def _pid(position_id: str) -> str:
    """Stable short id (alias + token digest) for UI keys / attention ids."""
    alias, _, token = str(position_id).partition(":")
    return f"{alias}:{hashlib.sha1(token.encode()).hexdigest()[:10]}"


def _alerts(acct: dict, df: pd.DataFrame, s_all: pd.DataFrame, now: int) -> list[dict]:
    out = []
    t2 = s_all[s_all["track2"] == 1] if not s_all.empty else s_all
    if not t2.empty:
        recent = t2[(t2["result"] == "resolved_loss") & (t2["closed_at"].astype(int) >= now - LOSS_LOOKBACK_S)]
        for r in recent.to_dict("records"):
            out.append({"id": f"manual_loss:{_pid(r['position_id'])}", "severity": "info",
                        "account": acct["label"] or acct["alias"], "game": r.get("game_title"),
                        "market": _market(r), "realized_pnl": _r(r["realized_pnl"]),
                        "closed_at": C.iso(int(r["closed_at"]))})
    bank = acct["bankroll_usdc"]
    total = float(s_all["realized_pnl"].sum()) if not s_all.empty else 0.0
    if bank and total <= DRAWDOWN_PCT * bank:
        out.append({"id": f"manual_drawdown:{acct['alias']}", "severity": "warn",
                    "account": acct["label"] or acct["alias"], "realized_pnl": _r(total), "bankroll_usdc": bank,
                    "pct": _r(total / bank)})
    return out


def _track2(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["track2"] == 1] if not df.empty else df


def report_section(paths, since: int, until: int, kind: str, now: int) -> dict | None:
    """Section dict for reports.build (None when no manual ledger exists)."""
    accounts = [a for a in (load_account(p) for p in ledger_paths(paths)) if a]
    if not accounts:
        return None
    rows_accounts, trades, opens_out, settled_out, all_settled, t2_positions, alerts = [], [], [], [], [], [], []
    quarantined, other = 0, {"settled": 0, "realized_pnl": 0.0, "open": 0}
    all_open, all_fills = [], []
    for acct in accounts:
        df = acct["positions"]
        s_all = settled(df)
        opens = df[df["status"] == "open"] if not df.empty else df
        s2, o2 = _track2(s_all), _track2(opens)
        rows_accounts.append({"account": acct["label"] or acct["alias"], "since": C.iso(acct["since"]),
                              "last_sync_at": C.iso(acct["last_sync_at"]), "bankroll_usdc": acct["bankroll_usdc"],
                              "bankroll_first_seen_at": C.iso(acct["bankroll_first_seen_at"]),
                              "track2": money(s2, o2, now, acct["fills"]),
                              "all_realized_pnl": _r(s_all["realized_pnl"].sum()) if not s_all.empty else 0.0,
                              "drawdown_pct": (_r(float(s_all["realized_pnl"].sum()) / acct["bankroll_usdc"])
                                               if acct["bankroll_usdc"] and not s_all.empty else None),
                              "credits_usdc": acct["credits"]})
        quarantined += (int((df["status"] == "quarantined").sum()) if not df.empty else 0) \
            + acct["unattributed_redeems"]
        if not s_all.empty:
            so = s_all[s_all["track2"] != 1]
            other["settled"] += int(len(so))
            other["realized_pnl"] = _r(other["realized_pnl"] + float(so["realized_pnl"].sum()))
        if not opens.empty:
            other["open"] += int((opens["track2"] != 1).sum())
        by_pid = {r["position_id"]: r for r in df.to_dict("records")}
        for f in acct["fills"]:
            if since <= f["ts"] < until and f["position_id"] in by_pid:
                p = by_pid[f["position_id"]]
                trades.append({**{k: v for k, v in _pos_row(acct, p).items()
                                  if k in ("account", "position_id", "sport", "league", "game", "kickoff", "market",
                                           "track2", "result", "realized_pnl")},
                               "at": C.iso(f["ts"]), "side": f["side"], "price": _r(f["price"]),
                               "shares": _r(f["shares"]), "usdc": _r(f["usdc"]), "fee_usdc": _r(f["fee_usdc"], 6)})
        for r in df[(df["status"] == "resolved") & (df["exit_reason"].astype(str).str.startswith("resolution"))] \
                .to_dict("records") if not df.empty else []:
            if r.get("closed_at") is not None and since <= int(r["closed_at"]) < until:
                row = _pos_row(acct, r)
                trades.append({k: row[k] for k in ("account", "position_id", "sport", "league", "game", "kickoff",
                                                    "market", "track2", "result", "realized_pnl")}
                              | {"at": row["closed_at"], "side": "RESOLVE",
                                 "price": _r(r["exit_price"]), "shares": _r(r.get("remaining_shares") or None),
                                 "usdc": _r(r.get("payout_usdc")), "fee_usdc": None})
        opens_out += [_pos_row(acct, r) for r in opens.to_dict("records")] if not opens.empty else []
        if not s_all.empty:
            w = s_all[(s_all["closed_at"].astype(int) >= since) & (s_all["closed_at"].astype(int) < until)]
            settled_out += [_pos_row(acct, r) for r in w.to_dict("records")]
            all_settled.append(s_all)
        if not opens.empty:
            all_open.append(opens)
        all_fills += acct["fills"]
        t2_positions += [{**r, "alias": acct["alias"]} for r in df[df["track2"] == 1].to_dict("records")] \
            if not df.empty else []
        alerts += _alerts(acct, df, s_all, now)
    total2 = _track2(pd.concat(all_settled, ignore_index=True)) if all_settled else pd.DataFrame()
    open2 = _track2(pd.concat(all_open, ignore_index=True)) if all_open else pd.DataFrame()
    section = {"generated_at": C.iso(now), "window": {"since": C.iso(since), "until": C.iso(until)},
               "accounts": rows_accounts,
               "totals": money(total2, open2, now, all_fills),
               "by_stake": by_stake(total2), "other": other, "quarantined": quarantined,
               "trades": sorted(trades, key=lambda t: t["at"] or "", reverse=True),
               "open_positions": sorted(opens_out, key=lambda o: o["opened_at"] or ""),
               "settled_in_window": sorted(settled_out, key=lambda o: o["closed_at"] or "", reverse=True),
               "alerts": alerts, "predictions": None}
    if kind != "daily":
        section["predictions"] = prediction_table(paths, t2_positions, since, until)
    return section


def prediction_table(paths, positions: list[dict], since: int, until: int) -> dict | None:
    """Predictions whose file date (KST) falls in the report window (one day of slack before), else None."""
    import datetime as dt
    lo = (dt.datetime.fromtimestamp(since, C.KST).date() - dt.timedelta(days=1)).isoformat()
    hi = dt.datetime.fromtimestamp(until, C.KST).date().isoformat()
    preds = [p for p in predictions_mod.load(paths) if lo <= p["file_date"] <= hi]
    if not preds:
        return None
    pairs, unmatched = predictions_mod.match(preds, positions)
    rows = []
    for x in pairs:
        p, pos = x["prediction"], x["position"]
        zero_zero = None
        if pos.get("market_type") == "total" and pos.get("line") == 0.5 and pos.get("result") in \
                ("resolved_win", "redeemed", "resolved_loss"):
            won = pos["result"] != "resolved_loss"
            zero_zero = (not won) if pos.get("side") == "over" else (won if pos.get("side") == "under" else None)
        rows.append({"file_date": p["file_date"], "game": p["game"], "engine": p["engine"], "ai_p00": p["p00"],
                     "rank": p["rank"], "market_p00_at_entry": _r(pos.get("implied_p00")),
                     "market": _market(pos), "result": pos.get("result"), "zero_zero": zero_zero,
                     "realized_pnl": _r(pos.get("realized_pnl")), "position_id": _pid(pos["position_id"])})
    return {"rows": rows, "unmatched": [{k: p[k] for k in ("file_date", "game", "engine", "p00", "rank")}
                                        for p in unmatched], "predictions": len(preds)}


# ------------------------------------------------------------------ markdown

def _n(v, nd=2, sign=False) -> str:
    if v is None:
        return "–"
    return f"{v:+,.{nd}f}" if sign else f"{v:,.{nd}f}"


def _pct(v) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def _kst(iso: str | None, fmt: str = "%m-%d %H:%M") -> str:
    if not iso:
        return "–"
    import datetime as dt
    return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).astimezone(C.KST) \
        .strftime(fmt)


def _table(header: list[str], rows: list[list]) -> list[str]:
    esc = lambda v: "–" if v is None or v == "" else str(v).replace("|", "/").replace("\n", " ")  # noqa: E731
    return ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|",
            *["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]]


def render_lines(section: dict | None, kind: str = "daily") -> list[str]:
    if not section:
        return []
    lines = ["## 수동 AI 베팅 (트랙 2)", "",
             "연구자가 직접 넣은 베팅을 공개 지갑 주소(watch-only, 키 없음)의 Data API 체결로 자동 기록한다. 계좌는 "
             "별칭만 표시. 실현 = 매도 체결·확인된 정산·redeem 현금으로 끝난 포지션, 미실현 = Data API 평가가(별도). "
             "승률 = 정산 승/(승+패), 만기 전 매도는 따로 센다.", ""]
    rows = []
    for a in section["accounts"]:
        t = a["track2"]
        bank = "미설정(−10% 규칙 비활성)" if not a["bankroll_usdc"] else \
            f"{_n(a['bankroll_usdc'])} ({_pct(a['drawdown_pct'])})"
        rows.append([a["account"], _kst(a["since"], "%Y-%m-%d") if a["since"] else "전체", t["settled"],
                     f"{t['wins']}/{t['losses']}", t["sold"], _pct(t["win_rate"]), _n(t["realized_pnl"]["all"], sign=True),
                     _pct(t["roi"]), t["open"], _n(t["unrealized_pnl"], sign=True), _n(a["all_realized_pnl"], sign=True),
                     bank, _kst(a["last_sync_at"])])
    lines += _table(["계좌", "집계 시작", "정산", "승/패", "매도청산", "승률", "누적 실현", "ROI", "보유", "미실현(별도)",
                     "계좌 전체 실현", "bankroll (누적/bankroll)", "마지막 동기화"], rows)
    tot = section["totals"]
    lines += ["", f"트랙 2 합계: 정산 {tot['settled']}건 (승 {tot['wins']} / 패 {tot['losses']} / 매도 {tot['sold']}), "
                  f"승률 {_pct(tot['win_rate'])}, 누적 실현 {_n(tot['realized_pnl']['all'], sign=True)} USDC, "
                  f"ROI {_pct(tot['roi'])}, 수수료 {_n(tot['fees_usdc'], 4)}"
                  + (f" (+미확인 {tot['fees_unknown']}건)" if tot["fees_unknown"] else "") + " USDC. "
                  f"기타(경기 미연결) 정산 {section['other']['settled']}건 {_n(section['other']['realized_pnl'], sign=True)} "
                  f"USDC · 보유 {section['other']['open']}개, 격리(집계 제외) {section['quarantined']}건.", ""]
    if section["by_stake"]:
        lines += ["### 금액대별 (트랙 2 정산분)", ""]
        lines += _table(["금액대 USDC", "정산", "승/패", "승률", "손익", "원가", "ROI"],
                        [[b["band"], b["n"], f"{b['wins']}/{b['losses']}", _pct(b["win_rate"]), _n(b["pnl"], sign=True),
                          _n(b["cost"]), _pct(b["roi"])] for b in section["by_stake"]])
        lines.append("")
    title = "### 지난 24시간 수동 거래" if kind == "daily" else "### 기간 수동 거래"
    lines += [title, ""]
    if section["trades"]:
        lines += _table(["시각(KST)", "계좌", "경기", "리그", "마켓", "구분", "가격", "수량", "USDC", "수수료", "포지션 결과",
                         "실현"],
                        [[_kst(t["at"]), t["account"], t["game"], t["league"], t["market"], t["side"], _n(t["price"], 3),
                          _n(t["shares"]), _n(t["usdc"]), _n(t["fee_usdc"], 4), RESULT_KO.get(t["result"], t["result"]),
                          _n(t["realized_pnl"], sign=True)] for t in section["trades"]])
    else:
        lines.append("거래 없음.")
    lines += ["", "### 수동 보유 포지션 (미실현, 실현에 미포함)", ""]
    if section["open_positions"]:
        lines += _table(["진입(KST)", "계좌", "경기", "킥오프", "마켓", "진입가", "수량", "원가", "현재가", "미실현",
                         "시장 P(0:0)"],
                        [[_kst(o["opened_at"]), o["account"], o["game"], _kst(o["kickoff"]), o["market"],
                          _n(o["entry_price"], 3), _n(o["shares"]), _n(o["stake_usdc"]), _n(o["mark_price"], 3),
                          _n(o["unrealized_pnl"], sign=True), _n(o["implied_p00_at_entry"], 3)]
                         for o in section["open_positions"]])
    else:
        lines.append("보유 포지션 없음.")
    lines.append("")
    if section["settled_in_window"]:
        lines += ["### 기간 내 정산·청산", ""]
        lines += _table(["종료(KST)", "계좌", "경기", "마켓", "진입가", "원가", "회수", "결과", "실현", "시장 P(0:0)"],
                        [[_kst(o["closed_at"]), o["account"], o["game"], o["market"], _n(o["entry_price"], 3),
                          _n(o["stake_usdc"]), _n(o["proceeds_usdc"]), RESULT_KO.get(o["result"], o["result"]),
                          _n(o["realized_pnl"], sign=True), _n(o["implied_p00_at_entry"], 3)]
                         for o in section["settled_in_window"]])
        lines.append("")
    pred = section.get("predictions")
    if kind != "daily" and pred:
        lines += ["### AI 예측 vs 결과 (manual/predictions)", ""]
        if pred["rows"]:
            lines += _table(["날짜", "경기", "엔진", "AI P(0:0)", "순위", "시장 P(0:0) 진입", "마켓", "0:0 여부",
                             "결과", "실현"],
                            [[r["file_date"], r["game"], r["engine"], _n(r["ai_p00"], 3), r["rank"],
                              _n(r["market_p00_at_entry"], 3), r["market"],
                              "–" if r["zero_zero"] is None else ("0:0" if r["zero_zero"] else "득점"),
                              RESULT_KO.get(r["result"], r["result"]), _n(r["realized_pnl"], sign=True)]
                             for r in pred["rows"]])
            lines.append("")
        if pred["unmatched"]:
            lines += [f"거래와 연결되지 않은 예측 {len(pred['unmatched'])}건: "
                      + "; ".join(f"{u['file_date']} {u['game']} ({u['engine'] or '–'})"
                                  for u in pred["unmatched"][:15])
                      + (" …" if len(pred["unmatched"]) > 15 else ""), ""]
    return lines


# ------------------------------------------------------------------ dashboard + attention

def snapshot(section: dict | None, generated_at: str) -> dict:
    """latest/manual.json (docs/contracts/dashboard-json.md)."""
    if not section:
        return {"generated_at": generated_at, "accounts": [], "totals": None, "by_stake": [], "trades_24h": [],
                "open_positions": [], "settled_24h": [], "other": None, "quarantined": 0}
    return {"generated_at": generated_at, "accounts": section["accounts"], "totals": section["totals"],
            "by_stake": section["by_stake"], "trades_24h": section["trades"],
            "open_positions": section["open_positions"], "settled_24h": section["settled_in_window"],
            "other": section["other"], "quarantined": section["quarantined"]}


def attention_items(report: dict, report_ref: str) -> list[dict]:
    """Rule items for autopilot.attention (family 'manual'); they auto-resolve when no longer emitted."""
    sec = report.get("manual") or {}
    out = []
    for a in sec.get("alerts") or []:
        if a["id"].startswith("manual_loss:"):
            out.append({"id": a["id"], "rule": "manual", "severity": "info", "category": "research_finding",
                        "title": f"수동 베팅 정산 패: {a['account']} · {a['game']} {a['market']} {_n(a['realized_pnl'], sign=True)}",
                        "detail": f"{_kst(a['closed_at'])} KST 정산. 트랙 2 기록용 알림(결정 불필요).",
                        "evidence_ref": report_ref, "source": "rule"})
        else:
            out.append({"id": a["id"], "rule": "manual", "severity": "warn", "category": "risk",
                        "title": f"수동 베팅 누적 실현 {_n(a['realized_pnl'], sign=True)} USDC: bankroll의 {_pct(a['pct'])}"
                                 f" ({a['account']})",
                        "detail": f"계좌 {a['account']} 실현손익이 처음 기록된 bankroll {_n(a['bankroll_usdc'])} USDC의 −10% "
                                  "아래로 내려갔다. 베팅 금액·선택 기준 점검 권장.",
                        "evidence_ref": report_ref, "source": "rule"})
    return out
