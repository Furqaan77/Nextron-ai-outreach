"""``FrozenClock`` implements the ``Clock`` port."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.ports.clock import Clock
from tests.fakes.clock import FrozenClock


def test_frozen_clock_implements_the_port() -> None:
    clock: Clock = FrozenClock()

    assert isinstance(clock, Clock)


def test_now_is_utc_aware_and_stands_still() -> None:
    clock = FrozenClock(datetime(2026, 11, 16, 9, 0, tzinfo=UTC))

    assert clock.now() == clock.now() == datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_advance_and_set_move_the_clock() -> None:
    clock = FrozenClock(datetime(2026, 11, 16, 9, 0, tzinfo=UTC))

    clock.advance(timedelta(minutes=15))
    assert clock.now() == datetime(2026, 11, 16, 9, 15, tzinfo=UTC)

    clock.set(datetime(2026, 11, 16, 12, 0, tzinfo=timezone(timedelta(hours=1))))
    assert clock.now() == datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 11, 16, 9, 0))
