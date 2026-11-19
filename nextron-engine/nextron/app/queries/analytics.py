"""Read API of the ``analytics`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.analytics`` only. All read through the
current unit of work, read-only ones included; nothing here writes.

- ``lead_events(lead_ids, extra)`` (Story 9.8): every ``events`` row about one of the Leads
  ``lead_ids``, about any row linked to them by foreign keys (found generically from the
  models' metadata, ``nextron.db.repos.analytics.lineage``), or about a row of ``extra``
  (``{entity: ids}``, for rows no foreign key links), as ``StoredEvent`` rows, newest first (by
  ``at``, then ``id``, descending). An id that isn't a ULID is left out; no id reads nothing.
"""

from collections.abc import Iterable, Mapping

from nextron.db.repos.analytics import lineage
from nextron.db.repos.analytics.lineage import StoredEvent
from nextron.domain.ids import is_ulid

__all__ = ["StoredEvent", "lead_events"]


def lead_events(
    lead_ids: Iterable[str], extra: Mapping[str, Iterable[str]]
) -> tuple[StoredEvent, ...]:
    """The ``events`` rows of the Leads, their linked rows and ``extra``, newest first."""
    ids = {lead_id for lead_id in lead_ids if is_ulid(lead_id)}
    wanted = {
        entity: {row_id for row_id in row_ids if is_ulid(row_id)}
        for entity, row_ids in extra.items()
    }
    return lineage.lead_events(ids, wanted)
