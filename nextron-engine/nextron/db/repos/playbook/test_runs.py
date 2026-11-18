"""The ``ai_test_runs`` table's repository (FR-73, AD-3, AD-12, Story 4.5): the only code that
reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks and
foreign key refuse a bad row (``IntegrityError``). A row is never updated or deleted.

- ``insert(...) -> id``: a new run;
- ``newest(tuple_id, test_set)``: the newest run of that tuple on that set (``finished_at``,
  then ``id``) as a ``StoredRun``, or ``None``;
- ``newest_passed(tuple_id, test_set)``: the newest ``passed`` one, or ``None``;
- ``every()``: every run, by ``tuple_id`` and ``test_set``, then newest first (the AI content
  view and the backfill read it).
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select
from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.playbook import AiTestRunRecord
from nextron.db.uow import current
from nextron.domain.ai_test_gate import RunOutcome


@dataclass(frozen=True, slots=True)
class StoredRun:
    """One ``ai_test_runs`` row; ``metrics`` and ``results`` are the stored JSON text."""

    id: str
    tuple_id: str
    test_set: str
    outcome: str
    error: str | None
    case_count: int
    passed_count: int
    metrics: str = field(repr=False)
    results: str = field(repr=False)
    cost_micros: int
    started_at: datetime
    finished_at: datetime


_COLUMNS = (
    AiTestRunRecord.id,
    AiTestRunRecord.tuple_id,
    AiTestRunRecord.test_set,
    AiTestRunRecord.outcome,
    AiTestRunRecord.error,
    AiTestRunRecord.case_count,
    AiTestRunRecord.passed_count,
    AiTestRunRecord.metrics,
    AiTestRunRecord.results,
    AiTestRunRecord.cost_micros,
    AiTestRunRecord.started_at,
    AiTestRunRecord.finished_at,
)


def insert(
    *,
    tuple_id: str,
    test_set: str,
    outcome: str,
    error: str | None,
    case_count: int,
    passed_count: int,
    metrics: str,
    results: str,
    cost_micros: int,
    started_at: datetime,
    finished_at: datetime,
) -> str:
    """Store a new run; return its ULID."""
    run_id = new_id()
    current().session.execute(
        insert_rows(AiTestRunRecord).values(
            id=run_id,
            tuple_id=tuple_id,
            test_set=test_set,
            outcome=outcome,
            error=error,
            case_count=case_count,
            passed_count=passed_count,
            metrics=metrics,
            results=results,
            cost_micros=cost_micros,
            started_at=started_at,
            finished_at=finished_at,
        )
    )
    return run_id


def _newest(statement: Select[tuple[object, ...]]) -> StoredRun | None:
    ordered = statement.order_by(
        AiTestRunRecord.finished_at.desc(), AiTestRunRecord.id.desc()
    ).limit(1)
    row = current().session.execute(ordered).one_or_none()
    return None if row is None else StoredRun(*row)


def newest(tuple_id: str, test_set: str) -> StoredRun | None:
    """The newest run of ``tuple_id`` on ``test_set``, or ``None``."""
    return _newest(
        select(*_COLUMNS).where(
            AiTestRunRecord.tuple_id == str(tuple_id), AiTestRunRecord.test_set == str(test_set)
        )
    )


def newest_passed(tuple_id: str, test_set: str) -> StoredRun | None:
    """The newest ``passed`` run of ``tuple_id`` on ``test_set``, or ``None``."""
    return _newest(
        select(*_COLUMNS).where(
            AiTestRunRecord.tuple_id == str(tuple_id),
            AiTestRunRecord.test_set == str(test_set),
            AiTestRunRecord.outcome == RunOutcome.PASSED.value,
        )
    )


def every() -> tuple[StoredRun, ...]:
    """Every run, by ``tuple_id`` and ``test_set``, then newest first."""
    statement = select(*_COLUMNS).order_by(
        AiTestRunRecord.tuple_id,
        AiTestRunRecord.test_set,
        AiTestRunRecord.finished_at.desc(),
        AiTestRunRecord.id.desc(),
    )
    return tuple(StoredRun(*row) for row in current().session.execute(statement))
