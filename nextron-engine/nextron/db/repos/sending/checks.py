"""The ``domain_checks`` repository (FR-64, AD-3): the only code that writes domain checks.

Append-only: it only inserts and reads. Every function runs one explicit Core statement through
the current unit of work's session (``nextron.db.uow.current()``): reads work in a read-only
unit of work too, writes only inside the running use case's transaction. It checks nothing;
``nextron.app.sending`` does.

- ``StoredCheck``: one row;
- ``insert(...) -> id``: a new row;
- ``newest(sending_domain_id)``: the domain's newest check, or ``None``;
- ``newest_times(sending_domain_ids)``: each domain's newest check's ``checked_at``, ``None`` for
  a domain never checked, keyed by id in the order given.

*Newest* means the last inserted (the highest SQLite ``rowid``), never the latest ``checked_at``
or ULID: both come from the wall clock, so a check stamped in the future (the clock was set back)
would otherwise hide every check recorded after it. The table is append-only and the engine
never runs ``VACUUM``, so ``rowid`` order is insertion order.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, literal_column, select
from sqlalchemy import insert as insert_rows

from nextron.db.models.base import new_id
from nextron.db.models.sending import DomainCheckRecord
from nextron.db.uow import current

ROWID = literal_column("rowid")  # insertion order in this append-only table


@dataclass(frozen=True, slots=True)
class StoredCheck:
    """One ``domain_checks`` row."""

    id: str
    sending_domain_id: str
    checked_at: datetime
    spf_ok: bool
    dkim_ok: bool
    dmarc_ok: bool
    aligned_ok: bool
    passed: bool
    details: str


def insert(
    *,
    sending_domain_id: str,
    checked_at: datetime,
    spf_ok: bool,
    dkim_ok: bool,
    dmarc_ok: bool,
    aligned_ok: bool,
    passed: bool,
    details: str,
) -> str:
    """Store one check; return its new ULID."""
    check_id = new_id()
    current().session.execute(
        insert_rows(DomainCheckRecord).values(
            id=check_id,
            sending_domain_id=sending_domain_id,
            checked_at=checked_at,
            spf_ok=spf_ok,
            dkim_ok=dkim_ok,
            dmarc_ok=dmarc_ok,
            aligned_ok=aligned_ok,
            passed=passed,
            details=details,
        )
    )
    return check_id


def newest(sending_domain_id: str) -> StoredCheck | None:
    """The newest check of ``sending_domain_id``, or ``None``."""
    row = (
        current()
        .session.execute(
            select(
                DomainCheckRecord.id,
                DomainCheckRecord.sending_domain_id,
                DomainCheckRecord.checked_at,
                DomainCheckRecord.spf_ok,
                DomainCheckRecord.dkim_ok,
                DomainCheckRecord.dmarc_ok,
                DomainCheckRecord.aligned_ok,
                DomainCheckRecord.passed,
                DomainCheckRecord.details,
            )
            .where(DomainCheckRecord.sending_domain_id == sending_domain_id)
            .order_by(ROWID.desc())
            .limit(1)
        )
        .one_or_none()
    )
    return None if row is None else StoredCheck(*row)


def newest_times(sending_domain_ids: Iterable[str]) -> dict[str, datetime | None]:
    """Each domain's newest (last inserted) check's ``checked_at``, ``None`` when never
    checked, in the order given."""
    wanted = list(dict.fromkeys(sending_domain_ids))
    if not wanted:
        return {}
    last = (
        select(func.max(ROWID))
        .where(DomainCheckRecord.sending_domain_id.in_(wanted))
        .group_by(DomainCheckRecord.sending_domain_id)
    )
    rows = current().session.execute(
        select(DomainCheckRecord.sending_domain_id, DomainCheckRecord.checked_at).where(
            ROWID.in_(last)
        )
    )
    found: dict[str, datetime | None] = {domain_id: newest for domain_id, newest in rows}
    return {domain_id: found.get(domain_id) for domain_id in wanted}
