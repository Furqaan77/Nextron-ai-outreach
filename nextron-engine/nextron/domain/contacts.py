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
"""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain.errors import DomainError
from nextron.domain.geo import Location
from nextron.domain.identity import (
    IdentityError,
    normalize_email,
    normalize_linkedin_url,
    normalize_name,
)

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
