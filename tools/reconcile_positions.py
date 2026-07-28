#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///
"""봇 DB의 오픈 포지션을 지갑 실보유와 대조해 정리한다.

왜 필요한가
-----------
`trades` 행은 실제 체결 없이도 `HOLDING`으로 남는다. 매수 GTC가 체결되지 않았거나
매도 GTC가 `orderID`만 받고 끝난 경우가 그렇다. 이런 행도 `max_positions`와
`max_open_notional_usdc`를 소모하므로, 쌓이면 봇이 신규 진입을 못 하고 멈춘다.

지갑 실보유가 유일한 권위다. `order_fills`는 계측 시작 이전 구간이 비어 있고
이후에도 누락이 있으므로 "fill 기록 없음"을 미체결의 근거로 쓰면 안 된다.

읽기 전용 공개 API만 쓴다. **private key가 필요 없고 주문도 내지 않는다.**

사용
----
    uv run tools/reconcile_positions.py --db golden-cherry/data/default/trades.db \\
        --funder 0x...                                   # 조회만 (기본)
    uv run tools/reconcile_positions.py --db ... --funder 0x... --execute --confirm CLOSE_183

`--execute`는 지갑 잔고가 없는 오픈 행만 종결 처리한다. 지갑에 있는 행은 절대 건드리지 않는다.
"""

import argparse
import json
import shutil
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

POSITIONS_URL = "https://data-api.polymarket.com/positions"
OPEN_STATUSES = ("PENDING_BUY", "HOLDING", "PENDING_SELL", "QUARANTINED")
# 지갑에 남은 티끌은 종결 대상이 아니다. CLOB 최소 주문 수량과 같은 기준을 쓴다.
DUST_SHARES = 5.0


