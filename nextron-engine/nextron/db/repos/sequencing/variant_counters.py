"""The ``variant_counters`` repository (AD-3, AD-21): the only code that writes a Campaign's
round-robin Variant counter.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``). It checks nothing; ``nextron.app.sequencing`` does.

- ``last_variant_id(campaign_id)``: the last Variant assigned in the Campaign, or ``None``
  before its first Enrollment;
- ``record(campaign_id, variant_id, at)``: the Campaign's counter row inserted, or updated in
  place (one row per Campaign: ``uq_variant_counters_campaign_id``).
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as upsert_rows

from nextron.db.models.base import new_id
from nextron.db.models.sequencing import VariantCounterRecord
from nextron.db.uow import current


def last_variant_id(campaign_id: str) -> str | None:
    """The last Variant assigned in the Campaign ``campaign_id``, or ``None``."""
    statement = select(VariantCounterRecord.last_variant_id).where(
        VariantCounterRecord.campaign_id == campaign_id
    )
    return current().session.execute(statement).scalar_one_or_none()


def record(campaign_id: str, variant_id: str, at: datetime) -> None:
    """Record ``variant_id`` as the Campaign's last assigned Variant."""
    statement = upsert_rows(VariantCounterRecord).values(
        id=new_id(), campaign_id=campaign_id, last_variant_id=variant_id, updated_at=at
    )
    current().session.execute(
        statement.on_conflict_do_update(
            index_elements=[VariantCounterRecord.campaign_id],
            set_={"last_variant_id": variant_id, "updated_at": at},
        )
    )
