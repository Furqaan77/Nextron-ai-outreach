"""The ``leads`` profile group's repository (FR-11, AD-3, AD-11; Story 2.9): the only code that
writes ``hq_tz`` and ``hq_evidence_id``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.research`` does.

- ``StoredHeadquarters(hq_tz, hq_evidence_id)``: the profile group of one row;
- ``headquarters(lead_id)``: the row's profile group, or ``None`` for an unknown Lead;
- ``set_headquarters(lead_id, hq_tz, hq_evidence_id)``: sets both columns.
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
