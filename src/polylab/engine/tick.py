"""`polylab tick [--only ID] [--paper-all] [--dry-run]` — one strategy cycle for every variant.

Order per variant: reconcile pending live orders -> settle exact resolutions -> exits ->
entries (fresh-book re-check, risk caps, kill switch) -> execute (live FOK via CLOB or
paper broker) -> ledger. One variant failing never stops the others. Fail closed: a live
variant without credentials is skipped with an error; the kill switch blocks every new
entry (exits and reconciliation still run). --dry-run evaluates signals and logs decisions
but sends zero orders and writes no positions.

Maker execution (`order_style: maker`, polylab.execution.maker): after reconciliation and settlement, every
active resting GTC order of the leg is synced (CONFIRMED fills booked), and cancelled / re-quoted on window
close, kill switch, TTL or price drift; live legs first sweep the account's open orders (adopt our id-less
intents, cancel our own orphans, never touch others). Exits: a resting take-profit is left to work, a stop
cancels it first (confirmed by the venue) and sells the remainder as taker; an open position without one rests
its TP when the strategy allows. Entries: maker variants rest a post-only BUY inside the spread instead of a
FOK walk. Off variants still holding resting orders run a no-entry leg until those orders are terminal.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from polylab import db, registry, settings
from polylab.execution import clob as clobmod
from polylab.execution import maker as makermod
from polylab.execution.fees import FeeSchedule, parse_fee_schedule
from polylab.execution.ledger import StrategyLedger, open_ledger, row_to_view
from polylab.execution.paper import paper_buy, paper_sell
from polylab.execution.reconcile import reconcile_live, reconcile_order, settle_resolutions
from polylab.marketview import MarketView
from polylab.risk.caps import CapState, check_entry, clamp_stake, kill_switch_active, utc_day_start
from polylab.strategies import build
from polylab.marketview import EPS
from polylab.strategies.base import EntryIntent, ExitIntent, floor2
from polylab.strategies.plum import source_minute

JOB_NAME = "polylab-tick"
DEFAULT_SELLS_PER_CYCLE = 10
ENTRY_STATE_MAX_AGE_S = 1800              # game_states are written on change only (MLB innings can idle long)
PERSISTED_SKIPS = {"rapid_jump"}
MIN_REPRICE_MOVE = 0.01                   # maker re-quote tolerance floor (price points)          # permanent exclusions strategies must remember

_ADDR = re.compile(r"(?:0x)?[0-9a-fA-F]{40,}")
_QUERY = re.compile(r"(https?://[^\s?]+)\?\S*")


def sanitize(text: Any, limit: int = 400) -> str:
    """Error text ends up in public reports/dashboards: drop URL queries and addresses/keys."""
    s = _QUERY.sub(r"\1?…", str(text))
    s = _ADDR.sub("0x***", s)
    return s[:limit]


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(settings.REPO_ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


@dataclass
class VariantResult:
    variant_id: str
    mode: str
    sports: list | None = None           # entry sports of this leg (per-sport variants)
    ok: bool = True
    error: str | None = None
    reconciled: dict = field(default_factory=dict)
    resolved: int = 0
    exits: int = 0
    entries: int = 0
    candidates: int = 0
    skipped: dict = field(default_factory=dict)
    maker: dict = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class BookOverlay:
    """MarketView whose `book()` answers from fresh overrides first (live exit evaluation)."""

    def __init__(self, view: MarketView, books: dict):
        self._view = view
        self._books = books

    def book(self, token_id: str, now: int, max_age_s: int | None = 180):
        if token_id in self._books:
            return self._books[token_id]
        return self._view.book(token_id, now, max_age_s=max_age_s)

    def __getattr__(self, name):
        return getattr(self._view, name)


def entry_game_minute(view: MarketView, game_key: str | None, sport: str | None, now: int) -> float | None:
    """Game minute from the as-of game_states row, for intents that do not carry their own.

    Soccer advances a running 1H/2H clock by the state's age (plum.source_minute); other sports
    use the stored game_minute. Missing, stale, scheduled-only or ended states give None.
    """
    if not game_key:
        return None
    state = view.game_state(game_key, now)
    if state is None or now - state.ts > ENTRY_STATE_MAX_AGE_S or state.ended:
        return None
    if (state.status or "").lower() in ("scheduled", "ended", "cancelled") or state.live is False:
        return None
    if sport == "soccer":
        minute = source_minute(state, now, ENTRY_STATE_MAX_AGE_S)
        return None if minute is None else round(minute, 2)
    return None if state.game_minute is None else float(state.game_minute)


# ---------------------------------------------------------------- executors

class PaperExecutor:
    mode = "paper"

    def __init__(self, view: MarketView):
        self.view = view

    def fresh_book(self, token_id: str, now: int):
        return self.view.book(token_id, now, max_age_s=180)

    def fee(self, condition_id: str) -> FeeSchedule | None:
        override = getattr(self.view, "fee_override", None)   # backtest --fee-rate (gate evidence at current fees)
        if override is not None:
            return override
        m = self.view.market(condition_id)
        return parse_fee_schedule(m.fee_schedule) if m else None

    def market_accepting(self, condition_id: str, now: int) -> bool:
        m = self.view.market(condition_id)
        return m is not None and self.view.is_tradable(m, now)

    def buy(self, ledger: StrategyLedger, pid: str, intent: EntryIntent, stake: float, check, now: int) -> str:
        book = self.fresh_book(intent.token_id, now)
        fill = paper_buy(book, stake, intent.max_price, self.fee(intent.condition_id))
        iid = ledger.record_intent(position_id=pid, mode="paper", side="BUY", token_id=intent.token_id,
                                   condition_id=intent.condition_id, now=now, usdc_amount=stake,
                                   limit_price=intent.max_price, expected_avg_price=check.walk_vwap)
        if not fill.filled:
            ledger.update_order(iid, now, "cancelled", response={"reason": fill.reason})
            ledger.mark_unfilled(pid, now, f"paper_{fill.reason}")
            return "unfilled"
        ledger.record_fill(fill_id=iid, intent_id=iid, ts=now, side="BUY", price=fill.price, shares=fill.shares,
                           fee_usdc=fill.fee_usdc, status="PAPER")
        ledger.update_order(iid, now, "confirmed")
        ledger.apply_buy(pid, fill.shares, fill.usd, fill.fee_usdc)
        return "filled"

    def sell(self, ledger: StrategyLedger, pos, intent: ExitIntent, check, now: int) -> str:
        book = self.fresh_book(pos["token_id"], now)
        fill = paper_sell(book, intent.shares, check.limit_price or intent.min_price, self.fee(pos["condition_id"]))
        iid = ledger.record_intent(position_id=pos["position_id"], mode="paper", side="SELL", token_id=pos["token_id"],
                                   condition_id=pos["condition_id"], now=now, shares=intent.shares,
                                   limit_price=check.limit_price, expected_avg_price=check.walk_vwap)
        if not fill.filled:
            ledger.update_order(iid, now, "cancelled", response={"reason": fill.reason})
            return "unfilled"
        ledger.record_fill(fill_id=iid, intent_id=iid, ts=now, side="SELL", price=fill.price, shares=fill.shares,
                           fee_usdc=fill.fee_usdc, status="PAPER")
        ledger.update_order(iid, now, "confirmed")
        ledger.apply_sell(pos["position_id"], fill.shares, fill.usd, fill.fee_usdc, intent.kind, now, "paper")
        return "filled"


class LiveExecutor:
    mode = "live"

    def __init__(self, clob: clobmod.Clob, view: MarketView):
        self.clob = clob
        self.view = view

    def fresh_book(self, token_id: str, now: int):
        return self.clob.book(token_id, now)

    def fee(self, condition_id: str) -> FeeSchedule | None:
        sched = self.clob.fee_schedule(condition_id)
        if sched is not None:
            return sched
        m = self.view.market(condition_id)
        return parse_fee_schedule(m.fee_schedule) if m else None

    def market_accepting(self, condition_id: str, now: int) -> bool:
        m = self.view.market(condition_id)
        if m is None or not self.view.is_tradable(m, now):
            return False
        return self.clob.market_accepting(condition_id)

    def _post(self, ledger: StrategyLedger, iid: str, signed, now: int) -> str:
        try:
            resp = self.clob.post(signed)
        except Exception as e:
            status = clobmod.classify_post_error(e)
            ledger.update_order(iid, now, status, response={"error": sanitize(e)})
            return status
        status, oid = clobmod.classify_post_response(resp)
        ledger.update_order(iid, now, status, oid, {k: resp.get(k) for k in
                                                    ("success", "status", "orderID", "tradeIDs", "errorMsg",
                                                     "makingAmount", "takingAmount")})
        return status

    def buy(self, ledger: StrategyLedger, pid: str, intent: EntryIntent, stake: float, check, now: int) -> str:
        try:
            signed = self.clob.sign_fok_buy(intent.token_id, stake, check.limit_price, intent.max_price)
        except clobmod.PreSubmissionError as e:
            ledger.mark_unfilled(pid, now, f"no_post:{sanitize(e, 120)}")
            return "no_post"
        iid = ledger.record_intent(position_id=pid, mode="live", side="BUY", token_id=intent.token_id,
                                   condition_id=intent.condition_id, now=now, usdc_amount=stake,
                                   limit_price=signed.price, expected_avg_price=check.walk_vwap)
        status = self._post(ledger, iid, signed, now)
        if status == "failed":
            ledger.mark_unfilled(pid, now, "venue_rejected")
            return status
        if status in ("posted", "matched"):
            status = reconcile_order(ledger, self.clob, ledger.order(iid), now, self.fee)
        return status

    def sell(self, ledger: StrategyLedger, pos, intent: ExitIntent, check, now: int) -> str:
        try:
            signed = self.clob.sign_fok_sell(pos["token_id"], intent.shares, check.limit_price)
        except clobmod.PreSubmissionError:
            return "no_post"
        partial = floor2(float(pos["shares"] or 0)) - signed.maker_amount >= 0.01
        ledger.set_position(pos["position_id"], status="closing",
                            exit_reason=f"partial_{intent.kind}" if partial else intent.kind)
        iid = ledger.record_intent(position_id=pos["position_id"], mode="live", side="SELL", token_id=pos["token_id"],
                                   condition_id=pos["condition_id"], now=now, shares=signed.maker_amount,
                                   limit_price=signed.price, expected_avg_price=check.walk_vwap)
        status = self._post(ledger, iid, signed, now)
        if status == "failed":
            ledger.set_position(pos["position_id"], status="open", exit_reason=None)
            return status
        if status in ("posted", "matched"):
            status = reconcile_order(ledger, self.clob, ledger.order(iid), now, self.fee)
        return status


# ---------------------------------------------------------------- maker maintenance

def _bump(d: dict, key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


def _requote_entry(ledger: StrategyLedger, venue, strategy, pos, now: int, out: dict) -> bool:
    """Re-post the unfilled remainder of a maker entry on the same position. False = nothing posted."""
    pview = row_to_view(pos)
    ex = strategy.execution(pos["sport"])
    lo, hi, rule = _entry_band(ledger, pos)
    try:
        book, tick = venue.book(pos["token_id"], now), venue.tick(pos["token_id"])
    except Exception:
        return False
    px = makermod.entry_price(book, tick, lo, hi, rule or str(ex["maker_price_rule"]))
    spent = float(pos["shares"] or 0) * float(pos["entry_price"] or 0)
    remaining = float(pos["stake_usdc"]) - spent
    if px is None or remaining <= 0:
        return False
    size = floor2(remaining / px)
    if size < venue.min_shares(pos["token_id"]):
        return False
    status, _ = venue.post(ledger, position_id=pview.position_id, side="BUY", token_id=pos["token_id"],
                           condition_id=pos["condition_id"], price=px, shares=size, now=now, purpose="entry")
    if status in ("resting", "unknown"):
        _bump(out, "requoted")
        return True
    return False


def _entry_band(ledger: StrategyLedger, pos) -> tuple[float, float, str | None]:
    """[min_price, max_price] and the per-intent maker price rule frozen at entry (features of the 'enter' decision)."""
    r = ledger.conn.execute("SELECT features FROM decisions WHERE action='enter' AND token_id=? AND ts<=? "
                            "ORDER BY ts DESC LIMIT 1", (pos["token_id"], pos["opened_at"])).fetchone()
    try:
        f = json.loads(r["features"]) if r and r["features"] else {}
    except ValueError:
        f = {}
    lo, hi = f.get("band_lo"), f.get("band_hi")
    return (float(lo) if lo is not None else 0.0, float(hi) if hi is not None else 1.0, f.get("maker_price_rule"))


def maintain_maker(ledger: StrategyLedger, venue, strategy, view, now: int, *, kill: bool) -> dict:
    """Sync and steer every active maker order of this leg (see module docstring)."""
    out: dict[str, Any] = {}
    if venue.mode == "live":
        try:
            out.update(makermod.sweep(ledger, venue, now))
        except Exception as e:   # no prefetch: polls fall back to per-order lookups
            out["sweep_error"] = type(e).__name__
    for order in ledger.maker_orders(venue.mode):
        try:
            st = makermod.sync(ledger, venue, order, now)
        except Exception as e:
            _bump(out, f"sync_error:{type(e).__name__}")
            continue
        _bump(out, "active")
        if st not in makermod.MAKER_ACTIVE:
            _bump(out, f"done_{st}")
            continue
        order = ledger.order(order["intent_id"])
        pos = ledger.position(order["position_id"]) if order["position_id"] else None
        pview = row_to_view(pos) if pos is not None else None
        reason = order["cancel_reason"] if order["status"] == "cancel_requested" else None
        if order["side"] == "BUY":
            ex = strategy.execution(pos["sport"] if pos is not None else None)
            if reason is None:
                if kill:
                    reason = "kill_switch"
                elif pview is None or not strategy.maker_entry_open(view, now, pview):
                    reason = "window_closed"
                elif now - int(order["created_at"]) >= float(ex["maker_ttl_minutes"]) * 60:
                    reason = "ttl"
                else:
                    try:
                        tick = venue.tick(order["token_id"])
                        mine = float(order["shares"] or 0) - float(order["filled_shares"] or 0)
                        book = makermod.own_removed(venue.book(order["token_id"], now), "BUY",
                                                    float(order["limit_price"]), mine if venue.mode == "live" else 0)
                        lo, hi, rule = _entry_band(ledger, pos)
                        target = makermod.entry_price(book, tick, lo, hi, rule or str(ex["maker_price_rule"]))
                    except Exception:
                        target, tick = None, makermod.DEFAULT_TICK
                    # floor at 1 cent: on 0.001-tick books a 2-tick tolerance would re-quote on every wiggle
                    tol = max(float(ex["maker_reprice_ticks"]) * tick, MIN_REPRICE_MOVE)
                    if target is None or abs(target - float(order["limit_price"])) > tol + 1e-9:
                        reason = "reprice"
            if reason is None:
                continue
            requote = reason in ("ttl", "reprice")
            done = makermod.request_cancel(ledger, venue, order, now, reason, finalize=not requote)
            if not done:
                _bump(out, "cancel_pending")
                continue
            _bump(out, f"cancelled:{reason}")
            pos = ledger.position(order["position_id"]) if order["position_id"] else None
            if pos is not None and requote:
                if not (pos["status"] == "pending" and _requote_entry(ledger, venue, strategy, pos, now, out)):
                    makermod.finalize_entry(ledger, pos["position_id"], now)
        else:
            if reason is None:
                if pos is None or pos["status"] != "open":
                    reason = "position_not_open"
                elif not strategy.maker_tp_allowed(view, now, pview):
                    reason = "kickoff"
            if reason is None:
                continue
            if makermod.request_cancel(ledger, venue, order, now, reason):
                _bump(out, f"cancelled:{reason}")
            else:
                _bump(out, "cancel_pending")
    return out


def place_resting_tp(ledger: StrategyLedger, venue, strategy, view, pos, now: int, blocked: set,
                     fresh=None) -> str | None:
    """Rest the take-profit SELL of an open position. Returns 'resting' when posted, else a skip reason
    (None = the strategy wants no resting TP)."""
    pview = row_to_view(pos)
    tp = strategy.maker_tp_price(pview)
    if tp is None or not strategy.maker_tp_allowed(view, now, pview):
        return None
    if (pos["token_id"], "SELL") in blocked:
        return "blocked"
    shares = floor2(float(pos["shares"] or 0))
    if shares <= 0 or shares < venue.min_shares(pos["token_id"]):
        return "below_min_size"
    tick = venue.tick(pos["token_id"])
    px = clobmod._round_tick(tp, tick, up=True)
    book = fresh if fresh is not None else venue.book(pos["token_id"], now)
    if book is not None and book.best_bid is not None and book.best_bid >= px - EPS:
        return "would_cross"                 # the taker TP path (net-positive check) handles it
    if pos["cost_usdc"] is None or not shares * px > float(pos["cost_usdc"]) + 1e-9:
        return "not_net_positive"
    status, _ = venue.post(ledger, position_id=pos["position_id"], side="SELL", token_id=pos["token_id"],
                           condition_id=pos["condition_id"], price=px, shares=shares, now=now, purpose="take_profit")
    return "resting" if status in ("resting", "unknown") else f"tp_post_{status}"


# ---------------------------------------------------------------- one variant

def leg_stake(variant, sports: list[str] | None) -> float:
    """Stake recorded on the leg's param version: the largest per-sport stake of the leg."""
    if not getattr(variant, "per_sport", False):
        return variant.stake_usdc
    return max((variant.sport_stake(s) for s in (sports if sports is not None else variant.sports)),
               default=variant.stake_usdc)


