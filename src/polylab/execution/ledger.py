"""Strategy-DB ledger: param versions, cycles, decisions, order intents, fills, positions.

Rules that matter:
- An order intent row is committed BEFORE the order is posted.
- Realized P&L exists only from confirmed fills with known fees, or an exact resolution.
- Every read that feeds caps/strategies filters by mode, so paper rows (--paper-all)
  never mix with live exposure.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterable

from polylab.strategies.base import Ledger, PositionView

DUST_SHARES = 0.01 + 1e-6

# additive tables for the strategy DB (applied by open_ledger)
EXTRA_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS redemptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        condition_id TEXT NOT NULL,
        status TEXT NOT NULL,                   -- redeemable | submitted | done | failed | dry_run
        detail TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS orders_position ON orders(position_id)",
    "CREATE INDEX IF NOT EXISTS fills_intent ON fills(intent_id)",
)


def _j(v: Any) -> str | None:
    return None if v is None else json.dumps(v, default=str, sort_keys=True)


class StrategyLedger:
    def __init__(self, conn: sqlite3.Connection, strategy_id: str):
        self.conn = conn
        self.strategy_id = strategy_id
        for stmt in EXTRA_SCHEMA:
            conn.execute(stmt)
        conn.commit()
        from polylab.risk.ladder import ensure_sport_column  # noqa: PLC0415
        ensure_sport_column(conn)

    # ------------------------------------------------------------ params / cycles
    def ensure_param_version(self, params: dict, stake_usdc: float, mode: str, now: int,
                             git_commit: str | None = None, author: str = "registry",
                             rationale: str | None = None) -> int:
        blob = json.dumps(params, sort_keys=True, default=str)
        # per mode: a per-sport variant runs a live and a paper leg in the same minute
        r = self.conn.execute("SELECT version, params, stake_usdc, mode FROM param_versions WHERE mode=? "
                              "ORDER BY version DESC LIMIT 1", (mode,)).fetchone()
        if r and r["params"] == blob and float(r["stake_usdc"]) == float(stake_usdc):
            return int(r["version"])
        last = self.conn.execute("SELECT MAX(version) FROM param_versions").fetchone()[0]
        version = (int(last) + 1) if last is not None else 1
        self.conn.execute(
            "INSERT INTO param_versions(version, created_at, params, stake_usdc, mode, git_commit, author, rationale) "
            "VALUES(?,?,?,?,?,?,?,?)", (version, now, blob, stake_usdc, mode, git_commit,
                                        "init" if last is None else author, rationale))
        self.conn.commit()
        return version

    def start_cycle(self, now: int, param_version: int) -> int:
        cur = self.conn.execute("INSERT INTO cycles(started_at, param_version) VALUES(?,?)", (now, param_version))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_cycle(self, cycle_id: int, now: int, ok: bool, candidates: int, orders: int,
                     error: str | None = None) -> None:
        self.conn.execute("UPDATE cycles SET finished_at=?, ok=?, candidates=?, orders=?, error=? WHERE cycle_id=?",
                          (now, int(ok), candidates, orders, error, cycle_id))
        self.conn.commit()

    def decision(self, cycle_id: int | None, now: int, action: str, reason: str, token_id: str | None = None,
                 condition_id: str | None = None, game_key: str | None = None, features: Any = None) -> None:
        self.conn.execute(
            "INSERT INTO decisions(cycle_id, ts, token_id, condition_id, game_key, action, reason, features) "
            "VALUES(?,?,?,?,?,?,?,?)", (cycle_id, now, token_id, condition_id, game_key, action, reason, _j(features)))
        self.conn.commit()

    # ------------------------------------------------------------ positions
    def open_position(self, intent, mode: str, param_version: int, stake_usdc: float, now: int) -> str:
        pid = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO positions(position_id, mode, param_version, stake_usdc, sport, league, game_key, condition_id, "
            "token_id, outcome_label, opened_at, game_minute_at_entry, exit_rules, status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')",
            (pid, mode, param_version, stake_usdc, intent.sport, intent.league, intent.game_key, intent.condition_id,
             intent.token_id, intent.outcome_label, now, intent.game_minute, _j(intent.exit_rules)))
        self.conn.commit()
        return pid

    def position(self, position_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM positions WHERE position_id=?", (position_id,)).fetchone()

    def positions(self, mode: str, statuses: Iterable[str] | None = None) -> list[sqlite3.Row]:
        if statuses is None:
            return self.conn.execute("SELECT * FROM positions WHERE mode=? ORDER BY opened_at", (mode,)).fetchall()
        st = list(statuses)
        return self.conn.execute(
            f"SELECT * FROM positions WHERE mode=? AND status IN ({','.join('?' * len(st))}) ORDER BY opened_at",
            (mode, *st)).fetchall()

    def set_position(self, position_id: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE positions SET {cols} WHERE position_id=?", (*fields.values(), position_id))
        self.conn.commit()

    def apply_buy(self, position_id: str, shares: float, cost_ex_fee: float, fee: float | None) -> None:
        """Confirmed BUY → open. `fee` None only in paper (unknown schedule), noted on the row."""
        vwap = cost_ex_fee / shares
        cost = cost_ex_fee + (fee or 0.0)
        self.set_position(position_id, status="open", entry_price=round(vwap, 8), shares=shares,
                          cost_usdc=round(cost, 8), entry_fee_usdc=fee,
                          **({"notes": "fee_unknown"} if fee is None else {}))

    def apply_sell(self, position_id: str, shares: float, proceeds_gross: float, fee: float | None, kind: str,
                   now: int, settlement: str) -> None:
        pos = self.position(position_id)
        remaining = max(0.0, float(pos["shares"] or 0) - shares)
        net = proceeds_gross - (fee or 0.0)
        total_net = float(pos["proceeds_usdc"] or 0) + net
        total_fee = None if fee is None and pos["exit_fee_usdc"] is None and pos["proceeds_usdc"] is None \
            else float(pos["exit_fee_usdc"] or 0) + (fee or 0.0)
        fields: dict[str, Any] = {"shares": remaining, "proceeds_usdc": round(total_net, 8),
                                  "exit_fee_usdc": total_fee, "exit_price": round(proceeds_gross / shares, 8)}
        if remaining < DUST_SHARES:
            fields.update(status="closed", closed_at=now, exit_reason=kind, settlement=settlement,
                          realized_pnl=round(total_net - float(pos["cost_usdc"]), 8))
            if fee is None:
                fields["notes"] = "fee_unknown"
        else:
            fields.update(status="open", exit_reason=f"partial_{kind}")
        self.set_position(position_id, **fields)

    def apply_resolution(self, position_id: str, payout_per_share: float, now: int, redeemable: bool) -> None:
        pos = self.position(position_id)
        shares = float(pos["shares"] or 0)
        payout = shares * payout_per_share
        total = float(pos["proceeds_usdc"] or 0) + payout
        self.set_position(position_id, status="resolved", closed_at=now,
                          exit_reason="resolution_win" if payout_per_share > 0.5 else "resolution_loss",
                          exit_price=payout_per_share, proceeds_usdc=round(total, 8),
                          realized_pnl=round(total - float(pos["cost_usdc"]), 8), settlement="resolution",
                          redeemed=0 if (redeemable and payout > 0) else 1)

    def mark_unfilled(self, position_id: str, now: int, reason: str) -> None:
        self.set_position(position_id, status="unfilled", closed_at=now, exit_reason=reason, shares=0)

    def quarantine(self, position_id: str, now: int, reason: str) -> None:
        self.set_position(position_id, status="quarantined", notes=reason)

    # ------------------------------------------------------------ orders / fills
    def record_intent(self, *, position_id: str, mode: str, side: str, token_id: str, condition_id: str | None,
                      now: int, usdc_amount: float | None = None, shares: float | None = None,
                      limit_price: float | None = None, expected_avg_price: float | None = None,
                      order_type: str = "FOK") -> str:
        intent_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO orders(intent_id, position_id, created_at, mode, side, token_id, condition_id, order_type, "
            "usdc_amount, shares, limit_price, expected_avg_price, status, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'intent', ?)",
            (intent_id, position_id, now, mode, side, token_id, condition_id, order_type, usdc_amount, shares,
             limit_price, expected_avg_price, now))
        self.conn.commit()   # durable before POST
        return intent_id

    def update_order(self, intent_id: str, now: int, status: str, exchange_order_id: str | None = None,
                     response: Any = None) -> None:
        sets = ["status=?", "updated_at=?"]
        vals: list[Any] = [status, now]
        if exchange_order_id is not None:
            sets.append("exchange_order_id=?")
            vals.append(exchange_order_id)
        if response is not None:
            sets.append("response=?")
            vals.append(_j(response))
        self.conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE intent_id=?", (*vals, intent_id))
        self.conn.commit()

    def order(self, intent_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM orders WHERE intent_id=?", (intent_id,)).fetchone()

    def pending_orders(self, mode: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM orders WHERE mode=? AND status IN ('intent','posted','matched','unknown') ORDER BY created_at",
            (mode,)).fetchall()

    def blocked_token_sides(self, mode: str) -> set[tuple[str, str]]:
        """token×side with an unresolved submission: no new order until reconciled."""
        rows = self.conn.execute(
            "SELECT token_id, side FROM orders WHERE mode=? AND status IN ('intent','unknown')", (mode,)).fetchall()
        return {(r["token_id"], r["side"]) for r in rows}

    def record_fill(self, *, fill_id: str, intent_id: str, ts: int, side: str, price: float, shares: float,
                    fee_usdc: float | None, status: str, raw: Any = None) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO fills(fill_id, intent_id, ts, side, price, shares, fee_usdc, status, raw) "
            "VALUES(?,?,?,?,?,?,?,?,?)", (fill_id, intent_id, ts, side, price, shares, fee_usdc, status, _j(raw)))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------ views for strategy / risk
    def view(self, mode: str, now: int, lookback_days: int = 31) -> Ledger:
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE mode=? AND (status IN ('pending','open','closing','quarantined') "
            "OR opened_at >= ?) ORDER BY opened_at", (mode, now - lookback_days * 86400)).fetchall()
        blocked = {r["condition_id"] for r in self.conn.execute(
            "SELECT DISTINCT condition_id FROM decisions WHERE action='skip' AND reason='rapid_jump' "
            "AND condition_id IS NOT NULL")}
        return Ledger([row_to_view(r) for r in rows], blocked)

    def open_exposure(self, mode: str, sport: str | None = None) -> tuple[int, float]:
        """(open position count, open cost incl. pending stake) for caps; optionally one sport only."""
        r = self.conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(COALESCE(cost_usdc, stake_usdc)), 0) c FROM positions "
            "WHERE mode=? AND status IN ('pending','open','closing','quarantined')"
            + (" AND sport=?" if sport else ""), (mode, sport) if sport else (mode,)).fetchone()
        return int(r["n"]), float(r["c"])

    def realized_since(self, mode: str, since: int, sport: str | None = None) -> float:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE mode=? AND closed_at >= ? "
            "AND realized_pnl IS NOT NULL" + (" AND sport=?" if sport else ""),
            (mode, since, sport) if sport else (mode, since)).fetchone()
        return float(r[0])

    def settled_trades(self, mode: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT position_id, closed_at, realized_pnl, cost_usdc, stake_usdc FROM positions "
            "WHERE mode=? AND status IN ('closed','resolved') AND realized_pnl IS NOT NULL ORDER BY closed_at",
            (mode,)).fetchall()


def row_to_view(r: sqlite3.Row) -> PositionView:
    return PositionView(
        position_id=r["position_id"], status=r["status"], token_id=r["token_id"], condition_id=r["condition_id"],
        game_key=r["game_key"], sport=r["sport"], opened_at=r["opened_at"], entry_price=r["entry_price"],
        shares=r["shares"], cost_usdc=r["cost_usdc"], exit_rules=json.loads(r["exit_rules"] or "{}"),
        closed_at=r["closed_at"], exit_reason=r["exit_reason"], game_minute_at_entry=r["game_minute_at_entry"])


def open_ledger(paths, strategy_id: str) -> StrategyLedger:
    from polylab import db
    return StrategyLedger(db.strategy(paths, strategy_id), strategy_id)
