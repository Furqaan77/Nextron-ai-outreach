"""The CSV Lead import's rules (FR-59, FR-9, FR-15, FR-49, UX-DR20). Standard library only.

``nextron.app.discovery.api.LeadImports`` runs an import in three steps: ``check_origin`` and
``check_file_size`` on what the form sent, ``plan_import`` on the file (no database, no
network), then the plan's rows through ``Discovery.upsert_lead`` and ``Research.add_contact``.

**Refusals.** ``ImportRefused(field, reason)`` (code ``lead_import_refused``, message
``"<field>: <reason>"``) refuses the whole import before anything is written; ``field`` is
``origin``, ``file`` or ``default_segment`` and ``reason`` is a fixed text the form shows by
that field. No reason holds a cell, the origin or the file name.

- ``check_origin(text)``: each run of whitespace becomes one space and the result is stripped;
  it must be ``MIN_ORIGIN_LENGTH`` to ``MAX_ORIGIN_LENGTH`` (3 to 200) characters with no
  ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character. It returns the cleaned origin.
- ``check_file_size(data)``: the file must not be empty or over ``MAX_FILE_BYTES`` (5 MiB).

**``plan_import(data, *, segments, default_segment_id, email_ok) -> ImportPlan(rows,
rejected)``.** ``segments`` maps each Segment's key and name, each whitespace-collapsed and
casefolded, to its id;
``default_segment_id`` is the form's default Segment's id, or ``None``; ``email_ok(address)`` is
the syntax check (``EmailSyntaxPort.is_valid``). The file as a whole is refused
(``ImportRefused("file", ...)``) when:

- ``check_file_size`` refuses it;
- it isn't strict UTF-8 (a BOM is allowed: it is decoded as ``utf-8-sig``) or holds a NUL;
- ``csv.reader(io.StringIO(text, newline=""), strict=True)`` raises ``csv.Error``: "The file
  isn't valid CSV (row <n>: <message>)", ``n`` counting records from 1;
- it has no non-blank record, so no header;
- the header (its first non-blank record) names a field twice or lacks a required one (named).
  Each header cell is stripped and casefolded, each run of whitespace, ``_`` or ``-`` becomes
  one space, and the result is stripped and looked up in ``HEADERS`` (the aliases of each
  ``ImportField``); other columns are ignored and never stored;
- it has more than ``MAX_ROWS`` (5,000) counted rows: the reader streams, so the refusal comes
  as soon as the 5,001st is read.

A record whose every cell is blank is skipped and not counted, before the header too. A row's
``number`` is its record's 1-based position in the file, blank records included, as a
spreadsheet numbers its rows (the header is usually row 1). Each cell is whitespace-collapsed
(``" ".join(cell.split())``); a missing cell is blank.

**Row checks,** in this order; the first failure rejects the row with its fixed reason (the
``REASON_*`` texts): a non-blank cell past the header's width (blank trailing cells, such as a
trailing comma, are ignored); a missing organization name, website or
country; an organization name that breaks ``LeadIdentity``'s name rule; a website
``registrable_domain`` refuses; a website on the FR-49 denylist (``fetch_policy.is_denied``);
a website on a shared platform (``leads.is_shared_platform``); a country
``normalize_country_code`` refuses (``UK`` is ``GB``); a Segment cell that matches no key or
name (ignoring case); a blank Segment cell with no default. A blank Segment cell takes the
default.

**Contact cells** of an accepted row: an email is ``normalize_email``'d and must pass
``email_ok``; a LinkedIn URL is ``normalize_linkedin_url``'d; a name and a role follow
``ContactCandidate``'s text rules (a name also needs a letter or digit). A cell that fails is
dropped with its ``NOTE_*`` text, and the rest of the row imports. The row gets a ``ContactCandidate`` when a name or an email
remains; when some Contact cell was filled but neither remains, it gets ``NOTE_NO_CONTACT`` and
no Contact.

**The plan.** ``ImportRow(number, label, identity, contact, notes)`` holds a ``LeadIdentity``
built from normaliser output (the cleaned name, the Segment's id, the registrable domain and the
country code) and ``RejectedRow(number, label, reason)`` its reason. A ``label`` is the cleaned
name cell without its ``Cc``, ``Cf``, ``Zl`` and ``Zp`` characters, cut to ``LABEL_LENGTH``
(100) characters; it is CSV text, so the page renders it escaped.
"""

