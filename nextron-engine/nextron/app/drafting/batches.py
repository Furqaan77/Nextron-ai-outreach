"""The ``drafting`` service's Message Batches (AD-8, AD-10, AD-16, AD-18, INT-7; Story 4.18): each
Enrollment's follow-up Steps 2 to 4, drafted in the afternoon batch with a ``draft_deadline``
(07:00 by default) after which the work runs directly. Every row it writes through
``nextron.db.ai_batches`` is owner ``drafting`` (``BatchOwner.DRAFTING``); no log line holds text:
ids, codes and counts only. No budget check: ``draft_sequence`` is ``essential``.

``Drafting.request_steps`` queues one ``pending`` item per Enrollment (route ``draft_sequence``,
target the Enrollment) when it creates any of Steps 2 to 4's slots.

**``DraftingBatches(clock, llm, queue, follow_ups)``**: ``follow_ups`` is the ``StepDrafts``
(``nextron.app.drafting.generation``) that builds, checks and writes a follow-up answer;
``queue`` enqueues the direct ``draft_step:<enrollment>:2`` job (interactive lane, ``soon``).

**``@use_case submit_bulk() -> int``** (the daily ``submit_bulk_ai`` job; how many batches it
sent). One transaction: of the ``pending`` items for one Enrollment all but the newest become
``stale``; the newest is built now by ``StepDrafts.follow_up_call``:

- an unknown or ended Enrollment, no Draft 2 to 4 left open, or a Contact erased, gone or blocked
  (AD-16): ``stale``;
- a paused Enrollment, or a Step 1 with no revision yet: it stays ``pending``;
- no active ``draft_sequence`` tuple, or one on schema 1: Drafts 2 to 4 ``draft_failed``
  (``no_route``) and ``stale``; a context that can't be read: ``not_composable`` and ``stale``;
- the Enrollment's deadline (``deadline_of``) at or before now: ``stale`` and the direct job, no
  batch request;
- otherwise a ``BatchRequest`` (its ``custom_id``, the blocks, the framing) and its fingerprint.

Then, per route tuple key, ``llm.submit_batch(tuple, requests)`` with no transaction open (the
request carries no ``fallbacks``; an ``AdapterError`` propagates and the items stay ``pending``),
then one transaction writes the batch row and marks each item ``submitted`` with its
fingerprint.

**``@use_case poll() -> int``** (``poll_ai_batches``, every 15 minutes; how many items it
settled), for each batch not yet deleted, oldest first, as ``ResearchBatches.poll`` (a
non-retryable ``AdapterError`` from one batch is logged and the next polled; a retryable one
propagates), except that a batch not yet ``ended`` is fetched even when every item is terminal
already (a batch in progress can't be deleted). The one exception: a batch whose route tuple no
longer resolves can't be read, so it is neither fetched nor deleted at the provider; its open
items become ``rerun`` with the direct job and the row is marked ``deleted`` locally. Ended:
each ``submitted`` item, re-read in its own transaction:

- ``BatchSucceeded(LlmOk)``: ``apply_draft_sequence_result``;
- ``BatchSucceeded(LlmInvalid)``: the Enrollment ended or gone, no Draft 2 to 4 open, or a
  Contact erased, gone or blocked (AD-16): ``stale``, nothing written; else ``failed``, Drafts 2
  to 4 ``draft_failed`` (``invalid``);
- refused, truncated, lost or no result: ``rerun`` and the direct job.

Once every item is terminal: ``llm.delete_batch``, then the row ``deleted``. Then **the deadline
sweep**, one transaction: each open (``pending`` or ``submitted``) drafting item whose
Enrollment ended becomes ``stale``; one whose Enrollment's deadline (``deadline_of``) is at or
before now, whose Enrollment is ``active`` and whose Step 1 has a revision becomes ``stale`` and
the direct job is enqueued (so a later batch result writes nothing); a paused one, or one with no
Step 1 revision, is left alone.

**``apply_draft_sequence_result(item, parsed) -> ItemStatus``** (in the item's transaction):
the Enrollment ended or gone, a Contact erased, gone or blocked (``assert_contactable(
ContactSubject, "email", "cold")``), or no Draft 2 to 4 open: ``stale``, nothing written. The
Enrollment paused, Step 1 with no revision, the tuple gone or changed, or the fingerprint
recomputed now unequal to the stored one: ``stale`` and a new ``pending`` item (``rev`` + 1),
or the direct job when the deadline has passed. Otherwise ``StepDrafts.write_follow_ups`` at
``soon``: ``applied`` when it appended any revision, else ``failed`` (``invalid_proposal``).

**``deadline_of(created_at) -> datetime``**: the Enrollment's deadline,
``domain.step_drafting.draft_deadline_of`` with the ``bulk_ai_submit_at`` and
``draft_deadline`` settings in ``sender_timezone`` (UTC until it is set).

**``schedule() -> BulkAiSchedule``**: as ``ResearchBatches.schedule``, for drafting's items.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Final

from nextron.app.drafting.generation import (
    FollowUpCall,
    FollowUpState,
    StepDraftOutcome,
    StepDrafts,
    enqueue_follow_up_job,
    requeue_follow_ups,
)
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import ai_batches
from nextron.db.ai_batches import StoredBatch, StoredItem
from nextron.db.uow import current, use_case
from nextron.domain.ai import Urgency
from nextron.domain.ai_batch import (
    TERMINAL,
    BatchOwner,
    BatchStatus,
    BulkAiSchedule,
    ItemStatus,
)
from nextron.domain.backup import latest_slot
from nextron.domain.draft import STEP_KINDS
from nextron.domain.enrollment import LIVE_STATUSES, EnrollmentStatus
from nextron.domain.settings_schema import Key, SettingNotSet
from nextron.domain.step_drafting import (
    DRAFT_SEQUENCE_ROUTE,
    FIRST_STEP,
    DraftFailure,
    draft_deadline_of,
)
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    BatchEnded,
    BatchItemResult,
    BatchRequest,
    BatchSucceeded,
    LlmInvalid,
    LlmOk,
    LlmPort,
)

log = logging.getLogger(__name__)

OWNER: Final = BatchOwner.DRAFTING
ROUTE: Final = DRAFT_SEQUENCE_ROUTE
UTC_TIMEZONE: Final = "UTC"
AI_BATCH_SUBMITTED: Final = "ai_batch_submitted"
AI_BATCH_ITEM_SETTLED: Final = "ai_batch_item_settled"
AI_BATCH_DELETED: Final = "ai_batch_deleted"
AI_BATCH_TUPLE_MISSING: Final = "ai_batch_tuple_missing"
AI_BATCH_POLL_FAILED: Final = "ai_batch_poll_failed"
DRAFT_DEADLINE_PASSED: Final = "draft_deadline_passed"

# no call can be built yet: the item waits
_WAIT: Final = frozenset({FollowUpState.PAUSED, FollowUpState.NO_STEP_1})
# a follow-up call fails without a call: Drafts 2 to 4 record why
# nothing may be written for the Enrollment: its item goes stale
_GONE: Final = frozenset(
    {
        FollowUpState.UNKNOWN,
        FollowUpState.ENDED,
        FollowUpState.NOTHING_OPEN,
        FollowUpState.NOT_CONTACTABLE,
        FollowUpState.BLOCKED,
    }
)
_FAILS: Final = {
    FollowUpState.NO_ROUTE: DraftFailure.NO_ROUTE,
    FollowUpState.NOT_COMPOSABLE: DraftFailure.NOT_COMPOSABLE,
}


@dataclass(frozen=True, slots=True)
class _Plan:
    item: StoredItem
    call: FollowUpCall


class DraftingBatches:
    """The ``drafting`` service's Message Batches; see the module docstring."""

    def __init__(self, clock: Clock, llm: LlmPort, queue: JobQueue, follow_ups: StepDrafts) -> None:
        self._clock = clock
        self._llm = llm
        self._queue = queue
        self._follow_ups = follow_ups

    # Submit -----------------------------------------------------------------------------------

    @use_case
    def submit_bulk(self) -> int:
        """Send the queued follow-up items as Message Batches; how many batches were sent."""
        return self.submit_queued()

    def submit_queued(self) -> int:
        """``submit_bulk``'s work inside the caller's use case (the ``submit_bulk_ai`` job runs
        every batch owner's in its one use case)."""
        uow = current()
        now = self._clock.now()
        plans: list[_Plan] = []
        with uow.transaction():
            newest: dict[str, StoredItem] = {}
            for item in ai_batches.pending(OWNER, ROUTE):  # oldest first
                older = newest.get(item.target_id)
                if older is not None:
                    ai_batches.settle(OWNER, older.id, ItemStatus.STALE, now)
                newest[item.target_id] = item
            for item in newest.values():
                built = self._follow_ups.follow_up_call(item.target_id)
                if isinstance(built, FollowUpCall):
                    if self._past_deadline(built.enrollment_id, now):
                        # too late for a batch: directly at soon, no batch request
                        ai_batches.settle(OWNER, item.id, ItemStatus.STALE, now)
                        enqueue_follow_up_job(self._queue, built.enrollment_id)
                        log.info(DRAFT_DEADLINE_PASSED, extra={"enrollment_id": item.target_id})
                        continue
                    plans.append(_Plan(item, built))
                    continue
                if built in _WAIT:
                    continue  # stays pending
                failure = _FAILS.get(built)
                if failure is not None:
                    self._follow_ups.fail_follow_ups(item.target_id, failure)
                ai_batches.settle(OWNER, item.id, ItemStatus.STALE, now)
        groups: dict[str, list[_Plan]] = {}
        for plan in plans:
            groups.setdefault(plan.call.route_tuple.key, []).append(plan)
        for key, group in groups.items():
            batch_id = self._llm.submit_batch(
                group[0].call.route_tuple,
                [
                    BatchRequest(plan.item.custom_id, plan.call.blocks, plan.call.framing)
                    for plan in group
                ],
            )
            with uow.transaction():
                batch_row_id = ai_batches.insert_batch(
                    OWNER,
                    provider_batch_id=batch_id,
                    route=ROUTE,
                    route_tuple_key=key,
                    at=now,
                )
                for plan in group:
                    ai_batches.mark_submitted(
                        OWNER,
                        plan.item.id,
                        batch_row_id=batch_row_id,
                        fingerprint=plan.call.fingerprint.encode(),
                        at=now,
                    )
            log.info(
                AI_BATCH_SUBMITTED,
                extra={"batch_row_id": batch_row_id, "route": ROUTE, "items": len(group)},
            )
        return len(groups)

    # Poll -------------------------------------------------------------------------------------

    @use_case
    def poll(self) -> int:
        """Settle the items of every ended batch, delete each settled batch, then re-run the
        follow-ups whose deadline passed; how many items were settled."""
        return self.poll_open()

    def poll_open(self) -> int:
        """``poll``'s work inside the caller's use case (the ``poll_ai_batches`` job runs every
        batch owner's in its one use case)."""
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
        return settled + self._sweep()

    def _poll(self, batch: StoredBatch) -> int:
        uow = current()
        with uow.transaction():
            items = ai_batches.items_of(OWNER, batch.id)
            route_tuple = playbook_queries.tuple_by_key(batch.route, batch.route_tuple_key)
        settled = 0
        open_items = [item for item in items if item.status == ItemStatus.SUBMITTED]
        if route_tuple is None:
            # without its tuple the batch can't be read: no provider call; the work runs directly
            log.warning(AI_BATCH_TUPLE_MISSING, extra={"batch_row_id": batch.id})
            for item in open_items:
                settled += self._rerun(item)
            with uow.transaction():
                ai_batches.mark_deleted(OWNER, batch.id, self._clock.now())
            return settled
        if batch.status != BatchStatus.ENDED:  # fetched until ended, even with no open item
            state = self._llm.fetch_batch(batch.provider_batch_id, route_tuple, metered=True)
            if not isinstance(state, BatchEnded):
                return 0
            with uow.transaction():
                ai_batches.mark_ended(OWNER, batch.id, self._clock.now())
            results = state.results
        elif open_items:  # an item left open by a retryable error: re-read, metered once
            state = self._llm.fetch_batch(batch.provider_batch_id, route_tuple, metered=False)
            results = state.results if isinstance(state, BatchEnded) else {}
        else:
            results = {}
        for item in open_items:
            settled += self._settle(item, results.get(item.custom_id))
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

    def _settle(self, item: StoredItem, result: BatchItemResult | None) -> int:
        """Settle one ``submitted`` item by its result; 1 when it settled, else 0."""
        answer = result.result if isinstance(result, BatchSucceeded) else None
        if not isinstance(answer, LlmOk | LlmInvalid):
            return self._rerun(item)  # refused, truncated, lost or missing: directly at soon
        with current().transaction():
            fresh = ai_batches.item(OWNER, item.id)
            if fresh is None or fresh.status != ItemStatus.SUBMITTED:
                return 0  # settled meanwhile
            if isinstance(answer, LlmOk):
                status = self.apply_draft_sequence_result(fresh, answer.parsed)
            elif _gone(self._follow_ups.follow_up_call(fresh.target_id)):
                status = ItemStatus.STALE  # ended, erased or blocked (AD-16): nothing written
            else:
                self._follow_ups.fail_follow_ups(fresh.target_id, DraftFailure.INVALID)
                status = ItemStatus.FAILED
            ai_batches.settle(OWNER, item.id, status, self._clock.now())
        self._log_settled(item, status)
        return 1

    def _rerun(self, item: StoredItem) -> int:
        """``rerun`` and the direct job, in one transaction; 1 when it settled."""
        with current().transaction():
            changed = ai_batches.settle(OWNER, item.id, ItemStatus.RERUN, self._clock.now())
            if changed:
                enqueue_follow_up_job(self._queue, item.target_id)
        if not changed:
            return 0
        self._log_settled(item, ItemStatus.RERUN)
        return 1

    # Apply ------------------------------------------------------------------------------------

    def apply_draft_sequence_result(self, item: StoredItem, parsed: dict[str, Any]) -> ItemStatus:
        """Apply one follow-up answer in the item's transaction; its item status. See the module
        docstring."""
        enrollment = sequencing_queries.enrollment(item.target_id)
        if enrollment is None:
            return ItemStatus.STALE
        # the one gate call (AD-16: drafting.batch_apply) is follow_up_call's, before any write
        built = self._follow_ups.follow_up_call(enrollment.id)
        if _gone(built):
            return ItemStatus.STALE
        if not isinstance(built, FollowUpCall) or built.fingerprint.encode() != item.fingerprint:
            # paused, Step 1 edited or gone, the tuple or the inputs changed: again, later
            self._again(item, enrollment.created_at)
            return ItemStatus.STALE
        written = self._follow_ups.write_follow_ups(built, parsed, Urgency.SOON)
        if written is StepDraftOutcome.DRAFTED:
            return ItemStatus.APPLIED
        return ItemStatus.FAILED if written is StepDraftOutcome.FAILED else ItemStatus.STALE

    def _again(self, item: StoredItem, created_at: datetime) -> None:
        """A new ``pending`` item, or the direct job once the deadline has passed."""
        now = self._clock.now()
        if self.deadline_of(created_at) <= now:
            enqueue_follow_up_job(self._queue, item.target_id)
            log.info(DRAFT_DEADLINE_PASSED, extra={"enrollment_id": item.target_id})
        else:
            requeue_follow_ups(item.target_id, now, besides=item.id)

    # The deadline -----------------------------------------------------------------------------

    def _settings(self) -> tuple[time, time, str]:
        submit_at = setup_queries.get(Key.BULK_AI_SUBMIT_AT)
        deadline = setup_queries.get(Key.DRAFT_DEADLINE)
        if not isinstance(submit_at, time) or not isinstance(deadline, time):
            raise TypeError("bulk_ai_submit_at and draft_deadline must be times")
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except SettingNotSet:
            timezone = UTC_TIMEZONE
        return submit_at, deadline, timezone

    def deadline_of(self, created_at: datetime) -> datetime:
        """The deadline of an Enrollment created at ``created_at``; see the module docstring."""
        submit_at, deadline, timezone = self._settings()
        return draft_deadline_of(created_at, submit_at, deadline, timezone)

    def _past_deadline(self, enrollment_id: str, now: datetime) -> bool:
        """Whether the Enrollment's deadline is at or before ``now`` (in the caller's
        transaction)."""
        enrollment = sequencing_queries.enrollment(enrollment_id)
        return enrollment is not None and self.deadline_of(enrollment.created_at) <= now

    def _sweep(self) -> int:
        """The deadline sweep; how many items it settled."""
        now = self._clock.now()
        settled: list[tuple[StoredItem, ItemStatus]] = []
        with current().transaction():
            submit_at, deadline, timezone = self._settings()
            for item in ai_batches.open_items(OWNER, ROUTE):
                enrollment = sequencing_queries.enrollment(item.target_id)
                status = None if enrollment is None else EnrollmentStatus(enrollment.status)
                if enrollment is None or status not in LIVE_STATUSES:
                    if ai_batches.settle(OWNER, item.id, ItemStatus.STALE, now):
                        settled.append((item, ItemStatus.STALE))
                    continue
                if draft_deadline_of(enrollment.created_at, submit_at, deadline, timezone) > now:
                    continue
                if status is not EnrollmentStatus.ACTIVE or not _has_step_1(enrollment.id):
                    continue  # paused, or Step 1 not drafted yet: left alone
                if ai_batches.settle(OWNER, item.id, ItemStatus.STALE, now):
                    enqueue_follow_up_job(self._queue, enrollment.id)
                    settled.append((item, ItemStatus.STALE))
                    log.info(DRAFT_DEADLINE_PASSED, extra={"enrollment_id": enrollment.id})
        for item, status in settled:
            self._log_settled(item, status)
        return len(settled)

    @staticmethod
    def _log_settled(item: StoredItem, status: ItemStatus) -> None:
        log.info(
            AI_BATCH_ITEM_SETTLED,
            extra={
                "item_id": item.id,
                "route": item.route,
                "enrollment_id": item.target_id,
                "status": str(status),
            },
        )

    # Schedule ---------------------------------------------------------------------------------

    def schedule(self) -> BulkAiSchedule:
        """When the daily submit runs and whether a queued item missed it."""
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


def _gone(built: FollowUpCall | FollowUpState) -> bool:
    """Whether nothing may be written for the Enrollment (its item goes ``stale``)."""
    return isinstance(built, FollowUpState) and built in _GONE


def _has_step_1(enrollment_id: str) -> bool:
    """Whether the Enrollment's Step 1 Draft has a revision."""
    return any(
        draft.kind in STEP_KINDS and draft.step_no == FIRST_STEP and draft.current_rev is not None
        for draft in drafting_queries.drafts_of(enrollment_id)
    )
