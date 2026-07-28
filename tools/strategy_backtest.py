#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""전략 백테스트 엔진 — 실제 가격 경로 위에서 진입/청산 규칙을 재생한다.

## 왜 이 엔진이 필요한가

`docs/new-strategy-playbook.md` §2는 새 전략에 **재실행 가능한 독립 backtest artifact**를
요구한다. 그리고 2026-07-28~29에 폐쇄한 4개 전략은 전부 이 단계를 건너뛰고 실거래로
직행했다 (`docs/retro/closed-strategies-postmortem.md`).

이 엔진은 전략과 무관하다. 진입 규칙과 청산 규칙을 함수로 받아서, 폐쇄 전략들이 남긴
**snapshot 450만 행** 위에서 재생한다.

## 데이터

`market_snapshots(condition_id, probability, liquidity, volume_24h, timestamp)`.
`probability`는 **항상 YES 가격**(midpoint)이다. bid/ask가 없으므로 체결가는 모델로
가정해야 한다 — 그래서 `ExecutionModel`을 분리했다.

해결 결과는 Gamma에서 가져와 JSON으로 캐시한다. 같은 캐시를 여러 실험이 공유한다.

## 설계 원칙 (플레이북 §2)

- **look-ahead 금지**: 시점 t의 판단은 t 이하의 관측만 본다. `features_at()`이 이를 강제한다.
- **survivorship 측정**: 해결 결과를 못 찾은 시장의 비중을 항상 보고한다.
- **midpoint 100% 체결은 baseline이 아니다**: 기본 실행 모델은 보수적이고,
  낙관적 모델은 민감도 분석으로만 쓴다.
- **표본 분할**: 시장 단위로 나눈다. 같은 시장의 관측이 train과 test에 섞이지 않는다.

## 사용

    # 1) 해결 결과 캐시 구축 (최초 1회, 느림)
    uv run --script tools/strategy_backtest.py cache --out /tmp/res.json

    # 2) 캘리브레이션 (가격 구간별 실현률) — 어떤 전략이든 첫 검정
    uv run --script tools/strategy_backtest.py calib --cache /tmp/res.json

    # 3) 규칙 재생
    uv run --script tools/strategy_backtest.py replay --cache /tmp/res.json \\
        --entry-price-min 0.90 --entry-price-max 0.94 \\
        --take-profit 0.08 --stop-loss -0.08 --hours-min 6 --hours-max 168
