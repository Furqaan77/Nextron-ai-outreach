"""Read API of the ``research`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.research`` only. All read through the
current unit of work, read-only ones included.

- ``contact(contact_id)``: the Contact as a ``StoredContact``, or ``None`` when no Contact has
  that id (a text that isn't a ULID included).
- ``contacts(lead_id)``: the Lead's Contacts as ``StoredContact`` rows, oldest first (by
  ``created_at``, then ``id``); empty for an unknown Lead (a text that isn't a ULID included).
- ``erased_contacts()`` (Story 3.16): ``(id, erased_at)`` of every erased Contact, by
  ``erased_at``, then ``id``.
- ``contacts_with_email(email)`` (Story 3.5): the Contacts whose stored ``email`` (always
  normalised, ``identity.normalize_email``) is exactly ``email``, oldest first; empty for a
  value that isn't a ``str``. ``Compliance.suppress`` reads it for an address the Operator
  typed.
- ``contacts_with_address_hash(address_hash)`` (Story 3.11): the Contacts whose stored ``email``
  hashes (``key_hash("address", email)``) to ``address_hash``, plus the erased Contacts whose
  stored ``email`` (the tombstone: the address hash itself) is ``address_hash``, oldest first;
  empty for a value that isn't a 64-character lowercase hex hash. It scans every Contact with
  an ``email``: no column holds an address hash. ``Compliance.unsubscribe_by_token`` reads it.
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
- ``contact_methods(lead_id)`` (Story 2.6): the Lead's profile value, a
  ``nextron.domain.widgets.ContactMethods`` (every page checked, and each method's pages), or
  ``None`` before any run set it (or for an unknown Lead).
- ``page_contact_methods(lead_id)``: the contact methods detected on each of the Lead's pages,
  as ``StoredContactMethod`` rows (``matched`` hidden from the ``repr``), by page URL, then
  method, signature and source.

Story 2.9 adds the Signals and their Evidence (a text that isn't a ULID reads as an unknown
Lead: empty, or ``None``):

- ``signals(lead_id)``: the Lead's Signals that aren't retracted, as ``StoredSignal`` rows, by
  ``first_seen_at``, then ``id``.
- ``evidence(lead_id)``: the Lead's live Evidence (neither superseded nor retracted), as
  ``StoredEvidence`` rows (``pages_checked`` a tuple of URLs), oldest first.
- ``hq_tz(lead_id)``: the Lead's headquarters zone, or ``None``.
- ``last_ok_extraction(lead_id)``: the Lead's ``ok`` ``extract_signals`` run started last, as
  a ``StoredExtractionRun``, or ``None``; ``extraction_runs(lead_id)``: every run (of every
  route), oldest first.
- ``lead_signals(lead_id, now, type_names) -> LeadSignalsView``: what the Lead page shows, read
  at once: ``hq_tz``; each current Signal as a ``SignalStatus(signal, name, age, live)``, its
  ``name`` from ``type_names`` (a Signal type key to its Segment's name) or else its key, its
  ``age`` from ``nextron.domain.signals.age_days`` at ``now`` and ``live`` from ``is_live``
  (a Signal whose stored class or Tier the domain refuses counts as expired); and each live
  Evidence row as an ``EvidenceSupport(evidence, supports)``, ``supports`` being the names of
  the current Signals it supports, in the Signals' order.

Story 2.11 adds ``job_postings(lead_id)``: the Lead's job postings as ``StoredPosting`` rows
(title and description hidden from the ``repr``), the live ones first, then the newest first
seen (then by ``id``); empty for an unknown Lead (a text that isn't a ULID included). A crawl
run's ``StoredCrawlRun`` carries its posting counts (``careers_postings``, ...,
``board_errors``; ``None`` until a check writes them).

Story 2.15 adds ``signal_evidence_ids(lead_id) -> dict[str, tuple[str, ...]]``: each of the
Lead's Signals (retracted ones included) that has live Evidence, mapped to the ids of that live
Evidence, sorted; ``{}`` for an unknown Lead (a text that isn't a ULID included).

Story 2.10 adds ``no_decision_maker(lead_id)``: ``True`` when the Lead's last Primary Contact
selection found no decision-maker, ``False`` when it found one, ``None`` before the first
selection (and for an unknown Lead). A Contact's ``StoredContact`` row carries
``primary_set_by`` (``system`` or ``operator``) beside ``is_primary``.

Story 2.12 adds ``legal_form(lead_id)``: the Lead's legal form as a ``StoredLegalForm``
(``legal_form``, ``company_status``, ``legal_form_status``, ``site_evidence_id``,
``registry_evidence_id``, each ``None`` until the Lead is first checked), or ``None`` for an
unknown Lead (a text that isn't a ULID included).

Story 3.15 adds the subject access exporter (``nextron.domain.contact_export``):

- ``export_pii(contact_id) -> {section: tuple[dict, ...]}``: everything ``research`` holds on
  the Contact, every row read with every column (``nextron.db.repos.research.export``), so a
  column a later story adds is exported without code. An unknown Contact (a text that isn't a
  ULID included) raises ``LookupError``, whose message holds no id. The sections:

  - ``contact``: the one ``contacts`` row;
  - ``provenance_records``: every record, every eligibility basis included, oldest first;
  - ``addresses``: ``contact_export.address_history`` of those records and the stored email;
  - ``evidence``: every row whose ``person_contact_id`` is the Contact (superseded and
    retracted ones too), its ``pages_checked`` decoded to a list;
  - ``signals``: the Signals that Evidence supports, each with its ``evidence_ids``;
  - ``pages``: the Lead's crawled pages whose stored ``text`` mentions the Contact
    (``contact_export.mentions`` of the terms: ``mention_terms(name, email)``, then one
    ``email`` term, ``mention_terms(None, address)``, per other distinct ``address`` in the
    Contact's Provenance, so a page naming only an earlier address is found, or one of whose
    ``mailto`` addresses does, Story 2.13): every column but ``text``, with ``mailto`` only the
    addresses that mention the Contact (a list), plus ``mentions`` (``name``, ``email``, each once) and ``text_excerpts``
    (``contact_export.excerpts``); never a whole page, since the rest is the firm's and other
    people's data;
  - ``contact_methods``: the Lead's pages' contact methods whose ``matched`` text mentions the
    Contact (the same terms), each with ``page_url`` and ``mentions``;
  - ``job_postings`` (Story 2.11): the Lead's postings whose ``title`` or ``description``
    mentions the Contact (the same terms), every column plus ``mentions``.

Story 3.14 (an erasure's receipt):

- ``evidence_history(lead_id)``: every Evidence row of the Lead, live, superseded or retracted,
  oldest first (a stored quote is held whether or not it is live).
- ``other_contacts_with_keys(contact_id, keys)``: the ids, sorted, of the Contacts other than
  ``contact_id``, on any Lead, that aren't erased and whose address key or profile key
  (``contactability.person_keys`` of their email and LinkedIn URL) is one of the address or
  profile ``SuppressionKey`` items in ``keys`` (others are ignored). It scans every Contact that
  isn't erased and has an email or a LinkedIn URL.
"""

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import export as export_repo
from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import profile as profile_repo
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.repos.research.contacts import StoredContact
from nextron.db.repos.research.crawl import (
    StoredAtsLink,
    StoredContactMethod,
    StoredCrawlRun,
    StoredPage,
)
from nextron.db.repos.research.postings import StoredPosting
from nextron.db.repos.research.profile import StoredLegalForm
from nextron.db.repos.research.provenance import StoredProvenance
from nextron.db.repos.research.signals import StoredEvidence, StoredExtractionRun, StoredSignal
from nextron.domain.contact_export import address_history, excerpts, mention_terms, mentions
from nextron.domain.contactability import KeyKind, SuppressionKey, is_hash, key_hash, person_keys
from nextron.domain.extraction import ROUTE as SIGNALS_ROUTE
from nextron.domain.identity import IdentityError
from nextron.domain.ids import is_ulid
from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live
from nextron.domain.widgets import ContactMethods, decode

