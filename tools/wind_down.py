#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "py-clob-client-v2>=1.0.1",
#     "requests>=2.31",
#     "python-dotenv>=1.0",
# ]
# ///
"""Polymarket 계정 wind-down 도구 — 전략 전환용 현황/일괄 청산 스크립트.

전략 A → B 전환 권장 절차:
  1) A Jenkins 잡에 POLYBOT_BUY_AMOUNT=0 을 넣어 드레인 모드로 전환
     (모든 봇 trader의 MIN_ORDER_SIZE=5주 체크에 걸려 매수만 자연 중단,
      손절/익절/시간 청산 등 매도 로직은 그대로 동작)
  2) 이 스크립트 status 로 잔여 포지션/미체결/예상 청산비용 확인
  3) 드레인 데드라인이 지나면 A 잡을 끄고 flatten 으로 잔여분 강제 청산
     (기본 dry-run — 예상 비용을 보고 --yes 로 실행)
  4) 자금 정산 확인 후 Jenkins 스크립트를 B 로 교체

사용 예 (봇과 동일한 env 사용):
  export POLYMARKET_FUNDER_ADDRESS=0x...          # status 는 이것만으로 동작
  export POLYMARKET_PRIVATE_KEY=0x...             # cancel/flatten 에만 필요
  uv run tools/wind_down.py status
  uv run tools/wind_down.py cancel --yes
  uv run tools/wind_down.py flatten                       # dry-run (계획만 출력)
  uv run tools/wind_down.py flatten --yes                 # 취소 후 best bid 매도
  uv run tools/wind_down.py flatten --mode mid --rounds 6 --wait 300 --yes
                                                          # midpoint 지정가로 반복 재호가
  uv run tools/wind_down.py status --env-file golden-date/.env

매도 모드:
  bid   (기본) best bid 지정가 — 사실상 즉시 체결, 비용 = mid-bid (반스프레드)
  mid   midpoint 지정가 — 스프레드 비용 0 에 수렴하나 체결 불확실 (--rounds 로 재호가)
  sweep bid 아래 --sweep-ticks 틱 지정가 — 대량 물량을 호가 깊이까지 쓸어 매도
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import requests

CLOB_HOST = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CHAIN_ID = 137        # Polygon
SIGNATURE_TYPE = 1    # 봇들과 동일 (email/proxy funder 지갑)
MIN_ORDER_SIZE = 5.0  # CLOB 최소 주문 수량(주) - 봇 trader 와 동일
DEFAULT_TICK = 0.01


@dataclass
class Position:
    token_id: str
    condition_id: str
    title: str
    outcome: str
    size: float
    avg_price: float
    cur_price: float
    redeemable: bool
    neg_risk: bool


@dataclass
class Quote:
    bid: Optional[float] = None
    ask: Optional[float] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return round(self.ask - self.bid, 4)


@dataclass
class SellPlan:
    pos: Position
    price: float
    size: float
    proceeds: float
    cost_vs_mid: float  # 즉시 청산 비용 (mid 대비, mid 없으면 0 처리)


@dataclass
class Buckets:
    """포지션 분류 결과. sellable 외에는 이 스크립트로 처리 불가(리포트만)."""
    sellable: List[SellPlan] = field(default_factory=list)
    redeem: List[Position] = field(default_factory=list)     # 해결됨 → UI redeem 필요
    dust: List[Position] = field(default_factory=list)       # < 5주 (CLOB 최소 미달)
    no_book: List[Position] = field(default_factory=list)    # 호가 없음
    wide: List[Position] = field(default_factory=list)       # 스프레드 과대 (skip)


# ---------------------------------------------------------------------------
# 데이터 조회
# ---------------------------------------------------------------------------

def fetch_positions(funder: str) -> List[Position]:
    """data-api /positions 조회 (공개 endpoint, 키 불필요)."""
    session = requests.Session()
    session.headers.update(
        {"Accept": "application/json", "User-Agent": "PolybotWindDown/1.0"}
    )
    out: List[Position] = []
    offset, limit = 0, 500
    while True:
        resp = session.get(
            f"{DATA_API}/positions",
            params={
                "user": funder.lower(),
                "limit": limit,
                "offset": offset,
                "sizeThreshold": 0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json() or []
        for p in batch:
            try:
                out.append(Position(
                    token_id=str(p.get("asset") or ""),
                    condition_id=str(p.get("conditionId") or ""),
                    title=str(p.get("title") or "")[:55],
                    outcome=str(p.get("outcome") or "?"),
                    size=float(p.get("size") or 0),
                    avg_price=float(p.get("avgPrice") or 0),
                    cur_price=float(p.get("curPrice") or 0),
                    redeemable=bool(p.get("redeemable", False)),
                    neg_risk=bool(p.get("negativeRisk", p.get("negRisk", False))),
                ))
            except (TypeError, ValueError):
                continue
        if len(batch) < limit:
            break
        offset += limit
    return [p for p in out if p.token_id and p.size > 0]


def make_readonly_client():
    """가격 조회 전용 클라이언트 (키 불필요)."""
    from py_clob_client_v2 import ClobClient
    return ClobClient(host=CLOB_HOST, chain_id=CHAIN_ID)


def make_trading_client(private_key: str, funder: str):
    """주문/취소용 인증 클라이언트 (봇 ClobClientWrapper 와 동일 초기화)."""
    from py_clob_client_v2 import ClobClient
    client = ClobClient(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=private_key,
        signature_type=SIGNATURE_TYPE,
        funder=funder,
    )
    client.set_api_creds(client.create_or_derive_api_key())
    return client


def _to_price(value) -> Optional[float]:
    """get_price(s) 응답 방어 파싱: {'price': '0.49'} / '0.49' / 0.49 모두 허용."""
    if isinstance(value, dict):
        value = value.get("price", value.get("BUY", value.get("SELL")))
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def fetch_quotes(client, token_ids: List[str]) -> Dict[str, Quote]:
    """배치 get_prices 우선, 실패 시 토큰별 get_price 폴백."""
    from py_clob_client_v2 import BookParams
    quotes: Dict[str, Quote] = {t: Quote() for t in token_ids}
    CHUNK = 50
    for i in range(0, len(token_ids), CHUNK):
        chunk = token_ids[i:i + CHUNK]
        params = [BookParams(t, s) for t in chunk for s in ("BUY", "SELL")]
        try:
            result = client.get_prices(params)
            if not isinstance(result, dict):
                raise ValueError(f"unexpected get_prices shape: {type(result)}")
            for tid, sides in result.items():
                if tid not in quotes or not isinstance(sides, dict):
                    continue
                bid = _to_price(sides.get("BUY"))
                ask = _to_price(sides.get("SELL"))
                quotes[tid] = Quote(bid=bid, ask=ask)
        except Exception:
            for t in chunk:
                q = Quote()
                for side in ("BUY", "SELL"):
                    try:
                        price = _to_price(client.get_price(t, side=side))
                    except Exception:
                        price = None  # 해결/비유동 시장은 orderbook 404 가 정상
                    if side == "BUY":
                        q.bid = price
                    else:
                        q.ask = price
                quotes[t] = q
    return quotes


def fetch_tick_sizes(client, token_ids: List[str]) -> Dict[str, float]:
    ticks: Dict[str, float] = {}
    for t in token_ids:
        try:
            ticks[t] = float(client.get_tick_size(t))
        except Exception:
            ticks[t] = DEFAULT_TICK
    return ticks


# ---------------------------------------------------------------------------
# 매도 계획 (순수 함수 - 네트워크 없음)
# ---------------------------------------------------------------------------

def floor_to_tick(price: float, tick: float) -> float:
    """SELL 은 틱 내림이 안전(더 체결 가능). [tick, 1-tick] 클램프는 봇과 동일."""
    floored = math.floor(price / tick + 1e-9) * tick
    return max(tick, min(round(floored, 4), round(1 - tick, 4)))


def plan_sales(
    positions: List[Position],
    quotes: Dict[str, Quote],
    mode: str = "bid",
    max_spread: float = 0.05,
    sweep_ticks: int = 2,
    tick_sizes: Optional[Dict[str, float]] = None,
) -> Buckets:
    tick_sizes = tick_sizes or {}
    buckets = Buckets()
    for pos in positions:
        if pos.redeemable:
            buckets.redeem.append(pos)
            continue
        sell_size = math.floor(pos.size * 100) / 100
        if sell_size < MIN_ORDER_SIZE:
            buckets.dust.append(pos)
            continue
        q = quotes.get(pos.token_id, Quote())
        tick = tick_sizes.get(pos.token_id, DEFAULT_TICK)
        if mode == "mid":
            if q.mid is None:
                buckets.no_book.append(pos)
                continue
            raw = q.mid
        else:  # bid / sweep 은 bid 필수
            if q.bid is None:
                buckets.no_book.append(pos)
                continue
            if q.spread is not None and q.spread > max_spread:
                buckets.wide.append(pos)
                continue
            raw = q.bid if mode == "bid" else q.bid - sweep_ticks * tick
        price = floor_to_tick(raw, tick)
        proceeds = sell_size * price
        cost = sell_size * max(q.mid - price, 0.0) if q.mid is not None else 0.0
        buckets.sellable.append(
            SellPlan(pos=pos, price=price, size=sell_size,
                     proceeds=round(proceeds, 2), cost_vs_mid=round(cost, 2))
        )
    return buckets


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------

def print_report(buckets: Buckets, quotes: Dict[str, Quote], mode: str):
    total_proceeds = sum(p.proceeds for p in buckets.sellable)
    total_cost = sum(p.cost_vs_mid for p in buckets.sellable)

    print(f"\n[매도 가능] {len(buckets.sellable)}개 | "
          f"예상 회수 ${total_proceeds:,.2f} (mode={mode}) | "
          f"즉시청산 비용(스프레드) ${total_cost:,.2f}")
    if buckets.sellable:
        print(f"  {'수량':>9}  {'매도가':>6}  {'bid':>6}  {'ask':>6}  "
              f"{'회수($)':>9}  {'비용($)':>7}  제목 (outcome)")
        for plan in sorted(buckets.sellable, key=lambda x: -x.proceeds):
            q = quotes.get(plan.pos.token_id, Quote())
            bid = f"{q.bid:.2f}" if q.bid is not None else "N/A"
            ask = f"{q.ask:.2f}" if q.ask is not None else "N/A"
            print(f"  {plan.size:>9.2f}  {plan.price:>6.2f}  {bid:>6}  {ask:>6}  "
                  f"{plan.proceeds:>9.2f}  {plan.cost_vs_mid:>7.2f}  "
                  f"{plan.pos.title} ({plan.pos.outcome})")

    def _section(name: str, items: List[Position], note: str):
        if not items:
            return
        value = sum(p.size * p.cur_price for p in items)
        print(f"\n[{name}] {len(items)}개 (표시가치 ${value:,.2f}) - {note}")
        for p in items:
            print(f"  {p.size:>9.2f}주 @ cur {p.cur_price:.2f}  {p.title} ({p.outcome})")

    _section("해결됨", buckets.redeem, "CLOB 매도 불가, Polymarket UI에서 redeem")
    _section("먼지", buckets.dust, f"{MIN_ORDER_SIZE:.0f}주 미만이라 주문 불가, redeem/보유")
    _section("호가 없음", buckets.no_book, "orderbook 없음 - 해결 대기 권장")
    _section("스프레드 과대", buckets.wide, "--max-spread 상향 또는 --mode mid 로 재시도")


def print_open_orders(orders: list):
    print(f"\n[미체결 주문] {len(orders)}건")
    for o in orders[:100]:
        try:
            matched = float(o.get("size_matched") or 0)
            original = float(o.get("original_size") or 0)
            print(f"  {o.get('side', '?'):<4} {original - matched:>9.2f}주 "
                  f"@ {float(o.get('price') or 0):.2f}  "
                  f"order={str(o.get('id', ''))[:16]}... "
                  f"token={str(o.get('asset_id', ''))[:16]}...")
        except (TypeError, ValueError):
            print(f"  {o}")


# ---------------------------------------------------------------------------
# 실행 (주문 취소 / 매도)
# ---------------------------------------------------------------------------

def cancel_all_orders(client, execute: bool) -> int:
    orders = client.get_open_orders() or []
    print_open_orders(orders)
    if not orders:
        return 0
    if not execute:
        print("  -> dry-run: 실제 취소는 --yes")
        return len(orders)
    resp = client.cancel_all()
    print(f"  -> cancel_all 완료: {resp}")
    return len(orders)


def execute_plans(client, plans: List[SellPlan]) -> List[dict]:
    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions
    results = []
    for plan in plans:
        tid = plan.pos.token_id
        record = {
            "token_id": tid,
            "condition_id": plan.pos.condition_id,
            "title": plan.pos.title,
            "outcome": plan.pos.outcome,
            "size": plan.size,
            "price": plan.price,
            "order_id": "",
            "status": "",
        }
        try:
            # neg-risk 마켓은 다른 exchange 컨트랙트를 쓰므로 명시 지정
            try:
                options = PartialCreateOrderOptions(neg_risk=bool(client.get_neg_risk(tid)))
            except Exception:
                options = None
            args = OrderArgs(token_id=tid, price=plan.price, size=plan.size, side="SELL")
            signed = client.create_order(args, options) if options else client.create_order(args)
            resp = client.post_order(signed, OrderType.GTC)
            if isinstance(resp, dict):
                record["order_id"] = str(resp.get("orderID") or "")
                ok = bool(resp.get("success") or resp.get("orderID"))
                record["status"] = "posted" if ok else f"rejected:{resp}"
            else:
                record["status"] = f"posted:{resp}"
            print(f"  SELL {plan.size:.2f}주 @ {plan.price:.2f} "
                  f"{plan.pos.title} ({plan.pos.outcome}) -> {record['status']}")
        except Exception as e:
            record["status"] = f"error:{e}"
            print(f"  SELL 실패 {plan.pos.title}: {e}")
        results.append(record)
        time.sleep(0.2)  # 연속 주문 rate limit 완화
    return results


def write_csv(funder: str, results: List[dict]) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"wind_down_{funder[:8]}_{ts}.csv"
    fieldnames = ["token_id", "condition_id", "title", "outcome",
                  "size", "price", "order_id", "status"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    return path


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------

def cmd_status(args, funder: str, private_key: Optional[str]) -> int:
    positions = fetch_positions(funder)
    print(f"계정 {funder[:10]}... 포지션 {len(positions)}개")
    client = make_readonly_client()
    live = [p.token_id for p in positions if not p.redeemable]
    quotes = fetch_quotes(client, live) if live else {}
    buckets = plan_sales(positions, quotes, mode=args.mode,
                         max_spread=args.max_spread, sweep_ticks=args.sweep_ticks)
    print_report(buckets, quotes, args.mode)

    if private_key:
        trading = make_trading_client(private_key, funder)
        print_open_orders(trading.get_open_orders() or [])
    else:
        print("\n(POLYMARKET_PRIVATE_KEY 미설정 - 미체결 주문 조회 생략)")
    return 0


def cmd_cancel(args, funder: str, private_key: str) -> int:
    client = make_trading_client(private_key, funder)
    cancel_all_orders(client, execute=args.yes)
    return 0


def cmd_flatten(args, funder: str, private_key: str) -> int:
    client = make_trading_client(private_key, funder)
    print("주의: 이 계정의 봇 Jenkins 잡을 먼저 중지/드레인했는지 확인하세요.")

    for round_no in range(1, args.rounds + 1):
        print(f"\n===== round {round_no}/{args.rounds} =====")
        # 미체결(이전 라운드의 잔여 매도 포함) 전량 취소 후 재호가
        cancel_all_orders(client, execute=args.yes)
        if args.yes:
            time.sleep(2)

        positions = fetch_positions(funder)
        live = [p.token_id for p in positions if not p.redeemable]
        quotes = fetch_quotes(client, live) if live else {}
        ticks = fetch_tick_sizes(client, live) if live else {}
        buckets = plan_sales(positions, quotes, mode=args.mode,
                             max_spread=args.max_spread,
                             sweep_ticks=args.sweep_ticks, tick_sizes=ticks)
        print_report(buckets, quotes, args.mode)

        if not buckets.sellable:
            print("\n매도 가능 포지션 없음 - 종료")
            return 0
        if not args.yes:
            print("\ndry-run 완료 - 실제 실행은 --yes")
            return 0

        results = execute_plans(client, buckets.sellable)
        csv_path = write_csv(funder, results)
        print(f"\n매도 기록 저장: {csv_path}")

        if round_no < args.rounds:
            print(f"{args.wait}s 대기 후 잔여분 재확인...")
            time.sleep(args.wait)

    print("\n지정한 라운드 종료. 잔여분은 status 로 확인하세요.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--env-file", help="봇 .env 경로 (예: golden-date/.env)")
    parser.add_argument("--funder", help="POLYMARKET_FUNDER_ADDRESS 오버라이드")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--mode", choices=["bid", "mid", "sweep"], default="bid")
        p.add_argument("--max-spread", type=float, default=0.05,
                       help="bid/sweep 모드에서 이 스프레드 초과 시장은 skip (기본 0.05)")
        p.add_argument("--sweep-ticks", type=int, default=2,
                       help="sweep 모드에서 bid 아래 몇 틱에 걸지 (기본 2)")

    p_status = sub.add_parser("status", help="포지션/미체결/예상 청산비용 리포트")
    add_common(p_status)

    p_cancel = sub.add_parser("cancel", help="미체결 주문 전량 취소")
    p_cancel.add_argument("--yes", action="store_true", help="실제 실행 (기본 dry-run)")

    p_flat = sub.add_parser("flatten", help="전량 취소 + 보유 포지션 일괄 매도")
    add_common(p_flat)
    p_flat.add_argument("--yes", action="store_true", help="실제 실행 (기본 dry-run)")
    p_flat.add_argument("--rounds", type=int, default=1,
                        help="취소->재호가->매도 반복 횟수 (기본 1)")
    p_flat.add_argument("--wait", type=int, default=60,
                        help="라운드 사이 대기 초 (기본 60)")

    args = parser.parse_args()

    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)

    funder = args.funder or os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip()
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip() or None
    if not funder:
        print("POLYMARKET_FUNDER_ADDRESS 가 필요합니다 (env 또는 --funder).")
        return 2
    if args.command in ("cancel", "flatten") and not private_key:
        print(f"{args.command} 는 POLYMARKET_PRIVATE_KEY 가 필요합니다.")
        return 2

    if args.command == "status":
        return cmd_status(args, funder, private_key)
    if args.command == "cancel":
        return cmd_cancel(args, funder, private_key)
    if args.command == "flatten":
        return cmd_flatten(args, funder, private_key)
    return 2


if __name__ == "__main__":
    sys.exit(main())
