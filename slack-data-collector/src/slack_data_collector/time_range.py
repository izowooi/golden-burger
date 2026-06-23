from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimeRangeError(ValueError):
    """Raised when a requested collection period is invalid."""


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end_exclusive: datetime
    timezone_name: str

    @classmethod
    def from_dates(cls, start: str, end: str, timezone_name: str) -> TimeRange:
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            raise TimeRangeError("기간은 YYYY-MM-DD 형식이어야 합니다.") from exc
        if end_date < start_date:
            raise TimeRangeError("종료일은 시작일보다 빠를 수 없습니다.")

        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise TimeRangeError(f"알 수 없는 타임존입니다: {timezone_name}") from exc

        start_at = datetime.combine(start_date, time.min, tzinfo=timezone)
        end_at = datetime.combine(
            end_date + timedelta(days=1), time.min, tzinfo=timezone
        )
        return cls(start=start_at, end_exclusive=end_at, timezone_name=timezone_name)

    @property
    def oldest(self) -> str:
        return f"{self.start.timestamp():.6f}"

    @property
    def latest(self) -> str:
        return f"{self.end_exclusive.timestamp():.6f}"

    @property
    def start_date(self) -> str:
        return self.start.date().isoformat()

    @property
    def end_date(self) -> str:
        return (self.end_exclusive.date() - timedelta(days=1)).isoformat()

    def contains_ts(self, slack_ts: str) -> bool:
        try:
            timestamp = Decimal(slack_ts)
        except (InvalidOperation, TypeError) as exc:
            raise TimeRangeError(f"잘못된 Slack timestamp입니다: {slack_ts!r}") from exc
        return Decimal(self.oldest) <= timestamp < Decimal(self.latest)

    def to_utc_iso(self, slack_ts: str) -> str:
        return datetime.fromtimestamp(float(slack_ts), tz=UTC).isoformat()

    def to_local_date(self, slack_ts: str) -> str:
        timezone = ZoneInfo(self.timezone_name)
        return (
            datetime.fromtimestamp(float(slack_ts), tz=UTC)
            .astimezone(timezone)
            .date()
            .isoformat()
        )