__all__ = [
    "EvidenceSupport",
    "LeadSignalsView",
    "SignalStatus",
    "StoredAtsLink",
    "StoredContact",
    "StoredContactMethod",
    "StoredCrawlRun",
    "StoredEvidence",
    "StoredExtractionRun",
    "StoredLegalForm",
    "StoredPage",
    "StoredProvenance",
    "StoredSignal",
    "ats_links",
    "contact",
    "contact_methods",
    "contacts",
    "contacts_with_address_hash",
    "contacts_with_email",
    "crawl_run",
    "crawled_pages",
    "evidence",
    "export_pii",
    "extraction_runs",
    "hq_tz",
    "last_ok_extraction",
    "latest_crawl_run",
    "lead_provenance",
    "legal_form",
    "lead_signals",
    "no_decision_maker",
    "page_contact_methods",
    "provenance",
    "signal_evidence_ids",
    "signals",
]


@dataclass(frozen=True, slots=True)
class SignalStatus:
    """A current Signal as the Lead page shows it: its type's name, its age in days and whether
    it is still live."""

    signal: StoredSignal
    name: str
    age: int
    live: bool


@dataclass(frozen=True, slots=True)
class EvidenceSupport:
    """A live Evidence row and the names of the current Signals it supports."""

    evidence: StoredEvidence
    supports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LeadSignalsView:
    """The Lead page's Signals and Evidence, and the headquarters zone."""

    hq_tz: str | None
    signals: tuple[SignalStatus, ...]
    evidence: tuple[EvidenceSupport, ...]


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