def fetch_positions(funder: str) -> dict:
    """지갑의 모든 포지션을 token_id -> position 으로. 공개 endpoint, 인증 불필요."""
    out = {}
    offset = 0
    while True:
        qs = urllib.parse.urlencode(
            {"user": funder, "sizeThreshold": 0.0, "limit": 500, "offset": offset}
        )
        req = urllib.request.Request(
            f"{POSITIONS_URL}?{qs}", headers={"User-Agent": "polybot-reconcile/1"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read())
        if not page:
            break
        for p in page:
            out[str(p.get("asset"))] = p
        if len(page) < 500:
            break
        offset += 500
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--funder", required=True, help="POLYMARKET_FUNDER_ADDRESS (공개 주소)")
    ap.add_argument("--execute", action="store_true", help="실제 DB 갱신 (기본은 조회만)")
    ap.add_argument("--confirm", help="--execute 시 필수. CLOSE_<종결할 건수>")
    ap.add_argument("--reason", default="operator: wallet reconciliation")
    ap.add_argument("--sync-held", action="store_true",
                    help="유지되는 행의 buy_shares/buy_amount를 지갑 실보유로 맞춘다. "
                         "부분체결된 대형 주문이 요청액 그대로 남아 max_open_notional_usdc를 "
                         "잠식하는 것을 푼다. buy_price는 건드리지 않으므로 손절·익절 "
                         "발동 기준은 변하지 않는다.")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"DB 없음: {args.db}", file=sys.stderr)
        return 2
    if args.funder.startswith("0x") is False or len(args.funder) != 42:
        print(f"funder 주소 형식이 이상하다: {args.funder}", file=sys.stderr)
        return 2

    wallet = fetch_positions(args.funder)
    live = {t: p for t, p in wallet.items()
            if not p.get("redeemable") and float(p.get("size") or 0) >= DUST_SHARES}
    print(f"지갑 포지션 {len(wallet)}건 (그중 유효 보유 {len(live)}건, "
          f"평가액 ${sum(float(p.get('currentValue') or 0) for p in live.values()):,.2f})")

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    rows = conn.execute(
        f"SELECT id, token_id, status, buy_price, buy_amount, buy_shares, question "
        f"FROM trades WHERE status IN ({placeholders})", OPEN_STATUSES
    ).fetchall()
    print(f"DB 오픈 행 {len(rows)}건 ({', '.join(OPEN_STATUSES)})")

    # 체결 증거 유무로 나눈다. 둘을 같은 status로 뭉치면 회고가 오염된다.
    filled_tokens = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT s.token_id FROM order_submissions s "
            "JOIN order_fills f ON f.submission_id=s.submission_id "
            "WHERE f.status='CONFIRMED' AND f.side='BUY'")
    }

    keep, no_fill, closed_off = [], [], []
    for r in rows:
        if str(r["token_id"]) in live:
            keep.append(r)
        elif str(r["token_id"]) in filled_tokens:
            closed_off.append(r)   # 체결됐던 것이 지갑에 없다 = 해결·상환 등으로 이미 종료
        else:
            no_fill.append(r)      # 체결 증거도 없고 지갑에도 없다

    close = no_fill + closed_off
    print(f"\n  지갑에 실재 → 유지                     : {len(keep):4d}건")
    print(f"  체결기록 O, 지갑 X → COMPLETED 종결     : {len(closed_off):4d}건")
    print(f"  체결기록 X, 지갑 X → UNFILLED 종결      : {len(no_fill):4d}건 "
          f"(요청 원금 ${sum(r['buy_amount'] or 0 for r in no_fill):,.0f})")

    by_status = {}
    for r in close:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    for s, n in sorted(by_status.items()):
        print(f"      (원래 status: {s:<14} {n:4d}건)")

    print("\n  ※ 체결기록 X가 곧 미체결은 아니다. order_fills 계측 이전 매수는 기록 자체가 없다.")
    print("    다만 지갑에도 없으므로 어느 쪽이든 오픈 노출로 잡아둘 이유는 없다.")

    # 유지 행의 DB 수량 vs 지갑 수량. 요청액 그대로 남은 부분체결 주문을 드러낸다.
    sync = []
    print("\n  유지되는 행 (DB 수량 → 지갑 실보유):")
    for r in keep:
        p = live[str(r["token_id"])]
        wsz = float(p.get("size") or 0)
        dsz = float(r["buy_shares"] or 0)
        new_amt = float(r["buy_price"] or 0) * wsz
        print(f"      #{r['id']:<5} ${float(p.get('currentValue') or 0):>9,.2f}  "
              f"{dsz:>9.1f} → {wsz:>9.1f}주   "
              f"요청 ${float(r['buy_amount'] or 0):>8,.0f} → ${new_amt:>8,.0f}  "
              f"{(r['question'] or '')[:36]}")
        if abs(dsz - wsz) > 1e-6:
            sync.append((r["id"], wsz, new_amt))

    cur_notional = sum(float(r["buy_amount"] or 0) for r in keep)
    new_notional = sum(
        float(r["buy_price"] or 0) * float(live[str(r["token_id"])].get("size") or 0)
        for r in keep)
    print(f"\n  오픈 요청 원금 합계: ${cur_notional:,.0f}"
          f"  → 동기화 시 ${new_notional:,.0f}")
    if sync and not args.sync_held:
        print(f"  ※ {len(sync)}건이 지갑과 어긋난다. --sync-held 를 붙이면 맞춘다.")
        print("    (max_open_notional_usdc는 요청액 기준이라, 안 맞추면 신규 매수가 계속 막힌다.)")

    if not args.execute:
        print(f"\n조회만 수행했다. 실제 종결하려면:")
        print(f"  --execute --confirm CLOSE_{len(close)}")
        return 0

    if args.confirm != f"CLOSE_{len(close)}":
        print(f"\n--confirm 불일치. 지금 종결 대상은 {len(close)}건이므로 "
              f"--confirm CLOSE_{len(close)} 가 필요하다.", file=sys.stderr)
        print("(대상 건수는 지갑 상태에 따라 바뀐다. 실행 직전에 다시 조회할 것.)", file=sys.stderr)
        return 2
    if not close and not (args.sync_held and sync):
        print("\n변경할 것이 없다.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = args.db.with_name(f"{args.db.stem}.{stamp}.pre-reconcile.db")
    shutil.copy2(args.db, backup)
    print(f"\n백업: {backup}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with conn:
        # realized_pnl은 손대지 않는다. 성과는 order_fills로만 계산하며,
        # 여기서 숫자를 만들어 넣으면 evidence gap을 추정값으로 메우는 셈이 된다.
        conn.executemany(
            "UPDATE trades SET status='UNFILLED', exit_reason=?, updated_at=? WHERE id=?",
            [("wallet_reconciled_no_fill_evidence", now, r["id"]) for r in no_fill],
        )
        conn.executemany(
            "UPDATE trades SET status='COMPLETED', exit_reason=?, updated_at=? WHERE id=?",
            [("wallet_reconciled_closed_offledger", now, r["id"]) for r in closed_off],
        )
        if args.sync_held and sync:
            conn.executemany(
                "UPDATE trades SET buy_shares=?, buy_amount=?, updated_at=? WHERE id=?",
                [(sz, amt, now, tid) for tid, sz, amt in sync],
            )
    remaining = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE status IN ({placeholders})", OPEN_STATUSES
    ).fetchone()[0]
    print(f"종결 완료: UNFILLED {len(no_fill)}건 + COMPLETED {len(closed_off)}건 = {len(close)}건")
    if args.sync_held and sync:
        print(f"수량 동기화: {len(sync)}건 → 오픈 요청 원금 ${new_notional:,.0f}")
        print(f"  POLYBOT_MAX_OPEN_NOTIONAL_USDC 는 이 값보다 커야 신규 매수가 된다.")
    print(f"남은 오픈 노출: {remaining}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
