"""``FrozenClock``: the in-memory fake of ``nextron.ports.clock.Clock``."""

from datetime import UTC, datetime, timedelta

DEFAULT_NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


class FrozenClock:
    """A clock that stands still until a test moves it."""

    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        self._now = self._utc(now)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("FrozenClock needs a timezone-aware datetime")
        return value.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = self._utc(now)

    def advance(self, delta: timedelta) -> None:
        self._now += delta
