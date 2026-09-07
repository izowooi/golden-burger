#!/usr/bin/env python3
"""Offline, gap-censored paired Cat/Dog entry/0.99-TP displayed-book replay.

Reads verified SQLite snapshots only. No order client, credentials or network.
The per-side fee grid is a sensitivity assumption, not historical fee evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import sports_visual_data as visual


def levels(book, side):
    result = []
    for level in book.get(side, []):
        price, size = visual.number(level.get("price")), visual.number(level.get("size"))
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            return []
        result.append((price, size))
    return sorted(result, reverse=side == "bids")


def ask_side_state(book):
    """Distinguish a real empty ask list from malformed/missing source data."""
    raw = book.get("asks")
    if not isinstance(raw, (list, tuple)):
        return "INVALID"
    if not raw:
        return "EMPTY_VALID"
    for level in raw:
        if not isinstance(level, dict):
            return "INVALID"
        price, size = visual.number(level.get("price")), visual.number(level.get("size"))
        if price is None or size is None or not 0 < price <= 1 or size <= 0:
            return "INVALID"
    return "PRESENT_VALID"


def decode_original_book_asks(blob, expected_sha, raw_bytes):
    """Read original CLOB asks; normalized levels may have dropped bad values."""
    raw = gzip.decompress(blob)
    if len(raw) != raw_bytes or hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("CLOB original batch checksum/length mismatch")
    batch = json.loads(raw)
    if not isinstance(batch, list):
        raise ValueError("CLOB original batch is not a list")
    result = {}
    for book in batch:
        if not isinstance(book, dict) or not str(book.get("asset_id") or ""):
            raise ValueError("CLOB original token identity missing")
        token = str(book["asset_id"])
        if token in result:
            raise ValueError("CLOB original token identity duplicated")
        digest = hashlib.sha256(json.dumps(book, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        result[token] = (digest, ask_side_state(book))
    return result


class WhiteOriginalAskEvidence:
    def __init__(self, connection):
        self.connection = connection
        self.snapshots = {r[0]:(r[1],r[2]) for r in connection.execute(
            "SELECT snapshot_id,request_id,raw_book_sha256 FROM orderbook_snapshots")}
        self.requests = defaultdict(list)
        for request, payload in connection.execute(
            "SELECT request_id,payload_id FROM raw_payloads WHERE payload_kind='CLOB_BOOK_BATCH'"):
            self.requests[request].append(payload)
        self.audit = Counter()

    @lru_cache(maxsize=64)
    def _batch(self, request):
        payloads = self.requests.get(request, [])
        if len(payloads) != 1:
            raise ValueError("CLOB original batch identity missing/duplicated")
        row = self.connection.execute(
            "SELECT payload_gzip,sha256,raw_bytes FROM raw_payloads WHERE payload_id=?",
            (payloads[0],)).fetchone()
        return decode_original_book_asks(*row)

    def state(self, snapshot_id, token):
        self.audit["checked_high_bid_rows"] += 1
        try:
            request, expected = self.snapshots[snapshot_id]
            digest, state = self._batch(request)[str(token)]
            if digest != expected:
                raise ValueError("original CLOB book disagrees with snapshot hash")
        except (KeyError, TypeError, ValueError, OSError):
            self.audit["raw_identity_or_hash_gap"] += 1
            return "INVALID"
        self.audit[state] += 1
        return state


def buy_walk(asks, notional=5.0):
    left, shares, worst = notional, 0.0, None
    for price, size in asks:
        spent = min(left, price * size)
        shares += spent / price
        left -= spent
        worst = price
        if left <= 1e-8:
            return notional / shares, shares, worst
    return None


def sell_walk(bids, shares):
    left, proceeds, worst = shares, 0.0, None
    for price, size in bids:
        sold = min(left, size)
        proceeds += sold * price
        left -= sold
        worst = price
        if left <= 1e-8:
            return proceeds / shares, proceeds, worst
    return None


def failed_between(failures, previous, current):
    return any(a <= current and (b is None or b > previous) for a, b in failures)


def replay_event(groups, terminals, failures, threshold, target, fee_bps, max_gap):
    """One first-observed qualifying entry per event/cohort; no reentry."""
    entered, previous, record = None, None, None
    rate = fee_bps / 10_000
    cap = 0.989 if threshold in (0.95, 0.97) else 0.999
    for group in groups:
        now = min(s["time"] for s in group)
        if entered:
            # A gap can hide both TP and stop: never bridge it by interpolation.
            if now - previous > max_gap or failed_between(failures, previous, now):
                record["exit_reason"] = "path_gap_or_failed_run"
                break
            same = [s for s in group if s["token_id"] == entered["token_id"]]
            if len(same) != 1 or not same[0]["valid"]:
                record["exit_reason"] = "selected_token_or_lineage_gap"
                break
            current = same[0]
            previous = now
            walk = sell_walk(current["bids"], record["sold_shares"])
            best_bid = current["bids"][0][0] if current["bids"] else None
            if best_bid is None:
                record["exit_reason"] = "missing_bid_depth"
                break
            if walk:
                vwap, proceeds, worst = walk
                net = proceeds * (1 - rate) - record["allocated_principal"] * (1 + rate)
                # Submission must recover the entire $5 BUY plus its fee even
                # if SDK dust pays zero. Reported realized-like amounts below
                # remain explicitly the sold portion, just like the live ledger.
                net_floor = worst * record["sold_shares"] * (1 - rate) - 5.0 * (1 + rate) - 0.001
                empty_asks = current.get("ask_state") == "EMPTY_VALID" and current["spread"] is None
                present_asks = (current.get("ask_state") == "PRESENT_VALID"
                    and current["spread"] is not None and math.isfinite(current["spread"])
                    and 0 <= current["spread"] <= 0.10)
                tp_preflight = current["open_at_observation"] and (empty_asks or present_asks)
                if target is not None and worst >= target and net_floor > 0 and tp_preflight:
                    record.update(exit_reason="TP", exit=current["timestamp"], exit_vwap=vwap,
                                  exit_worst=worst, modeled_net_usdc=net)
                    break
                if best_bid <= 0.70:
                    # The source collector proves the market live at sampling;
                    # this is still not the live strategy's dual API OPEN proof.
                    if current["open_at_observation"] and current["spread"] is not None and 0 <= current["spread"] <= 0.10:
                        record.update(exit_reason="SL", exit=current["timestamp"], exit_vwap=vwap,
                                      exit_worst=worst, modeled_net_usdc=net)
                        break
            if (target is not None and best_bid >= target) or best_bid <= 0.70:
                if not walk:
                    record["depth_blocked_observations"] += 1
            continue
        valid = [s for s in group if s["valid"]]
        expected = 3 if group[0]["sport_family"] == "soccer" else 2
        if len(valid) != expected or len({s["token_id"] for s in valid}) != expected:
            continue
        expected_results = {"HOME", "DRAW", "AWAY"} if expected == 3 else {"HOME", "AWAY"}
        if {s.get("result_kind") for s in valid} != expected_results:
            continue
        if previous is not None and failed_between(failures, previous, now):
            previous = now
            continue
        previous = now
        candidates = []
        for snap in valid:
            walk = buy_walk(snap["asks"])
            if not walk or not snap["entry_eligible"]:
                continue
            price, shares, worst = walk
            if threshold - 1e-9 <= price <= cap + 1e-9 and worst <= cap + 1e-9:
                candidates.append((price, snap["token_id"], snap, shares))
        if not candidates:
            continue
        price, _, entered, shares = sorted(candidates, key=lambda x: (-x[0], x[1]))[0]
        sold_shares = math.floor((shares + 1e-10) * 100) / 100
        record = {"event": entered["event_id"], "title": entered["title"],
                  "token": entered["token_id"], "condition": entered["condition_id"],
                  "outcome": entered["outcome"], "entry": entered["timestamp"],
                  "entry_vwap": price, "bought_shares": shares, "sold_shares": sold_shares,
                  "excluded_dust_shares": shares - sold_shares,
                  "allocated_principal": price * sold_shares,
                  "depth_blocked_observations": 0, "exit_reason": "right_censored",
                  "exit": None, "exit_vwap": None, "exit_worst": None,
                  "modeled_net_usdc": None}
    if record:
        proofs = terminals.get((record["condition"], record["token"]), [])
        proofs = [p for p in proofs if visual.timestamp(p["observed_at"]) >= visual.timestamp(record["entry"])]
        payout = proofs[0]["payout"] if proofs and len({p["payout"] for p in proofs}) == 1 else None
        # Settlement-only hold is identifiable despite book gaps. It is a
        # separate diagnostic, never the gap-censored protective-stop baseline.
        record["terminal_payout"] = payout
        record["settlement_only_net"] = (record["sold_shares"] * payout - record["allocated_principal"] * (1 + rate)) if payout is not None else None
        if record["exit_reason"] == "right_censored" and payout is not None:
            proof = min(proofs, key=lambda p: visual.timestamp(p["observed_at"]))
            resolution_time = visual.timestamp(proof["observed_at"])
            if resolution_time - previous <= max_gap and not failed_between(failures, previous, resolution_time):
                record.update(exit_reason="RESOLUTION", exit=proof["observed_at"], exit_vwap=payout,
                              modeled_net_usdc=record["settlement_only_net"])
        record["bound_lower"] = -record["allocated_principal"] * (1 + rate)
        record["bound_upper"] = record["sold_shares"] - record["allocated_principal"] * (1 + rate)
    return record


def analyze(path, start, end, max_gap):
    path = path.resolve()
    manifest = json.loads((path.parent / "manifest.json").read_text())
    checksum = visual.sha256(path)
    if checksum != manifest["sha256"] or manifest.get("quick_check") != ["ok"]:
        raise ValueError("source does not match its verified online-backup manifest")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    white = "research_run_events" in tables
    runs, configs = visual.read_runs(conn, white), visual.read_configs(conn, white)
    original_asks = WhiteOriginalAskEvidence(conn) if white else None
    terminals = visual.terminal_records(conn, white, runs)
    terminals = {key: [p for p in rows if visual.timestamp(p["observed_at"]) < visual.timestamp(end)] for key, rows in terminals.items()}
    meta = {}
    if white:
        for r in conn.execute("""SELECT DISTINCT m.run_id,m.condition_id,m.liquidity,m.volume_total,m.event_live,m.event_ended
          FROM market_observations m JOIN outcome_observations o ON o.market_observation_id=m.observation_id
          JOIN orderbook_snapshots b ON b.run_id=o.run_id AND b.token_id=o.token_id
          WHERE b.observed_at>=? AND b.observed_at<? AND m.eligible=1""", (start, end)):
            meta[(r["run_id"], r["condition_id"])] = dict(r)
    groups = defaultdict(lambda: defaultdict(list))
    invalid = Counter()
    for row in (visual.white_rows(conn, start, end) if white else visual.trading_rows(conn, start, end)):
        if row["sport_family"] == "soccer" and str(row.get("outcome_side", "")).upper() != "YES":
            continue  # Cat/Dog soccer buys real YES, never synthetic NO.
        run = runs.get(row["run_id"], {})
        config_hash = run.get("config_hash", "unknown")
        cfg = configs.get(config_hash, {})
        valid = run.get("status") == "SUCCESS" and bool(cfg.get("strategy_source_digest"))
        valid = valid and row.get("config_hash", config_hash) == config_hash
        valid = valid and row.get("strategy_source_digest", cfg.get("strategy_source_digest")) == cfg.get("strategy_source_digest")
        if not valid:
            invalid["run_or_lineage_gap"] += 1
        asks, bids = levels(row["book"], "asks"), levels(row["book"], "bids")
        if str(row["book"].get("token_id", row["token_id"])) != str(row["token_id"]):
            valid = False
        m = meta.get((row["run_id"], row["condition_id"]), {})
        if white:
            open_at_observation = m.get("event_live") == 1 and m.get("event_ended") == 0
            entry_eligible = open_at_observation and (m.get("liquidity") or 0) >= 5000 and (m.get("volume_total") or 0) >= 5000
        else:
            # Gold/Plum has a different discovery envelope and does not keep a
            # point-in-time cumulative-volume gate in each snapshot.
            entry_eligible = False
            open_at_observation = False
            invalid["missing_catdog_point_in_time_cumulative_volume_gate"] += 1
        asks_state = ask_side_state(row["book"])
        if white and row["best_bid"] is not None and row["best_bid"] >= .99:
            asks_state = original_asks.state(row["id"], row["token_id"])
        snapshot = {**row, "time": visual.timestamp(row["timestamp"]), "valid": valid,
                    "asks": asks, "bids": bids, "entry_eligible": entry_eligible,
                    "ask_state": asks_state,
                    "open_at_observation": open_at_observation,
                    "spread": row["best_ask"] - row["best_bid"] if row["best_ask"] is not None and row["best_bid"] is not None else None}
        snapshot.pop("book", None)
        groups[(row["sport_family"], config_hash, row["event_id"])][row["run_id"]].append(snapshot)
    rows = []
    cohorts = {}
    for (sport, config_hash, event), by_run in groups.items():
        cohort_key = f"{sport}/{config_hash}"
        cohorts.setdefault(cohort_key, {"sport": sport, **configs.get(config_hash, {}), "events": []})["events"].append(event)
        failures = [(visual.timestamp(r["started_at"]), visual.timestamp(r["finished_at"])) for r in runs.values() if r.get("config_hash") == config_hash and r["status"] != "SUCCESS"]
        ordered = sorted(by_run.values(), key=lambda g: min(s["time"] for s in g))
        for threshold in (0.95, 0.96, 0.97, 0.99):
            for target in (None, 0.99) if threshold in (0.95, 0.97) else (None,):
                for fee in (0, 25, 100):
                    result = replay_event(ordered, terminals, failures, threshold, target, fee, max_gap)
                    if result:
                        rows.append({"source": str(path), "sport": sport, "config_hash": config_hash,
                                     "entry_threshold": threshold, "target": target,
                                     "fee_bps_per_side": fee, **result})
    conn.close()
    if visual.sha256(path) != checksum:
        raise ValueError("source changed during replay")
    return {"path": str(path), "sha256": checksum, "synced_at": manifest.get("synced_at"),
            "source_contract": "White same-minute metadata" if white else "Gold diagnostic: Cat/Dog entry gate missing, no trades synthesized",
            "original_ask_audit": dict(original_asks.audit) if original_asks else None,
            "invalid_rows": dict(invalid), "cohorts": list(cohorts.values()), "outcomes": rows}


def report(payload, output):
    all_rows = [r for source in payload["sources"] for r in source["outcomes"]]
    cells = defaultdict(list)
    for row in all_rows:
        cells[(row["sport"], row["config_hash"], row["entry_threshold"], row["target"], row["fee_bps_per_side"])].append(row)
    summaries = []
    for (sport, cohort, threshold, target, fee), rows in cells.items():
        known = [r for r in rows if r["modeled_net_usdc"] is not None]
        unknown = [r for r in rows if r["modeled_net_usdc"] is None]
        total = sum(r["modeled_net_usdc"] for r in known)
        summaries.append(dict(sport=sport, cohort=cohort, entry=threshold, target=target, fee=fee,
                              entered=len(rows), complete=len(known), censored=len(unknown),
                              known_net=total if known else None,
                              lower=total+sum(r["bound_lower"] for r in unknown),
                              upper=total+sum(r["bound_upper"] for r in unknown),
                              reasons=dict(Counter(r["exit_reason"] for r in rows))))
    pairs = []
    for (sport, cohort, threshold, target, fee), rows in cells.items():
        if target is None:
            continue
        holds = {r["event"]: r for r in cells[(sport, cohort, threshold, None, fee)]}
        eligible = [(r, holds[r["event"]]) for r in rows if r["modeled_net_usdc"] is not None and holds[r["event"]]["modeled_net_usdc"] is not None]
        pairs.append(dict(sport=sport, cohort=cohort, entry=threshold, fee=fee, paired_complete=len(eligible),
                          hold_net=sum(b["modeled_net_usdc"] for a,b in eligible) if eligible else None,
                          tp_net=sum(a["modeled_net_usdc"] for a,b in eligible) if eligible else None,
                          delta=sum(a["modeled_net_usdc"]-b["modeled_net_usdc"] for a,b in eligible) if eligible else None))
    settlement_diagnostics = []
    for (sport, cohort, threshold, target, fee), rows in cells.items():
        if target is None:
            continue
        known = [r for r in rows if r["exit_reason"] == "TP" and r["settlement_only_net"] is not None]
        if known:
            settlement_diagnostics.append(dict(sport=sport, cohort=cohort, entry=threshold, fee=fee,
                tp_then_resolution_count=len(known), payouts=dict(Counter(r["terminal_payout"] for r in known)),
                tp_net=sum(r["modeled_net_usdc"] for r in known),
                pure_settlement_net=sum(r["settlement_only_net"] for r in known)))
    payload.update(summaries=summaries, paired=pairs, tp_then_resolution_diagnostic=settlement_diagnostics)
    output.mkdir(parents=True, exist_ok=True)
    (output/"results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    if all_rows:
        with (output/"event_outcomes.csv").open("w") as file:
            writer=csv.DictWriter(file, fieldnames=list(all_rows[0]));writer.writeheader();writer.writerows(all_rows)
    lines = ["# Cat/Dog .99 익절 paired 반사실", "", f"UTC [{payload['start']}, {payload['end_exclusive']})", "",
      "실제 체결 성과가 아닌 표시호가 연구다. fee는 0/25/100 bps **매수·매도 각 측** 민감도이며 실측 fee가 아니다. 동일 event도 다른 config/source에서는 별도 cohort다. 보유 중 90초 초과 공백·FAILED run은 즉시 검열하고 0손익으로 채우지 않는다.", "",
      "진입은 한 event/cohort의 첫 완전한 3개 축구 YES 또는 2개 direct 결과 관측 중 $5 ask VWAP가 band를 만족하는 1건이다. 여러 token이 동시 후보면 가장 높은 ask, 다음 token ID 순으로 고른다. 후보 .95/.97 상한은 .989, 기존 .96/.99 상한은 .999. 모두 0.70 stop을 보존한다. TP는 관측 당시 event_live=1/event_ended=0, 전량 표시 bid의 최악 가격 .99 이상, 전체 원가 기준 양수를 요구한다. ask가 있으면 finite spread [0,.10]이 필요하며, 정상 빈 asks 목록의 bid-only book은 허용한다. 누락·malformed·NaN asks는 빈 목록으로 바꾸지 않는다. 0.01주 내림 잔여는 모든 정책의 비교 손익에서 동일하게 제외한다. 동일분 종목·gate·depth를 확인하지만 live의 독립 Gamma+CLOB dual OPEN proof·주문 전 재확인·지연·FOK 거절·계좌 한도·반대결과 재진입을 재현하지 않는다.", "",
      "## 동일 entry의 hold 대 TP — 둘 다 완결한 경기만", "", "|종목|cohort|진입|fee/측 bps|paired 완결|hold|TP|차이|", "|---|---|---:|---:|---:|---:|---:|---:|"]
    def n(x): return "미확정" if x is None else f"{x:.6f}"
    for p in pairs:
        lines.append(f"|{p['sport']}|{p['cohort'][:10]}|{p['entry']}|{p['fee']}|{p['paired_complete']}|{n(p['hold_net'])}|{n(p['tp_net'])}|{n(p['delta'])}|")
    lines += ["", "## 각 정책의 완결 부분과 전체 구간", "", "|종목|cohort|entry|TP|fee|진입/완결/검열|확인 부분|보수적 전체 하한|전체 상한|", "|---|---|---:|---|---:|---|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"|{s['sport']}|{s['cohort'][:10]}|{s['entry']}|{s['target']}|{s['fee']}|{s['entered']}/{s['complete']}/{s['censored']}|{n(s['known_net'])}|{n(s['lower'])}|{n(s['upper'])}|")
    lines += ["", "TP 제출 조건은 dust 잔여가 0을 지급해도 **전체 $5 매수 원가·전체 매수 fee·매도 fee·$0.001 reserve를 회수**하는 순익 하한이다. 위 표시 손익은 실제 ledger와 같은 매도 가능 수량 부분만으로 계산하므로 제출 전 전체 원가 하한과 구분한다.", "", "## TP 뒤 terminal payout도 확인된 부분의 진단", "", "손절 없이 해결만 기다리는 가상 정책과 비교한 추가 진단이다. **현재 stop 정책 대조군이 아니며, TP에 도달하고 해결 증거도 있는 선택된 부분만** 포함한다. 미완결 거래나 전체 기회 수에 일반화하지 않는다.", "", "|종목|cohort|entry|fee|TP+해결 확인|TP 순익|순수 해결 보유 순익|", "|---|---|---:|---:|---:|---:|---:|"]
    for s in settlement_diagnostics:
        lines.append(f"|{s['sport']}|{s['cohort'][:10]}|{s['entry']}|{s['fee']}|{s['tp_then_resolution_count']}|{n(s['tp_net'])}|{n(s['pure_settlement_net'])}|")
    lines += ["", "0.95/0.97은 사용자가 제안한 사전 후보다. 여기서 최적값·흑자 전환·심리적 원인을 입증하지 않는다. 완결 부분만의 합계는 전체 전략 수익률이 아니다. Gold의 snapshot에는 Cat/Dog의 해당 시점 cumulative-volume gate가 없어 strict 재생 진입을 만들지 않으며, Gold의 부재는 0손익이 아니다.", ""]
    (output/"REPORT.md").write_text("\n".join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-gap-seconds", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    if visual.timestamp(args.start) >= visual.timestamp(args.end) or args.max_gap_seconds <= 0:
        parser.error("invalid fixed range or gap")
    sources=[]
    for path in args.db:
        sources.append(analyze(path,args.start,args.end,args.max_gap_seconds))
        print(json.dumps({"source":str(path),"outcomes":len(sources[-1]["outcomes"])}),flush=True)
    report({"start":args.start,"end_exclusive":args.end,"max_gap_seconds":args.max_gap_seconds,"sources":sources},args.output)


if __name__ == "__main__":
    main()
