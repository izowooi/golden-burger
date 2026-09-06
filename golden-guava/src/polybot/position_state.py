"""Policy-free Decimal accounting; no SDK, wallet, clock, DB or broker import.

Input: COMPLETE Broker.execution_inventory() batch (full _snapshot dictionaries)
from ONE Guava ledger: envelope, fills, aggregate/proof flags and reservation. Caller
must attest inventory_complete=True and provide the independent store's complete
expected_submission_ids (including orphans), explicit SELL-id -> BUY-id links,
cycle_buy_ids and optional unresolved_age_minutes. Never pass reservation-only
rows, wallet positions, overlapping historical versions or silently omit errors.
The caller owns coherent reads, provenance and durable parent links; a hash alone
is not source authentication. This reducer cannot discover an omitted DB order.

PositionStateError is a GLOBAL fail-closed result: never replace it with an empty
state. Valid uncertainty blocks its event OR token, not unrelated events. A BUY
owns quantity only after terminal quantity reconciliation, even if fees remain
unknown. SELLs reduce only exact CONFIRMED quantities with transaction evidence;
remaining NONTERMINAL SELL quantities stay reserved. Terminal fee gaps block BUY
but cannot make canceled quantities fillable again. No elapsed time invents a fill.

One active slot per economic BUY lot, never lot + reservation twice. Exact linked
end-SELL dust (<.01 shares, all reconciled/fees known) releases the trading slot,
NOT the economic position/cost. Other uncertainty retains the entire BUY cap.
Known remaining cost is allocated proportionally and rounded UP to a USDC micro;
the broker's +1 micro BUY notional tolerance is retained as observed excess, never
clipped back to the signed cap. Share dust is never rounded away. Closed BUYs consume no open slots,
but still consume their explicitly supplied cycle budget. No P&L/payout inference.

Limits/capacity are strict NOTIONAL/slot bounds, EXCLUDING fees, wallet cash,
mark-to-market loss, venue minima and strategy approval. Those remain runner
gates. fits() is accounting feasibility, NEVER authorization to submit an order.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
import hashlib
import json
import re
from typing import Mapping

D = Decimal
ZERO, MICRO, SHARE = D(0), D("0.000001"), D("0.0001")
TICKS = {"0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"}
BUY_AMOUNT_FIELDS = {"buy_amount_contract", "native_tick", "signer_tick", "buy_quantity_precision", "minimum_taker_amount"}
TERMINAL = {"MATCHED", "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "INVALID"}
Key = tuple[str, str, str]


class PositionStateError(ValueError):
    """Unbounded/unattributable/inconsistent evidence: deny all new execution."""


def _check(condition, code):
    if not condition:
        raise PositionStateError(code)


def _d(value, positive=False):
    _check(not isinstance(value, bool) and isinstance(value, (str, int, float, D)), "numeric type")
    try:
        result = D(str(value))
    except InvalidOperation:
        raise PositionStateError("invalid decimal") from None
    _check(result.is_finite() and ZERO <= result <= D("1e18"), "finite nonnegative decimal required")
    _check(not positive or result > 0, "positive decimal required")
    return result


def _id(value):
    _check(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value), "public ID required")
    return value


def _flag(row, key):
    _check(type(row[key]) is bool, "boolean flag required")
    return row[key]


def _sum(values):
    with localcontext() as ctx:
        ctx.prec = 60
        return sum(values, ZERO)


@dataclass(frozen=True)
class Limits:
    max_positions: int
    max_notional_usdc: Decimal
    max_event_positions: int
    max_event_notional_usdc: Decimal
    max_cycle_positions: int
    max_cycle_notional_usdc: Decimal
    max_order_notional_usdc: Decimal

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name.endswith("positions"):
                _check(type(value) is int and value >= 0, "nonnegative integer slot limit required")
            else:
                object.__setattr__(self, f.name, _d(value))
        _check(self.max_order_notional_usdc <= 100, "supported order cap is at most 100")


@dataclass(frozen=True)
class Capacity:
    notional_usdc: Decimal
    position_slots: int
    reasons: tuple[str, ...]

    def fits(self, notional):
        with localcontext() as ctx:
            ctx.prec = 60
            amount = _d(notional, positive=True)
            return not self.reasons and self.position_slots > 0 and 5 <= amount <= self.notional_usdc and amount % D("0.01") == 0


@dataclass(frozen=True)
class Lot:
    buy_id: str
    key: Key
    observed_confirmed_buy_shares: Decimal
    observed_buy_notional_usdc: Decimal
    buy_rounding_excess_usdc: Decimal
    owned_shares: Decimal | None
    sell_reserved_shares: Decimal
    remaining_cost_usdc: Decimal | None
    notional_usdc: Decimal
    fee_usdc: Decimal | None
    uncertain: bool
    label: str

    @property
    def active_slot(self):
        return self.label not in {"CLOSED", "DUST"}

    @property
    def economic_open(self):
        return self.owned_shares is None or self.owned_shares > 0 or self.notional_usdc > 0 or self.uncertain

    @property
    def protected_sell_available_shares(self):
        """Unreserved confirmed quantity, not venue/policy/POST authorization."""
        if self.owned_shares is None:
            return ZERO
        return max(ZERO, _sum([self.owned_shares, -self.sell_reserved_shares]))


@dataclass(frozen=True)
class PositionState:
    lots: tuple[Lot, ...]
    limits: Limits
    cycle_buy_ids: frozenset[str]
    cycle_notional_usdc: Decimal
    cycle_positions: int

    @property
    def holdings(self):
        result = {}
        for lot in self.lots:
            prior = result.get(lot.key, ZERO)
            result[lot.key] = None if prior is None or lot.owned_shares is None else _sum([prior, lot.owned_shares])
        return result

    @property
    def notional_usdc(self):
        return _sum(lot.notional_usdc for lot in self.lots)

    @property
    def active_position_count(self):
        return sum(lot.active_slot for lot in self.lots)

    @property
    def dust_count(self):
        return sum(lot.label == "DUST" for lot in self.lots)

    @property
    def open_economic_count(self):
        return sum(lot.economic_open for lot in self.lots)

    def capacity(self, event_id, token_id):
        _id(event_id); _id(token_id)
        with localcontext() as ctx:
            ctx.prec = 60
            event = [p for p in self.lots if p.key[0] == event_id]
            limits = self.limits
            slots = min(limits.max_positions - self.active_position_count,
                        limits.max_event_positions - sum(p.active_slot for p in event),
                        limits.max_cycle_positions - self.cycle_positions)
            amount = min(limits.max_order_notional_usdc, limits.max_notional_usdc - self.notional_usdc,
                         limits.max_event_notional_usdc - _sum(p.notional_usdc for p in event),
                         limits.max_cycle_notional_usdc - self.cycle_notional_usdc)
            reasons = set()
            for p in self.lots:
                if p.uncertain and p.key[0] == event_id:
                    reasons.add("EVENT_UNCERTAIN")
                if p.uncertain and p.key[2] == token_id:
                    reasons.add("TOKEN_UNCERTAIN")
            if slots <= 0 or amount < 5:
                reasons.add("LIMIT")
            return Capacity(ZERO if reasons - {"LIMIT"} else max(ZERO, amount).quantize(D("0.01"), rounding=ROUND_FLOOR),
                            max(0, slots), tuple(sorted(reasons)))


@dataclass(frozen=True)
class _Order:
    sid: str
    key: Key
    side: str
    cap: Decimal
    signed: Decimal
    requested: Decimal
    residual: Decimal
    observed: Decimal
    actual: Decimal
    gross: Decimal
    complete: bool
    fee: Decimal | None
    uncertain: bool
    released: bool


def _order(s):
    sid, env = _id(s["submission_id"]), s["envelope"]
    digest = hashlib.sha256(json.dumps(env, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    _check(digest == s["envelope_sha256"], "envelope hash mismatch")
    key = tuple(_id(env[k]) for k in ("event_id", "condition_id", "token_id"))
    _id(env["decision_id"])
    side = env["side"]
    _check(env["strategy_name"] == "golden-guava" and env["schema"] == "guava-signed-envelope-v1" and env["order_type"] == "FOK" and side in {"BUY", "SELL"}, "ownership/schema")
    maker, taker = _d(env["maker_amount"], True), _d(env["taker_amount"], True)
    signed, requested, residual = _d(env["signed_shares"], True), _d(env["requested_quantity"], True), _d(env["sell_residual_shares"])
    price, limit, tick = _d(env["limit_price"], True), _d(env["requested_limit_price"], True), _d(env["tick_size"], True)
    _check(str(tick) in TICKS and tick <= price <= 1 - tick and price % tick == 0 and limit < 1, "tick/limit")
    _check(maker == maker.to_integral_value() and taker == taker.to_integral_value(), "integer signed units")
    if side == "BUY":
        quantum = SHARE  # Unmarked historical envelopes keep original precision.
        if BUY_AMOUNT_FIELDS & env.keys():
            _check(BUY_AMOUNT_FIELDS <= env.keys(), "partial BUY arithmetic metadata")
            signer_tick, precision = _d(env["signer_tick"], True), env["buy_quantity_precision"]
            _check(env["buy_amount_contract"] == "guava-decimal-buy-v1" and _d(env["native_tick"]) == tick
                   and str(signer_tick) in TICKS and signer_tick >= tick and signer_tick % tick == 0
                   and price % signer_tick == 0 and type(precision) is int
                   and precision == (3 if signer_tick == D("0.1") else 4), "BUY arithmetic metadata")
            quantum = D(1).scaleb(-precision)
            minimum = (requested / limit).quantize(quantum, rounding=ROUND_FLOOR) / MICRO
            _check(_d(env["minimum_taker_amount"], True) == minimum and taker >= minimum, "BUY minimum quantity bound")
        _check(5 <= requested <= 100 and maker * MICRO == requested and maker % 10000 == 0 and taker % 100 == 0 and signed == taker * MICRO and signed == (requested / price).quantize(quantum, rounding=ROUND_FLOOR) and residual == 0 and price <= limit, "invalid BUY cap")
    else:
        _check(not (BUY_AMOUNT_FIELDS & env.keys()), "SELL cannot carry BUY arithmetic metadata")
        _check(maker % 10000 == 0 and signed == maker * MICRO and requested - signed == residual and 0 <= residual < D("0.01") and 0 <= taker * MICRO - signed * price < MICRO and price >= limit, "invalid SELL cap")
    for top, expected in (("signed_maker_amount", maker), ("signed_taker_amount", taker), ("signed_shares", signed), ("sell_residual_shares", residual)):
        _check(_d(s[top]) == expected, "signed summary mismatch")
    complete, no_post, zero = _flag(s, "execution_complete"), _flag(s, "no_post_proven"), _flag(s, "zero_fill_proven")
    for k in ("no_post", "submission_outcome_unknown", "exposure_unknown", "partial_terminal", "new_risk_allowed"):
        _flag(s, k)
    _check(not (no_post and zero), "conflicting release proofs")
    _check(isinstance(s["fills"], list), "full snapshot fills required")
    observed = actual = gross = fees = ZERO
    fee_known, buckets, all_confirmed = True, set(), True
    for fill in s["fills"]:
        bucket = fill["bucket_index"]
        _check(type(bucket) is int and bucket >= 0, "fill bucket")
        identity = (_id(fill["trade_id"]), bucket)
        _check(identity not in buckets and fill["side"] == side, "duplicate/wrong-side fill")
        buckets.add(identity)
        valid = fill["status"] == "CONFIRMED" and not fill["domain_error"]
        all_confirmed = all_confirmed and valid and bool(fill["transaction_hash"])
        if not valid:
            continue
        qty, px = _d(fill["size"], True), _d(fill["price"], True)
        _check(qty % MICRO == 0 and px < 1 and fill["liquidity_role"] == "TAKER", "actual fill domain")
        observed += qty; gross += qty * px
        if fill["transaction_hash"]:
            _id(fill["transaction_hash"]); actual += qty
        if fill["fee_amount_usdc"] is None:
            fee_known = False
        else:
            fees += _d(fill["fee_amount_usdc"])
    has_confirmed = observed > 0
    _check(s["confirmed_shares"] is None if not has_confirmed and not zero else _d(s["confirmed_shares"]) == observed, "confirmed quantity summary")
    _check(s["confirmed_notional_usdc"] is None if not has_confirmed and not zero else _d(s["confirmed_notional_usdc"]) == gross, "confirmed notional summary")
    if has_confirmed:
        _check(abs(_d(s["confirmed_vwap"], True) - gross / observed) < D("1e-20"), "VWAP summary")
    expected_fee = fees if (has_confirmed and fee_known) or zero else None
    _check(s["fee_status"] == ("KNOWN" if expected_fee is not None else "UNKNOWN") and (s["fee_usdc"] is None if expected_fee is None else _d(s["fee_usdc"]) == expected_fee), "fee summary")
    _check(gross <= maker * MICRO + MICRO if side == "BUY" else observed <= signed, "actual exceeds signed cap/tolerance")
    if no_post:
        _check(s["no_post"] and s["order_id"] is None and not s["fills"] and not complete and s["status"] == "INTENT" and not s["submission_outcome_unknown"], "NO_POST proof conflict")
    if complete:
        _id(s["order_id"])
        _check(not s["no_post"] and not s["submission_outcome_unknown"] and not s["exposure_unknown"] and s["status"] in TERMINAL, "terminal proof conflict")
        _check((zero and not s["fills"] and s["status"] != "MATCHED") or (has_confirmed and all_confirmed), "terminal CONFIRMED coverage")
        _check(_d(s["remaining_order_shares"]) == max(ZERO, signed - observed), "remaining order summary")
        _check(s["partial_terminal"] == (not zero and observed < signed - SHARE), "partial terminal summary")
        _check(s["status"] != "MATCHED" or observed + SHARE >= signed, "partial MATCHED not full proof")
    _check(not zero or complete, "zero requires terminal proof")
    uncertain = not (no_post or zero or (complete and expected_fee is not None))
    r = s["reservation"]
    for name, expected in (("submission_id", sid), ("side", side), ("envelope_sha256", digest), ("decision_id", env["decision_id"])):
        _check(r[name] == expected, "reservation identity")
    _check(tuple(r[k] for k in ("event_id", "condition_id", "token_id")) == key and _d(r["signed_shares"]) == signed, "reservation key/size")
    cap_key, unused = ("buy_notional_cap_usdc", "sell_shares_cap") if side == "BUY" else ("sell_shares_cap", "buy_notional_cap_usdc")
    _check(_d(r[cap_key]) == (maker * MICRO if side == "BUY" else signed) and r[unused] is None, "reservation cap")
    for name, expected in (("no_post_proven", no_post), ("reservation_required", not (no_post or zero)), ("execution_complete", complete), ("event_or_token_blocked", uncertain), ("runner_must_account", True), ("portfolio_approval", False), ("fee_budget_included", False)):
        _check(_flag(r, name) == expected, "reservation proof mismatch")
    _check(r["fee_status"] == s["fee_status"] and s["new_risk_allowed"] == (not uncertain), "reservation readiness mismatch")
    return _Order(sid, key, side, maker * MICRO if side == "BUY" else ZERO, signed, requested, residual, observed, actual, gross, complete, expected_fee, uncertain, no_post or zero)


def reduce_positions(snapshots, *, sell_to_buy: Mapping[str, str], expected_submission_ids,
                     inventory_complete: bool, limits: Limits, cycle_buy_ids=(), unresolved_age_minutes=None):
    """Recompute from complete evidence, not incremental wallet/P&L assumptions.

    Input/attribution errors RAISE globally. Capacity zero is a valid tight budget,
    not the representation of a failed read. Missing ages never infer expiry.
    sell_to_buy is SELL submission_id -> BUY submission_id, never token-only or
    decision IDs. Future storage must persist decision_id/parent_buy_decision_id
    and exact event/condition/token BEFORE POST, then join immutable envelope
    decision IDs after restart. This module neither writes nor infers that join.
    """
    try:
        with localcontext() as ctx:
            ctx.prec = 60
            _check(inventory_complete is True and isinstance(limits, Limits), "complete inventory/limits required")
            raw, orders, identities, decisions, venue_ids = {}, {}, {}, set(), set()
            for s in snapshots:
                sid = _id(s["submission_id"])
                if sid in raw:
                    _check(raw[sid] == s, "conflicting duplicate snapshot")
                    continue
                o = _order(s)
                for key, value in ((('token', o.key[2]), o.key), (('condition', o.key[1]), o.key[0])):
                    _check(key not in identities or identities[key] == value, "ambiguous ownership identity")
                    identities[key] = value
                decision = (s["envelope"]["decision_id"], o.side)
                _check(decision not in decisions, "duplicate economic decision")
                decisions.add(decision)
                if s["order_id"] is not None:
                    oid = _id(s["order_id"])
                    _check(oid not in venue_ids, "duplicate order attribution")
                    venue_ids.add(oid)
                raw[sid], orders[sid] = s, o
            _check(set(map(_id, expected_submission_ids)) == set(orders), "orphan/missing inventory")
            sells = {sid for sid, o in orders.items() if o.side == "SELL"}
            _check(isinstance(sell_to_buy, Mapping) and set(sell_to_buy) == sells, "exact SELL parent links required")
            children = {}
            for sid, parent in sell_to_buy.items():
                _check(parent in orders and orders[parent].side == "BUY" and orders[parent].key == orders[sid].key, "invalid SELL parent identity")
                children.setdefault(parent, []).append(orders[sid])
            age_input = {} if unresolved_age_minutes is None else unresolved_age_minutes
            _check(isinstance(age_input, Mapping), "age mapping required")
            ages = {_id(k): _d(v) for k, v in age_input.items()}
            _check(set(ages) <= set(orders), "unknown age submission")
            cycle = frozenset(map(_id, cycle_buy_ids))
            _check(cycle <= {sid for sid, o in orders.items() if o.side == "BUY"}, "cycle BUY inventory")
            lots = []
            for bid, buy in sorted(orders.items()):
                if buy.side != "BUY":
                    continue
                exits = [o for o in children.get(bid, []) if not o.released]
                _check(not exits or (buy.complete and not buy.released), "SELL without reconciled owned BUY")
                sold = _sum(o.actual for o in exits)
                reserved = _sum(o.signed - o.actual for o in exits if not o.complete)
                _check(sold + reserved <= buy.actual, "overSELL actual/reserved quantity")
                owned = ZERO if buy.released else buy.actual - sold if buy.complete else None
                cost = None if owned is None else (buy.gross * owned / buy.actual).quantize(MICRO, rounding=ROUND_CEILING) if buy.actual else ZERO
                involved = [buy, *exits]
                uncertain = any(o.uncertain for o in involved)
                observed_ceiling = buy.gross.quantize(MICRO, rounding=ROUND_CEILING)
                charge = max(buy.cap, observed_ceiling, cost or ZERO) if uncertain else cost or ZERO
                dust_proofs = [o for o in exits if o.complete and o.fee is not None and o.actual == o.signed
                               and o.residual == owned and o.requested == buy.actual - (sold - o.actual)]
                dust = not uncertain and owned is not None and 0 < owned < D("0.01") and len(dust_proofs) == 1
                label = ("QUARANTINED" if any(ages.get(o.sid, ZERO) >= 180 for o in involved if o.uncertain) else "PENDING") if uncertain else "DUST" if dust else "HOLDING" if owned else "CLOSED"
                fee = None if uncertain or any(o.fee is None for o in involved) else _sum(o.fee for o in involved)
                lots.append(Lot(bid, buy.key, buy.observed, buy.gross, max(ZERO, buy.gross - buy.cap),
                                owned, reserved, cost, charge, fee, uncertain, label))
            cycle_orders = [orders[sid] for sid in cycle if not orders[sid].released]
            cycle_charge = _sum(max(o.cap, o.gross.quantize(MICRO, rounding=ROUND_CEILING)) for o in cycle_orders)
            return PositionState(tuple(lots), limits, cycle, cycle_charge, len(cycle_orders))
    except PositionStateError:
        raise
    except (KeyError, TypeError, ValueError, InvalidOperation, ArithmeticError):
        raise PositionStateError("invalid full snapshot/link evidence; globally fail closed") from None
