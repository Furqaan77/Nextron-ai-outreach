"""Suppression keys (FR-50, FR-63, AD-16, AD-17, Story 3.3). Standard library only, plus
``nextron.domain``.

A Suppression List entry never holds a person's address, name or profile URL: it holds the
SHA-256 of a *key*, a value normalised only by ``nextron.domain.identity``, so a person who
said no is matched however their details are written when they reappear, and an erased
person stays matchable.

**Kinds** (``KeyKind``) and their normalised values (``normalised_key(kind, value)``):

=========  ==========================================  ========================================
Kind       ``value``                                   Normalised
=========  ==========================================  ========================================
address    an email address (``str``)                  ``identity.normalize_email(value)``: no
                                                       plus-tag or dot stripping
domain     a domain, website or URL (``str``)          ``identity.registrable_domain(value)``
person     a ``(full_name, web_domain)`` pair          ``identity.normalize_name(full_name)``
                                                       ``+ "|" +``
                                                       ``identity.registrable_domain(web_domain)``
profile    a LinkedIn profile URL (``str``)            ``identity.normalize_linkedin_url(value)``
=========  ==========================================  ========================================

A kind that isn't a ``KeyKind`` value raises ``ValueError``; a wrong value type (a ``str`` for
``person``, a pair for any other kind, anything else for any kind) raises ``TypeError``; a value
the normaliser refuses raises its ``IdentityError``. The person value is a pair, not one joined
text, so a name holding ``|`` can't be split wrongly.

``key_hash(kind, value)`` is the lowercase 64-hex SHA-256 of the UTF-8 bytes of
``kind + ":" + normalised_key(kind, value)``. Story 3.8's ``addr_hash`` is
``bytes.fromhex(key_hash("address", address))``.

``suppression_key(kind, value)`` is the ``SuppressionKey(kind, value_hash, domain_plain)`` an
entry stores: ``domain_plain`` is the normalised domain for the domain kind (a firm's domain
isn't personal data, and the Suppression List panel shows it) and ``None`` for every other kind.

**A Contact's keys.** Both functions take the stored values (``None`` when unknown). A value the
normaliser refuses (``IdentityError``) gives no key: it is "not known" then, and never raises
out of these functions.

- ``person_keys(*, email, name, linkedin_url, web_domain)``, in this order: the address key when
  ``email`` is set; the person key when both ``name`` and ``web_domain`` (the Lead's) are set;
  the profile key when ``linkedin_url`` is set.
- ``firm_domain_keys(*, email, web_domain)`` ("remove us"), in this order: the domain key of the
  registrable domain of the address's domain part; then the Lead's web domain, when it differs.
  A domain that ``nextron.domain.markets.is_free_webmail`` matches is never a key, so a whole
  free-webmail domain is never suppressed.

**The subject and the scope.** ``ContactSubject(contact_id)`` names a stored Contact (a
non-ULID ``contact_id`` raises ``ValueError``); ``SuppressionScope`` is ``person`` (the person's
own keys) or ``firm`` (those plus the firm's domain keys). Stories 3.5 and 3.11 add subject
types for an address or a domain the Operator types and for a token's address hash.

**The contactability gate's core (AD-16, Story 3.4).** ``compliance.assert_contactable`` reads
the facts and the active keys; everything it decides is here.

- ``Channel`` (``email``, ``linkedin``, ``whatsapp``, ``instagram``, ``phone``) and ``Purpose``
  (``cold``, ``follow_up``, ``reply``, ``referral``, ``rebook``, ``recontact``, ``task``,
  ``call``, ``discovery``). ``channel_of(value)`` and ``purpose_of(value)`` take a member or its
  ``str`` value: a value that isn't a ``str`` raises ``TypeError``, an unknown text
  ``ValueError``. They are validated and recorded, but they don't change the answer: an opt-out
  blocks every channel.
- ``BlockReason``, a closed enum, in this order: ``address_suppressed``,
  ``address_domain_suppressed``, ``lead_domain_suppressed``, ``person_suppressed``,
  ``profile_suppressed``, ``lead_lost``, ``lead_disqualified``. ``SUPPRESSION_REASONS`` are the
  first five.
- **Facts.** ``LeadFacts(web_domain, mail_domains=(), stage=None)``: the Lead's web domain
  (``None`` when it has none), its mail domains and its ``PipelineStage`` (``None`` for a Lead
  not stored yet). ``ContactFacts(lead, email=None, name=None, linkedin_url=None)``: a Contact's
  values with its Lead's facts. A wrong type raises ``TypeError``.
- **Subjects** of the app gate, besides ``ContactSubject`` (a stored Contact):
  ``NewContactSubject(lead_id, email=None, name=None, linkedin_url=None)``, a Contact about to
  be stored on the stored Lead ``lead_id`` (a non-ULID ``lead_id`` raises ``ValueError``), and
  ``NewLeadSubject(web_domain, mail_domains=())``, a Lead about to be created. A wrong type
  raises ``TypeError``. Personal values never appear in a ``repr``.
- **Checked keys**, each with the reason it gives, in this order: the address key
  (``address_suppressed``); the address's registrable domain (``address_domain_suppressed``);
  the Lead's web domain, then each mail domain (``lead_domain_suppressed``); the person key, the
  name with the Lead's web domain (``person_suppressed``); the profile key
  (``profile_suppressed``). A free-webmail domain is never a key, and a value that can't be
  normalised gives no key, exactly as for ``person_keys`` and ``firm_domain_keys``.
  ``lookup_keys(facts)`` gives the distinct keys to look up, in that order.
- ``check(facts, channel, purpose, active) -> Allowed() | Blocked(reasons)``: ``active`` is a
  collection of the active (``kind``, ``value_hash``) pairs. Each checked key whose pair is in
  ``active`` gives its reason; a Lead at ``lost`` gives ``lead_lost`` and one at
  ``disqualified`` ``lead_disqualified``. The reasons are distinct and in ``BlockReason``
  order; with none, the answer is ``Allowed()``. A wrong ``facts`` or ``active`` type raises
  ``TypeError``.
- ``Blocked(reasons)`` refuses a ``reasons`` that isn't a tuple (``TypeError``), and an empty
  one, one holding a value that isn't a ``BlockReason``, a repeat or reasons out of order
  (``ValueError``).
"""