def erased_contacts() -> tuple[tuple[str, datetime], ...]:
    """``(id, erased_at)`` of every erased Contact, by ``erased_at``, then ``id``."""
    return contacts_repo.erased()


def contacts_with_email(email: str) -> tuple[StoredContact, ...]:
    """The Contacts whose ``email`` is ``email``, oldest first."""
    if not isinstance(email, str):
        return ()
    return contacts_repo.with_email(email)


def _holds_address(contact: StoredContact, address_hash: str) -> bool:
    """Whether the Contact's stored ``email`` is the address ``address_hash`` hashes."""
    if contact.email is None:
        return False
    if contact.erased_at is not None:  # a tombstone holds the address hash itself
        return contact.email == address_hash
    try:
        return key_hash(KeyKind.ADDRESS, contact.email) == address_hash
    except (IdentityError, TypeError, ValueError):  # a stored value that no longer normalises
        return False


def contacts_with_address_hash(address_hash: str) -> tuple[StoredContact, ...]:
    """The Contacts holding the address ``address_hash`` hashes, oldest first."""
    if not is_hash(address_hash):
        return ()
    return tuple(
        contact
        for contact in contacts_repo.with_any_email()
        if _holds_address(contact, address_hash)
    )


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


def contact_methods(lead_id: str) -> ContactMethods | None:
    """The Lead's ``contact_methods`` profile value, or ``None``."""
    if not is_ulid(lead_id):
        return None
    value = profile_repo.contact_methods(lead_id)
    return None if value is None else decode(value)


def page_contact_methods(lead_id: str) -> tuple[StoredContactMethod, ...]:
    """The contact methods detected on the Lead's pages, by page URL."""
    return crawl_repo.contact_methods_of(lead_id) if is_ulid(lead_id) else ()


def signals(lead_id: str) -> tuple[StoredSignal, ...]:
    """The Lead's Signals that aren't retracted, first seen first."""
    return signals_repo.signals(lead_id, current_only=True) if is_ulid(lead_id) else ()


def evidence(lead_id: str) -> tuple[StoredEvidence, ...]:
    """The Lead's live Evidence, oldest first."""
    return signals_repo.evidence(lead_id, live_only=True) if is_ulid(lead_id) else ()


