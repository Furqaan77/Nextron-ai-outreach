"""The ``leads`` profile group's repository (FR-11, FR-13, AD-3, AD-11; Stories 2.6, 2.9 and
2.10): the only code that reads or writes the ``research``-owned columns of ``leads``:
``hq_tz``, ``hq_evidence_id``, ``contact_methods`` and ``no_decision_maker``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores and returns what it is given and checks nothing;
``nextron.app.research`` does.

- ``StoredHeadquarters(hq_tz, hq_evidence_id)``: the headquarters columns of one row;
- ``headquarters(lead_id)``: the row's headquarters columns, or ``None`` for an unknown Lead;
- ``set_headquarters(lead_id, hq_tz, hq_evidence_id)``: sets both columns (Story 2.9);
- ``contact_methods(lead_id) -> str | None``: the Lead's stored value (the canonical JSON of
  ``nextron.domain.widgets.ContactMethods``), or ``None`` (none yet, or no such Lead);
- ``set_contact_methods(lead_id, value)``: stores it (Story 2.6);
- ``no_decision_maker(lead_id)`` (Story 2.10): the row's ``no_decision_maker`` (``None`` until
  the Primary Contact's selection first runs, and for an unknown Lead);
  ``set_no_decision_maker(lead_id, value)``: sets it.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.leads import LeadRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredHeadquarters:
    """The Lead's headquarters zone and the Evidence it comes from (both ``None`` while
    unknown)."""

    hq_tz: str | None
    hq_evidence_id: str | None


def headquarters(lead_id: str) -> StoredHeadquarters | None:
    """The Lead's headquarters columns, or ``None`` when the Lead has no row."""
    statement = select(LeadRecord.hq_tz, LeadRecord.hq_evidence_id).where(
        LeadRecord.id == lead_id
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredHeadquarters(*row)


def set_headquarters(lead_id: str, hq_tz: str | None, hq_evidence_id: str | None) -> None:
    """Set the Lead's headquarters zone and its Evidence."""
    current().session.execute(
        update_rows(LeadRecord)
        .where(LeadRecord.id == lead_id)
        .values(hq_tz=hq_tz, hq_evidence_id=hq_evidence_id)
    )


def contact_methods(lead_id: str) -> str | None:
    """The Lead's ``contact_methods`` value, or ``None``."""
    statement = select(LeadRecord.contact_methods).where(LeadRecord.id == lead_id)
    return current().session.execute(statement).scalar_one_or_none()


def set_contact_methods(lead_id: str, value: str) -> None:
    """Store ``value`` as the Lead's ``contact_methods``."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(contact_methods=value)
    )


def no_decision_maker(lead_id: str) -> bool | None:
    """Whether the Lead's last selection found no decision-maker; ``None`` before the first
    selection and for an unknown Lead."""
    statement = select(LeadRecord.no_decision_maker).where(LeadRecord.id == lead_id)
    value = current().session.execute(statement).scalar_one_or_none()
    return None if value is None else bool(value)


def set_no_decision_maker(lead_id: str, value: bool) -> None:
    """Set the Lead's ``no_decision_maker``."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(no_decision_maker=value)
    )
