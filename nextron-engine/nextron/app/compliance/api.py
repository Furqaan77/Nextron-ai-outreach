"""Public command API of the ``compliance`` service (AD-3, AD-4, AD-15, AD-16, AD-17, FR-41,
FR-50, FR-63, FR-70).

Other services and the delivery layers change ``compliance`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back; the use cases
(Story 3.5's panel actions) open their own transactions.

``Compliance(clock, *, codec=None, ledger=None)`` owns the Suppression List
(``suppression_entries``) and applies the SuppressionEntry machine
(``nextron.domain.suppression_entry``). It is the only code that writes the list
(``tests/contracts/test_suppression_contracts.py``): ``suppress`` adds entries and
``reverse_optout`` voids one (and, after a restore, ``reinstate`` and ``void_reapplied``,
below). An entry is permanent: nothing here, or anywhere,
removes one or ages one out. It also issues the unsubscribe tokens (``unsub_tokens``) through
``codec``, a ``TokenCodecPort``.

- ``@command suppress(subject, scope, source, cause_ref) -> SuppressResult(written, existing)``:

  1. **Arguments.** ``subject`` must be a ``SuppressionSubject`` (``ContactSubject``,
     ``AddressSubject`` or ``DomainSubject``), ``scope`` a ``SuppressionScope`` and ``source`` a
     ``SuppressionSource`` (otherwise ``TypeError``); ``cause_ref`` must be ``None`` or a ULID
     (otherwise ``ValueError``). An ``AddressSubject`` takes scope ``person`` only and a
     ``DomainSubject`` scope ``firm`` only (otherwise ``ValueError``).
  2. **Keys**, each (``kind``, ``value_hash``) once, in order:

     - a ``ContactSubject``: the Contact through ``queries.research.contact`` and its Lead
       through ``queries.discovery.lead`` (either missing raises ``LookupError``, whose
       message holds no id or value); ``person_keys`` of the Contact's email, name and LinkedIn
       URL with the Lead's ``web_domain``, then, for scope ``firm``, ``firm_domain_keys`` of the
       email and the web domain (``nextron.domain.contactability``). A value that can't be
       normalised is no key, so a Contact with no known key gives ``SuppressResult((), ())``:
       nothing is written or published. An erased Contact (Story 3.13: ``erased_at`` set)
       holds tombstones, never keys: its keys are ``erased_person_keys(email)`` (the stored
       address hash as the address key) and, for scope ``firm``, ``firm_domain_keys`` of the
       web domain alone, so a later opt-out still lands.
     - an ``AddressSubject`` (Story 3.5): the address key, then ``person_keys`` of every Contact
       whose stored ``email`` is the address (``queries.research.contacts_with_email``, oldest
       first), each with its Lead's ``web_domain`` (a missing Lead raises ``LookupError``).
     - a ``DomainSubject`` (Story 3.5): the one domain key.
  3. **Writes.** A key that already has an ``active`` entry adds that entry's id to
     ``existing`` and writes nothing. Each other key gets a new ``active`` entry (its
     ``domain_plain`` only for a domain key) and one ``events`` row: entity
     ``suppression_entries``, ``None`` to ``active``, cause ``CREATION[source].cause`` (the
     source itself), the ``cause_ref``, the actor ``ACTORS[source]`` (``operator`` for the
     ``operator``, ``import`` and ``dsr`` sources; ``system:compliance`` for the opt-outs), at
     the clock's now. Its id goes to ``written``, in key order.
  4. **Publish** ``SubjectSuppressed`` (its subscribers, ``nextron.main.build_subscribers``, run
     before commit, and any exception in one rolls the entries back with it), even when every
     key was already ``active``, so a later opt-out still closes the Lead:

     - a ``ContactSubject`` with at least one key: once, ``suppression_entry_id=(written +
       existing)[0]``, with the Contact and its Lead;
     - an ``AddressSubject``: once per matched Contact (with its id and its Lead's), or once
       with no Contact or Lead when none matched; ``suppression_entry_id`` is the address
       key's entry;
     - a ``DomainSubject``: once, with no Contact and ``lead_id`` the Lead whose
       ``web_domain`` is the domain (``queries.discovery.lead_by_web_domain``), followed to the
       end of its merge chain (``queries.discovery.survivor_of``; a merged Lead keeps its own
       ``web_domain``), or that Lead itself when the chain is broken, or ``None`` when no Lead
       holds the domain.

  ``operator``, ``import`` and ``dsr`` aren't opt-out sources, so they leave every Pipeline
  Stage as it is.

- ``@command reverse_optout(entry_id, reason) -> tuple[str, ...]`` (Story 3.5, the Operator's
  Reverse). In
  order: a non-``str`` argument raises ``TypeError``; then ``SuppressionRefused``
  (``nextron.domain.suppression_list``) with ``UNKNOWN_ENTRY`` (an ``entry_id`` that isn't a
  ULID, or no entry has), ``ONLY_RULE_MATCHED`` (a source other than ``reply_rule``,
  ``VOIDABLE_SOURCES``), ``ALREADY_REVERSED`` (a ``voided`` entry), or ``checked_reason``'s
  refusal. Then it applies ``transition(active, void)``, voids the entry (``status``
  ``voided``, ``voided_at`` the clock's now, ``void_reason`` the stripped reason) and appends
  one ``events`` row (``active`` to ``voided``, cause ``operator_reversal``, no ``cause_ref``,
  actor ``operator``; never the reason). It publishes nothing and changes no Pipeline Stage. It
  returns the ids of the other ``active`` ``reply_rule`` entries with the same ``cause_ref``
  (the same opt-out), by ``created_at``, then ``id``, so the panel can show them with their own
  Reverse forms; ``()`` when the entry has no ``cause_ref``.
- ``@command import_values(values) -> ImportCounts(added, already)`` (Story 3.5): for each
  ``EntryValue`` (``nextron.domain.suppression_list``), in order, it counts ``already`` when
  the value's own key has an ``active`` entry and ``added`` otherwise, then calls
  ``suppress(subject, person | firm, import, None)`` either way, so a known Contact's missing
  keys are still written. A repeated value counts as ``already`` the second time.
- ``@command issue_unsub_token(om) -> str`` (Story 3.8) issues the one unsubscribe token of an
  OutboundMessage, in the caller's transaction (Story 5.2 calls it at enqueue). ``om`` has
  ``id``, a ULID (otherwise ``ValueError``), and ``to_address``, a ``str`` (otherwise
  ``TypeError``); with no ``codec`` it raises ``TypeError`` first. Then:

  1. a token record for ``om.id`` raises ``TokenAlreadyIssued`` ("token already issued for this
     message") before any keyring read;
  2. the kid is ``nextron.app.compliance.unsub_kid.active_unsub_kid()``, the
     ``unsub_active_kid`` setting (read there, so this module reads no setting: Eligibility,
     below, must never depend on one);
  3. ``issued_at`` is the clock's now, at whole seconds;
  4. ``codec.encode(kid, addr_hash_of(om.to_address), <issued_at as Unix seconds>)`` (two
     local Credential Manager reads), then one ``unsub_tokens`` row (``token_hash`` of the
     token, ``om.id``, the kid, ``issued_at``), and the token is returned.

  Any failure propagates and dooms the caller's transaction: a missing key
  (``AdapterError`` ``unsub_key_missing``) rolls the enqueue back, so no message goes out
  without a working link. It logs nothing and writes no ``events`` row; only the token's hash
  is stored.
- ``@command unsubscribe_by_token(token, source, received_at) -> UnsubOutcome`` (Story 3.11)
  applies one unsubscribe request, a one-click from the Worker's pending list
  (``nextron.app.compliance.unsub_poll``) or, from Epic 7, a mailto opt-out, in the caller's
  transaction. With no ``codec`` it raises ``TypeError``; ``source`` must be ``one_click`` or
  ``mailto`` (a ``SuppressionSource`` or its text; otherwise ``ValueError``) and
  ``received_at`` an aware ``datetime`` (otherwise ``TypeError``). Then:

  1. a ``token`` that isn't a ``str`` of 108 ``[A-Za-z0-9_-]`` characters is ``invalid``, with
     nothing read;
  2. a ``unsub_requests`` row with the token's ``token_hash`` makes it ``duplicate``;
  3. ``codec.decode(token)``: a ``TokenInvalid`` is ``invalid``, with nothing written (it is
     tried again on the next poll while the Worker keeps it); a Credential Manager
     ``AdapterError`` propagates;
  4. a new request id (a ULID) is the ``cause_ref`` of every entry below. The address key,
     ``erased_person_keys(<addr_hash as hex>)``, is written as ``_write`` writes (an ``events``
     row per new entry);
  5. each Contact holding the address (``queries.research.contacts_with_address_hash``, oldest
     first, erased ones included) gets ``suppress(ContactSubject(id), person, source,
     request_id)``, which writes its other keys and publishes ``SubjectSuppressed`` with its
     Lead, so the opt-out closes the Lead ``lost``. With no such Contact, one
     ``SubjectSuppressed`` with no Contact or Lead is published, as for an ``AddressSubject``;
  6. one ``unsub_requests`` row (that id, the ``token_hash``, ``received_at`` and
     ``processed_at``, the clock's now), and it returns ``applied``.

  It logs nothing; no token, address or address hash is stored, and the ``events`` rows hold
  only entry ids and the request id.

**The erasure ledger (Story 3.16).** ``ledger`` is a ``LedgerRecorder``
(``nextron.app.compliance.ledger.LedgerAppender`` in the engine), also reachable as
``compliance.ledger``. When one is given, every entry ``suppress`` (through ``_write``) or
``reinstate`` writes records one ``Suppressed`` line, ``reverse_optout`` one ``Voided`` line,
and ``Erasures.erase_contact`` one ``Erased`` line when it erased: ``record`` appends them to
the ledger after the transaction commits, so a rolled-back change never reaches it. With no
ledger nothing is recorded.

The two reapply writers (their caller is ``nextron.app.compliance.ledger.ErasureLedger``):

- ``@command reinstate(line: Suppressed) -> str | None``: unless the key (``line.kind``,
  ``line.value_hash``) has an ``active`` entry (``None``), it inserts an ``active`` entry with
  the line's source, ``cause_ref`` the line's entry id and ``domain_plain`` the line's domain,
  and one ``events`` row (``None`` to ``active``, cause ``CREATION[source].cause``, that
  ``cause_ref``, actor ``system:compliance``), and returns the new id. It publishes nothing, so
  no Pipeline Stage changes (the gate reads the list).
- ``@command void_reapplied(entry_id) -> bool``: voids the ``active`` entry ``entry_id``
  (``void_reason`` ``REAPPLIED``, "reapplied after restore") with one ``events`` row
  (``active`` to ``voided``, the machine's cause, no ``cause_ref``, actor
  ``system:compliance``). An unknown or already voided entry is ``False``, and so is one whose
  source can't be voided (only ``reply_rule`` can; it stays suppressed, the safe side).

**The panel's use cases** (Story 3.5; each the one use case of a web POST's unit of work):

- ``@use_case add_value(text) -> AddedValue(written, since, source)``: ``entry_value(text)``
  (``SuppressionRefused`` before any transaction), then one transaction: ``suppress(subject,
  person | firm, operator, None)`` and the typed value's own ``active`` entry, whose
  ``created_at`` and ``source`` it returns with the ``written`` ids.
- ``@use_case import_list(data) -> ImportOutcome(added, already, invalid_lines)``:
  ``parse_import(data)`` (``SuppressionRefused`` before any transaction: nothing is written),
  then ``import_values`` on ``IMPORT_BATCH`` (500) values per transaction, so the write lock is
  never held long. A failure part-way keeps the batches before it; importing the same file
  again counts them ``already``.
- ``@use_case reverse_entry(entry_id, reason) -> tuple[str, ...]``: one transaction running
  ``reverse_optout``, returning its ids.

**The export (Story 3.15, FR-52).** The data subject requests themselves are Story 3.13's
(``nextron.app.compliance.erasure.Erasures``: ``open_dsr``, ``complete_dsr`` and
``export_on_request``, which opens and completes an ``export`` request and then calls
``export_contact`` in the same transaction). ``Compliance`` builds the file:

- ``export_contact(contact_id) -> ContactExport(filename, body)`` only reads, so it runs in a
  caller's transaction or a read-only unit of work alike. It reads each owner's exporter:
  ``queries.research.export_pii(contact_id)`` (``LookupError`` for an unknown Contact),
  ``queries.compliance.export_pii(contact_id, export_hashes(...))`` with the hashes of every
  key held for the Contact (its stored values, its Lead's ``web_domain`` through
  ``queries.discovery.lead``, and its Provenance addresses), ``queries.notify.export_pii``,
  ``queries.playbook.export_pii`` (the Contact's override cases, Story 4.6),
  ``queries.sequencing.export_pii`` (the Contact's Enrollments, Story 4.8) and
  ``eventlog.history("contacts", contact_id)`` (the Address Class history); then
  ``contact_export.assemble`` at the clock's now and ``render``. The file is named
  ``contact_export.filename(contact_id)``.

The file is never written to disk, stored, queued or logged: the web layer hands it to the
browser from memory (``nextron.web.export_page``).

Nothing is logged: the ``events`` rows are the audit, and neither they nor the notifications
hold an address, a name, a URL, a reason or a request reference.

**The contactability gate (AD-16, FR-50, FR-15, FR-63, Story 3.4).** The module-level function
``assert_contactable(subject, channel, purpose) -> Allowed | Blocked`` is the only check of
whether a Lead or a Contact may be reached. It is not a ``@command``: it only reads, so it runs
inside a caller's transaction and in a read-only unit of work alike. Callers reach it as
``compliance_api.assert_contactable`` (a module attribute), so the AD-16 contract test
(``tests/contracts/test_ad16_callers.py``) can replace it with a recording fake.

1. **Arguments, before any read.** ``channel`` and ``purpose`` go through ``channel_of`` and
   ``purpose_of`` (a non-``str`` raises ``TypeError``, an unknown text ``ValueError``); a
   ``subject`` that isn't a ``ContactSubject``, ``NewContactSubject`` or ``NewLeadSubject``
   raises ``TypeError``.
2. **Facts.** A ``ContactSubject``: the Contact (``queries.research.contact``) and its Lead. A
   ``NewContactSubject``: its own values with its Lead. For a Contact, the Lead
   (``queries.discovery.lead``) gives the web domain, and ``queries.pipeline.stage`` the stage.
   A ``NewLeadSubject``: ``LeadFacts(web_domain, mail_domains)`` with no stage. An unknown
   Contact or Lead raises ``LookupError``, and a merged Lead ``ValueError``; no message holds an
   id or a value.
3. **The answer** is ``nextron.domain.contactability.check(facts, channel, purpose,
   queries.compliance.active_keys(lookup_keys(facts)))``: ``Blocked(reasons)`` or
   ``Allowed()``. Nothing is written, cached or logged, and a ``Blocked`` answer is returned,
   never raised, so a caller's transaction stays usable. An erased Contact (Story 3.13) gives
   ``ContactFacts(lead, erased=True)`` with no values, so it is always ``Blocked`` with
   ``contact_erased``.

**Live Eligibility (FR-18, FR-2, FR-51, FR-63, AD-16, NFR-7, Stories 3.6 and 3.7).**
``Compliance(clock).evaluate_eligibility(contact_id) -> Decision`` answers whether a stored
Contact may lawfully receive cold email at the clock's now. It is a plain method, not a
``@command``: it only reads, so it runs in a read-only unit of work (the Lead page renders it)
and inside a caller's transaction alike. Every consumer (the Lead page; Enrollment, Story 4.8;
the send gate, 6.9) calls it, and it is the only caller of
``nextron.domain.eligibility.evaluate``.

1. **Reads.** The Contact (``queries.research.contact``; unknown, a non-ULID id included:
   ``LookupError``), its Lead (``queries.discovery.lead``) and the Lead's stage
   (``queries.pipeline.stage``).
2. **The gate.** ``assert_contactable(ContactSubject(contact_id), "email", "cold")``, looked up
   in this module's namespace at call time, so a recording fake of
   ``compliance_api.assert_contactable`` sees the call. A merged Lead raises its
   ``ValueError``.
3. **The Markets.** ``queries.setup.markets()`` as a read-only ``{id: definition}``; a stored
   Market that no longer decodes (``InvalidMarkets``) makes the rules ``None``, which fails
   closed with ``no_enabled_market``.
4. **Provenance (FR-51, Story 3.7).** ``provenance_recorded(contact.email,
   queries.research.provenance(contact.id))`` (``nextron.domain.contacts``): false, which gives
   ``provenance_missing``, when no source record shows where the Contact or its email address
   came from.
5. **The legal form** (Story 2.12): ``queries.research.legal_form(lead.id)``, through
   ``nextron.domain.legal_form.eligibility_facts(legal_form, legal_form_status)``, gives
   ``EligibilityLead``'s ``legal_form`` and ``legal_form_confirmed``: ``(code, True)`` for a
   ``confirmed`` corporate code and for a ``not_corporate`` code (so the reason is
   ``uk_legal_form_ineligible``); anything else, a Lead never checked included, is unconfirmed
   (``uk_legal_form_unconfirmed`` in a GB Market).
6. **History (FR-63, Story 4.8).** ``history`` is ``queries.sequencing.ended_sequences(
   contact.id)``: the Contact's ended Enrollments that sent a Step, so a Sequence that ended
   without a Reply blocks a re-contact (``recontact_not_allowed``) until FR-63 allows it.
7. **The Decision** is ``evaluate(EligibilityContact(...), EligibilityLead(country_code, stage,
   legal_form=, legal_form_confirmed=), market_rules, clock.now())``. No Event times or
   re-contact date is read yet (Stories 2.9 and Epic 7 pass them), so ``events_first_seen``
   is empty and ``recontact_on`` is ``None`` (every ``not_now`` Lead fails with
   ``not_now_undated``).

Nothing is written, cached or logged: each call reads the current facts again, so an edit to a
Market, a suppression or a stage change shows in the very next Decision.

**Erasure (Story 3.13).** ``nextron.app.compliance.erasure`` holds ``Erasures`` (``open_dsr``,
``complete_dsr``, ``erase_contact``, compliance's ``erase_pii`` hook, the ``erase_on_request``
use case and Story 3.15's ``export_on_request``) and compliance's ``ERASURE`` declarations.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final, Protocol

from nextron.app.compliance.unsub_kid import active_unsub_kid
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.repos.compliance import unsub_requests as unsub_requests_repo
from nextron.db.repos.compliance import unsub_tokens as unsub_tokens_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.contact_export import (
    EVENT_LOG,
    ContactExport,
    assemble,
    export_hashes,
    filename,
    render,
)
from nextron.domain.contactability import (
    AddressSubject,
    Allowed,
    Blocked,
    Channel,
    ContactFacts,
    ContactSubject,
    DomainSubject,
    KeyKind,
    LeadFacts,
    NewContactSubject,
    NewLeadSubject,
    Purpose,
    SuppressionKey,
    SuppressionScope,
    SuppressionSubject,
    channel_of,
    check,
    erased_person_keys,
    firm_domain_keys,
    lookup_keys,
    person_keys,
    purpose_of,
    suppression_key,
)
from nextron.domain.contacts import provenance_recorded
from nextron.domain.eligibility import Decision, EligibilityContact, EligibilityLead, evaluate
from nextron.domain.erasure_ledger import REAPPLIED, LedgerLine, Suppressed, Voided
from nextron.domain.ids import is_ulid
from nextron.domain.legal_form import eligibility_facts
from nextron.domain.markets import InvalidMarkets, MarketDef
from nextron.domain.notifications import SubjectSuppressed
from nextron.domain.suppression_entry import (
    CREATION,
    OPT_OUT_SOURCES,
    VOIDABLE_SOURCES,
    EntryEvent,
    EntryStatus,
    SuppressionSource,
    transition,
)
from nextron.domain.suppression_list import (
    ALREADY_REVERSED,
    ONLY_RULE_MATCHED,
    UNKNOWN_ENTRY,
    EntryValue,
    SuppressionRefused,
    checked_reason,
    entry_value,
    parse_import,
)
from nextron.domain.unsub_requests import UnsubOutcome
from nextron.domain.unsub_token import (
    TOKEN_PATTERN,
    TokenAlreadyIssued,
    TokenInvalid,
    addr_hash_of,
    token_hash,
)
from nextron.ports.clock import Clock
from nextron.ports.token_codec import TokenCodecPort

ENTITY: Final = "suppression_entries"
CONTACTS_ENTITY: Final = "contacts"  # research's Address Class history, read for an export
OPERATOR_ACTOR: Final = "operator"
SYSTEM_ACTOR: Final = "system:compliance"
ACTORS: Final[Mapping[SuppressionSource, str]] = MappingProxyType(
    {
        source: SYSTEM_ACTOR if source in OPT_OUT_SOURCES else OPERATOR_ACTOR
        for source in SuppressionSource
    }
)
IMPORT_BATCH: Final = 500  # values per transaction in import_list
UNSUB_SOURCES: Final = frozenset({SuppressionSource.ONE_CLICK, SuppressionSource.MAILTO})


@dataclass(frozen=True, slots=True)
class SuppressResult:
    """The ids of the entries ``suppress`` wrote, and of the ``active`` ones it found."""

    written: tuple[str, ...]
    existing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportCounts:
    """How many imported values were new to the list, and how many were already on it."""

    added: int
    already: int


@dataclass(frozen=True, slots=True)
class AddedValue:
    """What ``add_value`` did: the ids it wrote, in key order, and when the typed value's own
    key became ``active`` and from which source."""

    written: tuple[str, ...]
    since: datetime
    source: str


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """An import's counts and the numbers of its invalid lines."""

    added: int
    already: int
    invalid_lines: tuple[int, ...]


