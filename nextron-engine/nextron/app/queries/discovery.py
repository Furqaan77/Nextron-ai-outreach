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
- ``lead_list(*, stage=None, segment_id=None, search=None, page=1)`` (Story 2.4): one page of
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
  rows, read-only.
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
"""

from dataclasses import dataclass
from math import ceil
from typing import Final

from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.repos.discovery.leads import ListedLead, StoredLead
from nextron.db.repos.discovery.runs import StoredRun
from nextron.domain.ids import is_ulid
from nextron.domain.pipeline_stage import PipelineStage

__all__ = [
    "LEADS_PER_PAGE",
    "MAX_MERGE_HOPS",
    "MAX_RECENT_RUNS",
    "SORT_TIER",
    "LeadListPage",
    "ListedLead",
    "StoredLead",
    "StoredRun",
    "lead",
    "lead_by_web_domain",
    "lead_list",
    "merged_lead_ids",
    "recent_runs",
    "survivor_of",
]

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
) -> LeadListPage:
    """One page of the Leads that aren't merged and match the filters, newest first."""
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
    total = leads_repo.count_listed(stage=stored_stage, segment_id=segment_id, search=search)
    pages = max(1, ceil(total / LEADS_PER_PAGE))
    shown = min(page, pages)
    rows = leads_repo.listed(
        stage=stored_stage,
        segment_id=segment_id,
        search=search,
        limit=LEADS_PER_PAGE,
        offset=(shown - 1) * LEADS_PER_PAGE,
        sort=sort,
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
