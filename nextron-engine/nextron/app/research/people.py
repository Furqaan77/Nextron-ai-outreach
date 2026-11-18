"""Named decision-makers and the Primary Contact (FR-10, FR-13, FR-51, AD-3, AD-7, AD-10, AD-17,
AD-18; Story 2.10): part of the ``research`` service, the sole writer of Contacts, Evidence and
Provenance. ``nextron.app.research.api``'s docstring points here.

The rules (which people a page really names, a person's office, the lawyer count, how new quotes
meet the Lead's earlier person Evidence) are pure and live in ``nextron.domain.people``; the
roles and the Primary Contact's choice in ``nextron.domain.contacts``. This module reads and
writes, and makes the one Claude call. No log line, ``events`` row, error or job payload holds a
name, a role, a quote, a URL or a place: IDs, codes and counts only.

**``PeopleExtractions(clock, meter, llm, research, queue)``** is the people step of the
``extract_lead`` job: ``SignalExtractions.extract_lead`` calls ``extract(lead_id, run_id) ->
bool`` after its Signals phases. It isn't a use case: it runs inside ``extract_lead``'s unit of
work and opens its own transactions, in three phases:

1. **Prepare** (a transaction). When ``nextron.domain.budget.allow(BUDGET_CLASS,
   meter.snapshot(now), queries.setup.budget_limits())`` refuses, it returns ``False`` (the
   whole job is deferred) and nothing else happens. An unknown Lead (a text that isn't a ULID
   included) or a merged one logs ``extraction_skipped`` (``lead_id``, ``reason``), enqueues
   nothing and returns ``True``. Otherwise it reads the Lead's Segment and
   ``queries.playbook.active_tuple(ROUTE)``; with no tuple it records a ``no_activation`` run.
   The pages are the Lead's ``team``, ``about`` and ``contact`` pages with text: when any of
   them has a ``changed_at`` later than the ``started_at`` of the Lead's last ``ok``
   ``extract_people`` run (or there is none), every one of them is sent; otherwise it records a
   ``no_pages`` run. They go in ``PageKind`` order, then by URL, each cut by
   ``allot(texts, PAGE_TEXT_BUDGET)``, as one ``website`` block each (block id: the page's id,
   subject ``lead:<id>``). The full stored text of each page sent is kept for phase 3.
2. **The call**, with no transaction open: ``llm.run(tuple_, blocks, Urgency.BULK)``. An
   ``AdapterError`` records a ``failed`` run with its code (in a transaction of its own) and is
   raised again (the runner retries only a retryable one). ``LlmRefused`` (``error_code``: its
   category), ``LlmTruncated``, ``LlmInvalid`` and ``LlmFailed`` (its code) record that outcome
   and store nothing.
3. **Apply** (a transaction) after ``LlmOk``. A Lead merged meanwhile records a ``lead_merged``
   run and nothing else. Otherwise:

   - ``validate_people(parsed, pages)``; each refusal appends one ``events`` row (entity
     ``leads``, the Lead's id, ``None`` to ``person_discarded``, the ``PersonDiscardCause``,
     ``cause_ref`` the cited page's id when it was sent, actor ``tuple_.actor``);
   - each kept person who is a ``decision_maker`` in the Lead's Segment (a ``role_match``, and
     a quote that doesn't qualify the role away: "Assistant to the Managing Partner") becomes a
     Contact
     through ``research.add_contact(lead_id, ContactCandidate(name, role, office), source=
     website, detail=<the page's URL>, retrieved_at=<the page's fetched_at>)``, the office
     being ``office_from_quote(quote, <the Lead's country>, <the stored Markets>)``; a person in
     any other role is never stored (their name stays only in page text, purged after 30 days);
   - a decision-maker the contactability gate blocks (``add_contact`` returns ``contact_id``
     ``None``: a suppressed address, domain, person or profile, or a Lost or Disqualified Lead;
     Story 3.4) is skipped entirely: no Contact, no person Evidence and no quote for
     reconciliation (so no row with a NULL ``person_contact_id``). They are counted as
     ``contacts_blocked``;
   - the person Evidence is reconciled (``reconcile_people``) with the Lead's live ``quote``
     rows that have a ``person_contact_id``, an ``ai:extract_people@`` actor (any prompt
     version) and a ``source_url`` among the pages sent. A confirmed row's
     ``last_confirmed_at`` becomes its page's ``fetched_at`` (never earlier than it was); a new
     row has ``type`` ``quote``, ``source`` ``website``, ``source_url`` and ``pages_checked``
     ``[url]``, ``retrieved_at`` and ``last_confirmed_at`` the page's ``fetched_at``,
     ``extracted_by`` ``tuple_.actor`` and ``person_contact_id`` the Contact; a superseded row
     gets ``superseded_by``, a retracted one ``retracted_at`` (now);
   - a Law Lead's lawyer count (Decision 6): only when the Segment's ``size_range.unit`` is
     ``lawyers``, a ``team`` page was sent, no page sent was cut by the page budget (a cut page
     may hide lawyers) and ``lawyer_count(people)`` is at least 1. The row is ``type``
     ``count``, ``sought`` ``LAWYERS_LISTED``, ``number`` the count, ``pages_checked`` the URLs
     sent (in send order), ``source`` ``website``, ``source_url`` NULL, ``retrieved_at`` the
     latest ``fetched_at`` sent, ``extracted_by`` ``rule`` and no ``fact``. When a live
     ``rule`` count of ``LAWYERS_LISTED`` has the same number and the same pages, it is
     confirmed and every other live one is retracted; otherwise the new row is inserted and
     supersedes every live one (a merge can bring a second). No count leaves them alone;
   - ``EvidenceChanged(lead_id, evidence_id)`` is published for each Evidence superseded, then
     each retracted (the counts' included, and counted in the run's log line);
   - the ``ok`` run is recorded (``stored``: Evidence rows inserted, the count's included;
     ``discarded``: refusals).

   Then it logs ``people_extracted`` (``lead_id``, ``run_id``, ``pages_sent``,
   ``pages_truncated``, ``people`` (kept), ``contacts_created``, ``contacts_blocked``,
   ``not_decision_makers``,
   ``stored``, ``confirmed``, ``superseded``, ``retracted``, ``discarded`` and ``lawyers``: the
   lawyer count stored or confirmed, else 0). Every other recorded run logs
   ``extraction_finished`` (``lead_id``, ``run_id``, ``outcome``, ``error_code``), as Story 2.9's
   do.

Whenever it records a run (any outcome but the raise), the same transaction enqueues the
chain's next stage through ``_enqueue_next_stage``, the one insertion point for the stages
between the people step and the Primary Contact's selection: ``check_job_boards`` (Story 2.11,
``nextron.app.research.job_boards``), whose finish enqueues ``check_legal_form`` (Story 2.12,
``nextron.app.discovery.legal_form``), which enqueues ``select_primary_contact``, so the
selection sees the hiring Events and the legal form. Scope the Lead, discriminator ``tick``
(a live job absorbs a repeat), payload ``{v, lead_id, run_id}`` (``run_id`` the crawl run
``extract`` was given) and, when the Lead has a ``web_domain``, the concurrency key
``domain:<web_domain>``. A people run is an ``extraction_runs`` row with ``route``
``extract_people``, recorded like Story 2.9's (``actor``, ``crawl_run_id``, ``pages_sent``,
``pages_truncated``, ``started_at`` phase 1's now, ``ended_at``).

**``PrimaryContacts(clock, research, queue)``** runs the Primary Contact's selection. Whenever
the Primary it leaves has no usable address (an email whose class isn't ``invalid``), the same
transaction enqueues its ``find_contact_email`` job (Story 2.13,
``nextron.app.research.finding.enqueue_search``): on the job's default (bulk) lane after a
selection, on the interactive lane after "Make primary". When its address is ``guessed`` and the
verifier hasn't answered for it, the same transaction enqueues its ``verify_contact_email``
job instead (Story 2.14, ``nextron.app.research.verification.enqueue_verify``), on the same
lane; like the search, it is a job beside the chain, not a stage of it.

- ``@use_case select_primary_contact(lead_id) -> PrimaryChoice | None`` (the
  ``select_primary_contact`` job): in one transaction, ``research.select_primary(lead_id)``. An
  unknown or merged Lead logs ``primary_selection_skipped`` (``lead_id``, ``reason``) and
  returns ``None``; otherwise it logs ``primary_contact_selected`` (``lead_id``,
  ``contact_id``: ``None`` for no decision-maker, ``set_by``). When a selection ran, the same
  transaction enqueues ``score_lead`` (Story 2.15, ``nextron.domain.scoring.SCORE_LEAD_JOB``,
  the end of the Lead's research chain): scope the Lead, discriminator ``tick``, payload ``{v,
  lead_id}``. The Primary's ``find_contact_email`` (Story 2.13), enqueued in the same
  transaction when it has no usable address, is a job on the Contact beside the chain, not a
  stage of it: the score reads nothing it writes, and a quota can defer it for days.
- ``@use_case make_primary(lead_id, contact_id)`` (the Lead page's "Make primary"): in one
  transaction, ``research.make_primary(lead_id, contact_id)``; a ``PrimaryRefused`` leaves the
  transaction with nothing written and reaches the caller. It logs ``primary_contact_made``
  (``lead_id``, ``contact_id``).
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.discovery import StoredLead
from nextron.app.research.api import Research
from nextron.app.research.extraction import RecordedRun, outcome_of, page_order
from nextron.app.research.finding import enqueue_search, has_usable_address
from nextron.app.research.verification import enqueue_verify, needs_verifier
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.signals import StoredEvidence
from nextron.db.uow import current, use_case
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import allow
from nextron.domain.contacts import ContactCandidate, PrimaryChoice, ProvenanceSource
from nextron.domain.crawl import (
    DOMAIN_KEY_PREFIX,
    LEAD_ID_FIELD,
    PAYLOAD_VERSION,
    RUN_ID_FIELD,
    PageKind,
)
from nextron.domain.extraction import (
    EXTRACTED_BY_RULE,
    PAGE_TEXT_BUDGET,
    WEBSITE_BLOCK,
    EvidenceSource,
    ExtractionOutcome,
    SentPage,
    allot,
)
from nextron.domain.ids import is_ulid
from nextron.domain.job import TICK, VERSION_FIELD, Lane
from nextron.domain.scoring import SCORE_LEAD_JOB
from nextron.domain.notifications import EvidenceChanged
from nextron.domain.people import (
    BUDGET_CLASS,
    LAWYERS_LISTED,
    PEOPLE_PAGE_KINDS,
    PERSON_DISCARDED,
    ROUTE,
    LivePersonEvidence,
    PeoplePlan,
    PeopleValidation,
    PersonQuote,
    decision_maker,
    lawyer_count,
    office_from_quote,
    reconcile_people,
    validate_people,
)
from nextron.domain.postings import CHECK_JOB_BOARDS_JOB
from nextron.domain.segments import EvidenceType, SegmentDef, SizeUnit
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmOk, LlmPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

ENTITY: Final = "leads"  # the events rows of a refusal are about the Lead
EXTRACTION_SKIPPED: Final = "extraction_skipped"
PEOPLE_EXTRACTED: Final = "people_extracted"
PRIMARY_SELECTION_SKIPPED: Final = "primary_selection_skipped"
PRIMARY_CONTACT_SELECTED: Final = "primary_contact_selected"
PRIMARY_CONTACT_MADE: Final = "primary_contact_made"
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
PEOPLE_KINDS: Final = frozenset(str(kind) for kind in PEOPLE_PAGE_KINDS)
ACTOR_PREFIX: Final = f"ai:{ROUTE}@"


@dataclass(frozen=True, slots=True)
class _PeopleCall:
    """What phase 1 prepared for the call and phase 3."""

    lead_id: str
    web_domain: str | None
    run_id: str  # the payload's crawl run, passed down the chain (Story 2.11)
    crawl_run_id: str | None
    started_at: datetime
    route_tuple: RouteTuple
    segment: SegmentDef
    pages: Mapping[str, SentPage] = field(repr=False)  # in send order
    team_sent: bool
    blocks: tuple[UntrustedBlock, ...] = field(repr=False)
    pages_truncated: int


@dataclass(slots=True)
class _Applied:
    """What phase 3 did, for its run and its log line."""

    run_id: str = ""
    merged: RecordedRun | None = None
    people: int = 0
    contacts_created: int = 0
    contacts_blocked: int = 0
    not_decision_makers: int = 0
    stored: int = 0
    confirmed: int = 0
    superseded: int = 0
    retracted: int = 0
    discarded: int = 0
    lawyers: int = 0


class PeopleExtractions:
    """The people step of the ``extract_lead`` jobs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        llm: LlmPort,
        research: Research,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._llm = llm
        self._research = research
        self._queue = queue

    def extract(self, lead_id: str, run_id: str) -> bool:
        """Extract the Lead's named decision-makers from its people pages; see the module
        docstring. ``False`` when the budget refused the call."""
        uow = current()
        started_at = self._clock.now()
        skipped: str | None = None
        call: _PeopleCall | RecordedRun | None = None
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
                call = self._prepare(lead, run_id, started_at)
        if call is None:  # an unknown or merged Lead
            log.info(EXTRACTION_SKIPPED, extra={"lead_id": lead_id, "reason": skipped})
            return True
        if isinstance(call, RecordedRun):  # phase 1 recorded its run
            call.log()
            return True
        try:
            result = self._llm.run(call.route_tuple, call.blocks, Urgency.BULK)
        except AdapterError as error:
            with uow.transaction():
                recorded = self._finished(call, ExtractionOutcome.FAILED, error.code, select=False)
            recorded.log()
            raise  # the runner retries only a retryable one
        if not isinstance(result, LlmOk):
            outcome, code = outcome_of(result)
            with uow.transaction():
                recorded = self._finished(call, outcome, code)
            recorded.log()
            return True
        with uow.transaction():
            applied = self._apply(call, result.parsed)
        if applied.merged is not None:
            applied.merged.log()
            return True
        log.info(
            PEOPLE_EXTRACTED,
            extra={
                "lead_id": call.lead_id,
                "run_id": applied.run_id,
                "pages_sent": len(call.pages),
                "pages_truncated": call.pages_truncated,
                "people": applied.people,
                "contacts_created": applied.contacts_created,
                "contacts_blocked": applied.contacts_blocked,
                "not_decision_makers": applied.not_decision_makers,
                "stored": applied.stored,
                "confirmed": applied.confirmed,
                "superseded": applied.superseded,
                "retracted": applied.retracted,
                "discarded": applied.discarded,
                "lawyers": applied.lawyers,
            },
        )
        return True

    # Phase 1 ------------------------------------------------------------------------------------

    def _prepare(
        self, lead: StoredLead, run_id: str, started_at: datetime
    ) -> _PeopleCall | RecordedRun:
        """The call to make, or the ``no_activation`` or ``no_pages`` run it recorded."""
        crawl_run_id = run_id if is_ulid(run_id) and crawl_repo.run(run_id) is not None else None
        seeded = setup_queries.segment_by_id(lead.segment_id)
        if seeded is None:  # leads.segment_id is a foreign key
            raise RuntimeError(f"Lead {lead.id}'s Segment has no row")
        route_tuple = playbook_queries.active_tuple(ROUTE)
        if route_tuple is None:
            return self._recorded(
                lead, run_id, crawl_run_id, ExtractionOutcome.NO_ACTIVATION, None, started_at
            )
        last = signals_repo.last_ok_run(lead.id, ROUTE)
        pages = sorted(
            (page for page in crawl_repo.pages(lead.id) if page.kind in PEOPLE_KINDS and page.text),
            key=page_order,
        )
        if not any(last is None or page.changed_at > last.started_at for page in pages):
            return self._recorded(
                lead,
                run_id,
                crawl_run_id,
                ExtractionOutcome.NO_PAGES,
                route_tuple.actor,
                started_at,
            )
        full = [page.text or "" for page in pages]
        cut = allot(full, PAGE_TEXT_BUDGET)
        subject = f"lead:{lead.id}"
        return _PeopleCall(
            lead_id=lead.id,
            web_domain=lead.web_domain,
            run_id=run_id,
            crawl_run_id=crawl_run_id,
            started_at=started_at,
            route_tuple=route_tuple,
            segment=seeded.definition,
            pages={
                page.id: SentPage(page.url, page.fetched_at, text)
                for page, text in zip(pages, full, strict=True)
            },
            team_sent=any(page.kind == PageKind.TEAM for page in pages),
            blocks=tuple(
                UntrustedBlock(subject, WEBSITE_BLOCK, page.id, text)
                for page, text in zip(pages, cut, strict=True)
            ),
            pages_truncated=sum(
                len(short) < len(text) for short, text in zip(cut, full, strict=True)
            ),
        )

    def _recorded(
        self,
        lead: StoredLead,
        run_id: str,
        crawl_run_id: str | None,
        outcome: ExtractionOutcome,
        actor: str | None,
        started_at: datetime,
    ) -> RecordedRun:
        """Record a run that made no call, and enqueue the next stage."""
        recorded = self._record(lead.id, crawl_run_id, outcome, actor=actor, started_at=started_at)
        self._enqueue_next_stage(lead.id, run_id, lead.web_domain)
        return RecordedRun(lead.id, recorded, outcome, None)

    # Runs and the next stage --------------------------------------------------------------------

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
        self,
        call: _PeopleCall,
        outcome: ExtractionOutcome,
        code: str | None,
        *,
        select: bool = True,
    ) -> RecordedRun:
        """Record a run that stored nothing after the call (and enqueue the next stage, unless
        the run is followed by a raise)."""
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
        if select:
            self._enqueue_next_stage(call.lead_id, call.run_id, call.web_domain)
        return RecordedRun(call.lead_id, run_id, outcome, code)

    def _enqueue_next_stage(self, lead_id: str, run_id: str, web_domain: str | None) -> None:
        """Enqueue the chain's next stage for the Lead, in the caller's transaction: the one
        insertion point between the people step and ``select_primary_contact``. It is
        ``check_job_boards`` (Story 2.11), whose finish enqueues ``check_legal_form`` (Story
        2.12), which enqueues the Primary Contact's selection."""
        self._queue.enqueue(
            CHECK_JOB_BOARDS_JOB,
            scope=lead_id,
            discriminator=TICK,
            payload={VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead_id, RUN_ID_FIELD: run_id},
            concurrency_key=None if web_domain is None else f"{DOMAIN_KEY_PREFIX}{web_domain}",
        )

    # Phase 3 ------------------------------------------------------------------------------------

    def _apply(self, call: _PeopleCall, parsed: Mapping[str, Any]) -> _Applied:
        """Phase 3, in the caller's transaction."""
        now = self._clock.now()
        applied = _Applied()
        lead = discovery_queries.lead(call.lead_id)
        if lead is None:  # Leads are never deleted
            raise RuntimeError(f"Lead {call.lead_id} has no row")
        if lead.merged_into is not None:
            applied.merged = self._finished(call, ExtractionOutcome.LEAD_MERGED, None)
            return applied
        validation = validate_people(parsed, call.pages)
        self._log_refusals(call, validation, now)
        applied.people = len(validation.people)
        applied.discarded = len(validation.discards)
        proposed = self._contacts(call, lead, validation, applied)
        live_rows = signals_repo.evidence(call.lead_id, live_only=True)
        texts = {page.url: page.text for page in call.pages.values()}
        plan = reconcile_people(self._in_scope(call, live_rows), proposed, texts)
        superseded, retracted = self._carry_out(call, plan, live_rows, now, applied)
        counts_superseded, counts_retracted = self._lawyers(
            call, validation, live_rows, now, applied
        )
        superseded.extend(counts_superseded)
        retracted.extend(counts_retracted)
        for evidence_id in (*superseded, *retracted):
            current().publish(EvidenceChanged(lead_id=call.lead_id, evidence_id=evidence_id))
        applied.superseded = len(superseded)
        applied.retracted = len(retracted)
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
        self._enqueue_next_stage(call.lead_id, call.run_id, call.web_domain)
        return applied

    def _log_refusals(self, call: _PeopleCall, validation: PeopleValidation, now: datetime) -> None:
        for discard in validation.discards:
            eventlog.append(
                entity=ENTITY,
                entity_id=call.lead_id,
                from_status=None,
                to_status=PERSON_DISCARDED,
                cause=str(discard.cause),
                cause_ref=discard.page_id,
                actor=call.route_tuple.actor,
                at=now,
            )

    def _contacts(
        self,
        call: _PeopleCall,
        lead: StoredLead,
        validation: PeopleValidation,
        applied: _Applied,
    ) -> list[PersonQuote]:
        """Store each decision-maker as a Contact; their quotes, for reconciliation."""
        markets = [market.definition for market in setup_queries.markets()]
        proposed: list[PersonQuote] = []
        for person in validation.people:
            if decision_maker(person, call.segment) is None:
                applied.not_decision_makers += 1  # never stored
                continue
            page = call.pages[person.page_id]
            office = office_from_quote(person.quote, lead.country_code, markets)
            added = self._research.add_contact(
                lead.id,
                ContactCandidate(name=person.name, role=person.role, office=office),
                source=ProvenanceSource.WEBSITE,
                detail=page.url,
                retrieved_at=page.fetched_at,
            )
            if added.contact_id is None:  # the contactability gate blocked them (AD-16)
                applied.contacts_blocked += 1  # no Contact, no person Evidence, no quote
                continue
            applied.contacts_created += added.created
            proposed.append(PersonQuote(added.contact_id, person.page_id, page.url, person.quote))
        return proposed

    @staticmethod
    def _in_scope(
        call: _PeopleCall, rows: Sequence[StoredEvidence]
    ) -> list[LivePersonEvidence]:
        """The live rows reconciliation may touch: this route's person quotes on the pages
        sent."""
        urls = {page.url for page in call.pages.values()}
        return [
            LivePersonEvidence(
                row.id, row.person_contact_id, row.source_url, row.quote, row.created_at
            )
            for row in rows
            if row.type == EvidenceType.QUOTE
            and row.person_contact_id is not None
            and row.extracted_by.startswith(ACTOR_PREFIX)
            and row.quote is not None
            and row.source_url is not None
            and row.source_url in urls
        ]

    def _carry_out(
        self,
        call: _PeopleCall,
        plan: PeoplePlan,
        live_rows: Sequence[StoredEvidence],
        now: datetime,
        applied: _Applied,
    ) -> tuple[list[str], list[str]]:
        """Write the plan; the ids of the Evidence superseded and retracted."""
        fetched = {page.url: page.fetched_at for page in call.pages.values()}
        rows = {row.id: row for row in live_rows}
        for evidence_id in plan.confirm:
            row = rows[evidence_id]
            at = max(row.last_confirmed_at, fetched[row.source_url or ""])
            signals_repo.confirm_evidence(evidence_id, at)
        superseded: list[str] = []
        for quote in plan.insert:
            self._insert(call, quote, now)
        for superseding in plan.supersede:
            new_id = self._insert(call, superseding.new, now)
            signals_repo.supersede_evidence(superseding.old_id, new_id)
            superseded.append(superseding.old_id)
        for evidence_id in plan.retract:
            signals_repo.retract_evidence(evidence_id, now)
        applied.confirmed = len(plan.confirm)
        applied.stored = len(plan.insert) + len(plan.supersede)
        return superseded, list(plan.retract)

    def _insert(self, call: _PeopleCall, quote: PersonQuote, now: datetime) -> str:
        page = call.pages[quote.page_id]
        return signals_repo.insert_evidence(
            lead_id=call.lead_id,
            evidence_type=str(EvidenceType.QUOTE),
            quote=quote.quote,
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=(page.url,),
            source=str(EvidenceSource.WEBSITE),
            source_url=page.url,
            retrieved_at=page.fetched_at,
            extracted_by=call.route_tuple.actor,
            person_contact_id=quote.contact_id,
            at=now,
        )

    def _lawyers(
        self,
        call: _PeopleCall,
        validation: PeopleValidation,
        live_rows: Sequence[StoredEvidence],
        now: datetime,
        applied: _Applied,
    ) -> tuple[list[str], list[str]]:
        """Store or confirm a Law Lead's lawyer count; the ids of the live counts it superseded
        and retracted."""
        if (
            call.segment.size_range.unit is not SizeUnit.LAWYERS
            or not call.team_sent
            or call.pages_truncated > 0  # a cut page may hide lawyers
        ):
            return [], []
        count = lawyer_count(validation.people)
        if count < 1:  # a zero would falsely disqualify the Lead
            return [], []
        urls = tuple(page.url for page in call.pages.values())
        retrieved_at = max(page.fetched_at for page in call.pages.values())
        applied.lawyers = count
        live = [
            row
            for row in live_rows
            if row.type == EvidenceType.COUNT
            and row.sought == LAWYERS_LISTED
            and row.extracted_by == EXTRACTED_BY_RULE
        ]
        same = next(
            (
                row
                for row in live
                if row.number == count and sorted(row.pages_checked) == sorted(urls)
            ),
            None,
        )
        if same is not None:
            signals_repo.confirm_evidence(same.id, max(same.last_confirmed_at, retrieved_at))
            applied.confirmed += 1
            others = [row.id for row in live if row.id != same.id]
            for evidence_id in others:
                signals_repo.retract_evidence(evidence_id, now)
            return [], others
        new_id = signals_repo.insert_evidence(
            lead_id=call.lead_id,
            evidence_type=str(EvidenceType.COUNT),
            quote=None,
            sought=LAWYERS_LISTED,
            number=count,
            fact=None,
            value=None,
            pages_checked=urls,
            source=str(EvidenceSource.WEBSITE),
            source_url=None,
            retrieved_at=retrieved_at,
            extracted_by=EXTRACTED_BY_RULE,
            person_contact_id=None,
            at=now,
        )
        applied.stored += 1
        for row in live:
            signals_repo.supersede_evidence(row.id, new_id)
        return [row.id for row in live], []