def run_variant(paths, variant, view: MarketView, now: int, *, mode: str, dry_run: bool = False,
                clob_factory: Callable[[Any], Any] | None = None, kill: bool = False,
                commit: str | None = None, sports: list[str] | None = None) -> VariantResult:
    """One leg of a variant in one mode. `sports` restricts entries (None = every variant sport);
    exits, reconciliation and settlement always cover every open position of that mode."""
    res = VariantResult(variant.id, mode)
    if sports is not None:
        res.sports = list(sports)
    ledger = open_ledger(paths, variant.id)
    pv = ledger.ensure_param_version(variant.params, leg_stake(variant, sports), mode, now, commit)
    cycle = ledger.start_cycle(now, pv)
    orders = 0
    try:
        strategy = build(variant)
        if sports is not None:
            strategy.sports = list(sports)
        if mode == "live":
            try:
                creds = settings.account_credentials(variant.account)
            except KeyError:
                raise RuntimeError(f"credentials missing for account alias {variant.account!r}; variant skipped")
            factory = clob_factory or (lambda c: clobmod.Clob(clobmod.build_client(c)))
            executor: Any = LiveExecutor(factory(creds), view)
            if not dry_run:
                res.reconciled = reconcile_live(ledger, executor.clob, now, executor.fee)
        else:
            executor = PaperExecutor(view)
        if not dry_run:
            res.resolved = settle_resolutions(ledger, view, now, mode)
        venue = makermod.LiveVenue(executor.clob, executor.fee) if mode == "live" \
            else makermod.PaperVenue(view, executor.fee)
        maker_style = {s: strategy.order_style(s) == "maker" for s in (strategy.sports or [])}
        uses_maker = any(maker_style.values()) or bool(ledger.maker_orders(mode, active=False))
        if uses_maker and not dry_run:
            res.maker = maintain_maker(ledger, venue, strategy, view, now, kill=kill)
        resting_tp = {o["position_id"]: o for o in ledger.maker_orders(mode)
                      if o["side"] == "SELL" and o["position_id"]} if uses_maker else {}

        # ---- exits
        blocked = ledger.blocked_token_sides(mode)
        sells_left = int(variant.limits.get("max_sells_per_cycle", DEFAULT_SELLS_PER_CYCLE))
        for pos in ledger.positions(mode, ("open",)):
            pview = row_to_view(pos)
            fresh = None
            if mode == "live":
                # stops must not depend on collector freshness: evaluate exits on a live book
                try:
                    fresh = executor.fresh_book(pos["token_id"], now)
                except Exception as e:
                    res.skip(f"exit_book_error:{type(e).__name__}")
            exit_view = BookOverlay(view, {pos["token_id"]: fresh} if fresh is not None else {})
            intent = strategy.exit_signals(exit_view, now, pview)
            rest = resting_tp.get(pos["position_id"])
            if rest is not None:
                if intent is None or intent.kind == "take_profit":
                    continue                      # the resting TP works the exit fee-free
                if dry_run:
                    continue
                # stop / time exit: the resting TP must be gone (venue-confirmed) before selling as taker
                if not makermod.request_cancel(ledger, venue, rest, now, intent.kind):
                    res.skip("tp_cancel_pending")
                    continue
                pos = ledger.position(pos["position_id"])
                if pos["status"] != "open" or floor2(float(pos["shares"] or 0)) <= 0:
                    continue                      # the TP filled meanwhile
                blocked.discard((pos["token_id"], "SELL"))
                pview = row_to_view(pos)
                intent = ExitIntent(intent.position_id, intent.kind, floor2(float(pos["shares"])), intent.min_price,
                                    intent.reason, intent.features)
            elif intent is None and maker_style.get(pos["sport"]) and not dry_run:
                try:
                    placed = place_resting_tp(ledger, venue, strategy, view, pos, now, blocked, fresh)
                except Exception as e:
                    placed = f"tp_error:{type(e).__name__}"
                if placed == "resting":
                    _bump(res.maker, "tp_rested")
                    orders += 1
                elif placed:
                    _bump(res.maker, f"tp_skip:{placed}")
            if intent is None:
                continue
            if sells_left <= 0 or (pos["token_id"], "SELL") in blocked:
                res.skip("sell_budget_or_blocked")
                continue
            if not executor.market_accepting(pos["condition_id"], now):
                ledger.decision(cycle, now, "hold", "market_not_accepting", pos["token_id"], pos["condition_id"],
                                pos["game_key"])
                continue
            check = strategy.confirm_exit(pview, intent, fresh if fresh is not None
                                          else executor.fresh_book(pos["token_id"], now))
            if not check.ok:
                ledger.decision(cycle, now, "hold", f"exit_recheck:{check.reason}", pos["token_id"],
                                pos["condition_id"], pos["game_key"], {"intent": intent.kind})
                continue
            ledger.decision(cycle, now, "exit", f"{intent.kind}: {intent.reason}", pos["token_id"],
                            pos["condition_id"], pos["game_key"],
                            {**intent.features, "limit": check.limit_price, "vwap": check.walk_vwap,
                             "shares": check.shares, "dry_run": dry_run})
            if dry_run:
                continue
            sells_left -= 1
            orders += 1
            if executor.sell(ledger, pos, ExitIntent(intent.position_id, intent.kind, check.shares or intent.shares,
                                                     intent.min_price, intent.reason, intent.features),
                             check, now) in ("filled", "confirmed"):
                res.exits += 1

        # ---- entries
        if kill:
            ledger.decision(cycle, now, "skip", "kill_switch")
            res.skip("kill_switch")
        else:
            n_open, open_usdc = ledger.open_exposure(mode)
            caps = CapState(n_open, open_usdc, ledger.realized_since(mode, utc_day_start(now)))
            sport_caps: dict[str, CapState] = {}
            intents = [] if sports is not None and not sports else strategy.entry_signals(view, now, ledger.view(mode, now))
            res.candidates = len(intents)
            for key, reason in strategy.skips:
                res.skip(reason)
                if reason in PERSISTED_SKIPS:
                    ledger.decision(cycle, now, "skip", reason, condition_id=key)
            for intent in intents:
                stake = clamp_stake(variant.sport_stake(intent.sport) if hasattr(variant, "sport_stake")
                                    else variant.stake_usdc)
                cap = check_entry(variant.limits, caps, stake)
                if not cap.ok:
                    ledger.decision(cycle, now, "skip", f"cap:{cap.reason}", intent.token_id, intent.condition_id,
                                    intent.game_key)
                    res.skip(cap.reason)
                    break
                slim = variant.sport_limits(intent.sport) if hasattr(variant, "sport_limits") else {}
                if slim:
                    if intent.sport not in sport_caps:
                        n_s, usd_s = ledger.open_exposure(mode, intent.sport)
                        sport_caps[intent.sport] = CapState(n_s, usd_s, ledger.realized_since(
                            mode, utc_day_start(now), intent.sport))
                    scap = check_entry(slim, sport_caps[intent.sport], stake)
                    if not scap.ok:
                        ledger.decision(cycle, now, "skip", f"cap:{intent.sport}:{scap.reason}", intent.token_id,
                                        intent.condition_id, intent.game_key)
                        res.skip(f"{intent.sport}:{scap.reason}")
                        continue
                if (intent.token_id, "BUY") in blocked:
                    res.skip("token_side_blocked")
                    continue
                if maker_style.get(intent.sport):
                    status = _maker_entry(ledger, venue, strategy, intent, stake, cycle, now, pv, mode, dry_run,
                                          view, res)
                    if status is None:
                        continue
                    caps.new_this_cycle += 1
                    if intent.sport in sport_caps:
                        sport_caps[intent.sport].new_this_cycle += 1
                    if status == "resting":
                        orders += 1
                        for c in (caps, sport_caps.get(intent.sport)):
                            if c is not None:
                                c.open_positions += 1
                                c.open_usdc += stake
                    continue
                books = {t: executor.fresh_book(t, now) for t in (intent.context_tokens or [intent.token_id])}
                check = strategy.confirm_entry(intent, books, stake)
                feats = {**intent.features, "signal_price": intent.signal_price, "stake": stake,
                         "recheck": check.reason, "vwap": check.walk_vwap, "limit": check.limit_price,
                         "dry_run": dry_run}
                if not check.ok:
                    ledger.decision(cycle, now, "skip", f"entry_recheck:{check.reason}", intent.token_id,
                                    intent.condition_id, intent.game_key, feats)
                    res.skip(f"recheck:{check.reason}")
                    continue
                if intent.game_minute is None:      # never overwrite a strategy's own clock (apricot: since tick0)
                    intent.game_minute = entry_game_minute(view, intent.game_key, intent.sport, now)
                ledger.decision(cycle, now, "enter", intent.reason, intent.token_id, intent.condition_id,
                                intent.game_key, feats)
                caps.new_this_cycle += 1
                if intent.sport in sport_caps:
                    sport_caps[intent.sport].new_this_cycle += 1
                if dry_run:
                    continue
                pid = ledger.open_position(intent, mode, pv, stake, now)
                orders += 1
                status = executor.buy(ledger, pid, intent, stake, check, now)
                if status in ("filled", "confirmed"):
                    res.entries += 1
                if status not in ("no_post", "unfilled", "failed", "cancelled"):
                    for c in (caps, sport_caps.get(intent.sport)):
                        if c is not None:
                            c.open_positions += 1      # pending/unknown outcomes reserve capacity too
                            c.open_usdc += stake
        ledger.finish_cycle(cycle, int(time.time()), True, res.candidates, orders)
    except Exception as e:
        res.ok = False
        res.error = sanitize(f"{type(e).__name__}: {e}")
        ledger.finish_cycle(cycle, int(time.time()), False, res.candidates, orders, res.error)
    finally:
        ledger.conn.close()
    return res