import csv
import io
import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nextron.domain.contacts import ContactCandidate, InvalidContact, check_name, check_role
from nextron.domain.errors import DomainError
from nextron.domain.fetch_policy import is_denied
from nextron.domain.identity import (
    IdentityError,
    normalize_country_code,
    normalize_email,
    normalize_linkedin_url,
    registrable_domain,
)
from nextron.domain.leads import (
    InvalidLeadIdentity,
    LeadIdentity,
    check_name as check_lead_name,
    is_shared_platform,
)

MAX_FILE_BYTES: Final = 5 * 1024 * 1024
MAX_ROWS: Final = 5000
MIN_ORIGIN_LENGTH: Final = 3
MAX_ORIGIN_LENGTH: Final = 200
LABEL_LENGTH: Final = 100
# control, format, line separator and paragraph separator characters
REFUSED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
HEADER_SEPARATORS: Final = re.compile(r"[\s_-]+")
NUL: Final = "\x00"

FIELD_ORIGIN: Final = "origin"
FIELD_FILE: Final = "file"
FIELD_DEFAULT_SEGMENT: Final = "default_segment"


class ImportField(StrEnum):
    """A column the import reads; its value is how a message names it."""

    ORGANIZATION = "organization name"
    WEBSITE = "website"
    COUNTRY = "country"
    SEGMENT = "segment"
    CONTACT_NAME = "contact name"
    ROLE = "role"
    EMAIL = "email"
    LINKEDIN = "linkedin url"


REQUIRED_FIELDS: Final = (ImportField.ORGANIZATION, ImportField.WEBSITE, ImportField.COUNTRY)
CONTACT_FIELDS: Final = (
    ImportField.CONTACT_NAME,
    ImportField.ROLE,
    ImportField.EMAIL,
    ImportField.LINKEDIN,
)

_ALIASES: Final[dict[ImportField, tuple[str, ...]]] = {
    ImportField.ORGANIZATION: (
        "organization name",
        "organisation name",
        "organization",
        "organisation",
        "company",
        "company name",
    ),
    ImportField.WEBSITE: ("website", "website url", "web site", "url"),
    ImportField.COUNTRY: ("country", "country code"),
    ImportField.SEGMENT: ("segment",),
    ImportField.CONTACT_NAME: ("contact name", "contact"),
    ImportField.ROLE: ("role", "contact role", "title", "job title"),
    ImportField.EMAIL: ("email", "email address", "contact email"),
    ImportField.LINKEDIN: ("linkedin url", "linkedin", "contact linkedin url"),
}
# Each accepted header, after normalisation, with the field it names.
HEADERS: Final[Mapping[str, ImportField]] = {
    alias: field for field, aliases in _ALIASES.items() for alias in aliases
}

# The file refusals.
FILE_EMPTY: Final = "The file is empty."
FILE_TOO_LARGE: Final = "The file is over 5 MB."
FILE_NOT_UTF8: Final = "The file isn't UTF-8 text. Save it as CSV UTF-8 and try again."
FILE_HAS_NUL: Final = "The file holds a NUL character, so it isn't a CSV text file."
FILE_NO_HEADER: Final = "The file has no header row."
FILE_TOO_MANY_ROWS: Final = f"The file has more than {MAX_ROWS:,} rows."
ORIGIN_LENGTH: Final = (
    f"Give the list's origin in {MIN_ORIGIN_LENGTH} to {MAX_ORIGIN_LENGTH} characters."
)
ORIGIN_CHARACTERS: Final = "The origin can't hold control or format characters."
DEFAULT_SEGMENT_UNKNOWN: Final = "That Segment isn't stored. Choose one from the list."

# The row rejections: fixed texts that never echo a cell.
REASON_EXTRA_CELLS: Final = "the row has more cells than the header row"
REASON_MISSING: Final = {
    ImportField.ORGANIZATION: "missing organization name",
    ImportField.WEBSITE: "missing website",
    ImportField.COUNTRY: "missing country",
}
REASON_BAD_NAME: Final = "the organization name is over 200 characters or holds control characters"
REASON_BAD_WEBSITE: Final = "the website can't be read as a web address"
REASON_DENIED: Final = "a LinkedIn, Instagram, Maps or job-site URL is not a company website"
REASON_SHARED_PLATFORM: Final = (
    "a page on a shared platform such as Google Sites or Facebook is not the organization's own "
    "website"
)
REASON_BAD_COUNTRY: Final = "unknown country: use a two-letter code such as US or GB"
REASON_UNKNOWN_SEGMENT: Final = "unknown Segment"
REASON_NO_SEGMENT: Final = "no Segment, and no default Segment was chosen"

