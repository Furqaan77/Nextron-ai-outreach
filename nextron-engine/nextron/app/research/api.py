"""Public command API of the ``research`` service (AD-3, AD-4, AD-7, AD-11, FR-2, FR-14, FR-15,
FR-51).

Other services and the delivery layers change ``research`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Research(clock)`` owns ``contacts`` and ``provenance_records`` and is the sole writer of
Provenance. Every Address Class assignment appends one ``events`` row (entity ``contacts``,
actor ``system:research``, at the clock's now) with IDs and codes only.

- ``@command add_contact(lead_id, candidate, *, source, detail, retrieved_at) ->
  AddedContact(contact_id, created, email_dropped)`` stores a person on a Lead, deduplicated
  within it:

  1. **Checks, before any write.** ``candidate`` not a ``ContactCandidate``, ``source`` not a
     ``ProvenanceSource``, or ``retrieved_at`` not an aware ``datetime`` raises ``TypeError``;
     a ``detail`` that isn't a ``str`` of 1 to 2,048 characters equal to its own strip raises
     ``ValueError``. The Lead is read with ``queries.discovery.lead``: an
     unknown Lead raises ``LookupError`` and a merged one ``ValueError``. With an email,
     ``assign(source)`` (``nextron.domain.address_class``) runs first, so a source that never
     yields an address (``IllegalTransition``) writes nothing.
  2. **Matching.** Among the Lead's Contacts (oldest first), the match is the first with an
     equal ``email``, else, when the candidate has a name, the first whose name has an equal
     ``normalize_name`` key.
  3. **No match.** A Contact is inserted (its Address Class ``assign(source).to`` when it has an
     email, its office's ``office_country`` and ``office_region`` when the candidate has an
     ``office``, ``is_primary`` false, created and updated now), then a ``contact`` Provenance
     Record. With an email, an ``address`` record (its ``address`` and ``address_class``) and
     one ``events`` row (``None`` to the class, cause ``assign(source).cause``, no
     ``cause_ref``) follow. ``created=True``.
  4. **A match.** Each of ``name``, ``role``, ``linkedin_url``, the office and ``email`` the
     Contact lacks is filled; nothing is ever overwritten. A filled name, role, LinkedIn URL or
     office adds one ``contact`` record; a filled email sets its class and adds the ``address``
     record and the ``events`` row as in step 3. A candidate email that differs from the
     Contact's own gives ``email_dropped=True`` and is not stored. When nothing is filled,
     nothing is written. ``created=False``.
  5. **Provenance Records** carry ``source_kind`` = ``source``, ``source_detail`` = ``detail``
     and ``retrieved_at``; ``address_class`` is set only on ``address`` records, ``notes`` hold
     the Market notes (below) and the eligibility-basis fields are NULL.
  6. **The Market (Story 3.2).** After the writes, the Contact is resolved (below) whenever it
     was created or anything was filled, in the same transaction (a filled name, role or
     LinkedIn URL included: it resolves a Contact stored before Story 3.2 and keeps the notes on
     its first ``contact`` record).

- ``@command reparent_lead(n: LeadsMerged)``: the ``LeadsMerged`` subscriber
  (``nextron.main.build_subscribers``, after ``pipeline``'s). Every Contact of the merged Lead
  moves to the survivor (``updated_at`` now); when the survivor already has a Primary Contact,
  the moved Contacts' Primary flags are cleared. Provenance Records follow their Contact. It
  appends no ``events`` row. Duplicate Contacts on the survivor are kept (Story 2.10 folds
  them). Each moved Contact is then resolved, since the survivor's country may differ.

- ``@command resolve_market(contact_id)`` resolves one Contact, for the later paths that change
  its email, office location or Lead outside ``add_contact`` (Stories 2.10, 2.13, 2.14, 2.16);
  an unknown Contact (an id that isn't a ULID included) raises ``LookupError``.

**Resolving a Contact's Market and timezone (Story 3.2, AD-11).** One routine, ``_resolve``,
serves ``add_contact``, ``reparent_lead`` and ``resolve_market``. It reads the Contact, its Lead
(``queries.discovery.lead``) and the stored Markets (``queries.setup.markets()``), and calls
``nextron.domain.contact_market.resolve_contact_market`` with the stored email, the stored
office (a ``Location``, or ``None`` without an ``office_country``), the Lead's ``country_code``
and ``lead_hq_tz=None`` (Story 2.9 passes the Lead's headquarters zone). It writes
``market_id`` (the chosen Market's row id, or NULL), ``market_basis``, ``tz`` and ``tz_basis``
through ``contacts.fill`` (``updated_at`` now) only when one of them differs, and sets the notes
(joined with a newline, or NULL when there are none) on the Contact's first ``contact``
Provenance Record (by ``retrieved_at``, then ``id``) only when they differ; when it does, it also
clears the notes of the Contact's other ``contact`` records, so only the first holds them (a
later source may supply an earlier ``retrieved_at``). No log line or ``events`` row holds a note
or a place.

**``ContactMarkets(clock, research, queue)``** resolves the Contacts stored before Story 3.2,
through the one-off ``resolve_contact_markets`` job (bulk lane):

- ``@use_case queue_backfill() -> bool``: in one transaction, when any Contact is unresolved
  (``market_basis`` NULL), enqueues ``resolve_contact_markets`` (scope ``global``,
  discriminator ``1``, payload ``{"v": 1}``; a live job with that key absorbs the repeat) and
  logs ``contact_markets_queued`` (with ``new_job``: whether this call created the job). It
  returns whether a job is now queued.
  ``nextron.main`` runs it at each engine start, before the runner starts.
- ``@use_case backfill() -> int``: with no Market stored, it returns 0 and writes nothing, so
  the next start queues the job again. Otherwise it walks the unresolved Contacts by id in
  batches of ``BACKFILL_BATCH`` (200), one transaction per batch, resolving each, and returns
  how many it resolved, logged as ``contact_markets_resolved`` (``count`` only).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.uow import command, current, use_case
from nextron.domain.address_class import AddressCause, AddressClass, assign
from nextron.domain.contact_market import RESOLVE_CONTACT_MARKETS_JOB, resolve_contact_market
from nextron.domain.contacts import ContactCandidate, ProvenanceSource, ProvenanceSubject
from nextron.domain.geo import Location
from nextron.domain.identity import normalize_name
from nextron.domain.ids import is_ulid
from nextron.domain.machines import Transition
from nextron.domain.notifications import LeadsMerged
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

ENTITY: Final = "contacts"
ACTOR: Final = "system:research"
MAX_DETAIL_LENGTH: Final = 2048
# The Contact fields a later source fills when the Contact lacks them, besides the email and
# the office.
TEXT_FIELDS: Final = ("name", "role", "linkedin_url")
BACKFILL_BATCH: Final = 200  # Contacts resolved per transaction by ContactMarkets.backfill

type Assignment = Transition[AddressClass, AddressCause]


@dataclass(frozen=True, slots=True)
class AddedContact:
    """The Contact a candidate became: its id, whether it was created, and whether the
    candidate's email was dropped because the Contact already has another."""

    contact_id: str
    created: bool
    email_dropped: bool


