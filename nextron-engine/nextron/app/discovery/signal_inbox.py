"""The Signal Inbox (FR-8, FR-9, FR-11, FR-49, AD-4, AD-7, AD-8, AD-10, AD-18; Story 2.16): part
of the ``discovery`` service, which owns ``signal_inbox_items``. Mac pastes a post or job ad with
its URL; the engine identifies the organization, stores the item as the Lead's Evidence and runs
the research chain on the interactive lane.

The rules (the machine, the form, what a parse may give) are pure and live in
``nextron.domain.signal_inbox_item``. No log line, ``events`` row, error or job payload holds
the pasted text, a quote, a name or a URL: IDs, codes and counts only. An item's URL is stored as
a source only and is never fetched by any job.

**``SignalInbox(clock, meter, llm, discovery, research, queue)``**:

- ``@use_case submit(items) -> tuple[str, ...]`` (the Signal Inbox form): in one transaction,
  ``discovery.submit_signal_items(items)`` stores each item ``pending`` with its submission time
  (one ``events`` row each, cause ``submitted``, actor ``operator``) and enqueues its
  ``process_signal_item`` job on the interactive lane (scope the item, discriminator ``tick``,
  payload ``{v, item_id}``); it logs ``signal_items_submitted`` with the count.

- ``@use_case process_signal_item(item_id, *, last_attempt=False) -> bool`` (the
  ``process_signal_item`` job; ``last_attempt`` on the job's last allowed attempt):

  1. **First transaction.** When ``nextron.domain.budget.allow(BUDGET_CLASS,
     meter.snapshot(now), queries.setup.budget_limits())`` refuses (discovery's budget class),
     it returns ``False`` (the job is deferred) and the item stays ``pending``. An unknown item
     (a text that isn't a ULID included) or one no longer ``pending`` logs
     ``signal_item_skipped`` (``item_id``, ``reason``) and returns ``True``. With no active
     ``signal_inbox_parse`` tuple (``queries.playbook.active_tuple``) the item is flagged
     ``parse_failed``. Otherwise it reads every Segment (``queries.setup.segments()``) and
     builds the call's blocks, all of subject ``signal_item:<item id>``: one ``signal_types``
     block (``signal_catalogue`` of every Segment; block id and source ``signal_types``), then
     one ``signal_inbox`` block (block id: the item's id) holding the item text, then, when
     given, a line ``Author: <author>`` and a line ``Company: <company>``.
  2. **The call**, with no transaction open: ``llm.run(tuple_, blocks, Urgency.SOON)``. A
     retryable ``AdapterError`` is raised again (the runner retries the job; the item stays
     ``pending``), except on the last attempt, which flags the item ``parse_failed``; any other ``AdapterError``, and ``LlmRefused``, ``LlmTruncated``,
     ``LlmInvalid`` and ``LlmFailed``, flag the item ``parse_failed``.
  3. **Apply transaction** after ``LlmOk``, on an item still ``pending``:
     ``accept_parse(parsed, item, segments, submitted_at)`` (a malformed answer flags
     ``parse_failed``); the accepted fields and ``extracted_by`` (the tuple's actor) are stored
     on the item, so a reprocess needs no second call. When the parse's ``flag`` is set (no
     website: ``organization_unknown``; no Segment: ``segment_unknown``) the item is flagged
     with it. Otherwise it is applied (below).

- ``@use_case reprocess(item_id, choice) -> ReprocessOutcome`` (the Signal Inbox's reprocess
  POST): in one transaction, on a ``flagged`` item only, with ``choice`` either an existing
  Lead (``ReprocessChoice(lead=<id or Lead page link>)``; a merged Lead resolves to its
  survivor) or a website and a Segment key (``ReprocessChoice(website=..., segment=...)``).
  The stored parse fields are applied as they are; no AI call. An unknown item, an item that
  isn't flagged, a choice with neither (or both), an unknown Lead, a website that isn't a
  registrable domain or is FR-49-denied or a shared platform, an unknown Segment, or a new Lead
  the contactability gate blocks answer with that outcome, and the item stays flagged and
  unchanged. On success the item becomes ``processed`` (event ``reprocess``, actor
  ``operator``) and ``enrich_lead`` is enqueued on the interactive lane.

**Applying** (both paths, in the caller's transaction): a website path runs
``discovery.upsert_lead(LeadIdentity(name=lead_name(company, website), segment_id,
web_domain=website), LeadSource.SIGNAL_INBOX)``, which returns the existing Lead at any stage
(its own Segment unchanged) or creates one; a ``blocked`` answer (a new Lead on a suppressed
domain) flags the item ``organization_suppressed`` (the job) or refuses (the reprocess), and
nothing else is written. Then ``research.ingest_signal_item(lead_id, SignalItemFacts(...))``
stores the Quote Evidence (``source=signal_inbox``, ``source_url`` the item URL,
``retrieved_at`` the submission time), the Signal when the type is one of the Lead's Segment's
inbox types (an Event dated by the stated date, else the submission date; first seen at the
submission time) and the author Contact when their role matches a decision-maker title. The
item becomes ``processed`` with ``processed_at`` (now) and ``lead_id``, and
``discovery.enqueue_enrich(lead_id, lane=interactive)`` enqueues ``enrich_lead``, whose chain
then stays on the interactive lane through ``score_lead`` (each stage enqueues the next on the
lane its job ran on; ``render_pages`` carries the lane in its payload).

Each status change appends one ``events`` row (entity ``signal_inbox_items``, the item's id,
the transition's statuses and cause, ``cause_ref`` the Lead's id when processed; actor
``system:discovery`` for the job, ``operator`` for a reprocess) and logs
``signal_item_processed`` (``item_id``, ``lead_id``, ``created``, ``evidence``, ``signal``,
``contact``) or ``signal_item_flagged`` (``item_id``, ``reason``).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.setup import SeededSegment
from nextron.app.research.api import Research, SignalItemFacts
from nextron.db import eventlog
from nextron.db.repos.discovery import signal_inbox as signal_inbox_repo
from nextron.db.repos.discovery.signal_inbox import ParseColumns, StoredItem
from nextron.db.uow import current, use_case
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import allow
from nextron.domain.fetch_policy import is_denied
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.domain.ids import is_ulid
from nextron.domain.job import Lane
from nextron.domain.leads import LeadIdentity, LeadSource, is_shared_platform
from nextron.domain.quotes import quote_in
from nextron.domain.segments import signal_catalogue
from nextron.domain.signal_inbox_item import (
    ACTOR,
    BUDGET_CLASS,
    ENTITY,
    INBOX_BLOCK,
    OPERATOR,
    ROUTE,
    SIGNAL_TYPES_BLOCK,
    SUBJECT_PREFIX,
    AcceptedParse,
    FlagReason,
    ItemEvent,
    ItemFacts,
    ItemStatus,
    SubmittedItem,
    accept_parse,
    lead_name,
    transition,
)
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmOk, LlmPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

SIGNAL_ITEMS_SUBMITTED: Final = "signal_items_submitted"
SIGNAL_ITEM_SKIPPED: Final = "signal_item_skipped"
SIGNAL_ITEM_PROCESSED: Final = "signal_item_processed"
SIGNAL_ITEM_FLAGGED: Final = "signal_item_flagged"
UNKNOWN_ITEM: Final = "unknown_item"
NOT_PENDING: Final = "not_pending"
LEADS_PATH: Final = "/leads/"


class ReprocessOutcome(StrEnum):
    """What a reprocess did: ``processed``, or why the item stayed as it was."""

    PROCESSED = "processed"
    UNKNOWN_ITEM = "unknown_item"
    NOT_FLAGGED = "not_flagged"
    INCOMPLETE = "incomplete"
    UNKNOWN_LEAD = "unknown_lead"
    WEBSITE_REFUSED = "website_refused"
    UNKNOWN_SEGMENT = "unknown_segment"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True, slots=True)
class ReprocessChoice:
    """Mac's resolution of a flagged item: an existing Lead (its id or Lead page link), or a
    website and a Segment key."""

    lead: str | None = None
    website: str | None = None
    segment: str | None = None


@dataclass(frozen=True, slots=True)
class _Call:
    """What phase 1 prepared for the call and phase 3."""

    item_id: str
    route_tuple: RouteTuple
    segments: tuple[SeededSegment, ...] = field(repr=False)
    blocks: tuple[UntrustedBlock, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Applied:
    """What applying an item did, for its log line."""

    lead_id: str
    created: bool
    evidence: bool
    signal: bool
    contact: bool


def lead_id_of(text: str) -> str | None:
    """The Lead id a reprocess's Lead field names: an id, or a Lead page link (``.../leads/<id>``,
    a query or fragment ignored); ``None`` for anything else."""
    value = text.strip()
    if LEADS_PATH in value:
        value = value.rsplit(LEADS_PATH, 1)[1]
        for separator in ("?", "#", "/"):
            value = value.split(separator, 1)[0]
    return value if is_ulid(value) else None


def _facts(item: StoredItem) -> ItemFacts:
    return ItemFacts(item.text, item.author, item.company)


def _blocks(item: StoredItem, segments: tuple[SeededSegment, ...]) -> tuple[UntrustedBlock, ...]:
    subject = f"{SUBJECT_PREFIX}{item.id}"
    body = item.text
    if item.author is not None:
        body += f"\nAuthor: {item.author}"
    if item.company is not None:
        body += f"\nCompany: {item.company}"
    catalogue = signal_catalogue([segment.definition for segment in segments])
    return (
        UntrustedBlock(subject, SIGNAL_TYPES_BLOCK, SIGNAL_TYPES_BLOCK, catalogue),
        UntrustedBlock(subject, INBOX_BLOCK, item.id, body),
    )


class SignalInbox:
    """The Signal Inbox's processing; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        llm: LlmPort,
        discovery: Discovery,
        research: Research,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._llm = llm
        self._discovery = discovery
        self._research = research
        self._queue = queue

    # submit -------------------------------------------------------------------------------------

    @use_case
    def submit(self, items: Sequence[SubmittedItem]) -> tuple[str, ...]:
        """Store the pasted items and enqueue their jobs in one transaction
        (``discovery.submit_signal_items``); their ids."""
        with current().transaction():
            ids = self._discovery.submit_signal_items(items)
        log.info(SIGNAL_ITEMS_SUBMITTED, extra={"items": len(ids)})
        return ids

    # process_signal_item ------------------------------------------------------------------------

    @use_case
    def process_signal_item(self, item_id: str, *, last_attempt: bool = False) -> bool:
        """Parse the pending item and apply it, or flag it; see the module docstring. ``False``
        when the budget refused the work. On the job's ``last_attempt`` a retryable adapter
        error flags the item ``parse_failed`` instead of being raised."""
        uow = current()
        now = self._clock.now()
        skipped: str | None = None
        call: _Call | None = None
        flagged = False
        with uow.transaction():
            if not allow(BUDGET_CLASS, self._meter.snapshot(now), setup_queries.budget_limits()):
                return False
            item = discovery_queries.signal_item(item_id)
            if item is None:
                skipped = UNKNOWN_ITEM
            elif item.status != ItemStatus.PENDING:
                skipped = NOT_PENDING
            else:
                route_tuple = playbook_queries.active_tuple(ROUTE)
                if route_tuple is None:
                    self._flag(item.id, FlagReason.PARSE_FAILED)
                    flagged = True
                else:
                    segments = setup_queries.segments()
                    call = _Call(item.id, route_tuple, segments, _blocks(item, segments))
        if skipped is not None:
            log.info(SIGNAL_ITEM_SKIPPED, extra={"item_id": item_id, "reason": skipped})
            return True
        if flagged or call is None:
            _log_flagged(item_id, FlagReason.PARSE_FAILED)
            return True
        try:
            result = self._llm.run(call.route_tuple, call.blocks, Urgency.SOON)
        except AdapterError as error:
            if error.retryable and not last_attempt:
                raise  # the runner retries the job; the item stays pending
            result = None
        with uow.transaction():
            outcome = self._apply_parse(call, result)
        if isinstance(outcome, _Applied):
            _log_processed(call.item_id, outcome)
        elif outcome is not None:
            _log_flagged(call.item_id, outcome)
        else:
            log.info(SIGNAL_ITEM_SKIPPED, extra={"item_id": item_id, "reason": NOT_PENDING})
        return True

    def _apply_parse(self, call: _Call, result: object) -> _Applied | FlagReason | None:
        """Phase 3, in the caller's transaction: what applying did, the flag it set, or
        ``None`` when the item is no longer pending."""
        item = signal_inbox_repo.get(call.item_id)
        if item is None or item.status != ItemStatus.PENDING:
            return None
        if not isinstance(result, LlmOk):
            return self._flag(item.id, FlagReason.PARSE_FAILED)
        try:
            accepted = accept_parse(
                result.parsed,
                _facts(item),
                [segment.definition for segment in call.segments],
                item.submitted_at,
            )
        except ValueError:
            return self._flag(item.id, FlagReason.PARSE_FAILED)
        parse = _columns(accepted, call.route_tuple.actor)
        if accepted.flag is not None:
            signal_inbox_repo.set_parse(item.id, parse)
            return self._flag(item.id, accepted.flag)
        if accepted.website is None or accepted.segment is None:  # flag above says so
            raise RuntimeError("an accepted parse without a website or Segment")
        segment_id = next(each.id for each in call.segments if each.key == accepted.segment)
        # the contactability gate (in upsert_lead) runs before the job writes anything
        applied = self._apply_website(item, accepted.website, segment_id, parse)
        if applied is None:
            return self._flag(item.id, FlagReason.ORGANIZATION_SUPPRESSED)
        self._processed(item, applied.lead_id, ItemEvent.PROCESS, ACTOR)
        return applied

    # reprocess ----------------------------------------------------------------------------------

    @use_case
    def reprocess(self, item_id: str, choice: ReprocessChoice) -> ReprocessOutcome:
        """Resolve a flagged item with Mac's choice; see the module docstring."""
        if not isinstance(choice, ReprocessChoice):
            raise TypeError(f"reprocess takes a ReprocessChoice, not {type(choice).__name__}")
        applied: _Applied | None = None
        with current().transaction():
            outcome, applied = self._reprocess(item_id, choice)
        if applied is not None:
            _log_processed(item_id, applied)
        return outcome

    def _reprocess(
        self, item_id: str, choice: ReprocessChoice
    ) -> tuple[ReprocessOutcome, _Applied | None]:
        item = discovery_queries.signal_item(item_id)
        if item is None:
            return ReprocessOutcome.UNKNOWN_ITEM, None
        if item.status != ItemStatus.FLAGGED:
            return ReprocessOutcome.NOT_FLAGGED, None
        lead_text = (choice.lead or "").strip()
        website = (choice.website or "").strip()
        segment_key = (choice.segment or "").strip()
        if lead_text and not website and not segment_key:
            lead_id = lead_id_of(lead_text)
            lead = None if lead_id is None else discovery_queries.survivor_of(lead_id)
            if lead is None:
                return ReprocessOutcome.UNKNOWN_LEAD, None
            applied = self._ingest(item, lead.id, created=False)
        elif website and segment_key and not lead_text:
            domain = _operator_domain(website)
            if domain is None:
                return ReprocessOutcome.WEBSITE_REFUSED, None
            segment = next(
                (each for each in setup_queries.segments() if each.key == segment_key), None
            )
            if segment is None:
                return ReprocessOutcome.UNKNOWN_SEGMENT, None
            found = self._apply_website(item, domain, segment.id, None)
            if found is None:
                return ReprocessOutcome.SUPPRESSED, None
            applied = found
        else:
            return ReprocessOutcome.INCOMPLETE, None
        self._processed(item, applied.lead_id, ItemEvent.REPROCESS, OPERATOR)
        return ReprocessOutcome.PROCESSED, applied

    # Applying -----------------------------------------------------------------------------------

    def _apply_website(
        self, item: StoredItem, domain: str, segment_id: str, parse: ParseColumns | None
    ) -> _Applied | None:
        """Upsert the Lead of ``domain``, then store ``parse`` on the item (the job's; ``None``
        for a reprocess, which applies the stored one) and ingest it; ``None`` when the gate
        blocked a new Lead (only ``parse`` was stored)."""
        upserted = self._discovery.upsert_lead(
            LeadIdentity(
                name=lead_name(item.company, domain), segment_id=segment_id, web_domain=domain
            ),
            LeadSource.SIGNAL_INBOX,
        )
        if parse is not None:
            signal_inbox_repo.set_parse(item.id, parse)
            stored = signal_inbox_repo.get(item.id)
            if stored is None:  # just updated
                raise RuntimeError(f"Signal Inbox item {item.id} has no row")
            item = stored
        if upserted.lead_id is None:
            return None
        return self._ingest(item, upserted.lead_id, created=upserted.created)

    def _ingest(self, item: StoredItem, lead_id: str, *, created: bool) -> _Applied:
        quote = item.parsed_quote
        if quote is not None and not quote_in(quote, item.text):  # an erased item's text
            quote = None
        ingested = self._research.ingest_signal_item(
            lead_id,
            SignalItemFacts(
                item_id=item.id,
                url=item.url,
                text=item.text,
                submitted_at=item.submitted_at,
                extracted_by=item.extracted_by or OPERATOR,
                signal_type=item.parsed_signal_type,
                event_date=item.parsed_event_date,
                quote=quote,
                author_name=item.parsed_author_name,
                author_role=item.parsed_author_role,
            ),
        )
        return _Applied(
            lead_id=lead_id,
            created=created,
            evidence=ingested.evidence_id is not None,
            signal=ingested.signal_id is not None,
            contact=ingested.contact_id is not None,
        )

    def _processed(self, item: StoredItem, lead_id: str, event: ItemEvent, actor: str) -> None:
        """Mark the item processed, append its ``events`` row and enqueue the interactive
        chain's ``enrich_lead``."""
        change = transition(ItemStatus(item.status), event)
        now = self._clock.now()
        signal_inbox_repo.set_status(
            item.id, str(change.to), flag_reason=None, lead_id=lead_id, processed_at=now
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=item.id,
            from_status=item.status,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=lead_id,
            actor=actor,
            at=now,
        )
        self._discovery.enqueue_enrich(lead_id, lane=Lane.INTERACTIVE)

    def _flag(self, item_id: str, reason: FlagReason) -> FlagReason:
        """Flag the pending item with ``reason`` and append its ``events`` row."""
        change = transition(ItemStatus.PENDING, ItemEvent.FLAG)
        now = self._clock.now()
        signal_inbox_repo.set_status(
            item_id, str(change.to), flag_reason=str(reason), lead_id=None, processed_at=None
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=item_id,
            from_status=str(ItemStatus.PENDING),
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=None,
            actor=ACTOR,
            at=now,
        )
        return reason