# The notes on accepted rows.
NOTE_BAD_EMAIL: Final = "the email fails the syntax check, so it was not imported"
NOTE_BAD_LINKEDIN: Final = (
    "the LinkedIn URL isn't a linkedin.com/in/ profile, so it was not imported"
)
NOTE_BAD_NAME: Final = (
    "the Contact name is over 200 characters, holds control characters or has no letters or "
    "digits, so it was not imported"
)
NOTE_BAD_ROLE: Final = (
    "the Contact role is over 200 characters or holds control characters, so it was not imported"
)
NOTE_NO_CONTACT: Final = "the Contact needs a name or a valid email, so no Contact was imported"
NOTE_EMAIL_DROPPED: Final = (
    "the Contact already has a different email address, so this one was not stored"
)


class ImportRefused(DomainError):
    """The import as a whole is refused before anything is written; ``reason`` is the fixed
    text the form shows by ``field``."""

    code = "lead_import_refused"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ImportRow:
    """An accepted row: its number and label, the Lead it names, its Contact (or ``None``) and
    the notes on cells that were dropped."""

    number: int
    label: str
    identity: LeadIdentity
    contact: ContactCandidate | None
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RejectedRow:
    """A row that imports nothing: its number, its label and its fixed reason."""

    number: int
    label: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """The accepted rows and the rejected rows, each in row order."""

    rows: tuple[ImportRow, ...]
    rejected: tuple[RejectedRow, ...]


def _has_refused_character(text: str) -> bool:
    return any(unicodedata.category(ch) in REFUSED_CATEGORIES for ch in text)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def check_origin(text: str) -> str:
    """The list's origin, whitespace-collapsed; raises ``ImportRefused("origin", ...)``."""
    if not isinstance(text, str):
        raise TypeError(f"check_origin takes a str, not {type(text).__name__}")
    origin = _collapse(text)
    if not MIN_ORIGIN_LENGTH <= len(origin) <= MAX_ORIGIN_LENGTH:
        raise ImportRefused(FIELD_ORIGIN, ORIGIN_LENGTH)
    if _has_refused_character(origin):
        raise ImportRefused(FIELD_ORIGIN, ORIGIN_CHARACTERS)
    return origin


def check_file_size(data: bytes) -> None:
    """Raise ``ImportRefused("file", ...)`` for an empty file or one over 5 MiB."""
    if not isinstance(data, bytes):
        raise TypeError(f"check_file_size takes bytes, not {type(data).__name__}")
    if not data:
        raise ImportRefused(FIELD_FILE, FILE_EMPTY)
    if len(data) > MAX_FILE_BYTES:
        raise ImportRefused(FIELD_FILE, FILE_TOO_LARGE)


