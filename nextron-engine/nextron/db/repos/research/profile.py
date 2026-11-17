"""The Lead's profile group (Story 2.6, AD-3): the only code that reads or writes the
``research``-owned columns of ``leads``, so far ``contact_methods``.

Every function runs explicit Core statements through the current unit of work's session; it
stores and returns what it is given and checks nothing (``nextron.app.research`` does).

- ``contact_methods(lead_id) -> str | None``: the Lead's stored value (the canonical JSON of
  ``nextron.domain.widgets.ContactMethods``), or ``None`` (none yet, or no such Lead).
- ``set_contact_methods(lead_id, value)``: stores it.
"""

from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.leads import LeadRecord
from nextron.db.uow import current


def contact_methods(lead_id: str) -> str | None:
    """The Lead's ``contact_methods`` value, or ``None``."""
    statement = select(LeadRecord.contact_methods).where(LeadRecord.id == lead_id)
    return current().session.execute(statement).scalar_one_or_none()


def set_contact_methods(lead_id: str, value: str) -> None:
    """Store ``value`` as the Lead's ``contact_methods``."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(contact_methods=value)
    )
