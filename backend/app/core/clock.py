from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock pinned to a calendar date (noon UTC) unless a datetime is given."""

    def __init__(self, value: date | datetime):
        self.value = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day, 12, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def local_today(clock: Clock, timezone_name: str) -> date:
    """Business dates (check-in rules, "today") are in the hotel's own time zone."""
    return clock.now().astimezone(ZoneInfo(timezone_name)).date()
