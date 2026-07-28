#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""golden-lime 가설 직접 검정 — market_snapshots로 하는 사후 백테스트.

lime의 STRATEGY.md에는 백테스트 artifact가 없다(문서 감사로 확인). 그런데 봇이
매 사이클 universe 전체 snapshot을 적재하므로 그 데이터로 가설 자체를 검정할 수 있다:

  "6h 내 +0.10 점프 + 거래량 폭증 + 고점 유지" 뒤에
  가격이 계속 오르는가(모멘텀), 되돌아오는가(평균회귀)?

거래 표본(약 31건)이 아니라 **관측된 모든 점프 이벤트**로 판정하므로
검정력이 훨씬 높다. 실제 매수 여부와 무관하다.

사용:
    python3 lime_backtest.py [trades.db 경로]
"""

import sqlite3
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta

DB = sys.argv[1] if len(sys.argv) > 1 else \
    "/Users/izowooi/git/t1/golden-lime/data/default/trades.db"

JUMP_WINDOW_H = 6.0
HOLD_WINDOW_MIN = 60
VOL_AVG_WINDOW_H = 24.0
FORWARD_HORIZONS_H = [1, 3, 6, 12, 24, 48]
MIN_PTS = 12


def parse_ts(s):
    if isinstance(s, (int, float)):
        return datetime.utcfromtimestamp(s)
    s = str(s).replace("T", " ").replace("Z", "").split("+")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def load():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT condition_id, probability, volume_24h, timestamp "
        "FROM market_snapshots ORDER BY condition_id, timestamp"
    ).fetchall()
    print(f"snapshot {len(rows):,}행")
    if not rows:
        print("market_snapshots가 비어 있다. 백테스트 불가.")
        raise SystemExit(1)
    s = defaultdict(list)
    for r in rows:
        ts = parse_ts(r["timestamp"])
        if ts is None or r["probability"] is None:
            continue
        s[r["condition_id"]].append(
            (ts, float(r["probability"]), float(r["volume_24h"] or 0)))
    for v in s.values():
        v.sort(key=lambda x: x[0])
    return s


SERIES = load()
spans = [(v[0][0], v[-1][0]) for v in SERIES.values() if len(v) > 1]
if spans:
    lo, hi = min(s[0] for s in spans), max(s[1] for s in spans)
    print(f"시장 {len(SERIES):,}개 | 기간 {lo} ~ {hi} ({(hi - lo).days}일)")
counts = sorted(len(v) for v in SERIES.values())
if counts:
    print(f"시장당 snapshot 중앙값 {counts[len(counts) // 2]}개 "
          f"(최소 {counts[0]}, 최대 {counts[-1]})")


def detect(jump_min, vol_mult_min, max_pullback,
           base_min=0.15, base_max=0.70, current_max=0.85):
    """O(n) 슬라이딩 윈도우. 단조 deque로 6h 최저·60m 최고를 유지한다."""
    events = []
    for cid, pts in SERIES.items():
        n = len(pts)
        if n < MIN_PTS:
            continue
        jmin = deque()          # (idx) 6h 윈도우 최저 후보, 값 증가 순
        hmax = deque()          # (idx) 60m 윈도우 최고 후보, 값 감소 순
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
            base = pts[jmin[0]][1]
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
    return events


def forward(cid, idx, hours):
    pts = SERIES[cid]
    target = pts[idx][0] + timedelta(hours=hours)
    best = None
    for t, p, _ in pts[idx + 1:]:
        if t <= target:
            best = p
        else:
            break
    return best


def dedupe(events):
    seen, uniq = {}, []
    for cid, ts, p, i in sorted(events, key=lambda e: (e[0], e[1])):
        if cid in seen and (ts - seen[cid]) < timedelta(hours=24):
            continue
        seen[cid] = ts
        uniq.append((cid, ts, p, i))
    return uniq


def report(events, label):
    uniq = dedupe(events)
    print(f"\n=== {label} ===")
    print(f"  이벤트 {len(events)}건 → 24h 중복제거 {len(uniq)}건 "
          f"(시장 {len({e[0] for e in uniq})}개)")
    if len(uniq) < 5:
        print("  표본 부족")
        return
    print(f"  {'경과':>6}{'n':>6}{'평균':>10}{'중앙값':>10}{'상승비율':>10}{'t통계':>9}")
    print("  " + "-" * 51)
    for h in FORWARD_HORIZONS_H:
        rs = []
        for cid, ts, p0, i in uniq:
            p1 = forward(cid, i, h)
            if p1 is not None and p0 > 0:
                rs.append((p1 - p0) / p0)
        if len(rs) < 5:
            continue
        m = sum(rs) / len(rs)
        var = sum((r - m) ** 2 for r in rs) / (len(rs) - 1)
        se = (var / len(rs)) ** 0.5
        t = m / se if se > 0 else 0
        srt = sorted(rs)
        med = srt[len(srt) // 2]
        up = sum(1 for r in rs if r > 0) / len(rs)
        print(f"  {h:>5}h{len(rs):>6}{100 * m:>9.2f}%{100 * med:>9.2f}%"
              f"{100 * up:>9.1f}%{t:>9.2f}")


report(detect(0.10, 2.0, 0.02), "운영 설정 (jump 0.10 / vol 2.0x / pullback 2%)")

print("\n\n########## 스윕: 어떤 조합에서든 양의 표류가 존재하는가 ##########")
for jm in [0.08, 0.10, 0.15, 0.20]:
    for vm in [0.0, 2.0, 3.0]:
        ev = detect(jm, vm, 0.02)
        if len(dedupe(ev)) >= 10:
            report(ev, f"jump>={jm:.2f} / vol>={vm:.1f}x / pullback<=2%")

print("\n\n########## 되돌림 허용(천장매수 완화) ##########")
for mp in [0.05, 0.10]:
    ev = detect(0.10, 2.0, mp)
    if len(dedupe(ev)) >= 10:
        report(ev, f"jump>=0.10 / vol>=2.0x / pullback<={mp:.2f}")

print("\n\n########## 반대 가설(elderberry): 하락 후 반등하는가 ##########")


def detect_drop(drop_min=0.10, base_min=0.30, base_max=0.95):
    """6h 윈도우 최고가 대비 drop_min 이상 하락한 시점."""
    events = []
    for cid, pts in SERIES.items():
        n = len(pts)
        if n < MIN_PTS:
            continue
        hmax = deque()
        lo = 0
        for i in range(n):
            ts, p, _ = pts[i]
            while hmax and pts[hmax[-1]][1] <= p:
                hmax.pop()
            hmax.append(i)
            w0 = ts - timedelta(hours=JUMP_WINDOW_H)
            while pts[lo][0] < w0:
                lo += 1
            while hmax and hmax[0] < lo:
                hmax.popleft()
            if i - lo < 5:
                continue
            ref = pts[hmax[0]][1]
            if not (base_min <= ref <= base_max):
                continue
            if ref - p < drop_min:
                continue
            events.append((cid, ts, p, i))
    return events


report(detect_drop(), "6h 내 -0.10 이상 급락 (elderberry 방향)")
