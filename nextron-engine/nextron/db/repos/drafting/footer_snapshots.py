"""The ``footer_snapshots`` table's repository (FR-23, AD-3, Story 4.11): the only code that
reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A snapshot is one distinct footer text of one Market, keyed by its
``nextron.domain.render.footer_sha256``.

- ``ensure(market_id, text, at) -> id``: the id of the row with this ``market_id`` and
  ``footer_sha256(text)``, else a new row created at ``at``; identical content reuses its row.
  A ``text`` that isn't a ``str`` or is blank, or a ``market_id`` that isn't a ULID, raises
  ``ValueError`` (the message never shows the text).
- ``by_id(snapshot_id)``: the row as a ``StoredFooterSnapshot``, or ``None`` for an id that
  isn't a ULID or has no row.

There is no update or delete: a snapshot never changes once written (Story 4.12's revisions
point at it).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.drafting import FooterSnapshotRecord
from nextron.db.uow import current
from nextron.domain.ids import is_ulid
from nextron.domain.render import footer_sha256


@dataclass(frozen=True, slots=True)
class StoredFooterSnapshot:
    """One ``footer_snapshots`` row."""

    id: str
    market_id: str
    sha256: str
    text: str
    created_at: datetime


def ensure(market_id: str, text: str, at: datetime) -> str:
    """The id of the snapshot of ``text`` for the Market ``market_id``, inserted at ``at`` when
    there is none yet."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a footer snapshot needs a footer text that isn't blank")
    if not is_ulid(market_id):
        raise ValueError("a footer snapshot needs a Market id that is a ULID")
    digest = footer_sha256(text)
    session = current().session
    found = session.execute(
        select(FooterSnapshotRecord.id).where(
            FooterSnapshotRecord.market_id == market_id,
            FooterSnapshotRecord.sha256 == digest,
        )
    ).scalar_one_or_none()
    if found is not None:
        return str(found)
    snapshot_id = new_id()
    session.execute(
        insert_rows(FooterSnapshotRecord).values(
            id=snapshot_id, market_id=market_id, sha256=digest, text=text, created_at=at
        )
    )
    return snapshot_id


def by_id(snapshot_id: str) -> StoredFooterSnapshot | None:
    """The snapshot ``snapshot_id``, or ``None`` when it isn't a ULID or has no row."""
    if not is_ulid(snapshot_id):
        return None
    row = (
        current()
        .session.execute(
            select(
                FooterSnapshotRecord.id,
                FooterSnapshotRecord.market_id,
                FooterSnapshotRecord.sha256,
                FooterSnapshotRecord.text,
                FooterSnapshotRecord.created_at,
            ).where(FooterSnapshotRecord.id == snapshot_id)
        )
        .one_or_none()
    )
    if row is None:
        return None
    return StoredFooterSnapshot(
        id=row.id,
        market_id=row.market_id,
        sha256=row.sha256,
        text=row.text,
        created_at=row.created_at,
    )