import hashlib
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from nextron.domain import identity
from nextron.domain.ids import is_ulid
from nextron.domain.markets import is_free_webmail
from nextron.domain.pipeline_stage import PipelineStage

KIND_SEPARATOR: Final = ":"
PERSON_SEPARATOR: Final = "|"
HASH_LENGTH: Final = 64
_HEX_DIGITS: Final = frozenset("0123456789abcdef")


class KeyKind(StrEnum):
    ADDRESS = "address"
    DOMAIN = "domain"
    PERSON = "person"
    PROFILE = "profile"


class SuppressionScope(StrEnum):
    PERSON = "person"
    FIRM = "firm"


@dataclass(frozen=True, slots=True)
class ContactSubject:
    """A stored Contact to suppress, by its id."""

    contact_id: str

    def __post_init__(self) -> None:
        if not is_ulid(self.contact_id):
            raise ValueError("ContactSubject.contact_id must be a ULID")


@dataclass(frozen=True, slots=True)
class SuppressionKey:
    """One key an entry stores: its kind, its ``value_hash`` and, for the domain kind only,
    the normalised domain ``domain_plain``."""

    kind: KeyKind
    value_hash: str
    domain_plain: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, KeyKind):
            raise TypeError(
                f"SuppressionKey.kind must be a KeyKind, not {type(self.kind).__name__}"
            )
        if not is_hash(self.value_hash):
            raise ValueError("SuppressionKey.value_hash must be 64 lowercase hex characters")
        if (self.kind is KeyKind.DOMAIN) != (self.domain_plain is not None):
            raise ValueError("SuppressionKey.domain_plain is set exactly for the domain kind")
        if self.domain_plain is not None and not isinstance(self.domain_plain, str):
            raise TypeError("SuppressionKey.domain_plain must be a str or None")


def is_hash(value: object) -> bool:
    """Whether ``value`` is a ``key_hash``: 64 lowercase hex characters."""
    return (
        isinstance(value, str)
        and len(value) == HASH_LENGTH
        and all(character in _HEX_DIGITS for character in value)
    )


def _kind(kind: object) -> KeyKind:
    """``kind`` as a ``KeyKind``; ``ValueError`` when it is none."""
    if isinstance(kind, KeyKind):
        return kind
    if isinstance(kind, str):
        try:
            return KeyKind(kind)
        except ValueError:
            pass
    raise ValueError("kind must be address, domain, person or profile")


