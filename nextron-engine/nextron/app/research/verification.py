"""Checking a Contact's email address and assigning its Address Class (FR-15, FR-57, FR-59,
AD-4, AD-9, AD-16, INT-6, INT-11; Story 2.14): part of the ``research`` service, the sole writer
of Contacts, Evidence and Provenance.

The codes, the disposable list and the MX rule are pure and live in
``nextron.domain.email_checks``; the Address Class machine is ``nextron.domain.address_class``.
This module reads, writes, looks up MX records and calls the verifier. No log line, ``events``
row, error or job payload holds an address, a name, a quote or a domain: IDs, codes and counts
only.

**``EmailVerification(clock, meter, queue, *, dns, email_syntax, verifier)``** runs the
``verify_contact_email`` jobs. ``@use_case verify_contact_email(lead_id, contact_id) -> str |
None`` opens its own transactions, and makes the DNS and verifier calls with none open. It
runs, in order, and stops at the first outcome:

1. **Skip** (``None``, logs ``email_check_skipped``; nothing written): the Contact is missing,
   not on the Lead, erased, has no email or its class is ``invalid``.
2. **The gate.** ``compliance_api.assert_contactable(ContactSubject(contact_id), email,
   discovery)``. ``Blocked`` records the result ``suppressed`` with its date, leaves the class
   unchanged and makes no DNS or verifier call.
3. **Syntax** (``EmailSyntaxPort.is_valid``, offline). A refusal is ``invalid``, reason
   ``syntax``.
4. **Disposable** (``email_checks.is_disposable`` of the address's domain): ``invalid``, reason
   ``disposable``.
5. **MX** (``DnsPort.mx`` of the domain, in its IDNA/ASCII form; a domain that isn't a DNS name
   is ``syntax``). ``email_checks.mx_reason``: ``no_mx`` or ``null_mx`` is ``invalid``. A
   retryable ``AdapterError`` (``dns_timeout``, ``dns_unavailable``) propagates so the runner
   retries; any other DNS error propagates and fails the job. Nothing is written then.
6. A ``published`` or ``verified`` address, or a ``guessed`` address on a Contact that isn't the
   Primary, records ``checks_passed``; a ``guessed`` address the verifier already answered for
   (``email_verifier_at`` set) keeps its stored status and dates (nothing written). No verifier
   call.
7. **The verifier** for the Primary's ``guessed`` address: in its own transaction, the Contact
   is re-read (skipped if changed) and the gate re-run (``Blocked`` records ``suppressed``, no
   call), then ``allow(BUDGET_CLASS, meter.snapshot(now), budget_limits(), quota="hunter")``; a
   refusal
   records ``quota_reached`` and returns it (the handler defers the job). Then
   ``verify_email(email)`` with no transaction open. ``valid`` moves the address to
   ``verified``; ``invalid`` moves it to ``invalid``, reason ``verifier_invalid``; any other
   status leaves it ``guessed``. Every answer records the status
   (``email_checks.normalise_status``), the date and ``email_verifier_at``.

After the DNS or verifier call, the apply transaction re-reads the Contact and skips (nothing
written) when it is gone, erased, or its address or class changed meanwhile.

**Each class change** goes through ``address_class.transition`` and writes, in one
transaction: ``contacts_repo.set_address_class``; one ``events`` row (entity ``contacts``, the
old class to the new, the transition's cause, actor ``system:research``); and an ``address``
Provenance Record with the address and its new class (deferred-work 246): ``finder`` /
"Hunter Email Verifier" at the clock's now for a verifier answer, ``website`` / the page URL at
the page's ``fetched_at`` for one found on the site, and a copy of the source and detail of the
address's latest ``address`` record, at the clock's now, for a failed offline check (none is
written when the address has no record). **After a move to ``invalid``** of the Primary's
address, when a finder source is still untried for it (``hunter`` or ``prospeo`` not in
``email_search_tried``, or a Domain Search ``finding`` would still run: the Contact's name has a
last token and the Lead's web domain has neither a stored pattern nor ``domain_search_at``),
``find_contact_email``
is enqueued through ``finding.enqueue_search`` in the same transaction (a live job with its key
absorbs it, so it is enqueued once).

Every outcome is stored on the Contact (``email_check_result``, ``email_checked_at``,
``email_invalid_reason`` and, after a verifier answer, ``email_verifier_at``), and logged as
``email_check_finished`` (``lead_id``, ``contact_id``, ``result``, ``reason``, ``calls``).

**``enqueue_verify(queue, lead_id, contact_id, *, lane=None)``** is the enqueue every trigger
uses, in the caller's transaction: scope the Contact, discriminator ``tick``, payload ``{v,
lead_id, contact_id}``. **``needs_verifier(contact)``**: the address is ``guessed`` and the
verifier hasn't answered for it.

**``promote_found_on_site(lead_id, at) -> int``** (find-on-site), in the caller's transaction,
is called by ``WebsiteCrawls`` after it stores pages (the crawl's and the renders' apply). Each
``guessed`` address of a Contact of the Lead that isn't erased, passes the gate
(``assert_contactable``, email, discovery; a ``Blocked`` Contact is left alone) and appears on a
stored page
(``email_finding.address_quote``: literally in its text, or in its ``mailto`` list; pages in
page order) moves to ``published`` (``find_on_site``) with its Quote Evidence (``quote``,
``website``, the page as ``source_url`` and ``pages_checked``, ``retrieved_at`` its
``fetched_at``, ``extracted_by`` ``rule``, ``person_contact_id``; a live row with the same
quote, URL, Contact and actor is confirmed instead). It returns how many moved.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from nextron.app.compliance import api as compliance_api
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import email_domains as email_domains_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.repos.research.crawl import StoredPage
from nextron.db.uow import current, use_case
from nextron.domain.address_class import AddressClass, AddressEvent, transition
from nextron.domain.budget import allow
from nextron.domain.contactability import Allowed, Blocked, Channel, ContactSubject, Purpose
from nextron.domain.contacts import ProvenanceSource, ProvenanceSubject
from nextron.domain.crawl import LEAD_ID_FIELD, PAYLOAD_VERSION
from nextron.domain.email_checks import (
    BUDGET_CLASS,
    HUNTER_QUOTA,
    INVALID_STATUS,
    VALID_STATUS,
    VERIFIER_DETAIL,
    VERIFY_CONTACT_EMAIL_JOB,
    CheckResult,
    InvalidReason,
    is_disposable,
    mx_reason,
    normalise_status,
)
from nextron.domain.email_finding import (
    CONTACT_ID_FIELD,
    FINDER_PROVIDERS,
    address_quote,
    name_tokens,
    tried_of,
)
from nextron.domain.extraction import EXTRACTED_BY_RULE, EvidenceSource
from nextron.domain.identity import IdentityError, normalize_email
from nextron.domain.ids import is_ulid
from nextron.domain.job import TICK, VERSION_FIELD, Lane
from nextron.domain.segments import EvidenceType
from nextron.ports.clock import Clock
from nextron.ports.dns import DnsPort, MxRecord, is_dns_name
from nextron.ports.email_finder import EmailVerifierPort
from nextron.ports.email_syntax import EmailSyntaxPort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

ENTITY: Final = "contacts"
ACTOR: Final = "system:research"
EMAIL_CHECK_SKIPPED: Final = "email_check_skipped"
EMAIL_CHECK_FINISHED: Final = "email_check_finished"
# Why a check was skipped, as logged
NOT_ON_LEAD: Final = "not_on_lead"
ERASED: Final = "erased"
NO_ADDRESS: Final = "no_address"
ALREADY_INVALID: Final = "invalid"
CHANGED: Final = "changed"


def enqueue_verify(
    queue: JobQueue, lead_id: str, contact_id: str, *, lane: Lane | None = None
) -> None:
    """Enqueue the Contact's ``verify_contact_email`` job in the caller's transaction."""
    queue.enqueue(
        VERIFY_CONTACT_EMAIL_JOB,
        scope=contact_id,
        discriminator=TICK,
        payload={
            VERSION_FIELD: PAYLOAD_VERSION,
            LEAD_ID_FIELD: lead_id,
            CONTACT_ID_FIELD: contact_id,
        },
        lane=lane,
    )


def needs_verifier(contact: StoredContact) -> bool:
    """Whether the Contact's address is ``guessed`` and the verifier hasn't answered for it."""
    return (
        contact.email is not None
        and contact.address_class == AddressClass.GUESSED
        and contact.email_verifier_at is None
    )


@dataclass(frozen=True, slots=True)
class _Source:
    """The source of a class change's ``address`` Provenance Record."""

    kind: str
    detail: str
    retrieved_at: datetime


