"""The Suppression List panel's rules and messages (FR-50, FR-41, AD-16, NFR-36, UX-DR25,
Story 3.5). Standard library only, plus ``nextron.domain``.

The Operator adds an address or a domain, imports a list, searches for one value and reverses a
rule-matched opt-out. This module reads what was typed or uploaded; ``nextron.app.compliance``
writes the list. Every refusal raises ``SuppressionRefused`` (code ``suppression_refused``),
whose ``message`` is one of the messages below, shown on the panel as it is: no message holds
the value that was typed or uploaded.

**A typed value.**

- ``entry_key(text)``: ``text`` is stripped. With an ``@`` it is an address key
  (``identity.normalize_email``: no plus-tag or dot stripping); otherwise a domain key
  (``identity.registrable_domain``, so a website or a URL works too: ``https://www.Firm.co.uk/x``
  is ``firm.co.uk``). An empty text, or one the normaliser refuses (``IdentityError``), raises
  ``NOT_VALID``, and so does an address whose local part holds a character RFC 5322 allows only
  inside a quoted string (``LOCAL_PART_SPECIALS``: ``( ) < > [ ] : ; , \\ "``), such as
  ``mailto:jane@firm.co.uk``, ``<jane@firm.co.uk>`` or ``https://medium.com/@jane.doe``: no
  Contact can hold one (email-validator refuses quoted local parts), so adding it would say
  "Added" while the real address stays off the list. A non-``str`` raises ``TypeError``.
- ``entry_value(text) -> EntryValue(subject, key)``: ``entry_key``, then a domain that is free
  webmail (its host as typed, or its registrable domain: ``markets.is_free_webmail``) raises
  ``FREE_WEBMAIL``, and a registrable domain that is a shared platform
  (``leads.is_shared_platform``: ``facebook.com``, ``google.com`` for ``sites.google.com``,
  ``lawsociety.org.uk`` and the like) raises ``SHARED_PLATFORM``: a firm's page there must
  never suppress the whole platform. The subject is an ``AddressSubject`` or a
  ``DomainSubject`` (``nextron.domain.contactability``).
- ``search_key(text)``: the key a search looks up, ``entry_key(text)``; ``None`` for a free
  webmail domain or a shared platform, which are never on the list.

**An imported file.** ``parse_import(data) -> ImportPlan(values, invalid_lines)``. The whole file
is refused, and nothing is read from it, when it is over ``MAX_FILE_BYTES`` (5 MiB:
``FILE_TOO_LARGE``), isn't strict UTF-8 (a BOM is allowed: it is decoded as ``utf-8-sig``) or
holds a NUL (``FILE_NOT_UTF8``), has no non-blank line (``FILE_EMPTY``) or has more than
``MAX_VALUES`` (10,000) non-blank lines (``FILE_TOO_MANY``); when the first non-blank line is
invalid (a header line), one more is allowed, so a header and 10,000 values import. Lines split
on ``\\r\\n``, ``\\r`` and ``\\n`` and are numbered from 1; a blank line (empty or whitespace
only) is skipped. A line's value is its first CSV column (``csv.reader`` on the line), stripped.
A value ``entry_value`` refuses (a header line, a free-webmail domain, a shared platform, junk),
or a line ``csv`` can't read, is invalid:
only its number is kept. ``values`` holds ``(line number, EntryValue)`` pairs in file order,
repeats included (the import counts a repeat as already suppressed).

**A reversal's reason.** ``checked_reason(text)``: ``text`` stripped, which must be 1 to
``MAX_REASON_LENGTH`` (500) characters with no control, format, surrogate, private-use or
unassigned (noncharacter) character, and no line or paragraph separator (Unicode categories
``Cc``, ``Cf``, ``Cs``, ``Co``, ``Cn``, ``Zl`` and ``Zp``, so no line break of any kind):
``REASON_REQUIRED`` when empty, ``REASON_INVALID`` otherwise.

**Messages** (each a constant, or a function of the found entry's date and source):

==========================  ==================================================================
``NOT_VALID``               Not a valid email address or domain
``FREE_WEBMAIL``            Free-webmail domains can't be suppressed as a whole. Add the
                            address instead.
``SHARED_PLATFORM``         Shared platforms such as facebook.com or sites.google.com can't be
                            suppressed as a whole. Add the firm's own domain or the address
                            instead.
``ONLY_RULE_MATCHED``       Only rule-matched opt-outs can be reversed
``ALREADY_REVERSED``        This opt-out has already been reversed
``UNKNOWN_ENTRY``           That entry isn't on the list
``REASON_REQUIRED``         Enter a reason for reversing this opt-out
``REASON_INVALID``          The reason must be at most 500 characters of plain text
``FILE_EMPTY``              The file has no addresses or domains
``FILE_NOT_UTF8``           The file isn't UTF-8 text
``FILE_TOO_LARGE``          The file is larger than 5 MB
``FILE_TOO_MANY``           The file has more than 10,000 addresses and domains. Split it and
                            import each part.
``ADDED``                   Added to the Suppression List
``NOT_ON_THE_LIST``         Not on the list
``on_the_list``             On the list (active since <YYYY-MM-DD>, source <source>)
``already_on_the_list``     Already on the list (active since <YYYY-MM-DD>, source <source>)
``reversed_message``        Opt-out reversed. (plus how many other entries of the same opt-out
                            are still on the list, when any are)
``import_message``          Import finished: <n> added, <n> already suppressed, <n> invalid.
``invalid_lines_message``   Invalid lines: <numbers> (at most ``INVALID_LINES_SHOWN``, 100,
                            then "and <n> more")
==========================  ==================================================================
"""