def _text(value: object, kind: KeyKind) -> str:
    if not isinstance(value, str):
        raise TypeError(f"a {kind} key takes a str, not {type(value).__name__}")
    return value


def normalised_key(kind: KeyKind | str, value: str | tuple[str, str]) -> str:
    """The normalised value of a ``kind`` key; see the module docstring."""
    found = _kind(kind)
    if found is KeyKind.PERSON:
        if not (isinstance(value, tuple) and len(value) == 2):
            raise TypeError(
                f"a person key takes a (full_name, web_domain) pair, not {type(value).__name__}"
            )
        full_name, web_domain = value
        return (
            identity.normalize_name(full_name)
            + PERSON_SEPARATOR
            + identity.registrable_domain(web_domain)
        )
    text = _text(value, found)
    if found is KeyKind.ADDRESS:
        return identity.normalize_email(text)
    if found is KeyKind.DOMAIN:
        return identity.registrable_domain(text)
    return identity.normalize_linkedin_url(text)


def key_hash(kind: KeyKind | str, value: str | tuple[str, str]) -> str:
    """The lowercase hex SHA-256 of ``kind:normalised value``; see the module docstring."""
    found = _kind(kind)
    keyed = f"{found}{KIND_SEPARATOR}{normalised_key(found, value)}"
    return hashlib.sha256(keyed.encode("utf-8")).hexdigest()


def suppression_key(kind: KeyKind | str, value: str | tuple[str, str]) -> SuppressionKey:
    """The ``SuppressionKey`` of a ``kind`` key; see the module docstring."""
    found = _kind(kind)
    domain_plain = normalised_key(found, value) if found is KeyKind.DOMAIN else None
    return SuppressionKey(found, key_hash(found, value), domain_plain)


def _known(kind: KeyKind, value: str | tuple[str, str]) -> SuppressionKey | None:
    """The key, or ``None`` when the normaliser refuses the value."""
    try:
        return suppression_key(kind, value)
    except identity.IdentityError:
        return None


def person_keys(
    *,
    email: str | None,
    name: str | None,
    linkedin_url: str | None,
    web_domain: str | None,
) -> tuple[SuppressionKey, ...]:
    """The person's known keys: address, person, profile; see the module docstring."""
    candidates: list[SuppressionKey | None] = []
    if email is not None:
        candidates.append(_known(KeyKind.ADDRESS, email))
    if name is not None and web_domain is not None:
        candidates.append(_known(KeyKind.PERSON, (name, web_domain)))
    if linkedin_url is not None:
        candidates.append(_known(KeyKind.PROFILE, linkedin_url))
    return tuple(key for key in candidates if key is not None)


def _firm_domain(text: str) -> str | None:
    """The registrable domain of ``text``, or ``None`` when it isn't one or is free webmail."""
    try:
        domain = identity.registrable_domain(text)
    except identity.IdentityError:
        return None
    if is_free_webmail(text) or is_free_webmail(domain):
        return None
    return domain


def _address_domain(email: str) -> str | None:
    """The domain part of the normalised address, or ``None`` when it can't be normalised."""
    try:
        return identity.normalize_email(email).rpartition("@")[2]
    except identity.IdentityError:
        return None


def firm_domain_keys(*, email: str | None, web_domain: str | None) -> tuple[SuppressionKey, ...]:
    """The firm's domain keys: the address's domain, then a differing web domain; never a free
    webmail domain; see the module docstring."""
    domains: list[str] = []
    if email is not None:
        host = _address_domain(_text(email, KeyKind.ADDRESS))
        found = None if host is None else _firm_domain(host)
        if found is not None:
            domains.append(found)
    if web_domain is not None:
        found = _firm_domain(_text(web_domain, KeyKind.DOMAIN))
        if found is not None and found not in domains:
            domains.append(found)
    return tuple(suppression_key(KeyKind.DOMAIN, domain) for domain in domains)


# The contactability gate's core (Story 3.4) -----------------------------------------------------