def _change_class(
    contact: StoredContact, event: AddressEvent, *, source: _Source | None, at: datetime
) -> AddressClass:
    """Move the Contact's address through ``event``, in the caller's transaction: the class,
    one ``events`` row and, unless ``source`` is ``None``, an ``address`` Provenance Record;
    the new class."""
    if contact.email is None or contact.address_class is None:
        raise ValueError("only a stored address changes class")
    old = AddressClass(contact.address_class)
    moved = transition(old, event)
    contacts_repo.set_address_class(contact.id, address_class=str(moved.to), at=at)
    eventlog.append(
        entity=ENTITY,
        entity_id=contact.id,
        from_status=str(old),
        to_status=str(moved.to),
        cause=str(moved.cause),
        cause_ref=None,
        actor=ACTOR,
        at=at,
    )
    if source is not None:
        provenance_repo.insert(
            contact_id=contact.id,
            subject=str(ProvenanceSubject.ADDRESS),
            source_kind=source.kind,
            source_detail=source.detail,
            address=contact.email,
            retrieved_at=source.retrieved_at,
            address_class=str(moved.to),
        )
    return moved.to


def _site_evidence(
    lead_id: str, contact_id: str, quote: str, page: StoredPage, now: datetime
) -> str:
    """The Quote Evidence of an address found on ``page``: a live row with the same quote,
    URL, Contact and actor is confirmed, else one is inserted; its id."""
    for row in signals_repo.evidence(lead_id, live_only=True):
        if (
            row.type == EvidenceType.QUOTE
            and row.quote == quote
            and row.source_url == page.url
            and row.person_contact_id == contact_id
            and row.extracted_by == EXTRACTED_BY_RULE
        ):
            signals_repo.confirm_evidence(row.id, max(row.last_confirmed_at, page.fetched_at))
            return row.id
    return signals_repo.insert_evidence(
        lead_id=lead_id,
        evidence_type=str(EvidenceType.QUOTE),
        quote=quote,
        sought=None,
        number=None,
        fact=None,
        value=None,
        pages_checked=(page.url,),
        source=str(EvidenceSource.WEBSITE),
        source_url=page.url,
        retrieved_at=page.fetched_at,
        extracted_by=EXTRACTED_BY_RULE,
        person_contact_id=contact_id,
        at=now,
    )


