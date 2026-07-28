#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""시장 캘리브레이션 측정 — 가격이 실제 해결률과 일치하는가.

여러 전략(mango=settlement discount, fig=favorite-longshot bias, cherry=resolution
momentum)이 공통으로 "시장이 특정 확률 구간을 잘못 매긴다"에 베팅한다. 그 전제는
`market_snapshots` + 공개 Gamma 해결 결과만으로 직접 측정할 수 있다.

거래 표본이 아니라 **관측된 모든 시장**을 쓰므로 검정력이 훨씬 높다.
전략이 실제로 진입했는지와 무관하다.

사용:
    uv run --script tools/market_calibration.py <trades.db> [샘플할 시장 수]
"""

import json
import random
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

DB = sys.argv[1] if len(sys.argv) > 1 else sys.exit("trades.db 경로 필요")
MAX_MARKETS = int(sys.argv[2]) if len(sys.argv) > 2 else 2500
UA = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}
# 해결까지 남은 시간이 이보다 짧은 관측은 제외한다. 해결 직전 가격은
# 이미 결과를 반영하므로 캘리브레이션 측정을 왜곡한다.
MIN_HOURS_TO_RESOLUTION = 24.0


def parse_ts(s):
    s = str(s).replace("T", " ").replace("Z", "").split("+")[0].strip()
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


conn = sqlite3.connect(DB)
# 시장별 관측이 충분한 것만. 각 시장에서 하루 1개씩만 뽑아 중복 상관을 줄인다.
rows = conn.execute("""
    SELECT condition_id, probability, timestamp FROM market_snapshots
    WHERE condition_id IN (
        SELECT condition_id FROM market_snapshots
        GROUP BY condition_id HAVING COUNT(*) >= 12
    )
    ORDER BY condition_id, timestamp
""").fetchall()
print(f"snapshot {len(rows):,}행")

per_market = defaultdict(list)
for cid, prob, ts in rows:
    t = parse_ts(ts)
    if t and prob is not None:
        per_market[cid].append((t, float(prob)))

# 시장당 하루 1개 관측만 샘플링
random.seed(11)
obs = []
for cid, pts in per_market.items():
    byday = {}
    for t, p in pts:
        byday.setdefault(t.date(), (t, p))
    for _, (t, p) in byday.items():
        obs.append((cid, t, p))
cids = sorted(per_market)
if len(cids) > MAX_MARKETS:
    keep = set(random.sample(cids, MAX_MARKETS))
    obs = [o for o in obs if o[0] in keep]
    cids = sorted(keep)
print(f"시장 {len(cids):,}개 / 관측 {len(obs):,}개 (시장·일자당 1개)")


def fetch(chunk, closed):
    qs = "&".join(f"condition_ids={urllib.parse.quote(c)}" for c in chunk)
    url = f"https://gamma-api.polymarket.com/markets?limit=100&closed={closed}&{qs}"
    for a in range(4):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            if a == 3:
                print(f"  실패: {e}", file=sys.stderr)
                return []
            time.sleep(2 * (a + 1))
    return []


markets = {}
CH = 20
for i in range(0, len(cids), CH):
    ch = cids[i:i + CH]
    data = fetch(ch, "true")
    found = {m.get("conditionId") for m in data}
    miss = [c for c in ch if c not in found]
    if miss:
        data += fetch(miss, "false")
    for m in data:
        markets[m.get("conditionId")] = m
    if (i // CH) % 25 == 0:
        print(f"  Gamma {i + len(ch)}/{len(cids)}", file=sys.stderr)
    time.sleep(0.12)
print(f"Gamma 수집 {len(markets):,}개")

resolved = {}
for cid, m in markets.items():
    if not m.get("closed"):
        continue
    try:
        pr = m["outcomePrices"]
        if isinstance(pr, str):
            pr = json.loads(pr)
        resolved[cid] = float(pr[0])          # YES(index 0) 최종값
    except Exception:  # noqa: BLE001
        pass
    end = m.get("endDate")
    if end:
        m["_end"] = parse_ts(end)
print(f"해결 확정 {len(resolved):,}개")

samples = []
for cid, t, p in obs:
    if cid not in resolved:
        continue
    end = markets[cid].get("_end")
    if end and (end - t) < timedelta(hours=MIN_HOURS_TO_RESOLUTION):
        continue
    samples.append((p, resolved[cid]))
print(f"유효 관측 {len(samples):,}개 "
      f"(해결 {MIN_HOURS_TO_RESOLUTION:.0f}h 이내 관측 제외)\n")

EDGES = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50,
         0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.01]
g = defaultdict(list)
for p, r in samples:
    for i in range(len(EDGES) - 1):
        if EDGES[i] <= p < EDGES[i + 1]:
            g[(EDGES[i], EDGES[i + 1])].append((p, r))
            break

import math
print("=== 캘리브레이션: YES 가격 구간별 실제 YES 해결률 ===")
print("edge > 0 이면 시장이 과소평가(사는 쪽 유리), < 0 이면 과대평가(파는 쪽 유리)")
hdr = f"{'가격 구간':<14}{'n':>7}{'평균가격':>10}{'실현률':>9}{'edge(pp)':>10}{'z':>8}"
print(hdr)
print("-" * len(hdr))
for k in sorted(g):
    rs = g[k]
    if len(rs) < 30:
        continue
    n = len(rs)
    pbar = sum(p for p, _ in rs) / n
    obs_r = sum(1 for _, r in rs if r >= 0.5) / n
    se = math.sqrt(pbar * (1 - pbar) / n)
    z = (obs_r - pbar) / se if se > 0 else 0
    star = " ★" if abs(z) > 1.96 else ""
    print(f"{k[0]:.2f}-{k[1]:.2f}     {n:>7,}{pbar:>10.3f}{100*obs_r:>8.1f}%"
          f"{100*(obs_r-pbar):>+10.1f}{z:>8.2f}{star}")
print("-" * len(hdr))
n = len(samples)
if n:
    pbar = sum(p for p, _ in samples) / n
    obs_r = sum(1 for _, r in samples if r >= 0.5) / n
    se = math.sqrt(pbar * (1 - pbar) / n)
    print(f"{'전체':<14}{n:>7,}{pbar:>10.3f}{100*obs_r:>8.1f}%"
          f"{100*(obs_r-pbar):>+10.1f}{(obs_r-pbar)/se if se else 0:>8.2f}")
print("\n★ = |z| > 1.96 (5% 유의). 다만 구간을 여럿 검정하므로 다중검정을 고려할 것.")
