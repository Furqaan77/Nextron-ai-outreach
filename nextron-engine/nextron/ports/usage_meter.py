"""The ``UsageMeterPort`` (AD-8, AD-9, FR-57): every external call, counted.

Adapters record each HTTP request they attempt (an ``ApiCall``) and each AI call (an
``AiCall``, Story 2.7) through it; ``nextron.db.metering.UsageMeter`` implements it
structurally and is the only writer of ``api_usage`` and ``ai_usage``. Tests use
``tests.fakes.usage_meter.InMemoryUsageMeter``. The records and the budget rules live in
``nextron.domain.budget``.

- ``record(call)`` writes one row in its own transaction, which commits on its own, so the
  usage is kept even when the caller's work rolls back. It never raises: a row that can't be
  written is logged, because the provider has already done (and billed) the work. It must be
  called outside any transaction (AD-3); inside one it writes nothing.
- ``snapshot(now)`` is the usage of ``now``'s UTC calendar month, read through the caller's
  current unit of work: inside a transaction, or in a read-only unit.
- ``day_calls(now, provider, operation)`` (Story 3.12) is how many ``ApiCall`` rows of that
  provider and operation were recorded on ``now``'s UTC calendar day, ``ok`` or failed, read
  the same way.

``Metered(meter, provider, operation)`` is how an HTTP adapter records a request: a context
manager around the one request that records exactly one ``ApiCall`` when it ends, ``ok`` or
with the ``AdapterError``'s code, before the error propagates. It needs no ``raise`` of its own,
so an adapter never re-raises a caught error (``tests/contracts/test_error_contracts.py``).
"""

from datetime import datetime
from types import TracebackType
from typing import Final, Literal, Protocol, Self, runtime_checkable

from nextron.domain.budget import AiCall, ApiCall, Usage
from nextron.ports.errors import AdapterError

MAX_CODE_LENGTH: Final = 64  # nextron.domain.budget.CODE_PATTERN


@runtime_checkable
class UsageMeterPort(Protocol):
    """Records external calls and reads a month's usage."""

    def record(self, call: ApiCall | AiCall) -> None:
        """Write one usage row in its own transaction; never raises."""
        ...

    def snapshot(self, now: datetime) -> Usage:
        """The usage of ``now``'s UTC calendar month, read in the current unit of work."""
        ...

    def day_calls(self, now: datetime, provider: str, operation: str) -> int:
        """The calls of ``provider`` and ``operation`` on ``now``'s UTC day, read in the
        current unit of work."""
        ...


class Metered:
    """``with Metered(meter, provider, operation):`` around one HTTP request records exactly one
    ``ApiCall`` when the block ends: ``ok=True`` when it ends normally, else ``ok=False`` with the
    ``AdapterError``'s code, recorded before the error propagates (it is never suppressed). The
    block may set ``count`` (``0`` for a call the provider doesn't bill) or ``sku`` on the
    object ``with`` gives it. An adapter enters it only once the request is about to be made, so
    a call refused before any request records nothing; only ``AdapterError`` leaves an adapter's
    request, so nothing else is recorded."""

    __slots__ = ("_meter", "_operation", "_provider", "count", "sku")

    def __init__(
        self,
        meter: UsageMeterPort,
        provider: str,
        operation: str,
        *,
        sku: str | None = None,
        count: int = 1,
    ) -> None:
        self._meter = meter
        self._provider = provider
        self._operation = operation
        self.sku = sku
        self.count = count

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        if error is None:
            call = ApiCall(
                self._provider, self._operation, ok=True, sku=self.sku, count=self.count
            )
        elif isinstance(error, AdapterError):
            call = ApiCall(
                self._provider,
                self._operation,
                ok=False,
                error_code=error.code[:MAX_CODE_LENGTH],
                sku=self.sku,
                count=self.count,
            )
        else:
            return False  # not an adapter outcome: an adapter lets only AdapterError out
        self._meter.record(call)
        return False
