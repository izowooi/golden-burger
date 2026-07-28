#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""golden-lime barrier 시뮬레이션 — 실제 가격 경로로 전략을 그대로 재생한다.

평균 전방 수익률은 분포가 좌편향이면 오해를 부른다. 전략이 실제로 하는 일은
"TP와 SL 중 어느 쪽에 먼저 닿는가"이므로, market_snapshots의 실제 경로를 따라가며
그 순서를 센다. 그러면 승률을 손익분기(=SL/(TP+SL))와 직접 비교할 수 있다.

관측 간격이 평균 39분이므로 5분 폴링보다 성기다. 즉 이 시뮬레이션은 갭을
과소평가하며, 실제 성과는 여기서 나온 값보다 나쁘다(낙관적 상한).

사용:
    uv run --script tools/lime_barrier_sim.py [trades.db]
"""

import sqlite3
import sys
from collections import defaultdict, deque
from datetime import timedelta

sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

DB = sys.argv[1] if len(sys.argv) > 1 else \
    "/Users/izowooi/git/t1/golden-lime/data/default/trades.db"

JUMP_WINDOW_H = 6.0
HOLD_WINDOW_MIN = 60
VOL_AVG_WINDOW_H = 24.0
MIN_PTS = 12
MAX_HOLD_H = 48.0          # 이 안에 어느 barrier에도 안 닿으면 censored
ROUND_TRIP_COST = 0.015    # 포인트. ask 진입 1틱 + midpoint 청산 0.5틱


def parse_ts(s):
    from datetime import datetime
    s = str(s).replace("T", " ").replace("Z", "").split("+")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


conn = sqlite3.connect(DB)
rows = conn.execute(
    "SELECT condition_id, probability, volume_24h, timestamp "
    "FROM market_snapshots ORDER BY condition_id, timestamp"
).fetchall()
SERIES = defaultdict(list)
for cid, prob, vol, ts in rows:
    t = parse_ts(ts)
    if t is None or prob is None:
        continue
    SERIES[cid].append((t, float(prob), float(vol or 0)))
for v in SERIES.values():
    v.sort(key=lambda x: x[0])
print(f"snapshot {len(rows):,}행 / 시장 {len(SERIES):,}개")


def detect(jump_min, vol_mult_min, max_pullback, base_min, base_max,
           current_max=0.85, base_mode="min"):
    events = []
    for cid, pts in SERIES.items():
        n = len(pts)
        if n < MIN_PTS:
            continue
        jmin, hmax = deque(), deque()
        lo_j = lo_h = lo_v = 0
        vol_sum = 0.0
        vol_cnt = 0
        for i in range(n):
            ts, p, v = pts[i]
            while jmin and pts[jmin[-1]][1] >= p:
                jmin.pop()
            jmin.append(i)
            while hmax and pts[hmax[-1]][1] <= p:
                hmax.pop()
            hmax.append(i)
            vol_sum += v
            vol_cnt += 1
            j0 = ts - timedelta(hours=JUMP_WINDOW_H)
            while pts[lo_j][0] < j0:
                lo_j += 1
            while jmin and jmin[0] < lo_j:
                jmin.popleft()
            h0 = ts - timedelta(minutes=HOLD_WINDOW_MIN)
            while pts[lo_h][0] < h0:
                lo_h += 1
            while hmax and hmax[0] < lo_h:
                hmax.popleft()
            v0 = ts - timedelta(hours=VOL_AVG_WINDOW_H)
            while pts[lo_v][0] < v0:
                vol_sum -= pts[lo_v][2]
                vol_cnt -= 1
                lo_v += 1
            if i - lo_j < 5 or i - lo_h < 2:
                continue
            base = pts[lo_j][1] if base_mode == "open" else pts[jmin[0]][1]
            if not (base_min <= base <= base_max):
                continue
            if p > current_max or p - base < jump_min:
                continue
            peak = pts[hmax[0]][1]
            if peak <= 0 or (peak - p) / peak > max_pullback:
                continue
            if vol_mult_min > 0 and vol_cnt > 0:
                avg = vol_sum / vol_cnt
                if avg > 0 and v < avg * vol_mult_min:
                    continue
            events.append((cid, ts, p, i))
    seen, uniq = {}, []
    for cid, ts, p, i in sorted(events, key=lambda e: (e[0], e[1])):
        if cid in seen and (ts - seen[cid]) < timedelta(hours=24):
            continue
        seen[cid] = ts
        uniq.append((cid, ts, p, i))
    return uniq


def simulate(events, tp_pct, sl_pct, trailing_pct=None):
    """실제 경로를 따라가며 TP/SL/트레일링 중 무엇에 먼저 닿는지 센다."""
    win = loss = censored = 0
    gross = 0.0
    for cid, ts0, entry, idx in events:
        pts = SERIES[cid]
        tp = entry * (1 + tp_pct)
        sl = entry * (1 - sl_pct)
        peak = entry
        outcome = None
        for t, p, _ in pts[idx + 1:]:
            if (t - ts0).total_seconds() / 3600 > MAX_HOLD_H:
                break
            if p > peak:
                peak = p
            if p <= sl:
                outcome = -sl_pct
                break
            if trailing_pct and p < peak * (1 - trailing_pct):
                outcome = (p - entry) / entry
                break
            if p >= tp:
                outcome = tp_pct
                break
        if outcome is None:
            censored += 1
            continue
        gross += outcome
        if outcome > 0:
            win += 1
        else:
            loss += 1
    n = win + loss
    if n == 0:
        return None
    wr = win / n
    be = sl_pct / (tp_pct + sl_pct)
    avg_gross = gross / n
    # 왕복비용을 진입가 대비 상대값으로. 이벤트 평균 진입가로 근사한다.
    avg_entry = sum(e[2] for e in events) / len(events)
    c = ROUND_TRIP_COST / avg_entry
    return {
        "n": n, "censored": censored, "wr": wr, "be": be,
        "gross": avg_gross, "net": avg_gross - c, "c": c, "entry": avg_entry,
    }


GRID = [
    ("운영값 (jump.10 vol2.0 pb.02 base.15-.70)", dict(jump_min=0.10, vol_mult_min=2.0, max_pullback=0.02, base_min=0.15, base_max=0.70)),
    ("제안값 (jump.05 vol1.5 pb.02 base.40-.70)", dict(jump_min=0.05, vol_mult_min=1.5, max_pullback=0.02, base_min=0.40, base_max=0.70)),
    ("제안값+open  (base_mode=open)", dict(jump_min=0.05, vol_mult_min=1.5, max_pullback=0.02, base_min=0.40, base_max=0.70, base_mode="open")),
    ("jump 0.15 / base .40-.65", dict(jump_min=0.15, vol_mult_min=0.0, max_pullback=0.02, base_min=0.40, base_max=0.65)),
    ("jump 0.20 / base .35-.65", dict(jump_min=0.20, vol_mult_min=0.0, max_pullback=0.02, base_min=0.35, base_max=0.65)),
    ("jump 0.20 / vol 3.0 / base .35-.65", dict(jump_min=0.20, vol_mult_min=3.0, max_pullback=0.02, base_min=0.35, base_max=0.65)),
    ("고가 진입만 base .55-.75", dict(jump_min=0.10, vol_mult_min=0.0, max_pullback=0.02, base_min=0.55, base_max=0.75)),
]

EXITS = [
    ("TP+12 / SL-8  + 트레일링6 (운영)", 0.12, 0.08, 0.06),
    ("TP+12 / SL-12 (제안)", 0.12, 0.12, None),
    ("TP+20 / SL-20", 0.20, 0.20, None),
    ("TP+30 / SL-30", 0.30, 0.30, None),
]

print(f"\n왕복비용 가정 c = {ROUND_TRIP_COST} 포인트 (진입가로 나눠 상대화)")
print(f"보유 상한 {MAX_HOLD_H:.0f}h. 그 안에 미도달이면 censored로 제외.\n")

for label, kw in GRID:
    ev = detect(**kw)
    if len(ev) < 20:
        print(f"### {label}: 이벤트 {len(ev)}건 — 표본 부족\n")
        continue
    print(f"### {label} — 이벤트 {len(ev)}건, 평균 진입가 "
          f"{sum(e[2] for e in ev) / len(ev):.3f}")
    print(f"  {'청산 설정':<32}{'n':>5}{'미도달':>7}{'승률':>8}{'손익분기':>9}"
          f"{'총수익':>9}{'비용':>8}{'순수익':>9}")
    print("  " + "-" * 87)
    for elabel, tp, sl, tr in EXITS:
        r = simulate(ev, tp, sl, tr)
        if not r:
            continue
        flag = " ★" if r["net"] > 0 else ""
        print(f"  {elabel:<32}{r['n']:>5}{r['censored']:>7}{100 * r['wr']:>7.1f}%"
              f"{100 * r['be']:>8.1f}%{100 * r['gross']:>8.2f}%"
              f"{100 * r['c']:>7.2f}%{100 * r['net']:>8.2f}%{flag}")
    print()


# ── 강건성: 다중검정 보정과 기간 분할 ──────────────────────────────
print("\n" + "=" * 78)
print("강건성 검정 — 위에서 ★가 나온 셀이 진짜인가")
print("=" * 78)


def z_vs_breakeven(win_rate, n, be):
    if n <= 0:
        return 0.0
    se = (be * (1 - be) / n) ** 0.5
    return (win_rate - be) / se if se > 0 else 0.0


BEST = dict(jump_min=0.05, vol_mult_min=1.5, max_pullback=0.02,
            base_min=0.40, base_max=0.70)
ev = detect(**BEST)
r = simulate(ev, 0.20, 0.20, None)
z = z_vs_breakeven(r["wr"], r["n"], r["be"])
n_cells = 7 * 4
print(f"\n대상: 제안값 + TP+20/SL-20  (승률 {100 * r['wr']:.1f}%, n={r['n']})")
print(f"  단일 검정 z = {z:.2f}  (양측 p ≈ {2 * (1 - 0.5 * (1 + __import__('math').erf(abs(z) / 2 ** 0.5))):.3f})")
print(f"  그러나 격자에서 {n_cells}개 셀을 검정했다.")
print(f"  Bonferroni 보정 유의수준 α/{n_cells} = {0.05 / n_cells:.4f} → 필요 |z| > 3.1")
print(f"  판정: {'통과' if abs(z) > 3.1 else '★ 미통과 — 다중검정 잡음과 구별되지 않는다'}")
print(f"  참고: {n_cells}개 셀을 α=0.05로 검정하면 우연히 "
      f"{n_cells * 0.05:.1f}개가 유의하게 나온다. 관측된 ★ 개수와 같은 수준이다.")

print("\n기간 분할 (전반 3.5일 vs 후반 3.5일) — 효과가 재현되는가")
allts = sorted(t for pts in SERIES.values() for t, _, _ in pts)
mid = allts[len(allts) // 2]
print(f"  분할 기준: {mid}")
for half, keep in (("전반", lambda t: t < mid), ("후반", lambda t: t >= mid)):
    sub = [e for e in ev if keep(e[1])]
    if len(sub) < 15:
        print(f"  {half}: 이벤트 {len(sub)}건 — 표본 부족")
        continue
    rr = simulate(sub, 0.20, 0.20, None)
    if not rr:
        continue
    zz = z_vs_breakeven(rr["wr"], rr["n"], rr["be"])
    print(f"  {half}: n={rr['n']:>3}  승률 {100 * rr['wr']:>5.1f}%  "
          f"순수익 {100 * rr['net']:>+6.2f}%  z={zz:>+5.2f}")