def _maker_entry(ledger: StrategyLedger, venue, strategy, intent: EntryIntent, stake: float, cycle: int, now: int,
                 pv: int, mode: str, dry_run: bool, view, res: VariantResult) -> str | None:
    """Rest a post-only BUY for one entry intent. None = skipped (decision logged)."""
    ex = strategy.execution(intent.sport)
    try:
        book, tick = venue.book(intent.token_id, now), venue.tick(intent.token_id)
        min_sh = venue.min_shares(intent.token_id)
    except Exception as e:
        res.skip(f"maker_book_error:{type(e).__name__}")
        return None
    rule = str(intent.features.get("maker_price_rule") or ex["maker_price_rule"])     # per-intent override (ai_ou05)
    px = makermod.entry_price(book, tick, intent.min_price, intent.max_price, rule)
    feats = {**intent.features, "signal_price": intent.signal_price, "stake": stake, "order_style": "maker",
             "maker_price": px, "best_bid": book.best_bid if book else None,
             "best_ask": book.best_ask if book else None, "band_lo": intent.min_price, "band_hi": intent.max_price,
             "dry_run": dry_run}
    if px is None:
        ledger.decision(cycle, now, "skip", "maker_no_price", intent.token_id, intent.condition_id, intent.game_key,
                        feats)
        res.skip("maker_no_price")
        return None
    size = floor2(stake / px)
    if size < min_sh:
        ledger.decision(cycle, now, "skip", "maker_below_min_size", intent.token_id, intent.condition_id,
                        intent.game_key, {**feats, "size": size, "min_size": min_sh})
        res.skip("maker_below_min_size")
        return None
    if intent.game_minute is None:
        intent.game_minute = entry_game_minute(view, intent.game_key, intent.sport, now)
    ledger.decision(cycle, now, "enter", f"maker {px} x {size}: {intent.reason}", intent.token_id,
                    intent.condition_id, intent.game_key, {**feats, "size": size})
    if dry_run:
        return "dry_run"
    pid = ledger.open_position(intent, mode, pv, stake, now)
    status, _ = venue.post(ledger, position_id=pid, side="BUY", token_id=intent.token_id,
                           condition_id=intent.condition_id, price=px, shares=size, now=now, purpose="entry")
    if status in ("resting", "unknown"):
        _bump(res.maker, "entries_rested")
        return "resting"
    ledger.mark_unfilled(pid, now, f"maker_{status}"[:120])
    _bump(res.maker, f"entry_{status.split(':')[0]}")
    return status


