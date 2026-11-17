"""Public command API of the ``research`` service (AD-3, AD-4, AD-7, FR-14, FR-15, FR-51).

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
     email, ``is_primary`` false, ``tz`` and ``market_id`` NULL, created and updated now),
     then a ``contact`` Provenance Record. With an email, an ``address`` record (its
     ``address`` and ``address_class``) and one ``events`` row (``None`` to the class, cause
     ``assign(source).cause``, no ``cause_ref``) follow. ``created=True``.
  4. **A match.** Each of ``name``, ``role``, ``linkedin_url`` and ``email`` the Contact lacks
     is filled; nothing is ever overwritten. A filled name, role or LinkedIn URL adds one
     ``contact`` record; a filled email sets its class and adds the ``address`` record and the
     ``events`` row as in step 3. A candidate email that differs from the Contact's own gives
     ``email_dropped=True`` and is not stored. When nothing is filled, nothing is written.
     ``created=False``.
  5. **Provenance Records** carry ``source_kind`` = ``source``, ``source_detail`` = ``detail``
     and ``retrieved_at``; ``address_class`` is set only on ``address`` records, and ``notes``
     and the eligibility-basis fields are NULL.

- ``@command reparent_lead(n: LeadsMerged)``: the ``LeadsMerged`` subscriber
  (``nextron.main.build_subscribers``, after ``pipeline``'s). Every Contact of the merged Lead
  moves to the survivor (``updated_at`` now); when the survivor already has a Primary Contact,
  the moved Contacts' Primary flags are cleared. Provenance Records follow their Contact. It
  appends no ``events`` row. Duplicate Contacts on the survivor are kept (Story 2.10 folds
  them).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.queries import discovery as discovery_queries
from nextron.db import eventlog
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.uow import command
from nextron.domain.address_class import AddressCause, AddressClass, assign
from nextron.domain.contacts import ContactCandidate, ProvenanceSource, ProvenanceSubject
from nextron.domain.identity import normalize_name
from nextron.domain.machines import Transition
from nextron.domain.notifications import LeadsMerged
from nextron.ports.clock import Clock

ENTITY: Final = "contacts"
ACTOR: Final = "system:research"
MAX_DETAIL_LENGTH: Final = 2048
# The Contact fields a later source fills when the Contact lacks them, besides the email.
TEXT_FIELDS: Final = ("name", "role", "linkedin_url")

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
        if match is None:
            contact_id = contacts_repo.insert(
                lead_id=lead.id,
                name=candidate.name,
                role=candidate.role,
                email=email,
                address_class=None if assignment is None else str(assignment.to),
                linkedin_url=candidate.linkedin_url,
                at=now,
            )
            self._record(contact_id, ProvenanceSubject.CONTACT, provenance)
            if email is not None and assignment is not None:
                self._address(contact_id, email, assignment, provenance)
            return AddedContact(contact_id, created=True, email_dropped=False)
        fills: dict[str, str | None] = {
            field: value
            for field in TEXT_FIELDS
            if getattr(match, field) is None and (value := getattr(candidate, field)) is not None
        }
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
        dropped = email is not None and match.email is not None and match.email != email
        return AddedContact(match.id, created=False, email_dropped=dropped)

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
        contacts_repo.move_to_lead(
            n.merged_lead_id,
            n.survivor_lead_id,
            self._clock.now(),
            clear_primary=survivor_has_primary,
        )
