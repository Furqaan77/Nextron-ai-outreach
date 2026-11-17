"""The ``ops`` heartbeat (``Heartbeat.beat``, ``queries.ops.heartbeat``, AD-20, NFR-14) on a
temporary database at head, with ``FrozenClock``: the row is saved in one transaction and the
heartbeat file touched after the commit; the checks keep the table to one row."""

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from nextron.app.ops.api import Heartbeat
from nextron.app.queries import ops as ops_queries
from nextron.db.models.ops import HEARTBEAT_ID
from nextron.db.repos.ops.heartbeat import HeartbeatRow
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, is_use_case
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
OTHER_ID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def heartbeat_file(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir / "heartbeat"


def beat(units: UnitOfWorkFactory, heartbeat: Heartbeat) -> None:
    with units.open():
        heartbeat.beat()


def stored(units: UnitOfWorkFactory) -> HeartbeatRow | None:
    with units.open(read_only=True):
        return ops_queries.heartbeat()


def raw(
    engine: Engine, statement: str, params: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    assert engine.url.database
    connection = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        return connection.execute(statement, params).fetchall()
    finally:
        connection.close()


def test_the_query_has_no_row_before_the_first_beat(units: UnitOfWorkFactory) -> None:
    assert stored(units) is None


def test_a_beat_inserts_the_row_then_updates_it(
    units: UnitOfWorkFactory, clock: FrozenClock, heartbeat_file: Path, db_engine: Engine
) -> None:
    heartbeat = Heartbeat(clock, heartbeat_file)

    beat(units, heartbeat)

    assert stored(units) == HeartbeatRow(HEARTBEAT_ID, os.getpid(), T0, T0)

    clock.advance(timedelta(seconds=30))
    beat(units, heartbeat)

    assert stored(units) == HeartbeatRow(HEARTBEAT_ID, os.getpid(), T0, T0 + timedelta(seconds=30))
    assert raw(db_engine, "SELECT count(*) FROM runner_heartbeat") == [(1,)]
    assert raw(db_engine, "SELECT count(*) FROM events") == [(0,)]  # no audit rows


def test_a_new_heartbeat_object_starts_afresh_and_takes_over_the_row(
    units: UnitOfWorkFactory, clock: FrozenClock, heartbeat_file: Path, db_engine: Engine
) -> None:
    beat(units, Heartbeat(clock, heartbeat_file))
    raw(db_engine, "UPDATE runner_heartbeat SET pid = 1")  # a previous engine's row
    clock.advance(timedelta(hours=1))

    beat(units, Heartbeat(clock, heartbeat_file))  # the engine restarted

    later = T0 + timedelta(hours=1)
    assert stored(units) == HeartbeatRow(HEARTBEAT_ID, os.getpid(), later, later)


def test_a_beat_creates_the_file_and_then_moves_its_mtime(
    units: UnitOfWorkFactory, clock: FrozenClock, heartbeat_file: Path
) -> None:
    heartbeat = Heartbeat(clock, heartbeat_file)

    beat(units, heartbeat)

    assert heartbeat_file.is_file()
    assert heartbeat_file.read_bytes() == b""  # it holds nothing
    old = heartbeat_file.stat().st_mtime - 3600
    os.utime(heartbeat_file, (old, old))

    beat(units, heartbeat)

    assert heartbeat_file.stat().st_mtime > old + 3000


def test_the_row_stays_committed_when_the_file_cannot_be_touched(
    units: UnitOfWorkFactory, clock: FrozenClock, tmp_path: Path
) -> None:
    heartbeat = Heartbeat(clock, tmp_path / "missing" / "heartbeat")

    with pytest.raises(FileNotFoundError):
        beat(units, heartbeat)

    assert stored(units) == HeartbeatRow(HEARTBEAT_ID, os.getpid(), T0, T0)
    assert not (tmp_path / "missing").exists()


def test_a_failed_row_write_raises_and_touches_no_file(
    units: UnitOfWorkFactory, clock: FrozenClock, heartbeat_file: Path, db_engine: Engine
) -> None:
    raw(db_engine, "DROP TABLE runner_heartbeat")
    heartbeat = Heartbeat(clock, heartbeat_file)

    with pytest.raises(Exception, match="runner_heartbeat"):
        beat(units, heartbeat)

    assert not heartbeat_file.exists()


def test_started_at_is_this_objects_first_beat_even_when_that_beat_failed(
    units: UnitOfWorkFactory, clock: FrozenClock, tmp_path: Path
) -> None:
    folder = tmp_path / "later"
    heartbeat = Heartbeat(clock, folder / "heartbeat")
    with pytest.raises(FileNotFoundError):
        beat(units, heartbeat)
    folder.mkdir()
    clock.advance(timedelta(seconds=30))

    beat(units, heartbeat)

    assert stored(units) == HeartbeatRow(HEARTBEAT_ID, os.getpid(), T0, T0 + timedelta(seconds=30))


def test_beat_reads_the_clock_once(units: UnitOfWorkFactory, heartbeat_file: Path) -> None:
    class CountingClock(FrozenClock):
        reads = 0

        def now(self) -> datetime:
            CountingClock.reads += 1
            return super().now()

    counting = CountingClock(T0)

    beat(units, Heartbeat(counting, heartbeat_file))

    assert CountingClock.reads == 1


def test_beat_is_a_use_case_an_entry_point_calls_in_its_unit_of_work(
    units: UnitOfWorkFactory, clock: FrozenClock, heartbeat_file: Path
) -> None:
    heartbeat = Heartbeat(clock, heartbeat_file)

    assert is_use_case(Heartbeat.beat)
    with pytest.raises(UnitOfWorkError):
        heartbeat.beat()  # no unit of work open
    with units.open(read_only=True), pytest.raises(UnitOfWorkError):
        heartbeat.beat()
    assert stored(units) is None
    assert not heartbeat_file.exists()


@pytest.mark.parametrize(
    ("statement", "params"),
    [
        (
            "INSERT INTO runner_heartbeat (id, pid, started_at, beat_at) VALUES (?, ?, ?, ?)",
            (OTHER_ID, 42, "2026-11-17T09:00:00.000000Z", "2026-11-17T09:00:00.000000Z"),
        ),
        ("UPDATE runner_heartbeat SET pid = 0", ()),
        ("UPDATE runner_heartbeat SET pid = -1", ()),
        ("UPDATE runner_heartbeat SET id = ?", (OTHER_ID,)),
    ],
)
def test_the_checks_refuse_a_second_row_or_a_pid_below_1(
    statement: str,
    params: tuple[object, ...],
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    heartbeat_file: Path,
    db_engine: Engine,
) -> None:
    beat(units, Heartbeat(clock, heartbeat_file))

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        raw(db_engine, statement, params)

    assert stored(units) == HeartbeatRow(HEARTBEAT_ID, os.getpid(), T0, T0)


def test_a_first_row_with_a_pid_of_0_is_refused(db_engine: Engine) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="ck_runner_heartbeat_pid"):
        raw(
            db_engine,
            "INSERT INTO runner_heartbeat (id, pid, started_at, beat_at) VALUES (?, 0, ?, ?)",
            (HEARTBEAT_ID, "2026-11-17T09:00:00.000000Z", "2026-11-17T09:00:00.000000Z"),
        )
