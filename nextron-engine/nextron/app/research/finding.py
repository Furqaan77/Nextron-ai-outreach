"""Finding a Contact's email address in the cheapest order (FR-14, FR-51, FR-57, AD-7, AD-9,
AD-10, AD-16, INT-6, NFR-12; Story 2.13): part of the ``research`` service, the sole writer of
Contacts, Evidence and Provenance. ``nextron.app.research.api``'s docstring points here.

The rules (a name's tokens, the seven patterns, which published address names the Contact, how
a pattern is learned, each step's Provenance) are pure and live in
``nextron.domain.email_finding``. This module reads, writes and makes the paid calls. No log
line, ``events`` row, error or job payload holds an address, a name, a quote or a domain: IDs,
codes and counts only.

**``EmailFinding(clock, meter, research, queue, *, domain_search, hunter, prospeo)``** runs
the ``find_contact_email`` job. ``find_contact_email`` is the job's use case (the unit of
work only lets a running use case open a transaction); it opens its own transactions, and every
port call happens with no transaction open.

``@use_case find_contact_email(lead_id, contact_id) -> EmailSearchOutcome | None``. It returns
``None`` and logs ``email_search_skipped`` (``lead_id``, ``contact_id``, ``reason``) for an
unknown or merged Lead, a Contact unknown or not on the Lead, or a Contact that already has a
usable address (an email whose class isn't ``invalid``), whether found at the start or by a re-read
before a write. Otherwise it tries, in order, stopping at the first address assigned:

1. **The gate.** ``compliance_api.assert_contactable(ContactSubject(contact_id), email,
   discovery)``. ``Blocked`` sets the outcome ``lead_closed`` when every reason is
   ``lead_lost`` or ``lead_disqualified``, else ``suppressed``, and appends one ``events`` row
   (entity ``contacts``, the Contact, ``None`` to ``email_search_blocked``, cause the first
   reason, no ``cause_ref``, actor ``system:research``). Nothing else runs.
2. **Published** (free): ``published_matches`` over the Lead's crawled pages (``page_order``,
   each with its stored text and ``mailto`` list), with the Lead's ``web_domain`` as the mail
   domain (Decision 5).
3. **Pattern** (free): the domain's stored pattern, else one learned (Decision 7) from the
   Lead's *other* named Contacts whose published match is at the mail domain and passes the
   gate (``NewContactSubject``); each example fitting the learned pattern gets its Quote
   Evidence (inserted or confirmed), whose ids are stored with the pattern (``published``). A
   stored pattern is never overwritten.
4. **Hunter Domain Search**, only with a web domain, no stored pattern, no earlier Domain
   Search for the domain (``domain_search_at``) and a Contact whose name has a last token.
   After an answer it sets ``domain_search_at`` and stores the mapped pattern
   (``hunter_domain_search``) when none is stored; the candidates are the ``personal`` people
   whose name tokens equal the Contact's, then the pattern's address. Nothing else from the
   answer is stored.
5. **Hunter Email Finder**, then **Prospeo**, each only with a web domain, a last token and the
   provider not yet in the Contact's ``email_search_tried``. An answer (a 2xx, or Hunter's 404)
   adds the provider to ``email_search_tried`` at once. Hunter's ``claimed`` answer ends the
   search ``not_found`` (without Prospeo, and without recording Hunter as tried, so a re-run
   asks Hunter again rather than Prospeo). Prospeo's ``credential_not_set`` skips Prospeo
   without recording it; every other ``AdapterError`` propagates (the runner retries a
   retryable one; a Hunter key that isn't set fails the job).
6. Otherwise the outcome is ``not_found``.

**Before each paid step** a fresh transaction runs ``allow(BUDGET_CLASS, meter.snapshot(now),
queries.setup.budget_limits(), quota=<hunter or prospeo>)``. A refusal records
``quota_reached``, makes no call and returns ``QUOTA_REACHED`` (the handler defers the job).

**Assigning an address** is one transaction that re-reads the Contact and the Lead first. A
candidate is dropped (the next is tried) when ``normalize_email`` refuses it, it is a role
address, another Contact on the Lead holds it, or it is in this Contact's ``address``
Provenance history. The first one left is gated with ``NewContactSubject(lead_id,
email=<address>, name=<the Contact's name>, linkedin_url=<its LinkedIn URL>)``: ``Blocked`` is
handled as in step 1 and ends the search. Otherwise: ``contacts_repo.set_address`` (class
``assign(source).to``), an ``address`` Provenance Record (``provenance_for``: ``website`` with
the page URL and its ``fetched_at``, ``pattern`` or ``finder`` with the clock's now), one
``events`` row (entity ``contacts``, ``None`` to the class, cause ``assign(source).cause``,
actor ``system:research``), for a published address its Quote Evidence (``quote``,
``website``, ``source_url`` and ``pages_checked`` the page, ``retrieved_at`` its
``fetched_at``, ``extracted_by`` ``rule``, ``person_contact_id``; a live row with the same
quote, URL, Contact and actor is confirmed instead), ``research.resolve_market`` and the
outcome ``found``.

Every outcome is stored on the Contact (``email_search_outcome``, ``email_search_at``, with
``email_search_tried``). After the transaction that stores it commits, it logs
``email_search_finished`` (``lead_id``, ``contact_id``, ``outcome``, ``step``: the step that
ended the search, ``calls``: the paid calls made by this run).

**``@use_case queue_search(lead_id, contact_id) -> SearchQueued``** (the Lead page's "Find
email"): ``unknown_lead``, ``merged_lead``, ``not_on_lead`` and ``has_address`` write nothing;
``queued`` enqueues the job on the interactive lane (a live job with its key absorbs it).
``enqueue_search`` is the enqueue every trigger uses: scope the Contact, discriminator
``tick``, payload ``{v, lead_id, contact_id}``, concurrency key ``domain:<web_domain>`` when
the Lead has one.
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.discovery import StoredLead
from nextron.app.research.api import Research
from nextron.app.research.extraction import page_order
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import email_domains as email_domains_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.uow import current, use_case
from nextron.domain.address_class import AddressClass, assign
from nextron.domain.budget import allow
from nextron.domain.contactability import (
    Allowed,
    Blocked,
    BlockReason,
    Channel,
    ContactSubject,
    NewContactSubject,
    Purpose,
)
from nextron.domain.contacts import ProvenanceSubject
from nextron.domain.crawl import DOMAIN_KEY_PREFIX, LEAD_ID_FIELD, PAYLOAD_VERSION
from nextron.domain.email_finding import (
    BUDGET_CLASS,
    CONTACT_ID_FIELD,
    FIND_CONTACT_EMAIL_JOB,
    HUNTER_QUOTA,
    PROSPEO_QUOTA,
    EmailSearchOutcome,
    FinderStep,
    NameTokens,
    PageAddresses,
    Pattern,
    PatternSource,
    PublishedMatch,
    address_for,
    fitting,
    hunter_pattern,
    is_role_address,
    learn_pattern,
    name_tokens,
    provenance_for,
    published_matches,
    tried_of,
    tried_text,
)
from nextron.domain.extraction import EXTRACTED_BY_RULE, EvidenceSource
from nextron.domain.identity import IdentityError, normalize_email, registrable_domain
from nextron.domain.ids import is_ulid
from nextron.domain.job import TICK, VERSION_FIELD, Lane
from nextron.domain.segments import EvidenceType
from nextron.ports.clock import Clock
from nextron.ports.email_finder import (
    DomainSearchAnswer,
    DomainSearchPort,
    EmailFinderPort,
    FinderQuery,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

ENTITY: Final = "contacts"
ACTOR: Final = "system:research"
EMAIL_SEARCH_BLOCKED: Final = "email_search_blocked"
EMAIL_SEARCH_SKIPPED: Final = "email_search_skipped"
EMAIL_SEARCH_FINISHED: Final = "email_search_finished"
# Why a search was skipped, as logged
UNKNOWN_LEAD: Final = "unknown_lead"
MERGED_LEAD: Final = "merged_lead"
NOT_ON_LEAD: Final = "not_on_lead"
HAS_ADDRESS: Final = "has_address"
CLOSED_REASONS: Final = frozenset({BlockReason.LEAD_LOST, BlockReason.LEAD_DISQUALIFIED})


class SearchQueued(StrEnum):
    """What "Find email" did."""

    QUEUED = "queued"
    UNKNOWN_LEAD = "unknown_lead"
    MERGED_LEAD = "merged_lead"
    NOT_ON_LEAD = "not_on_lead"
    HAS_ADDRESS = "has_address"


def has_usable_address(contact: StoredContact) -> bool:
    """Whether the Contact has an address whose class isn't ``invalid``."""
    return contact.email is not None and contact.address_class != AddressClass.INVALID


