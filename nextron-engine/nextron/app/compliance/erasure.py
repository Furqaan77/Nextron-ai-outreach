"""Erasure of a Contact on request (FR-52, FR-50, AD-3, AD-4, AD-16, AD-17, NFR-3, Story 3.13),
part of the ``compliance`` service's public command API.

``Erasures(clock, compliance)`` owns the data subject requests (``data_subject_requests``, the
DataSubjectRequest machine of ``nextron.domain.data_subject_request``) and erases a Contact:
every personal-data column the registry marks (``nextron.db.pii_registry``) becomes its
tombstone, no row is deleted, and for a data subject request the person's hashed keys stay on
the Suppression List so they are never contacted again. ``compliance`` is the ``Compliance``
whose ``suppress`` writes those keys.

- ``@command open_dsr(contact_id, kind, request_ref) -> str`` opens a request (for this story's
  erasure and for later callers):

  1. **Arguments.** ``contact_id`` and ``request_ref`` must be ``str`` and ``kind`` a
     ``RequestKind`` or its ``str`` value (otherwise ``TypeError``); a ``contact_id`` that isn't
     a ULID, an unknown ``kind`` text or a ``request_ref`` that ``check_request_ref`` refuses
     raises ``ValueError``; an unknown Contact raises ``LookupError``. ``check_request_ref`` gets
     the Contact's name (``None`` once the Contact is erased: a tombstone is no name). No
     message holds a value.
  2. **Idempotent.** An ``open`` request with the same Contact, kind and reference returns its
     id and writes nothing.
  3. **Otherwise** it inserts an ``open`` request (``opened_at`` now) and one ``events`` row:
     entity ``data_subject_requests``, ``None`` to ``open``, cause ``opened``, ``cause_ref`` the
     Contact, actor ``operator``.

- ``@command erase_contact(contact_id, request_ref, cause) -> ErasureResult(contact_id,
  request_id, erased)``, in the caller's transaction:

  1. ``cause`` goes through ``ErasureCause`` (a non-``str`` raises ``TypeError``, an unknown text
     ``ValueError``). ``dsr`` needs a ``request_ref`` that ``check_request_ref`` accepts;
     ``retention`` needs ``None`` (else ``ValueError``). A ``contact_id`` that isn't a ``str``
     raises ``TypeError``.
  2. An unknown Contact raises ``LookupError``.
  3. For ``dsr``, the ``open`` ``erase`` request of the Contact and reference is found, or
     ``open_dsr`` opens one, and ``compliance.suppress(ContactSubject, PERSON, DSR,
     cause_ref=<request id>)`` writes every key known for the person (address, person and
     profile: OQ-A10 keeps the person and profile hashes as suppression-only keys; for a
     Contact erased already, by retention say, its stored address hash). ``retention`` opens no
     request (``request_id`` is ``None``) and writes no key.
  4. **Already erased.** Nothing more is published or tombstoned; the request, if any, is
     completed; ``erased=False``.
  5. **Otherwise** ``ContactErased(contact_id, cause)`` is published: its subscribers
     (``nextron.main.build_subscribers``) are each owner's ``erase_pii`` hook, this one first
     and research's second, all in this transaction. The Contact is read again, and unless its
     ``erased_at`` is set it raises ``RuntimeError("erasure incomplete")`` (fail closed: the
     transaction rolls back). Last, the request is completed: ``completed``, ``completed_at``
     now, and one ``events`` row ``open`` to ``completed``, cause ``completed``, ``cause_ref``
     the Contact, actor ``operator``. ``erased=True``. When ``compliance.ledger`` is set
     (Story 3.16), one ``Erased(contact_id, cause, erased_at)`` line is recorded, and
     appended to the erasure ledger after the commit.

  Any exception (a hook's included) dooms the caller's transaction, so nothing is half erased.

- ``@command erase_pii(n: ContactErased)``: compliance's own hook, registered first so it reads
  the Contact while it still holds its values. It reads the Contact and its Lead (either
  unknown raises ``LookupError``; an erased Contact returns), and for each of the person's keys
  (``person_keys`` of the email, name and LinkedIn URL with the Lead's web domain) and the
  firm's domain keys (``firm_domain_keys`` of the email and the web domain: a "remove us" void
  reason may name the person) it replaces the ``void_reason`` of every ``voided`` entry with
  ``[erased]``; the key hashes stay.
  ``ERASURE`` declares ``suppression_entries`` ``tombstoned`` and ``data_subject_requests``
  ``kept``: the request is the proof of erasure, and its reference holds no address or name.

- ``@use_case erase_on_request(contact_id, request_ref) -> ErasureResult`` (the Lead page's
  "Erase (data subject request)"): the first transaction runs ``open_dsr(contact_id, erase,
  request_ref)`` and then ``compliance.suppress(ContactSubject, PERSON, DSR, cause_ref=<the
  request>)``, and commits, so the request exists and the person is suppressed whatever
  happens next. The second runs ``erase_contact(contact_id, request_ref, dsr)`` (its
  ``suppress`` finds those keys ``active``); an exception rolls it back, the request stays
  ``open`` and the ``dsr`` entries stay (a retry with the same reference reuses the request),
  and the exception propagates.
  After the commit it logs ``contact_erased`` (``contact_id``, ``request_id``, ``erased``).

Nothing logged, published or written to ``events`` holds a name, an address, a URL or the
reference.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from nextron.app.compliance.api import Compliance
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.db import eventlog
from nextron.db.repos.compliance import data_subject_requests as requests_repo
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.contactability import (
    ContactSubject,
    SuppressionScope,
    firm_domain_keys,
    person_keys,
)
from nextron.domain.data_subject_request import (
    CREATION,
    RequestEvent,
    RequestKind,
    RequestStatus,
    check_request_ref,
    transition,
)
from nextron.domain.erasure import REDACTED, Coverage, ErasureCause, TableErasure
from nextron.domain.erasure_ledger import Erased
from nextron.domain.ids import is_ulid
from nextron.domain.notifications import ContactErased
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

ENTITY: Final = "data_subject_requests"
OPERATOR_ACTOR: Final = "operator"
CONTACT_ERASED: Final = "contact_erased"
ERASURE_INCOMPLETE: Final = "erasure incomplete"

ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "suppression_entries": TableErasure(
            Coverage.TOMBSTONED,
            "the void reason of every voided entry on the person's keys becomes [erased]; the "
            "key hashes stay, so the person stays suppressed",
        ),
        "data_subject_requests": TableErasure(
            Coverage.KEPT,
            "the proof of erasure: its reference holds no address or name (check_request_ref) "
            "and the Lead page shows it",
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ErasureResult:
    """What ``erase_contact`` did: the Contact, its request (``None`` for retention) and whether
    this call erased it (``False`` when it was erased already)."""

    contact_id: str
    request_id: str | None
    erased: bool


def _kind(kind: object) -> RequestKind:
    if isinstance(kind, RequestKind):
        return kind
    if not isinstance(kind, str):
        raise TypeError(f"kind must be a RequestKind or a str, not {type(kind).__name__}")
    try:
        return RequestKind(kind)
    except ValueError:
        raise ValueError("kind must be erase or export") from None


def _cause(cause: object) -> ErasureCause:
    if isinstance(cause, ErasureCause):
        return cause
    if not isinstance(cause, str):
        raise TypeError(f"cause must be an ErasureCause or a str, not {type(cause).__name__}")
    try:
        return ErasureCause(cause)
    except ValueError:
        raise ValueError("cause must be dsr or retention") from None


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    return value


class Erasures:
    """Data subject requests and the erasure of a Contact; see the module docstring."""

    def __init__(self, clock: Clock, compliance: Compliance) -> None:
        self._clock = clock
        self._compliance = compliance

    @command
    def open_dsr(self, contact_id: str, kind: RequestKind | str, request_ref: str) -> str:
        """Open (or find) the request; see the module docstring."""
        _text(contact_id, "contact_id")
        _text(request_ref, "request_ref")
        found_kind = _kind(kind)
        if not is_ulid(contact_id):
            raise ValueError("open_dsr: contact_id must be a ULID")
        check_request_ref(request_ref, name=None)
        contact = research_queries.contact(contact_id)
        if contact is None:
            raise LookupError("open_dsr: the Contact is unknown")
        check_request_ref(request_ref, name=None if contact.erased_at is not None else contact.name)
        existing = requests_repo.open_for(contact.id, str(found_kind), request_ref)
        if existing is not None:
            return existing.id
        now = self._clock.now()
        request_id = requests_repo.insert_open(
            contact_id=contact.id, kind=str(found_kind), request_ref=request_ref, opened_at=now
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=request_id,
            from_status=None,
            to_status=str(CREATION.to),
            cause=str(CREATION.cause),
            cause_ref=contact.id,
            actor=OPERATOR_ACTOR,
            at=now,
        )
        return request_id

    @command
    def erase_contact(
        self, contact_id: str, request_ref: str | None, cause: ErasureCause | str
    ) -> ErasureResult:
        """Erase the Contact ``contact_id``; see the module docstring."""
        found_cause = _cause(cause)
        _text(contact_id, "contact_id")
        ref: str | None = None
        if found_cause is ErasureCause.DSR:
            ref = _text(request_ref, "request_ref")
            check_request_ref(ref, name=None)
        elif request_ref is not None:
            raise ValueError("erase_contact: a retention erasure takes no request reference")
        contact = research_queries.contact(contact_id)
        if contact is None:
            raise LookupError("erase_contact: the Contact is unknown")
        request_id = None
        if ref is not None:
            request_id = self.open_dsr(contact.id, RequestKind.ERASE, ref)
            # before the already-erased check: a Contact retention erased gets its dsr key too
            self._suppress(contact.id, request_id)
        if contact.erased_at is not None:
            if request_id is not None:
                self._complete(request_id, contact.id)
            return ErasureResult(contact.id, request_id, erased=False)
        current().publish(ContactErased(contact_id=contact.id, cause=str(found_cause)))
        after = research_queries.contact(contact.id)
        if after is None or after.erased_at is None:
            raise RuntimeError(ERASURE_INCOMPLETE)
        if request_id is not None:
            self._complete(request_id, contact.id)
        ledger = self._compliance.ledger
        if ledger is not None:  # Story 3.16: appended once the transaction commits
            ledger.record([Erased(contact.id, found_cause, after.erased_at)])
        return ErasureResult(contact.id, request_id, erased=True)

    def _suppress(self, contact_id: str, request_id: str) -> None:
        """Suppress every key known for the person, with source ``dsr``."""
        self._compliance.suppress(
            ContactSubject(contact_id),
            SuppressionScope.PERSON,
            SuppressionSource.DSR,
            request_id,
        )

    def _complete(self, request_id: str, contact_id: str) -> None:
        """Move the request ``request_id`` to ``completed`` and record it."""
        stored = requests_repo.get(request_id)
        if stored is None:
            raise LookupError("erase_contact: the request is unknown")
        change = transition(RequestStatus(stored.status), RequestEvent.COMPLETE)
        now = self._clock.now()
        requests_repo.complete(request_id, now)
        eventlog.append(
            entity=ENTITY,
            entity_id=request_id,
            from_status=stored.status,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=contact_id,
            actor=OPERATOR_ACTOR,
            at=now,
        )

    @command
    def erase_pii(self, n: ContactErased) -> None:
        """Compliance's erasure hook: redact the void reasons on the person's keys; see the
        module docstring."""
        _cause(n.cause)
        contact = research_queries.contact(n.contact_id)
        if contact is None:
            raise LookupError("erase_pii: the Contact is unknown")
        if contact.erased_at is not None:
            return
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None:
            raise LookupError("erase_pii: the Contact's Lead is unknown")
        keys = person_keys(
            email=contact.email,
            name=contact.name,
            linkedin_url=contact.linkedin_url,
            web_domain=lead.web_domain,
        ) + firm_domain_keys(email=contact.email, web_domain=lead.web_domain)
        for key in keys:
            for entry in entries_repo.voided_for(str(key.kind), key.value_hash):
                if entry.void_reason != REDACTED:
                    entries_repo.redact_void_reason(entry.id)

    @use_case
    def erase_on_request(self, contact_id: str, request_ref: str) -> ErasureResult:
        """Open the request, commit, then erase the Contact; see the module docstring."""
        uow = current()
        with uow.transaction():  # recorded and suppressed, whatever happens next
            request_id = self.open_dsr(contact_id, RequestKind.ERASE, request_ref)
            self._suppress(contact_id, request_id)
        with uow.transaction():
            result = self.erase_contact(contact_id, request_ref, ErasureCause.DSR)
        log.info(
            CONTACT_ERASED,
            extra={
                "contact_id": result.contact_id,
                "request_id": result.request_id,
                "erased": result.erased,
            },
        )
        return result
