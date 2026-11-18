"""Read API of the ``compliance`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.compliance`` only. All read through the
current unit of work, read-only ones included. Each entry is a ``ListedEntry`` (``id``,
``kind``, ``value_hash``, ``domain_plain``, ``status``, ``source``, ``created_at``): never its
``cause_ref``, ``voided_at`` or ``void_reason``.

- ``entry_list(*, kind=None, source=None, status=None, page=1)`` (Story 3.5): one page of the
  Suppression List as an ``EntryListPage``: at most ``ENTRIES_PER_PAGE`` (50) entries, newest
  first (by ``created_at``, then ``id``, descending), the ``total`` that match, the ``page``
  shown and the number of ``pages`` (``max(1, ceil(total / 50))``). ``kind`` keeps the entries
  of that ``KeyKind``, ``source`` of that ``SuppressionSource`` and ``status`` of that
  ``EntryStatus``; ``None`` is any. A ``page`` past the last shows the last. A ``page`` that
  isn't an ``int`` (a ``bool`` included), or a filter that isn't its enum, raises
  ``TypeError``; a ``page`` below 1 raises ``ValueError``.
- ``entries(ids)``: the entries of ``ids``, in the order given, each once; an id that isn't a
  ULID, or that no entry has, is left out.
- ``active_entry(kind, value_hash)``: the ``active`` entry of the key, or ``None``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil
from typing import Final

from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.repos.compliance.suppression_entries import ListedEntry
from nextron.domain.contactability import KeyKind
from nextron.domain.ids import is_ulid
from nextron.domain.suppression_entry import EntryStatus, SuppressionSource

__all__ = [
    "ENTRIES_PER_PAGE",
    "EntryListPage",
    "ListedEntry",
    "active_entry",
    "entries",
    "entry_list",
]

ENTRIES_PER_PAGE: Final = 50


@dataclass(frozen=True, slots=True)
class EntryListPage:
    """One page of the Suppression List: its entries, how many match, the page shown (from 1)
    and the number of pages (at least 1)."""

    entries: tuple[ListedEntry, ...]
    total: int
    page: int
    pages: int


def entry_list(
    *,
    kind: KeyKind | None = None,
    source: SuppressionSource | None = None,
    status: EntryStatus | None = None,
    page: int = 1,
) -> EntryListPage:
    """One page of the entries that match the filters, newest first."""
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError(f"page must be an int, not {type(page).__name__}")
    if page < 1:
        raise ValueError("page must be at least 1")
    for name, value, enum in (
        ("kind", kind, KeyKind),
        ("source", source, SuppressionSource),
        ("status", status, EntryStatus),
    ):
        if value is not None and not isinstance(value, enum):
            raise TypeError(f"{name} must be a {enum.__name__}, not {type(value).__name__}")
    stored_kind = None if kind is None else str(kind)
    stored_source = None if source is None else str(source)
    stored_status = None if status is None else str(status)
    total = entries_repo.count_listed(kind=stored_kind, source=stored_source, status=stored_status)
    pages = max(1, ceil(total / ENTRIES_PER_PAGE))
    shown = min(page, pages)
    rows = entries_repo.listed(
        kind=stored_kind,
        source=stored_source,
        status=stored_status,
        limit=ENTRIES_PER_PAGE,
        offset=(shown - 1) * ENTRIES_PER_PAGE,
    )
    return EntryListPage(entries=rows, total=total, page=shown, pages=pages)


def entries(ids: Iterable[str]) -> tuple[ListedEntry, ...]:
    """The entries of ``ids``, in the order given, each once."""
    wanted = list(dict.fromkeys(entry_id for entry_id in ids if is_ulid(entry_id)))
    found = {entry.id: entry for entry in entries_repo.by_ids(wanted)}
    return tuple(found[entry_id] for entry_id in wanted if entry_id in found)


def active_entry(kind: KeyKind | str, value_hash: str) -> ListedEntry | None:
    """The ``active`` entry of the key (``kind``, ``value_hash``), or ``None``."""
    stored = entries_repo.active_for(str(kind), value_hash)
    if stored is None:
        return None
    return ListedEntry(
        stored.id,
        stored.kind,
        stored.value_hash,
        stored.domain_plain,
        stored.status,
        stored.source,
        stored.created_at,
    )
