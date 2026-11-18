"""The erasure ledger's lines (FR-50, FR-52, AD-16, AD-17, AD-20, Story 3.16). Standard library
only, plus ``nextron.domain``.

The ledger (``<backup dir>\\erasure-ledger.jsonl``, written by
``nextron.app.compliance.ledger``) mirrors every committed suppression entry, void and erasure,
one JSON object per line, so a restored backup can be brought forward again. It holds hashes,
ids, enum values and times only, and the firm's web domain on a domain key's line (a domain
entry needs it to be re-inserted, and ``domain_plain`` is stored in plain already).

- ``LEDGER_VERSION`` is ``1``; ``REAPPLIED`` (``"reapplied after restore"``) marks what a reapply
  wrote (a voided entry's reason, a reapplied DSR erasure's request reference).
- The lines, each frozen and checked when built (``TypeError`` or ``ValueError``):

  - ``Suppressed(entry_id, kind, value_hash, source, at, domain=None)``: an entry was written.
    ``entry_id`` a ULID, ``kind`` a ``KeyKind``, ``value_hash`` 64 lowercase hex characters,
    ``source`` a ``SuppressionSource``, ``at`` an aware ``datetime``; ``domain`` is set exactly
    for kind ``domain``.
  - ``Voided(entry_id, at)``: an entry was voided.
  - ``Erased(contact_id, cause, at)``: a Contact was erased; ``cause`` an ``ErasureCause``.

- ``encode(line) -> str``: one compact JSON object, keys in the acceptance criteria's order
  (``v``, ``type``, then the fields; ``domain`` last, only for a domain key), ``at`` as UTC
  ISO-8601 with microseconds and a ``Z``, ending ``"\\n"``.
- ``parse(data: bytes) -> ParsedLedger(lines, malformed, torn)``: the ledger's bytes split on
  ``\\n``, after a leading UTF-8 byte order mark is dropped. An unterminated last segment is
  ``torn`` (``True``) and ignored; a complete line that isn't a valid version-1 line (bad UTF-8
  or JSON, an unknown ``type``, a missing, extra or invalid field, a blank line, nesting too
  deep to decode) counts in ``malformed`` and is skipped. Duplicates are kept, in
  order.
- ``identity(line)``: ``("suppressed", entry_id)``, ``("voided", entry_id)`` or ``("erased",
  contact_id)``.
- ``plan_reapply(lines) -> ReapplyPlan(voids, inserts, erasures)``, each in ledger order:
  ``voids``, every voided entry id once; ``inserts``, the first ``Suppressed`` line of each entry
  id that isn't voided; ``erasures``, the first ``Erased`` line of each Contact.
"""

import codecs
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nextron.domain.contactability import KeyKind, is_hash
from nextron.domain.erasure import ErasureCause
from nextron.domain.ids import is_ulid
from nextron.domain.suppression_entry import SuppressionSource

LEDGER_VERSION: Final = 1
REAPPLIED: Final = "reapplied after restore"
SUPPRESSED: Final = "suppressed"
VOIDED: Final = "voided"
ERASED: Final = "erased"
_AT_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"


def _check_at(value: object) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"at must be a datetime, not {type(value).__name__}")
    if value.utcoffset() is None:
        raise ValueError("at must be an aware datetime")


