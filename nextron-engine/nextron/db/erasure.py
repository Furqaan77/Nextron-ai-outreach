"""Tombstones by registry class (AD-17, FR-52, Story 3.13): what an erasure writes into a row.

The repositories that tombstone a Contact's rows (``nextron.db.repos.research`` and
``nextron.db.repos.compliance``) build their ``UPDATE`` values here, so the class each column
gets is the one ``nextron.db.pii_registry`` gives it and nothing else decides it; the app never
reads the registry (import contract c1).

``tombstoned(table, row) -> dict``: for each column of ``PII[table]`` whose class isn't
``none``, in the registry's order, ``nextron.domain.erasure.tombstone(class, row[column],
hash_kind=...)``, the hash kind of a ``keep_hash`` column being ``HASH_KINDS[(table, column)]``.
``row`` maps column names to the stored values. A table the registry doesn't list, a row
lacking one of those columns, and a ``keep_hash`` column missing from ``HASH_KINDS`` raise
``KeyError``: an erasure fails closed rather than keep a value it can't place.
"""

from collections.abc import Mapping

from nextron.db.pii_registry import HASH_KINDS, PII, PiiClass
from nextron.domain.erasure import tombstone


def tombstoned(table: str, row: Mapping[str, object]) -> dict[str, object]:
    """The tombstone of every personal-data column of ``row``; see the module docstring."""
    values: dict[str, object] = {}
    for column, pii_class in PII[table].items():
        if pii_class is PiiClass.NONE:
            continue
        hash_kind = HASH_KINDS[(table, column)] if pii_class is PiiClass.KEEP_HASH else None
        values[column] = tombstone(str(pii_class), row[column], hash_kind=hash_kind)
    return values
