"""The ``restore_tests`` table's repository (AD-3, AD-20, Story 1.18): the only SQL on it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores what the ``ops`` service gives it and checks nothing (the table's checks
do). Rows come back as ``RestoreTestRow``, a frozen copy of every column.

- ``insert(...)``: a new row, written once; returns its new ULID;
- ``newest(*, kind=None, result=None)``: the newest row (``started_at``, then id, descending),
  of ``kind`` and ``result`` when given, or ``None``;
- ``failed_after(since)``: every ``fail`` row that started after ``since`` (every one when
  ``since`` is ``None``), oldest first.
"""

from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import Executable, select
from sqlalchemy import insert as sql_insert

from nextron.db.models.base import new_id
from nextron.db.models.ops import RestoreTestRecord
from nextron.db.uow import current

FAIL = "fail"  # nextron.domain.restore_test.RestoreResult.FAIL


@dataclass(frozen=True, slots=True)
class RestoreTestRow:
    """Every column of one ``restore_tests`` row."""

    id: str
    kind: str
    result: str
    started_at: datetime
    finished_at: datetime
    backup_id: str | None
    check_fetch: str
    check_integrity: str
    check_revision: str
    check_suppression: str
    check_provenance: str
    reason: str | None
    copy_revision: str | None
    suppression_count: int | None
    provenance_count: int | None
    detail: str | None

    @property
    def checks(self) -> tuple[str, str, str, str, str]:
        """The five check outcomes, in the order the checks run."""
        return (
            self.check_fetch,
            self.check_integrity,
            self.check_revision,
            self.check_suppression,
            self.check_provenance,
        )


_COLUMNS = tuple(getattr(RestoreTestRecord, field.name) for field in fields(RestoreTestRow))


def _rows(statement: Executable) -> list[RestoreTestRow]:
    return [RestoreTestRow(*row) for row in current().session.execute(statement)]


def insert(
    *,
    kind: str,
    result: str,
    started_at: datetime,
    finished_at: datetime,
    backup_id: str | None,
    check_fetch: str,
    check_integrity: str,
    check_revision: str,
    check_suppression: str,
    check_provenance: str,
    reason: str | None,
    copy_revision: str | None,
    suppression_count: int | None,
    provenance_count: int | None,
    detail: str | None,
) -> str:
    """Insert one restore test row; return its new id."""
    test_id = new_id()
    current().session.execute(
        sql_insert(RestoreTestRecord).values(
            id=test_id,
            kind=kind,
            result=result,
            started_at=started_at,
            finished_at=finished_at,
            backup_id=backup_id,
            check_fetch=check_fetch,
            check_integrity=check_integrity,
            check_revision=check_revision,
            check_suppression=check_suppression,
            check_provenance=check_provenance,
            reason=reason,
            copy_revision=copy_revision,
            suppression_count=suppression_count,
            provenance_count=provenance_count,
            detail=detail,
        )
    )
    return test_id


def newest(*, kind: str | None = None, result: str | None = None) -> RestoreTestRow | None:
    """The newest row, of ``kind`` and ``result`` when given; ``None`` when there is none."""
    statement = select(*_COLUMNS)
    if kind is not None:
        statement = statement.where(RestoreTestRecord.kind == kind)
    if result is not None:
        statement = statement.where(RestoreTestRecord.result == result)
    rows = _rows(
        statement.order_by(
            RestoreTestRecord.started_at.desc(), RestoreTestRecord.id.desc()
        ).limit(1)
    )
    return rows[0] if rows else None


def failed_after(since: datetime | None) -> tuple[RestoreTestRow, ...]:
    """Every ``fail`` row that started after ``since`` (all when ``None``), oldest first."""
    statement = select(*_COLUMNS).where(RestoreTestRecord.result == FAIL)
    if since is not None:
        statement = statement.where(RestoreTestRecord.started_at > since)
    return tuple(
        _rows(statement.order_by(RestoreTestRecord.started_at, RestoreTestRecord.id))
    )