def _check_arguments(subject: object, scope: object, source: object, cause_ref: object) -> None:
    if not isinstance(subject, SuppressionSubject):
        raise TypeError(
            "suppress takes a ContactSubject, AddressSubject or DomainSubject, not "
            f"{type(subject).__name__}"
        )
    if not isinstance(scope, SuppressionScope):
        raise TypeError(f"suppress takes a SuppressionScope, not {type(scope).__name__}")
    if not isinstance(source, SuppressionSource):
        raise TypeError(f"suppress takes a SuppressionSource, not {type(source).__name__}")
    if not (cause_ref is None or is_ulid(cause_ref)):
        raise ValueError("suppress: cause_ref must be None or a ULID")
    if isinstance(subject, AddressSubject) and scope is not SuppressionScope.PERSON:
        raise ValueError("suppress: an AddressSubject takes scope person only")
    if isinstance(subject, DomainSubject) and scope is not SuppressionScope.FIRM:
        raise ValueError("suppress: a DomainSubject takes scope firm only")


class LedgerRecorder(Protocol):
    """What ``Compliance`` needs of the erasure ledger's appender: ``record(lines)``, called
    inside a transaction, appends them once it commits."""

    def record(self, lines: Sequence[LedgerLine]) -> None: ...


class OutboundAddress(Protocol):
    """What ``issue_unsub_token`` reads of an OutboundMessage: its id and its To address."""

    @property
    def id(self) -> str: ...

    @property
    def to_address(self) -> str: ...


