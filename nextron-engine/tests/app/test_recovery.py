"""Recovery mode's rows (the ``ops`` service's ``Recovery``, Story 1.19) on real units of work
over a temporary database at head: ``begin`` (all four steps pending, ``backup_at`` clamped to
the restore, a superseded ``on`` row lowering it), ``pending`` (unregistered steps become
``not_applicable``, the registered ones come back in order), ``mark_done``, ``finish``, the
``recovery_mode`` gate and ``recovery_state``, the health report's ``recovery``, the log
records, and raw-SQL rows that break ``ck_recovery_state_outcome``, a step's or the mode's
check, or the one-``on`` index, each refused by name."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.ops.api import EngineHealth, Recovery
from nextron.app.queries import ops as ops_queries
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.readiness import CheckResult, Status
from nextron.domain.recovery import RecoveryContext, RecoveryMode, RecoveryStep, StepStatus
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
T0_TEXT = "2026-11-17T09:00:00.000000Z"
OPS_LOGGER = "nextron.app.ops.api"
STEPS = tuple(RecoveryStep)
PENDING = tuple((step, StepStatus.PENDING) for step in STEPS)
NAME = "nextron-test-20261116T100000.000Z.db"


def db_file(engine: Engine) -> Path:
    assert engine.url.database
    return Path(engine.url.database)


def query(engine: Engine, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
    connection = sqlite3.connect(db_file(engine), isolation_level=None)
    try:
        return connection.execute(statement, params).fetchall()
    finally:
        connection.close()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def recovery(clock: FrozenClock) -> Recovery:
    return Recovery(clock)


def begin(
    units: UnitOfWorkFactory,
    recovery: Recovery,
    backup_at: datetime = T0 - timedelta(days=1),
    source_name: str = NAME,
    pre_restore_name: str | None = "nextron-test-20261117T090000.000Z.db",
) -> Any:
    with units.open():
        return recovery.begin(
            backup_at=backup_at, source_name=source_name, pre_restore_name=pre_restore_name
        )


def read(units: UnitOfWorkFactory, call: Any) -> Any:
    with units.open(read_only=True):
        return call()


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == OPS_LOGGER]


# begin ------------------------------------------------------------------------------------------


def test_begin_turns_recovery_mode_on_with_four_pending_steps(
    units: UnitOfWorkFactory, recovery: Recovery, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    assert read(units, ops_queries.recovery_mode) is False
    assert read(units, ops_queries.recovery_state) is None

    row = begin(units, recovery)

    assert (row.mode, row.restored_at, row.backup_at, row.ended_at) == (
        "on",
        T0,
        T0 - timedelta(days=1),
        None,
    )
    assert (row.source_name, row.pre_restore_name) == (
        NAME,
        "nextron-test-20261117T090000.000Z.db",
    )
    assert row.steps == PENDING
    assert read(units, ops_queries.recovery_mode) is True
    assert read(units, ops_queries.recovery_state) == row
    [record] = [r for r in caplog.records if r.getMessage() == "recovery_mode_started"]
    assert (record.levelno, record.recovery_id, record.superseded) == (  # type: ignore[attr-defined]
        logging.INFO,
        row.id,
        None,
    )


def test_begin_clamps_backup_at_to_the_restore(
    units: UnitOfWorkFactory, recovery: Recovery
) -> None:
    row = begin(units, recovery, backup_at=T0 + timedelta(hours=3), pre_restore_name=None)

    assert (row.backup_at, row.pre_restore_name) == (T0, None)


def test_a_second_begin_supersedes_the_on_row_and_keeps_the_earlier_backup_time(
    units: UnitOfWorkFactory,
    recovery: Recovery,
    clock: FrozenClock,
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    first = begin(units, recovery, backup_at=T0 - timedelta(days=5))
    clock.advance(timedelta(hours=1))

    second = begin(units, recovery, backup_at=T0 - timedelta(days=1))

    assert second.backup_at == T0 - timedelta(days=5)  # the superseded row's, the earlier
    assert second.restored_at == T0 + timedelta(hours=1)
    rows = query(engine, "SELECT id, mode, ended_at FROM recovery_state ORDER BY restored_at")
    assert rows == [
        (first.id, "superseded", "2026-11-17T10:00:00.000000Z"),
        (second.id, "on", None),
    ]
    [record] = [
        r
        for r in caplog.records
        if r.getMessage() == "recovery_mode_started" and r.recovery_id == second.id  # type: ignore[attr-defined]
    ]
    assert record.superseded == first.id  # type: ignore[attr-defined]
    assert read(units, ops_queries.recovery_state).id == second.id


def test_the_on_row_is_the_recovery_state_even_when_the_clock_went_back(
    units: UnitOfWorkFactory,
) -> None:
    first = begin(units, Recovery(FrozenClock(T0)))

    second = begin(units, Recovery(FrozenClock(T0 - timedelta(days=1))))

    found = read(units, ops_queries.recovery_state)
    assert (found.id, found.mode) == (second.id, "on")
    assert second.restored_at < first.restored_at  # the superseded row is the later one


def test_a_later_backup_time_than_the_superseded_rows_is_lowered(
    units: UnitOfWorkFactory, recovery: Recovery, clock: FrozenClock
) -> None:
    begin(units, recovery, backup_at=T0 - timedelta(days=1))
    clock.advance(timedelta(hours=1))

    second = begin(units, recovery, backup_at=T0 - timedelta(days=9))

    assert second.backup_at == T0 - timedelta(days=9)  # the given one is earlier still


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"backup_at": "yesterday"}, TypeError),
        ({"backup_at": datetime(2026, 11, 16, 9, 0)}, ValueError),
        ({"source_name": "C:\\backups\\x.db"}, ValueError),
        ({"source_name": "folder/x.db"}, ValueError),
        ({"source_name": " "}, ValueError),
        ({"source_name": None}, TypeError),
        ({"pre_restore_name": "D:x.db"}, ValueError),
    ],
)
def test_begin_refuses_a_path_or_a_naive_time_and_writes_nothing(
    units: UnitOfWorkFactory, recovery: Recovery, engine: Engine, changes: dict[str, Any],
    error: type[Exception],
) -> None:
    fields: dict[str, Any] = {
        "backup_at": T0,
        "source_name": NAME,
        "pre_restore_name": None,
    } | changes

    with pytest.raises(error), units.open():
        recovery.begin(**fields)

    assert query(engine, "SELECT count(*) FROM recovery_state") == [(0,)]


# pending, mark_done and finish ------------------------------------------------------------------


def pending(units: UnitOfWorkFactory, recovery: Recovery, registered: frozenset[RecoveryStep]) -> Any:
    with units.open():
        return recovery.pending(registered)


def test_pending_is_none_while_recovery_mode_is_off(
    units: UnitOfWorkFactory, recovery: Recovery
) -> None:
    assert pending(units, recovery, frozenset(STEPS)) is None


def test_pending_marks_unregistered_steps_not_applicable_and_returns_the_rest_in_order(
    units: UnitOfWorkFactory, recovery: Recovery, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    row = begin(units, recovery)
    registered = frozenset({RecoveryStep.REPOLL_UNSUBSCRIBES, RecoveryStep.REAPPLY_ERASURE_LEDGER})

    found = pending(units, recovery, registered)

    assert found.context == RecoveryContext(row.id, row.backup_at, row.restored_at)
    assert found.pending == (RecoveryStep.REAPPLY_ERASURE_LEDGER, RecoveryStep.REPOLL_UNSUBSCRIBES)
    state = read(units, ops_queries.recovery_state)
    assert state.steps == (
        (RecoveryStep.REAPPLY_ERASURE_LEDGER, StepStatus.PENDING),
        (RecoveryStep.MARK_OMS_UNKNOWN, StepStatus.NOT_APPLICABLE),
        (RecoveryStep.REINGEST_MAILBOXES, StepStatus.NOT_APPLICABLE),
        (RecoveryStep.REPOLL_UNSUBSCRIBES, StepStatus.PENDING),
    )
    marked = [
        (r.recovery_id, r.step)  # type: ignore[attr-defined]
        for r in caplog.records
        if r.getMessage() == "recovery_step_not_applicable"
    ]
    assert marked == [(row.id, "mark_oms_unknown"), (row.id, "reingest_mailboxes")]
    caplog.clear()

    again = pending(units, recovery, registered)  # nothing new to mark

    assert again.pending == found.pending
    assert "recovery_step_not_applicable" not in messages(caplog)


def test_mark_done_finish_and_the_gate(
    units: UnitOfWorkFactory,
    recovery: Recovery,
    clock: FrozenClock,
    engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    row = begin(units, recovery)
    pending(units, recovery, frozenset({RecoveryStep.REPOLL_UNSUBSCRIBES}))
    with units.open():
        assert recovery.finish() is True  # a step is still pending
    assert read(units, ops_queries.recovery_mode) is True

    with units.open():
        assert recovery.mark_done(row.id, RecoveryStep.REPOLL_UNSUBSCRIBES) is True
    with units.open():
        assert recovery.mark_done(row.id, RecoveryStep.REPOLL_UNSUBSCRIBES) is False  # once
    with units.open():
        assert recovery.mark_done(row.id, RecoveryStep.MARK_OMS_UNKNOWN) is False  # n/a
    clock.advance(timedelta(minutes=2))
    with units.open():
        assert recovery.finish() is False

    state = read(units, ops_queries.recovery_state)
    assert (state.mode, state.ended_at) == ("off", T0 + timedelta(minutes=2))
    assert dict(state.steps)[RecoveryStep.REPOLL_UNSUBSCRIBES] is StepStatus.DONE
    assert read(units, ops_queries.recovery_mode) is False
    with units.open():
        assert recovery.finish() is False  # still off, nothing to end
    assert messages(caplog).count("recovery_step_done") == 1
    [ended] = [r for r in caplog.records if r.getMessage() == "recovery_mode_ended"]
    assert ended.recovery_id == row.id  # type: ignore[attr-defined]
    assert pending(units, recovery, frozenset(STEPS)) is None


def test_mark_done_changes_nothing_on_a_row_that_is_not_on(
    units: UnitOfWorkFactory, recovery: Recovery, clock: FrozenClock
) -> None:
    first = begin(units, recovery)
    clock.advance(timedelta(minutes=1))
    begin(units, recovery)  # supersedes the first

    with units.open():
        assert recovery.mark_done(first.id, RecoveryStep.MARK_OMS_UNKNOWN) is False


def test_the_health_report_carries_the_newest_recovery_row(
    units: UnitOfWorkFactory, recovery: Recovery, clock: FrozenClock
) -> None:
    job_types = main.build_job_types(
        alerts=lambda: None,  # type: ignore[arg-type,return-value]
        backups=lambda: None,  # type: ignore[arg-type,return-value]
        restore_tests=lambda: None,  # type: ignore[arg-type,return-value]
        recovery=lambda: None,  # type: ignore[arg-type,return-value]
        contact_markets=lambda: None,  # type: ignore[arg-type,return-value]
        crawls=lambda: None,  # type: ignore[arg-type,return-value]
        discovery=lambda: None,  # type: ignore[arg-type,return-value]
        research=lambda: None,  # type: ignore[arg-type,return-value]
        extractions=lambda: None,  # type: ignore[arg-type,return-value]
        primary=lambda: None,  # type: ignore[arg-type,return-value]
    )
    issues = main.build_health_issues(budget=_NoBudget())  # type: ignore[arg-type]
    health = EngineHealth(
        clock,
        job_types,
        issues=issues,
        readiness=lambda: (CheckResult("python_version", Status.OK, "fine"),),
    )
    assert read(units, health.report).recovery is None

    row = begin(units, recovery)

    assert read(units, health.report).recovery == row


class _NoBudget:
    def health_issues(self) -> tuple[()]:
        return ()


# The table's checks -------------------------------------------------------------------------------

ON_ROW: dict[str, object] = {
    "id": "01JD3QZ8X5V9K2M7N4P6R8T0AB",
    "mode": "on",
    "restored_at": T0_TEXT,
    "backup_at": T0_TEXT,
    "source_name": NAME,
    "pre_restore_name": None,
    "ended_at": None,
    "reapply_erasure_ledger": "pending",
    "mark_oms_unknown": "pending",
    "reingest_mailboxes": "pending",
    "repoll_unsubscribes": "pending",
}
OFF_ROW: dict[str, object] = {
    **ON_ROW,
    "id": "01JD3QZ8X5V9K2M7N4P6R8T0AC",
    "mode": "off",
    "ended_at": T0_TEXT,
    "reapply_erasure_ledger": "done",
    "mark_oms_unknown": "not_applicable",
    "reingest_mailboxes": "done",
    "repoll_unsubscribes": "not_applicable",
}
SUPERSEDED_ROW: dict[str, object] = {
    **ON_ROW,
    "id": "01JD3QZ8X5V9K2M7N4P6R8T0AD",
    "mode": "superseded",
    "ended_at": T0_TEXT,
}


def insert_row(engine: Engine, row: dict[str, object]) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    query(engine, f"INSERT INTO recovery_state ({columns}) VALUES ({marks})", tuple(row.values()))


@pytest.fixture
def engine(db_engine: Engine) -> Engine:
    return db_engine


def test_valid_on_off_and_superseded_rows_are_accepted(engine: Engine) -> None:
    insert_row(engine, ON_ROW)
    insert_row(engine, OFF_ROW)
    insert_row(engine, SUPERSEDED_ROW)
    insert_row(engine, {**SUPERSEDED_ROW, "id": "01JD3QZ8X5V9K2M7N4P6R8T0AE"})

    assert query(engine, "SELECT count(*) FROM recovery_state") == [(4,)]


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        ({**ON_ROW, "ended_at": T0_TEXT}, "ck_recovery_state_outcome"),
        ({**SUPERSEDED_ROW, "ended_at": None}, "ck_recovery_state_outcome"),
        ({**OFF_ROW, "ended_at": None}, "ck_recovery_state_outcome"),
        ({**OFF_ROW, "reapply_erasure_ledger": "pending"}, "ck_recovery_state_outcome"),
        ({**OFF_ROW, "mark_oms_unknown": "pending"}, "ck_recovery_state_outcome"),
        ({**OFF_ROW, "reingest_mailboxes": "pending"}, "ck_recovery_state_outcome"),
        ({**OFF_ROW, "repoll_unsubscribes": "pending"}, "ck_recovery_state_outcome"),
        ({**ON_ROW, "mode": "paused"}, "ck_recovery_state_mode"),
        ({**ON_ROW, "mark_oms_unknown": "skipped"}, "ck_recovery_state_mark_oms_unknown"),
        (
            {**ON_ROW, "reapply_erasure_ledger": "maybe"},
            "ck_recovery_state_reapply_erasure_ledger",
        ),
        ({**ON_ROW, "reingest_mailboxes": "later"}, "ck_recovery_state_reingest_mailboxes"),
        ({**ON_ROW, "repoll_unsubscribes": "soon"}, "ck_recovery_state_repoll_unsubscribes"),
    ],
)
def test_a_row_that_breaks_a_check_is_refused_by_name(
    engine: Engine, row: dict[str, object], constraint: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        insert_row(engine, row)


def test_a_second_on_row_is_refused_by_the_partial_unique_index(engine: Engine) -> None:
    insert_row(engine, ON_ROW)

    with pytest.raises(sqlite3.IntegrityError, match="recovery_state.mode"):
        insert_row(engine, {**ON_ROW, "id": "01JD3QZ8X5V9K2M7N4P6R8T0AF"})
    [(sql,)] = query(
        engine, "SELECT sql FROM sqlite_master WHERE name = 'uq_recovery_state_on'"
    )
    assert "UNIQUE" in sql and "WHERE mode = 'on'" in sql


def test_ending_a_row_with_a_pending_step_is_refused(
    units: UnitOfWorkFactory, recovery: Recovery, engine: Engine
) -> None:
    row = begin(units, recovery)

    with pytest.raises(sqlite3.IntegrityError, match="ck_recovery_state_outcome"):
        query(
            engine,
            "UPDATE recovery_state SET mode = 'off', ended_at = ? WHERE id = ?",
            (T0_TEXT, row.id),
        )
    assert read(units, ops_queries.recovery_mode) is True
    assert RecoveryMode(read(units, ops_queries.recovery_state).mode) is RecoveryMode.ON