def promote_found_on_site(lead_id: str, at: datetime) -> int:
    """Move each ``guessed`` address of the Lead's Contacts found on a stored page to
    ``published``, in the caller's transaction; how many moved. See the module docstring."""
    guessed = [
        contact
        for contact in contacts_repo.for_lead(lead_id)
        if contact.erased_at is None
        and contact.email is not None
        and contact.address_class == AddressClass.GUESSED
    ]
    if not guessed:
        return 0
    # extraction imports research.api, which imports this module: imported here, not above
    from nextron.app.research.extraction import page_order  # noqa: PLC0415

    pages = sorted(crawl_repo.pages(lead_id), key=page_order)
    moved = 0
    for contact in guessed:
        email = contact.email
        if email is None:  # narrowed above
            continue
        if not _contactable(contact.id):
            continue
        for page in pages:
            quote = address_quote(email, page.text, page.mailto)
            if quote is None:
                continue
            source = _Source(str(ProvenanceSource.WEBSITE), page.url, page.fetched_at)
            _change_class(contact, AddressEvent.FIND_ON_SITE, source=source, at=at)
            _site_evidence(lead_id, contact.id, quote, page, at)
            moved += 1
            break
    return moved


def _contactable(contact_id: str) -> bool:
    """Whether the gate allows the Contact (email, discovery); ``False`` when ``Blocked``."""
    answer = compliance_api.assert_contactable(
        ContactSubject(contact_id), Channel.EMAIL, Purpose.DISCOVERY
    )
    if isinstance(answer, Blocked):
        return False
    if not isinstance(answer, Allowed):
        raise TypeError(f"assert_contactable answered a {type(answer).__name__}")
    return True


