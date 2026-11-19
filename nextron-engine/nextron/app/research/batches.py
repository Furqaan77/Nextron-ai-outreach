"""The ``research`` service's Message Batches (AD-8, AD-18, INT-7, FR-57; Story 4.17): its bulk
extraction work, sent as one batch per route and settled when it ends. ``nextron.app.research.
api``'s docstring points here. Every row it writes through ``nextron.db.ai_batches`` is owner
``research`` (``BatchOwner.RESEARCH``); no log line holds text: ids, codes and counts only.

**``ResearchBatches(clock, meter, llm, extractions, people)``**: ``extractions`` is the
``SignalExtractions`` (route ``extract_signals``) and ``people`` the ``PeopleExtractions``
(``extract_people``) whose ``bulk`` steps queued the ``pending`` items.

**``@use_case submit_bulk() -> int``** (the daily ``submit_bulk_ai`` job; how many batches it
submitted). For each route, in that order:

1. One transaction. ``domain.budget.allow(<the route's budget class>, meter.snapshot(now),
   queries.setup.budget_limits())`` first: refused, every item stays ``pending``, nothing is
   sent and ``bulk_ai_deferred`` is logged (``route``). Otherwise, of the route's ``pending``
   items for one Lead, all but the newest become ``stale``; the newest is rebuilt now by its
   step's ``batch_request``: an unknown or merged Lead, or a step that records its
   ``no_activation`` or ``no_pages`` run instead, makes it ``stale``.
2. No request left: nothing is sent. Otherwise, per route tuple, ``llm.submit_batch(tuple,
   requests)`` with no transaction open (an ``AdapterError`` propagates and the items stay
   ``pending``), then one transaction writes the batch row (``submitted_at`` the step-1 now)
   and marks each of its items ``submitted`` with its fingerprint. ``ai_batch_submitted``
   logs ``batch_row_id``, ``route`` and ``items``.

**``@use_case poll() -> int``** (the ``poll_ai_batches`` job, every 15 minutes; how many items
it settled). For each batch not yet deleted, oldest first; a non-retryable ``AdapterError``
from one batch is logged (``ai_batch_poll_failed``: ``batch_row_id``, ``error_code``) and the
next batch is polled, while a retryable one propagates (the job retries):

- while an item is ``submitted``: the batch's tuple (``queries.playbook.tuple_by_key``, which
  falls back to the stored tuple once another is active). None left: every open item becomes
  ``failed`` and the row ``deleted`` locally with no provider call (the batch can't be read
  without its tuple; logged ``ai_batch_tuple_missing``). Otherwise
  ``llm.fetch_batch(provider_batch_id, tuple, metered=<the row isn't ended yet>)``, so a
  re-fetch of an ended batch (an item left open by a retryable re-run error) meters nothing
  again. In progress: nothing more. Ended: the row
  becomes ``ended`` (``ended_at`` now), then each ``submitted`` item, re-read in its own
  transaction (one settled meanwhile is passed over):

  - ``BatchSucceeded(LlmOk)``: the step's ``apply_<route>_result`` (in that transaction):
    ``applied`` or ``stale``;
  - ``BatchSucceeded(LlmInvalid)``: the step's ``record_invalid``: ``failed``;
  - ``LlmRefused``, ``LlmTruncated``, ``BatchLost`` or no result: the step's ``rerun``, a direct
    call at ``soon`` with its own budget check, then ``rerun`` (``failed`` when the budget
    refused it). A retryable ``AdapterError`` propagates: the item stays ``submitted`` and
    the job retries; a non-retryable one makes the item ``failed`` (logged
    ``ai_batch_rerun_failed``: ``item_id``, ``route``, ``error_code``).

  Each settled item logs ``ai_batch_item_settled`` (``item_id``, ``route``, ``lead_id``,
  ``status``).
- once every item is terminal: ``llm.delete_batch`` (a 404 counts as deleted), then the row
  becomes ``deleted`` (``deleted_at`` now); ``ai_batch_deleted`` logs ``batch_row_id``.

A re-poll of an ended batch therefore touches no settled item, and a deleted batch is never
fetched again.

**``schedule() -> BulkAiSchedule(at, timezone, missed)``** (read in the current unit of work, a
read-only one included): the ``bulk_ai_submit_at`` setting, ``sender_timezone`` (UTC until it is
set), and whether a ``pending`` item was queued before the latest daily time (a submit the
engine missed while it was off).
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Final, Protocol

from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.extraction import BatchPlan, RecordedRun, Settled
from nextron.db import ai_batches
from nextron.db.ai_batches import StoredBatch, StoredItem
from nextron.db.uow import current, use_case
from nextron.domain import extraction as extraction_rules
from nextron.domain import people as people_rules
from nextron.domain.ai_batch import (
    TERMINAL,
    BatchOwner,
    BatchStatus,
    BulkAiSchedule,
    ItemStatus,
)
from nextron.domain.backup import latest_slot
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.settings_schema import Key, SettingNotSet
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    BatchEnded,
    BatchItemResult,
    BatchSucceeded,
    LlmInvalid,
    LlmOk,
    LlmPort,
)
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

OWNER: Final = BatchOwner.RESEARCH
UTC_TIMEZONE: Final = "UTC"
BULK_AI_DEFERRED: Final = "bulk_ai_deferred"
AI_BATCH_SUBMITTED: Final = "ai_batch_submitted"
AI_BATCH_ITEM_SETTLED: Final = "ai_batch_item_settled"
AI_BATCH_DELETED: Final = "ai_batch_deleted"
AI_BATCH_TUPLE_MISSING: Final = "ai_batch_tuple_missing"
AI_BATCH_POLL_FAILED: Final = "ai_batch_poll_failed"
AI_BATCH_RERUN_FAILED: Final = "ai_batch_rerun_failed"


class BatchStep(Protocol):
    """What the batches need of one research step (``SignalExtractions`` or
    ``PeopleExtractions``)."""

    def batch_request(
        self, item: StoredItem, started_at: datetime
    ) -> BatchPlan | RecordedRun | None: ...

    def record_invalid(self, item: StoredItem, submitted_at: datetime) -> Settled: ...

    def rerun(self, item: StoredItem) -> bool: ...


Apply = Callable[[StoredItem, Mapping[str, Any], datetime], Settled]


class SignalsStep(BatchStep, Protocol):
    def apply_extract_signals_result(
        self, item: StoredItem, parsed: Mapping[str, Any], submitted_at: datetime
    ) -> Settled: ...


class PeopleStep(BatchStep, Protocol):
    def apply_extract_people_result(
        self, item: StoredItem, parsed: Mapping[str, Any], submitted_at: datetime
    ) -> Settled: ...


@dataclass(frozen=True, slots=True)
class _Route:
    """One batched route: its step, its result's apply and its budget class."""

    name: str
    step: BatchStep
    apply: Apply
    budget_class: BudgetClass


