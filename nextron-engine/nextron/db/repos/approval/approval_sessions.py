"""The ``approval_sessions`` repository (AD-3, AD-4 analytics; Story 5.4): the only code that
writes approval sessions.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.approval`` applies the
rules (the migration's partial unique index allows one open session).

- ``StoredSession(id, started_at, ended_at, decisions, active_ms)``;
- ``open_session()``: the session with ``ended_at`` NULL, or ``None``;
- ``latest_ended_at()``: the newest ``ended_at`` of a closed session, or ``None``;
- ``insert(started_at) -> id``: an open session with no decisions;
- ``close(session_id, ended_at) -> bool``: closes it if still open; whether a row changed;
- ``count_decision(session_id, review_ms)``: ``decisions`` + 1 and ``active_ms`` +
  ``review_ms``.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.approval import ApprovalSessionRecord
from nextron.db.models.base import new_id
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredSession:
    """One Approval Queue session."""

    id: str
    started_at: datetime
    ended_at: datetime | None
    decisions: int
    active_ms: int


def open_session() -> StoredSession | None:
    """The open session, or ``None``."""
    row = current().session.execute(
        select(
            ApprovalSessionRecord.id,
            ApprovalSessionRecord.started_at,
            ApprovalSessionRecord.ended_at,
            ApprovalSessionRecord.decisions,
            ApprovalSessionRecord.active_ms,
        ).where(ApprovalSessionRecord.ended_at.is_(None))
    ).first()
    if row is None:
        return None
    return StoredSession(str(row[0]), row[1], row[2], int(row[3]), int(row[4]))


def latest_ended_at() -> datetime | None:
    """The newest ``ended_at`` of a closed session, or ``None``."""
    found: datetime | None = current().session.execute(
        select(func.max(ApprovalSessionRecord.ended_at))
    ).scalar_one()
    return found


def insert(started_at: datetime) -> str:
    """Open a session at ``started_at`` with no decisions; its id."""
    session_id = new_id()
    current().session.execute(
        insert_rows(ApprovalSessionRecord).values(
            id=session_id, started_at=started_at, ended_at=None, decisions=0, active_ms=0
        )
    )
    return session_id


def close(session_id: str, ended_at: datetime) -> bool:
    """Close the session if it is open; whether a row changed."""
    result = current().session.execute(
        update_rows(ApprovalSessionRecord)
        .where(ApprovalSessionRecord.id == session_id, ApprovalSessionRecord.ended_at.is_(None))
        .values(ended_at=ended_at)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def count_decision(session_id: str, review_ms: int) -> None:
    """Add one decision and ``review_ms`` of active time to the session."""
    current().session.execute(
        update_rows(ApprovalSessionRecord)
        .where(ApprovalSessionRecord.id == session_id)
        .values(
            decisions=ApprovalSessionRecord.decisions + 1,
            active_ms=ApprovalSessionRecord.active_ms + review_ms,
        )
    )
