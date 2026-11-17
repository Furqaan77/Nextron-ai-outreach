"""Read API of the ``compliance`` service (AD-3, AD-16, Story 3.4).

Import rule: ``nextron.domain`` and ``nextron.db.repos.compliance`` only. All read through the
current unit of work, read-only ones included; nothing here writes, caches or logs.

- ``active_entry(kind, value_hash)``: the ``active`` Suppression List entry of that key (a
  ``StoredSuppressionEntry``), or ``None``.
- ``active_keys(keys)``: the (``kind``, ``value_hash``) pairs of the ``SuppressionKey`` items in
  ``keys`` that have an ``active`` entry, as a ``frozenset``; a voided entry isn't active. An
  item that isn't a ``SuppressionKey`` raises ``TypeError`` before anything is read.
  ``compliance.assert_contactable`` passes it ``nextron.domain.contactability.lookup_keys``.
"""

from collections.abc import Iterable

from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.repos.compliance.suppression_entries import StoredSuppressionEntry
from nextron.domain.contactability import SuppressionKey

__all__ = ["StoredSuppressionEntry", "active_entry", "active_keys"]


def active_entry(kind: str, value_hash: str) -> StoredSuppressionEntry | None:
    """The ``active`` entry of the key (``kind``, ``value_hash``), or ``None``."""
    return entries_repo.active_for(kind, value_hash)


def active_keys(keys: Iterable[SuppressionKey]) -> frozenset[tuple[str, str]]:
    """The (``kind``, ``value_hash``) pairs among ``keys`` that have an ``active`` entry."""
    pairs: list[tuple[str, str]] = []
    for key in keys:
        if not isinstance(key, SuppressionKey):
            raise TypeError(f"active_keys takes SuppressionKey items, not {type(key).__name__}")
        pair = (str(key.kind), key.value_hash)
        if pair not in pairs:
            pairs.append(pair)
    return frozenset(pair for pair in pairs if entries_repo.active_for(*pair) is not None)