def evidence_history(lead_id: str) -> tuple[StoredEvidence, ...]:
    """Every Evidence row of the Lead, live or not, oldest first."""
    return signals_repo.evidence(lead_id, live_only=False) if is_ulid(lead_id) else ()


_MATCHED_KINDS = (KeyKind.ADDRESS, KeyKind.PROFILE)


def other_contacts_with_keys(contact_id: str, keys: Iterable[SuppressionKey]) -> tuple[str, ...]:
    """The other Contacts that aren't erased and share an address or profile key in ``keys``,
    by id."""
    wanted = {
        (key.kind, key.value_hash)
        for key in keys
        if isinstance(key, SuppressionKey) and key.kind in _MATCHED_KINDS
    }
    if not wanted:
        return ()
    found = []
    for other in contacts_repo.with_address_or_profile():
        if other.id == contact_id:
            continue
        theirs = person_keys(
            email=other.email, name=None, linkedin_url=other.linkedin_url, web_domain=None
        )
        if any((key.kind, key.value_hash) in wanted for key in theirs):
            found.append(other.id)
    return tuple(sorted(found))


def hq_tz(lead_id: str) -> str | None:
    """The Lead's headquarters zone, or ``None``."""
    found = profile_repo.headquarters(lead_id) if is_ulid(lead_id) else None
    return None if found is None else found.hq_tz


def last_ok_extraction(lead_id: str) -> StoredExtractionRun | None:
    """The Lead's ``ok`` ``extract_signals`` run started last, or ``None``."""
    return signals_repo.last_ok_run(lead_id, SIGNALS_ROUTE) if is_ulid(lead_id) else None


def no_decision_maker(lead_id: str) -> bool | None:
    """Whether the Lead's last Primary Contact selection found no decision-maker; ``None``
    before the first selection."""
    return profile_repo.no_decision_maker(lead_id) if is_ulid(lead_id) else None


def job_postings(lead_id: str) -> tuple[StoredPosting, ...]:
    """The Lead's job postings: live first, then the newest first seen; see the module
    docstring."""
    if not is_ulid(lead_id):
        return ()
    found = postings_repo.for_lead(lead_id)
    newest = sorted(found, key=lambda row: (row.first_seen_at, row.id), reverse=True)
    return tuple(sorted(newest, key=lambda row: not row.live))


def legal_form(lead_id: str) -> StoredLegalForm | None:
    """The Lead's legal form, or ``None`` for an unknown Lead."""
    return profile_repo.legal_form(lead_id) if is_ulid(lead_id) else None


def signal_evidence_ids(lead_id: str) -> dict[str, tuple[str, ...]]:
    """The ids of each of the Lead's Signals' live Evidence, sorted; see the module
    docstring."""
    if not is_ulid(lead_id):
        return {}
    live = {row.id for row in evidence(lead_id)}
    grouped: dict[str, list[str]] = {}
    for link in signals_repo.links(lead_id):
        if link.evidence_id in live:
            grouped.setdefault(link.signal_id, []).append(link.evidence_id)
    return {signal_id: tuple(sorted(ids)) for signal_id, ids in grouped.items()}


def extraction_runs(lead_id: str) -> tuple[StoredExtractionRun, ...]:
    """Every extraction run of the Lead, oldest first."""
    return signals_repo.runs(lead_id) if is_ulid(lead_id) else ()


def _status(signal: StoredSignal, name: str, now: datetime) -> SignalStatus:
    age = age_days(signal.event_date, signal.first_seen_at, now)
    try:
        live = is_live(SignalClass(signal.signal_class), signal.tier, age)
    except (TypeError, ValueError):  # a stored class or Tier the domain no longer knows
        live = False
    return SignalStatus(signal, name, age, live)