@dataclass(slots=True)
class _Check:
    """One run's state."""

    lead_id: str
    contact_id: str
    email: str = field(default="", repr=False)
    address_class: str = ""
    calls: int = 0
    reason: str | None = None


class _Skip(Exception):
    """The Contact no longer qualifies; ``reason`` is logged."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EmailVerification:
    """The ``verify_contact_email`` jobs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        queue: JobQueue,
        *,
        dns: DnsPort,
        email_syntax: EmailSyntaxPort,
        verifier: EmailVerifierPort,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._queue = queue
        self._dns = dns
        self._email_syntax = email_syntax
        self._verifier = verifier

    @use_case
    def verify_contact_email(self, lead_id: str, contact_id: str) -> str | None:
        """Check the Contact's address; see the module docstring."""
        check = _Check(lead_id, contact_id)
        try:
            result = self._run(check)
        except _Skip as skip:
            log.info(
                EMAIL_CHECK_SKIPPED,
                extra={"lead_id": lead_id, "contact_id": contact_id, "reason": skip.reason},
            )
            return None
        log.info(
            EMAIL_CHECK_FINISHED,
            extra={
                "lead_id": lead_id,
                "contact_id": contact_id,
                "result": result,
                "reason": check.reason,
                "calls": check.calls,
            },
        )
        return result

    def _run(self, check: _Check) -> str:
        uow = current()
        with uow.transaction():
            contact = self._read(check)
            if not _contactable(contact.id):
                return self._record(contact, CheckResult.SUPPRESSED)
            domain = self._offline(contact)
            if domain is None:
                return self._invalidate(contact, InvalidReason.SYNTAX, check)
            if is_disposable(domain):
                return self._invalidate(contact, InvalidReason.DISPOSABLE, check)
        records = self._mx(domain)
        with uow.transaction():
            contact = self._reread(check)
            reason = mx_reason(records)
            if reason is not None:
                return self._invalidate(contact, reason, check)
            if (
                contact.address_class == AddressClass.GUESSED
                and contact.email_verifier_at is not None
                and contact.email_check_result != CheckResult.SUPPRESSED
            ):
                # the verifier's stored status and date stand: nothing is overwritten
                return contact.email_check_result or str(CheckResult.CHECKS_PASSED)
            if not (contact.is_primary and needs_verifier(contact)):
                return self._record(contact, CheckResult.CHECKS_PASSED)
        with uow.transaction():
            # erased, changed or suppressed since the MX transaction: no paid call
            contact = self._reread(check)
            if not _contactable(contact.id):
                return self._record(contact, CheckResult.SUPPRESSED)
            now = self._clock.now()
            limits = setup_queries.budget_limits()
            if not allow(BUDGET_CLASS, self._meter.snapshot(now), limits, quota=HUNTER_QUOTA):
                return self._record(contact, CheckResult.QUOTA_REACHED)
        check.calls += 1
        verdict = self._verifier.verify_email(check.email)
        with uow.transaction():
            contact = self._reread(check)
            return self._apply_verdict(contact, normalise_status(verdict.status), check)

    # Reads -------------------------------------------------------------------------------------

    def _read(self, check: _Check) -> StoredContact:
        contact = contacts_repo.get(check.contact_id) if is_ulid(check.contact_id) else None
        if contact is None or contact.lead_id != check.lead_id:
            raise _Skip(NOT_ON_LEAD)
        if contact.erased_at is not None:
            raise _Skip(ERASED)
        if contact.email is None or contact.address_class is None:
            raise _Skip(NO_ADDRESS)
        if contact.address_class == AddressClass.INVALID:
            raise _Skip(ALREADY_INVALID)
        check.email = contact.email
        check.address_class = contact.address_class
        return contact

    def _reread(self, check: _Check) -> StoredContact:
        """The Contact again, after a call made with no transaction open; ``_Skip`` when its
        address or class changed meanwhile."""
        email, address_class = check.email, check.address_class
        contact = self._read(check)
        if contact.email != email or contact.address_class != address_class:
            raise _Skip(CHANGED)
        return contact

    # The checks --------------------------------------------------------------------------------

    def _offline(self, contact: StoredContact) -> str | None:
        """The address's domain in its ASCII form when the syntax check passes, else
        ``None``."""
        email = contact.email or ""
        if not self._email_syntax.is_valid(email):
            return None
        try:
            domain = normalize_email(email).rpartition("@")[2]
        except IdentityError:
            return None
        return domain if is_dns_name(domain) else None

    def _mx(self, domain: str) -> tuple[MxRecord, ...]:
        """The domain's MX records, with no transaction open; every ``AdapterError``
        propagates (the runner retries a retryable one)."""
        return self._dns.mx(domain)

    def _apply_verdict(self, contact: StoredContact, status: str, check: _Check) -> str:
        now = self._clock.now()
        source = _Source(str(ProvenanceSource.FINDER), VERIFIER_DETAIL, now)
        if status == VALID_STATUS:
            _change_class(contact, AddressEvent.VERIFY, source=source, at=now)
            reason = None
        elif status == INVALID_STATUS:
            _change_class(contact, AddressEvent.FAIL_CHECK, source=source, at=now)
            reason = str(InvalidReason.VERIFIER_INVALID)
            self._after_invalid(contact)
        else:
            reason = None
        check.reason = reason
        contacts_repo.set_email_check(
            contact.id, result=status, reason=reason, at=now, verifier_at=now
        )
        return status

    # Writes ------------------------------------------------------------------------------------

    def _record(self, contact: StoredContact, result: CheckResult) -> str:
        contacts_repo.set_email_check(
            contact.id, result=str(result), reason=None, at=self._clock.now()
        )
        return str(result)

    def _invalidate(self, contact: StoredContact, reason: InvalidReason, check: _Check) -> str:
        """A failed offline check, in the caller's transaction."""
        now = self._clock.now()
        latest = next(
            (
                record
                for record in reversed(provenance_repo.for_contact(contact.id))
                if record.subject == ProvenanceSubject.ADDRESS and record.address == contact.email
            ),
            None,
        )
        source = (
            None  # no record to copy: the class still changes, with its event
            if latest is None
            else _Source(latest.source_kind, latest.source_detail, now)
        )
        _change_class(contact, AddressEvent.FAIL_CHECK, source=source, at=now)
        check.reason = str(reason)
        contacts_repo.set_email_check(
            contact.id, result=str(CheckResult.INVALID), reason=str(reason), at=now
        )
        self._after_invalid(contact)
        return str(CheckResult.INVALID)

    def _after_invalid(self, contact: StoredContact) -> None:
        """Enqueue the Primary's email search again when a source is still untried for it."""
        if not contact.is_primary:
            return
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None or lead.merged_into is not None or lead.web_domain is None:
            return
        tried = tried_of(contact.email_search_tried)
        stored = email_domains_repo.get(lead.web_domain)
        tokens = None if contact.name is None else name_tokens(contact.name)
        # finding's Domain Search precondition: a last name token, no stored pattern, none yet
        domain_search_runs = (
            tokens is not None
            and tokens.last is not None
            and (stored is None or (stored.pattern is None and not stored.domain_search_at))
        )
        untried = domain_search_runs or any(provider not in tried for provider in FINDER_PROVIDERS)
        if not untried:
            return
        # finding imports research.api, which imports this module: imported here, not above
        from nextron.app.research.finding import enqueue_search  # noqa: PLC0415

        enqueue_search(self._queue, lead.id, contact.id, lead.web_domain)
