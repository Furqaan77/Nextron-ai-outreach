"""Cold-email Eligibility (FR-18, FR-2, FR-51, FR-63, AD-4, AD-16, NFR-5, NFR-7; Stories 3.6 and
3.7). Standard library and ``nextron.domain`` only.

Eligibility answers whether a Contact may lawfully receive cold email **now**. It is computed
live on every call and never stored: ``compliance.evaluate_eligibility(contact_id)`` gathers the
facts through the owners' read APIs and calls ``evaluate``, which is pure and is called from
nowhere else (``tests/contracts/test_eligibility_contracts.py``).

**Reasons.** ``ReasonKind`` is a closed ``StrEnum``, in this order: ``no_enabled_market``,
``cold_email_not_allowed``, ``uk_legal_form_ineligible``, ``uk_legal_form_unconfirmed``,
``uk_free_webmail``, ``address_not_sendable``, ``provenance_missing`` (Story 3.7),
``address_or_domain_suppressed``, ``asked_not_to_be_contacted``, ``recontact_not_allowed``,
``not_now_until``, ``not_now_undated``, ``lead_closed``, ``contact_erased`` (Story 3.13).
``Reason(kind, text, until=None)`` is one failing check:
``until`` is the re-contact ``date`` of a ``not_now_until`` reason (required there, refused on
every other kind), and ``code`` is ``not_now_until_<YYYY-MM-DD>`` for that kind and the kind's
value for every other. ``text`` is the Operator-facing sentence (the ``*_TEXT`` constants): it
holds Market names, Address Classes, legal-form labels and dates only, never an address, a name
or a domain.

**The Decision.** ``Decision(eligible, basis_rule, reasons)``: ``eligible`` is true exactly when
``reasons`` is empty and exactly when ``basis_rule`` (the rule that makes the Contact eligible)
is set. The reasons have distinct kinds, in ``ReasonKind`` order; ``kinds`` gives them. A
``basis_rule`` longer than ``MAX_BASIS_RULE_LENGTH`` (128, the width of
``provenance_records.basis_rule``) raises ``ValueError``; ``evaluate``'s longest is 113 (a
64-character legal-form code with no label), and a labelled UK basis is 71 to 108.

**``basis_country(basis_rule) -> str | None``** (Story 3.7) is the country a basis rule is for:
``US`` for ``US_BASIS``, ``GB`` for a rule of ``UK_BASIS``'s shape with any non-blank label,
and ``None`` for anything else (another text, ``None``, a non-``str``).
``Research.record_eligibility_basis`` refuses a basis for another country than its Campaign's
Market.

**Facts.**

- ``EndedSequence(last_step_at, ended_at, replied)``: one earlier Sequence of the Contact's
  (Story 4.8 passes the Enrollment history); ``ended_at`` before ``last_step_at`` raises
  ``ValueError``, since it would move FR-63's "first seen after it ended" window earlier.
- ``EligibilityContact(email, address_class, market_id, market_basis, office_country,
  office_region, contactable, provenance_recorded, history=())``: the stored Contact texts as
  they are stored, the gate's answer (``Allowed`` or ``Blocked``, from ``assert_contactable`` on
  ``email``), whether its Provenance Records show where it and its email came from
  (``nextron.domain.contacts.provenance_recorded``; required, a ``bool``) and the
  ``EndedSequence`` history. ``email`` never appears in a ``repr``.
- ``EligibilityLead(country_code, stage, recontact_on=None, legal_form=None,
  legal_form_confirmed=False, events_first_seen=())``: the Lead's country, its
  ``PipelineStage``, the ``date`` a ``not_now`` Lead may be re-contacted, its Companies House
  legal form (a ``[a-z][a-z0-9-]`` code), whether FR-12 Evidence confirms it, and the times its
  Events were first seen (Story 2.9).
- ``market_rules``: ``Mapping[str, MarketDef]``, each stored Market's row id to its current
  definition, or ``None`` when the Markets can't be read.

A wrong type raises ``TypeError`` and a bad value ``ValueError``; neither message holds a value.
Every datetime, ``now`` included, must be timezone-aware.

**``evaluate(contact, lead, market_rules, now) -> Decision``** runs every check and collects
every failing reason; the Contact is eligible only with none.

1. **The Market** gives ``no_enabled_market`` (one text per cause) when the rules can't be read,
   the Contact has no stored Market (the text follows ``market_basis``), its Market is no
   longer stored or is disabled, its stored office isn't a valid ``geo.Location``, or resolving
   it afresh (``resolve_contact_market`` against the current Markets) gives no Market or
   another one. Otherwise the stored Market's current definition applies; without one, step 2
   is skipped.
2. **The Market's rules**, for country ``c`` = the Market's ``country_code``:
   ``cold_email_not_allowed`` unless ``cold_email_allowed`` and ``c`` is ``US`` or ``GB``. When
   ``eligible_legal_forms`` is set or ``c`` is ``GB``: ``uk_legal_form_unconfirmed`` when there
   is no legal form or it isn't confirmed, and ``uk_legal_form_ineligible`` when there is one the
   Market doesn't list. ``uk_free_webmail`` when ``free_webmail_blocked`` (or ``c`` is ``GB``)
   and the email is free webmail (``markets.is_free_webmail``).
3. **The address:** ``address_not_sendable`` unless there is an email whose Address Class is
   ``published`` or ``verified``.
4. **Provenance (FR-51, Story 3.7):** ``provenance_missing`` when ``provenance_recorded`` is
   false: no Provenance Record shows where the Contact, or the email address it would be sent
   to, came from (a data error).
5. **The Suppression List:** a ``Blocked`` answer maps each ``BlockReason`` through
   ``BLOCK_REASON_KINDS``: address and domain keys to ``address_or_domain_suppressed``, person
   and profile keys to ``asked_not_to_be_contacted``, a Lost or Disqualified Lead to
   ``lead_closed``, and an erased Contact (Story 3.13: the gate blocks a tombstone whatever the
   list holds) to ``contact_erased``, so an erased Contact is never eligible. Every
   ``BlockReason`` gives a reason: a ``Blocked`` answer is never eligible.
6. **FR-63:** ``recontact_not_allowed`` unless ``recontact_allowed(history, events_first_seen,
   now)``.
7. **Not Now:** a ``not_now`` Lead gives ``not_now_undated`` with no ``recontact_on``, and
   ``not_now_until`` while ``recontact_on`` is after ``now``'s UTC date.
8. **Closed:** ``lead_closed`` for a ``lost`` or ``disqualified`` Lead.
9. **The basis** of an eligible Contact: ``US_BASIS`` in a US Market; ``UK_BASIS`` with the legal
   form's ``LEGAL_FORM_LABELS`` label (``nextron.domain.legal_form``, Story 2.12) or its code
   in a GB Market.

**``recontact_allowed(history, events_first_seen, now)``** (FR-63) is true when every
``EndedSequence`` that ended without a Reply had its last Step at least ``RECONTACT_WAIT`` (90
days) before ``now`` and some Event was first seen after it ended, up to ``now``. An empty
history passes. A Sequence that ended with a Reply is outside this predicate.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.address_class import AddressClass
from nextron.domain.contact_market import MarketBasis, resolve_contact_market
from nextron.domain.contactability import Allowed, Blocked, BlockReason
from nextron.domain.geo import InvalidLocation, Location
from nextron.domain.legal_form import LEGAL_FORM_LABELS, LEGAL_FORM_PATTERN
from nextron.domain.markets import (
    COLD_EMAIL_COUNTRIES,
    GB,
    US,
    MarketDef,
    is_free_webmail,
)
from nextron.domain.pipeline_stage import CLOSED_STAGES, PipelineStage

RECONTACT_WAIT: Final = timedelta(days=90)  # FR-63: since an unanswered Sequence's last Step
# provenance_records.basis_rule's width (nextron.db.models.research.BASIS_RULE_LENGTH)
MAX_BASIS_RULE_LENGTH: Final = 128


class ReasonKind(StrEnum):
    NO_ENABLED_MARKET = "no_enabled_market"
    COLD_EMAIL_NOT_ALLOWED = "cold_email_not_allowed"
    UK_LEGAL_FORM_INELIGIBLE = "uk_legal_form_ineligible"
    UK_LEGAL_FORM_UNCONFIRMED = "uk_legal_form_unconfirmed"
    UK_FREE_WEBMAIL = "uk_free_webmail"
    ADDRESS_NOT_SENDABLE = "address_not_sendable"
    PROVENANCE_MISSING = "provenance_missing"
    ADDRESS_OR_DOMAIN_SUPPRESSED = "address_or_domain_suppressed"
    ASKED_NOT_TO_BE_CONTACTED = "asked_not_to_be_contacted"
    RECONTACT_NOT_ALLOWED = "recontact_not_allowed"
    NOT_NOW_UNTIL = "not_now_until"
    NOT_NOW_UNDATED = "not_now_undated"
    LEAD_CLOSED = "lead_closed"
    CONTACT_ERASED = "contact_erased"  # Story 3.13


_KIND_ORDER: Final = {kind: index for index, kind in enumerate(ReasonKind)}

# The texts. ``{market}`` is a Market's name; nothing else about a person is ever filled in.
NO_MARKET_RULES_TEXT: Final = "No enabled Market: the Market rules can't be read"
NO_MARKET_UNRESOLVED_TEXT: Final = (
    "No enabled Market: the Contact's Market hasn't been resolved yet"
)
NO_MARKET_BASIS_TEXTS: Final[Mapping[str, str]] = MappingProxyType(
    {
        MarketBasis.CCTLD_OUTSIDE_MARKETS: (
            "No enabled Market: the email address is on a country-code domain outside the "
            "enabled Markets"
        ),
        MarketBasis.LOCATION_OUTSIDE_MARKETS: (
            "No enabled Market: the office location or the Lead's country isn't in an enabled "
            "Market"
        ),
        MarketBasis.LOCATION_UNKNOWN: (
            "No enabled Market: neither the office location nor the Lead's country is known"
        ),
        MarketBasis.CONFLICT_STRICTER: (
            "No enabled Market: the location and the email's country-code domain point to "
            "equally strict Markets"
        ),
    }
)
NO_MARKET_OTHER_TEXT: Final = "No enabled Market applies to this Contact"
NO_MARKET_GONE_TEXT: Final = "No enabled Market: the Contact's Market is no longer stored"
NO_MARKET_DISABLED_TEXT: Final = "No enabled Market: the {market} Market is disabled"
NO_MARKET_OFFICE_TEXT: Final = "No enabled Market: the Contact's office location can't be read"
NO_MARKET_STALE_TEXT: Final = (
    "No enabled Market: the Contact's Market no longer matches the current Market rules"
)
COLD_EMAIL_TEXT: Final = "The {market} Market doesn't allow cold email"
LEGAL_FORM_UNCONFIRMED_TEXT: Final = (
    "The Lead's legal form isn't confirmed: no Evidence links the Lead to its Companies House "
    "record"
)
LEGAL_FORM_INELIGIBLE_TEXT: Final = (
    "The Lead's legal form ({label}) isn't a corporate subscriber the {market} Market allows"
)
FREE_WEBMAIL_TEXT: Final = (
    "The address is on a free-webmail domain; the {market} Market allows corporate addresses "
    "only"
)
ADDRESS_CLASS_TEXT: Final = (
    "The address is {address_class}; only Published or Verified addresses can be emailed"
)
NO_EMAIL_TEXT: Final = "The Contact has no email address"
NO_ADDRESS_CLASS_TEXT: Final = "The address has no known Address Class"
PROVENANCE_MISSING_TEXT: Final = (
    "Provenance missing: no Provenance Record shows where this Contact or its email address "
    "came from"
)
SUPPRESSED_TEXT: Final = "The address or its domain is on the Suppression List"
ASKED_TEXT: Final = "This person asked not to be contacted (Suppression List)"
RECONTACT_TEXT: Final = (
    "An earlier Sequence ended without a Reply: re-contact needs 90 days since its last Step "
    "and an Event first seen after it ended"
)
NOT_NOW_UNTIL_TEXT: Final = "Parked Not Now until {until}"
NOT_NOW_UNDATED_TEXT: Final = "Parked Not Now with no re-contact date recorded"
LEAD_CLOSED_TEXT: Final = "The Lead is closed ({stage})"
CONTACT_ERASED_TEXT: Final = "This Contact was erased: it is never contacted"  # Story 3.13
US_BASIS: Final = "US: cold email allowed (CAN-SPAM)"
UK_BASIS: Final = "UK: corporate subscriber ({label}), legitimate interests"
_UK_BASIS_PREFIX, _UK_BASIS_SUFFIX = UK_BASIS.split("{label}")

SENDABLE_CLASSES: Final = frozenset({AddressClass.PUBLISHED, AddressClass.VERIFIED})
ADDRESS_CLASS_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        AddressClass.PUBLISHED: "Published",
        AddressClass.GUESSED: "Guessed",
        AddressClass.VERIFIED: "Verified",
        AddressClass.INVALID: "Invalid",
    }
)
CLOSED_STAGE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {PipelineStage.LOST: "Lost", PipelineStage.DISQUALIFIED: "Disqualified"}
)
# Every ``BlockReason`` of the contactability gate, as the reason it gives here.
BLOCK_REASON_KINDS: Final[Mapping[BlockReason, ReasonKind]] = MappingProxyType(
    {
        BlockReason.ADDRESS_SUPPRESSED: ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        BlockReason.ADDRESS_DOMAIN_SUPPRESSED: ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        BlockReason.LEAD_DOMAIN_SUPPRESSED: ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED,
        BlockReason.PERSON_SUPPRESSED: ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
        BlockReason.PROFILE_SUPPRESSED: ReasonKind.ASKED_NOT_TO_BE_CONTACTED,
        BlockReason.LEAD_LOST: ReasonKind.LEAD_CLOSED,
        BlockReason.LEAD_DISQUALIFIED: ReasonKind.LEAD_CLOSED,
        BlockReason.CONTACT_ERASED: ReasonKind.CONTACT_ERASED,  # Story 3.13
    }
)
_BLOCK_STAGES: Final[Mapping[BlockReason, PipelineStage]] = MappingProxyType(
    {
        BlockReason.LEAD_LOST: PipelineStage.LOST,
        BlockReason.LEAD_DISQUALIFIED: PipelineStage.DISQUALIFIED,
    }
)


# Checks of the arguments --------------------------------------------------------------------------


def _type_name(value: object) -> str:
    return type(value).__name__


def _optional_text(value: object, name: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a str or None, not {_type_name(value)}")


def _aware(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime, not {_type_name(value)}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _bool(value: object, name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, not {_type_name(value)}")


def _day(value: object, name: str) -> None:
    """``value`` must be a ``date`` that isn't a ``datetime``."""
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{name} must be a date, not {_type_name(value)}")


