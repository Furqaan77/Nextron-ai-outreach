"""Contacts and their Provenance (FR-14, FR-15, FR-51, AD-7). Standard library only.

**``ProvenanceSource``** is where a Contact or an address came from, the ``source_kind`` of its
Provenance Record: ``csv``, ``website``, ``signal_inbox``, ``referral``, ``replier``,
``operator``, ``pattern``, ``finder`` and ``eligibility_basis``. **``ProvenanceSubject``** is
what a record is about: the ``contact`` (its name, role or LinkedIn URL) or its ``address``.

**``ContactCandidate(name=None, role=None, email=None, linkedin_url=None, office=None)``** is a
person that ``Research.add_contact`` matches and stores on a Lead. It is frozen and checks itself
when built:

- ``name`` and ``role``: each ``None`` or a ``str`` of 1 to 200 characters, equal to its own
  strip, with no ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character; ``name`` must also give a
  ``normalize_name`` key (``check_name`` and ``check_role`` apply these rules alone);
- ``email``: ``None`` or already normalised (``normalize_email`` returns it unchanged);
- ``linkedin_url``: ``None`` or already normalised (``normalize_linkedin_url`` returns it
  unchanged): a profile URL is stored as a source, never fetched (FR-49);
- ``office`` (Story 3.2): ``None`` or a ``nextron.domain.geo.Location``, the place the person's
  office is in (its country and, in the US, its state); a Contact's Market and timezone are
  resolved from it (``nextron.domain.contact_market``);
- at least one of ``name`` and ``email`` is set.

A problem raises ``InvalidContact(field, reason)`` (code ``invalid_contact``, message
``"<field>: <reason>"``), never showing the value. ``name_key`` is ``normalize_name(name)``, or
``None`` without a name: Contacts on one Lead match by email, else by this key.

**Decision-maker roles (Story 2.10).** A role as a page writes it is compared with a Segment's
titles (``SegmentDef.titles()``):

- **Normalisation** (a role and a title alike): casefold; ``&`` becomes ``and``; the text is
  split on non-alphanumerics. The canonical forms: ``chief operating``, ``executive`` and
  ``financial`` (or ``finance``) ``officer`` become ``coo``, ``ceo`` and ``cfo``, and so do
  ``chief operations officer`` (``coo``) and ``chief executive`` (``ceo``); ``vice
  president`` and ``v p`` become ``vp``; ``svp``, ``evp`` and ``avp`` become ``senior vp``,
  ``executive vp`` and ``assistant vp``; ``ops`` and ``operation`` become ``operations``;
  ``financial`` becomes ``finance``. The words ``of``, ``the``, ``and``, ``for``, ``at``, ``a``
  and ``an`` are dropped.
- **Variants.** A role is also read swapped around its first ``of`` or comma (``Director of
  Finance`` is also ``Finance Director``; ``Vice President, Operations`` also ``Operations Vice
  President``).
- ``title_matches(role, title) -> bool``: the title's canonical words appear contiguously in a
  variant of the role; a one-word ``Controller`` or ``Owner`` doesn't match just after one of
  its junior qualifiers (``TITLE_QUALIFIERS``: "Credit Controller", "Stock Controller",
  "Product Owner", "Account Owner", ...). A role holding ``assistant``, ``deputy``,
  ``former``, ``retired``, ``emeritus``, ``intern``, ``secretary``, ``pa``, ``ea``, ``ex`` or
  ``to`` (``NEVER_WORDS``, after the canonical forms, so an ``AVP`` is an assistant) never
  matches.
- ``role_match(role, segment) -> RoleMatch(title, rank) | None``: the best-ranked title the role
  matches. A ``decision_maker_roles`` title ranks its rank's index (0 is best); a title found
  only in a Signal type's ``preferred_roles`` ranks ``len(decision_maker_roles)``.
- ``is_lawyer_role(role) -> bool``: the role holds ``attorney``, ``lawyer``, ``partner``,
  ``associate``, ``counsel``, ``solicitor`` or ``barrister`` (singular or plural) and none of
  ``NOT_LAWYER_WORDS``: ``assistant``, ``secretary`` or ``paralegal`` (singular or plural),
  ``retired``, ``former``, ``emeritus``, ``ex``, ``summer``, ``intern``, ``marketing``,
  ``business``, ``development``, ``relations`` or ``client``.
- ``role_words(text)``: ``text``'s words as a role is read (the canonical forms applied, commas
  left out), for ``nextron.domain.people``'s check of the words before a role in its quote.

**The Primary Contact (Story 2.10).** ``PrimarySetBy`` says who chose a Lead's Primary Contact
(``system`` or ``operator``). ``choose_primary(candidates, segment, preferred_roles) ->
PrimaryChoice(contact_id, set_by)`` picks it among the Lead's Contacts, each a
``PrimaryCandidate(contact_id, role, created_at, address_class, departed, is_primary,
set_by)``:

1. the operator's Primary (``is_primary``, ``set_by`` ``operator``) stays while its
   ``address_class`` isn't ``invalid``;
2. otherwise only an eligible Contact counts: its role has a ``role_match``, it isn't
   ``departed`` (gone from a re-crawled page) and its ``address_class`` isn't ``invalid``
   (terminal). With ``preferred_roles`` (the strongest live Event's), the eligible Contact
   matching the earliest-listed preferred role (``title_matches``) wins;
3. otherwise the eligible Contact with the best rank wins.

Ties go to the earliest found (``created_at``, then ``contact_id``). With no eligible Contact the
choice is ``NO_PRIMARY`` (``PrimaryChoice(None, None)``): the Lead has no decision-maker. The
engine's choice is ``set_by`` ``system``. ``INVALID_ADDRESS`` restates
``AddressClass.INVALID`` (``nextron.domain.address_class`` imports this module).

``PrimaryRefused(reason)`` (code ``primary_refused``) is why the operator can't make a Contact
Primary: a ``PrimaryRefusal``, ``unknown_lead``, ``merged_lead``, ``not_on_lead``,
``address_invalid`` or ``contact_erased`` (Story 3.13: a tombstone is nobody's Primary). Its
message holds the reason's code only.

**``provenance_recorded(email, records) -> bool``** (FR-51, Story 3.7) answers "where did you
get my details?" for a stored Contact. ``records`` are the Contact's Provenance Records, any
objects with ``subject``, ``source_kind`` and ``address`` (``ProvenanceFacts``). It is true
when both hold:

- some record is a ``contact`` record whose source kind isn't ``eligibility_basis`` (a basis
  record says why the Contact may be emailed, not where it came from);
- ``email`` is ``None``, or some ``address`` record whose source kind isn't
  ``eligibility_basis`` has ``address == email``: the address we would email has its own
  source.

So it fails closed: no record, only basis records, or an email without an ``address`` record for
that exact address all give false (Eligibility's ``provenance_missing``).
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from nextron.domain.errors import DomainError
from nextron.domain.geo import Location
from nextron.domain.identity import (
    IdentityError,
    normalize_email,
    normalize_linkedin_url,
    normalize_name,
)
from nextron.domain.segments import SegmentDef

MAX_TEXT_LENGTH: Final = 200
# control, format, line separator and paragraph separator characters
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})


class ProvenanceSource(StrEnum):
    CSV = "csv"
    WEBSITE = "website"
    SIGNAL_INBOX = "signal_inbox"
    REFERRAL = "referral"
    REPLIER = "replier"
    OPERATOR = "operator"
    PATTERN = "pattern"
    FINDER = "finder"
    ELIGIBILITY_BASIS = "eligibility_basis"


class ProvenanceSubject(StrEnum):
    CONTACT = "contact"
    ADDRESS = "address"


class ProvenanceFacts(Protocol):
    """What ``provenance_recorded`` reads of one Provenance Record (a stored row fits)."""

    @property
    def subject(self) -> str: ...

    @property
    def source_kind(self) -> str: ...

    @property
    def address(self) -> str | None: ...


def provenance_recorded(email: str | None, records: Iterable[ProvenanceFacts]) -> bool:
    """Whether ``records`` show where the Contact, and its ``email`` when it has one, came
    from; see the module docstring."""
    sources = [
        record for record in records if record.source_kind != ProvenanceSource.ELIGIBILITY_BASIS
    ]
    contact = any(record.subject == ProvenanceSubject.CONTACT for record in sources)
    address = email is None or any(
        record.subject == ProvenanceSubject.ADDRESS and record.address == email
        for record in sources
    )
    return contact and address


class InvalidContact(DomainError):
    """A ``ContactCandidate`` field that breaks its rule; the message names the field, never its
    value."""

    code = "invalid_contact"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def _check_text(field: str, value: object) -> None:
    if not isinstance(value, str):
        raise InvalidContact(field, "must be a str or None")
    if not 1 <= len(value) <= MAX_TEXT_LENGTH:
        raise InvalidContact(field, f"must be 1 to {MAX_TEXT_LENGTH} characters")
    if value != value.strip():
        raise InvalidContact(field, "must not start or end with whitespace")
    if any(unicodedata.category(ch) in REFUSED_TEXT_CATEGORIES for ch in value):
        raise InvalidContact(field, "must not hold a control or format character")


def check_name(value: object) -> None:
    """Raise ``InvalidContact("name", ...)`` unless ``value`` is a Contact name: the text rule,
    plus a ``normalize_name`` key."""
    _check_text("name", value)
    try:
        normalize_name(str(value))
    except IdentityError as error:
        raise InvalidContact("name", error.reason) from None


def check_role(value: object) -> None:
    """Raise ``InvalidContact("role", ...)`` unless ``value`` is a Contact role."""
    _check_text("role", value)


def _check_normalised(field: str, value: object, normalise: Callable[[str], str]) -> None:
    if not isinstance(value, str):
        raise InvalidContact(field, "must be a str or None")
    try:
        normalised = normalise(value)
    except IdentityError as error:
        raise InvalidContact(field, error.reason) from None
    if normalised != value:
        raise InvalidContact(field, "is not in normalised form")


@dataclass(frozen=True, slots=True)
class ContactCandidate:
    """A person to store on a Lead; see the module docstring."""

    name: str | None = None
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    office: Location | None = None

    def __post_init__(self) -> None:
        if self.name is not None:
            check_name(self.name)
        if self.role is not None:
            check_role(self.role)
        if self.email is not None:
            _check_normalised("email", self.email, normalize_email)
        if self.linkedin_url is not None:
            _check_normalised("linkedin_url", self.linkedin_url, normalize_linkedin_url)
        if self.office is not None and not isinstance(self.office, Location):
            raise InvalidContact("office", "must be a Location or None")
        if self.name is None and self.email is None:
            raise InvalidContact("identity", "set at least one of name, email")

    @property
    def name_key(self) -> str | None:
        """The name folded for matching, or ``None`` without a name."""
        return None if self.name is None else normalize_name(self.name)


# Decision-maker roles (Story 2.10) -------------------------------------------------------------

INVALID_ADDRESS: Final = "invalid"  # nextron.domain.address_class.AddressClass.INVALID
_WORDS: Final = re.compile(r"[^\W_]+|,")  # alphanumeric runs, and commas for the swap
SWAP_WORDS: Final = frozenset({"of", ","})  # a role is also read swapped around the first one
DROPPED_WORDS: Final = frozenset({"of", "the", "and", "for", "at", "a", "an"})
NEVER_WORDS: Final = frozenset(
    {
        "assistant",
        "deputy",
        "former",
        "retired",
        "emeritus",
        "intern",
        "secretary",
        "pa",
        "ea",
        "ex",
        "to",
    }
)
# Single words first, then phrases, so "chief financial officer" reads "chief finance officer".
SINGLE_FORMS: Final = MappingProxyType(
    {
        "financial": ("finance",),
        "ops": ("operations",),
        "operation": ("operations",),
        "svp": ("senior", "vp"),
        "evp": ("executive", "vp"),
        "avp": ("assistant", "vp"),
    }
)
# Longer phrases first: "chief executive officer" before "chief executive".
PHRASE_FORMS: Final = (
    (("chief", "operating", "officer"), "coo"),
    (("chief", "operations", "officer"), "coo"),
    (("chief", "executive", "officer"), "ceo"),
    (("chief", "executive"), "ceo"),
    (("chief", "finance", "officer"), "cfo"),
    (("vice", "president"), "vp"),
    (("v", "p"), "vp"),
)
# A one-word title that a junior qualifier just before it takes away ("Credit Controller",
# "Product Owner").
TITLE_QUALIFIERS: Final = MappingProxyType(
    {
        "controller": frozenset(
            {
                "credit",
                "document",
                "documents",
                "stock",
                "inventory",
                "quality",
                "project",
                "production",
                "traffic",
                "cost",
                "budget",
                "air",
                "flight",
                "quantity",
                "site",
                "shift",
                "fleet",
                "warehouse",
                "planning",
                "materials",
                "sales",
            }
        ),
        "owner": frozenset(
            {
                "product",
                "process",
                "account",
                "data",
                "system",
                "service",
                "risk",
                "control",
                "content",
                "brand",
                "project",
                "asset",
                "budget",
                "case",
                "channel",
                "platform",
                "program",
                "programme",
                "relationship",
            }
        ),
    }
)
LAWYER_WORDS: Final = frozenset(
    {
        "attorney",
        "attorneys",
        "lawyer",
        "lawyers",
        "partner",
        "partners",
        "associate",
        "associates",
        "counsel",
        "counsels",
        "solicitor",
        "solicitors",
        "barrister",
        "barristers",
    }
)
NOT_LAWYER_WORDS: Final = frozenset(
    {
        "assistant",
        "assistants",
        "secretary",
        "secretaries",
        "paralegal",
        "paralegals",
        # not a practising lawyer, or not a lawyer's work (review pass 1)
        "retired",
        "former",
        "emeritus",
        "ex",
        "summer",
        "intern",
        "marketing",
        "business",
        "development",
        "relations",
        "client",
    }
)


@dataclass(frozen=True, slots=True)
class RoleMatch:
    """The Segment title a role matched and its rank (0 is best)."""

    title: str
    rank: int


def _words(text: str) -> list[str]:
    if not isinstance(text, str):
        raise TypeError("a role or title must be a str")
    return _WORDS.findall(text.casefold().replace("&", " and "))


def _canonical(words: Sequence[str]) -> list[str]:
    """The words with the canonical forms applied (commas and dropped words still in)."""
    single = [part for word in words for part in SINGLE_FORMS.get(word, (word,))]
    out: list[str] = []
    index = 0
    while index < len(single):
        for phrase, form in PHRASE_FORMS:
            if tuple(single[index : index + len(phrase)]) == phrase:
                out.append(form)
                index += len(phrase)
                break
        else:
            out.append(single[index])
            index += 1
    return out


def _kept(words: Iterable[str]) -> tuple[str, ...]:
    return tuple(word for word in words if word != "," and word not in DROPPED_WORDS)


def _variants(role: str) -> tuple[tuple[str, ...], ...] | None:
    """The role's variants as canonical words, or ``None`` for a role that never matches."""
    words = _words(role)
    canonical = _canonical(words)
    if NEVER_WORDS.intersection(canonical):
        return None
    variants = [_kept(canonical)]
    swap = next((index for index, word in enumerate(words) if word in SWAP_WORDS), None)
    if swap is not None and 0 < swap < len(words) - 1:
        variants.append(_kept(_canonical([*words[swap + 1 :], *words[:swap]])))
    return tuple(variants)


