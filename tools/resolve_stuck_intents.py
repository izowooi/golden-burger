#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "py-clob-client-v2>=1.0.1",
#     "requests>=2.31",
#     "python-dotenv>=1.0",
# ]
# ///
"""매도를 영구히 막고 있는 CLOB intent 격리를 증거 기반으로 해제한다.

## 왜 필요한가

CLOB에 주문을 POST했는데 응답이 5xx나 timeout으로 끝나면, 봇은 "주문이 들어갔는지
모른다"는 상태(`SUBMIT_OUTCOME_UNKNOWN`)로 기록하고 **같은 token/side의 신규 주문을
영구히 차단**한다. 중복 주문을 막는 올바른 설계지만, 시간 기반 해제가 없어서 5xx
한 번이 그 포지션의 청산을 영원히 막는다.

`reconcile_order_ledger`는 `needs_reconciliation=1 AND order_id IS NOT NULL`만
처리하므로 이 상태(order_id NULL, needs_reconciliation=0)는 **대사가 조회조차 하지
않는다.** 사람이 개입해야만 풀린다.

2026-07-28 실측: 함대 전체 매도 실패 113건 중 111건이 이 격리였다.

## 어떻게 안전하게 푸는가

핵심 질문은 하나다 — **그 주문이 실제로 거래소에 들어갔는가?**

이 스크립트는 추측하지 않고 **거래소의 열린 주문 목록을 직접 조회해서 대조**한다.

  - 해당 token/side에 열린 주문이 **없다** → 주문이 안 들어간 것이 확인됨.
    `NO_ORDER_CREATED`로 해제해도 중복 주문 위험이 없다.
  - 열린 주문이 **있다** → 주문이 들어간 것이다. 자동 해제하지 않고 보고만 한다
    (order_id 연결은 `polybot-retro resolve-intent --resolution ORDER_ID_LINKED`).

주의: 거래소가 이미 체결·취소해서 목록에서 사라졌을 수도 있다. 그 경우 "없음"이
"안 들어갔음"을 100% 증명하지는 않는다. 다만 **매도 주문**은 보유 수량을 넘겨
팔 수 없으므로 중복 주문의 실질 위험이 낮고, 격리를 방치하면 포지션이 영구히
청산되지 않는다. 이 trade-off를 알고 실행할 것.

## 사용

    # 1) 조회만 (기본) - 무엇이 막혀 있고 거래소에 주문이 있는지 본다
    uv run --script tools/resolve_stuck_intents.py \\
      --db golden-elderberry/data/default/trades.db --strategy golden-elderberry

    # 2) 해제 - 출력이 알려준 확인 문구를 그대로 붙인다
    uv run --script tools/resolve_stuck_intents.py \\
      --db golden-elderberry/data/default/trades.db --strategy golden-elderberry \\
      --execute --confirm RESOLVE_12

환경변수 `POLYMARKET_PRIVATE_KEY` / `POLYMARKET_FUNDER_ADDRESS` /
`POLYMARKET_SIGNATURE_TYPE` 이 필요하다 (거래소 조회용). 주문은 내지 않는다.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def make_client(private_key: str, funder: str):
    from py_clob_client_v2 import ClobClient
    sig = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "1"))
    client = ClobClient(
        host=CLOB_HOST, chain_id=CHAIN_ID, key=private_key,
        signature_type=sig, funder=funder,
    )
    client.set_api_creds(client.create_or_derive_api_key())
    return client


# 격리 predicate A: 대사가 조회조차 하지 않는 상태
STUCK_SQL = """
SELECT s.submission_id, s.token_id, s.side, s.submitted_at, s.error_message,
       s.requested_size, s.requested_price
FROM order_submissions s
WHERE s.simulation = 0
  AND s.response_status IN ('INTENT', 'SUBMIT_OUTCOME_UNKNOWN')
  AND s.order_id IS NULL
  AND s.outcome_resolution IS NULL