def lead_signals(lead_id: str, now: datetime, type_names: Mapping[str, str]) -> LeadSignalsView:
    """The Lead page's Signals, Evidence and headquarters zone at ``now``; see the module
    docstring."""
    current = signals(lead_id)
    named = {
        signal.id: type_names.get(signal.signal_type, signal.signal_type) for signal in current
    }
    supports: dict[str, list[str]] = {}
    for link in signals_repo.links(lead_id) if is_ulid(lead_id) else ():
        if link.signal_id in named:
            supports.setdefault(link.evidence_id, []).append(link.signal_id)
    order = {signal.id: index for index, signal in enumerate(current)}
    return LeadSignalsView(
        hq_tz=hq_tz(lead_id),
        signals=tuple(_status(signal, named[signal.id], now) for signal in current),
        evidence=tuple(
            EvidenceSupport(
                row,
                tuple(
                    named[signal_id]
                    for signal_id in sorted(supports.get(row.id, ()), key=order.__getitem__)
                ),
            )
            for row in evidence(lead_id)
        ),
    )


# The subject access exporter (Story 3.15) -------------------------------------------------------

type ExportRows = tuple[dict[str, object], ...]
PAGE_TEXT = "text"
PAGE_MAILTO = "mailto"  # Story 2.13: a JSON list of the page's mailto addresses, or NULL


def _mailto(value: object) -> list[str]:
    """The stored ``mailto`` JSON list's addresses; ``[]`` for NULL or anything else."""
    if not isinstance(value, str):
        return []
    try:
        found = json.loads(value)
    except ValueError:
        return []
    return [item for item in found if isinstance(item, str)] if isinstance(found, list) else []


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _evidence_row(row: dict[str, object]) -> dict[str, object]:
    pages = row.get("pages_checked")
    return {**row, "pages_checked": json.loads(pages) if isinstance(pages, str) else pages}


def export_pii(contact_id: str) -> dict[str, ExportRows]:
    """Everything ``research`` holds on the Contact, by section; see the module docstring."""
    found = export_repo.contact(contact_id) if is_ulid(contact_id) else None
    if found is None:
        raise LookupError("export: the Contact is unknown")
    lead_id = str(found["lead_id"])
    email = _text_or_none(found.get("email"))
    records = export_repo.provenance(contact_id)
    terms = mention_terms(_text_or_none(found.get("name")), email)
    earlier = dict.fromkeys(_text_or_none(record.get("address")) for record in records)
    for address in earlier:
        if address is not None and address != email:
            terms += mention_terms(None, address)
    evidence_rows = tuple(_evidence_row(row) for row in export_repo.person_evidence(contact_id))
    pages: list[dict[str, object]] = []
    for page in export_repo.lead_pages(lead_id):
        text = _text_or_none(page.get(PAGE_TEXT))
        # Story 2.13: only the page's mailto addresses that mention the Contact; the rest are the
        # firm's and other people's
        addresses = [
            address for address in _mailto(page.get(PAGE_MAILTO)) if mentions(address, terms)
        ]
        named = mentions("\n".join([text or "", *addresses]), terms)
        if named:
            shown = {key: value for key, value in page.items() if key != PAGE_TEXT}
            if PAGE_MAILTO in shown:
                shown[PAGE_MAILTO] = addresses
            pages.append(
                {**shown, "mentions": list(named), "text_excerpts": list(excerpts(text, terms))}
            )
    methods: list[dict[str, object]] = []
    for method in export_repo.lead_contact_methods(lead_id):
        named = mentions(_text_or_none(method.get("matched")), terms)
        if named:
            methods.append({**method, "mentions": list(named)})
    postings: list[dict[str, object]] = []
    for posting in export_repo.lead_postings(lead_id):
        texts = (_text_or_none(posting.get(key)) for key in ("title", "description"))
        named = mentions(" \n ".join(text for text in texts if text is not None), terms)
        if named:
            postings.append({**posting, "mentions": list(named)})
    return {
        "contact": (found,),
        "provenance_records": records,
        "addresses": address_history(records, email),
        "evidence": evidence_rows,
        "signals": export_repo.signals_of([str(row["id"]) for row in evidence_rows]),
        "pages": tuple(pages),
        "contact_methods": tuple(methods),
        "job_postings": tuple(postings),
    }
