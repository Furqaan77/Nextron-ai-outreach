"""``SystemClock`` (``nextron.adapters.clock``) implements the ``Clock`` port with UTC-aware
times; ``nextron open`` and ``/enter`` stamp and check the one-time code with it."""

from datetime import UTC, datetime, timedelta

from nextron.adapters.clock import SystemClock
from nextron.ports.clock import Clock


def test_system_clock_implements_the_port() -> None:
    assert isinstance(SystemClock(), Clock)


def test_now_is_utc_aware_and_current() -> None:
    before = datetime.now(UTC)
    now = SystemClock().now()
    after = datetime.now(UTC)

    assert now.utcoffset() == timedelta(0)
    assert before <= now <= after