class Channel(StrEnum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    PHONE = "phone"


class Purpose(StrEnum):
    COLD = "cold"
    FOLLOW_UP = "follow_up"
    REPLY = "reply"
    REFERRAL = "referral"
    REBOOK = "rebook"
    RECONTACT = "recontact"
    TASK = "task"
    CALL = "call"
    DISCOVERY = "discovery"


class BlockReason(StrEnum):
    ADDRESS_SUPPRESSED = "address_suppressed"
    ADDRESS_DOMAIN_SUPPRESSED = "address_domain_suppressed"
    LEAD_DOMAIN_SUPPRESSED = "lead_domain_suppressed"
    PERSON_SUPPRESSED = "person_suppressed"
    PROFILE_SUPPRESSED = "profile_suppressed"
    LEAD_LOST = "lead_lost"
    LEAD_DISQUALIFIED = "lead_disqualified"


SUPPRESSION_REASONS: Final = frozenset(
    {
        BlockReason.ADDRESS_SUPPRESSED,
        BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
        BlockReason.LEAD_DOMAIN_SUPPRESSED,
        BlockReason.PERSON_SUPPRESSED,
        BlockReason.PROFILE_SUPPRESSED,
    }
)
_REASON_ORDER: Final = {reason: index for index, reason in enumerate(BlockReason)}
_STAGE_REASONS: Final = {
    PipelineStage.LOST: BlockReason.LEAD_LOST,
    PipelineStage.DISQUALIFIED: BlockReason.LEAD_DISQUALIFIED,
}


def _member[E: StrEnum](enum: type[E], value: object, name: str) -> E:
    """``value`` as a member of ``enum``: ``TypeError`` for a non-``str``, ``ValueError`` for an
    unknown text (neither message repeats the value)."""
    if isinstance(value, enum):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum)
        raise ValueError(f"{name} must be one of {allowed}") from None


def channel_of(value: object) -> Channel:
    """``value`` (a ``Channel`` or its ``str`` value) as a ``Channel``."""
    return _member(Channel, value, "channel")


def purpose_of(value: object) -> Purpose:
    """``value`` (a ``Purpose`` or its ``str`` value) as a ``Purpose``."""
    return _member(Purpose, value, "purpose")


def _optional_text(value: object, name: str) -> None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{name} must be a str or None, not {type(value).__name__}")


def _domains(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple of str, not {type(value).__name__}")
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{name} must hold str only, not {type(item).__name__}")


@dataclass(frozen=True, slots=True)
class LeadFacts:
    """What the gate knows of a Lead: its web domain, its mail domains and its stage (``None``
    for a Lead not stored yet)."""

    web_domain: str | None
    mail_domains: tuple[str, ...] = ()
    stage: PipelineStage | None = None

    def __post_init__(self) -> None:
        _optional_text(self.web_domain, "LeadFacts.web_domain")
        _domains(self.mail_domains, "LeadFacts.mail_domains")
        if self.stage is not None and not isinstance(self.stage, PipelineStage):
            raise TypeError(
                "LeadFacts.stage must be a PipelineStage or None, "
                f"not {type(self.stage).__name__}"
            )


@dataclass(frozen=True, slots=True)
class ContactFacts:
    """What the gate knows of a Contact: its Lead's facts and its own values."""

    lead: LeadFacts
    email: str | None = field(default=None, repr=False)
    name: str | None = field(default=None, repr=False)
    linkedin_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.lead, LeadFacts):
            raise TypeError(
                f"ContactFacts.lead must be a LeadFacts, not {type(self.lead).__name__}"
            )
        _optional_text(self.email, "ContactFacts.email")
        _optional_text(self.name, "ContactFacts.name")
        _optional_text(self.linkedin_url, "ContactFacts.linkedin_url")


@dataclass(frozen=True, slots=True)
class NewContactSubject:
    """A Contact about to be stored on the stored Lead ``lead_id``, by its values."""

    lead_id: str
    email: str | None = field(default=None, repr=False)
    name: str | None = field(default=None, repr=False)
    linkedin_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not is_ulid(self.lead_id):
            raise ValueError("NewContactSubject.lead_id must be a ULID")
        _optional_text(self.email, "NewContactSubject.email")
        _optional_text(self.name, "NewContactSubject.name")
        _optional_text(self.linkedin_url, "NewContactSubject.linkedin_url")


@dataclass(frozen=True, slots=True)
class NewLeadSubject:
    """A Lead about to be created, by its web domain and mail domains."""

    web_domain: str | None
    mail_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _optional_text(self.web_domain, "NewLeadSubject.web_domain")
        _domains(self.mail_domains, "NewLeadSubject.mail_domains")


@dataclass(frozen=True, slots=True)
class Allowed:
    """The gate's answer when nothing blocks the subject."""


