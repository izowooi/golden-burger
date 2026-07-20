"""Repository pattern for database operations."""
import csv
import hashlib
import json
import logging
import math
from polybot_observability import (
    compact_maintenance_active,
    current_run_id,
    membership_details_due,
)
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import (
    Trade, TradeStatus, SkippedMarket, MarketSnapshot, MarketCatalog,
    MarketSweep, MarketSweepMembership, CycleStat, CappedCandidate,
)

logger = logging.getLogger(__name__)


class TradeRepository:
    """CRUD operations for trades."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, trade_id: int) -> Optional[Trade]:
        """Get trade by ID."""
        return self.session.query(Trade).get(trade_id)

    def create_trade(self, **kwargs) -> Trade:
        """Create a new trade record."""
        trade = Trade(**kwargs)
        self.session.add(trade)
        self.session.commit()
        return trade

    def update_trade(self, trade_id: int, **kwargs) -> Trade:
        """Update an existing trade."""
        trade = self.session.query(Trade).get(trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")

        for key, value in kwargs.items():
            if hasattr(trade, key):
                setattr(trade, key, value)

        trade.updated_at = datetime.utcnow()
        self.session.commit()
        return trade

    def get_holding_trades(self) -> List[Trade]:
        """Get all trades currently in HOLDING status.

        EXPIRED(해결됐지만 청산 못한 시장)는 제외된다.
        """
        return self.session.query(Trade).filter(
            Trade.status == TradeStatus.HOLDING
        ).all()

    def get_all_trades(self) -> List[Trade]:
        """Get all trades."""
        return self.session.query(Trade).all()

    # ------------------------------------------------------------------
    # 재진입 제어 (영구 one-shot 대신 쿨다운 기반)
    # ------------------------------------------------------------------

    def has_holding(self, condition_id: str) -> bool:
        """해당 시장에 HOLDING 포지션이 있는지 확인."""
        count = self.session.query(func.count(Trade.id)).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.HOLDING,
        ).scalar() or 0
        return count > 0

    def is_reentry_blocked(
        self,
        condition_id: str,
        cooldown_hours: float,
        now: Optional[datetime] = None,
    ) -> tuple[bool, str]:
        """재진입 차단 여부 판정.

        차단 조건:
        - HOLDING 포지션 존재
        - 마지막 COMPLETED sell_timestamp가 cooldown_hours 이내
        - 마지막 skipped_markets 기록이 cooldown_hours 이내

        기본 쿨다운 168h(7일) - 롤링 최저가 부근에서는 매 사이클이 신저가라
        연속 재진입이 일어나기 쉬우므로 길게 잡는다.

        Returns:
            (차단 여부, 사유)
        """
        if now is None:
            now = datetime.utcnow()
        cutoff = now - timedelta(hours=cooldown_hours)

        if self.has_holding(condition_id):
            return True, "holding_position"

        last_sell = self.session.query(func.max(Trade.sell_timestamp)).filter(
            Trade.condition_id == condition_id,
            Trade.status == TradeStatus.COMPLETED,
        ).scalar()
        if last_sell is not None and last_sell >= cutoff:
            return True, "sell_cooldown"

        last_skip = self.session.query(func.max(SkippedMarket.skipped_at)).filter(
            SkippedMarket.condition_id == condition_id,
        ).scalar()
        if last_skip is not None and last_skip >= cutoff:
            return True, "skip_cooldown"

        return False, "ok"

    def mark_as_skipped(self, condition_id: str, reason: str) -> SkippedMarket:
        """Mark a market as skipped (쿨다운 시작점 기록)."""
        skipped = SkippedMarket(condition_id=condition_id, reason=reason)
        self.session.add(skipped)
        self.session.commit()
        return skipped

    # ------------------------------------------------------------------
    # max_positions 튜닝 계측 (사이클 통계 + 상한 스킵 후보)
    # ------------------------------------------------------------------

    def save_cycle_stats(self, **kwargs) -> CycleStat:
        """사이클 단위 매수 파이프라인 통계 기록."""
        stat = CycleStat(**kwargs)
        self.session.add(stat)
        self.session.commit()
        return stat

    def save_capped_candidate(
        self,
        condition_id: str,
        question: str,
        yes_price: float,
        rolling_min: float = None,
        hours_left: float = None,
        dedup_hours: float = 24.0,
    ) -> Optional[CappedCandidate]:
        """상한 스킵 후보 기록. 같은 시장은 dedup_hours 내 1회만 기록.

        상한에 걸린 후보는 신호가 유지되는 한 사이클(5분)마다 다시 스킵되므로,
        dedup 없이는 하루 수천 행이 쌓이고 반사실 회고 때 중복 집계된다.
        """
        cutoff = datetime.utcnow() - timedelta(hours=dedup_hours)
        exists = (
            self.session.query(CappedCandidate)
            .filter(
                CappedCandidate.condition_id == condition_id,
                CappedCandidate.ts >= cutoff,
            )
            .first()
        )
        if exists:
            return None
        capped = CappedCandidate(
            condition_id=condition_id,
            question=question,
            yes_price=yes_price,
            rolling_min=rolling_min,
            hours_left=hours_left,
        )
        self.session.add(capped)
        self.session.commit()
        return capped

    # ------------------------------------------------------------------
    # 스냅샷 (YES 가격 기준)
    # ------------------------------------------------------------------

    def save_snapshot(
        self,
        condition_id: str,
        probability: float,
        liquidity: float = None,
        volume_24h: float = None,
        best_bid: float = None,
        best_ask: float = None,
        spread: float = None,
        source_updated_at: str = None,
        market: Optional[Dict[str, Any]] = None,
        commit: bool = True,
    ) -> MarketSnapshot:
        """Save a market snapshot (probability = YES 가격)."""
        if market is not None:
            self._upsert_market_catalog(condition_id, market)
        snapshot = MarketSnapshot(
            condition_id=condition_id,
            probability=probability,
            liquidity=liquidity,
            volume_24h=volume_24h,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            source_updated_at=source_updated_at,
            run_id=current_run_id(),
        )
        self.session.add(snapshot)
        if commit:
            self.session.commit()
        return snapshot

    def commit(self) -> None:
        """Commit a batch of snapshots/catalog upserts atomically."""
        self.session.commit()

    def rollback(self) -> None:
        """Rollback the current snapshot/sweep transaction."""
        self.session.rollback()

    def save_market_catalog(
        self,
        condition_id: str,
        market: Dict[str, Any],
        *,
        commit: bool = False,
    ) -> None:
        """Upsert qualified-universe metadata even when no price row is eligible."""
        self._upsert_market_catalog(condition_id, market)
        if commit:
            self.session.commit()

    @staticmethod
    def _attestation_datetime(value: Any) -> datetime:
        """Parse an attestation timestamp and normalize it to naive UTC for SQLite."""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def record_market_sweep(
        self,
        attestation: Dict[str, Any],
        snapshot_results: Dict[str, Dict[str, Any]],
        *,
        commit: bool = False,
    ) -> MarketSweep:
        """Persist snapshots/catalog/qualified memberships as one evidence unit."""
        if not attestation or attestation.get("cursor_complete") is not True:
            raise ValueError("완료된 Gamma sweep attestation만 저장할 수 있습니다")
        if int(attestation.get("schema_version", 0)) != 1:
            raise ValueError("지원하지 않는 Gamma sweep schema_version입니다")
        if int(attestation.get("pages", 0)) < 1:
            raise ValueError("Gamma sweep pages는 1 이상이어야 합니다")

        memberships = attestation.get("memberships")
        if not isinstance(memberships, list):
            raise ValueError("Gamma sweep memberships가 list가 아닙니다")
        if attestation.get("membership_digest_scope") != "qualified_only":
            raise ValueError("Gamma sweep digest scope는 qualified_only여야 합니다")
        for item in memberships:
            if not isinstance(item, dict):
                raise ValueError("Gamma sweep membership은 object여야 합니다")
            if not isinstance(item.get("qualified"), bool):
                raise ValueError("Gamma sweep qualified는 boolean이어야 합니다")
            raw_seen = item.get("raw_seen_count")
            if isinstance(raw_seen, bool) or not isinstance(raw_seen, int):
                raise ValueError("Gamma sweep raw_seen_count는 integer여야 합니다")
            reason = item.get("qualification_reason")
            if not isinstance(reason, str) or not reason:
                raise ValueError("Gamma sweep qualification_reason이 필요합니다")
        condition_ids = [str(item.get("condition_id") or "") for item in memberships]
        if any(not condition_id for condition_id in condition_ids):
            raise ValueError("Gamma sweep membership condition_id가 비어 있습니다")
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("Gamma sweep membership condition_id가 중복되었습니다")

        canonical_memberships = sorted(
            [
                {
                    "condition_id": str(item["condition_id"]),
                    "raw_seen_count": int(item["raw_seen_count"]),
                    "qualified": bool(item["qualified"]),
                    "qualification_reason": str(item["qualification_reason"]),
                }
                for item in memberships
            ],
            key=lambda item: item["condition_id"],
        )
        if any(item["raw_seen_count"] < 1 for item in canonical_memberships):
            raise ValueError("Gamma sweep raw_seen_count는 1 이상이어야 합니다")
        qualified_memberships = [
            item for item in canonical_memberships if item["qualified"]
        ]
        digest = hashlib.sha256(
            json.dumps(
                qualified_memberships,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if digest != attestation.get("membership_digest_sha256"):
            raise ValueError("Gamma sweep membership digest가 일치하지 않습니다")

        unique_count = len(canonical_memberships)
        qualified_count = len(qualified_memberships)
        excluded_count = unique_count - qualified_count
        exclusion_counts: Dict[str, int] = {}
        for item in canonical_memberships:
            if item["qualified"]:
                continue
            reason = item["qualification_reason"]
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
        if attestation.get("exclusion_counts") != dict(sorted(exclusion_counts.items())):
            raise ValueError("Gamma sweep exclusion_counts가 membership과 불일치합니다")
        missing_count = int(attestation.get("missing_condition_id_count", 0))
        if missing_count < 0:
            raise ValueError("missing_condition_id_count는 음수일 수 없습니다")
        raw_count = sum(item["raw_seen_count"] for item in canonical_memberships) + missing_count
        duplicate_count = raw_count - missing_count - unique_count
        expected_counts = {
            "unique_condition_count": unique_count,
            "qualified_market_count": qualified_count,
            "excluded_condition_count": excluded_count,
            "raw_market_count": raw_count,
            "duplicate_raw_count": duplicate_count,
        }
        for field, expected in expected_counts.items():
            if int(attestation.get(field, -1)) != expected:
                raise ValueError(f"Gamma sweep {field} 집계가 membership과 불일치합니다")

        result_ids = set(snapshot_results)
        qualified_ids = {
            item["condition_id"] for item in canonical_memberships if item["qualified"]
        }
        if result_ids != qualified_ids:
            raise ValueError("모든 qualified condition의 snapshot 판정이 필요합니다")
        sweep_id = str(attestation.get("sweep_id") or "")
        if not sweep_id:
            raise ValueError("Gamma sweep_id가 비어 있습니다")
        started_at = self._attestation_datetime(attestation["started_at"])
        completed_at = self._attestation_datetime(attestation["completed_at"])
        if completed_at < started_at:
            raise ValueError("Gamma sweep completed_at이 started_at보다 빠릅니다")
        min_liquidity = float(attestation.get("min_liquidity", 0))
        min_volume = float(attestation.get("min_volume", 0))
        if (
            not math.isfinite(min_liquidity)
            or not math.isfinite(min_volume)
            or min_liquidity < 0
            or min_volume < 0
        ):
            raise ValueError("Gamma sweep filters must be finite and non-negative")

        enriched = []
        for membership in qualified_memberships:
            result = snapshot_results[membership["condition_id"]]
            eligible = result.get("snapshot_eligible") is True
            snapshotted = result.get("snapshotted") is True
            reason = str(result.get("snapshot_reason") or "")
            if not reason:
                raise ValueError("snapshot_reason은 비어 있을 수 없습니다")
            if snapshotted and not eligible:
                raise ValueError("snapshotted condition은 snapshot_eligible이어야 합니다")
            enriched.append((membership, eligible, snapshotted, reason))

        store_membership_details = membership_details_due(
            self.session, "golden-nectarine"
        )
        sweep = MarketSweep(
            sweep_id=sweep_id,
            schema_version=int(attestation["schema_version"]),
            run_id=current_run_id(),
            started_at=started_at,
            completed_at=completed_at,
            cursor_complete=1,
            pages=int(attestation["pages"]),
            raw_market_count=raw_count,
            unique_condition_count=unique_count,
            qualified_market_count=qualified_count,
            excluded_condition_count=excluded_count,
            exclusion_counts_json=json.dumps(
                exclusion_counts, sort_keys=True, separators=(",", ":")
            ),
            missing_condition_id_count=missing_count,
            duplicate_raw_count=duplicate_count,
            min_liquidity=min_liquidity,
            min_volume=min_volume,
            membership_digest_sha256=digest,
            snapshotted_market_count=sum(int(row[2]) for row in enriched),
            membership_detail_stored=int(store_membership_details),
        )
        self.session.add(sweep)
        if store_membership_details:
            for membership, eligible, snapshotted, reason in enriched:
                self.session.add(
                    MarketSweepMembership(
                        sweep_id=sweep.sweep_id,
                        condition_id=membership["condition_id"],
                        raw_seen_count=membership["raw_seen_count"],
                        qualified=int(membership["qualified"]),
                        qualification_reason=membership["qualification_reason"],
                        snapshot_eligible=int(eligible),
                        snapshotted=int(snapshotted),
                        snapshot_reason=reason,
                    )
                )
        if commit:
            self.session.commit()
        return sweep

    def _upsert_market_catalog(self, condition_id: str, market: Dict[str, Any]) -> None:
        """Upsert replay metadata in the same transaction as its price snapshot."""
        events = market.get("events") or []
        event = events[0] if events and isinstance(events[0], dict) else {}
        tags = market.get("tags") or []
        fee_schedule = market.get("feeSchedule") or {}
        values = {
            "market_id": str(market.get("id") or "") or None,
            "market_slug": market.get("slug"),
            "question": market.get("question"),
            "event_id": str(event.get("id") or "") or None,
            "event_slug": event.get("slug"),
            "end_date": market.get("endDate"),
            "outcomes_json": json.dumps(market.get("outcomes") or [], ensure_ascii=False),
            "token_ids_json": json.dumps(market.get("clobTokenIds") or []),
            "tags_json": json.dumps(
                [
                    {"id": tag.get("id"), "slug": tag.get("slug"), "label": tag.get("label")}
                    for tag in tags if isinstance(tag, dict)
                ],
                ensure_ascii=False,
            ),
            "fees_enabled": (
                None
                if market.get("feesEnabled") is None
                else int(bool(market.get("feesEnabled")))
            ),
            "fee_rate": fee_schedule.get("rate"),
            "last_seen_at": datetime.utcnow(),
        }
        catalog = self.session.get(MarketCatalog, condition_id)
        if catalog is None:
            self.session.add(MarketCatalog(condition_id=condition_id, **values))
            return
        for key, value in values.items():
            setattr(catalog, key, value)

    def get_snapshots_since(
        self,
        condition_id: str,
        since: datetime,
    ) -> List[MarketSnapshot]:
        """특정 마켓의 since 이후 스냅샷 조회 (시간순 정렬).

        개수 기반이 아닌 timestamp 기반 조회 - 윈도우 검증은 signals.py가 담당.

        Args:
            condition_id: 마켓 condition ID
            since: 이 시각 이후의 스냅샷만

        Returns:
            시간순 정렬된 스냅샷 리스트 (오래된 것 먼저)
        """
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id,
            MarketSnapshot.timestamp >= since,
        ).order_by(
            MarketSnapshot.timestamp.asc()
        ).all()

    def get_latest_snapshot(
        self,
        condition_id: str
    ) -> Optional[MarketSnapshot]:
        """마켓의 최신 스냅샷 조회."""
        return self.session.query(MarketSnapshot).filter(
            MarketSnapshot.condition_id == condition_id
        ).order_by(
            MarketSnapshot.timestamp.desc()
        ).first()

    def cleanup_old_snapshots(self, days: float = 7) -> int:
        """오래된 스냅샷 정리 (디스크 공간 관리).

        Args:
            days: 보관 일수 (전략 lookback의 3배, 최소 60일)

        Returns:
            삭제된 스냅샷 수
        """
        if compact_maintenance_active(self.session, "golden-nectarine"):
            return 0
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = self.session.query(MarketSnapshot).filter(
            MarketSnapshot.timestamp < cutoff
        ).delete()
        expired_sweep_ids = [
            row[0]
            for row in self.session.query(MarketSweep.sweep_id).filter(
                MarketSweep.completed_at < cutoff
            ).all()
        ]
        if expired_sweep_ids:
            self.session.query(MarketSweepMembership).filter(
                MarketSweepMembership.sweep_id.in_(expired_sweep_ids)
            ).delete(synchronize_session=False)
            self.session.query(MarketSweep).filter(
                MarketSweep.sweep_id.in_(expired_sweep_ids)
            ).delete(synchronize_session=False)
        self.session.commit()
        if deleted > 0:
            logger.info(f"오래된 스냅샷 {deleted}개 삭제 (기준: {days}일)")
        if expired_sweep_ids:
            logger.info(
                f"오래된 Gamma sweep evidence {len(expired_sweep_ids)}개 삭제 "
                f"(기준: {days}일)"
            )
        return deleted

    # ------------------------------------------------------------------
    # 통계
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get trading statistics."""
        total = self.session.query(func.count(Trade.id)).scalar() or 0

        holding = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.HOLDING
        ).scalar() or 0

        completed = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.COMPLETED
        ).scalar() or 0

        expired = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.EXPIRED
        ).scalar() or 0

        quarantined = self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.QUARANTINED
        ).scalar() or 0

        total_pnl = self.session.query(func.sum(Trade.realized_pnl)).filter(
            Trade.realized_pnl.isnot(None)
        ).scalar() or 0.0

        skipped = self.session.query(func.count(SkippedMarket.id)).scalar() or 0

        return {
            "total_trades": total,
            "holding": holding,
            "completed": completed,
            "expired": expired,
            "quarantined": quarantined,
            "skipped": skipped,
            "total_pnl": round(total_pnl, 4),
        }

    def get_position_count(self) -> int:
        """Get current number of open positions."""
        return self.session.query(func.count(Trade.id)).filter(
            Trade.status == TradeStatus.HOLDING
        ).scalar() or 0

    def append_trade_to_csv(self, trade: Trade, db_dir) -> None:
        """완료된 거래를 월별 CSV 파일에 추가 (시그널 컬럼 포함 - §A.7).

        파일: data/{job_name}/trades_YYYY-MM.csv
        파일이 없으면 헤더 포함 생성, 있으면 행 추가.

        Args:
            trade: 완료된 Trade 객체
            db_dir: DB 파일이 있는 디렉토리 (Path 또는 str)
        """
        sell_ts = trade.sell_timestamp or datetime.utcnow()
        month_str = sell_ts.strftime("%Y-%m")
        csv_path = Path(db_dir) / f"trades_{month_str}.csv"

        headers = [
            "id", "strategy_name", "mode",
            "question", "outcome", "market_slug",
            "buy_price", "sell_price", "realized_pnl",
            "buy_timestamp", "sell_timestamp",
            "exit_reason", "entry_reason",
            "rolling_min_at_buy", "lookback_days_at_buy",
            "hold_hours_at_exit",
            "hours_until_resolution_at_buy",
            "volume_24h_at_buy",
            "buy_probability", "sell_probability",
            "market_tags",
        ]

        row = {
            "id": trade.id,
            "strategy_name": trade.strategy_name or "",
            "mode": trade.mode or "",
            "question": trade.question,
            "outcome": trade.outcome,
            "market_slug": trade.market_slug or "",
            "buy_price": trade.buy_price,
            "sell_price": trade.sell_price or "",
            "realized_pnl": round(trade.realized_pnl, 6) if trade.realized_pnl is not None else "",
            "buy_timestamp": trade.buy_timestamp.isoformat() if trade.buy_timestamp else "",
            "sell_timestamp": trade.sell_timestamp.isoformat() if trade.sell_timestamp else "",
            "exit_reason": trade.exit_reason or "",
            "entry_reason": trade.entry_reason or "",
            "rolling_min_at_buy": trade.rolling_min_at_buy if trade.rolling_min_at_buy is not None else "",
            "lookback_days_at_buy": trade.lookback_days_at_buy if trade.lookback_days_at_buy is not None else "",
            "hold_hours_at_exit": trade.hold_hours_at_exit if trade.hold_hours_at_exit is not None else "",
            "hours_until_resolution_at_buy": trade.hours_until_resolution_at_buy or "",
            "volume_24h_at_buy": trade.volume_24h_at_buy if trade.volume_24h_at_buy is not None else "",
            "buy_probability": trade.buy_probability or "",
            "sell_probability": trade.sell_probability or "",
            "market_tags": trade.market_tags or "",
        }

        file_exists = csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        logger.info(f"거래 이력 CSV 저장: {csv_path}")
