"""Erasure tombstones (FR-52, AD-17, Story 3.13). Standard library only, plus
``nextron.domain``.

An erasure never deletes a row: it replaces each personal-data column with a *tombstone*, chosen
by the column's class in ``nextron.db.pii_registry`` (passed here as its code, so this module
stays pure).

- ``REDACTED`` is ``"[erased]"``, the tombstone of free text.
- ``ErasureCause``: ``dsr`` (a data subject request) or ``retention`` (Epic 8's retention job).
- ``Coverage`` says what an owner's erasure hook does to one of its tables, and ``TableErasure``
  (``coverage``, ``reason``) records it with why: ``tombstoned`` (the hook tombstones the
  Contact's rows), ``kept`` (the table is the proof of erasure and holds no plaintext of the
  person) or ``not_held`` (the table never holds a Contact's data). A ``coverage`` that isn't a
  ``Coverage`` raises ``TypeError``; a ``reason`` that isn't a ``str`` of 1 to 300 characters
  equal to its strip raises ``ValueError``.
- ``PII_CLASSES``: the class codes ``none``, ``redact``, ``null`` and ``keep_hash``.
- ``tombstone_hash(kind, value)``: ``nextron.domain.contactability.key_hash(kind, value)``, the
  same hash the Suppression List stores for that key, so an erased address still matches; when
  the normaliser refuses ``value`` (or it can't be encoded), the lowercase hex SHA-256 of the
  UTF-8 bytes of ``kind + ":" + value`` instead. It never raises on a ``str`` ``value``. A
  ``value`` that isn't a ``str`` raises ``TypeError``; a ``kind`` that isn't ``address``,
  ``domain`` or ``profile`` (a person key takes a pair) raises ``ValueError``.
- ``tombstone(pii_class, value, *, hash_kind)``, by class code:

  ===========  ==========================================================================
  Class        Tombstone
  ===========  ==========================================================================
  redact       ``REDACTED``; NULL stays NULL
  null         NULL
  keep_hash    ``tombstone_hash(hash_kind, value)``; the value unchanged when ``hash_kind``
               is ``None`` (it is already a hash); NULL stays NULL
  none         the value unchanged
  ===========  ==========================================================================

  A ``pii_class`` that isn't a ``str`` raises ``TypeError``, an unknown code ``ValueError``.
"""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain import identity
from nextron.domain.contactability import KIND_SEPARATOR, KeyKind, key_hash

REDACTED: Final = "[erased]"
MAX_REASON_LENGTH: Final = 300

NONE_CLASS: Final = "none"
REDACT_CLASS: Final = "redact"
NULL_CLASS: Final = "null"
KEEP_HASH_CLASS: Final = "keep_hash"
PII_CLASSES: Final = (NONE_CLASS, REDACT_CLASS, NULL_CLASS, KEEP_HASH_CLASS)
HASHABLE_KINDS: Final = (KeyKind.ADDRESS, KeyKind.DOMAIN, KeyKind.PROFILE)


class ErasureCause(StrEnum):
    DSR = "dsr"
    RETENTION = "retention"


class Coverage(StrEnum):
    TOMBSTONED = "tombstoned"
    KEPT = "kept"
    NOT_HELD = "not_held"


@dataclass(frozen=True, slots=True)
class TableErasure:
    """What an owner's erasure hook does to one of its tables, and why."""

    coverage: Coverage
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.coverage, Coverage):
            raise TypeError(
                f"TableErasure.coverage must be a Coverage, not {type(self.coverage).__name__}"
            )
        if (
            not isinstance(self.reason, str)
            or not 1 <= len(self.reason) <= MAX_REASON_LENGTH
            or self.reason != self.reason.strip()
        ):
            raise ValueError(
                f"TableErasure.reason must be a stripped text of 1 to {MAX_REASON_LENGTH} "
                "characters"
            )


def _hash_kind(kind: object) -> KeyKind:
    """``kind`` as a single-value ``KeyKind``; ``ValueError`` otherwise."""
    if isinstance(kind, str):
        try:
            found = KeyKind(kind)
        except ValueError:
            found = None
        if found in HASHABLE_KINDS:
            return found
    raise ValueError("a tombstone hash takes the address, domain or profile kind")


def tombstone_hash(kind: KeyKind | str, value: str) -> str:
    """The hash that replaces ``value``; see the module docstring."""
    found = _hash_kind(kind)
    if not isinstance(value, str):
        raise TypeError(f"a tombstone hash takes a str, not {type(value).__name__}")
    try:
        return key_hash(found, value)
    except (identity.IdentityError, UnicodeError):
        keyed = f"{found}{KIND_SEPARATOR}{value}"
        return hashlib.sha256(keyed.encode("utf-8", "surrogatepass")).hexdigest()


def _class_code(pii_class: object) -> str:
    if not isinstance(pii_class, str):
        raise TypeError(f"pii_class must be a str, not {type(pii_class).__name__}")
    code = str(pii_class)
    if code not in PII_CLASSES:
        raise ValueError("pii_class must be one of " + ", ".join(PII_CLASSES))
    return code


def tombstone(pii_class: str, value: object, *, hash_kind: KeyKind | str | None) -> object:
    """The tombstone of ``value`` in a column of class ``pii_class``; see the module
    docstring."""
    code = _class_code(pii_class)
    if code == NONE_CLASS:
        return value
    if code == NULL_CLASS or value is None:
        return None
    if code == REDACT_CLASS:
        return REDACTED
    if hash_kind is None:  # keep_hash on a column that already holds a hash
        return value
    if not isinstance(value, str):
        raise TypeError(f"a keep_hash column takes a str, not {type(value).__name__}")
    return tombstone_hash(hash_kind, value)
