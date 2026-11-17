"""The usage meter (AD-8, AD-9, FR-57): the only writer of ``api_usage`` and ``ai_usage``, and the
only SQL on them.

``UsageMeter(units, clock, *, after_record=None)`` implements ``nextron.ports.usage_meter.
UsageMeterPort`` structurally (this package never imports ``nextron.ports``). ``nextron.main``
builds one on the entry points' unit-of-work factory and hands it to every HTTP adapter, with
``after_record`` set to the ``ops`` service's threshold check.

**record(call)** writes one row for an ``ApiCall`` or an ``AiCall`` (``nextron.domain.budget``)
and never raises: the provider has already done, and billed, the work, so failing the call
would retry it and pay twice.

1. Inside the caller's transaction (``nextron.db.uow.in_transaction()``) it writes nothing and
   returns at once, without waiting for the database lock: a port call never runs inside a
   transaction (AD-3). It logs ``usage_record_failed`` with code ``inside_transaction``.
2. Otherwise, in a fresh ``contextvars.Context()`` (so it works while the caller's unit of work
   is open, between that unit's transactions), it opens ``units.open()`` and runs a use case
   that inserts the row in one transaction, which commits on its own: the usage is kept even
   when the caller's own work later rolls back. ``at`` is ``clock.now()``; an ``ApiCall``'s
   ``units`` are ``call.units`` (Hunter's credits doubled). A failure logs
   ``usage_record_failed`` and writes nothing.
3. After that unit closes, still in the fresh context, it calls ``after_record()`` if one is
   set; an error there logs ``usage_alerts_failed`` (ERROR, with the traceback) and the row
   stays.

``usage_record_failed`` (ERROR) carries ``provider`` and ``operation`` (or ``route``) and the
error ``code``, never another field value. The meter writes no ``events`` row.

**snapshot(now)** is ``now``'s UTC calendar month (``[start, end)``), read through the current
unit of work's session, a read-only one included, in two ``SELECT``s: ``api_usage`` grouped by
provider, operation and SKU (the row count, the failed count and the sum of units), and
``ai_usage`` (the row count and the sum of ``cost_micros``).
"""

import contextvars
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Final, Protocol

from sqlalchemy import Executable, case, func, insert, select

from nextron.db.models.metering import AiUsageRecord, ApiUsageRecord
from nextron.db.uow import current, in_transaction, use_case
from nextron.domain.budget import AiCall, ApiCall, ApiUse, Usage, month_bounds, month_of
from nextron.domain.errors import DomainError

INSIDE_TRANSACTION: Final = "inside_transaction"
NOT_A_CALL: Final = "not_a_call"
USAGE_RECORD_FAILED: Final = "usage_record_failed"
USAGE_ALERTS_FAILED: Final = "usage_alerts_failed"

log = logging.getLogger(__name__)


class Units(Protocol):
    """What the meter needs of ``nextron.db.uow.UnitOfWorkFactory``."""

    def open(self, *, read_only: bool = False) -> AbstractContextManager[object]: ...


class Clock(Protocol):
    """What the meter needs of ``nextron.ports.clock.Clock``."""

    def now(self) -> datetime: ...


def _code(error: BaseException) -> str:
    return error.code if isinstance(error, DomainError) else type(error).__name__


def _failed(call: object, code: str) -> None:
    """Log ``usage_record_failed`` with the call's provider and operation, or its route."""
    extra: dict[str, object] = {}
    if isinstance(call, ApiCall):
        extra = {"provider": call.provider, "operation": call.operation}
    elif isinstance(call, AiCall):
        extra = {"route": call.route}
    log.error(USAGE_RECORD_FAILED, extra={**extra, "code": code})


def _statement(call: ApiCall | AiCall, at: datetime) -> Executable:
    if isinstance(call, ApiCall):
        return insert(ApiUsageRecord).values(
            provider=call.provider,
            operation=call.operation,
            sku=call.sku,
            units=call.units,
            ok=call.ok,
            error_code=call.error_code,
            at=at,
        )
    return insert(AiUsageRecord).values(
        route=call.route,
        route_tuple=call.route_tuple,
        served_model=call.served_model,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cache_read_tokens=call.cache_read_tokens,
        cache_write_tokens=call.cache_write_tokens,
        iterations=call.iterations,
        batch=call.batch,
        cost_micros=call.cost_micros,
        refused=call.refused,
        error_code=call.error_code,
        at=at,
    )


@use_case
def insert_usage(call: ApiCall | AiCall, clock: Clock) -> None:
    """Insert ``call``'s row in one transaction of its own, stamped with ``clock.now()``."""
    at = clock.now()
    with current().transaction():
        current().session.execute(_statement(call, at))


class UsageMeter:
    """``UsageMeterPort`` on the database; see the module docstring."""

    def __init__(
        self,
        units: Units,
        clock: Clock,
        *,
        after_record: Callable[[], object] | None = None,
    ) -> None:
        self._units = units
        self._clock = clock
        self._after_record = after_record

    def record(self, call: ApiCall | AiCall) -> None:
        """Write one usage row in its own transaction; never raises."""
        try:
            if not isinstance(call, ApiCall | AiCall):
                _failed(call, NOT_A_CALL)
                return
            if in_transaction():
                _failed(call, INSIDE_TRANSACTION)
                return
            contextvars.Context().run(self._write_then_hook, call)
        except Exception as error:  # noqa: BLE001 - a usage row never fails the call
            _failed(call, _code(error))

    def _write_then_hook(self, call: ApiCall | AiCall) -> None:
        try:
            with self._units.open():
                insert_usage(call, self._clock)
        except Exception as error:  # noqa: BLE001 - logged; the provider already did the work
            _failed(call, _code(error))
            return
        if self._after_record is None:
            return
        try:
            self._after_record()
        except Exception:  # noqa: BLE001 - the row is kept; a notice can wait for the next one
            log.exception(USAGE_ALERTS_FAILED)

    def snapshot(self, now: datetime) -> Usage:
        """``now``'s UTC calendar month of usage, read in the current unit of work."""
        start, end = month_bounds(now)
        session = current().session
        api = ApiUsageRecord
        lines = session.execute(
            select(
                api.provider,
                api.operation,
                api.sku,
                func.count(),
                func.sum(case((api.ok.is_(False), 1), else_=0)),
                func.sum(api.units),
            )
            .where(api.at >= start, api.at < end)
            .group_by(api.provider, api.operation, api.sku)
        ).all()
        ai = AiUsageRecord
        calls, cost = session.execute(
            select(func.count(), func.coalesce(func.sum(ai.cost_micros), 0)).where(
                ai.at >= start, ai.at < end
            )
        ).one()
        return Usage(
            month=month_of(now),
            api=tuple(
                ApiUse(provider, operation, sku, int(count), int(failed), int(units))
                for provider, operation, sku, count, failed, units in lines
            ),
            ai_calls=int(calls),
            ai_cost_micros=int(cost),
        )