# ---------------------------------------------------------------- job

def _has_resting(paths, variant_id: str, mode: str) -> bool:
    """Active maker orders of `mode` in the variant ledger (read-only)."""
    path = paths.strategy_db(variant_id)
    if not Path(path).exists():
        return False
    import sqlite3  # noqa: PLC0415
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            return conn.execute("SELECT 1 FROM orders WHERE mode=? AND order_type='GTC' AND status IN "
                                "('intent','resting','cancel_requested','unknown') LIMIT 1",
                                (mode,)).fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return False

def _has_open(paths, variant_id: str, mode: str) -> bool:
    """Open/pending positions of `mode` in the variant ledger (read-only; no DB -> False)."""
    path = paths.strategy_db(variant_id)
    if not Path(path).exists():
        return False
    import sqlite3  # noqa: PLC0415
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            return conn.execute("SELECT 1 FROM positions WHERE mode=? AND status IN "
                                "('pending','open','closing','quarantined') LIMIT 1", (mode,)).fetchone() is not None
        finally:
            conn.close()
    except Exception:
        return True   # cannot tell: run the leg so exits/reconciliation are never silently skipped


def plan_legs(paths, variant, paper_all: bool) -> list[tuple[str, list[str] | None]]:
    """(mode, entry sports) legs for one tick.

    Legacy list-form variants: one leg at the master mode with every sport (unchanged behaviour).
    Per-sport variants: a live leg for live sports and a paper leg for paper sports. A mode with no
    entry sports still runs (entries none) while it holds open positions, so a sport moved
    live->paper keeps reconciling, settling and exiting its live positions on the same account.
    """
    if variant.mode == "off":   # only to finish resting maker orders (no entries)
        return [(m, []) for m in ("live", "paper")
                if not (paper_all and m == "live") and _has_resting(paths, variant.id, m)]
    if not getattr(variant, "per_sport", False):
        return [("paper" if paper_all or variant.mode == "paper" else "live", None)]
    active = [s for s in variant.sports if variant.sport_mode(s) != "off"]
    if paper_all:
        groups = {"paper": active}
    else:
        groups = {"live": [s for s in active if variant.sport_mode(s) == "live"],
                  "paper": [s for s in active if variant.sport_mode(s) == "paper"]}
    legs = []
    for mode in ("live", "paper"):
        sports = groups.get(mode, [])
        if sports:
            legs.append((mode, sports))
        elif mode == "live" and not paper_all and variant.account and _has_open(paths, variant.id, "live"):
            legs.append(("live", []))
        elif mode == "paper" and _has_open(paths, variant.id, "paper"):
            legs.append(("paper", []))
    return legs