"""

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "curl/8.7.1", "Accept": "*/*"}

# 폐쇄 전략들이 남긴 snapshot 자산. 각 봇이 자기 필터를 통과한 시장만 기록했으므로
# 합집합에는 선택 편향이 있다 — 보고에 항상 명시한다.
SNAPSHOT_DBS = [
    ROOT / "golden-fig/data/default/trades.db",
    ROOT / "golden-date/data/default/trades.db",
    ROOT / "golden-mango/data/default/trades.db",
    ROOT / "golden-lime/data/default/trades.db",
]


# ---------------------------------------------------------------------------
# 데이터 적재
# ---------------------------------------------------------------------------

def parse_ts(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_series(dbs=None, minute_bucket=True):
    """시장별 (시각, 가격, 유동성, 거래량) 시계열. 시각 오름차순.

    여러 봇이 같은 시장을 같은 시각에 기록하므로 분 단위로 중복을 제거한다.
    """
    dbs = dbs or SNAPSHOT_DBS
    raw = defaultdict(dict)
    for db in dbs:
        if not Path(db).exists():
            print(f"  건너뜀 (없음): {db}", file=sys.stderr)
            continue
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        n = 0
        for cid, prob, liq, vol, ts in conn.execute(
            "SELECT condition_id, probability, liquidity, volume_24h, timestamp "
            "FROM market_snapshots"
        ):
            t = parse_ts(ts)
            if t is None or prob is None:
                continue
            key = t.replace(second=0, microsecond=0) if minute_bucket else t
            raw[cid][key] = (float(prob), liq, vol)
            n += 1
        conn.close()
        print(f"  {Path(db).parent.parent.parent.name}: {n:,}행", file=sys.stderr)

    series = {}
    for cid, obs in raw.items():
        pts = sorted(obs.items())
        series[cid] = {
            "t": [p[0] for p in pts],
            "p": [p[1][0] for p in pts],
            "liq": [p[1][1] for p in pts],
            "vol": [p[1][2] for p in pts],
        }
    return series


def fetch_resolutions(cids, cache_path, chunk=20):
    """Gamma에서 해결 결과를 가져와 캐시한다. 이미 있는 것은 다시 받지 않는다."""
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = [c for c in cids if c not in cache]
    print(f"캐시 {len(cache):,} / 신규 조회 {len(missing):,}", file=sys.stderr)

    def get(ids, closed):
        qs = "&".join(f"condition_ids={urllib.parse.quote(c)}" for c in ids)
        url = (f"https://gamma-api.polymarket.com/markets"
               f"?limit=100&closed={closed}&{qs}")
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read())
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    print(f"    실패: {e}", file=sys.stderr)
                    return []
                time.sleep(2 * (attempt + 1))
        return []

    for i in range(0, len(missing), chunk):
        ch = missing[i:i + chunk]
        data = get(ch, "true")
        found = {m.get("conditionId") for m in data}
        for c in ch:
            if c not in found:
                cache[c] = None          # 미해결로 기록해 재조회를 막는다
        for m in data:
            cid = m.get("conditionId")
            cache[cid] = {
                "closed": bool(m.get("closed")),
                "outcomes": m.get("outcomes"),
                "outcomePrices": m.get("outcomePrices"),
                "endDate": m.get("endDate"),
                "question": m.get("question"),
                "tags": [t.get("label") for t in (m.get("tags") or [])
                         if isinstance(t, dict)],
            }
        if (i // chunk) % 25 == 0:
            print(f"  {i + len(ch):,}/{len(missing):,}", file=sys.stderr)
            cache_path.write_text(json.dumps(cache))
        time.sleep(0.15)

    cache_path.write_text(json.dumps(cache))
    return cache


def yes_outcome(entry):
    """최종 YES 값 (1.0 / 0.0). 미해결이면 None."""
    if not entry or not entry.get("closed"):
        return None
    try:
        prices, names = entry["outcomePrices"], entry["outcomes"]
        if isinstance(prices, str):
            prices = json.loads(prices)
        if isinstance(names, str):
            names = json.loads(names)
        m = {str(n).strip().lower(): float(p) for n, p in zip(names, prices)}
        return m.get("yes")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 특징량 — look-ahead 금지를 여기서 강제한다
# ---------------------------------------------------------------------------

def features_at(s, i, resolve_at):
    """시각 인덱스 i에서 관측 가능한 특징량만 반환한다.

    i 이후의 어떤 값도 읽지 않는다. 이 함수가 look-ahead 방어선이다.
    """
    t, p = s["t"][i], s["p"][i]

    def change_over(hours):
        """t-hours 시점 대비 가격 변화. 관측이 그 근처에 없으면 None.

        단순히 '목표 이하의 마지막 관측'을 쓰면 시계열이 끊긴 시장에서 모든
        horizon이 같은 값으로 포화되어 가짜 신호를 만든다 (lime 백테스트에서
        실제로 겪은 버그). 허용 오차를 두고, 벗어나면 None을 준다.
        """
        target = t - timedelta(hours=hours)
        j = bisect_right(s["t"], target, 0, i) - 1
        if j < 0:
            return None
        tol = timedelta(minutes=max(45.0, hours * 60 * 0.4))
        if abs(s["t"][j] - target) > tol:
            return None
        return p - s["p"][j]

    def vol_over(hours):
        """최근 hours 구간의 실현 변동성 (관측 간 변화의 표준편차)."""
        cut = t - timedelta(hours=hours)
        j = bisect_right(s["t"], cut, 0, i)
        seg = s["p"][j:i + 1]
        if len(seg) < 3:
            return None
        d = [seg[k + 1] - seg[k] for k in range(len(seg) - 1)]
        mu = sum(d) / len(d)
        return math.sqrt(sum((x - mu) ** 2 for x in d) / len(d))

    hours_left = None
    if resolve_at is not None:
        hours_left = (resolve_at - t).total_seconds() / 3600.0

    return {
        "t": t, "p": p, "i": i,
        "liquidity": s["liq"][i], "volume_24h": s["vol"][i],
        "chg_1h": change_over(1), "chg_6h": change_over(6), "chg_24h": change_over(24),
        "vol_6h": vol_over(6), "vol_24h_rlz": vol_over(24),
        "hours_left": hours_left,
        "n_prior": i + 1,
    }


# ---------------------------------------------------------------------------
# 실행 모델 — midpoint 100% 체결은 baseline이 아니다 (플레이북 §2)
# ---------------------------------------------------------------------------

class ExecutionModel:
    """체결 가격과 체결 여부를 모델링한다.

    snapshot에 bid/ask가 없으므로 스프레드를 가정할 수밖에 없다. 그래서 가정을
    한곳에 모으고, 낙관/보수를 명시적으로 나눈다.

    half_spread : 매수는 midpoint + half_spread, 매도는 midpoint - half_spread로 체결.
                  Polymarket 틱이 0.01이므로 보수 기본값 0.005 = 반 틱.
    fee_bps     : 왕복이 아니라 **편도** 수수료 (bp). 실측으로 확정할 것.
    fill_prob   : 지정가가 체결될 확률. 1.0은 낙관적 민감도 분석용.
    """

    def __init__(self, half_spread=0.005, fee_bps=0.0, fill_prob=1.0):
        self.half_spread = half_spread
        self.fee_bps = fee_bps
        self.fill_prob = fill_prob

    def buy_price(self, mid):
        return min(0.999, mid + self.half_spread) * (1 + self.fee_bps / 10000.0)

    def sell_price(self, mid):
        return max(0.001, mid - self.half_spread) * (1 - self.fee_bps / 10000.0)

    def describe(self):
        return (f"half_spread={self.half_spread} fee_bps={self.fee_bps} "
                f"fill_prob={self.fill_prob}")


# ---------------------------------------------------------------------------
# 재생
# ---------------------------------------------------------------------------

def replay_market(s, resolve_at, final_yes, entry_fn, exit_cfg, exec_model,
                  max_entries=1, cooldown_hours=24.0):
    """한 시장에서 진입/청산을 재생해 trade 리스트를 만든다."""
    trades = []
    n = len(s["t"])
    i = 0
    last_exit_t = None
    while i < n and len(trades) < max_entries:
        f = features_at(s, i, resolve_at)
        if last_exit_t is not None:
            if (f["t"] - last_exit_t).total_seconds() / 3600.0 < cooldown_hours:
                i += 1
                continue
        sig = entry_fn(f)
        if not sig:
            i += 1
            continue

        entry_price = exec_model.buy_price(f["p"])
        if entry_price <= 0 or entry_price >= 1.0:
            i += 1
            continue

        tr = {
            "entry_t": f["t"], "entry_mid": f["p"], "entry_price": entry_price,
            "hours_left_at_entry": f["hours_left"],
            "chg_6h_at_entry": f["chg_6h"], "liq_at_entry": f["liquidity"],
        }

        max_seen = f["p"]
        exit_i = None
        for j in range(i + 1, n):
            mid = s["p"][j]
            max_seen = max(max_seen, mid)
            pnl_pct = (mid - entry_price) / entry_price
            reason = None
            if exit_cfg.get("stop_loss") is not None and pnl_pct <= exit_cfg["stop_loss"]:
                reason = "stop_loss"
            elif (exit_cfg.get("take_profit") is not None
                  and mid >= min(entry_price * (1 + exit_cfg["take_profit"]), 0.99)):
                reason = "take_profit"
            elif (exit_cfg.get("trailing") is not None
                  and mid < max_seen * (1 - exit_cfg["trailing"])):
                reason = "trailing_stop"
            elif (exit_cfg.get("exit_hours") is not None and resolve_at is not None
                  and (resolve_at - s["t"][j]).total_seconds() / 3600.0
                  < exit_cfg["exit_hours"]):
                reason = "time_exit"
            if reason:
                tr["exit_t"] = s["t"][j]
                tr["exit_price"] = exec_model.sell_price(mid)
                tr["exit_reason"] = reason
                exit_i = j
                break

        if exit_i is None:
            # 청산 없이 해결까지 보유 — 최종 가치는 해결 결과 그 자체
            tr["exit_t"] = resolve_at or s["t"][-1]
            tr["exit_price"] = final_yes
            tr["exit_reason"] = "resolution"
            exit_i = n - 1

        tr["ret"] = (tr["exit_price"] - tr["entry_price"]) / tr["entry_price"]
        tr["hold_hours"] = (tr["exit_t"] - tr["entry_t"]).total_seconds() / 3600.0
        tr["final_yes"] = final_yes
        trades.append(tr)
        last_exit_t = tr["exit_t"]
        i = exit_i + 1
    return trades


def summarize(trades, label=""):
    if not trades:
        return None
    n = len(trades)
    rets = [t["ret"] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / n) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else 0.0
    hold = sum(t["hold_hours"] for t in trades) / n
    return {
        "label": label, "n": n, "win_rate": wins / n, "mean_ret": mean,
        "t_stat": mean / se if se > 0 else 0.0, "sd": sd,
        "avg_hold_h": hold,
        "total_ret": sum(rets),
    }


def print_summary(rows):
    hdr = (f"{'구분':<22}{'n':>6}{'승률':>8}{'평균수익':>10}"
           f"{'t':>8}{'평균보유h':>10}{'합계수익':>10}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if not r:
            continue
        print(f"{r['label']:<22}{r['n']:>6}{100*r['win_rate']:>7.1f}%"
              f"{100*r['mean_ret']:>+9.2f}%{r['t_stat']:>+8.2f}"
              f"{r['avg_hold_h']:>10.1f}{100*r['total_ret']:>+9.1f}%")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_cache(args):
    print("snapshot 적재 중...", file=sys.stderr)
    series = load_series()
    print(f"시장 {len(series):,}개", file=sys.stderr)
    fetch_resolutions(sorted(series), args.out)
    print(f"캐시 저장: {args.out}")
    return 0


def build_resolve_map(cache):
    """condition_id → (해결 시각, 최종 YES). 해결 안 된 것은 제외."""
    out = {}
    for cid, entry in cache.items():
        y = yes_outcome(entry)
        if y is None:
            continue
        t = parse_ts(entry.get("endDate")) if entry.get("endDate") else None
        out[cid] = (t, y)
    return out


def cmd_calib(args):
    series = load_series()
    cache = json.loads(Path(args.cache).read_text())
    res = build_resolve_map(cache)
    print(f"\n시장 {len(series):,}개 중 해결 확정 {len(res):,}개 "
          f"({100*len(res)/max(1,len(series)):.1f}%)")
    print("※ 미해결 시장은 제외됨 — survivorship 편향을 보고에 명시할 것\n")

    # 시장당 1관측으로 줄여 독립성을 확보한다 (같은 시장의 관측은 독립이 아니다).
    buckets = defaultdict(list)
    edges = [0.0, .05, .10, .20, .30, .40, .50, .60, .70, .75, .80,
             .85, .90, .93, .95, .97, .99, 1.01]

    def bname(p):
        for k in range(len(edges) - 1):
            if edges[k] <= p < edges[k + 1]:
                return f"{edges[k]:.2f}-{edges[k+1]:.2f}"
        return "?"

    for cid, s in series.items():
        if cid not in res:
            continue
        _, y = res[cid]
        mid = len(s["p"]) // 2          # 시장당 중앙 관측 1개만
        buckets[bname(s["p"][mid])].append((s["p"][mid], y))

    hdr = f"{'가격 구간':<14}{'n':>7}{'평균가격':>10}{'실현 YES율':>12}{'edge(pp)':>10}{'z':>8}"
    print(hdr)
    print("-" * len(hdr))
    for k in sorted(buckets):
        rows = buckets[k]
        n = len(rows)
        if n < 15:
            continue
        pbar = sum(r[0] for r in rows) / n
        obs = sum(1 for r in rows if r[1] >= 0.5) / n
        se = math.sqrt(pbar * (1 - pbar) / n)
        z = (obs - pbar) / se if se > 0 else 0.0
        print(f"{k:<14}{n:>7}{pbar:>10.3f}{100*obs:>11.1f}%"
              f"{100*(obs-pbar):>+10.1f}{z:>+8.2f}")
    print("\n|z| > 1.96 이면 명목상 5% 유의. 구간을 여럿 검정하므로 "
          "Bonferroni 보정 후 판단할 것.")
    return 0


def cmd_replay(args):
    series = load_series()
    cache = json.loads(Path(args.cache).read_text())
    res = build_resolve_map(cache)

    exec_model = ExecutionModel(half_spread=args.half_spread,
                                fee_bps=args.fee_bps)
    exit_cfg = {
        "take_profit": args.take_profit, "stop_loss": args.stop_loss,
        "trailing": args.trailing, "exit_hours": args.exit_hours,
    }

    def entry_fn(f):
        if not (args.entry_price_min <= f["p"] <= args.entry_price_max):
            return False
        if f["hours_left"] is None:
            return False
        if not (args.hours_min <= f["hours_left"] <= args.hours_max):
            return False
        if args.min_liquidity and (f["liquidity"] or 0) < args.min_liquidity:
            return False
        if args.momentum_min is not None:
            if f["chg_6h"] is None or f["chg_6h"] < args.momentum_min:
                return False
        return True

    # 시장 단위 분할 — 같은 시장이 train/test에 섞이지 않게 한다.
    cids = sorted(c for c in series if c in res)
    half = len(cids) // 2
    split = {c: ("A" if k < half else "B") for k, c in enumerate(cids)}

    all_trades, by_split = [], defaultdict(list)
    for cid in cids:
        t_res, y = res[cid]
        tr = replay_market(series[cid], t_res, y, entry_fn, exit_cfg, exec_model,
                           max_entries=args.max_entries,
                           cooldown_hours=args.cooldown_hours)
        for x in tr:
            x["condition_id"] = cid
        all_trades.extend(tr)
        by_split[split[cid]].extend(tr)

    print(f"\n실행 모델: {exec_model.describe()}")
    print(f"진입: 가격 [{args.entry_price_min}, {args.entry_price_max}], "
          f"잔여 [{args.hours_min}h, {args.hours_max}h]")
    print(f"청산: TP={args.take_profit} SL={args.stop_loss} "
          f"trail={args.trailing} exit_h={args.exit_hours}\n")

    rows = [summarize(all_trades, "전체"),
            summarize(by_split["A"], "표본 A (분할)"),
            summarize(by_split["B"], "표본 B (분할)")]
    print_summary([r for r in rows if r])

    by_reason = defaultdict(list)
    for t in all_trades:
        by_reason[t["exit_reason"]].append(t)
    if by_reason:
        print("\n청산 사유별")
        print_summary([summarize(v, k) for k, v in
                       sorted(by_reason.items(), key=lambda kv: -len(kv[1]))])

    if args.out:
        Path(args.out).write_text(json.dumps(
            [{k: (v.isoformat() if isinstance(v, datetime) else v)
              for k, v in t.items()} for t in all_trades], indent=1))
        print(f"\ntrade 단위 결과 저장: {args.out}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cache", help="Gamma 해결 결과 캐시 구축")
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_cache)

    c = sub.add_parser("calib", help="가격 구간별 실현 해결률")
    c.add_argument("--cache", required=True)
    c.set_defaults(func=cmd_calib)

    c = sub.add_parser("replay", help="진입/청산 규칙 재생")
    c.add_argument("--cache", required=True)
    c.add_argument("--entry-price-min", type=float, default=0.90)
    c.add_argument("--entry-price-max", type=float, default=0.94)
    c.add_argument("--hours-min", type=float, default=6)
    c.add_argument("--hours-max", type=float, default=168)
    c.add_argument("--min-liquidity", type=float, default=0)
    c.add_argument("--momentum-min", type=float, default=None)
    c.add_argument("--take-profit", type=float, default=None)
    c.add_argument("--stop-loss", type=float, default=None)
    c.add_argument("--trailing", type=float, default=None)
    c.add_argument("--exit-hours", type=float, default=None)
    c.add_argument("--half-spread", type=float, default=0.005)
    c.add_argument("--fee-bps", type=float, default=0.0)
    c.add_argument("--max-entries", type=int, default=1)
    c.add_argument("--cooldown-hours", type=float, default=24.0)
    c.add_argument("--out")
    c.set_defaults(func=cmd_replay)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