@dataclass(frozen=True, slots=True)
class _Provenance:
    """What every Provenance Record of one ``add_contact`` call carries."""

    source: ProvenanceSource
    detail: str
    retrieved_at: datetime


def _check_arguments(
    candidate: object, source: object, detail: object, retrieved_at: object
) -> None:
    if not isinstance(candidate, ContactCandidate):
        raise TypeError(f"add_contact takes a ContactCandidate, not {type(candidate).__name__}")
    if not isinstance(source, ProvenanceSource):
        raise TypeError(f"add_contact takes a ProvenanceSource, not {type(source).__name__}")
    if not (isinstance(retrieved_at, datetime) and retrieved_at.utcoffset() is not None):
        raise TypeError("add_contact takes retrieved_at as an aware datetime")
    if (
        not isinstance(detail, str)
        or not 1 <= len(detail) <= MAX_DETAIL_LENGTH
        or detail != detail.strip()
    ):
        raise ValueError(
            f"add_contact takes a stripped detail of 1 to {MAX_DETAIL_LENGTH:,} characters"
        )


def _name_key(contact: StoredContact) -> str | None:
    return None if contact.name is None else normalize_name(contact.name)


def _match(
    contacts: tuple[StoredContact, ...], candidate: ContactCandidate
) -> StoredContact | None:
    """The first Contact with the candidate's email, else the first with its name key."""
    if candidate.email is not None:
        for contact in contacts:
            if contact.email == candidate.email:
                return contact
    key = candidate.name_key
    if key is not None:
        for contact in contacts:
            if _name_key(contact) == key:
                return contact
    return None