def _unsub_source(source: object) -> SuppressionSource:
    """``source`` as ``one_click`` or ``mailto``; ``ValueError`` for anything else."""
    for allowed in UNSUB_SOURCES:
        if isinstance(source, str) and source == str(allowed):
            return allowed
    raise ValueError("unsubscribe_by_token: source must be one_click or mailto")


def _market_rules() -> Mapping[str, MarketDef] | None:
    """Every stored Market's row id to its current definition, read-only; ``None`` when a
    stored Market no longer decodes (Eligibility then fails closed)."""
    try:
        stored = setup_queries.markets()
    except InvalidMarkets:
        return None
    return MappingProxyType({market.id: market.definition for market in stored})


def _unique(keys: tuple[SuppressionKey, ...]) -> tuple[SuppressionKey, ...]:
    """``keys`` in order, each (``kind``, ``value_hash``) once."""
    seen: set[tuple[str, str]] = set()
    found = []
    for key in keys:
        identity = (str(key.kind), key.value_hash)
        if identity not in seen:
            seen.add(identity)
            found.append(key)
    return tuple(found)


def _scope_of(subject: AddressSubject | DomainSubject) -> SuppressionScope:
    """The one scope a typed subject takes: ``person`` for an address, ``firm`` for a
    domain."""
    return SuppressionScope.PERSON if isinstance(subject, AddressSubject) else SuppressionScope.FIRM


