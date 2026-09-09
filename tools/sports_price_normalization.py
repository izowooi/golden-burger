"""S and proportional normalization of aligned, caller-verified outcome books.

Quotes are not calibrated event probabilities. Same-share bundle ask/bid sums
are distinct from sums of separately spending $5 on each outcome. This module
does not choose trades, place orders, or interpret an ask overround as profit.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

DIRECT_SPORTS = {"mlb", "nba", "nfl", "nhl"}
BASES = ("ask", "bid", "mid", "depth_ask", "depth_bid")


def decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid numeric value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("invalid numeric value") from error
    if not result.is_finite():
        raise ValueError("non-finite numeric value")
    return result


def levels(book, side, token):
    if not isinstance(book, dict) or not isinstance(book.get(side), list):
        raise ValueError("BOOK_SIDE_MISSING")
    identity = book.get("asset_id", book.get("token_id"))
    if identity is not None and str(identity) != str(token):
        raise ValueError("BOOK_TOKEN_MISMATCH")
    result, seen = [], set()
    for raw in book[side]:
        if not isinstance(raw, dict):
            raise ValueError("MALFORMED_LEVEL")
        p, q = decimal(raw.get("price")), decimal(raw.get("size"))
        if not 0 < p <= 1 or q <= 0 or p in seen:
            raise ValueError("INVALID_OR_DUPLICATE_LEVEL")
        seen.add(p)
        result.append((p, q))
    return sorted(result, reverse=side == "bids")


def share_walk(orders, quantity):
    left, cash, consumed = quantity, Decimal(0), []
    for price, size in orders:
        take = min(left, size)
        consumed.append((price, take))
        cash += price * take
        left -= take
        if left == 0:
            return {"vwap": cash / quantity, "cash": cash, "levels": consumed}
    return None


def fee_rate(market):
    if not isinstance(market, dict):
        return None
    if market.get("feesEnabled") is False:
        return Decimal(0)
    schedule = market.get("feeSchedule")
    if (market.get("feesEnabled") is not True or not isinstance(schedule, dict)
            or isinstance(schedule.get("exponent"), bool) or schedule.get("exponent") != 1
            or schedule.get("takerOnly") is not True):
        return None
    try:
        rate = decimal(schedule.get("rate"))
    except ValueError:
        return None
    return rate if 0 <= rate <= 1 else None


def fee(walk, rate):
    if walk is None or rate is None:
        return None
    return sum((q * p * (1-p) * rate).quantize(Decimal(".00001"), rounding=ROUND_HALF_UP)
               for p, q in walk["levels"])


def summarize_group(tokens, observations, sport, *, common_shares=5, max_skew_seconds=2,
                    bbo_only=False):
    """Inputs are one verified event/cohort/run; unknown/missing inputs stay null.

    token: token_id, condition_id, outcome_side, result_kind, label; optionally
      partition_role=TEAM_A/DRAW/TEAM_B when source proves the partition but not venue.
    observation: token_id, t (UTC epoch seconds), run_id, book, valid (bool),
      market_open (True/False/None), optional fee_market/event_id/cohort_id.
    signal_eligible is quote-set/depth/open evidence only, not profitability or
    permission to trade. Fee coverage has its own fields. Decimal output strings
    preserve exact sums for threshold comparisons.
    bbo_only=True accepts book.best_ask/best_bid prices without sizes. It never
    supplies depth, fee-adjusted bundle values or execution eligibility.
    """
    with localcontext() as context:
        context.prec = 42
        return _summarize(tokens, observations, sport, decimal(common_shares),
                          decimal(max_skew_seconds), bbo_only=bbo_only)


def _summarize(tokens, observations, sport, quantity, tolerance, *, bbo_only=False):
    result = {"schema": "sports-normalized-quotes-v1", "sport": sport,
              "common_shares": None if bbo_only else str(quantity), "max_skew_seconds": str(tolerance),
              "quote_depth_basis": "BBO_ONLY_NO_SIZE" if bbo_only else "RECORDED_DEPTH",
              "valid": False, "signal_eligible": False, "reasons": [],
              "fee_evidence_complete": False, "cost_evidence_eligible": False,
              "s_best_ask": None, "s_best_bid": None, "s_mid": None,
              "s_ask": None, "s_bid": None, "s_ask_with_fees": None,
              "s_bid_after_fees": None, "deviation": {}, "normalized": {},
              "total_top_spread": None, "ask_excess_from_mid": None,
              "ask_excess_from_spread": None,
              "no_reference": {}, "values": {}, "missing_basis": {},
              "semantics": "NORMALIZED_MARKET_QUOTES_NOT_CALIBRATED_PROBABILITIES"}
    if quantity <= 0 or tolerance < 0:
        raise ValueError("positive common shares and nonnegative skew are required")
    if sport == "soccer":
        members = [t for t in tokens if t.get("outcome_side") == "YES"]
        roles = {t.get("partition_role", t.get("result_kind")) for t in members}
        shape = (len(members) == 3 and roles in ({"HOME", "DRAW", "AWAY"}, {"TEAM_A", "DRAW", "TEAM_B"})
                 and len({t.get("condition_id") for t in members}) == 3)
        members.sort(key=lambda t: str(t.get("partition_role", t.get("result_kind"))))
    elif sport in DIRECT_SPORTS:
        members = [t for t in tokens if t.get("outcome_side") == "DIRECT"]
        shape = (len(members) == 2 and len({t.get("condition_id") for t in members}) == 1
                 and len({t.get("label", t.get("outcome")) for t in members}) == 2)
    else:
        members, shape = [], False
    ids = [str(t.get("token_id") or "") for t in members]
    if (not shape or not all(ids) or len(set(ids)) != len(ids)
            or any(not t.get("condition_id") for t in members)):
        result["reasons"].append("INCOMPLETE_OR_AMBIGUOUS_OUTCOME_SET")
        return result
    selected = [o for o in observations if str(o.get("token_id")) in ids]
    if len(selected) != len(ids) or len({str(o.get("token_id")) for o in selected}) != len(ids):
        result["reasons"].append("MISSING_OR_DUPLICATE_OBSERVATIONS")
        return result
    if any(o.get("valid") is not True for o in selected):
        result["reasons"].append("UNVERIFIED_OBSERVATION")
    runs = {o.get("run_id") for o in selected}
    if len(runs) != 1 or None in runs or "" in runs:
        result["reasons"].append("RUN_MISMATCH_OR_MISSING")
    for key in ("event_id", "cohort_id"):
        if any(key in o for o in selected) and (len({o.get(key) for o in selected}) != 1
                                               or any(not o.get(key) for o in selected)):
            result["reasons"].append(key.upper() + "_MISMATCH_OR_MISSING")
    try:
        times = [decimal(o.get("t")) for o in selected]
        skew = max(times) - min(times)
        result.update(t=str(max(times)), skew_seconds=str(skew))
        if skew > tolerance:
            result["reasons"].append("OBSERVATION_SKEW_EXCEEDED")
    except ValueError:
        result["reasons"].append("INVALID_OBSERVATION_TIME")
    if result["reasons"]:
        return result
    member_by_id = {str(t["token_id"]): t for t in members}
    walks, fees = {}, {}
    try:
        for observation in selected:
            token = str(observation["token_id"])
            book = observation["book"]
            market = book.get("market") if isinstance(book, dict) else None
            if market is not None and str(market) != str(member_by_id[token]["condition_id"]):
                raise ValueError("BOOK_CONDITION_MISMATCH")
            if bbo_only:
                if not isinstance(book, dict) or "asks" in book or "bids" in book:
                    raise ValueError("BBO_ONLY_REQUIRES_PRICE_FIELDS_WITHOUT_LEVELS")
                identity = book.get("asset_id", book.get("token_id"))
                if identity is not None and str(identity) != token:
                    raise ValueError("BOOK_TOKEN_MISMATCH")
                a = decimal(book["best_ask"]) if book.get("best_ask") is not None else None
                b = decimal(book["best_bid"]) if book.get("best_bid") is not None else None
                if any(p is not None and not 0 < p <= 1 for p in (a, b)):
                    raise ValueError("INVALID_BBO_PRICE")
                wa, wb = None, None
            else:
                asks, bids = levels(book, "asks", token), levels(book, "bids", token)
                a, b = asks[0][0] if asks else None, bids[0][0] if bids else None
                wa, wb = share_walk(asks, quantity), share_walk(bids, quantity)
            if a is not None and b is not None and b > a:
                raise ValueError("CROSSED_TOKEN_BOOK")
            walks[token] = {"ask": wa, "bid": wb}
            rate = fee_rate(observation.get("fee_market"))
            fees[token] = {"ask": fee(wa, rate), "bid": fee(wb, rate)}
            result["values"][token] = {"ask": a, "bid": b,
                "mid": (a+b)/2 if a is not None and b is not None else None,
                "depth_ask": wa["vwap"] if wa else None,
                "depth_bid": wb["vwap"] if wb else None}
    except (ValueError, KeyError, TypeError, ArithmeticError) as error:
        result["reasons"].append("INVALID_BOOK:" + str(error))
        result["values"] = {}
        return result
    sums = {}
    for basis in BASES:
        missing = [token for token in ids if result["values"][token][basis] is None]
        result["missing_basis"][basis] = missing
        sums[basis] = None if missing else sum(result["values"][t][basis] for t in ids)
    mapping = {"ask": "s_best_ask", "bid": "s_best_bid", "mid": "s_mid",
               "depth_ask": "s_ask", "depth_bid": "s_bid"}
    for basis, key in mapping.items():
        total = sums[basis]
        result[key] = str(total) if total is not None else None
        result["deviation"][key] = str(total - 1) if total is not None else None
    if sums["ask"] is not None and sums["bid"] is not None:
        result["total_top_spread"] = str(sums["ask"] - sums["bid"])
        result["ask_excess_from_mid"] = str(sums["mid"] - 1)
        result["ask_excess_from_spread"] = str((sums["ask"] - sums["bid"]) / 2)
    for token in ids:
        result["normalized"][token] = {
            basis: str(result["values"][token][basis] / sums[basis])
            if sums[basis] is not None and sums[basis] > 0 else None for basis in BASES}
    for side, key, sign in (("ask", "s_ask_with_fees", 1), ("bid", "s_bid_after_fees", -1)):
        if all(walks[t][side] is not None and fees[t][side] is not None for t in ids):
            result[key] = str(sum(walks[t][side]["cash"] + sign*fees[t][side] for t in ids) / quantity)
    if sport == "soccer":
        yes_by_condition = {str(t["condition_id"]): str(t["token_id"]) for t in members}
        for token in tokens:
            if token.get("outcome_side") != "NO":
                continue
            yes = yes_by_condition.get(str(token.get("condition_id")))
            if yes and token.get("token_id"):
                result["no_reference"][str(token["token_id"])] = {
                    b: str(1 - decimal(v)) if v is not None else None
                    for b, v in result["normalized"][yes].items()}
    result["values"] = {t: {k: str(v) if v is not None else None for k, v in row.items()}
                        for t, row in result["values"].items()}
    result["valid"] = True
    result["signal_eligible"] = (sums["depth_ask"] is not None and sums["depth_bid"] is not None
                                 and all(o.get("market_open") is True for o in selected))
    result["fee_evidence_complete"] = (result["s_ask_with_fees"] is not None
                                       and result["s_bid_after_fees"] is not None)
    result["cost_evidence_eligible"] = result["signal_eligible"] and result["fee_evidence_complete"]
    result["market_open_unknown"] = any(o.get("market_open") is None for o in selected)
    result["market_closed_observed"] = any(o.get("market_open") is False for o in selected)
    result["members"] = ids
    result["run_id"] = next(iter(runs))
    return result
