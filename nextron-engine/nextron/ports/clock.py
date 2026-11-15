"""The ``Clock`` port (AD-11).

Only app services hold a ``Clock``, and each use case reads it once and passes ``now`` down to
the domain. Tests use ``tests.fakes.clock.FrozenClock``.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a UTC-aware ``datetime``."""
        ...