def enqueue_search(
    queue: JobQueue,
    lead_id: str,
    contact_id: str,
    web_domain: str | None,
    *,
    lane: Lane | None = None,
) -> None:
    """Enqueue the Contact's ``find_contact_email`` job in the caller's transaction."""
    queue.enqueue(
        FIND_CONTACT_EMAIL_JOB,
        scope=contact_id,
        discriminator=TICK,
        payload={
            VERSION_FIELD: PAYLOAD_VERSION,
            LEAD_ID_FIELD: lead_id,
            CONTACT_ID_FIELD: contact_id,
        },
        lane=lane,
        concurrency_key=None if web_domain is None else f"{DOMAIN_KEY_PREFIX}{web_domain}",
    )


def _blocked_outcome(blocked: Blocked) -> EmailSearchOutcome:
    if all(reason in CLOSED_REASONS for reason in blocked.reasons):
        return EmailSearchOutcome.LEAD_CLOSED
    return EmailSearchOutcome.SUPPRESSED


def _gate(subject: ContactSubject | NewContactSubject) -> Blocked | None:
    """The gate's answer: ``None`` when allowed; anything but ``Allowed`` or ``Blocked`` raises
    ``TypeError`` (naming its type only), so nothing proceeds on it."""
    answer = compliance_api.assert_contactable(subject, Channel.EMAIL, Purpose.DISCOVERY)
    if isinstance(answer, Blocked):
        return answer
    if not isinstance(answer, Allowed):
        raise TypeError(f"assert_contactable answered a {type(answer).__name__}")
    return None


