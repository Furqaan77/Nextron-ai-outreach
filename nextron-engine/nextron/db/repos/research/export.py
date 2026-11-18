"""What a subject access export reads of ``research``'s tables (FR-52, AD-3, AD-17, Story 3.15).

It only reads, through the current unit of work's session (``nextron.db.uow.current()``), so it
works in a read-only unit of work too. Every row comes back as a ``dict`` of every column of its
table, from a generic ``select(<table>)``: no column is listed by hand, so a column a later story
adds is exported without code (``tests/contracts/test_export_contracts.py`` checks the export
still carries every personal-data column). Values keep their stored types: an aware ``datetime``,
a ``date``, an ``int``, a ``bool`` or a ``str`` (``evidence.pages_checked`` is its JSON text).

- ``contact(contact_id)``: the ``contacts`` row, or ``None``;
- ``provenance(contact_id)``: the Contact's ``provenance_records``, by ``retrieved_at``, then
  ``id``;
- ``person_evidence(contact_id)``: every ``evidence`` row whose ``person_contact_id`` is the
  Contact, superseded and retracted ones included, by ``created_at``, then ``id``;
- ``signals_of(evidence_ids)``: every ``signals`` row a ``signal_evidence`` link ties to one of
  ``evidence_ids``, retracted ones included, by ``first_seen_at``, then ``id``, each with
  ``evidence_ids``: the linked ids among ``evidence_ids``, sorted;
- ``lead_pages(lead_id)``: the Lead's ``crawled_pages``, by ``url``, then ``id``;
- ``lead_postings(lead_id)`` (Story 2.11): the Lead's ``job_postings``, by ``first_seen_at``,
  then ``id``;
- ``lead_contact_methods(lead_id)``: the ``page_contact_methods`` of the Lead's pages, each
  with ``page_url``, its page's ``url``, by that URL, then ``method``, ``signature``,
  ``source`` and ``id``.
"""

from collections.abc import Collection
from typing import Final

from sqlalchemy import Executable, select

from nextron.db.models.crawl import (
    CrawledPageRecord,
    JobPostingRecord,
    PageContactMethodRecord,
)
from nextron.db.models.research import ContactRecord, ProvenanceRecord
from nextron.db.models.signals import EvidenceRecord, SignalEvidenceRecord, SignalRecord
from nextron.db.uow import current

CONTACTS: Final = ContactRecord.__table__
PROVENANCE: Final = ProvenanceRecord.__table__
EVIDENCE: Final = EvidenceRecord.__table__
SIGNALS: Final = SignalRecord.__table__
LINKS: Final = SignalEvidenceRecord.__table__
PAGES: Final = CrawledPageRecord.__table__
METHODS: Final = PageContactMethodRecord.__table__
POSTINGS: Final = JobPostingRecord.__table__
PAGE_URL: Final = "page_url"
EVIDENCE_IDS: Final = "evidence_ids"


def _rows(statement: Executable) -> tuple[dict[str, object], ...]:
    return tuple(dict(row._mapping) for row in current().session.execute(statement))


def contact(contact_id: str) -> dict[str, object] | None:
    """Every column of the Contact ``contact_id``, or ``None``."""
    rows = _rows(select(CONTACTS).where(CONTACTS.c.id == contact_id))
    return rows[0] if rows else None


def provenance(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of the Contact's Provenance Records, oldest first."""
    return _rows(
        select(PROVENANCE)
        .where(PROVENANCE.c.contact_id == contact_id)
        .order_by(PROVENANCE.c.retrieved_at, PROVENANCE.c.id)
    )


def person_evidence(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of each Evidence row about the Contact, oldest first."""
    return _rows(
        select(EVIDENCE)
        .where(EVIDENCE.c.person_contact_id == contact_id)
        .order_by(EVIDENCE.c.created_at, EVIDENCE.c.id)
    )


def signals_of(evidence_ids: Collection[str]) -> tuple[dict[str, object], ...]:
    """Every column of each Signal linked to one of ``evidence_ids``, with its linked
    ``evidence_ids``, first seen first."""
    if not evidence_ids:
        return ()
    wanted = sorted(evidence_ids)
    linked: dict[str, list[str]] = {}
    for signal_id, evidence_id in current().session.execute(
        select(LINKS.c.signal_id, LINKS.c.evidence_id).where(LINKS.c.evidence_id.in_(wanted))
    ):
        linked.setdefault(signal_id, []).append(evidence_id)
    if not linked:
        return ()
    rows = _rows(
        select(SIGNALS)
        .where(SIGNALS.c.id.in_(sorted(linked)))
        .order_by(SIGNALS.c.first_seen_at, SIGNALS.c.id)
    )
    return tuple({**row, EVIDENCE_IDS: sorted(linked[str(row["id"])])} for row in rows)


def lead_pages(lead_id: str) -> tuple[dict[str, object], ...]:
    """Every column of the Lead's crawled pages, by URL."""
    return _rows(
        select(PAGES).where(PAGES.c.lead_id == lead_id).order_by(PAGES.c.url, PAGES.c.id)
    )


def lead_contact_methods(lead_id: str) -> tuple[dict[str, object], ...]:
    """Every column of the contact methods of the Lead's pages, each with its page's URL."""
    return _rows(
        select(METHODS, PAGES.c.url.label(PAGE_URL))
        .join(PAGES, PAGES.c.id == METHODS.c.page_id)
        .where(PAGES.c.lead_id == lead_id)
        .order_by(
            PAGES.c.url,
            METHODS.c.method,
            METHODS.c.signature,
            METHODS.c.source,
            METHODS.c.id,
        )
    )


def lead_postings(lead_id: str) -> tuple[dict[str, object], ...]:
    """Every column of the Lead's job postings, first seen first."""
    return _rows(
        select(POSTINGS)
        .where(POSTINGS.c.lead_id == lead_id)
        .order_by(POSTINGS.c.first_seen_at, POSTINGS.c.id)
    )