@dataclass(frozen=True, slots=True)
class Blocked:
    """The gate's answer when something blocks the subject: every reason, in ``BlockReason``
    order."""

    reasons: tuple[BlockReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reasons, tuple):
            raise TypeError(
                f"Blocked.reasons must be a tuple, not {type(self.reasons).__name__}"
            )
        if not self.reasons:
            raise ValueError("Blocked needs at least one reason")
        if not all(isinstance(reason, BlockReason) for reason in self.reasons):
            raise ValueError("Blocked.reasons must hold BlockReason members only")
        order = [_REASON_ORDER[reason] for reason in self.reasons]
        if any(later <= earlier for earlier, later in zip(order, order[1:], strict=False)):
            raise ValueError("Blocked.reasons must be distinct and in BlockReason order")


type Facts = LeadFacts | ContactFacts
type Answer = Allowed | Blocked


def _lead_of(facts: object) -> LeadFacts:
    if isinstance(facts, ContactFacts):
        return facts.lead
    if isinstance(facts, LeadFacts):
        return facts
    raise TypeError(f"facts must be a LeadFacts or ContactFacts, not {type(facts).__name__}")


def _domain_key(text: str) -> SuppressionKey | None:
    """The domain key of ``text``'s registrable domain; ``None`` for free webmail or a value
    that isn't a domain."""
    found = _firm_domain(text)
    return None if found is None else suppression_key(KeyKind.DOMAIN, found)


def _checked_keys(facts: object) -> tuple[tuple[BlockReason, SuppressionKey], ...]:
    """Each checked key with the reason it gives, in the module docstring's order."""
    lead = _lead_of(facts)
    contact = facts if isinstance(facts, ContactFacts) else None
    candidates: list[tuple[BlockReason, SuppressionKey | None]] = []
    if contact is not None and contact.email is not None:
        candidates.append(
            (BlockReason.ADDRESS_SUPPRESSED, _known(KeyKind.ADDRESS, contact.email))
        )
        host = _address_domain(contact.email)
        if host is not None:
            candidates.append((BlockReason.ADDRESS_DOMAIN_SUPPRESSED, _domain_key(host)))
    domains = () if lead.web_domain is None else (lead.web_domain,)
    for domain in (*domains, *lead.mail_domains):
        candidates.append((BlockReason.LEAD_DOMAIN_SUPPRESSED, _domain_key(domain)))
    if contact is not None and contact.name is not None and lead.web_domain is not None:
        person = _known(KeyKind.PERSON, (contact.name, lead.web_domain))
        candidates.append((BlockReason.PERSON_SUPPRESSED, person))
    if contact is not None and contact.linkedin_url is not None:
        profile = _known(KeyKind.PROFILE, contact.linkedin_url)
        candidates.append((BlockReason.PROFILE_SUPPRESSED, profile))
    return tuple((reason, key) for reason, key in candidates if key is not None)


def _pair(key: SuppressionKey) -> tuple[str, str]:
    return (str(key.kind), key.value_hash)


def lookup_keys(facts: Facts) -> tuple[SuppressionKey, ...]:
    """The distinct keys to look up for ``facts``, in check order; see the module docstring."""
    seen: set[tuple[str, str]] = set()
    found: list[SuppressionKey] = []
    for _, key in _checked_keys(facts):
        if _pair(key) not in seen:
            seen.add(_pair(key))
            found.append(key)
    return tuple(found)


def check(
    facts: Facts,
    channel: Channel | str,
    purpose: Purpose | str,
    active: Collection[tuple[str, str]],
) -> Answer:
    """Whether ``facts`` may be contacted, given the ``active`` (``kind``, ``value_hash``)
    pairs; see the module docstring."""
    channel_of(channel)
    purpose_of(purpose)
    lead = _lead_of(facts)
    if not isinstance(active, Collection) or isinstance(active, str | bytes):
        raise TypeError(
            "active must be a collection of (kind, value_hash) pairs, "
            f"not {type(active).__name__}"
        )
    reasons = {reason for reason, key in _checked_keys(facts) if _pair(key) in active}
    if lead.stage in _STAGE_REASONS:
        reasons.add(_STAGE_REASONS[lead.stage])
    ordered = tuple(reason for reason in BlockReason if reason in reasons)
    return Blocked(ordered) if ordered else Allowed()
