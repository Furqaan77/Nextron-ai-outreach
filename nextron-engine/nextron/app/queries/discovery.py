"""Read API of the ``discovery`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.discovery`` only. All read through the
current unit of work, read-only ones included.

- ``lead(lead_id)``: the Lead's identity group as a ``StoredLead`` (a merged Lead included:
  its ``merged_into`` names the survivor), or ``None`` when no Lead has that id (a text that
  isn't a ULID included).
- ``lead_by_web_domain(web_domain)`` (Story 3.5): the Lead whose stored ``web_domain`` (a
  registrable domain) is exactly ``web_domain``, as a ``StoredLead``, or ``None`` (a value that
  isn't a ``str`` included). ``Compliance.suppress`` reads it for a domain the Operator typed.
- ``survivor_of(lead_id)`` (Story 2.4): the Lead that ``lead_id`` ends up as, following
  ``merged_into`` to the Lead that isn't merged through at most ``MAX_MERGE_HOPS`` (32) hops:
  the Lead itself when it isn't merged. ``None`` for a text that isn't a ULID, an unknown id,
  a chain that leads to no Lead, and a chain longer than the bound (a loop included). Story
  2.5's jobs reuse it.
- ``lead_list(*, stage=None, segment_id=None, search=None, page=1, website_needed=False)``
  (Story 2.4): one page of
  the Leads list as a ``LeadListPage``: at most ``LEADS_PER_PAGE`` (50) ``ListedLead`` rows,
  newest first (by ``created_at``, then ``id``), the ``total`` that match, the ``page`` shown
  and the number of ``pages`` (``max(1, ceil(total / 50))``). Merged Leads are left out; every
  stage, Disqualified included, is listed. ``stage`` keeps the Leads at that
  ``PipelineStage``, ``segment_id`` those in that Segment, and ``search`` those whose name or
  website domain contains it (ASCII letters ignore case; ``%`` and ``_`` are literal); a
  blank ``search`` is none. A ``page`` past the last shows the last. A ``page`` that isn't an
  ``int`` (a ``bool`` included), a ``stage`` that isn't a ``PipelineStage``, or a
  ``segment_id`` or ``search`` that isn't a ``str`` raises ``TypeError``; a ``page`` below 1
  raises ``ValueError``. It reads the pipeline group's ``pipeline_stage`` column of the same
  rows, read-only. ``website_needed`` (Story 8.4, the "Website needed" filter) keeps only the
  Leads with no ``web_domain``; a value that isn't a ``bool`` raises ``TypeError``.
- ``merged_lead_ids(lead_id)`` (Story 3.13): the ids of every Lead whose merge chain leads to
  ``lead_id``, by id; ``()`` for a text that isn't a ULID. An erasure nulls their pages' text
  too, since a merge leaves a page whose URL the survivor holds on the merged Lead.
  rows, read-only. Story 2.15: each row also carries its ``tier`` and ``lead_score`` (the score
  group's, read-only; ``None`` until scored), and ``sort=SORT_TIER`` (``"tier"``) orders by
  Tier, A to C, unscored last, then Lead Score descending, then newest; ``sort`` ``None`` is
  newest first, and any other value raises ``ValueError``.
- ``recent_runs(limit=20)`` (Story 8.3): the newest ``limit`` discovery runs, as ``StoredRun``
  rows, newest first (by ``started_at``, then ``id``). A ``limit`` that isn't an ``int`` from 1
  to ``MAX_RECENT_RUNS`` (200) raises ``ValueError``.
- ``signal_item(item_id)`` (Story 2.16): the Signal Inbox item as a ``StoredItem``, or ``None``
  (a text that isn't a ULID included); ``signal_items(limit=SIGNAL_ITEMS_SHOWN)``: the newest
  items, newest first (by ``submitted_at``, then ``id``), at most ``limit`` (100 by default).
- ``contact_items(lead_id, name, emails)`` (Story 2.16): every column of each Signal Inbox
  item that may hold the Contact's data: each item of the Contact's Lead and of the Leads
  merged into it, and each item with no Lead (``pending`` or ``flagged``) that mentions the
  Contact (``item_mentions``) or whose ``parsed_website`` is one of those Leads' web domains;
  by id; ``()`` for a ``lead_id`` that isn't a ULID. ``item_mentions(item, terms)``: the labels
  of the ``terms`` that the item's text, author, company, parsed author or quote mentions;
  ``contact_terms(name, emails)``: ``nextron.domain.contact_export.mention_terms`` of ``name``
  and of each of ``emails``.
- ``export_pii(lead_id, name, emails)`` (Story 2.16, the subject access export): the
  ``signal_inbox_items`` section: each of ``contact_items`` that mentions the Contact, with
  every column but ``text``, its ``mentions`` and ``text_excerpts`` (the lines of the text
  that mention the Contact: the rest is other people's data); ``()`` for a text that isn't a
  ULID.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Final

from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.repos.discovery import signal_inbox as signal_inbox_repo
from nextron.db.repos.discovery.leads import ListedLead, StoredLead
from nextron.db.repos.discovery.runs import StoredRun
from nextron.db.repos.discovery.signal_inbox import StoredItem
from nextron.domain.contact_export import MentionTerm, excerpts, mention_terms, mentions
from nextron.domain.ids import is_ulid
from nextron.domain.pipeline_stage import PipelineStage

__all__ = [
    "LEADS_PER_PAGE",
    "MAX_MERGE_HOPS",
    "MAX_RECENT_RUNS",
    "SIGNAL_ITEMS_SHOWN",
    "SORT_TIER",
    "LeadListPage",
    "ListedLead",
    "StoredItem",
    "StoredLead",
    "StoredRun",
    "lead",
    "lead_by_web_domain",
    "lead_list",
    "export_pii",
    "merged_lead_ids",
    "recent_runs",
    "signal_item",
    "signal_items",
    "survivor_of",
]

SIGNAL_ITEMS_SHOWN: Final = 100

LEADS_PER_PAGE: Final = 50
MAX_MERGE_HOPS: Final = 32
SORT_TIER: Final = leads_repo.TIER_SORT
MAX_RECENT_RUNS: Final = 200


@dataclass(frozen=True, slots=True)
class LeadListPage:
    """One page of the Leads list: its rows, how many Leads match, the page shown (from 1) and
    the number of pages (at least 1)."""

    leads: tuple[ListedLead, ...]
    total: int
    page: int
    pages: int


def lead(lead_id: str) -> StoredLead | None:
    """The Lead ``lead_id``, or ``None``."""
    if not is_ulid(lead_id):
        return None
    return leads_repo.get(lead_id)


def lead_by_web_domain(web_domain: str) -> StoredLead | None:
    """The Lead holding ``web_domain``, or ``None``."""
    if not isinstance(web_domain, str):
        return None
    return leads_repo.by_web_domain(web_domain)


def survivor_of(lead_id: str) -> StoredLead | None:
    """The Lead that isn't merged at the end of ``lead_id``'s merge chain, or ``None``."""
    if not is_ulid(lead_id):
        return None
    found = leads_repo.get(lead_id)
    for _ in range(MAX_MERGE_HOPS):
        if found is None or found.merged_into is None:
            return found
        found = leads_repo.get(found.merged_into)
    return found if found is not None and found.merged_into is None else None


def lead_list(
    *,
    stage: PipelineStage | None = None,
    segment_id: str | None = None,
    search: str | None = None,
    page: int = 1,
    sort: str | None = None,
    website_needed: bool = False,
) -> LeadListPage:
    """One page of the Leads that aren't merged and match the filters, newest first."""
    if not isinstance(website_needed, bool):
        raise TypeError("website_needed must be a bool")
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError(f"page must be an int, not {type(page).__name__}")
    if page < 1:
        raise ValueError("page must be at least 1")
    if stage is not None and not isinstance(stage, PipelineStage):
        raise TypeError(f"stage must be a PipelineStage, not {type(stage).__name__}")
    for name, value in (("segment_id", segment_id), ("search", search)):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    if sort is not None and sort != SORT_TIER:
        raise ValueError("sort must be None or tier")
    if search is not None and not search.strip():
        search = None
    stored_stage = None if stage is None else stage.value
    total = leads_repo.count_listed(
        stage=stored_stage, segment_id=segment_id, search=search, website_needed=website_needed
    )
    pages = max(1, ceil(total / LEADS_PER_PAGE))
    shown = min(page, pages)
    rows = leads_repo.listed(
        stage=stored_stage,
        segment_id=segment_id,
        search=search,
        limit=LEADS_PER_PAGE,
        offset=(shown - 1) * LEADS_PER_PAGE,
        sort=sort,
        website_needed=website_needed,
    )
    return LeadListPage(leads=rows, total=total, page=shown, pages=pages)