def _contains(words: Sequence[str], part: Sequence[str]) -> bool:
    """Whether the title's words ``part`` appear together in ``words``, a one-word title
    (``TITLE_QUALIFIERS``) not just after one of its qualifiers."""
    size = len(part)
    if size == 0:
        return False
    wanted = tuple(part)
    qualifiers = TITLE_QUALIFIERS.get(wanted[0], frozenset()) if size == 1 else frozenset()
    return any(
        tuple(words[index : index + size]) == wanted
        and not (index > 0 and words[index - 1] in qualifiers)
        for index in range(len(words) - size + 1)
    )


def role_words(text: str) -> tuple[str, ...]:
    """``text``'s words as a role is read (casefolded, ``&`` as ``and``, split on
    non-alphanumerics, the canonical forms applied), commas left out; the dropped words stay."""
    return tuple(word for word in _canonical(_words(text)) if word != ",")


def _title_words(title: str) -> tuple[str, ...]:
    return _kept(_canonical(_words(title)))


def title_matches(role: str, title: str) -> bool:
    """Whether ``role`` is the Segment title ``title``; see the module docstring."""
    variants = _variants(role)
    wanted = _title_words(title)
    return variants is not None and any(_contains(variant, wanted) for variant in variants)


def role_match(role: str, segment: SegmentDef) -> RoleMatch | None:
    """The best-ranked title of ``segment`` that ``role`` matches, or ``None``; see the module
    docstring."""
    if not isinstance(segment, SegmentDef):
        raise TypeError("role_match takes a SegmentDef")
    variants = _variants(role)
    if variants is None:
        return None
    ranks = segment.decision_maker_roles
    ranked = [(title, rank) for rank, titles in enumerate(ranks) for title in titles]
    listed = {title.casefold() for title, _ in ranked}
    ranked.extend(
        (title, len(ranks)) for title in segment.titles() if title.casefold() not in listed
    )
    for title, rank in ranked:  # in rank order, so the first match is the best
        wanted = _title_words(title)
        if any(_contains(variant, wanted) for variant in variants):
            return RoleMatch(title, rank)
    return None


