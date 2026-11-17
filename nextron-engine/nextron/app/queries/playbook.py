"""Read API of the ``playbook`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.playbook`` only.

The Approved Company Facts (FR-72, Story 4.3). A fact's versions are append-only; its *current*
version is its highest version that isn't retracted, or none (a retraction retracts every
version, so a retracted fact has none until the Operator edits it again).

- ``company_facts()``: every fact as a ``FactHistory(fact_id, current, versions)``, by
  ``fact_id``. ``versions`` lists every version, newest first; ``current`` is a ``FactVersion``
  or ``None``. The Approved Company Facts page reads it.
- ``fact(fact_id)``: one ``FactHistory``, or ``None`` when the fact has no version.
- ``allowed_facts(segment_id, market_id)``: the current version of every fact whose allowed
  Segments hold ``segment_id`` and whose allowed Markets hold ``market_id`` (``segments.id`` and
  ``markets.id`` ULIDs), by ``fact_id``. A retracted fact is never returned. Drafting (Story
  4.15) and grounding (4.13, 4.14) read the facts a Draft may cite only through it, so ABA
  Formal Opinion 512 reaches only US Drafts and the SRA notice only UK Drafts.

``FactVersion(id, fact_id, version, content, created_by, created_at, retracted_at)`` is one
version: its row ``id``, its ``nextron.domain.company_facts.FactContent`` (with ULID references),
who made it (``FactCreator``), when, and when it was retracted (``None`` if it wasn't). A stored
row that no longer validates raises ``InvalidCompanyFacts``.

All read through the current unit of work, read-only ones included.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby

from nextron.db.repos.playbook import company_facts as company_facts_repo
from nextron.db.repos.playbook.company_facts import StoredFact
from nextron.domain.company_facts import FactContent, FactCreator


@dataclass(frozen=True, slots=True)
class FactVersion:
    """One version of an Approved Company Fact."""

    id: str
    fact_id: str
    version: int
    content: FactContent
    created_by: FactCreator
    created_at: datetime
    retracted_at: datetime | None


@dataclass(frozen=True, slots=True)
class FactHistory:
    """A fact's versions, newest first, and its current version (``None`` when retracted)."""

    fact_id: str
    current: FactVersion | None
    versions: tuple[FactVersion, ...]


def _version(stored: StoredFact) -> FactVersion:
    return FactVersion(
        id=stored.id,
        fact_id=stored.fact_id,
        version=stored.version,
        content=stored.content,
        created_by=stored.created_by,
        created_at=stored.created_at,
        retracted_at=stored.retracted_at,
    )


def _history(fact_id: str, rows: Iterable[StoredFact]) -> FactHistory:
    newest_first = tuple(
        sorted((_version(row) for row in rows), key=lambda found: found.version, reverse=True)
    )
    current = next((found for found in newest_first if found.retracted_at is None), None)
    return FactHistory(fact_id=fact_id, current=current, versions=newest_first)


def company_facts() -> tuple[FactHistory, ...]:
    """Every fact with its versions, by ``fact_id``."""
    rows = company_facts_repo.all_versions()  # by fact_id, then version
    return tuple(
        _history(fact_id, group) for fact_id, group in groupby(rows, key=lambda row: row.fact_id)
    )


def fact(fact_id: str) -> FactHistory | None:
    """The fact ``fact_id`` with its versions, or ``None`` when it has none."""
    rows = company_facts_repo.versions_of(fact_id)
    return _history(rows[0].fact_id, rows) if rows else None


def allowed_facts(segment_id: str, market_id: str) -> tuple[FactVersion, ...]:
    """The current version of every fact allowed for the Segment ``segment_id`` and the Market
    ``market_id``, by ``fact_id``."""
    found: list[FactVersion] = []
    for history in company_facts():
        current = history.current
        if (
            current is not None
            and segment_id in current.content.segments
            and market_id in current.content.markets
        ):
            found.append(current)
    return tuple(found)