ORDER BY s.submitted_at
"""

BLOCKED_TRADES_SQL = """
SELECT token_id, COUNT(*) n, SUM(buy_amount) amt
FROM trades
WHERE status IN ('PENDING_BUY','HOLDING','PENDING_SELL')
GROUP BY token_id
"""


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--strategy", required=True, help="로그 표기용 전략명")
    ap.add_argument("--side", default="SELL", choices=["SELL", "BUY", "ALL"],
                    help="해제 대상 side (기본 SELL - 청산을 막는 쪽)")
    ap.add_argument("--execute", action="store_true", help="실제 해제 (기본은 조회만)")
    ap.add_argument("--confirm", help="--execute 시 필수. RESOLVE_<건수>")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"DB 없음: {args.db}", file=sys.stderr)
        return 2

    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
    funder = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "").strip()
    if not key or not funder:
        print("POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER_ADDRESS 필요",
              file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    stuck = [dict(r) for r in conn.execute(STUCK_SQL)]
    if args.side != "ALL":
        stuck = [s for s in stuck if str(s["side"]).upper() == args.side]

    print(f"=== {args.strategy} 격리 intent 진단 ===")
    print(f"DB: {args.db}")
    if not stuck:
        print("\n격리된 intent 없음 — 조치 불필요")
        return 0

    blocked = {r["token_id"]: (r["n"], r["amt"] or 0)
               for r in conn.execute(BLOCKED_TRADES_SQL)}

    print(f"\n격리된 {args.side} intent: {len(stuck)}건")
    print("거래소 열린 주문 조회 중...")
    try:
        client = make_client(key, funder)
        open_orders = client.get_open_orders() or []
    except Exception as e:  # noqa: BLE001
        print(f"열린 주문 조회 실패: {e}", file=sys.stderr)
        print("거래소 상태를 확인할 수 없으면 해제하지 않는다.", file=sys.stderr)
        return 3

    live = {}
    for o in open_orders:
        tok = str(o.get("asset_id") or o.get("token_id") or "")
        side = str(o.get("side", "")).upper()
        live.setdefault((tok, side), []).append(o)
    print(f"거래소 열린 주문: {len(open_orders)}건\n")

    now = datetime.now(timezone.utc)
    safe, has_order = [], []
    hdr = f"{'제출시각':<18}{'경과':>7}{'token':<20}{'요청수량':>12}{'거래소주문':>10}{'막힌포지션':>10}"
    print(hdr)
    print("-" * len(hdr))
    for s in stuck:
        tok = str(s["token_id"])
        ts = parse_ts(s["submitted_at"])
        age = f"{(now - ts).days}일" if ts else "?"
        key_t = (tok, str(s["side"]).upper())
        n_live = len(live.get(key_t, []))
        n_block, amt = blocked.get(tok, (0, 0))
        (has_order if n_live else safe).append(s)
        print(f"{str(s['submitted_at'])[:16]:<18}{age:>7}{tok[:18]+'…':<20}"
              f"{float(s['requested_size'] or 0):>12.4f}"
              f"{('있음(' + str(n_live) + ')') if n_live else '없음':>10}"
              f"{str(n_block) + '건':>10}")

    print("-" * len(hdr))
    print(f"\n거래소에 주문 없음 → 해제 안전: {len(safe)}건")
    print(f"거래소에 주문 있음 → 자동 해제 안 함: {len(has_order)}건")
    if has_order:
        print("  (이쪽은 주문이 실재하므로 order_id 연결이 필요하다:")
        print("   polybot-retro resolve-intent --resolution ORDER_ID_LINKED --order-id <id>)")

    if not args.execute:
        print(f"\n조회만 수행했다. 해제하려면:")
        print(f"  --execute --confirm RESOLVE_{len(safe)}")
        return 0

    if args.confirm != f"RESOLVE_{len(safe)}":
        print(f"\n--confirm 불일치. 해제 대상은 {len(safe)}건이므로 "
              f"--confirm RESOLVE_{len(safe)} 가 필요하다.", file=sys.stderr)
        print("(대상 건수는 거래소 상태에 따라 바뀐다. 실행 직전 다시 조회할 것.)",
              file=sys.stderr)
        return 2
    if not safe:
        print("\n해제할 대상이 없다.")
        return 0

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup = args.db.with_name(f"{args.db.stem}.{stamp}.pre-resolve.db")
    shutil.copy2(args.db, backup)
    print(f"\n백업: {backup}")

    reason = (f"operator: exchange open-order check showed no live "
              f"{args.side} order for this token at {stamp}")
    iso = now.strftime("%Y-%m-%dT%H:%M:%S.%f000+00:00")
    with conn:
        conn.executemany(
            "UPDATE order_submissions SET outcome_resolution='NO_ORDER_CREATED', "
            "outcome_resolved_at=?, outcome_resolution_reason=? "
            "WHERE submission_id=?",
            [(iso, reason, s["submission_id"]) for s in safe],
        )
    print(f"해제 완료: {len(safe)}건 → NO_ORDER_CREATED")
    print("다음 사이클부터 해당 token/side의 매도가 다시 시도된다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
