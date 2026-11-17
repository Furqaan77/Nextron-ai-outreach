"""The ``unsub_tokens`` repository (FR-70, AD-3, AD-15, AD-17, Story 3.8): the record of every
issued unsubscribe token.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, the write only inside
the running transaction. It checks nothing; ``nextron.app.compliance`` does.

- ``StoredUnsubToken``: one row;
- readers: ``for_om(om_id)``, the row of that message or ``None``; ``newest_issued_at(kid)``, the
  newest ``issued_at`` under ``kid`` or ``None``; ``newest_by_kid()``, ``{kid: newest
  issued_at}`` for every kid with a token;
- the one writer: ``insert(...) -> id``, a new row.

The table holds only the token's hash. Nothing here changes a row or takes one away: the
records are permanent, and the table's triggers refuse it too.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import func, select
from sqlalchemy import insert as insert_rows

from nextron.db.models.base import new_id
from nextron.db.models.compliance import UnsubTokenRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredUnsubToken:
    """One ``unsub_tokens`` row."""

    id: str
    token_hash: str
    om_id: str
    kid: int
    issued_at: datetime


_COLUMNS = (
    UnsubTokenRecord.id,
    UnsubTokenRecord.token_hash,
    UnsubTokenRecord.om_id,
    UnsubTokenRecord.kid,
    UnsubTokenRecord.issued_at,
)


def for_om(om_id: str) -> StoredUnsubToken | None:
    """The token record of the message ``om_id``, or ``None``."""
    statement = select(*_COLUMNS).where(UnsubTokenRecord.om_id == om_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredUnsubToken(*row)


def newest_issued_at(kid: int) -> datetime | None:
    """When the newest token under ``kid`` was issued, or ``None`` when it has none."""
    statement = select(func.max(UnsubTokenRecord.issued_at)).where(UnsubTokenRecord.kid == kid)
    found: datetime | None = current().session.execute(statement).scalar_one()
    return found


def newest_by_kid() -> Mapping[int, datetime]:
    """``{kid: when its newest token was issued}`` for every kid with a token, by kid."""
    statement = (
        select(UnsubTokenRecord.kid, func.max(UnsubTokenRecord.issued_at))
        .group_by(UnsubTokenRecord.kid)
        .order_by(UnsubTokenRecord.kid)
    )
    rows = current().session.execute(statement).all()
    return MappingProxyType({int(kid): newest for kid, newest in rows})


def insert(*, token_hash: str, om_id: str, kid: int, issued_at: datetime) -> str:
    """Store a new token record; return its new ULID."""
    record_id = new_id()
    current().session.execute(
        insert_rows(UnsubTokenRecord).values(
            id=record_id,
            token_hash=token_hash,
            om_id=om_id,
            kid=kid,
            issued_at=issued_at,
        )
    )
    return record_id