def _poll(paths) -> dict:
    try:
        from polylab.collector.poll import run_once
    except (ImportError, AttributeError):
        return {"skipped": "collector.poll.run_once unavailable"}
    try:
        return {"ok": True, **(run_once(paths) or {})}
    except Exception as e:
        return {"ok": False, "error": sanitize(f"{type(e).__name__}: {e}")}


def _redeem(paths, now: int) -> dict:
    try:
        from polylab.execution.redeem import run_due
        return run_due(paths, now)
    except ImportError as e:
        return {"skipped": f"redeem sdk unavailable: {sanitize(e, 120)}"}
    except Exception as e:
        return {"error": sanitize(f"{type(e).__name__}: {e}", 200)}


def run(paths, *, only: str | None = None, paper_all: bool = False, dry_run: bool = False,
        registry_dir: Path | None = None, clob_factory=None, poll: bool = True,
        now: int | None = None, redeem: bool = False) -> dict:
    started = int(time.time())
    core = db.core(paths)
    commit = git_commit()
    run_id = core.execute("INSERT INTO job_runs(job, started_at, git_commit) VALUES(?,?,?)",
                          (JOB_NAME, started, commit)).lastrowid
    core.commit()
    summary: dict[str, Any] = {"paper_all": paper_all, "dry_run": dry_run}
    ok = True
    try:
        summary["poll"] = _poll(paths) if poll else {"skipped": "disabled"}
        now = int(now or time.time())
        kill = kill_switch_active(paths.state)
        summary["kill_switch"] = kill
        variants = [v for v in registry.load_all(registry_dir, include_off=True)
                    if v.mode != "off" or _has_resting(paths, v.id, "live") or _has_resting(paths, v.id, "paper")]
        if only:
            variants = [v for v in variants if v.id == only]
        view = MarketView.open(paths, now)
        results = []
        try:
            for v in variants:
                for mode, sports in plan_legs(paths, v, paper_all):
                    try:
                        r = run_variant(paths, v, view, now, mode=mode, dry_run=dry_run, clob_factory=clob_factory,
                                        kill=kill, commit=commit, sports=sports)
                    except Exception as e:  # ledger open failure etc.
                        r = VariantResult(v.id, mode, sports, ok=False, error=sanitize(f"{type(e).__name__}: {e}"))
                    results.append(r.__dict__)
        finally:
            view.close()
        summary["variants"] = results
        ok = all(r["ok"] for r in results)
        if redeem and not dry_run and not paper_all:
            summary["redeem"] = _redeem(paths, now)
    except Exception as e:
        ok = False
        summary["error"] = sanitize(f"{type(e).__name__}: {e}")
        summary["trace"] = sanitize(traceback.format_exc(limit=3), 1200)
    core.execute("UPDATE job_runs SET finished_at=?, ok=?, summary=? WHERE id=?",
                 (int(time.time()), int(ok), json.dumps(summary, default=str), run_id))
    core.commit()
    core.close()
    summary["ok"] = ok
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polylab tick")
    ap.add_argument("--only", help="run a single variant id")
    ap.add_argument("--paper-all", action="store_true", help="run every non-off variant in paper mode")
    ap.add_argument("--dry-run", action="store_true", help="signals and decisions only, zero orders")
    ap.add_argument("--no-poll", action="store_true", help="skip the collector poll")
    args = ap.parse_args(argv)
    paths = settings.paths()
    lock_path = paths.state / "tick.lock"
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("tick already running; exiting")
            return 0
        from polylab.execution.redeem import auto_redeem_enabled
        summary = run(paths, only=args.only, paper_all=args.paper_all, dry_run=args.dry_run, poll=not args.no_poll,
                      redeem=auto_redeem_enabled() and not args.only)
    print(json.dumps(summary, default=str, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
