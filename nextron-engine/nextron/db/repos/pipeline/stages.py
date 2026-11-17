"""The ``leads`` pipeline group's repository (AD-3, AD-4): the only code that writes
``leads.pipeline_stage``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.pipeline`` applies the
Pipeline Stage machine.

- ``stage(lead_id)``: the stored stage text, or ``None`` when the Lead has no row;
- ``set_stage(lead_id, stage)``: sets it.
"""

from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.leads import LeadRecord
from nextron.db.uow import current


def stage(lead_id: str) -> str | None:
    """The stored Pipeline Stage of the Lead ``lead_id``, or ``None``."""
    statement = select(LeadRecord.pipeline_stage).where(LeadRecord.id == lead_id)
    return current().session.execute(statement).scalar_one_or_none()


def set_stage(lead_id: str, stage: str) -> None:
    """Set the Pipeline Stage of the Lead ``lead_id``."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(pipeline_stage=stage)
    )