class PrimaryContacts:
    """The Primary Contact's selection and the operator's choice; see the module docstring."""

    def __init__(self, clock: Clock, research: Research, queue: JobQueue) -> None:
        self._clock = clock
        self._research = research
        self._queue = queue

    @use_case
    def select_primary_contact(self, lead_id: str) -> PrimaryChoice | None:
        """Pick the Lead's Primary Contact; ``None`` for an unknown or merged Lead."""
        skipped: str | None = None
        choice: PrimaryChoice | None = None
        with current().transaction():
            lead = discovery_queries.lead(lead_id)
            if lead is None:
                skipped = UNKNOWN_LEAD
            elif lead.merged_into is not None:
                skipped = MERGED_LEAD
            else:
                choice = self._research.select_primary(lead.id)
                self._search(lead, choice.contact_id, lane=None)
                self._queue.enqueue(
                    SCORE_LEAD_JOB,
                    scope=lead.id,
                    discriminator=TICK,
                    payload={VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead.id},
                )
        if choice is None:
            log.info(PRIMARY_SELECTION_SKIPPED, extra={"lead_id": lead_id, "reason": skipped})
            return None
        log.info(
            PRIMARY_CONTACT_SELECTED,
            extra={
                "lead_id": lead_id,
                "contact_id": choice.contact_id,
                "set_by": None if choice.set_by is None else str(choice.set_by),
            },
        )
        return choice

    @use_case
    def make_primary(self, lead_id: str, contact_id: str) -> None:
        """Make the Contact the Lead's Primary, chosen by the operator; ``PrimaryRefused``
        when it can't be."""
        with current().transaction():
            self._research.make_primary(lead_id, contact_id)
            lead = discovery_queries.lead(lead_id)
            assert lead is not None  # make_primary raised PrimaryRefused for an unknown Lead
            self._search(lead, contact_id, lane=Lane.INTERACTIVE)
        log.info(PRIMARY_CONTACT_MADE, extra={"lead_id": lead_id, "contact_id": contact_id})

    def _search(self, lead: StoredLead, contact_id: str | None, *, lane: Lane | None) -> None:
        """Enqueue the Primary Contact's email search (Story 2.13) when it has no usable
        address, or its verification (Story 2.14) when its address is ``guessed`` and not yet
        verified, in the caller's transaction."""
        contact = None if contact_id is None else contacts_repo.get(contact_id)
        if contact is None:
            return
        if not has_usable_address(contact):
            enqueue_search(self._queue, lead.id, contact.id, lead.web_domain, lane=lane)
        elif needs_verifier(contact):
            enqueue_verify(self._queue, lead.id, contact.id, lane=lane)
