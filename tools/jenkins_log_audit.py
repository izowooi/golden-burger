#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Jenkins 실행 로그를 봇별로 판정한다.

매 사이클 로그에서 아래를 뽑아 한 표로 만든다:
  - 실행된 커밋 / lifecycle mode / 사이클 결과
  - 매도 실패를 사유별로 (`매도 실패 진단` 로그)
  - CLOB intent 격리로 막힌 매도 (`신규 SELL 주문을 보류`)
  - 포지션 상한 포화 여부

파일명은 `<전략>-#<젠킨스빌드번호>.txt` 형식을 가정한다. 같은 전략이 서로 다른
파라미터로 두 job에서 돌 수 있으므로 빌드번호로 구분한다.

사용:
    uv run --script tools/jenkins_log_audit.py docs/logs/*.txt
"""

import re
import sys
from collections import Counter
from pathlib import Path

RE_COMMIT = re.compile(r"Checking out Revision ([0-9a-f]{12})")
RE_TIME = re.compile(r"^\[(20[0-9-]+ [0-9:]+)\]", re.M)
RE_REASON = re.compile(r"매도 실패 진단 - 사유=([a-z_]+)")
RE_QUARANTINE_START = re.compile(r"불확실한 CLOB intent는[^-]*- count=(\d+)")
RE_RECON = re.compile(
    r"주문 원장 대사 - 확인 (\d+), fill (\d+), 완료 (\d+), legacy gap (\d+), 오류 (\d+)"
)
RE_CYCLE = re.compile(
    r"'checked_holdings': (\d+), 'sold': (\d+), 'buy_candidates': (\d+), 'bought': (\d+)"
)
RE_MODE = re.compile(r"lifecycle_mode': '([a-z_]+)'")
RE_CAP = re.compile(r"포지션 현황 - 보유 (\d+)/(\d+)")
RE_MAXPOS = re.compile(r"최대 포지션 수 \((\d+)\) 도달")

# 사유별 심각도. 운영자 개입이 필요한 것과 자동 해소되는 것을 나눈다.
NEEDS_OPERATOR = {
    "quarantined_intent",   # polybot-retro resolve-intent 필요
    "market_gone",          # 시장 소멸 - 지갑 대조 후 정리
    "dust_unsellable",      # 최소 주문량 미만
    "locked_in_own_orders",  # 기존 미체결 주문 취소 필요
}
SELF_HEALING = {"partial_balance", "balance_edge", "transient"}


def audit(path: Path) -> dict:
    text = path.read_text(errors="replace")
    stem = path.stem
    strat = stem.split("-#")[0] if "-#" in stem else stem
    build = stem.split("-#")[1] if "-#" in stem else "?"

    reasons = Counter(RE_REASON.findall(text))
    # 격리는 clob_client와 trader가 각각 로그를 남겨 2배로 세어진다.
    blocked = text.count("신규 SELL 주문을 보류") // 2
    if blocked:
        reasons["quarantined_intent"] = blocked
        # "other"로 분류된 것의 실체가 격리다. 중복 계상 방지.
        reasons["other"] = max(0, reasons.get("other", 0) - blocked)
        if reasons["other"] == 0:
            del reasons["other"]

    m_q = RE_QUARANTINE_START.search(text)
    m_r = RE_RECON.search(text)
    m_c = RE_CYCLE.search(text)
    m_cap = RE_CAP.search(text)

    return {
        "strategy": strat,
        "build": build,
        "commit": (RE_COMMIT.search(text) or [None, "?"])[1],
        "time": (RE_TIME.search(text) or [None, "?"])[1],
        "mode": (RE_MODE.search(text) or [None, "?"])[1],
        "ok": "Finished: SUCCESS" in text,
        "quarantined_intents": int(m_q.group(1)) if m_q else 0,
        "recon_errors": int(m_r.group(5)) if m_r else 0,
        "holdings": int(m_c.group(1)) if m_c else None,
        "sold": int(m_c.group(2)) if m_c else None,
        "candidates": int(m_c.group(3)) if m_c else None,
        "bought": int(m_c.group(4)) if m_c else None,
        "cap": (int(m_cap.group(1)), int(m_cap.group(2))) if m_cap else None,
        "cap_hit": bool(RE_MAXPOS.search(text)),
        "reasons": reasons,
    }


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return 2
    rows = sorted((audit(p) for p in paths),
                  key=lambda r: (-sum(r["reasons"].values()), r["strategy"]))

    print("Jenkins 로그 판정")
    print("=" * 96)
    hdr = (f"{'전략':<12}{'빌드':>8}{'모드':>12}{'보유':>6}{'매도':>5}{'매수':>5}"
           f"{'격리intent':>10}{'대사오류':>9}{'매도실패':>9}")
    print(hdr)
    print("-" * 96)
    for r in rows:
        fails = sum(r["reasons"].values())
        cap = "!" if r["cap_hit"] else " "
        print(f"{r['strategy']:<12}{r['build']:>8}{r['mode']:>12}"
              f"{str(r['holdings'] or '-'):>5}{cap}{str(r['sold'] or 0):>5}"
              f"{str(r['bought'] or 0):>5}{r['quarantined_intents']:>10}"
              f"{r['recon_errors']:>9}{fails:>9}")
    print("-" * 96)
    print("보유 뒤 '!' = 포지션 상한 도달 로그가 있음\n")

    print("매도 실패 사유 (운영자 개입 필요 항목은 ⛔)")
    print("-" * 96)
    for r in rows:
        if not r["reasons"]:
            continue
        parts = []
        for k, v in r["reasons"].most_common():
            mark = "⛔" if k in NEEDS_OPERATOR else ("✔" if k in SELF_HEALING else "")
            parts.append(f"{k}={v}{mark}")
        print(f"  {r['strategy']}-#{r['build']:<8} " + ", ".join(parts))

    clean = [r for r in rows if not r["reasons"]]
    if clean:
        print("\n매도 실패 0건:")
        for r in clean:
            print(f"  {r['strategy']}-#{r['build']} (mode={r['mode']})")

    tot = Counter()
    for r in rows:
        tot.update(r["reasons"])
    if tot:
        print("\n전체 합계")
        print("-" * 96)
        for k, v in tot.most_common():
            mark = " ⛔운영자 개입 필요" if k in NEEDS_OPERATOR else (
                " ✔자동 해소" if k in SELF_HEALING else "")
            print(f"  {k:<22}{v:>6}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