class ResearchBatches:
    """The ``research`` service's Message Batches; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        llm: LlmPort,
        extractions: SignalsStep,
        people: PeopleStep,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._llm = llm
        self._routes: dict[str, _Route] = {
            extraction_rules.ROUTE: _Route(
                extraction_rules.ROUTE,
                extractions,
                extractions.apply_extract_signals_result,
                extraction_rules.BUDGET_CLASS,
            ),
            people_rules.ROUTE: _Route(
                people_rules.ROUTE,
                people,
                people.apply_extract_people_result,
                people_rules.BUDGET_CLASS,
            ),
        }

    # Submit -----------------------------------------------------------------------------------

    @use_case
    def submit_bulk(self) -> int:
        """Send each route's queued items as Message Batches; how many batches were sent."""
        return self.submit_queued()

    def submit_queued(self) -> int:
        """``submit_bulk``'s work inside the caller's use case (Story 4.18: the
        ``submit_bulk_ai`` job runs every batch owner's in its one use case)."""
        return sum(self._submit(route) for route in self._routes.values())

    def _submit(self, route: _Route) -> int:
        uow = current()
        now = self._clock.now()
        plans: list[tuple[StoredItem, BatchPlan]] = []
        recorded: list[RecordedRun] = []
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(route.budget_class, self._meter.snapshot(now), limits):
                deferred = True
            else:
                deferred = False
                newest: dict[str, StoredItem] = {}
                for item in ai_batches.pending(OWNER, route.name):  # oldest first
                    older = newest.get(item.target_id)
                    if older is not None:
                        ai_batches.settle(OWNER, older.id, ItemStatus.STALE, now)
                    newest[item.target_id] = item
                for item in newest.values():
                    plan = route.step.batch_request(item, now)
                    if isinstance(plan, BatchPlan):
                        plans.append((item, plan))
                        continue
                    if isinstance(plan, RecordedRun):
                        recorded.append(plan)
                    ai_batches.settle(OWNER, item.id, ItemStatus.STALE, now)
        if deferred:
            log.info(BULK_AI_DEFERRED, extra={"route": route.name})
            return 0
        for run in recorded:
            run.log()
        groups: dict[str, list[tuple[StoredItem, BatchPlan]]] = {}
        for item, plan in plans:
            groups.setdefault(plan.route_tuple.key, []).append((item, plan))
        for key, group in groups.items():
            route_tuple = group[0][1].route_tuple
            batch_id = self._llm.submit_batch(route_tuple, [plan.request for _, plan in group])
            with uow.transaction():
                batch_row_id = ai_batches.insert_batch(
                    OWNER,
                    provider_batch_id=batch_id,
                    route=route.name,
                    route_tuple_key=key,
                    at=now,
                )
                for item, plan in group:
                    ai_batches.mark_submitted(
                        OWNER,
                        item.id,
                        batch_row_id=batch_row_id,
                        fingerprint=plan.fingerprint.encode(),
                        at=now,
                    )
            log.info(
                AI_BATCH_SUBMITTED,
                extra={"batch_row_id": batch_row_id, "route": route.name, "items": len(group)},
            )
        return len(groups)

    # Poll -------------------------------------------------------------------------------------

    @use_case
    def poll(self) -> int:
        """Settle the items of every ended batch and delete each settled batch; how many items
        were settled."""
        return self.poll_open()

    def poll_open(self) -> int:
        """``poll``'s work inside the caller's use case (Story 4.18: the ``poll_ai_batches`` job
        runs every batch owner's in its one use case)."""
        with current().transaction():
            batches = ai_batches.open_batches(OWNER)
        settled = 0
        for batch in batches:
            try:
                settled += self._poll(batch)
            except AdapterError as error:
                if error.retryable:
                    raise
                log.warning(
                    AI_BATCH_POLL_FAILED,
                    extra={"batch_row_id": batch.id, "error_code": error.code},
                )
        return settled

    def _poll(self, batch: StoredBatch) -> int:
        uow = current()
        with uow.transaction():
            items = ai_batches.items_of(OWNER, batch.id)
            route_tuple = None
            if any(item.status == ItemStatus.SUBMITTED for item in items):
                route_tuple = playbook_queries.tuple_by_key(batch.route, batch.route_tuple_key)
        settled = 0
        open_items = [item for item in items if item.status == ItemStatus.SUBMITTED]
        if open_items and route_tuple is None:
            # without its tuple the batch can't be read: no provider call (it may still run)
            log.warning(AI_BATCH_TUPLE_MISSING, extra={"batch_row_id": batch.id})
            for item in open_items:
                settled += self._settle_as(item, ItemStatus.FAILED)
            with uow.transaction():
                ai_batches.mark_deleted(OWNER, batch.id, self._clock.now())
            return settled
        if open_items:
            assert route_tuple is not None
            state = self._llm.fetch_batch(
                batch.provider_batch_id,
                route_tuple,
                metered=batch.status != BatchStatus.ENDED,  # an ended batch was metered once
            )
            if not isinstance(state, BatchEnded):
                return 0
            with uow.transaction():
                ai_batches.mark_ended(OWNER, batch.id, self._clock.now())
            for item in open_items:
                settled += self._settle(item, state.results.get(item.custom_id), batch)
        with uow.transaction():
            left = [
                item for item in ai_batches.items_of(OWNER, batch.id) if item.status not in TERMINAL
            ]
        if left:
            return settled
        self._llm.delete_batch(batch.provider_batch_id)
        with uow.transaction():
            ai_batches.mark_deleted(OWNER, batch.id, self._clock.now())
        log.info(AI_BATCH_DELETED, extra={"batch_row_id": batch.id})
        return settled

    def _settle(
        self, item: StoredItem, result: BatchItemResult | None, batch: StoredBatch
    ) -> int:
        """Settle one ``submitted`` item by its result; 1 when it settled, else 0."""
        route = self._routes.get(item.route)
        if route is None:  # a route this owner never queues: nothing can apply it
            return self._settle_as(item, ItemStatus.FAILED)
        answer = result.result if isinstance(result, BatchSucceeded) else None
        if isinstance(answer, LlmOk | LlmInvalid):
            uow = current()
            with uow.transaction():
                fresh = ai_batches.item(OWNER, item.id)
                if fresh is None or fresh.status != ItemStatus.SUBMITTED:
                    return 0  # settled meanwhile
                if isinstance(answer, LlmOk):
                    done = route.apply(fresh, answer.parsed, batch.submitted_at)
                else:
                    done = route.step.record_invalid(fresh, batch.submitted_at)
                ai_batches.settle(OWNER, item.id, done.status, self._clock.now())
            if done.log is not None:
                done.log()
            self._log_settled(item, done.status)
            return 1
        # refused, truncated, lost or missing: the step again, directly at soon
        try:
            ran = route.step.rerun(item)
        except AdapterError as error:
            if error.retryable:
                raise  # the item stays submitted; the job retries
            log.warning(
                AI_BATCH_RERUN_FAILED,
                extra={"item_id": item.id, "route": item.route, "error_code": error.code},
            )
            return self._settle_as(item, ItemStatus.FAILED)
        return self._settle_as(item, ItemStatus.RERUN if ran else ItemStatus.FAILED)

    def _settle_as(self, item: StoredItem, status: ItemStatus) -> int:
        with current().transaction():
            changed = ai_batches.settle(OWNER, item.id, status, self._clock.now())
        if not changed:
            return 0
        self._log_settled(item, status)
        return 1

    @staticmethod
    def _log_settled(item: StoredItem, status: ItemStatus) -> None:
        log.info(
            AI_BATCH_ITEM_SETTLED,
            extra={
                "item_id": item.id,
                "route": item.route,
                "lead_id": item.target_id,
                "status": str(status),
            },
        )

    # Schedule ---------------------------------------------------------------------------------

    def schedule(self) -> BulkAiSchedule:
        """When the daily submit runs and whether a queued item missed it; see the module
        docstring."""
        now = self._clock.now()
        at = setup_queries.get(Key.BULK_AI_SUBMIT_AT)
        if not isinstance(at, time):
            raise TypeError("bulk_ai_submit_at must be a time")
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except SettingNotSet:
            timezone = UTC_TIMEZONE
        oldest = ai_batches.oldest_pending_at(OWNER)
        missed = oldest is not None and oldest < latest_slot(now, at, timezone)
        return BulkAiSchedule(at, timezone, missed)