def _at_domain(address: str, mail_domain: str) -> bool:
    domain = address.rpartition("@")[2]
    if domain == mail_domain:
        return True
    try:
        return registrable_domain(domain) == mail_domain
    except IdentityError:
        return False


@dataclass(frozen=True, slots=True)
class _Candidate:
    """An address to try, and how it was found."""

    address: str = field(repr=False)
    step: FinderStep
    pattern: Pattern | None = None
    pattern_source: PatternSource | None = None
    match: PublishedMatch | None = field(default=None, repr=False)


@dataclass(slots=True)
class _Search:
    """One run's state: what was read up front and what the run did."""

    lead_id: str
    contact_id: str
    web_domain: str | None = None
    name: str | None = field(default=None, repr=False)
    tokens: NameTokens | None = field(default=None, repr=False)
    tried: list[str] = field(default_factory=list)
    calls: int = 0
    step: FinderStep | None = None


class _Skip(Exception):
    """The Contact or Lead no longer qualifies; ``reason`` is logged."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EmailFinding:
    """The ``find_contact_email`` jobs and "Find email"; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        research: Research,
        queue: JobQueue,
        *,
        domain_search: DomainSearchPort,
        hunter: EmailFinderPort,
        prospeo: EmailFinderPort,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._research = research
        self._queue = queue
        self._domain_search = domain_search
        self._hunter = hunter
        self._prospeo = prospeo

    # The job -----------------------------------------------------------------------------------

    @use_case
    def find_contact_email(self, lead_id: str, contact_id: str) -> EmailSearchOutcome | None:
        """Find the Contact's address; see the module docstring."""
        search = _Search(lead_id, contact_id)
        try:
            outcome = self._run(search)
        except _Skip as skip:
            log.info(
                EMAIL_SEARCH_SKIPPED,
                extra={"lead_id": lead_id, "contact_id": contact_id, "reason": skip.reason},
            )
            return None
        log.info(
            EMAIL_SEARCH_FINISHED,
            extra={
                "lead_id": lead_id,
                "contact_id": contact_id,
                "outcome": str(outcome),
                "step": None if search.step is None else str(search.step),
                "calls": search.calls,
            },
        )
        return outcome

    def _run(self, search: _Search) -> EmailSearchOutcome:
        uow = current()
        with uow.transaction():
            self._read(search)
            blocked = _gate(ContactSubject(search.contact_id))
            if blocked is not None:
                return self._block(search, blocked)
        with uow.transaction():  # the free steps
            outcome = self._free_steps(search)
        if outcome is not None:
            return outcome
        tokens, domain = search.tokens, search.web_domain
        if tokens is None or tokens.last is None or domain is None:
            return self._finish(search, EmailSearchOutcome.NOT_FOUND)
        outcome = self._domain_search_step(search, tokens, domain)
        if outcome is not None:
            return outcome
        for step, port, quota in (
            (FinderStep.HUNTER, self._hunter, HUNTER_QUOTA),
            (FinderStep.PROSPEO, self._prospeo, PROSPEO_QUOTA),
        ):
            if quota in search.tried:
                continue
            outcome = self._finder_step(search, tokens, domain, step, port, quota)
            if outcome is not None:
                return outcome
        return self._finish(search, EmailSearchOutcome.NOT_FOUND)

    # Reads -------------------------------------------------------------------------------------

    def _read(self, search: _Search) -> tuple[StoredLead, StoredContact]:
        """Read the Lead and the Contact into ``search``; raises ``_Skip``."""
        lead = discovery_queries.lead(search.lead_id)
        if lead is None:
            raise _Skip(UNKNOWN_LEAD)
        if lead.merged_into is not None:
            raise _Skip(MERGED_LEAD)
        contact = contacts_repo.get(search.contact_id) if is_ulid(search.contact_id) else None
        if contact is None or contact.lead_id != lead.id:
            raise _Skip(NOT_ON_LEAD)
        if has_usable_address(contact):
            raise _Skip(HAS_ADDRESS)
        search.web_domain = lead.web_domain
        search.name = contact.name
        search.tokens = None if contact.name is None else name_tokens(contact.name)
        search.tried = list(tried_of(contact.email_search_tried))
        return lead, contact

    def _pages(self, lead_id: str) -> list[PageAddresses]:
        return [
            PageAddresses(page.id, page.url, page.fetched_at, page.text, page.mailto)
            for page in sorted(crawl_repo.pages(lead_id), key=page_order)
        ]

    # Steps 2 and 3 -----------------------------------------------------------------------------

    def _free_steps(self, search: _Search) -> EmailSearchOutcome | None:
        """The published and pattern steps, in the caller's transaction."""
        self._read(search)
        tokens, name, domain = search.tokens, search.name, search.web_domain
        if tokens is None or name is None:
            return None
        pages = self._pages(search.lead_id)
        published = [
            _Candidate(match.address, FinderStep.PUBLISHED, match=match)
            for match in published_matches(tokens, name, pages, mail_domain=domain)
        ]
        outcome = self._assign(search, published)
        if outcome is not None or domain is None:
            return outcome
        stored = email_domains_repo.get(domain)
        pattern: Pattern | None = None
        source: PatternSource | None = None
        if stored is not None and stored.pattern is not None:
            pattern = Pattern(stored.pattern)
            source = None if stored.pattern_source is None else PatternSource(stored.pattern_source)
        else:
            pattern = self._learn(search, domain, pages)
            source = None if pattern is None else PatternSource.PUBLISHED
        if pattern is None:
            return None
        address = address_for(pattern, tokens, domain)
        if address is None:
            return None
        return self._assign(
            search,
            [_Candidate(address, FinderStep.PATTERN, pattern=pattern, pattern_source=source)],
        )

    def _learn(
        self, search: _Search, domain: str, pages: Sequence[PageAddresses]
    ) -> Pattern | None:
        """Learn the domain's pattern from the Lead's other Contacts' published addresses
        (Decision 7) and store it with its example Evidence; ``None`` when none is learned."""
        examples: list[tuple[StoredContact, PublishedMatch, NameTokens]] = []
        seen: set[str] = set()
        for other in contacts_repo.for_lead(search.lead_id):
            if other.id == search.contact_id or other.name is None:
                continue
            tokens = name_tokens(other.name)
            if tokens is None:
                continue
            match = next(
                (
                    found
                    for found in published_matches(tokens, other.name, pages, mail_domain=domain)
                    if _at_domain(found.address, domain) and not is_role_address(found.address)
                ),
                None,
            )
            if match is None or match.address in seen:  # one address is one example
                continue
            subject = NewContactSubject(
                search.lead_id,
                email=match.address,
                name=other.name,
                linkedin_url=other.linkedin_url,
            )
            if _gate(subject) is not None:
                continue
            seen.add(match.address)
            examples.append((other, match, tokens))
        pattern = learn_pattern((match.address, tokens) for _, match, tokens in examples)
        if pattern is None:
            return None
        now = self._clock.now()
        evidence_ids = [
            self._quote_evidence(search.lead_id, other.id, match, now)
            for other, match, tokens in examples
            if pattern in fitting(match.address.rpartition("@")[0], tokens)
        ]
        row = email_domains_repo.ensure(domain, now)
        email_domains_repo.set_pattern(
            row.id,
            pattern=str(pattern),
            source=str(PatternSource.PUBLISHED),
            evidence_ids=evidence_ids,
            at=now,
        )
        return pattern

    # Step 4 ------------------------------------------------------------------------------------

    def _allowed(self, search: _Search, quota: str) -> bool:
        """The budget check before a paid step, in its own transaction; a refusal records
        ``quota_reached``."""
        with current().transaction():
            now = self._clock.now()
            limits = setup_queries.budget_limits()
            if allow(BUDGET_CLASS, self._meter.snapshot(now), limits, quota=quota):
                return True
            self._read(search)
            self._record(search, EmailSearchOutcome.QUOTA_REACHED)
        return False

    def _domain_search_step(
        self, search: _Search, tokens: NameTokens, domain: str
    ) -> EmailSearchOutcome | None:
        with current().transaction():
            stored = email_domains_repo.get(domain)
        if stored is not None and (stored.pattern is not None or stored.domain_search_at):
            return None
        search.step = FinderStep.HUNTER_DOMAIN_SEARCH
        if not self._allowed(search, HUNTER_QUOTA):
            return EmailSearchOutcome.QUOTA_REACHED
        search.calls += 1
        answer = self._domain_search.domain_search(domain)
        with current().transaction():
            # the paid call is recorded first, whatever the re-read finds (no second credit)
            now = self._clock.now()
            row = email_domains_repo.ensure(domain, now)
            email_domains_repo.set_domain_search_at(row.id, now)
            mapped = hunter_pattern(answer.pattern)
            if mapped is not None and row.pattern is None:
                email_domains_repo.set_pattern(
                    row.id,
                    pattern=str(mapped),
                    source=str(PatternSource.HUNTER_DOMAIN_SEARCH),
                    evidence_ids=None,
                    at=now,
                )
            skipped = self._skipped(search)
            if skipped is not None:
                result: EmailSearchOutcome | _Skip | None = skipped
            else:
                pattern = Pattern(row.pattern) if row.pattern is not None else mapped
                candidates = self._domain_candidates(answer, tokens, domain, pattern)
                result = self._assign(search, candidates)
        if isinstance(result, _Skip):
            raise result
        return result

    @staticmethod
    def _domain_candidates(
        answer: DomainSearchAnswer, tokens: NameTokens, domain: str, pattern: Pattern | None
    ) -> list[_Candidate]:
        """The Domain Search's candidates: the personal people named as the Contact, then the
        domain pattern's address (the stored one, else Hunter's)."""
        candidates = [
            _Candidate(person.email, FinderStep.HUNTER_DOMAIN_SEARCH)
            for person in answer.people
            if person.personal
            and name_tokens(" ".join(n for n in (person.first_name, person.last_name) if n))
            == tokens
        ]
        if pattern is not None:
            address = address_for(pattern, tokens, domain)
            if address is not None:
                candidates.append(_Candidate(address, FinderStep.HUNTER_DOMAIN_SEARCH))
        return candidates

    def _skipped(self, search: _Search) -> _Skip | None:
        """The ``_Skip`` a re-read would raise, returned instead, so the caller's writes stay."""
        try:
            self._read(search)
        except _Skip as skip:
            return skip
        return None

    # Step 5 ------------------------------------------------------------------------------------

    def _finder_step(
        self,
        search: _Search,
        tokens: NameTokens,
        domain: str,
        step: FinderStep,
        port: EmailFinderPort,
        provider: str,
    ) -> EmailSearchOutcome | None:
        previous, search.step = search.step, step
        if not self._allowed(search, provider):
            return EmailSearchOutcome.QUOTA_REACHED
        query = FinderQuery(domain, tokens.first, tokens.last or "")
        try:
            answer = port.find_email(query)
        except AdapterError as error:
            if step is FinderStep.PROSPEO and error.code == CREDENTIAL_NOT_SET:
                search.step = previous  # Prospeo's key is optional: skipped, not recorded
                return None
            raise
        search.calls += 1
        if answer.claimed:  # the person asked Hunter not to give their address out
            return self._finish(search, EmailSearchOutcome.NOT_FOUND)
        with current().transaction():
            # the answer is recorded first, whatever the re-read finds (no second credit)
            stored = contacts_repo.get(search.contact_id)
            if stored is not None:
                tried = list(tried_of(stored.email_search_tried))
                if provider not in tried:
                    tried.append(provider)
                search.tried = tried
                contacts_repo.set_email_search(
                    search.contact_id, tried=tried_text(tried), outcome=None, at=None
                )
            skipped = self._skipped(search)
            if skipped is not None:
                result: EmailSearchOutcome | _Skip | None = skipped
            elif answer.email is None:
                result = None
            else:
                result = self._assign(search, [_Candidate(answer.email, step)])
        if isinstance(result, _Skip):
            raise result
        return result

    # Assigning ---------------------------------------------------------------------------------

    def _assign(
        self, search: _Search, candidates: Iterable[_Candidate]
    ) -> EmailSearchOutcome | None:
        """Assign the first candidate left after the checks, in the caller's transaction;
        ``None`` when none is left."""
        lead, contact = self._read(search)
        held = {
            other.email
            for other in contacts_repo.for_lead(lead.id)
            if other.id != contact.id and other.email is not None
        }
        history = {
            record.address
            for record in provenance_repo.for_contact(contact.id)
            if record.subject == ProvenanceSubject.ADDRESS and record.address is not None
        }
        for candidate in candidates:
            try:
                address = normalize_email(candidate.address)
            except IdentityError:
                continue
            if is_role_address(address) or address in held or address in history:
                continue
            blocked = _gate(
                NewContactSubject(
                    lead.id, email=address, name=contact.name, linkedin_url=contact.linkedin_url
                )
            )
            search.step = candidate.step
            if blocked is not None:
                return self._block(search, blocked)
            self._write(search, contact, address, candidate)
            return EmailSearchOutcome.FOUND
        return None

    def _write(
        self, search: _Search, contact: StoredContact, address: str, candidate: _Candidate
    ) -> None:
        now = self._clock.now()
        match = candidate.match
        found = provenance_for(
            candidate.step,
            page_url=None if match is None else match.url,
            pattern=candidate.pattern,
            pattern_source=candidate.pattern_source,
        )
        assignment = assign(found.source)
        contacts_repo.set_address(
            contact.id, email=address, address_class=str(assignment.to), at=now
        )
        provenance_repo.insert(
            contact_id=contact.id,
            subject=str(ProvenanceSubject.ADDRESS),
            source_kind=str(found.source),
            source_detail=found.detail,
            address=address,
            retrieved_at=now if match is None else match.fetched_at,
            address_class=str(assignment.to),
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=contact.id,
            from_status=None,
            to_status=str(assignment.to),
            cause=str(assignment.cause),
            cause_ref=None,
            actor=ACTOR,
            at=now,
        )
        if match is not None:
            self._quote_evidence(search.lead_id, contact.id, match, now)
        self._research.resolve_market(contact.id)
        self._record(search, EmailSearchOutcome.FOUND)

    def _quote_evidence(
        self, lead_id: str, contact_id: str, match: PublishedMatch, now: datetime
    ) -> str:
        """The Quote Evidence of a published address: a live row with the same quote, URL,
        Contact and actor is confirmed, else one is inserted; its id."""
        for row in signals_repo.evidence(lead_id, live_only=True):
            if (
                row.type == EvidenceType.QUOTE
                and row.quote == match.quote
                and row.source_url == match.url
                and row.person_contact_id == contact_id
                and row.extracted_by == EXTRACTED_BY_RULE
            ):
                signals_repo.confirm_evidence(row.id, max(row.last_confirmed_at, match.fetched_at))
                return row.id
        return signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type=str(EvidenceType.QUOTE),
            quote=match.quote,
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=(match.url,),
            source=str(EvidenceSource.WEBSITE),
            source_url=match.url,
            retrieved_at=match.fetched_at,
            extracted_by=EXTRACTED_BY_RULE,
            person_contact_id=contact_id,
            at=now,
        )

    # Outcomes ----------------------------------------------------------------------------------

    def _record(self, search: _Search, outcome: EmailSearchOutcome) -> None:
        contacts_repo.set_email_search(
            search.contact_id,
            tried=tried_text(search.tried),
            outcome=str(outcome),
            at=self._clock.now(),
        )

    def _block(self, search: _Search, blocked: Blocked) -> EmailSearchOutcome:
        """Record a ``Blocked`` answer, in the caller's transaction."""
        outcome = _blocked_outcome(blocked)
        eventlog.append(
            entity=ENTITY,
            entity_id=search.contact_id,
            from_status=None,
            to_status=EMAIL_SEARCH_BLOCKED,
            cause=str(blocked.reasons[0]),
            cause_ref=None,
            actor=ACTOR,
            at=self._clock.now(),
        )
        self._record(search, outcome)
        return outcome

    def _finish(self, search: _Search, outcome: EmailSearchOutcome) -> EmailSearchOutcome:
        with current().transaction():
            self._read(search)
            self._record(search, outcome)
        return outcome

    # "Find email" ------------------------------------------------------------------------------

    @use_case
    def queue_search(self, lead_id: str, contact_id: str) -> SearchQueued:
        """Queue the Contact's email search on the interactive lane; see the module
        docstring."""
        with current().transaction():
            lead = discovery_queries.lead(lead_id) if is_ulid(lead_id) else None
            if lead is None:
                return SearchQueued.UNKNOWN_LEAD
            if lead.merged_into is not None:
                return SearchQueued.MERGED_LEAD
            contact = contacts_repo.get(contact_id) if is_ulid(contact_id) else None
            if contact is None or contact.lead_id != lead.id:
                return SearchQueued.NOT_ON_LEAD
            if has_usable_address(contact):
                return SearchQueued.HAS_ADDRESS
            enqueue_search(
                self._queue, lead.id, contact.id, lead.web_domain, lane=Lane.INTERACTIVE
            )
        return SearchQueued.QUEUED