def is_lawyer_role(role: str) -> bool:
    """Whether ``role`` is a lawyer's title; see the module docstring."""
    words = set(_words(role))
    return bool(words & LAWYER_WORDS) and not words & NOT_LAWYER_WORDS


# The Primary Contact (Story 2.10) --------------------------------------------------------------


class PrimarySetBy(StrEnum):
    SYSTEM = "system"
    OPERATOR = "operator"


class PrimaryRefusal(StrEnum):
    UNKNOWN_LEAD = "unknown_lead"
    MERGED_LEAD = "merged_lead"
    NOT_ON_LEAD = "not_on_lead"
    ADDRESS_INVALID = "address_invalid"
    CONTACT_ERASED = "contact_erased"  # Story 3.13


class PrimaryRefused(DomainError):
    """The operator can't make that Contact the Lead's Primary; ``reason`` says why."""

    code = "primary_refused"

    def __init__(self, reason: PrimaryRefusal) -> None:
        refusal = PrimaryRefusal(reason)
        super().__init__(f"the Primary Contact wasn't set: {refusal}")
        self.reason = refusal


@dataclass(frozen=True, slots=True)
class PrimaryCandidate:
    """One Contact of the Lead as ``choose_primary`` sees it; its role never shows in the
    ``repr``."""

    contact_id: str
    role: str | None = field(repr=False)
    created_at: datetime
    address_class: str | None
    departed: bool
    is_primary: bool
    set_by: PrimarySetBy | None