def _check_ulid(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    if not is_ulid(value):
        raise ValueError(f"{name} must be a ULID")


@dataclass(frozen=True, slots=True)
class Suppressed:
    """A suppression entry was written."""

    entry_id: str
    kind: KeyKind
    value_hash: str
    source: SuppressionSource
    at: datetime
    domain: str | None = None

    def __post_init__(self) -> None:
        _check_ulid(self.entry_id, "entry_id")
        if not isinstance(self.kind, KeyKind):
            raise TypeError(f"kind must be a KeyKind, not {type(self.kind).__name__}")
        if not is_hash(self.value_hash):
            raise ValueError("value_hash must be 64 lowercase hex characters")
        if not isinstance(self.source, SuppressionSource):
            raise TypeError(
                f"source must be a SuppressionSource, not {type(self.source).__name__}"
            )
        _check_at(self.at)
        if self.domain is not None and not isinstance(self.domain, str):
            raise TypeError("domain must be a str or None")
        if (self.kind is KeyKind.DOMAIN) != (self.domain is not None) or self.domain == "":
            raise ValueError("domain is set exactly for a domain key")


@dataclass(frozen=True, slots=True)
class Voided:
    """A suppression entry was voided."""

    entry_id: str
    at: datetime

    def __post_init__(self) -> None:
        _check_ulid(self.entry_id, "entry_id")
        _check_at(self.at)


@dataclass(frozen=True, slots=True)
class Erased:
    """A Contact was erased."""

    contact_id: str
    cause: ErasureCause
    at: datetime

    def __post_init__(self) -> None:
        _check_ulid(self.contact_id, "contact_id")
        if not isinstance(self.cause, ErasureCause):
            raise TypeError(f"cause must be an ErasureCause, not {type(self.cause).__name__}")
        _check_at(self.at)


type LedgerLine = Suppressed | Voided | Erased


@dataclass(frozen=True, slots=True)
class ParsedLedger:
    """The ledger's valid lines in order, how many complete lines were malformed, and whether
    an unterminated last segment was ignored."""

    lines: tuple[LedgerLine, ...]
    malformed: int
    torn: bool


@dataclass(frozen=True, slots=True)
class ReapplyPlan:
    """What a reapply does, in order: void ``voids``, re-insert ``inserts``, re-erase
    ``erasures``."""

    voids: tuple[str, ...]
    inserts: tuple[Suppressed, ...]
    erasures: tuple[Erased, ...]


def _at_text(at: datetime) -> str:
    return at.astimezone(UTC).strftime(_AT_FORMAT)


def encode(line: LedgerLine) -> str:
    """``line`` as one compact JSON object ending ``"\\n"``; see the module docstring."""
    fields: dict[str, object]
    if isinstance(line, Suppressed):
        fields = {
            "v": LEDGER_VERSION,
            "type": SUPPRESSED,
            "entry_id": line.entry_id,
            "kind": str(line.kind),
            "value_hash": line.value_hash,
            "source": str(line.source),
            "at": _at_text(line.at),
        }
        if line.domain is not None:
            fields["domain"] = line.domain
    elif isinstance(line, Voided):
        fields = {
            "v": LEDGER_VERSION,
            "type": VOIDED,
            "entry_id": line.entry_id,
            "at": _at_text(line.at),
        }
    elif isinstance(line, Erased):
        fields = {
            "v": LEDGER_VERSION,
            "type": ERASED,
            "contact_id": line.contact_id,
            "cause": str(line.cause),
            "at": _at_text(line.at),
        }
    else:
        raise TypeError(f"encode takes a ledger line, not {type(line).__name__}")
    return json.dumps(fields, separators=(",", ":")) + "\n"


def _parse_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("at must be text")
    return datetime.strptime(value, _AT_FORMAT).replace(tzinfo=UTC)


_SUPPRESSED_KEYS: Final = frozenset(
    {"v", "type", "entry_id", "kind", "value_hash", "source", "at"}
)
_VOIDED_KEYS: Final = frozenset({"v", "type", "entry_id", "at"})
_ERASED_KEYS: Final = frozenset({"v", "type", "contact_id", "cause", "at"})


def _decode(raw: bytes) -> LedgerLine:
    """One complete line; ``ValueError`` (or ``TypeError``) when it isn't a valid v1 line."""
    found = json.loads(raw.decode("utf-8"))
    if not isinstance(found, dict):
        raise ValueError("a ledger line is a JSON object")
    version = found.get("v")
    if type(version) is not int or version != LEDGER_VERSION:
        raise ValueError("unknown ledger version")
    kind = found.get("type")
    keys = frozenset(found)
    if kind == SUPPRESSED:
        domain_key = found.get("kind") == KeyKind.DOMAIN
        expected = _SUPPRESSED_KEYS | {"domain"} if domain_key else _SUPPRESSED_KEYS
        if keys != expected:
            raise ValueError("a suppressed line's fields are wrong")
        return Suppressed(
            found["entry_id"],
            KeyKind(found["kind"]),
            found["value_hash"],
            SuppressionSource(found["source"]),
            _parse_at(found["at"]),
            found.get("domain"),
        )
    if kind == VOIDED:
        if keys != _VOIDED_KEYS:
            raise ValueError("a voided line's fields are wrong")
        return Voided(found["entry_id"], _parse_at(found["at"]))
    if kind == ERASED:
        if keys != _ERASED_KEYS:
            raise ValueError("an erased line's fields are wrong")
        return Erased(found["contact_id"], ErasureCause(found["cause"]), _parse_at(found["at"]))
    raise ValueError("unknown ledger line type")


def parse(data: bytes) -> ParsedLedger:
    """The ledger's lines; see the module docstring."""
    if not isinstance(data, bytes):
        raise TypeError(f"parse takes bytes, not {type(data).__name__}")
    data = data.removeprefix(codecs.BOM_UTF8)  # an editor may have saved one
    segments = data.split(b"\n")
    tail = segments.pop()  # what follows the last newline: empty when the file ends with one
    torn = tail != b""
    lines: list[LedgerLine] = []
    malformed = 0
    for raw in segments:
        try:
            lines.append(_decode(raw))
        except (ValueError, TypeError, KeyError, RecursionError):
            malformed += 1
    return ParsedLedger(tuple(lines), malformed, torn)


def identity(line: LedgerLine) -> tuple[str, str]:
    """The line's identity: its type and its entry or Contact id."""
    if isinstance(line, Suppressed):
        return (SUPPRESSED, line.entry_id)
    if isinstance(line, Voided):
        return (VOIDED, line.entry_id)
    if isinstance(line, Erased):
        return (ERASED, line.contact_id)
    raise TypeError(f"identity takes a ledger line, not {type(line).__name__}")


def plan_reapply(lines: Iterable[LedgerLine]) -> ReapplyPlan:
    """What a reapply of ``lines`` does; see the module docstring."""
    listed = tuple(lines)
    voids: dict[str, None] = {}
    for line in listed:
        if isinstance(line, Voided):
            voids.setdefault(line.entry_id)
        elif not isinstance(line, Suppressed | Erased):
            raise TypeError(f"plan_reapply takes ledger lines, not {type(line).__name__}")
    inserts: dict[str, Suppressed] = {}
    erasures: dict[str, Erased] = {}
    for line in listed:
        if isinstance(line, Suppressed) and line.entry_id not in voids:
            inserts.setdefault(line.entry_id, line)
        elif isinstance(line, Erased):
            erasures.setdefault(line.contact_id, line)
    return ReapplyPlan(tuple(voids), tuple(inserts.values()), tuple(erasures.values()))
