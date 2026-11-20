"""The ``approvals`` repository (AD-3, AD-6; Story 5.4): the only code that writes approvals.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, the insert only inside
the running use case's transaction. It checks nothing; ``nextron.app.approval`` applies the
rules. The table is append-only (its triggers refuse an update, a delete or a replacing insert),
so there is no update or delete here.

- ``new_approval_id()``: a fresh ULID;
- ``insert(approval_id, NewApproval)``: one row;
- ``StoredApproval(id, draft_id, rev, text_hash, approved_by, approved_at, review_ms,
  edit_ratio)``; ``latest_of(draft_id)``: the Draft's newest approval (by ``approved_at``,
  then ``id``), or ``None``; ``latest_at_since(at)``: the newest ``approved_at`` at or after
  ``at``, or ``None``.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy import insert as insert_rows

from nextron.db.models.approval import ApprovalRecord
from nextron.db.models.base import new_id
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class NewApproval:
    """What an approval row holds besides its id."""

    draft_id: str
    rev: int
    text_hash: str
    approved_by: str
    approved_at: datetime
    review_ms: int | None
    edit_ratio: float


@dataclass(frozen=True, slots=True)
class StoredApproval:
    """One stored approval."""

    id: str
    draft_id: str
    rev: int
    text_hash: str
    approved_by: str
    approved_at: datetime
    review_ms: int | None
    edit_ratio: float


def new_approval_id() -> str:
    """A fresh ULID for an approval."""
    return new_id()


def insert(approval_id: str, new: NewApproval) -> None:
    """Insert one approval row."""
    current().session.execute(
        insert_rows(ApprovalRecord).values(
            id=approval_id,
            draft_id=new.draft_id,
            rev=new.rev,
            text_hash=new.text_hash,
            approved_by=new.approved_by,
            approved_at=new.approved_at,
            review_ms=new.review_ms,
            edit_ratio=new.edit_ratio,
        )
    )


def latest_of(draft_id: str) -> StoredApproval | None:
    """The Draft's newest approval, or ``None``."""
    row = current().session.execute(
        select(
            ApprovalRecord.id,
            ApprovalRecord.draft_id,
            ApprovalRecord.rev,
            ApprovalRecord.text_hash,
            ApprovalRecord.approved_by,
            ApprovalRecord.approved_at,
            ApprovalRecord.review_ms,
            ApprovalRecord.edit_ratio,
        )
        .where(ApprovalRecord.draft_id == draft_id)
        .order_by(ApprovalRecord.approved_at.desc(), ApprovalRecord.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return StoredApproval(
        id=str(row[0]),
        draft_id=str(row[1]),
        rev=int(row[2]),
        text_hash=str(row[3]),
        approved_by=str(row[4]),
        approved_at=row[5],
        review_ms=None if row[6] is None else int(row[6]),
        edit_ratio=float(row[7]),
    )


def latest_at_since(at: datetime) -> datetime | None:
    """The newest ``approved_at`` at or after ``at``, or ``None``."""
    found: datetime | None = current().session.execute(
        select(func.max(ApprovalRecord.approved_at)).where(ApprovalRecord.approved_at >= at)
    ).scalar_one()
    return found