# The reasons and the Decision ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reason:
    """One failing check: its kind, its Operator-facing text and, for ``not_now_until`` only,
    the re-contact date."""

    kind: ReasonKind
    text: str
    until: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReasonKind):
            raise TypeError(f"Reason.kind must be a ReasonKind, not {_type_name(self.kind)}")
        if not isinstance(self.text, str):
            raise TypeError(f"Reason.text must be a str, not {_type_name(self.text)}")
        if not self.text.strip():
            raise ValueError("Reason.text must not be blank")
        if self.kind is ReasonKind.NOT_NOW_UNTIL:
            if self.until is None:
                raise ValueError("a not_now_until Reason needs its until date")
            _day(self.until, "Reason.until")
        elif self.until is not None:
            raise ValueError("Reason.until is set only for a not_now_until Reason")

    @property
    def code(self) -> str:
        """``not_now_until_<YYYY-MM-DD>`` for a ``not_now_until`` reason, else the kind's
        value."""
        if self.until is not None:
            return f"{self.kind.value}_{self.until.isoformat()}"
        return self.kind.value


@dataclass(frozen=True, slots=True)
class Decision:
    """The live Eligibility of one Contact; see the module docstring."""

    eligible: bool
    basis_rule: str | None
    reasons: tuple[Reason, ...]

    def __post_init__(self) -> None:
        _bool(self.eligible, "Decision.eligible")
        _optional_text(self.basis_rule, "Decision.basis_rule")
        if not isinstance(self.reasons, tuple):
            raise TypeError(f"Decision.reasons must be a tuple, not {_type_name(self.reasons)}")
        if not all(isinstance(reason, Reason) for reason in self.reasons):
            raise TypeError("Decision.reasons must hold Reason values only")
        if self.basis_rule is not None and not self.basis_rule.strip():
            raise ValueError("Decision.basis_rule must not be blank")
        if self.basis_rule is not None and len(self.basis_rule) > MAX_BASIS_RULE_LENGTH:
            raise ValueError(
                f"Decision.basis_rule must be at most {MAX_BASIS_RULE_LENGTH} characters"
            )
        if not (self.eligible == (not self.reasons) == (self.basis_rule is not None)):
            raise ValueError(
                "a Decision is eligible exactly when it has no reason and exactly when it has "
                "a basis rule"
            )
        order = [_KIND_ORDER[reason.kind] for reason in self.reasons]
        if any(later <= earlier for earlier, later in zip(order, order[1:], strict=False)):
            raise ValueError("Decision.reasons must have distinct kinds, in ReasonKind order")

    @property
    def kinds(self) -> tuple[ReasonKind, ...]:
        """The reasons' kinds, in order."""
        return tuple(reason.kind for reason in self.reasons)