def merged_lead_ids(lead_id: str) -> tuple[str, ...]:
    """The ids of the Leads whose merge chain leads to ``lead_id``, by id."""
    if not is_ulid(lead_id):
        return ()
    return leads_repo.merged_from(lead_id)


def recent_runs(limit: int = 20) -> tuple[StoredRun, ...]:
    """The newest ``limit`` discovery runs, newest first."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECENT_RUNS:
        raise ValueError(f"limit must be an int from 1 to {MAX_RECENT_RUNS}")
    return runs_repo.recent(limit)
def signal_item(item_id: str) -> StoredItem | None:
    """The Signal Inbox item, or ``None``."""
    if not isinstance(item_id, str) or not is_ulid(item_id):
        return None
    return signal_inbox_repo.get(item_id)


def signal_items(limit: int = SIGNAL_ITEMS_SHOWN) -> tuple[StoredItem, ...]:
    """The newest Signal Inbox items, newest first."""
    return signal_inbox_repo.items(limit)


_ITEM_TEXT = "text"
_ITEM_MENTION_COLUMNS = (
    "author",
    "company",
    "parsed_author_name",
    "parsed_author_role",
    "parsed_quote",
)


def contact_terms(name: str | None, emails: Sequence[str]) -> tuple[MentionTerm, ...]:
    """The terms by which an item mentions the Contact: their name and each address."""
    terms = mention_terms(name, None)
    for email in emails:
        terms += mention_terms(None, email)
    return terms


def item_mentions(item: dict[str, object], terms: Sequence[MentionTerm]) -> tuple[str, ...]:
    """The labels of the ``terms`` that the item's text, author, company, parsed author or
    quote mentions."""
    text = item.get(_ITEM_TEXT)
    texts = [text if isinstance(text, str) else ""]
    texts += [str(item[key]) for key in _ITEM_MENTION_COLUMNS if item.get(key) is not None]
    return mentions(" \n ".join(texts), terms)


def contact_items(
    lead_id: str, name: str | None, emails: Sequence[str]
) -> tuple[dict[str, object], ...]:
    """The Signal Inbox items that may hold the Contact's data; see the module docstring."""
    if not isinstance(lead_id, str) or not is_ulid(lead_id):
        return ()
    leads = (lead_id, *leads_repo.merged_from(lead_id))
    domains = set()
    for each in leads:
        lead = leads_repo.get(each)
        if lead is not None and lead.web_domain is not None:
            domains.add(lead.web_domain)
    terms = contact_terms(name, emails)
    found = list(signal_inbox_repo.of_leads(leads))
    for item in signal_inbox_repo.unlinked():
        if item.get("parsed_website") in domains or item_mentions(item, terms):
            found.append(item)
    return tuple(sorted(found, key=lambda item: str(item["id"])))


def export_pii(
    lead_id: str, name: str | None, emails: Sequence[str]
) -> dict[str, tuple[dict[str, object], ...]]:
    """The Signal Inbox items that mention the Contact; see the module docstring."""
    terms = contact_terms(name, emails)
    found: list[dict[str, object]] = []
    for item in contact_items(lead_id, name, emails):
        text = item.get(_ITEM_TEXT)
        named = item_mentions(item, terms)
        if named:
            shown = {key: value for key, value in item.items() if key != _ITEM_TEXT}
            found.append(
                {
                    **shown,
                    "mentions": list(named),
                    "text_excerpts": list(excerpts(text if isinstance(text, str) else None, terms)),
                }
            )
    return {"signal_inbox_items": tuple(found)}
