"""Public command API of the ``compliance`` service (AD-3, AD-4, AD-16, AD-17, FR-50, FR-63).

Other services and the delivery layers change ``compliance`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Compliance(clock)`` owns the Suppression List (``suppression_entries``) and applies the
SuppressionEntry machine (``nextron.domain.suppression_entry``). Its ``suppress`` is the only
code that writes the list (``tests/contracts/test_suppression_contracts.py``). An entry is
permanent: nothing here, or anywhere, removes one or ages one out.

- ``@command suppress(subject, scope, source, cause_ref) -> SuppressResult(written, existing)``:

  1. **Arguments.** ``subject`` must be a ``ContactSubject``, ``scope`` a ``SuppressionScope``
     and ``source`` a ``SuppressionSource`` (otherwise ``TypeError``); ``cause_ref`` must be
     ``None`` or a ULID (otherwise ``ValueError``).
  2. **Reads.** The Contact through ``queries.research.contact`` and its Lead through
     ``queries.discovery.lead``; either missing raises ``LookupError``, whose message holds no
     id or value.
  3. **Keys.** ``person_keys`` of the Contact's email, name and LinkedIn URL with the Lead's
     ``web_domain``, then, for scope ``firm``, ``firm_domain_keys`` of the email and the web
     domain (``nextron.domain.contactability``), each (``kind``, ``value_hash``) once. A value
     that can't be normalised is no key, so a Contact with no known key gives
     ``SuppressResult((), ())``: nothing is written or published.
  4. **Writes.** A key that already has an ``active`` entry adds that entry's id to
     ``existing`` and writes nothing. Each other key gets a new ``active`` entry (its
     ``domain_plain`` only for a domain key) and one ``events`` row: entity
     ``suppression_entries``, ``None`` to ``active``, cause ``CREATION[source].cause`` (the
     source itself), the ``cause_ref``, the actor ``ACTORS[source]`` (``operator`` for the
     ``operator``, ``import`` and ``dsr`` sources; ``system:compliance`` for the opt-outs), at
     the clock's now. Its id goes to ``written``, in key order.
  5. **Publish.** With at least one key, ``SubjectSuppressed(suppression_entry_id=(written +
     existing)[0], source, contact_id, lead_id)`` is published, even when every key was already
     ``active``, so a later opt-out still closes the Lead. Its subscribers
     (``nextron.main.build_subscribers``) run before commit, and any exception in one rolls the
     entries back with it.

  Nothing is logged: the ``events`` rows are the audit, and neither they nor the notification
  hold an address, a name or a URL.

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
   never raised, so a caller's transaction stays usable.

**Live Eligibility (FR-18, FR-2, FR-63, AD-16, NFR-7, Story 3.6).** ``Compliance(clock)
.evaluate_eligibility(contact_id) -> Decision`` answers whether a stored Contact may lawfully
receive cold email at the clock's now. It is a plain method, not a ``@command``: it only reads,
so it runs in a read-only unit of work (the Lead page renders it) and inside a caller's
transaction alike. Every consumer (the Lead page; Enrollment, Story 4.8; the send gate, 6.9)
calls it, and it is the only caller of ``nextron.domain.eligibility.evaluate``.

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
4. **The Decision** is ``evaluate(EligibilityContact(...), EligibilityLead(country_code,
   stage), market_rules, clock.now())``. No Enrollment history, Event times, legal form or
   re-contact date is read yet (Stories 4.8, 2.9, 2.12 and Epic 7 pass them), so ``history``
   and ``events_first_seen`` are empty, ``legal_form`` is ``None`` (every UK Contact fails
   with ``uk_legal_form_unconfirmed``) and ``recontact_on`` is ``None`` (every ``not_now`` Lead
   fails with ``not_now_undated``).

Nothing is written, cached or logged: each call reads the current facts again, so an edit to a
Market, a suppression or a stage change shows in the very next Decision.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import command, current
from nextron.domain.contactability import (
    Allowed,
    Blocked,
    Channel,
    ContactFacts,
    ContactSubject,
    LeadFacts,
    NewContactSubject,
    NewLeadSubject,
    Purpose,
    SuppressionKey,
    SuppressionScope,
    channel_of,
    check,
    firm_domain_keys,
    lookup_keys,
    person_keys,
    purpose_of,
)
from nextron.domain.eligibility import Decision, EligibilityContact, EligibilityLead, evaluate
from nextron.domain.ids import is_ulid
from nextron.domain.markets import InvalidMarkets, MarketDef
from nextron.domain.notifications import SubjectSuppressed
from nextron.domain.suppression_entry import CREATION, OPT_OUT_SOURCES, SuppressionSource
from nextron.ports.clock import Clock

ENTITY: Final = "suppression_entries"
OPERATOR_ACTOR: Final = "operator"
SYSTEM_ACTOR: Final = "system:compliance"
ACTORS: Final[Mapping[SuppressionSource, str]] = MappingProxyType(
    {
        source: SYSTEM_ACTOR if source in OPT_OUT_SOURCES else OPERATOR_ACTOR
        for source in SuppressionSource
    }
)


@dataclass(frozen=True, slots=True)
class SuppressResult:
    """The ids of the entries ``suppress`` wrote, and of the ``active`` ones it found."""

    written: tuple[str, ...]
    existing: tuple[str, ...]


def _check_arguments(subject: object, scope: object, source: object, cause_ref: object) -> None:
    if not isinstance(subject, ContactSubject):
        raise TypeError(f"suppress takes a ContactSubject, not {type(subject).__name__}")
    if not isinstance(scope, SuppressionScope):
        raise TypeError(f"suppress takes a SuppressionScope, not {type(scope).__name__}")
    if not isinstance(source, SuppressionSource):
        raise TypeError(f"suppress takes a SuppressionSource, not {type(source).__name__}")
    if not (cause_ref is None or is_ulid(cause_ref)):
        raise ValueError("suppress: cause_ref must be None or a ULID")


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


class Compliance:
    """The Suppression List; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def suppress(
        self,
        subject: ContactSubject,
        scope: SuppressionScope,
        source: SuppressionSource,
        cause_ref: str | None,
    ) -> SuppressResult:
        """Write an ``active`` entry for every known key of ``subject``; see the module
        docstring."""
        _check_arguments(subject, scope, source, cause_ref)
        contact = research_queries.contact(subject.contact_id)
        if contact is None:
            raise LookupError("suppress: the Contact is unknown")
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None:
            raise LookupError("suppress: the Contact's Lead is unknown")
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
        change = CREATION[source]
        now = self._clock.now()
        written: list[str] = []
        existing: list[str] = []
        for key in keys:
            found = entries_repo.active_for(str(key.kind), key.value_hash)
            if found is not None:
                existing.append(found.id)
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
        result = SuppressResult(tuple(written), tuple(existing))
        current().publish(
            SubjectSuppressed(
                suppression_entry_id=(result.written + result.existing)[0],
                source=str(source),
                contact_id=contact.id,
                lead_id=lead.id,
            )
        )
        return result

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
        return evaluate(
            EligibilityContact(
                email=contact.email,
                address_class=contact.address_class,
                market_id=contact.market_id,
                market_basis=contact.market_basis,
                office_country=contact.office_country,
                office_region=contact.office_region,
                contactable=contactable,
            ),
            EligibilityLead(country_code=lead.country_code, stage=stage),
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
