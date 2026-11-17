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
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain import identity
from nextron.domain.ids import is_ulid
from nextron.domain.markets import is_free_webmail

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