# The facts ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EndedSequence:
    """One earlier Sequence of the Contact's: when its last Step went out, when it ended and
    whether it ended with a Reply."""

    last_step_at: datetime
    ended_at: datetime
    replied: bool

    def __post_init__(self) -> None:
        _aware(self.last_step_at, "EndedSequence.last_step_at")
        _aware(self.ended_at, "EndedSequence.ended_at")
        _bool(self.replied, "EndedSequence.replied")
        if self.ended_at < self.last_step_at:
            raise ValueError("EndedSequence.ended_at must not be before its last_step_at")


def _history(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple, not {_type_name(value)}")
    if not all(isinstance(item, EndedSequence) for item in value):
        raise TypeError(f"{name} must hold EndedSequence values only")


def _times(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple, not {_type_name(value)}")
    for item in value:
        _aware(item, f"{name}[]")


@dataclass(frozen=True, slots=True)
class EligibilityContact:
    """A stored Contact's facts for ``evaluate``; see the module docstring."""

    email: str | None = field(repr=False)
    address_class: str | None
    market_id: str | None
    market_basis: str | None
    office_country: str | None
    office_region: str | None
    contactable: Allowed | Blocked
    provenance_recorded: bool
    history: tuple[EndedSequence, ...] = ()

    def __post_init__(self) -> None:
        _optional_text(self.email, "EligibilityContact.email")
        _optional_text(self.address_class, "EligibilityContact.address_class")
        _optional_text(self.market_id, "EligibilityContact.market_id")
        _optional_text(self.market_basis, "EligibilityContact.market_basis")
        _optional_text(self.office_country, "EligibilityContact.office_country")
        _optional_text(self.office_region, "EligibilityContact.office_region")
        if not isinstance(self.contactable, Allowed | Blocked):
            raise TypeError(
                "EligibilityContact.contactable must be Allowed or Blocked, not "
                f"{_type_name(self.contactable)}"
            )
        _bool(self.provenance_recorded, "EligibilityContact.provenance_recorded")
        _history(self.history, "EligibilityContact.history")


@dataclass(frozen=True, slots=True)
class EligibilityLead:
    """A Lead's facts for ``evaluate``; see the module docstring."""

    country_code: str | None
    stage: PipelineStage
    recontact_on: date | None = None
    legal_form: str | None = None
    legal_form_confirmed: bool = False
    events_first_seen: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        _optional_text(self.country_code, "EligibilityLead.country_code")
        if not isinstance(self.stage, PipelineStage):
            raise TypeError(
                f"EligibilityLead.stage must be a PipelineStage, not {_type_name(self.stage)}"
            )
        if self.recontact_on is not None:
            _day(self.recontact_on, "EligibilityLead.recontact_on")
        _optional_text(self.legal_form, "EligibilityLead.legal_form")
        if self.legal_form is not None and LEGAL_FORM_PATTERN.fullmatch(self.legal_form) is None:
            raise ValueError("EligibilityLead.legal_form must be a Companies House company type")
        _bool(self.legal_form_confirmed, "EligibilityLead.legal_form_confirmed")
        _times(self.events_first_seen, "EligibilityLead.events_first_seen")


def _market_rules(value: object) -> Mapping[str, MarketDef] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"market_rules must be a Mapping or None, not {_type_name(value)}")
    for key, market in value.items():
        if not isinstance(key, str):
            raise TypeError(f"market_rules keys must be str, not {_type_name(key)}")
        if not isinstance(market, MarketDef):
            raise TypeError(f"market_rules values must be MarketDef, not {_type_name(market)}")
    return value


# FR-63 --------------------------------------------------------------------------------------------


def recontact_allowed(
    history: tuple[EndedSequence, ...], events_first_seen: tuple[datetime, ...], now: datetime
) -> bool:
    """Whether every Sequence that ended without a Reply allows a re-contact at ``now``; see the
    module docstring."""
    _history(history, "history")
    _times(events_first_seen, "events_first_seen")
    _aware(now, "now")
    for sequence in history:
        if sequence.replied:
            continue
        if now - sequence.last_step_at < RECONTACT_WAIT:
            return False
        if not any(sequence.ended_at < seen <= now for seen in events_first_seen):
            return False
    return True


# The checks ---------------------------------------------------------------------------------------


def _no_market(text: str) -> tuple[None, Reason]:
    return None, Reason(ReasonKind.NO_ENABLED_MARKET, text)


def _office(contact: EligibilityContact) -> Location | None:
    """The stored office; ``InvalidLocation`` when it can't be read (a region with no country
    included, which the ``contacts`` checks also refuse)."""
    if contact.office_country is None:
        if contact.office_region is not None:
            raise InvalidLocation("region", "is set without a country")
        return None
    return Location(contact.office_country, contact.office_region)


def _market(
    contact: EligibilityContact,
    lead: EligibilityLead,
    market_rules: Mapping[str, MarketDef] | None,
) -> tuple[MarketDef | None, Reason | None]:
    """The Contact's Market, confirmed live, or the ``no_enabled_market`` reason."""
    if market_rules is None:
        return _no_market(NO_MARKET_RULES_TEXT)
    if contact.market_id is None:
        if contact.market_basis is None:
            return _no_market(NO_MARKET_UNRESOLVED_TEXT)
        return _no_market(NO_MARKET_BASIS_TEXTS.get(contact.market_basis, NO_MARKET_OTHER_TEXT))
    market = market_rules.get(contact.market_id)
    if market is None:
        return _no_market(NO_MARKET_GONE_TEXT)
    if not market.enabled:
        return _no_market(NO_MARKET_DISABLED_TEXT.format(market=market.name))
    try:
        office = _office(contact)
    except InvalidLocation:
        return _no_market(NO_MARKET_OFFICE_TEXT)
    fresh = resolve_contact_market(
        email=contact.email,
        office=office,
        lead_country=lead.country_code,
        lead_hq_tz=None,
        markets=market_rules.values(),
    )
    if fresh.market is None or fresh.market.code != market.code:
        return _no_market(NO_MARKET_STALE_TEXT)
    return market, None


def _market_reasons(
    market: MarketDef, contact: EligibilityContact, lead: EligibilityLead
) -> list[Reason]:
    """The failing rules of the Contact's Market."""
    reasons = []
    country = market.country_code
    if not (market.cold_email_allowed and country in COLD_EMAIL_COUNTRIES):
        reasons.append(
            Reason(ReasonKind.COLD_EMAIL_NOT_ALLOWED, COLD_EMAIL_TEXT.format(market=market.name))
        )
    if market.eligible_legal_forms is not None or country == GB:
        listed = market.eligible_legal_forms or ()
        if lead.legal_form is not None and lead.legal_form not in listed:
            text = LEGAL_FORM_INELIGIBLE_TEXT.format(
                label=_legal_form_label(lead.legal_form), market=market.name
            )
            reasons.append(Reason(ReasonKind.UK_LEGAL_FORM_INELIGIBLE, text))
        if lead.legal_form is None or not lead.legal_form_confirmed:
            unconfirmed = Reason(ReasonKind.UK_LEGAL_FORM_UNCONFIRMED, LEGAL_FORM_UNCONFIRMED_TEXT)
            reasons.append(unconfirmed)
    if (
        (market.free_webmail_blocked or country == GB)
        and contact.email is not None
        and is_free_webmail(contact.email)
    ):
        reasons.append(
            Reason(ReasonKind.UK_FREE_WEBMAIL, FREE_WEBMAIL_TEXT.format(market=market.name))
        )
    return reasons


def _address_reason(contact: EligibilityContact) -> Reason | None:
    """``address_not_sendable`` unless the Contact has a Published or Verified email."""
    if contact.email is None:
        text = NO_EMAIL_TEXT
    elif contact.address_class not in ADDRESS_CLASS_LABELS:
        text = NO_ADDRESS_CLASS_TEXT
    elif contact.address_class in SENDABLE_CLASSES:
        return None
    else:
        label = ADDRESS_CLASS_LABELS[contact.address_class]
        text = ADDRESS_CLASS_TEXT.format(address_class=label)
    return Reason(ReasonKind.ADDRESS_NOT_SENDABLE, text)


def _provenance_reason(contact: EligibilityContact) -> Reason | None:
    """``provenance_missing`` unless the Contact's Provenance Records show where it came from."""
    if contact.provenance_recorded:
        return None
    return Reason(ReasonKind.PROVENANCE_MISSING, PROVENANCE_MISSING_TEXT)


def _closed_reason(lead: EligibilityLead, blocked: tuple[BlockReason, ...]) -> Reason | None:
    """``lead_closed`` for a closed stage or a gate's closed-Lead reason, naming the stage."""
    if lead.stage in CLOSED_STAGES:
        stage = lead.stage
    else:
        closing = [_BLOCK_STAGES[reason] for reason in blocked if reason in _BLOCK_STAGES]
        if not closing:
            return None
        stage = closing[0]
    return Reason(ReasonKind.LEAD_CLOSED, LEAD_CLOSED_TEXT.format(stage=CLOSED_STAGE_LABELS[stage]))


def _suppression_reasons(blocked: tuple[BlockReason, ...]) -> list[Reason]:
    """The Suppression List and erasure reasons of the gate's answer (its closed-Lead reasons
    are ``_closed_reason``'s)."""
    texts = {
        ReasonKind.ADDRESS_OR_DOMAIN_SUPPRESSED: SUPPRESSED_TEXT,
        ReasonKind.ASKED_NOT_TO_BE_CONTACTED: ASKED_TEXT,
        ReasonKind.CONTACT_ERASED: CONTACT_ERASED_TEXT,  # Story 3.13
    }
    kinds = {BLOCK_REASON_KINDS[reason] for reason in blocked}
    return [Reason(kind, text) for kind, text in texts.items() if kind in kinds]


def _not_now_reason(lead: EligibilityLead, now: datetime) -> Reason | None:
    if lead.stage is not PipelineStage.NOT_NOW:
        return None
    if lead.recontact_on is None:
        return Reason(ReasonKind.NOT_NOW_UNDATED, NOT_NOW_UNDATED_TEXT)
    if lead.recontact_on > now.astimezone(UTC).date():
        text = NOT_NOW_UNTIL_TEXT.format(until=lead.recontact_on.isoformat())
        return Reason(ReasonKind.NOT_NOW_UNTIL, text, until=lead.recontact_on)
    return None


def _legal_form_label(code: str) -> str:
    return LEGAL_FORM_LABELS.get(code, code)


def basis_country(basis_rule: object) -> str | None:
    """The country ``basis_rule`` is for: ``US``, ``GB`` or ``None``; see the module
    docstring."""
    if not isinstance(basis_rule, str):
        return None
    if basis_rule == US_BASIS:
        return US
    if (
        len(basis_rule) > len(_UK_BASIS_PREFIX) + len(_UK_BASIS_SUFFIX)
        and basis_rule.startswith(_UK_BASIS_PREFIX)
        and basis_rule.endswith(_UK_BASIS_SUFFIX)
        and basis_rule[len(_UK_BASIS_PREFIX) : -len(_UK_BASIS_SUFFIX)].strip()
    ):
        return GB
    return None


def _basis_rule(market: MarketDef | None, lead: EligibilityLead) -> str | None:
    """The rule that makes a Contact with no failing check eligible in ``market``: by then the
    Market is a US one, or a GB one with a listed, confirmed legal form."""
    if market is None:
        return None
    if market.country_code == US:
        return US_BASIS
    if market.country_code == GB and lead.legal_form is not None:
        return UK_BASIS.format(label=_legal_form_label(lead.legal_form))
    return None


def evaluate(
    contact: EligibilityContact,
    lead: EligibilityLead,
    market_rules: Mapping[str, MarketDef] | None,
    now: datetime,
) -> Decision:
    """The Contact's live Eligibility; see the module docstring."""
    if not isinstance(contact, EligibilityContact):
        raise TypeError(f"evaluate takes an EligibilityContact, not {_type_name(contact)}")
    if not isinstance(lead, EligibilityLead):
        raise TypeError(f"evaluate takes an EligibilityLead, not {_type_name(lead)}")
    rules = _market_rules(market_rules)
    _aware(now, "now")
    blocked = contact.contactable.reasons if isinstance(contact.contactable, Blocked) else ()
    found: list[Reason] = []
    market, no_market = _market(contact, lead, rules)
    if no_market is not None:
        found.append(no_market)
    if market is not None:
        found += _market_reasons(market, contact, lead)
    found += [
        reason
        for reason in (_address_reason(contact), _provenance_reason(contact))
        if reason is not None
    ]
    found += _suppression_reasons(blocked)
    if not recontact_allowed(contact.history, lead.events_first_seen, now):
        found.append(Reason(ReasonKind.RECONTACT_NOT_ALLOWED, RECONTACT_TEXT))
    found += [
        reason
        for reason in (_not_now_reason(lead, now), _closed_reason(lead, blocked))
        if reason is not None
    ]
    reasons = tuple(sorted(found, key=lambda reason: _KIND_ORDER[reason.kind]))
    if reasons:
        return Decision(False, None, reasons)
    # No reason means a live US or GB Market; a missing basis would make Decision raise.
    basis = _basis_rule(market, lead)
    return Decision(basis is not None, basis, ())
