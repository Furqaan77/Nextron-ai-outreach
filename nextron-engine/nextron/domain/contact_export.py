"""The export of everything held on a Contact (FR-52, AD-17, UK GDPR subject access, Story 3.15).
Standard library only, plus ``nextron.domain``.

**Coverage.** ``COVERAGE`` maps every table holding a personal-data column (a column that
``nextron.db.pii_registry`` classifies other than ``none``), and every table with a column
whose foreign key targets ``contacts.id``, to one of:

- ``Exported(owner, section, shown_as={}, withheld={})``: ``queries.<owner>.export_pii`` exports
  the table's rows about the Contact in that section, each row with every personal-data column
  of the table, except that a column in ``shown_as`` is shown under another key (``{column:
  key}``) and a column in ``withheld`` (``{column: why}``) is never shown;
- ``NotAboutAContact(owner, reason)``: the table holds personal data, but none about a Contact.

=========================  ===========  ====================================================
Table                      Owner        Exported as / why not
=========================  ===========  ====================================================
``contacts``               research     ``contact``
``provenance_records``     research     ``provenance_records``
``evidence``               research     ``evidence``
``crawled_pages``          research     ``pages`` (``text`` shown as ``text_excerpts``)
``page_contact_methods``   research     ``contact_methods``
``job_postings``           research     ``job_postings`` (Story 2.11: title or description
                                        mentions the Contact)
``suppression_entries``    compliance   ``suppression`` (``value_hash`` withheld)
``data_subject_requests``  compliance   ``requests``
``alerts``                 notify       ``alerts``
``notifications``          notify       ``notifications``
``ai_test_cases``          playbook     ``override_cases`` (Story 4.6's override cases)
``settings``               setup        Nextron's own settings
``footer_snapshots``       drafting     Nextron's own footer text
``linkedin_accounts``      pipeline     Nextron's own LinkedIn account (Story 10.2)
``jobs``                   jobqueue     never linked to a Contact
``backups``                ops          never linked to a Contact
``restore_tests``          ops          never linked to a Contact
=========================  ===========  ====================================================

``tests/contracts/test_export_contracts.py`` derives the tables that need an entry from the
registry, the owner map (``owners.TABLES``) and the foreign keys to ``contacts.id``, so a new
personal-data table or column, or a new table that references a Contact, fails it until its
export is decided. ``EXTRA_SECTIONS`` are the sections that no table maps to one to
one: ``research.addresses`` (each address with its Address Class history), ``research.signals``
(the Signals the Contact's Evidence supports, which hold no personal data) and
``event_log.contact_history`` (the Contact's ``events`` rows). ``SECTIONS`` is every declared
section, by owner, in the order they are declared.

**Mentions.** Page-level data (a crawled page, a page's contact method) is about a Contact only
when its stored text mentions the Contact:

- ``mention_terms(name, email)``: the Contact's terms, each a ``MentionTerm(label, pattern)``
  (``label`` ``name`` or ``email``). The name is a term when it has at least
  ``MIN_NAME_LENGTH`` (3) characters besides whitespace: it matches as whole words (no letter,
  digit or ``_`` either side), ignoring case, with any whitespace between its words. The email is a term when it holds an ``@`` and isn't a key hash: it matches the whole
  address, ignoring case (no address character either side, so ``ajane@firm.co.uk`` and
  ``jane@firm.co.uk.au`` don't match). The erasure tombstone, ``nextron.domain.erasure.REDACTED``
  (``[erased]``), is never a term, so an erased Contact mentions nothing.
- ``mentions(text, terms)``: the labels of the terms ``text`` mentions, each label once, in
  term order; ``()`` for ``None``. (``queries.research.export_pii`` adds one ``email`` term per
  earlier address in the Contact's Provenance.)
- ``excerpts(text, terms)``: each line of ``text`` (split at ``\\n`` or ``\\r``) that mentions a
  term, stripped, in order: the whole line when it has at most ``MAX_EXCERPT_LENGTH`` (300)
  characters, else the 300 characters around its first match; at most ``MAX_EXCERPTS`` (20).
  A match that runs over a line break gives the lines it covers, and a match that starts
  inside lines already given is skipped, so no line is exported twice. The rest of a page is
  never exported: it is the firm's and other people's data.

**Addresses.** ``address_history(records, email)``: one entry per address the Contact's
``address`` Provenance Records name (``records`` are row mappings, oldest first), in the order
first recorded: ``{"address", "current", "history"}``, ``current`` saying whether it is the
Contact's stored ``email``, and ``history`` each record's ``address_class``, ``source_kind`` and
``retrieved_at``, in order: the Address Class the address had each time it was recorded.

**Suppression facts.** ``export_hashes(*, email, name, linkedin_url, web_domain, addresses)``:
the ``value_hash`` of every key the contactability gate checks for the Contact
(``contactability.lookup_keys``, with the Lead's ``web_domain``, so the firm's domain keys,
which block the person too, are included), the address key of every address in ``addresses``
(its Provenance), and any of these values that is already a key hash (``is_hash``: an erased
Contact's stored values). A tombstone (``REDACTED``) or a hash is never normalised into a key.

**The file.**

- ``assemble(contact_id, generated_at, parts)``: the document: ``schema_version``
  (``SCHEMA_VERSION``, 1), ``generated_at``, ``contact_id``, then one object per owner of
  ``SECTIONS``, holding every declared section as a list of row dicts: ``[]`` when the part has
  none, never omitted. ``parts`` maps an owner to ``{section: rows}``; an owner or section that
  isn't declared raises ``ValueError``, a ``contact_id`` that isn't a ULID ``ValueError``, and a
  ``generated_at`` that isn't an aware ``datetime`` ``TypeError`` (naive: ``ValueError``).
- ``render(document) -> bytes``: UTF-8 JSON, indented by 2, non-ASCII kept. A ``datetime``
  becomes ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` in UTC (a naive one raises ``ValueError``), a ``date``
  its ISO form; any other type JSON doesn't hold raises ``TypeError``, and a NaN or an infinity
  ``ValueError``.
- ``filename(contact_id)``: ``contact-<ULID>-export.json`` (``ValueError`` for a non-ULID).
- ``ContactExport(filename, body)``: the file, its ``body`` left out of the ``repr``.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Final

from nextron.domain.contactability import (
    ContactFacts,
    LeadFacts,
    is_hash,
    lookup_keys,
    person_keys,
)
from nextron.domain.erasure import REDACTED
from nextron.domain.ids import is_ulid

SCHEMA_VERSION: Final = 1
MIN_NAME_LENGTH: Final = 3
MAX_EXCERPT_LENGTH: Final = 300
MAX_EXCERPTS: Final = 20
NAME_TERM: Final = "name"
EMAIL_TERM: Final = "email"
EVENT_LOG: Final = "event_log"
ADDRESS_SUBJECT: Final = "address"  # nextron.domain.contacts.ProvenanceSubject.ADDRESS
# Characters an address may hold next to its ends: a match beside one isn't the whole address.
_LOCAL_CHARACTERS: Final = r"[\w.!#$%&'*+/=?^`{|}~-]"
_DOMAIN_TAIL: Final = r"(?:[\w-]|\.[\w-])"
_LINE_BREAKS: Final = "\n\r"


@dataclass(frozen=True, slots=True)
class Exported:
    """A table ``queries.<owner>.export_pii`` exports in ``section``; see the module
    docstring."""

    owner: str
    section: str
    shown_as: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    withheld: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class NotAboutAContact:
    """A table holding personal data, none of it about a Contact, and why."""

    owner: str
    reason: str


type Coverage = Exported | NotAboutAContact

# Why the engine's error text isn't exported: the rows never name a Contact.
UNLINKED: Final = (
    "never linked to a Contact, so a name an error message carries by chance can't be "
    "attributed to anyone"
)

COVERAGE: Final[Mapping[str, Coverage]] = MappingProxyType(
    {
        "contacts": Exported("research", "contact"),
        "provenance_records": Exported("research", "provenance_records"),
        "evidence": Exported("research", "evidence"),
        "crawled_pages": Exported(
            "research", "pages", shown_as=MappingProxyType({"text": "text_excerpts"})
        ),
        "page_contact_methods": Exported("research", "contact_methods"),
        "job_postings": Exported("research", "job_postings"),
        "suppression_entries": Exported(
            "compliance",
            "suppression",
            withheld=MappingProxyType(
                {"value_hash": "a key's hash: it would let the file be matched to the list"}
            ),
        ),
        "data_subject_requests": Exported("compliance", "requests"),
        "alerts": Exported("notify", "alerts"),
        "notifications": Exported("notify", "notifications"),
        "ai_test_cases": Exported("playbook", "override_cases"),
        "settings": NotAboutAContact(
            "setup", "Nextron's own settings: staff addresses, the sender and the postal address"
        ),
        "footer_snapshots": NotAboutAContact(
            "drafting", "Nextron's own footer text: the sender's name and the postal address"
        ),
        "linkedin_accounts": NotAboutAContact(
            "pipeline", "Nextron's own LinkedIn account: its label and the staff owner's name"
        ),
        "jobs": NotAboutAContact("jobqueue", UNLINKED),
        "backups": NotAboutAContact("ops", UNLINKED),
        "restore_tests": NotAboutAContact("ops", UNLINKED),
    }
)

EXTRA_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("research", "addresses"),
    ("research", "signals"),
    (EVENT_LOG, "contact_history"),
)


def _sections() -> Mapping[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    declared = [
        (entry.owner, entry.section) for entry in COVERAGE.values() if isinstance(entry, Exported)
    ]
    for owner, section in (*declared, *EXTRA_SECTIONS):
        found.setdefault(owner, []).append(section)
    return MappingProxyType({owner: tuple(sections) for owner, sections in found.items()})


SECTIONS: Final[Mapping[str, tuple[str, ...]]] = _sections()


@dataclass(frozen=True, slots=True)
class ContactExport:
    """The export file: its name and its UTF-8 JSON body."""

    filename: str
    body: bytes = field(repr=False)


# Mentions ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MentionTerm:
    """One way a text can mention a Contact: by ``name`` or by ``email``."""

    label: str
    pattern: re.Pattern[str] = field(repr=False)


def _name_term(name: object) -> MentionTerm | None:
    if not isinstance(name, str) or name.strip() == REDACTED:
        return None
    words = name.split()
    if len("".join(words)) < MIN_NAME_LENGTH:
        return None
    body = r"\s+".join(re.escape(word) for word in words)
    return MentionTerm(NAME_TERM, re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE))


def _email_term(email: object) -> MentionTerm | None:
    if not isinstance(email, str):
        return None
    address = email.strip()
    if "@" not in address or address == REDACTED or is_hash(address):
        return None
    pattern = rf"(?<!{_LOCAL_CHARACTERS}){re.escape(address)}(?!{_DOMAIN_TAIL})"
    return MentionTerm(EMAIL_TERM, re.compile(pattern, re.IGNORECASE))


def mention_terms(name: str | None, email: str | None) -> tuple[MentionTerm, ...]:
    """The terms by which a text mentions the Contact; see the module docstring."""
    return tuple(term for term in (_name_term(name), _email_term(email)) if term is not None)


def mentions(text: str | None, terms: Iterable[MentionTerm]) -> tuple[str, ...]:
    """The labels of the ``terms`` that ``text`` mentions, each once, in term order."""
    if not isinstance(text, str):
        return ()
    found = [term.label for term in terms if term.pattern.search(text) is not None]
    return tuple(dict.fromkeys(found))


def _line_start(text: str, at: int) -> int:
    return max(text.rfind(mark, 0, at) for mark in _LINE_BREAKS) + 1


def _line_end(text: str, at: int) -> int:
    ends = [found for found in (text.find(mark, at) for mark in _LINE_BREAKS) if found >= 0]
    return min(ends, default=len(text))


def _window(text: str, line: tuple[int, int], match: tuple[int, int]) -> str:
    """The line, or the ``MAX_EXCERPT_LENGTH`` characters of it around the match."""
    line_start, line_end = line
    start, end = match
    if line_end - line_start <= MAX_EXCERPT_LENGTH:
        return text[line_start:line_end]
    if end - start >= MAX_EXCERPT_LENGTH:
        return text[start : start + MAX_EXCERPT_LENGTH]
    left = max(line_start, start - (MAX_EXCERPT_LENGTH - (end - start)) // 2)
    right = min(line_end, left + MAX_EXCERPT_LENGTH)
    left = max(line_start, right - MAX_EXCERPT_LENGTH)
    return text[left:right]


def excerpts(text: str | None, terms: Iterable[MentionTerm]) -> tuple[str, ...]:
    """The lines of ``text`` that mention a term, each at most ``MAX_EXCERPT_LENGTH``
    characters, at most ``MAX_EXCERPTS``; see the module docstring."""
    listed = tuple(terms)
    if not isinstance(text, str) or not listed:
        return ()
    combined = re.compile(
        "|".join(f"(?:{term.pattern.pattern})" for term in listed), re.IGNORECASE
    )
    found: list[str] = []
    given_to = 0  # the end of the last line span given
    for match in combined.finditer(text):
        if match.start() < given_to:
            continue
        line = (_line_start(text, match.start()), _line_end(text, match.end()))
        given_to = line[1]
        excerpt = _window(text, line, match.span()).strip()
        if excerpt:
            found.append(excerpt)
        if len(found) == MAX_EXCERPTS:
            break
    return tuple(found)


# Addresses and suppression facts ----------------------------------------------------------------


def address_history(
    records: Iterable[Mapping[str, object]], email: str | None
) -> tuple[dict[str, object], ...]:
    """Each address the ``address`` records name, with its Address Class history; see the
    module docstring."""
    found: dict[str, list[dict[str, object]]] = {}
    for record in records:
        address = record.get("address")
        if record.get("subject") != ADDRESS_SUBJECT or not isinstance(address, str):
            continue
        found.setdefault(address, []).append(
            {
                "address_class": record.get("address_class"),
                "source_kind": record.get("source_kind"),
                "retrieved_at": record.get("retrieved_at"),
            }
        )
    return tuple(
        {"address": address, "current": address == email, "history": history}
        for address, history in found.items()
    )


def _plain(value: str | None) -> str | None:
    """``value`` when it can be normalised into a key: not a tombstone, and not a hash."""
    if value is None or value.strip() == REDACTED or is_hash(value):
        return None
    return value


def export_hashes(
    *,
    email: str | None,
    name: str | None,
    linkedin_url: str | None,
    web_domain: str | None,
    addresses: Iterable[str | None] = (),
) -> frozenset[str]:
    """The ``value_hash`` of every suppression key held for the Contact; see the module
    docstring."""
    listed = tuple(addresses)
    facts = ContactFacts(
        LeadFacts(web_domain),
        email=_plain(email),
        name=_plain(name),
        linkedin_url=_plain(linkedin_url),
    )
    hashes = {key.value_hash for key in lookup_keys(facts)}
    for value in (email, name, linkedin_url, *listed):
        if value is not None and is_hash(value):
            hashes.add(value)
    for address in listed:
        plain = _plain(address)
        if plain is not None:
            keys = person_keys(email=plain, name=None, linkedin_url=None, web_domain=None)
            hashes.update(key.value_hash for key in keys)
    return frozenset(hashes)


# The file ---------------------------------------------------------------------------------------


def assemble(
    contact_id: str,
    generated_at: datetime,
    parts: Mapping[str, Mapping[str, Iterable[Mapping[str, object]]]],
) -> dict[str, object]:
    """The export document; see the module docstring."""
    if not is_ulid(contact_id):
        raise ValueError("assemble: contact_id must be a ULID")
    if not isinstance(generated_at, datetime):
        raise TypeError(f"assemble: generated_at is a datetime, not {type(generated_at).__name__}")
    if generated_at.utcoffset() is None:
        raise ValueError("assemble: generated_at must be an aware datetime")
    for owner, sections in parts.items():
        declared = SECTIONS.get(owner)
        if declared is None:
            raise ValueError(f"assemble: {owner} declares no export section")
        for section in sections:
            if section not in declared:
                raise ValueError(f"assemble: {owner}.{section} is not a declared section")
    document: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "contact_id": contact_id,
    }
    for owner, sections in SECTIONS.items():
        given = parts.get(owner, {})
        document[owner] = {
            section: [dict(row) for row in given.get(section, ())] for section in sections
        }
    return document


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("render: a naive datetime has no time zone")
        return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"render: a {type(value).__name__} has no JSON form")


def render(document: Mapping[str, object]) -> bytes:
    """The document as UTF-8 JSON; see the module docstring."""
    text = json.dumps(
        dict(document), indent=2, ensure_ascii=False, allow_nan=False, default=_json_value
    )
    return text.encode("utf-8")


def filename(contact_id: str) -> str:
    """The export's file name, ``contact-<ULID>-export.json``."""
    if not is_ulid(contact_id):
        raise ValueError("filename: contact_id must be a ULID")
    return f"contact-{contact_id}-export.json"
