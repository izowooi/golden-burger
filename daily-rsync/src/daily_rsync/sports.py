"""Local-only sports projections and displayed-depth what-if calculations.

Imports are explicit user actions. Source SQLite files are never changed; a
complete, checksum-verified projection is atomically published as a new version.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import threading
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .config import AppConfig
from .sports_normalization import normalize_payload
from .sports_recorder import RUNTIMES as RECORDER_RUNTIMES
from .sports_recorder import export_group

SUPPORTED = {"golden-peach", "golden-plum", "golden-coconut"}
SPORTS = [
    ("soccer", "축구"),
    ("mlb", "야구 · MLB"),
    ("nba", "농구 · NBA"),
    ("nfl", "미식축구 · NFL"),
    ("nhl", "아이스하키 · NHL"),
    ("ufc", "UFC"),
    ("boxing", "복싱"),
]


def collector_role(strategy: str, jenkins_job: str, runtime_job: str) -> str:
    if strategy == "golden-coconut" and runtime_job in RECORDER_RUNTIMES:
        return "PRIMARY" if jenkins_job == "polybot-white" else "REPLICA"
    if jenkins_job == "polybot-grey":
        return "RETIRED"
    if jenkins_job == "polybot-silver" and strategy == "golden-plum":
        return "HISTORICAL"
    return "LEGACY"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("시간에는 UTC offset이 필요합니다.")
    return result.astimezone(UTC)


class SportsStore:
    def __init__(self, config: AppConfig, catalog: Any):
        self.config, self.catalog = config, catalog
        self.root = config.data_root / "sports-view"
        self._normalizations = OrderedDict()
        self._normalization_lock = threading.Lock()

    def sources(self) -> list[dict]:
        result = []
        with self.catalog.connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT * FROM artifacts WHERE source=? AND kind LIKE 'database%' "
                    "AND strategy IN ('golden-peach','golden-plum','golden-coconut') "
                    "ORDER BY jenkins_job,runtime_job,remote_path",
                    (self.config.ssh_host,),
                )
            )
        for raw in rows:
            row = dict(raw)
            if not str(row["kind"]).startswith("database") or row["strategy"] not in SUPPORTED:
                continue
            # Sidecars and backup files have other contracts; do not guess them.
            basename = Path(row["remote_path"]).name
            if row["strategy"] == "golden-coconut":
                if row["runtime_job"] not in RECORDER_RUNTIMES or not re.fullmatch(
                    r"trades_sim(?:_\d{8})?\.db", basename
                ):
                    continue
            elif basename not in {"trades_sim.db", "trades.db"}:
                continue
            path = Path(row["local_path"] or "")
            available = (
                row["status"] in {"SYNCED", "SOURCE_MISSING"}
                and bool(row["local_sha256"])
                and path.is_file()
                and path.resolve().is_relative_to(self.config.data_root.resolve())
            )
            result.append(
                {
                    k: row.get(k)
                    for k in (
                        "source_key",
                        "jenkins_job",
                        "strategy",
                        "runtime_job",
                        "size",
                        "synced_at",
                        "status",
                    )
                }
                | {
                    "available": available,
                    "basename": basename,
                    "collector_role": collector_role(
                        row["strategy"], row["jenkins_job"], row["runtime_job"]
                    ),
                }
            )
        return result

    def _version(self) -> Path | None:
        pointer = self.root / "current.json"
        if not pointer.exists():
            return None
        version = read_json(pointer)["version"]
        if not re.fullmatch(r"[a-f0-9]{32}", version):
            raise ValueError("잘못된 경기 인덱스입니다.")
        return self.root / version

    def index(self) -> dict:
        version = self._version()
        if version is None:
            return {
                "matches": [],
                "sources": [],
                "cohorts": [],
                "sports": SPORTS,
                "generated_at": None,
            }
        return read_json(version / "index.json")

    def match(self, match_id: str, *, depth: bool = False) -> dict:
        if not re.fullmatch(r"[a-f0-9]{20}", match_id):
            raise ValueError("잘못된 경기 ID입니다.")
        version = self._version()
        path = version / "events" / f"{match_id}.json" if version else None
        if path is None or not path.is_file():
            raise ValueError("경기를 먼저 로컬 인덱스에 추가하세요.")
        result = read_json(path)
        result["view_version"] = version.name
        if not depth:
            module_path = self.config.project_root.parent / "tools/sports_price_normalization.py"
            if module_path.is_file():
                module_source = module_path.read_bytes()
                key = (version.name, match_id, hashlib.sha256(module_source).hexdigest())
                with self._normalization_lock:
                    if key not in self._normalizations:
                        source = next(
                            (
                                s
                                for s in read_json(version / "index.json")["sources"]
                                if s["id"] == result["match"]["source_id"]
                            ),
                            {},
                        )
                        self._normalizations[key] = normalize_payload(
                            result, source, module_path, module_source=module_source
                        )
                        if len(self._normalizations) > 16:
                            self._normalizations.popitem(last=False)
                    self._normalizations.move_to_end(key)
                    result["normalization"] = self._normalizations[key]
            result.pop("depth", None)
        return result

    def build(self, keys: list[str], start: str, end: str, callback: Any) -> dict:
        first, last = utc(start), utc(end)
        if not 0 < (last - first).total_seconds() <= 31 * 86400:
            raise ValueError("한 번에 1초 초과~31일 범위를 선택하세요.")
        if not keys or len(keys) != len(set(keys)) or len(keys) > 64:
            raise ValueError("서로 다른 자료 1~64개를 선택하세요.")
        available = {x["source_key"]: x for x in self.sources() if x["available"]}
        if any(key not in available for key in keys):
            raise ValueError("검증된 로컬 DB만 선택할 수 있습니다. 수동 동기화 상태를 확인하세요.")
        if shutil.disk_usage(self.config.data_root).free < self.config.minimum_free_bytes:
            raise ValueError("로컬 디스크 여유 공간이 설정된 안전선보다 작습니다.")
        module_path = self.config.project_root.parent / "tools" / "sports_visual_data.py"
        spec = importlib.util.spec_from_file_location("sports_view_export", module_path)
        if spec is None or spec.loader is None:
            raise ValueError("저장소의 sports_visual_data.py가 필요합니다.")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        version = uuid.uuid4().hex
        output = self.root / version
        (output / "events").mkdir(parents=True, mode=0o700)
        index = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "range": {"start": first.isoformat(), "end_exclusive": last.isoformat()},
            "sources": [],
            "cohorts": {},
            "matches": [],
            "sports": SPORTS,
            "semantics": "표시 호가 반사실입니다. 실제 체결·실현 손익이 아닙니다.",
        }
        try:
            selected = [dict(self.catalog.get_artifact(key)) for key in keys]
            handled = set()
            for n, key in enumerate(keys):
                if key in handled:
                    continue
                row = dict(self.catalog.get_artifact(key))
                path = Path(row["local_path"]).resolve()
                if not path.is_relative_to(self.config.data_root.resolve()):
                    raise ValueError("자료 경로가 로컬 데이터 폴더 밖입니다.")
                if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
                    raise ValueError("실행 중인 SQLite 대신 동기화된 snapshot이 필요합니다.")
                callback(
                    {
                        "phase": "index",
                        "source": row["runtime_job"],
                        "completed": n,
                        "total": len(keys),
                    }
                )
                if row["strategy"] == "golden-coconut":
                    group = [
                        s
                        for s in selected
                        if s["strategy"] == "golden-coconut"
                        and s["jenkins_job"] == row["jenkins_job"]
                        and s["runtime_job"] == row["runtime_job"]
                    ]
                    export_group(
                        exporter,
                        self.config.project_root.parent
                        / "golden-coconut"
                        / "src/polybot/recorder_export.py",
                        group,
                        output,
                        first.isoformat(),
                        last.isoformat(),
                        index,
                        self.config.data_root,
                    )
                    handled.update(s["source_key"] for s in group)
                    continue
                source = {
                    "id": key,
                    "source_key": key,
                    "strategy": row["strategy"],
                    "jenkins_job": row["jenkins_job"],
                    "runtime_job": row["runtime_job"],
                    "synced_at": row["synced_at"],
                    "local_path": str(path),
                    "local_sha256": row["local_sha256"],
                }
                exporter.export_source(
                    source,
                    output,
                    first.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    last.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    index,
                    include_depth=True,
                )
            index["cohorts"] = list(index["cohorts"].values())
            index["matches"].sort(key=lambda x: (x["start"], x["title"]), reverse=True)
            (output / "index.json").write_text(
                json.dumps(index, ensure_ascii=False), encoding="utf-8"
            )
            pointer = self.root / f".{version}.json"
            pointer.write_text(json.dumps({"version": version}), encoding="utf-8")
            os.replace(pointer, self.root / "current.json")
        except Exception:
            shutil.rmtree(output)
            raise
        return {"status": "SUCCESS", "matches": len(index["matches"]), "sources": len(keys)}


def dec(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("잘못된 숫자입니다.")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("유한한 숫자가 필요합니다.")
    return result


def walk(book: dict, side: str, amount: Decimal) -> list[tuple[Decimal, Decimal]]:
    levels, seen = [], set()
    if not isinstance(book.get(side), list):
        raise ValueError("해당 시점의 원본 호가가 없습니다.")
    for row in book[side]:
        if not isinstance(row, dict) or "price" not in row or "size" not in row:
            raise ValueError("원본 호가의 가격·잔량 형식이 잘못됐습니다.")
        p, q = dec(row["price"]), dec(row["size"])
        if not 0 < p <= 1 or q <= 0 or p in seen:
            raise ValueError("원본 호가에 잘못된 가격·잔량이 있습니다.")
        levels.append((p, q))
        seen.add(p)
    fills = []
    for price, size in sorted(levels, reverse=side == "bids"):
        quantity = min(size, amount / price if side == "asks" else amount)
        fills.append((price, quantity))
        amount -= quantity * price if side == "asks" else quantity
        if amount <= Decimal("1e-18"):
            return fills
    raise ValueError("해당 시점의 호가 잔량으로 전량 매수·매도할 수 없습니다.")


def rate_from(market: dict) -> Decimal | None:
    if market.get("feesEnabled") is False:
        return Decimal(0)
    schedule = market.get("feeSchedule") or {}
    if not isinstance(schedule, dict):
        return None
    if (
        market.get("feesEnabled") is True
        and schedule.get("exponent") == 1
        and not isinstance(schedule.get("exponent"), bool)
        and schedule.get("takerOnly") is True
        and not isinstance(schedule.get("rate"), bool)
    ):
        try:
            rate = dec(schedule.get("rate"))
        except (ValueError, ArithmeticError):
            return None
        if 0 <= rate <= 1:
            return rate
    return None


def calculate(
    payload: dict, buy_index: int, sell_index: int, amount: float, fee_rate: float | None = None
) -> dict:
    points, depth = payload["points"], payload.get("depth")
    if not depth or len(depth) != len(points):
        raise ValueError("원본 호가를 포함해 경기 인덱스를 다시 만드세요.")
    if min(buy_index, sell_index) < 0 or max(buy_index, sell_index) >= len(points):
        raise ValueError("유효한 A/B 관측을 선택하세요.")
    a, b = points[buy_index], points[sell_index]
    if a[1] != b[1] or a[0] >= b[0]:
        raise ValueError("같은 결과 token에서 A 이후의 B를 선택하세요.")
    if (a[8] | b[8]) & (1 | 8):
        raise ValueError("실패한 실행 또는 식별 근거가 부족한 관측은 계산할 수 없습니다.")
    da, db = depth[buy_index], depth[sell_index]
    token = payload.get("tokens", [{}])[a[1]]
    for item in (da, db):
        book_token = item["book"].get("token_id", item["book"].get("asset_id"))
        if book_token is not None and str(book_token) != str(token.get("token_id")):
            raise ValueError("원본 호가의 token이 선택한 결과와 일치하지 않습니다.")
        condition = item["book"].get("market")
        if condition is not None and str(condition) != str(token.get("condition_id")):
            raise ValueError("원본 호가의 시장이 선택한 결과와 일치하지 않습니다.")
    if da.get("market_open") is False or db.get("market_open") is False:
        raise ValueError("해당 시점에 시장이 주문을 받는 상태가 아니었습니다.")
    cost = dec(amount)
    if not Decimal("0.01") <= cost <= 1000:
        raise ValueError("매수 금액은 $0.01~$1,000입니다.")
    buy = walk(da["book"], "asks", cost)
    shares = sum(q for _, q in buy)
    sell_shares = shares.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if sell_shares <= 0:
        raise ValueError("매도 가능한 수량이 없습니다.")
    sell = walk(db["book"], "bids", sell_shares)
    proceeds = sum(p * q for p, q in sell)
    rates = [rate_from(d.get("fee_market") or {}) for d in (da, db)]
    if fee_rate is not None:
        assumed = dec(fee_rate)
        if not 0 <= assumed <= 1:
            raise ValueError("가정 수수료율은 0~1입니다.")
        rates = [assumed, assumed]
    fees = [
        None
        if r is None
        else sum(
            (q * p * (1 - p) * r).quantize(Decimal(".00001"), rounding=ROUND_HALF_UP)
            for p, q in fills
        )
        for fills, r in zip((buy, sell), rates, strict=False)
    ]
    net = proceeds - cost - sum(fees) if all(f is not None for f in fees) else None
    return {
        "basis": "DISPLAYED_DEPTH_WHAT_IF",
        "fee_basis": "ASSUMED" if fee_rate is not None else "OBSERVED_SCHEDULE",
        "fee_collection": "v2 cash fee assumption",
        "buy_time": a[0],
        "sell_time": b[0],
        "cost": float(cost),
        "shares": float(shares),
        "sell_shares": float(sell_shares),
        "dust_shares_excluded": float(shares - sell_shares),
        "proceeds": float(proceeds),
        "buy_vwap": float(cost / shares),
        "sell_vwap": float(proceeds / sell_shares),
        "gross_pnl": float(proceeds - cost),
        "net_pnl": float(net) if net is not None else None,
        "buy_fee": float(fees[0]) if fees[0] is not None else None,
        "sell_fee": float(fees[1]) if fees[1] is not None else None,
        "intervening_gaps": sum(
            g["start"] < b[0] and g["end"] > a[0] and (g.get("token") is None or g["token"] == a[1])
            for g in payload.get("gaps", [])
        ),
        "market_status_unknown": any(d.get("market_open") is None for d in (da, db)),
        "note": (
            "A의 ask 잔량과 B의 bid 잔량으로 계산한 반사실입니다. "
            "사이 구간의 TP·SL은 재생하지 않습니다. "
            "미매도 잔량은 손익에서 제외합니다. 실제 체결은 보장되지 않습니다."
        ),
    }