def _columns(accepted: AcceptedParse, actor: str) -> ParseColumns:
    return ParseColumns(
        segment=accepted.segment,
        signal_type=accepted.signal_type,
        website=accepted.website,
        author_name=accepted.author_name,
        author_role=accepted.author_role,
        event_date=accepted.event_date,
        quote=accepted.quote,
        extracted_by=actor,
    )


def _operator_domain(website: str) -> str | None:
    """The registrable domain of a website Mac typed, unless it is denied or shared."""
    try:
        domain = registrable_domain(website)
    except IdentityError:
        return None
    if is_denied(website) or is_denied(domain) or is_shared_platform(domain):
        return None
    return domain


def _log_processed(item_id: str, applied: _Applied) -> None:
    log.info(
        SIGNAL_ITEM_PROCESSED,
        extra={
            "item_id": item_id,
            "lead_id": applied.lead_id,
            "created": applied.created,
            "evidence": applied.evidence,
            "signal": applied.signal,
            "contact": applied.contact,
        },
    )


def _log_flagged(item_id: str, reason: FlagReason) -> None:
    log.info(SIGNAL_ITEM_FLAGGED, extra={"item_id": item_id, "reason": str(reason)})


__all__ = [
    "ReprocessChoice",
    "ReprocessOutcome",
    "SignalInbox",
    "lead_id_of",
]