import csv
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.domain import identity
from nextron.domain.contactability import (
    AddressSubject,
    DomainSubject,
    KeyKind,
    SuppressionKey,
    suppression_key,
)
from nextron.domain.errors import DomainError
from nextron.domain.leads import is_shared_platform
from nextron.domain.markets import is_free_webmail

MAX_FILE_BYTES: Final = 5 * 1024 * 1024
MAX_VALUES: Final = 10_000
MAX_REASON_LENGTH: Final = 500
INVALID_LINES_SHOWN: Final = 100
ADDRESS_MARK: Final = "@"
# RFC 5322 specials legal in a local part only inside a quoted string; no Contact holds one.
LOCAL_PART_SPECIALS: Final = frozenset('()<>[]:;,\\"')
NUL: Final = "\x00"
LINE_BREAK: Final = re.compile(r"\r\n|\r|\n")
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
DATE_FORMAT: Final = "%Y-%m-%d"

NOT_VALID: Final = "Not a valid email address or domain"
FREE_WEBMAIL: Final = (
    "Free-webmail domains can't be suppressed as a whole. Add the address instead."
)
SHARED_PLATFORM: Final = (
    "Shared platforms such as facebook.com or sites.google.com can't be suppressed as a whole. "
    "Add the firm's own domain or the address instead."
)
ONLY_RULE_MATCHED: Final = "Only rule-matched opt-outs can be reversed"
ALREADY_REVERSED: Final = "This opt-out has already been reversed"
UNKNOWN_ENTRY: Final = "That entry isn't on the list"
REASON_REQUIRED: Final = "Enter a reason for reversing this opt-out"
REASON_INVALID: Final = "The reason must be at most 500 characters of plain text"
FILE_EMPTY: Final = "The file has no addresses or domains"
FILE_NOT_UTF8: Final = "The file isn't UTF-8 text"
FILE_TOO_LARGE: Final = "The file is larger than 5 MB"
FILE_TOO_MANY: Final = (
    "The file has more than 10,000 addresses and domains. Split it and import each part."
)
ADDED: Final = "Added to the Suppression List"
NOT_ON_THE_LIST: Final = "Not on the list"
REVERSED: Final = "Opt-out reversed."


class SuppressionRefused(DomainError):
    """A value, file or reversal the Suppression List panel refuses; ``message`` says why."""

    code = "suppression_refused"


@dataclass(frozen=True, slots=True)
class EntryValue:
    """A typed or imported value: the subject to suppress and the value's own key."""

    subject: AddressSubject | DomainSubject
    key: SuppressionKey


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """An imported file's values, as ``(line number, EntryValue)`` pairs in file order, and the
    numbers of its invalid lines."""

    values: tuple[tuple[int, EntryValue], ...]
    invalid_lines: tuple[int, ...]


def _normalised(text: object) -> tuple[KeyKind, str]:
    """The kind and the normalised value of a typed value; ``NOT_VALID`` when it is none."""
    if not isinstance(text, str):
        raise TypeError(f"a typed value is a str, not {type(text).__name__}")
    stripped = text.strip()
    if not stripped:
        raise SuppressionRefused(NOT_VALID)
    try:
        if ADDRESS_MARK in stripped:
            address = identity.normalize_email(stripped)
            local = address.rpartition(ADDRESS_MARK)[0]
            if any(character in LOCAL_PART_SPECIALS for character in local):
                raise SuppressionRefused(NOT_VALID)
            return KeyKind.ADDRESS, address
        return KeyKind.DOMAIN, identity.registrable_domain(stripped)
    except identity.IdentityError:
        raise SuppressionRefused(NOT_VALID) from None


def _whole_webmail(text: str, kind: KeyKind, value: str) -> bool:
    """Whether a domain value is free webmail, by its host as typed or its registrable
    domain."""
    if kind is not KeyKind.DOMAIN:
        return False
    return is_free_webmail(identity.url_host(text.strip())) or is_free_webmail(value)