@dataclass(frozen=True, slots=True)
class PrimaryChoice:
    """The Lead's Primary Contact and who chose it, or ``(None, None)`` for no
    decision-maker."""

    contact_id: str | None
    set_by: PrimarySetBy | None


NO_PRIMARY: Final = PrimaryChoice(None, None)


def choose_primary(
    candidates: Iterable[PrimaryCandidate], segment: SegmentDef, preferred_roles: Sequence[str]
) -> PrimaryChoice:
    """The Lead's Primary Contact; see the module docstring."""
    found = list(candidates)
    if not all(isinstance(candidate, PrimaryCandidate) for candidate in found):
        raise TypeError("choose_primary takes PrimaryCandidates")
    if isinstance(preferred_roles, str):
        raise TypeError("preferred_roles must be a sequence of titles, not a str")
    ordered = sorted(found, key=lambda candidate: (candidate.created_at, candidate.contact_id))
    for candidate in ordered:
        if (
            candidate.is_primary
            and candidate.set_by is PrimarySetBy.OPERATOR
            and candidate.address_class != INVALID_ADDRESS
        ):
            return PrimaryChoice(candidate.contact_id, PrimarySetBy.OPERATOR)
    eligible: list[tuple[PrimaryCandidate, str, RoleMatch]] = []
    for candidate in ordered:
        role = candidate.role
        if role is None or candidate.departed or candidate.address_class == INVALID_ADDRESS:
            continue
        matched = role_match(role, segment)
        if matched is not None:
            eligible.append((candidate, role, matched))
    for preferred in preferred_roles:
        for candidate, role, _ in eligible:
            if title_matches(role, preferred):
                return PrimaryChoice(candidate.contact_id, PrimarySetBy.SYSTEM)
    if not eligible:
        return NO_PRIMARY
    best = min(eligible, key=lambda found: found[2].rank)  # min keeps the earliest of a tie
    return PrimaryChoice(best[0].contact_id, PrimarySetBy.SYSTEM)
