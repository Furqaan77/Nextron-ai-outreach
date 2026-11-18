"""Read API of the ``research`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.research`` only. All read through the
current unit of work, read-only ones included.

- ``contact(contact_id)``: the Contact as a ``StoredContact``, or ``None`` when no Contact has
  that id (a text that isn't a ULID included).
- ``contacts(lead_id)``: the Lead's Contacts as ``StoredContact`` rows, oldest first (by
  ``created_at``, then ``id``); empty for an unknown Lead (a text that isn't a ULID included).
- ``contacts_with_email(email)`` (Story 3.5): the Contacts whose stored ``email`` (always
  normalised, ``identity.normalize_email``) is exactly ``email``, oldest first; empty for a
  value that isn't a ``str``. ``Compliance.suppress`` reads it for an address the Operator
  typed.
- ``provenance(contact_id)``: the Contact's Provenance Records as ``StoredProvenance`` rows, by
  ``retrieved_at``, then ``id``; empty for an unknown Contact.
- ``lead_provenance(lead_id)`` (Story 2.4): the Provenance Records of every Contact of the
  Lead, in one read: each Contact id that has records, mapped to them by ``retrieved_at``,
  then ``id``; ``{}`` for an unknown Lead (a text that isn't a ULID included).
- ``crawl_run(run_id)`` (Story 2.5): the crawl run as a ``StoredCrawlRun``, or ``None``;
  ``None`` for a text that isn't a ULID, like the reads below.
- ``latest_crawl_run(lead_id)``: the Lead's run started last, or ``None``. The Lead page
  (Story 2.4) shows its outcome with ``nextron.domain.crawl.OUTCOME_LABELS`` ("website
  unreachable").
- ``crawled_pages(lead_id)``: the Lead's stored pages, by URL, each a ``StoredPage`` (whose
  ``text`` is ``None`` once purged).
- ``ats_links(lead_id)``: the ATS board links found on the Lead's website, by URL.
"""

from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.repos.research.crawl import StoredAtsLink, StoredCrawlRun, StoredPage
from nextron.db.repos.research.provenance import StoredProvenance
from nextron.domain.ids import is_ulid

__all__ = [
    "StoredAtsLink",
    "StoredContact",
    "StoredCrawlRun",
    "StoredPage",
    "StoredProvenance",
    "ats_links",
    "contact",
    "contacts",
    "contacts_with_email",
    "crawl_run",
    "crawled_pages",
    "latest_crawl_run",
    "lead_provenance",
    "provenance",
]


def contact(contact_id: str) -> StoredContact | None:
    """The Contact ``contact_id``, or ``None``."""
    if not is_ulid(contact_id):
        return None
    return contacts_repo.get(contact_id)


def contacts(lead_id: str) -> tuple[StoredContact, ...]:
    """The Contacts of the Lead ``lead_id``, oldest first."""
    if not is_ulid(lead_id):
        return ()
    return contacts_repo.for_lead(lead_id)


def contacts_with_email(email: str) -> tuple[StoredContact, ...]:
    """The Contacts whose ``email`` is ``email``, oldest first."""
    if not isinstance(email, str):
        return ()
    return contacts_repo.with_email(email)


def provenance(contact_id: str) -> tuple[StoredProvenance, ...]:
    """The Provenance Records of the Contact ``contact_id``, oldest first."""
    if not is_ulid(contact_id):
        return ()
    return provenance_repo.for_contact(contact_id)


def lead_provenance(lead_id: str) -> dict[str, tuple[StoredProvenance, ...]]:
    """The Provenance Records of the Lead ``lead_id``'s Contacts, by Contact id, oldest
    first."""
    if not is_ulid(lead_id):
        return {}
    grouped: dict[str, list[StoredProvenance]] = {}
    for record in provenance_repo.for_lead(lead_id):
        grouped.setdefault(record.contact_id, []).append(record)
    return {contact_id: tuple(records) for contact_id, records in grouped.items()}


def crawl_run(run_id: str) -> StoredCrawlRun | None:
    """The crawl run ``run_id``, or ``None``."""
    return crawl_repo.run(run_id) if is_ulid(run_id) else None


def latest_crawl_run(lead_id: str) -> StoredCrawlRun | None:
    """The Lead's crawl run started last, or ``None``."""
    return crawl_repo.latest_run(lead_id) if is_ulid(lead_id) else None


def crawled_pages(lead_id: str) -> tuple[StoredPage, ...]:
    """The Lead's stored pages, by URL."""
    return crawl_repo.pages(lead_id) if is_ulid(lead_id) else ()


def ats_links(lead_id: str) -> tuple[StoredAtsLink, ...]:
    """The Lead's ATS board links, by URL."""
    return crawl_repo.ats_links(lead_id) if is_ulid(lead_id) else ()
