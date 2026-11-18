"""The ``leads`` profile group's repository (FR-11, FR-13, AD-3, AD-11; Stories 2.9 and 2.10):
the only code that writes ``hq_tz``, ``hq_evidence_id`` and ``no_decision_maker``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.research`` does.

- ``StoredHeadquarters(hq_tz, hq_evidence_id)``: the profile group of one row;
- ``headquarters(lead_id)``: the row's profile group, or ``None`` for an unknown Lead;
- ``set_headquarters(lead_id, hq_tz, hq_evidence_id)``: sets both columns;
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
    """The Lead's profile group, or ``None`` when the Lead has no row."""
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