def _decoded(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise ImportRefused(FIELD_FILE, FILE_NOT_UTF8) from None
    if NUL in text:
        raise ImportRefused(FIELD_FILE, FILE_HAS_NUL)
    return text


def _records(text: str) -> Iterator[tuple[int, list[str]]]:
    """Each non-blank record of the CSV text with its 1-based position, streamed; raises
    ``ImportRefused`` on a ``csv.Error``."""
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    position = 0
    try:
        for record in reader:
            position += 1
            if not _is_blank(record):
                yield position, record
    except csv.Error as error:
        raise ImportRefused(
            FIELD_FILE, f"The file isn't valid CSV (row {position + 1}: {error})"
        ) from None


def _header_key(cell: str) -> str:
    return HEADER_SEPARATORS.sub(" ", cell.strip().casefold()).strip()


def _columns(header: list[str]) -> dict[ImportField, int]:
    """Each field the header names, with its column; raises ``ImportRefused``."""
    columns: dict[ImportField, int] = {}
    for index, cell in enumerate(header):
        field = HEADERS.get(_header_key(cell))
        if field is None:
            continue
        if field in columns:
            raise ImportRefused(FIELD_FILE, f"The header names the {field} column twice.")
        columns[field] = index
    for field in REQUIRED_FIELDS:
        if field not in columns:
            raise ImportRefused(FIELD_FILE, f"The header has no {field} column.")
    return columns


def _is_blank(record: list[str]) -> bool:
    return all(not cell.split() for cell in record)


def _label(name: str) -> str:
    kept = "".join(ch for ch in name if unicodedata.category(ch) not in REFUSED_CATEGORIES)
    return kept[:LABEL_LENGTH]


@dataclass(frozen=True, slots=True)
class _Planner:
    segments: Mapping[str, str]
    default_segment_id: str | None
    email_ok: Callable[[str], bool]
    columns: Mapping[ImportField, int]
    width: int

    def cell(self, record: list[str], field: ImportField) -> str:
        index = self.columns.get(field)
        if index is None or index >= len(record):
            return ""
        return _collapse(record[index])

    def row(self, number: int, record: list[str]) -> ImportRow | RejectedRow:
        name = self.cell(record, ImportField.ORGANIZATION)
        label = _label(name)

        def rejected(reason: str) -> RejectedRow:
            return RejectedRow(number, label, reason)

        if any(cell.split() for cell in record[self.width :]):
            return rejected(REASON_EXTRA_CELLS)
        for field in REQUIRED_FIELDS:
            if not self.cell(record, field):
                return rejected(REASON_MISSING[field])
        try:
            check_lead_name(name)
        except InvalidLeadIdentity:
            return rejected(REASON_BAD_NAME)
        website = self.cell(record, ImportField.WEBSITE)
        try:
            web_domain = registrable_domain(website)
        except IdentityError:
            return rejected(REASON_BAD_WEBSITE)
        if is_denied(website):
            return rejected(REASON_DENIED)
        if is_shared_platform(web_domain):
            return rejected(REASON_SHARED_PLATFORM)
        try:
            country_code = normalize_country_code(self.cell(record, ImportField.COUNTRY))
        except IdentityError:
            return rejected(REASON_BAD_COUNTRY)
        segment = self.cell(record, ImportField.SEGMENT)
        if segment:
            segment_id = self.segments.get(segment.casefold())
            if segment_id is None:
                return rejected(REASON_UNKNOWN_SEGMENT)
        elif self.default_segment_id is not None:
            segment_id = self.default_segment_id
        else:
            return rejected(REASON_NO_SEGMENT)
        identity = LeadIdentity(
            name=name, segment_id=segment_id, web_domain=web_domain, country_code=country_code
        )
        contact, notes = self.contact(record)
        return ImportRow(number, label, identity, contact, notes)

    def contact(self, record: list[str]) -> tuple[ContactCandidate | None, tuple[str, ...]]:
        cells = {field: self.cell(record, field) for field in CONTACT_FIELDS}
        notes: list[str] = []
        email = self._email(cells[ImportField.EMAIL], notes)
        linkedin_url = self._linkedin(cells[ImportField.LINKEDIN], notes)
        name = self._text(cells[ImportField.CONTACT_NAME], check_name, NOTE_BAD_NAME, notes)
        role = self._text(cells[ImportField.ROLE], check_role, NOTE_BAD_ROLE, notes)
        if name is None and email is None:
            if any(cells.values()):
                notes.append(NOTE_NO_CONTACT)
            return None, tuple(notes)
        candidate = ContactCandidate(name=name, role=role, email=email, linkedin_url=linkedin_url)
        return candidate, tuple(notes)

    def _email(self, cell: str, notes: list[str]) -> str | None:
        if not cell:
            return None
        try:
            email = normalize_email(cell)
        except IdentityError:
            notes.append(NOTE_BAD_EMAIL)
            return None
        if not self.email_ok(email):
            notes.append(NOTE_BAD_EMAIL)
            return None
        return email

    @staticmethod
    def _linkedin(cell: str, notes: list[str]) -> str | None:
        if not cell:
            return None
        try:
            return normalize_linkedin_url(cell)
        except IdentityError:
            notes.append(NOTE_BAD_LINKEDIN)
            return None

    @staticmethod
    def _text(
        cell: str, check: Callable[[object], None], note: str, notes: list[str]
    ) -> str | None:
        if not cell:
            return None
        try:
            check(cell)
        except InvalidContact:
            notes.append(note)
            return None
        return cell


def plan_import(
    data: bytes,
    *,
    segments: Mapping[str, str],
    default_segment_id: str | None,
    email_ok: Callable[[str], bool],
) -> ImportPlan:
    """The rows of the CSV ``data``, accepted or rejected; raises ``ImportRefused("file", ...)``
    for a file that can't be imported at all. See the module docstring."""
    check_file_size(data)
    records = _records(_decoded(data))
    first = next(records, None)
    if first is None:
        raise ImportRefused(FIELD_FILE, FILE_NO_HEADER)
    _, header = first
    planner = _Planner(segments, default_segment_id, email_ok, _columns(header), len(header))
    rows: list[ImportRow] = []
    rejected: list[RejectedRow] = []
    for counted, (number, record) in enumerate(records, start=1):
        if counted > MAX_ROWS:
            raise ImportRefused(FIELD_FILE, FILE_TOO_MANY_ROWS)
        planned = planner.row(number, record)
        if isinstance(planned, ImportRow):
            rows.append(planned)
        else:
            rejected.append(planned)
    return ImportPlan(tuple(rows), tuple(rejected))
