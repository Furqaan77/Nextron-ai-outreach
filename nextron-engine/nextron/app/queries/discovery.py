"""Read API of the ``discovery`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.discovery`` only. All read through the
current unit of work, read-only ones included.

- ``lead(lead_id)``: the Lead's identity group as a ``StoredLead`` (a merged Lead included:
  its ``merged_into`` names the survivor), or ``None`` when no Lead has that id (a text that
  isn't a ULID included).
"""

from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery.leads import StoredLead
from nextron.domain.ids import is_ulid

__all__ = ["StoredLead", "lead"]


def lead(lead_id: str) -> StoredLead | None:
    """The Lead ``lead_id``, or ``None``."""
    if not is_ulid(lead_id):
        return None
    return leads_repo.get(lead_id)