class Compliance:
    """The Suppression List; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        *,
        codec: TokenCodecPort | None = None,
        ledger: LedgerRecorder | None = None,
    ) -> None:
        self._clock = clock
        self._codec = codec
        self._ledger = ledger

    @property
    def ledger(self) -> LedgerRecorder | None:
        """The erasure ledger's recorder, or ``None`` when nothing is recorded."""
        return self._ledger

    def _record(self, lines: Sequence[LedgerLine]) -> None:
        if self._ledger is not None and lines:
            self._ledger.record(lines)

    def _write(
        self,
        keys: tuple[SuppressionKey, ...],
        source: SuppressionSource,
        cause_ref: str | None,
    ) -> tuple[SuppressResult, tuple[str, ...]]:
        """Write an ``active`` entry and its ``events`` row for each key not yet ``active``;
        the result, and every key's entry id in key order."""
        change = CREATION[source]
        now = self._clock.now()
        written: list[str] = []
        existing: list[str] = []
        ids: list[str] = []
        lines: list[LedgerLine] = []
        for key in keys:
            found = entries_repo.active_for(str(key.kind), key.value_hash)
            if found is not None:
                existing.append(found.id)
                ids.append(found.id)
                continue
            entry_id = entries_repo.insert(
                kind=str(key.kind),
                value_hash=key.value_hash,
                domain_plain=key.domain_plain,
                source=str(source),
                cause_ref=cause_ref,
                created_at=now,
            )
            eventlog.append(
                entity=ENTITY,
                entity_id=entry_id,
                from_status=None,
                to_status=str(change.to),
                cause=str(change.cause),
                cause_ref=cause_ref,
                actor=ACTORS[source],
                at=now,
            )
            written.append(entry_id)
            ids.append(entry_id)
            lines.append(
                Suppressed(entry_id, key.kind, key.value_hash, source, now, key.domain_plain)
            )
        self._record(lines)
        return SuppressResult(tuple(written), tuple(existing)), tuple(ids)

    @command
    def suppress(
        self,
        subject: SuppressionSubject,
        scope: SuppressionScope,
        source: SuppressionSource,
        cause_ref: str | None,
    ) -> SuppressResult:
        """Write an ``active`` entry for every known key of ``subject``; see the module
        docstring."""
        _check_arguments(subject, scope, source, cause_ref)
        if isinstance(subject, AddressSubject):
            return self._suppress_address(subject, source, cause_ref)
        if isinstance(subject, DomainSubject):
            return self._suppress_domain(subject, source, cause_ref)
        contact = research_queries.contact(subject.contact_id)
        if contact is None:
            raise LookupError("suppress: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None:
            raise LookupError("suppress: the Contact's Lead is unknown")
        if contact.erased_at is not None:  # tombstones are no keys (Story 3.13)
            keys = erased_person_keys(contact.email)
            if scope is SuppressionScope.FIRM:
                keys += firm_domain_keys(email=None, web_domain=lead.web_domain)
        else:
            keys = person_keys(
                email=contact.email,
                name=contact.name,
                linkedin_url=contact.linkedin_url,
                web_domain=lead.web_domain,
            )
            if scope is SuppressionScope.FIRM:
                keys += firm_domain_keys(email=contact.email, web_domain=lead.web_domain)
        keys = _unique(keys)
        if not keys:
            return SuppressResult((), ())
        result, _ = self._write(keys, source, cause_ref)
        current().publish(
            SubjectSuppressed(
                suppression_entry_id=(result.written + result.existing)[0],
                source=str(source),
                contact_id=contact.id,
                lead_id=lead.id,
            )
        )
        return result

    def _suppress_address(
        self, subject: AddressSubject, source: SuppressionSource, cause_ref: str | None
    ) -> SuppressResult:
        """The address key, then every key of each Contact holding the address."""
        keys = (suppression_key(KeyKind.ADDRESS, subject.address),)
        matched: list[tuple[str | None, str | None]] = []  # (contact id, lead id)
        for contact in research_queries.contacts_with_email(subject.address):
            lead = discovery_queries.lead(contact.lead_id)
            if lead is None:
                raise LookupError("suppress: the Contact's Lead is unknown")
            keys += person_keys(
                email=contact.email,
                name=contact.name,
                linkedin_url=contact.linkedin_url,
                web_domain=lead.web_domain,
            )
            matched.append((contact.id, lead.id))
        result, ids = self._write(_unique(keys), source, cause_ref)
        address_entry = ids[0]  # the address key comes first
        for contact_id, lead_id in matched or [(None, None)]:
            current().publish(
                SubjectSuppressed(
                    suppression_entry_id=address_entry,
                    source=str(source),
                    contact_id=contact_id,
                    lead_id=lead_id,
                )
            )
        return result

    def _suppress_domain(
        self, subject: DomainSubject, source: SuppressionSource, cause_ref: str | None
    ) -> SuppressResult:
        """The one domain key, published with the Lead holding that web domain (its merge
        survivor), if any."""
        result, ids = self._write(
            (suppression_key(KeyKind.DOMAIN, subject.domain),), source, cause_ref
        )
        lead = discovery_queries.lead_by_web_domain(subject.domain)
        if lead is not None:  # a merged Lead keeps its web domain: publish its survivor
            lead = discovery_queries.survivor_of(lead.id) or lead
        current().publish(
            SubjectSuppressed(
                suppression_entry_id=ids[0],
                source=str(source),
                contact_id=None,
                lead_id=None if lead is None else lead.id,
            )
        )
        return result

    @command
    def reverse_optout(self, entry_id: str, reason: str) -> tuple[str, ...]:
        """Void a ``reply_rule`` entry with the Operator's reason; see the module docstring."""
        if not isinstance(entry_id, str) or not isinstance(reason, str):
            raise TypeError("reverse_optout takes an entry id and a reason, each a str")
        entry = entries_repo.get(entry_id) if is_ulid(entry_id) else None
        if entry is None:
            raise SuppressionRefused(UNKNOWN_ENTRY)
        if entry.source not in VOIDABLE_SOURCES:
            raise SuppressionRefused(ONLY_RULE_MATCHED)
        if entry.status == EntryStatus.VOIDED:
            raise SuppressionRefused(ALREADY_REVERSED)
        stated = checked_reason(reason)
        change = transition(EntryStatus(entry.status), EntryEvent.VOID)
        now = self._clock.now()
        if not entries_repo.void(entry.id, now, stated):
            raise SuppressionRefused(ALREADY_REVERSED)
        eventlog.append(
            entity=ENTITY,
            entity_id=entry.id,
            from_status=entry.status,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=None,
            actor=OPERATOR_ACTOR,
            at=now,
        )
        self._record([Voided(entry.id, now)])
        if entry.cause_ref is None:
            return ()
        return entries_repo.active_ids_for_cause(
            source=str(SuppressionSource.REPLY_RULE), cause_ref=entry.cause_ref
        )

    @command
    def reinstate(self, line: Suppressed) -> str | None:
        """Re-insert the entry of a ledger line after a restore; see the module docstring."""
        if not isinstance(line, Suppressed):
            raise TypeError(f"reinstate takes a Suppressed line, not {type(line).__name__}")
        if entries_repo.active_for(str(line.kind), line.value_hash) is not None:
            return None
        change = CREATION[line.source]
        now = self._clock.now()
        entry_id = entries_repo.insert(
            kind=str(line.kind),
            value_hash=line.value_hash,
            domain_plain=line.domain,
            source=str(line.source),
            cause_ref=line.entry_id,
            created_at=now,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=entry_id,
            from_status=None,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=line.entry_id,
            actor=SYSTEM_ACTOR,
            at=now,
        )
        self._record(
            [Suppressed(entry_id, line.kind, line.value_hash, line.source, now, line.domain)]
        )
        return entry_id

    @command
    def void_reapplied(self, entry_id: str) -> bool:
        """Void an entry a ledger line voided, after a restore; see the module docstring."""
        if not isinstance(entry_id, str):
            raise TypeError(f"void_reapplied takes an entry id, not {type(entry_id).__name__}")
        entry = entries_repo.get(entry_id) if is_ulid(entry_id) else None
        if entry is None or entry.status != EntryStatus.ACTIVE:
            return False
        if entry.source not in VOIDABLE_SOURCES:  # the table refuses it: it stays suppressed
            return False
        change = transition(EntryStatus.ACTIVE, EntryEvent.VOID)
        now = self._clock.now()
        if not entries_repo.void(entry.id, now, REAPPLIED):
            return False
        eventlog.append(
            entity=ENTITY,
            entity_id=entry.id,
            from_status=entry.status,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=None,
            actor=SYSTEM_ACTOR,
            at=now,
        )
        return True

    @command
    def import_values(self, values: Iterable[EntryValue]) -> ImportCounts:
        """Suppress each imported value with source ``import``; see the module docstring."""
        listed = tuple(values)
        for value in listed:
            if not isinstance(value, EntryValue):
                raise TypeError(f"import_values takes EntryValues, not {type(value).__name__}")
        added = already = 0
        for value in listed:
            if entries_repo.active_for(str(value.key.kind), value.key.value_hash) is None:
                added += 1
            else:
                already += 1
            self.suppress(
                value.subject, _scope_of(value.subject), SuppressionSource.IMPORT, None
            )
        return ImportCounts(added, already)

    @command
    def issue_unsub_token(self, om: OutboundAddress) -> str:
        """Issue and record the one unsubscribe token of ``om``; see the module docstring."""
        if self._codec is None:
            raise TypeError("issue_unsub_token needs a TokenCodecPort codec")
        om_id = om.id
        if not is_ulid(om_id):
            raise ValueError("issue_unsub_token: om.id must be a ULID")
        address = om.to_address
        if not isinstance(address, str):
            raise TypeError(f"issue_unsub_token: to_address is a str, not {type(address).__name__}")
        if unsub_tokens_repo.for_om(om_id) is not None:
            raise TokenAlreadyIssued()
        kid = active_unsub_kid()
        issued_at = self._clock.now().replace(microsecond=0)
        token = self._codec.encode(kid, addr_hash_of(address), int(issued_at.timestamp()))
        unsub_tokens_repo.insert(
            token_hash=token_hash(token), om_id=om_id, kid=kid, issued_at=issued_at
        )
        return token

    @command
    def unsubscribe_by_token(
        self, token: str, source: SuppressionSource | str, received_at: datetime
    ) -> UnsubOutcome:
        """Apply one unsubscribe request by its token; see the module docstring."""
        if self._codec is None:
            raise TypeError("unsubscribe_by_token needs a TokenCodecPort codec")
        found_source = _unsub_source(source)
        if not isinstance(received_at, datetime) or received_at.utcoffset() is None:
            raise TypeError("unsubscribe_by_token: received_at must be an aware datetime")
        if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
            return UnsubOutcome.INVALID
        hashed = token_hash(token)
        if unsub_requests_repo.for_token_hash(hashed) is not None:
            return UnsubOutcome.DUPLICATE
        decoded = self._codec.decode(token)
        if isinstance(decoded, TokenInvalid):
            return UnsubOutcome.INVALID
        request_id = unsub_requests_repo.new_request_id()
        address_hash = decoded.addr_hash.hex()
        _, ids = self._write(erased_person_keys(address_hash), found_source, request_id)
        matched = research_queries.contacts_with_address_hash(address_hash)
        for contact in matched:
            self.suppress(
                ContactSubject(contact.id), SuppressionScope.PERSON, found_source, request_id
            )
        if not matched:
            current().publish(
                SubjectSuppressed(
                    suppression_entry_id=ids[0],
                    source=str(found_source),
                    contact_id=None,
                    lead_id=None,
                )
            )
        unsub_requests_repo.insert(
            request_id=request_id,
            token_hash=hashed,
            received_at=received_at,
            processed_at=self._clock.now(),
        )
        return UnsubOutcome.APPLIED

    def export_contact(self, contact_id: str) -> ContactExport:
        """Everything held on the Contact as one JSON file; see the module docstring."""
        research = research_queries.export_pii(contact_id)  # LookupError when unknown
        contact = research_queries.contact(contact_id)
        if contact is None:  # just exported, so it is there
            raise LookupError("export_contact: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        hashes = export_hashes(
            email=contact.email,
            name=contact.name,
            linkedin_url=contact.linkedin_url,
            web_domain=None if lead is None else lead.web_domain,
            addresses=[record.address for record in research_queries.provenance(contact_id)],
        )
        document = assemble(
            contact_id,
            self._clock.now(),
            {
                "research": research,
                "compliance": compliance_queries.export_pii(contact_id, hashes),
                "notify": notify_queries.export_pii(contact_id),
                "playbook": playbook_queries.export_pii(contact_id),
                "sequencing": sequencing_queries.export_pii(contact_id),
                EVENT_LOG: {"contact_history": eventlog.history(CONTACTS_ENTITY, contact_id)},
            },
        )
        return ContactExport(filename(contact_id), render(document))

    @use_case
    def add_value(self, text: str) -> AddedValue:
        """Suppress a typed address or domain with source ``operator``; see the module
        docstring."""
        value = entry_value(text)
        with current().transaction():
            result = self.suppress(
                value.subject, _scope_of(value.subject), SuppressionSource.OPERATOR, None
            )
            own = entries_repo.active_for(str(value.key.kind), value.key.value_hash)
        if own is None:  # suppress wrote the key or found it active, in that transaction
            raise LookupError("add_value: the value's own entry is missing")
        return AddedValue(result.written, own.created_at, own.source)

    @use_case
    def import_list(self, data: bytes) -> ImportOutcome:
        """Suppress each valid value of an imported file with source ``import``; see the
        module docstring."""
        plan = parse_import(data)
        values = [value for _, value in plan.values]
        added = already = 0
        for start in range(0, len(values), IMPORT_BATCH):
            with current().transaction():
                counts = self.import_values(values[start : start + IMPORT_BATCH])
            added += counts.added
            already += counts.already
        return ImportOutcome(added, already, plan.invalid_lines)

    @use_case
    def reverse_entry(self, entry_id: str, reason: str) -> tuple[str, ...]:
        """Reverse a rule-matched opt-out; see ``reverse_optout``."""
        with current().transaction():
            return self.reverse_optout(entry_id, reason)

    def evaluate_eligibility(self, contact_id: str) -> Decision:
        """The stored Contact's live cold-email Eligibility; see the module docstring."""
        contact = research_queries.contact(contact_id)
        if contact is None:
            raise LookupError("evaluate_eligibility: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None:
            raise LookupError("evaluate_eligibility: the Contact's Lead is unknown")
        stage = pipeline_queries.stage(lead.id)
        if stage is None:  # the Lead was just read, so its stage is there
            raise LookupError("evaluate_eligibility: the Contact's Lead is unknown")
        # a global looked up at call time: a fake of compliance_api.assert_contactable sees it
        contactable = assert_contactable(ContactSubject(contact.id), "email", "cold")
        stored = research_queries.legal_form(lead.id)
        legal_form, confirmed = eligibility_facts(
            None if stored is None else stored.legal_form,
            None if stored is None else stored.legal_form_status,
        )
        return evaluate(
            EligibilityContact(
                email=contact.email,
                address_class=contact.address_class,
                market_id=contact.market_id,
                market_basis=contact.market_basis,
                office_country=contact.office_country,
                office_region=contact.office_region,
                contactable=contactable,
                provenance_recorded=provenance_recorded(
                    contact.email, research_queries.provenance(contact.id)
                ),
                history=sequencing_queries.ended_sequences(contact.id),
            ),
            EligibilityLead(
                country_code=lead.country_code,
                stage=stage,
                legal_form=legal_form,
                legal_form_confirmed=confirmed,
            ),
            _market_rules(),
            self._clock.now(),
        )


# The contactability gate ------------------------------------------------------------------------

type Subject = ContactSubject | NewContactSubject | NewLeadSubject


def _lead_facts(lead_id: str) -> LeadFacts:
    """The stored Lead's web domain and stage; ``LookupError`` when it is unknown and
    ``ValueError`` when it was merged."""
    lead = discovery_queries.lead(lead_id)
    if lead is None:
        raise LookupError("assert_contactable: the Lead is unknown")
    if lead.merged_into is not None:
        raise ValueError("assert_contactable: the Lead was merged into another Lead")
    stage = pipeline_queries.stage(lead.id)
    if stage is None:  # the Lead was just read, so its stage is there
        raise LookupError("assert_contactable: the Lead is unknown")
    return LeadFacts(lead.web_domain, (), stage)


def _facts(subject: Subject) -> LeadFacts | ContactFacts:
    if isinstance(subject, NewLeadSubject):
        return LeadFacts(subject.web_domain, subject.mail_domains)
    if isinstance(subject, NewContactSubject):
        return ContactFacts(
            _lead_facts(subject.lead_id),
            email=subject.email,
            name=subject.name,
            linkedin_url=subject.linkedin_url,
        )
    contact = research_queries.contact(subject.contact_id)
    if contact is None:
        raise LookupError("assert_contactable: the Contact is unknown")
    if contact.erased_at is not None:  # a tombstone: blocked, and its values are no keys
        return ContactFacts(_lead_facts(contact.lead_id), erased=True)
    return ContactFacts(
        _lead_facts(contact.lead_id),
        email=contact.email,
        name=contact.name,
        linkedin_url=contact.linkedin_url,
    )


def assert_contactable(
    subject: Subject, channel: Channel | str, purpose: Purpose | str
) -> Allowed | Blocked:
    """Whether ``subject`` may be reached on ``channel`` for ``purpose``; see the module
    docstring."""
    found_channel = channel_of(channel)
    found_purpose = purpose_of(purpose)
    if not isinstance(subject, ContactSubject | NewContactSubject | NewLeadSubject):
        raise TypeError(
            "assert_contactable takes a ContactSubject, NewContactSubject or NewLeadSubject, "
            f"not {type(subject).__name__}"
        )
    facts = _facts(subject)
    active = compliance_queries.active_keys(lookup_keys(facts))
    return check(facts, found_channel, found_purpose, active)