def _whole_platform(kind: KeyKind, value: str) -> bool:
    """Whether a domain value's registrable domain is a shared platform."""
    return kind is KeyKind.DOMAIN and is_shared_platform(value)


def entry_key(text: str) -> SuppressionKey:
    """The key of a typed value: an address, or a domain, website or URL; see the module
    docstring."""
    kind, value = _normalised(text)
    return suppression_key(kind, value)


def entry_value(text: str) -> EntryValue:
    """The subject and key of a value to add; never a whole free-webmail domain or shared
    platform."""
    kind, value = _normalised(text)
    if _whole_webmail(text, kind, value):
        raise SuppressionRefused(FREE_WEBMAIL)
    if _whole_platform(kind, value):
        raise SuppressionRefused(SHARED_PLATFORM)
    subject = AddressSubject(value) if kind is KeyKind.ADDRESS else DomainSubject(value)
    return EntryValue(subject, suppression_key(kind, value))


def search_key(text: str) -> SuppressionKey | None:
    """The key a search looks up, or ``None`` for a free-webmail domain or a shared platform
    (never on the list)."""
    kind, value = _normalised(text)
    if _whole_webmail(text, kind, value) or _whole_platform(kind, value):
        return None
    return suppression_key(kind, value)


def _first_column(line: str) -> str:
    """The line's first CSV column, stripped; empty when ``csv`` can't read the line."""
    try:
        row = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return ""
    return row[0].strip() if row else ""


def _line_value(line: str) -> EntryValue | None:
    """The line's value, or ``None`` when it is invalid."""
    try:
        return entry_value(_first_column(line))
    except SuppressionRefused:
        return None


def parse_import(data: bytes) -> ImportPlan:
    """The values of an imported file and its invalid lines; see the module docstring."""
    if not isinstance(data, bytes):
        raise TypeError(f"an imported file is bytes, not {type(data).__name__}")
    if len(data) > MAX_FILE_BYTES:
        raise SuppressionRefused(FILE_TOO_LARGE)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise SuppressionRefused(FILE_NOT_UTF8) from None
    if NUL in text:
        raise SuppressionRefused(FILE_NOT_UTF8)
    lines = [
        (number, line)
        for number, line in enumerate(LINE_BREAK.split(text), start=1)
        if line.strip()
    ]
    if not lines:
        raise SuppressionRefused(FILE_EMPTY)
    header = _line_value(lines[0][1]) is None  # an invalid first line doesn't count
    if len(lines) > MAX_VALUES + (1 if header else 0):
        raise SuppressionRefused(FILE_TOO_MANY)
    values: list[tuple[int, EntryValue]] = []
    invalid: list[int] = []
    for number, line in lines:
        value = _line_value(line)
        if value is None:
            invalid.append(number)
        else:
            values.append((number, value))
    return ImportPlan(tuple(values), tuple(invalid))


def checked_reason(text: str) -> str:
    """A reversal's reason, stripped; see the module docstring."""
    if not isinstance(text, str):
        raise TypeError(f"a reason is a str, not {type(text).__name__}")
    reason = text.strip()
    if not reason:
        raise SuppressionRefused(REASON_REQUIRED)
    if len(reason) > MAX_REASON_LENGTH or any(
        unicodedata.category(character) in REFUSED_CATEGORIES for character in reason
    ):
        raise SuppressionRefused(REASON_INVALID)
    return reason


def _day(at: datetime) -> str:
    return format(at, DATE_FORMAT)


def on_the_list(since: datetime, source: str) -> str:
    """A search's answer for a value whose key is ``active``."""
    return f"On the list (active since {_day(since)}, source {source})"


def already_on_the_list(since: datetime, source: str) -> str:
    """An add's answer when the value's key was already ``active``."""
    return f"Already on the list (active since {_day(since)}, source {source})"


def reversed_message(others: int) -> str:
    """A reversal's answer; ``others`` counts the same opt-out's other ``active`` entries."""
    if others <= 0:
        return REVERSED
    if others == 1:
        return f"{REVERSED} 1 other entry from the same opt-out is still on the list."
    return f"{REVERSED} {others:,} other entries from the same opt-out are still on the list."


def import_message(added: int, already: int, invalid: int) -> str:
    """An import's summary line."""
    return (
        f"Import finished: {added:,} added, {already:,} already suppressed, {invalid:,} invalid."
    )


def invalid_lines_message(lines: Sequence[int]) -> str | None:
    """The invalid line numbers, at most ``INVALID_LINES_SHOWN`` of them; ``None`` when there
    are none."""
    if not lines:
        return None
    shown = ", ".join(f"{number:,}" for number in lines[:INVALID_LINES_SHOWN])
    more = len(lines) - INVALID_LINES_SHOWN
    if more > 0:
        return f"Invalid lines: {shown} and {more:,} more"
    return f"Invalid lines: {shown}"
