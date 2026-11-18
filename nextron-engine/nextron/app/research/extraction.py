"""Signals and Evidence from a Lead's crawled pages (FR-10, FR-11, FR-57, AD-3, AD-7, AD-8,
AD-10, AD-11, AD-18, NFR-24; Story 2.9): part of the ``research`` service, which is the sole
writer of Evidence and Signals. ``nextron.app.research.api``'s docstring points here.

The rules (what a proposal must pass, how new observations meet the Lead's earlier Evidence,
which zone is the headquarters) are pure and live in ``nextron.domain.extraction``; ages and
liveness in ``nextron.domain.signals``. This module reads and writes, and makes the one Claude
call. No log line, ``events`` row, error or job payload holds prompt text, page text, a quote
or a URL: IDs, codes and counts only.

**``SignalExtractions(clock, meter, llm, research, people)``** runs the ``extract_lead`` jobs
that ``WebsiteCrawls`` enqueues after a crawl stored pages. ``@use_case extract_lead(lead_id,
run_id) -> bool`` (``run_id`` is the crawl run that queued it) has three phases, then the people
step (below):

1. **First transaction.** When ``nextron.domain.budget.allow(BUDGET_CLASS,
   meter.snapshot(now), queries.setup.budget_limits())`` refuses, it returns ``False`` (the job
   is deferred) and nothing else happens. An unknown Lead (a text that isn't a ULID included)
   or a merged one logs ``extraction_skipped`` (``lead_id``, ``reason``) and returns ``True``.
   Otherwise it reads the Lead's Segment (``queries.setup.segment_by_id``) and
   ``queries.playbook.active_tuple(ROUTE)``. With no tuple it records a ``no_activation`` run.
   The pages are the Lead's pages with text whose ``changed_at`` is later than the
   ``started_at`` of the Lead's last ``ok`` ``extract_signals`` run (every page with text when
   there is none; Story 2.10's ``extract_people`` runs don't count); with none it records a
   ``no_pages`` run. The pages go in ``PageKind`` order, then by URL, each cut by
   ``allot(texts, PAGE_TEXT_BUDGET)``; the call's
   blocks are the ``signal_types`` block (``signal_catalogue([segment])``, block id and source
   ``signal_types``) and one ``website`` block per page (block id: the page's id), all of
   subject ``lead:<id>``. The full stored text of each page sent is kept for phase 3.
2. **The call**, with no transaction open: ``llm.run(tuple_, blocks, Urgency.BULK)``. An
   ``AdapterError`` records a ``failed`` run with its code (in a transaction of its own) and is
   raised again, so the runner retries only a retryable one. ``LlmRefused`` (``error_code``: its
   category), ``LlmTruncated``, ``LlmInvalid`` and ``LlmFailed`` (its code) record that outcome
   and store nothing.
3. **Apply transaction** after ``LlmOk``. A Lead merged meanwhile records a ``lead_merged`` run
   and nothing else. Otherwise:

   - ``validate(parsed, segment, pages, now)``; each refusal, and each dropped Event date,
     appends one ``events`` row (entity ``leads``, the Lead's id, ``None`` to the
     ``Discarded`` status, the ``DiscardCause``, ``cause_ref`` the cited page's id when it was
     sent, actor ``tuple_.actor``);
   - ``reconcile`` with the Lead's live Evidence in scope: ``quote`` rows with ``source``
     ``website`` whose ``extracted_by`` is an ``extract_signals`` actor and whose
     ``source_url`` is a page sent (its claim is its ``fact``, else the type of the first
     Signal it supports). A confirmed row's ``last_confirmed_at`` becomes its page's
     ``fetched_at`` (never earlier than it was); an inserted row (also the new row of a
     supersede) has ``type`` ``quote``, ``source`` ``website``, ``source_url`` and
     ``pages_checked`` ``[url]`` (the page's URL), ``retrieved_at`` and ``last_confirmed_at``
     the page's ``fetched_at``, ``extracted_by`` ``tuple_.actor``, its ``fact`` with ``value``
     or ``number``; a superseded row gets ``superseded_by``, a retracted one ``retracted_at``
     (now);
   - each new row of a Signal claim is linked to the Lead's current Signal of its identity: one
     per type for an Attribute; one per type and stated event date for an Event (undated Events
     share one). With none, a Signal is created with its class, Tier and ``score_only`` from the
     Segment, the date for an Event, and ``first_seen_at`` the earliest ``retrieved_at`` of the
     rows that create it;
   - ``EvidenceChanged(lead_id, evidence_id)`` is published for each Evidence superseded or
     retracted;
   - **the careers step (Story 2.11):** ``nextron.domain.postings.validate_postings`` reads the
     answer's ``job_postings`` against the pages sent and the careers pages among them; each
     refusal, and each dropped posted date, appends one ``events`` row (entity ``leads``, the
     Lead's id, ``None`` to ``job_posting_discarded`` or ``job_posting_date_discarded``, the
     cause, ``cause_ref`` the cited page's id when it was sent, actor ``tuple_.actor``), and the
     refusals count in the run's ``discarded``. Each careers page sent then goes through
     ``nextron.app.research.postings.apply_page`` (its kept postings stored with source
     ``careers_page``, checked at the page's ``fetched_at``; each other live posting of the page
     kept while its title is still in the page's text, closed otherwise). When a careers page
     was sent and the crawl run exists, its ``careers_postings`` (listed) and
     ``careers_events`` (created) are written;
   - every current Signal of the Lead left with no live Evidence is retracted, then
     ``EventClosed`` is published for each closed posting that had an Event;
   - the headquarters is recomputed from all the Lead's live ``location`` Evidence
     (``headquarters``); when the zone or its Evidence differs from what the Lead holds, both
     are written (``hq_tz`` and ``hq_evidence_id``, NULL together) and
     ``research.resolve_market`` runs for each of the Lead's Contacts;
   - the ``ok`` run is recorded with its counts (``stored``: Evidence rows inserted;
     ``discarded``: proposals refused).

   Then it logs ``signals_extracted`` with IDs and counts only. Every other recorded run logs
   ``extraction_finished`` (``lead_id``, ``run_id``, ``outcome``, ``error_code``).

**The people step (Story 2.10).** Whatever the Signals phases' outcome, except a budget
refusal, an unknown or merged Lead (phase 1's skip, or phase 3's ``lead_merged`` run) or a
raise, ``extract_lead`` then returns ``people.extract(lead_id, run_id)``
(``nextron.app.research.people.PeopleExtractions``, typed here by the ``PeopleStep`` protocol):
``False`` when its own budget check refused (the job is deferred), else ``True``.
``RecordedRun``, ``outcome_of`` and ``page_order`` serve both steps.

An extraction run records ``route`` (``ROUTE``), ``actor`` (the tuple's, ``None`` without one),
``crawl_run_id`` (``run_id`` when such a crawl run exists, else ``None``), ``pages_sent``,
``pages_truncated`` (pages cut by the budget), ``started_at`` (phase 1's now) and ``ended_at``.

**``LeadSignals(clock)``** serves the Lead page: ``view(lead_id) -> LeadSignalsView`` reads the
clock once and returns ``queries.research.lead_signals`` at that time, with each Signal type's
name from the Lead's Segment (an unknown Lead or Segment names none). It reads through the
current unit of work, so the dashboard's read-only one serves it.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Protocol

from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research import postings
from nextron.app.research.api import Research
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import profile as profile_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.crawl import StoredPage
from nextron.db.repos.research.signals import StoredEvidence
from nextron.db.uow import current, use_case
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import allow
from nextron.domain.crawl import PageKind
from nextron.domain.extraction import (
    BUDGET_CLASS,
    PAGE_TEXT_BUDGET,
    ROUTE,
    SIGNAL_TYPES_BLOCK,
    WEBSITE_BLOCK,
    Claim,
    EvidenceSource,
    ExtractionOutcome,
    FactKind,
    LiveEvidence,
    LocationQuote,
    Observation,
    Plan,
    SentPage,
    Validation,
    allot,
    headquarters,
    reconcile,
    validate,
)
from nextron.domain.ids import is_ulid
from nextron.domain.notifications import EvidenceChanged
from nextron.domain.postings import PostingSource, validate_postings
from nextron.domain.segments import EvidenceType, SegmentDef, SignalClass, signal_catalogue
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmFailed, LlmOk, LlmPort, LlmRefused, LlmResult, LlmTruncated
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

ENTITY: Final = "leads"  # the events rows of a refusal are about the Lead
EXTRACTION_SKIPPED: Final = "extraction_skipped"
EXTRACTION_FINISHED: Final = "extraction_finished"
SIGNALS_EXTRACTED: Final = "signals_extracted"
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
KIND_ORDER: Final = {str(kind): index for index, kind in enumerate(PageKind)}


class PeopleStep(Protocol):
    """What ``extract_lead`` needs of the people step (Story 2.10)."""

    def extract(self, lead_id: str, run_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class _Call:
    """What phase 1 prepared for the call and phase 3."""

    lead_id: str
    crawl_run_id: str | None
    started_at: datetime
    route_tuple: RouteTuple
    segment: SegmentDef
    pages: Mapping[str, SentPage] = field(repr=False)
    blocks: tuple[UntrustedBlock, ...] = field(repr=False)
    pages_truncated: int
    careers_ids: tuple[str, ...] = ()  # the careers pages sent, in send order (Story 2.11)


@dataclass(frozen=True, slots=True)
class RecordedRun:
    """A run recorded without storing anything, to log once its transaction commits."""

    lead_id: str
    run_id: str
    outcome: ExtractionOutcome
    error_code: str | None

    def log(self) -> None:
        log.info(
            EXTRACTION_FINISHED,
            extra={
                "lead_id": self.lead_id,
                "run_id": self.run_id,
                "outcome": str(self.outcome),
                "error_code": self.error_code,
            },
        )


@dataclass(slots=True)
class _Applied:
    """What phase 3 did, for its run and its log line."""

    run_id: str = ""
    merged: RecordedRun | None = None
    stored: int = 0
    confirmed: int = 0
    superseded: int = 0
    retracted: int = 0
    discarded: int = 0
    dates_dropped: int = 0
    signals_created: int = 0
    signals_retracted: int = 0
    hq_changed: bool = False


def page_order(page: StoredPage) -> tuple[int, str]:
    return KIND_ORDER.get(page.kind, len(KIND_ORDER)), page.url


def outcome_of(result: LlmResult) -> tuple[ExtractionOutcome, str | None]:
    """The run outcome and error code of a result other than ``LlmOk``."""
    if isinstance(result, LlmRefused):
        return ExtractionOutcome.REFUSED, result.category
    if isinstance(result, LlmTruncated):
        return ExtractionOutcome.TRUNCATED, None
    if isinstance(result, LlmFailed):
        return ExtractionOutcome.FAILED, result.code
    return ExtractionOutcome.INVALID, None


class SignalExtractions:
    """The ``extract_lead`` jobs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        llm: LlmPort,
        research: Research,
        people: PeopleStep,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._llm = llm
        self._research = research
        self._people = people

    @use_case
    def extract_lead(self, lead_id: str, run_id: str) -> bool:
        """Extract the Lead's Signals and Evidence from its changed pages, then its people; see
        the module docstring. ``False`` when a budget check refused the work."""
        uow = current()
        started_at = self._clock.now()
        skipped: str | None = None
        call: _Call | RecordedRun | None = None
        with uow.transaction():
            limits = setup_queries.budget_limits()
            if not allow(BUDGET_CLASS, self._meter.snapshot(started_at), limits):
                return False
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                skipped = UNKNOWN_LEAD
            elif lead.merged_into is not None:
                skipped = MERGED_LEAD
            else:
                call = self._prepare(lead.id, lead.segment_id, run_id, started_at)
        if call is None:  # an unknown or merged Lead
            log.info(EXTRACTION_SKIPPED, extra={"lead_id": lead_id, "reason": skipped})
            return True
        if isinstance(call, RecordedRun):  # phase 1 recorded its run
            call.log()
            return self._people.extract(lead_id, run_id)
        try:
            result = self._llm.run(call.route_tuple, call.blocks, Urgency.BULK)
        except AdapterError as error:
            with uow.transaction():
                recorded = self._finished(call, ExtractionOutcome.FAILED, error.code)
            recorded.log()
            raise  # the runner retries only a retryable one
        if not isinstance(result, LlmOk):
            outcome, code = outcome_of(result)
            with uow.transaction():
                recorded = self._finished(call, outcome, code)
            recorded.log()
            return self._people.extract(lead_id, run_id)
        with uow.transaction():
            applied = self._apply(call, result.parsed)
        if applied.merged is not None:
            applied.merged.log()
            return True  # a merged Lead's people are its survivor's
        log.info(
            SIGNALS_EXTRACTED,
            extra={
                "lead_id": call.lead_id,
                "run_id": applied.run_id,
                "pages_sent": len(call.pages),
                "pages_truncated": call.pages_truncated,
                "stored": applied.stored,
                "confirmed": applied.confirmed,
                "superseded": applied.superseded,
                "retracted": applied.retracted,
                "discarded": applied.discarded,
                "dates_dropped": applied.dates_dropped,
                "signals_created": applied.signals_created,
                "signals_retracted": applied.signals_retracted,
                "hq_changed": applied.hq_changed,
            },
        )
        return self._people.extract(lead_id, run_id)

    # Phase 1 ------------------------------------------------------------------------------------

    def _prepare(
        self, lead_id: str, segment_id: str, run_id: str, started_at: datetime
    ) -> _Call | RecordedRun:
        """The call to make, or the ``no_activation`` or ``no_pages`` run it recorded."""
        crawl_run_id = run_id if is_ulid(run_id) and crawl_repo.run(run_id) is not None else None
        seeded = setup_queries.segment_by_id(segment_id)
        if seeded is None:  # leads.segment_id is a foreign key
            raise RuntimeError(f"Lead {lead_id}'s Segment has no row")
        route_tuple = playbook_queries.active_tuple(ROUTE)
        if route_tuple is None:
            outcome = ExtractionOutcome.NO_ACTIVATION
            run = self._record(lead_id, crawl_run_id, outcome, actor=None, started_at=started_at)
            return RecordedRun(lead_id, run, outcome, None)
        last = signals_repo.last_ok_run(lead_id, ROUTE)
        pages = sorted(
            (
                page
                for page in crawl_repo.pages(lead_id)
                if page.text and (last is None or page.changed_at > last.started_at)
            ),
            key=page_order,
        )
        if not pages:
            outcome = ExtractionOutcome.NO_PAGES
            run = self._record(
                lead_id, crawl_run_id, outcome, actor=route_tuple.actor, started_at=started_at
            )
            return RecordedRun(lead_id, run, outcome, None)
        full = [page.text or "" for page in pages]
        cut = allot(full, PAGE_TEXT_BUDGET)
        subject = f"lead:{lead_id}"
        blocks = (
            UntrustedBlock(
                subject,
                SIGNAL_TYPES_BLOCK,
                SIGNAL_TYPES_BLOCK,
                signal_catalogue([seeded.definition]),
            ),
            *(
                UntrustedBlock(subject, WEBSITE_BLOCK, page.id, text)
                for page, text in zip(pages, cut, strict=True)
            ),
        )
        return _Call(
            lead_id=lead_id,
            crawl_run_id=crawl_run_id,
            started_at=started_at,
            route_tuple=route_tuple,
            segment=seeded.definition,
            pages={
                page.id: SentPage(page.url, page.fetched_at, text)
                for page, text in zip(pages, full, strict=True)
            },
            blocks=blocks,
            pages_truncated=sum(
                len(short) < len(text) for short, text in zip(cut, full, strict=True)
            ),
            careers_ids=tuple(page.id for page in pages if page.kind == PageKind.CAREERS),
        )

    # Runs ---------------------------------------------------------------------------------------

    def _record(
        self,
        lead_id: str,
        crawl_run_id: str | None,
        outcome: ExtractionOutcome,
        *,
        actor: str | None,
        started_at: datetime,
        error_code: str | None = None,
        pages_sent: int = 0,
        pages_truncated: int = 0,
        stored: int = 0,
        discarded: int = 0,
    ) -> str:
        return signals_repo.insert_run(
            lead_id=lead_id,
            crawl_run_id=crawl_run_id,
            route=ROUTE,
            actor=actor,
            outcome=str(outcome),
            error_code=error_code,
            pages_sent=pages_sent,
            pages_truncated=pages_truncated,
            stored=stored,
            discarded=discarded,
            started_at=started_at,
            ended_at=self._clock.now(),
        )

    def _finished(
        self, call: _Call, outcome: ExtractionOutcome, code: str | None
    ) -> RecordedRun:
        """Record a run that stored nothing after the call."""
        run_id = self._record(
            call.lead_id,
            call.crawl_run_id,
            outcome,
            actor=call.route_tuple.actor,
            started_at=call.started_at,
            error_code=code,
            pages_sent=len(call.pages),
            pages_truncated=call.pages_truncated,
        )
        return RecordedRun(call.lead_id, run_id, outcome, code)

    # Phase 3 ------------------------------------------------------------------------------------

    def _apply(self, call: _Call, parsed: Mapping[str, Any]) -> _Applied:
        """Phase 3, in the caller's transaction."""
        now = self._clock.now()
        applied = _Applied()
        lead = discovery_queries.lead(call.lead_id)
        if lead is None:  # Leads are never deleted
            raise RuntimeError(f"Lead {call.lead_id} has no row")
        if lead.merged_into is not None:
            applied.merged = self._finished(call, ExtractionOutcome.LEAD_MERGED, None)
            return applied
        validation = validate(parsed, call.segment, call.pages, now)
        self._log_refusals(call, validation, now)
        applied.discarded = len(validation.discards)
        applied.dates_dropped = len(validation.dropped_dates)
        live_rows = signals_repo.evidence(call.lead_id, live_only=True)
        live = self._in_scope(call, live_rows)
        texts = {page.url: page.text for page in call.pages.values()}
        plan = reconcile(live, validation.observations, texts)
        changed = self._carry_out(call, plan, live, live_rows, now, applied)
        for evidence_id in changed:
            current().publish(EvidenceChanged(lead_id=call.lead_id, evidence_id=evidence_id))
        tally = self._careers(call, parsed, now, applied)
        applied.signals_retracted = len(signals_repo.retract_unsupported(call.lead_id, now))
        postings.publish_closed(call.lead_id, tally)
        applied.hq_changed = self._headquarters(call.lead_id)
        applied.run_id = self._record(
            call.lead_id,
            call.crawl_run_id,
            ExtractionOutcome.OK,
            actor=call.route_tuple.actor,
            started_at=call.started_at,
            pages_sent=len(call.pages),
            pages_truncated=call.pages_truncated,
            stored=applied.stored,
            discarded=applied.discarded,
        )
        return applied

    def _log_refusals(self, call: _Call, validation: Validation, now: datetime) -> None:
        for discard in (*validation.discards, *validation.dropped_dates):
            eventlog.append(
                entity=ENTITY,
                entity_id=call.lead_id,
                from_status=None,
                to_status=str(discard.status),
                cause=str(discard.cause),
                cause_ref=discard.page_id,
                actor=call.route_tuple.actor,
                at=now,
            )

    @staticmethod
    def _careers(
        call: _Call, parsed: Mapping[str, Any], now: datetime, applied: _Applied
    ) -> postings.Tally:
        """Story 2.11's careers step: validate the answer's job postings, store those of each
        careers page sent and apply the page rule; see the module docstring."""
        validation = validate_postings(parsed, call.pages, call.careers_ids, now)
        for discard in (*validation.discards, *validation.dropped_dates):
            eventlog.append(
                entity=ENTITY,
                entity_id=call.lead_id,
                from_status=None,
                to_status=str(discard.status),
                cause=str(discard.cause),
                cause_ref=discard.page_id,
                actor=call.route_tuple.actor,
                at=now,
            )
        applied.discarded += len(validation.discards)
        tally = postings.Tally()
        for page_id in call.careers_ids:
            page = call.pages[page_id]
            kept = [posting for posting in validation.postings if posting.page_id == page_id]
            postings.apply_page(
                call.lead_id, call.segment, page.url, page.text, kept, page.fetched_at, now, tally
            )
        if call.careers_ids and call.crawl_run_id is not None:
            counts = postings.tally_counts(tally, (PostingSource.CAREERS_PAGE,))
            crawl_repo.set_posting_counts(call.crawl_run_id, counts)
        return tally

    @staticmethod
    def _in_scope(call: _Call, rows: Sequence[StoredEvidence]) -> list[LiveEvidence]:
        """The live rows reconciliation may touch: this route's website quotes of the pages
        sent, each with its claim."""
        urls = {page.url for page in call.pages.values()}
        first_type: dict[str, str] = {}
        signal_types = {
            signal.id: signal.signal_type
            for signal in signals_repo.signals(call.lead_id, current_only=False)
        }
        for link in signals_repo.links(call.lead_id):
            if link.signal_id in signal_types:
                first_type.setdefault(link.evidence_id, signal_types[link.signal_id])
        actor_prefix = f"ai:{ROUTE}@"
        live: list[LiveEvidence] = []
        for row in rows:
            if (
                row.type != EvidenceType.QUOTE
                or row.source != EvidenceSource.WEBSITE
                or not row.extracted_by.startswith(actor_prefix)
                or row.quote is None
                or row.source_url not in urls
            ):
                continue
            if row.fact is not None:
                claim = Claim(fact=FactKind(row.fact))
            elif row.id in first_type:
                claim = Claim(signal_type=first_type[row.id])
            else:
                continue  # supports nothing: nothing to reconcile it with
            live.append(LiveEvidence(row.id, claim, row.source_url, row.quote, row.created_at))
        return live

    def _carry_out(
        self,
        call: _Call,
        plan: Plan,
        live: Sequence[LiveEvidence],
        live_rows: Sequence[StoredEvidence],
        now: datetime,
        applied: _Applied,
    ) -> list[str]:
        """Write the plan; the ids of the Evidence superseded or retracted."""
        fetched = {page.url: page.fetched_at for page in call.pages.values()}
        url_of = {row.id: row.url for row in live}
        confirmed_at = {row.id: row.last_confirmed_at for row in live_rows}
        for evidence_id in plan.confirm:
            at = max(confirmed_at[evidence_id], fetched[url_of[evidence_id]])
            signals_repo.confirm_evidence(evidence_id, at)
        applied.confirmed = len(plan.confirm)
        new_rows: list[tuple[str, Observation]] = []
        for observation in plan.insert:
            new_rows.append((self._insert(call, observation, now), observation))
        changed: list[str] = []
        for superseding in plan.supersede:
            new_id = self._insert(call, superseding.new, now)
            signals_repo.supersede_evidence(superseding.old_id, new_id)
            new_rows.append((new_id, superseding.new))
            changed.append(superseding.old_id)
        for evidence_id in plan.retract:
            signals_repo.retract_evidence(evidence_id, now)
            changed.append(evidence_id)
        applied.stored = len(new_rows)
        applied.superseded = len(plan.supersede)
        applied.retracted = len(plan.retract)
        applied.signals_created = self._link(call, new_rows)
        return changed

    def _insert(self, call: _Call, observation: Observation, now: datetime) -> str:
        page = call.pages[observation.page_id]
        fact = observation.claim.fact
        return signals_repo.insert_evidence(
            lead_id=call.lead_id,
            evidence_type=str(EvidenceType.QUOTE),
            quote=observation.quote,
            sought=None,
            number=observation.number,
            fact=None if fact is None else str(fact),
            value=observation.value,
            pages_checked=(page.url,),
            source=str(EvidenceSource.WEBSITE),
            source_url=page.url,
            retrieved_at=page.fetched_at,
            extracted_by=call.route_tuple.actor,
            person_contact_id=None,
            at=now,
        )

    def _link(self, call: _Call, new_rows: Sequence[tuple[str, Observation]]) -> int:
        """Link each new row of a Signal claim to its Signal, creating the Signals that don't
        exist; how many were created."""
        types = {signal_type.key: signal_type for signal_type in call.segment.signal_types}
        existing = signals_repo.signals(call.lead_id, current_only=True)
        creating: dict[tuple[str, date | None], list[tuple[str, datetime]]] = {}
        for evidence_id, observation in new_rows:
            key = observation.claim.signal_type
            if key is None:
                continue  # a profile fact or size clue supports no Signal
            signal_type = types[key]
            is_event = signal_type.signal_class is SignalClass.EVENT
            event_date = observation.event_date if is_event else None
            found = next(
                (
                    signal
                    for signal in existing
                    if signal.signal_type == key
                    and (not is_event or signal.event_date == event_date)
                ),
                None,
            )
            if found is not None:
                signals_repo.link(found.id, evidence_id)
                continue
            retrieved_at = call.pages[observation.page_id].fetched_at
            creating.setdefault((key, event_date), []).append((evidence_id, retrieved_at))
        for (key, event_date), rows in creating.items():
            signal_type = types[key]
            signals_repo.insert_signal(
                lead_id=call.lead_id,
                signal_type=key,
                signal_class=str(signal_type.signal_class),
                tier=signal_type.tier,
                score_only=signal_type.score_only,
                event_date=event_date,
                first_seen_at=min(retrieved_at for _, retrieved_at in rows),
                evidence_ids=[evidence_id for evidence_id, _ in rows],
            )
        return len(creating)

    def _headquarters(self, lead_id: str) -> bool:
        """Recompute the Lead's headquarters; whether it changed (its Contacts were then
        resolved again)."""
        locations = [
            LocationQuote(row.id, row.quote)
            for row in signals_repo.evidence(lead_id, live_only=True)
            if row.fact == FactKind.LOCATION and row.quote is not None
        ]
        found = headquarters(locations)
        wanted = (None, None) if found is None else (found.zone, found.evidence_id)
        stored = profile_repo.headquarters(lead_id)
        held = (None, None) if stored is None else (stored.hq_tz, stored.hq_evidence_id)
        if held == wanted:
            return False
        profile_repo.set_headquarters(lead_id, *wanted)
        for contact in contacts_repo.for_lead(lead_id):
            self._research.resolve_market(contact.id)
        return True


class LeadSignals:
    """The Lead page's Signals and Evidence; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def view(self, lead_id: str) -> research_queries.LeadSignalsView:
        """The Lead's Signals, Evidence and headquarters zone, now."""
        now = self._clock.now()
        names: dict[str, str] = {}
        lead = discovery_queries.lead(lead_id)
        seeded = None if lead is None else setup_queries.segment_by_id(lead.segment_id)
        if seeded is not None:
            names = {each.key: each.name for each in seeded.definition.signal_types}
        return research_queries.lead_signals(lead_id, now, names)