class Research:
    """Contacts and their Provenance; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def add_contact(
        self,
        lead_id: str,
        candidate: ContactCandidate,
        *,
        source: ProvenanceSource,
        detail: str,
        retrieved_at: datetime,
    ) -> AddedContact:
        """Store ``candidate`` on the Lead ``lead_id``, or fill its matching Contact; see the
        module docstring."""
        _check_arguments(candidate, source, detail, retrieved_at)
        lead = discovery_queries.lead(lead_id)
        if lead is None:
            raise LookupError("add_contact: the Lead is unknown")
        if lead.merged_into is not None:
            raise ValueError("add_contact: the Lead was merged into another Lead")
        email = candidate.email
        assignment = assign(source) if email is not None else None
        provenance = _Provenance(source, detail, retrieved_at)
        now = self._clock.now()
        match = _match(contacts_repo.for_lead(lead.id), candidate)
        office = candidate.office
        if match is None:
            contact_id = contacts_repo.insert(
                lead_id=lead.id,
                name=candidate.name,
                role=candidate.role,
                email=email,
                address_class=None if assignment is None else str(assignment.to),
                linkedin_url=candidate.linkedin_url,
                office_country=None if office is None else office.country_code,
                office_region=None if office is None else office.region,
                at=now,
            )
            self._record(contact_id, ProvenanceSubject.CONTACT, provenance)
            if email is not None and assignment is not None:
                self._address(contact_id, email, assignment, provenance)
            self._resolve(contact_id)
            return AddedContact(contact_id, created=True, email_dropped=False)
        fills: dict[str, str | None] = {
            field: value
            for field in TEXT_FIELDS
            if getattr(match, field) is None and (value := getattr(candidate, field)) is not None
        }
        new_office = office if match.office_country is None else None
        if new_office is not None:
            fills["office_country"] = new_office.country_code
            fills["office_region"] = new_office.region
        filled_text = bool(fills)
        new_email = email if match.email is None else None
        if new_email is not None and assignment is not None:
            fills["email"] = new_email
            fills["address_class"] = str(assignment.to)
        contacts_repo.fill(match.id, now, **fills)
        if filled_text:
            self._record(match.id, ProvenanceSubject.CONTACT, provenance)
        if new_email is not None and assignment is not None:
            self._address(match.id, new_email, assignment, provenance)
        if fills:
            self._resolve(match.id)
        dropped = email is not None and match.email is not None and match.email != email
        return AddedContact(match.id, created=False, email_dropped=dropped)

    @command
    def resolve_market(self, contact_id: str) -> None:
        """Resolve the Contact ``contact_id``'s Market and timezone again; see the module
        docstring. An unknown Contact raises ``LookupError``."""
        self._resolve(contact_id)

    def _resolve(self, contact_id: str) -> None:
        """Resolve the Contact's Market and timezone and write what changed; see the module
        docstring."""
        contact = contacts_repo.get(contact_id) if is_ulid(contact_id) else None
        if contact is None:
            raise LookupError("resolve_market: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        stored = setup_queries.markets()
        office = (
            None
            if contact.office_country is None
            else Location(contact.office_country, contact.office_region)
        )
        resolved = resolve_contact_market(
            email=contact.email,
            office=office,
            lead_country=None if lead is None else lead.country_code,
            lead_hq_tz=None,  # Story 2.9 passes the Lead's headquarters zone
            markets=[market.definition for market in stored],
        )
        chosen = resolved.market
        market_id = (
            None
            if chosen is None
            else next(market.id for market in stored if market.code == chosen.code)
        )
        values: dict[str, str | None] = {
            "market_id": market_id,
            "market_basis": str(resolved.market_basis),
            "tz": resolved.tz,
            "tz_basis": str(resolved.tz_basis),
        }
        if any(getattr(contact, field) != value for field, value in values.items()):
            contacts_repo.fill(contact.id, self._clock.now(), **values)
        notes = "\n".join(resolved.notes) or None
        record = provenance_repo.first_contact_record(contact.id)
        if record is not None and record.notes != notes:
            provenance_repo.set_notes(record.id, notes)
            provenance_repo.clear_other_contact_notes(contact.id, record.id)

    def _record(
        self,
        contact_id: str,
        subject: ProvenanceSubject,
        provenance: _Provenance,
        *,
        address: str | None = None,
        address_class: AddressClass | None = None,
    ) -> None:
        provenance_repo.insert(
            contact_id=contact_id,
            subject=str(subject),
            source_kind=str(provenance.source),
            source_detail=provenance.detail,
            address=address,
            retrieved_at=provenance.retrieved_at,
            address_class=None if address_class is None else str(address_class),
        )

    def _address(
        self, contact_id: str, email: str, assignment: Assignment, provenance: _Provenance
    ) -> None:
        """The ``address`` record and the Address Class ``events`` row of a new address."""
        self._record(
            contact_id,
            ProvenanceSubject.ADDRESS,
            provenance,
            address=email,
            address_class=assignment.to,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=contact_id,
            from_status=None,
            to_status=str(assignment.to),
            cause=str(assignment.cause),
            cause_ref=None,
            actor=ACTOR,
            at=self._clock.now(),
        )

    @command
    def reparent_lead(self, n: LeadsMerged) -> None:
        """Move the merged Lead's Contacts to the survivor; see the module docstring."""
        survivor_has_primary = contacts_repo.has_primary(n.survivor_lead_id)
        moved = contacts_repo.for_lead(n.merged_lead_id)
        contacts_repo.move_to_lead(
            n.merged_lead_id,
            n.survivor_lead_id,
            self._clock.now(),
            clear_primary=survivor_has_primary,
        )
        for contact in moved:
            self._resolve(contact.id)


