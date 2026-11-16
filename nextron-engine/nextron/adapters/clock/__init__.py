"""The system clock adapter of the ``Clock`` port (AD-11).

Import rule: ``nextron.ports``, ``nextron.domain`` and the standard library.
"""

from datetime import UTC, datetime


class SystemClock:
    """``Clock`` on the system time."""

    def now(self) -> datetime:
        """Return the current time, UTC-aware."""
        return datetime.now(UTC)
