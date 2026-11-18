"""``InMemoryUsageMeter``: the in-memory fake of ``nextron.ports.usage_meter.UsageMeterPort``.

``records`` keeps every recorded call, in order, each as a ``Recorded(call, at)`` stamped with
the fake's clock (``clock``, a ``FrozenClock`` unless one is given). ``calls`` lists just the
calls. ``snapshot(now)`` sums the records of ``now``'s UTC calendar month the way
``nextron.db.metering.UsageMeter`` does: one line per provider, operation and SKU (calls,
failed calls, units), plus the AI call count and spend. ``day_calls(now, provider, operation)``
(Story 3.12) counts the ``ApiCall`` records of that provider and operation on ``now``'s UTC
day. It never touches a database.
"""

from dataclasses import dataclass
from datetime import datetime

from nextron.domain.budget import AiCall, ApiCall, ApiUse, Usage, day_bounds, month_of
from nextron.ports.clock import Clock
from tests.fakes.clock import FrozenClock


@dataclass(frozen=True, slots=True)
class Recorded:
    """One recorded call and the fake clock's time when it was recorded."""

    call: ApiCall | AiCall
    at: datetime


class InMemoryUsageMeter:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock: Clock = clock if clock is not None else FrozenClock()
        self.records: list[Recorded] = []

    @property
    def calls(self) -> list[ApiCall | AiCall]:
        return [recorded.call for recorded in self.records]

    def record(self, call: ApiCall | AiCall) -> None:
        self.records.append(Recorded(call, self.clock.now()))

    def snapshot(self, now: datetime) -> Usage:
        month = month_of(now)
        lines: dict[tuple[str, str, str | None], list[int]] = {}
        ai_calls = ai_cost = 0
        for recorded in self.records:
            if month_of(recorded.at) != month:
                continue
            call = recorded.call
            if isinstance(call, ApiCall):
                line = lines.setdefault((call.provider, call.operation, call.sku), [0, 0, 0])
                line[0] += 1
                line[1] += 0 if call.ok else 1
                line[2] += call.units
            else:
                ai_calls += 1
                ai_cost += call.cost_micros
        return Usage(
            month=month,
            api=tuple(
                ApiUse(provider, operation, sku, calls, failed, units)
                for (provider, operation, sku), (calls, failed, units) in lines.items()
            ),
            ai_calls=ai_calls,
            ai_cost_micros=ai_cost,
        )

    def day_calls(self, now: datetime, provider: str, operation: str) -> int:
        start, end = day_bounds(now)
        return sum(
            1
            for recorded in self.records
            if isinstance(recorded.call, ApiCall)
            and recorded.call.provider == provider
            and recorded.call.operation == operation
            and start <= recorded.at < end
        )