class ContactMarkets:
    """The one-off resolution of the Contacts stored before Story 3.2; see the module
    docstring."""

    def __init__(self, clock: Clock, research: Research, queue: JobQueue) -> None:
        self._clock = clock
        self._research = research
        self._queue = queue

    @use_case
    def queue_backfill(self) -> bool:
        """Queue ``resolve_contact_markets`` when any Contact is unresolved; whether a job is
        now queued."""
        with current().transaction():
            if not contacts_repo.has_unresolved():
                return False
            enqueued = self._queue.enqueue(
                RESOLVE_CONTACT_MARKETS_JOB, scope="global", discriminator="1", payload={"v": 1}
            )
        log.info("contact_markets_queued", extra={"new_job": enqueued.created})
        return True

    @use_case
    def backfill(self) -> int:
        """Resolve every unresolved Contact, one batch per transaction; how many it resolved (0,
        with nothing written, while no Market is stored)."""
        uow = current()
        with uow.transaction():
            if not setup_queries.markets():
                return 0
        count = 0
        after: str | None = None
        while True:
            with uow.transaction():
                ids = contacts_repo.unresolved_ids(after, BACKFILL_BATCH)
                for contact_id in ids:
                    self._research.resolve_market(contact_id)
            count += len(ids)
            if len(ids) < BACKFILL_BATCH:
                break
            after = ids[-1]
        log.info("contact_markets_resolved", extra={"count": count})
        return count
