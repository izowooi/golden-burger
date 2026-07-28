#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""매도 무한 재시도 루프를 봇 DB에서 진단한다.

증상: 매도 거절이 trade 상태를 바꾸지 않으므로 HOLDING으로 남아 매 사이클 같은
주문을 반복 제출한다. golden-cherry 실측(2026-07-11~28): 실패한 SELL 제출
73,238건 / 401 token = 토큰당 평균 182.6회, 최대 4,002회.

거절 사유를 분류해 '재시도해도 절대 성공하지 않는 것'과 '수량을 줄이면 팔리는
것'을 나눈다. 로그가 아니라 DB로 판정하므로 과거 전체 구간을 한 번에 볼 수 있다.

사용:
    uv run --script tools/sell_retry_audit.py <trades.db> [...]
    uv run --script tools/sell_retry_audit.py golden-*/data/default/trades.db
"""

import re
import sqlite3
import sys
from collections import Counter, defaultdict

MIN_ORDER_SIZE = 5.0
SCALE = 1_000_000
BAL_RE = re.compile(r"balance:\s*(\d+)\s*,\s*order amount:\s*(\d+)", re.I)
GONE = ("invalid token id", "orderbook id does not exist")


def classify(msg: str):
    """(사유, 가용주식수, 요청주식수)."""
    low = (msg or "").lower()
    if any(g in low for g in GONE):
        return "market_gone", None, None
    if "not enough balance" not in low:
        if "not ready" in low or "request exception" in low:
            return "transient", None, None
        return "other", None, None
    m = BAL_RE.search(msg or "")
    if not m:
        return "balance_unparsed", None, None
    avail = int(m.group(1)) / SCALE
    want = int(m.group(2)) / SCALE
    if avail <= 0:
        return "zero_balance", avail, want
    if avail < MIN_ORDER_SIZE:
        return "dust_unsellable", avail, want
    if avail < want:
        return "partial_balance", avail, want
    return "balance_edge", avail, want


PERMANENT = {"market_gone", "dust_unsellable"}
FIXABLE = {"partial_balance", "balance_edge"}


def audit(path: str) -> None:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT token_id, error_message, submitted_at FROM order_submissions "
            "WHERE side='SELL' AND success=0 AND simulation=0"
        ).fetchall()
    except sqlite3.Error as e:
        print(f"  ! 조회 실패: {e}")
        return

    name = path.split("/")[0]
    if not rows:
        print(f"\n### {name}: 실패한 매도 제출 0건 — 이상 없음")
        return

    reasons = Counter()
    per_token = defaultdict(Counter)
    for token, msg, _ in rows:
        r, _a, _w = classify(msg)
        reasons[r] += 1
        per_token[token][r] += 1

    total = sum(reasons.values())
    tokens = len(per_token)
    print(f"\n### {name}")
    print(f"  실패한 매도 제출 {total:,}건 / {tokens} token "
          f"= 토큰당 평균 {total / tokens:.1f}회")
    perm = sum(v for k, v in reasons.items() if k in PERMANENT)
    fix = sum(v for k, v in reasons.items() if k in FIXABLE)
    print(f"  영구 실패(재시도 무의미) {perm:,}건 · "
          f"수량 축소로 해결 가능 {fix:,}건")
    for r, n in reasons.most_common():
        mark = " ⛔영구" if r in PERMANENT else (" ✔축소가능" if r in FIXABLE else "")
        print(f"    {r:<18} {n:>8,}{mark}")

    worst = sorted(per_token.items(), key=lambda kv: -sum(kv[1].values()))[:5]
    if worst and sum(worst[0][1].values()) >= 20:
        print("  재시도 상위 token:")
        for token, c in worst:
            n = sum(c.values())
            if n < 20:
                continue
            top = c.most_common(1)[0][0]
            print(f"    {str(token)[:18]}… {n:>6,}회  주사유={top}")


def main() -> int:
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 2
    print("매도 재시도 루프 진단")
    print("=" * 60)
    for p in paths:
        audit(p)
    print("\n영구 실패가 많으면 그 포지션은 max_positions를 잠식한 채 청산되지 않는다.")
    print("축소 가능이 많으면 trader.py의 _place_sell_with_balance_retry가 동작하는지 확인할 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
