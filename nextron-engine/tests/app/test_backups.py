"""Backups (the ``ops`` service, Story 1.16) on real units of work over a temporary database at
head, with a temporary backup dir and the real snapshot code: the Daily, Manual, Not verified,
Record fails, Rotation and Schedule rows of the story's matrix, a rotation that fails after
the row is recorded, ``record_pre_migrate``, the table's checks, ``build_local_backups``'s
binding, the lock (two threads never copy at once), ``health_issues`` and the health report's
last backup.
Nothing waits longer than 0.05 s per step."""

import functools
import hashlib
import json
import logging
import sqlite3
import threading
import time as clock_time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Backups, EngineHealth, LocalBackups, TakenBackup
from nextron.app.queries import ops as ops_queries
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.migrations import head_revision
from nextron.db.repos.ops import backups as backups_repo
from nextron.db.repos.ops import snapshot as snapshot_module
from nextron.db.repos.ops.snapshot import Snapshot, discard, snapshot
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.backup import (
    BACKUP_FAILED,
    BackupKind,
    BackupNotVerified,
    BackupSchedule,
    copy_name,
)
from nextron.domain.health import HealthIssue
from nextron.domain.job import InvalidJob, Job, JobStatus, Lane
from nextron.domain.readiness import CheckResult, Status
from nextron.domain.settings_schema import Key
from nextron.jobs import handlers
from nextron.ports.errors import AdapterError
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
OPS_LOGGER = "nextron.app.ops.api"
SNAPSHOT_LOGGER = "nextron.db.repos.ops.snapshot"
HEAD = head_revision()
READINESS = (CheckResult("python_version", Status.OK, "fine"),)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class World:
    """The ``ops`` backups on real units of work, a frozen clock and ``<tmp>\\backups\\local``."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    tmp_path: Path
    snap: Callable[[Path], Snapshot] | None = None
    notify: Notify = field(init=False)
    queue: JobQueue = field(init=False)
    local: LocalBackups = field(init=False)
    backups: Backups = field(init=False)

    def __post_init__(self) -> None:
        job_types = main.build_job_types(alerts=lambda: self.notify, backups=lambda: self.backups)
        self.queue = JobQueue(self.clock, job_types)
        self.notify = Notify(
            self.clock, self.queue, alert_ports={Channel.TELEGRAM: lambda: InMemoryAlert()}
        )
        self.local = LocalBackups(
            self.clock,
            env="test",
            local_dir=self.local_dir,
            snapshot=self.snap if self.snap is not None else self.real_snapshot,
        )
        self.backups = Backups(self.clock, self.local, notify=self.notify, queue=self.queue)

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    @property
    def local_dir(self) -> Path:
        return self.tmp_path / "backups" / "local"

    def real_snapshot(self, target: Path) -> Snapshot:
        return snapshot(self.db_file, target)

    def back_up(self, kind: BackupKind = BackupKind.DAILY) -> str:
        with self.units.open():
            return self.backups.back_up(kind)

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return connection.execute(statement, params).fetchall()
        finally:
            connection.close()

    def read(self, call: Callable[[], Any]) -> Any:
        with self.units.open(read_only=True):
            return call()

    def files(self) -> list[str]:
        if not self.local_dir.exists():
            return []
        return sorted(path.name for path in self.local_dir.iterdir())

    def set_setting(self, key: Key, value: object) -> None:
        with self.units.open():
            Setup(self.clock).set(key, value)

    def rotate(self) -> int:
        @use_case
        def run() -> int:
            return self.local.rotate()

        with self.units.open():
            return run()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def world(
    units: UnitOfWorkFactory, db_engine: Engine, clock: FrozenClock, tmp_path: Path
) -> World:
    return World(units, db_engine, clock, tmp_path)


def make_world(world: World, snap: Callable[[Path], Snapshot]) -> World:
    return World(world.units, world.engine, world.clock, world.tmp_path, snap)


def records(caplog: pytest.LogCaptureFixture, logger: str = OPS_LOGGER) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == logger]


def all_rows(world: World) -> list[tuple[Any, ...]]:
    return world.query(
        "SELECT id, kind, status, path, size_bytes, sha256, db_revision, suppression_count, "
        "provenance_count, error, local_deleted_at FROM backups ORDER BY started_at, id"
    )


# Row: Daily -------------------------------------------------------------------------------------


def test_a_daily_backup_is_a_verified_copy_in_local(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    backup_id = world.back_up(BackupKind.DAILY)

    name = "nextron-test-20261117T090000.000Z.db"
    copy = world.local_dir / name
    assert world.files() == [name]
    row = world.read(ops_queries.last_backup)
    assert row.id == backup_id
    assert (row.kind, row.status, row.path) == ("daily", "verified", str(copy))
    assert (row.size_bytes, row.sha256) == (copy.stat().st_size, sha256(copy))
    assert row.db_revision == HEAD
    assert (row.suppression_count, row.provenance_count) == (None, None)  # absent tables
    assert (row.started_at, row.finished_at, row.error, row.local_deleted_at) == (
        T0,
        T0,
        None,
        None,
    )
    [completed] = [r for r in records(caplog) if r.getMessage() == "backup_completed"]
    assert completed.levelno == logging.INFO
    assert (completed.backup_id, completed.kind, completed.size_bytes, completed.file) == (  # type: ignore[attr-defined]
        backup_id,
        BackupKind.DAILY,
        copy.stat().st_size,
        name,
    )
    assert world.query("SELECT count(*) FROM alerts") == [(0,)]
    assert world.query("SELECT count(*) FROM notifications") == [(0,)]
    assert world.query("SELECT count(*) FROM events WHERE entity = 'backups'") == [(0,)]


def test_the_copy_counts_the_suppression_and_provenance_tables_when_they_exist(
    world: World,
) -> None:
    world.query("CREATE TABLE suppression_entries (id INTEGER PRIMARY KEY)")
    world.query("INSERT INTO suppression_entries DEFAULT VALUES")
    world.query("INSERT INTO suppression_entries DEFAULT VALUES")
    world.query("CREATE TABLE provenance_records (id INTEGER PRIMARY KEY)")

    backup_id = world.back_up()

    row = world.read(ops_queries.last_backup)
    assert row.id == backup_id
    assert (row.suppression_count, row.provenance_count) == (2, 0)


def test_the_backup_db_tick_takes_a_daily_backup(world: World) -> None:
    with world.units.open():
        enqueued = world.queue.enqueue_tick("backup_db")
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None and claim.job.id == enqueued.job_id
    assert dict(claim.job.payload) == {"v": 1}
    handler = world.queue._job_types.get("backup_db").handler  # noqa: SLF001
    assert handler is not None

    with world.units.open():
        assert handler(claim.job) is None
    with world.units.open():
        assert world.queue.complete(claim)

    row = world.read(ops_queries.last_backup)
    assert (row.kind, row.status) == ("daily", "verified")


# Row: Manual --------------------------------------------------------------------------------------


def test_request_backup_queues_one_manual_job_at_a_time_and_absorbs_a_click_while_it_is_live(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    with world.units.open():
        first = world.backups.request_backup()
    with world.units.open():
        second = world.backups.request_backup()

    assert first.created and not second.created
    assert second.job_id == first.job_id
    [(key, lane, payload, status)] = world.query(
        "SELECT idempotency_key, lane, payload, status FROM jobs"
    )
    assert (key, lane, status) == ("backup_db:global:2026-11-17", "bulk", "queued")
    assert json.loads(payload) == {"v": 1, "kind": "manual"}
    requested = [r for r in records(caplog) if r.getMessage() == "backup_requested"]
    assert [(r.job_id, r.job_created) for r in requested] == [  # type: ignore[attr-defined]
        (first.job_id, True),
        (first.job_id, False),
    ]
    assert world.files() == []  # the request copies nothing; the job does

    # once that job has finished, a click the same day queues a new one
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None and claim.job.id == first.job_id
    with world.units.open():
        assert world.queue.complete(claim)
    with world.units.open():
        third = world.backups.request_backup()

    assert third.created and third.job_id != first.job_id
    assert world.query("SELECT id, status FROM jobs ORDER BY created_at, id") == [
        (first.job_id, "done"),
        (third.job_id, "queued"),
    ]


def test_the_manual_job_takes_a_manual_backup(world: World) -> None:
    with world.units.open():
        enqueued = world.backups.request_backup()
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None and claim.job.id == enqueued.job_id
    handler = handlers.backup_db(lambda: world.backups)

    with world.units.open():
        handler(claim.job)

    row = world.read(ops_queries.last_backup)
    assert (row.kind, row.status) == ("manual", "verified")


@pytest.mark.parametrize("kind", ["pre_migrate", "weekly", 7])
def test_the_handler_refuses_any_other_kind(world: World, kind: object) -> None:
    handler = handlers.backup_db(lambda: world.backups)
    job = Job("01JD3QZ8X5V9K2M7N4P6R8T0AB", "backup_db", Lane.BULK, {"v": 1, "kind": kind}, 1)

    with world.units.open(), pytest.raises(InvalidJob, match="daily or manual"):
        handler(job)

    assert world.files() == []
    assert all_rows(world) == []


@pytest.mark.parametrize("kind", [BackupKind.PRE_MIGRATE, "weekly"])
def test_back_up_takes_a_daily_or_manual_backup_only(world: World, kind: object) -> None:
    with pytest.raises(ValueError, match="daily or manual"):
        world.back_up(kind)  # type: ignore[arg-type]

    assert world.files() == []


# Row: Not verified --------------------------------------------------------------------------------


def assert_failed(
    world: World, backup_id: str, kind: str, error: str, caplog: pytest.LogCaptureFixture
) -> None:
    [(row_id, row_kind, status, path, size, digest, _, _, _, stored_error, deleted)] = all_rows(
        world
    )
    assert (row_id, row_kind, status) == (backup_id, kind, "failed")
    assert (size, digest, deleted) == (None, None, None)
    assert stored_error == error
    assert Path(path).parent == world.local_dir
    assert world.files() == []  # no file left
    text = f"Backup failed: {kind} {backup_id}"
    assert world.query("SELECT kind, subject, channel, text, status FROM alerts") == [
        (BACKUP_FAILED, backup_id, "telegram", text, "pending")
    ]
    assert world.query("SELECT kind, subject, text FROM notifications") == [
        (BACKUP_FAILED, backup_id, text)
    ]
    [failed] = [r for r in records(caplog) if r.getMessage() == "backup_failed"]
    assert failed.levelno == logging.ERROR
    assert (failed.backup_id, failed.kind) == (backup_id, kind)  # type: ignore[attr-defined]
    assert world.read(ops_queries.health_issues) == (
        HealthIssue(BACKUP_FAILED, backup_id, text, detail=f"Error: {error}"),
    )


def test_an_injected_snapshot_that_raises_records_a_failed_backup(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    def broken(target: Path) -> Snapshot:
        raise OSError("disk full")

    failing = make_world(world, broken)

    backup_id = failing.back_up(BackupKind.MANUAL)  # never raises: the job is not retried

    assert_failed(failing, backup_id, "manual", "OSError: disk full", caplog)
    [failed] = [r for r in records(caplog) if r.getMessage() == "backup_failed"]
    assert failed.code == "OSError"  # type: ignore[attr-defined]


def test_a_malformed_copy_is_not_verified_and_deleted(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world.query("CREATE TABLE filler (id INTEGER PRIMARY KEY, text TEXT)")
    for _ in range(20):
        world.query("INSERT INTO filler (text) VALUES (?)", ("x" * 2000,))
    real_copy = snapshot_module.copy_database

    def damaging_copy(db_path: Path, target: Path) -> None:
        real_copy(db_path, target)
        data = bytearray(target.read_bytes())
        page = int.from_bytes(data[16:18], "big")
        for start in range(page, len(data), page):  # every page but the first: b-tree pages
            data[start : start + 16] = b"\xff" * 16
        target.write_bytes(bytes(data))

    monkeypatch.setattr(snapshot_module, "copy_database", damaging_copy)

    backup_id = world.back_up(BackupKind.DAILY)

    [(error,)] = world.query("SELECT error FROM backups")
    assert "malformed" in error or error.startswith("backup_not_verified: integrity_check: ")
    assert_failed(world, backup_id, "daily", error, caplog)


def test_an_integrity_check_that_is_not_ok_fails_the_backup(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    real_open = snapshot_module.open_copy
    problem = "*** in database main ***\nPage 5: btree page is broken " + "x" * 300

    class Reporting:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, sql: str) -> Any:
            if sql.startswith("PRAGMA integrity_check"):
                return FakeCursor([(problem,), ("row 2",)])
            return self._connection.execute(sql)

    @contextmanager
    def reporting_open(path: Path) -> Iterator[Reporting]:
        with real_open(path) as connection:
            yield Reporting(connection)

    monkeypatch.setattr(snapshot_module, "open_copy", reporting_open)

    backup_id = world.back_up(BackupKind.DAILY)

    error = "backup_not_verified: integrity_check: *** in database main ***"
    assert_failed(world, backup_id, "daily", error, caplog)


@dataclass
class FakeCursor:
    rows: list[tuple[object, ...]]

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def test_a_long_integrity_line_is_cut_to_200_characters() -> None:
    rows: list[tuple[object, ...]] = [("y" * 500,)]

    assert snapshot_module._first_line(rows) == "y" * 200  # noqa: SLF001
    assert snapshot_module._first_line([]) == "no result"  # noqa: SLF001


def test_a_copy_with_no_revision_is_not_verified(tmp_path: Path) -> None:
    empty = tmp_path / "empty.db"
    writer = sqlite3.connect(empty, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.close()
    target = tmp_path / "local" / "copy.db"

    with pytest.raises(BackupNotVerified, match="the copy has no Alembic revision"):
        snapshot(empty, target)

    assert not target.exists()


def test_an_existing_copy_name_fails_the_backup_and_is_left_alone(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world.local_dir.mkdir(parents=True)
    existing = world.local_dir / copy_name("test", T0)
    existing.write_bytes(b"someone else's file")

    backup_id = world.back_up(BackupKind.DAILY)

    [(status, error)] = world.query("SELECT status, error FROM backups")
    assert status == "failed"
    assert error.startswith("FileExistsError: ")
    assert existing.read_bytes() == b"someone else's file"
    assert world.read(ops_queries.health_issues)[0].subject == backup_id


def test_a_missing_database_fails_the_backup_and_leaves_no_file(tmp_path: Path) -> None:
    target = tmp_path / "local" / "copy.db"

    with pytest.raises(sqlite3.OperationalError):
        snapshot(tmp_path / "missing.db", target)

    assert not target.exists()
    assert not (tmp_path / "missing.db").exists()


def test_snapshot_describes_a_verified_copy(world: World) -> None:
    target = world.tmp_path / "elsewhere" / "deeper" / "copy.db"

    made = snapshot(world.db_file, target)

    assert made == Snapshot(target, target.stat().st_size, sha256(target), HEAD, None, None)


# Row: Record fails --------------------------------------------------------------------------------


def test_a_failed_insert_deletes_the_copy_and_raises_a_retryable_error(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    def broken_insert(**kwargs: object) -> str:
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(backups_repo, "insert", broken_insert)

    with pytest.raises(AdapterError) as raised:
        world.back_up(BackupKind.DAILY)

    assert (raised.value.retryable, raised.value.code) == (True, "backup_not_recorded")
    assert "RuntimeError" in raised.value.message
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert world.files() == []  # never a copy without a row
    assert all_rows(world) == []
    [failed] = [r for r in records(caplog) if r.getMessage() == "backup_record_failed"]
    assert failed.levelno == logging.ERROR
    assert (failed.kind, failed.code) == (BackupKind.DAILY, "RuntimeError")  # type: ignore[attr-defined]
    assert failed.file == "nextron-test-20261117T090000.000Z.db"  # type: ignore[attr-defined]


def test_a_backup_job_whose_row_cannot_be_recorded_is_retried(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_insert(**kwargs: object) -> str:
        raise RuntimeError("the database is locked")

    with world.units.open():
        world.queue.enqueue_tick("backup_db")
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None
    handler = world.queue._job_types.get("backup_db").handler  # noqa: SLF001
    monkeypatch.setattr(backups_repo, "insert", broken_insert)

    with world.units.open(), pytest.raises(AdapterError) as raised:
        handler(claim.job)
    with world.units.open():
        status = world.queue.fail(claim, raised.value)

    assert status is JobStatus.QUEUED  # a retry, not dead
    assert world.files() == []


def test_a_failed_alert_rolls_the_failed_row_back_and_raises(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(target: Path) -> Snapshot:
        raise OSError("disk full")

    failing = make_world(world, broken)

    def broken_alert(*args: object) -> None:
        raise RuntimeError("notify broke")

    monkeypatch.setattr(failing.notify, "alert", broken_alert)

    with pytest.raises(AdapterError) as raised:
        failing.back_up(BackupKind.DAILY)

    assert raised.value.retryable
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert all_rows(failing) == []


# Row: Rotation ------------------------------------------------------------------------------------


def backup_at(world: World, when: datetime, kind: BackupKind = BackupKind.DAILY) -> str:
    world.clock.set(when)
    return world.back_up(kind)


def deleted_at(world: World) -> dict[str, datetime | None]:
    return {
        backup_id: None if at is None else datetime.fromisoformat(at)
        for backup_id, at in world.query("SELECT id, local_deleted_at FROM backups")
    }


def test_rotation_deletes_the_verified_copies_older_than_35_days(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    old = [backup_at(world, T0 - timedelta(days=days)) for days in (40, 36, 34)]
    names = {
        backup_id: Path(path).name
        for backup_id, path in world.query("SELECT id, path FROM backups")
    }
    assert len(world.files()) == 3  # nothing older than 35 days yet at each of those times
    caplog.clear()

    new = backup_at(world, T0)

    assert world.files() == sorted([names[old[2]], copy_name("test", T0)])
    marks = deleted_at(world)
    assert marks[old[0]] == marks[old[1]] == T0
    assert marks[old[2]] is None and marks[new] is None
    [rotated] = [r for r in records(caplog) if r.getMessage() == "backups_rotated"]
    assert (rotated.levelno, rotated.count) == (logging.INFO, 2)  # type: ignore[attr-defined]

    # a minute later the next backup's rotation finds nothing: those copies are marked deleted
    caplog.clear()
    later = backup_at(world, T0 + timedelta(minutes=1))

    assert [r for r in records(caplog) if r.getMessage() == "backups_rotated"] == []
    assert world.rotate() == 0
    after = deleted_at(world)
    assert after[old[0]] == after[old[1]] == T0  # not marked again
    assert after[old[2]] is None and after[new] is None and after[later] is None


def test_the_newest_verified_copy_is_kept_even_when_it_is_40_days_old(world: World) -> None:
    kept = backup_at(world, T0 - timedelta(days=40))
    world.clock.set(T0)

    assert world.rotate() == 0

    assert deleted_at(world) == {kept: None}
    assert len(world.files()) == 1


def test_rotation_follows_the_setting(world: World) -> None:
    eight = backup_at(world, T0 - timedelta(days=8))
    six = backup_at(world, T0 - timedelta(days=6))
    world.set_setting(Key.BACKUP_ROTATION_DAYS, 7)

    new = backup_at(world, T0)

    marks = deleted_at(world)
    assert marks[eight] == T0
    assert marks[six] is None and marks[new] is None


def test_a_failed_backup_never_rotates(world: World) -> None:
    old = backup_at(world, T0 - timedelta(days=40))
    backup_at(world, T0 - timedelta(days=39))

    def broken(target: Path) -> Snapshot:
        raise OSError("disk full")

    failing = make_world(world, broken)
    failing.clock.set(T0)
    failing.back_up()

    assert deleted_at(world)[old] is None


def insert_verified(world: World, path: Path, started_at: datetime) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a copy")
    made = Snapshot(path, 6, "0" * 64, HEAD, None, None)
    taken = TakenBackup(BackupKind.DAILY, path, started_at, started_at, made, None, None)

    @use_case
    def record() -> str:
        with current().transaction():
            return world.local.insert(taken)

    with world.units.open():
        return record()


@pytest.mark.parametrize(
    ("where", "reason"),
    [
        (lambda world: world.tmp_path / "elsewhere" / copy_name("test", T0), "outside_local_dir"),
        (lambda world: world.local_dir / "nested" / copy_name("test", T0), "outside_local_dir"),
        (lambda world: world.local_dir / copy_name("prod", T0), "not_a_copy_name"),
        (lambda world: world.local_dir / "nextron.db", "not_a_copy_name"),
    ],
)
def test_rotation_skips_a_copy_outside_local_or_misnamed(
    world: World,
    caplog: pytest.LogCaptureFixture,
    where: Callable[[World], Path],
    reason: str,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    path = where(world)
    odd = insert_verified(world, path, T0 - timedelta(days=40))
    world.clock.set(T0 + timedelta(seconds=1))
    backup_at(world, T0 + timedelta(seconds=1))

    assert path.read_bytes() == b"a copy"
    assert deleted_at(world)[odd] is None
    [skipped] = [r for r in records(caplog) if r.getMessage() == "backup_rotation_skipped"]
    assert skipped.levelno == logging.WARNING
    assert (skipped.backup_id, skipped.reason) == (odd, reason)  # type: ignore[attr-defined]
    assert str(world.tmp_path) not in caplog.text  # file names only, never a full path


def test_a_locked_copy_is_retried_next_time(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    old = backup_at(world, T0 - timedelta(days=40))
    [(old_path,)] = world.query("SELECT path FROM backups WHERE id = ?", (old,))
    locked = Path(old_path)
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == locked:
            raise PermissionError(13, "The process cannot access the file")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    backup_at(world, T0)

    assert locked.exists()
    assert deleted_at(world)[old] is None
    [warned] = [r for r in records(caplog, SNAPSHOT_LOGGER) if r.levelno == logging.WARNING]
    assert warned.getMessage() == "backup_copy_not_deleted"
    assert (warned.code, warned.file) == ("PermissionError", locked.name)  # type: ignore[attr-defined]

    monkeypatch.setattr(Path, "unlink", real_unlink)
    world.clock.set(T0 + timedelta(hours=1))
    assert world.rotate() == 1

    assert not locked.exists()
    assert deleted_at(world)[old] == T0 + timedelta(hours=1)


def test_rotation_skips_when_the_setting_does_not_parse(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    old = backup_at(world, T0 - timedelta(days=40))
    world.query(
        "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
        ("01JD3QZ8X5V9K2M7N4P6R8T0AB", "backup_rotation_days", "99", "2026-11-17T09:00:00.000000Z"),
    )

    backup_at(world, T0)

    assert deleted_at(world)[old] is None
    [skipped] = [r for r in records(caplog) if r.getMessage() == "backup_rotation_skipped"]
    assert (skipped.levelno, skipped.reason) == (logging.WARNING, "setting_unreadable")  # type: ignore[attr-defined]


def broken_rotation(monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    """Make rotation raise, as a locked database would, inside its first or second
    transaction."""

    def locked(*args: object, **kwargs: object) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backups_repo, where, locked)


@pytest.mark.parametrize("where", ["rotation_candidates", "mark_deleted"])
def test_a_failed_rotation_never_fails_a_recorded_backup(
    world: World,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    backup_at(world, T0 - timedelta(days=40))  # a candidate, so mark_deleted is reached
    broken_rotation(monkeypatch, where)
    world.clock.set(T0)
    with world.units.open():
        enqueued = world.queue.enqueue_tick("backup_db")
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None and claim.job.id == enqueued.job_id
    handler = world.queue._job_types.get("backup_db").handler  # noqa: SLF001
    assert handler is not None

    with world.units.open():
        assert handler(claim.job) is None  # the job's use case returns
    with world.units.open():
        assert world.queue.complete(claim)

    row = world.read(ops_queries.last_backup)
    assert (row.kind, row.status, row.started_at) == ("daily", "verified", T0)
    assert world.query("SELECT status FROM jobs") == [("done",)]
    [failed] = [r for r in records(caplog) if r.getMessage() == "backup_rotation_failed"]
    assert failed.levelno == logging.ERROR
    assert (failed.backup_id, failed.code) == (row.id, "OperationalError")  # type: ignore[attr-defined]
    assert failed.exc_info is not None
    assert [r.getMessage() for r in records(caplog)].count("backup_record_failed") == 0


def test_a_failed_rotation_after_a_pre_migrate_copy_still_returns_its_id(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    broken_rotation(monkeypatch, "rotation_candidates")
    taken = world.local.take(BackupKind.PRE_MIGRATE)

    with world.units.open():
        backup_id = world.local.record_pre_migrate(taken)

    row = world.read(ops_queries.last_backup)
    assert (row.id, row.kind, row.status) == (backup_id, "pre_migrate", "verified")
    [failed] = [r for r in records(caplog) if r.getMessage() == "backup_rotation_failed"]
    assert failed.backup_id == backup_id  # type: ignore[attr-defined]


def test_discard_reports_whether_the_file_is_gone(tmp_path: Path) -> None:
    present = tmp_path / "copy.db"
    present.write_bytes(b"x")

    assert discard(present) is True
    assert discard(present) is True  # already gone
    assert not present.exists()


# The table's checks -----------------------------------------------------------------------------

VERIFIED_ROW: dict[str, object] = {
    "id": "01JD3QZ8X5V9K2M7N4P6R8T0AB",
    "kind": "daily",
    "status": "verified",
    "started_at": "2026-11-17T09:00:00.000000Z",
    "finished_at": "2026-11-17T09:00:01.000000Z",
    "path": "C:/backups/local/nextron-test-20261117T090000.000Z.db",
    "size_bytes": 4096,
    "sha256": "a" * 64,
    "db_revision": "0007_1_16_backups",
    "suppression_count": None,
    "provenance_count": None,
    "error": None,
    "local_deleted_at": None,
}
FAILED_ROW: dict[str, object] = {
    **VERIFIED_ROW,
    "status": "failed",
    "size_bytes": None,
    "sha256": None,
    "db_revision": None,
    "error": "OSError: disk full",
}


def insert_row(world: World, row: dict[str, object]) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    world.query(f"INSERT INTO backups ({columns}) VALUES ({marks})", tuple(row.values()))


def test_a_valid_verified_and_failed_row_are_accepted(world: World) -> None:
    insert_row(world, VERIFIED_ROW)
    insert_row(world, {**FAILED_ROW, "id": "01JD3QZ8X5V9K2M7N4P6R8T0AC"})

    assert world.query("SELECT count(*) FROM backups") == [(2,)]


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        ({**VERIFIED_ROW, "sha256": None}, "ck_backups_outcome"),
        ({**VERIFIED_ROW, "error": "OSError: disk full"}, "ck_backups_outcome"),
        ({**FAILED_ROW, "size_bytes": 4096}, "ck_backups_outcome"),
        ({**FAILED_ROW, "local_deleted_at": "2026-11-18T09:00:00.000000Z"}, "ck_backups_outcome"),
        ({**FAILED_ROW, "error": None}, "ck_backups_outcome"),
        ({**VERIFIED_ROW, "suppression_count": -1}, "ck_backups_counts"),
        ({**VERIFIED_ROW, "provenance_count": -1}, "ck_backups_counts"),
        ({**VERIFIED_ROW, "size_bytes": -1}, "ck_backups_counts"),
        ({**VERIFIED_ROW, "sha256": "a" * 63}, "ck_backups_sha256"),
        ({**VERIFIED_ROW, "kind": "weekly"}, "ck_backups_kind"),
        ({**VERIFIED_ROW, "status": "pending"}, "ck_backups_status"),
    ],
    ids=[
        "verified without sha256",
        "verified with an error",
        "failed with a size",
        "failed with local_deleted_at",
        "failed without an error",
        "negative suppression count",
        "negative provenance count",
        "negative size",
        "63-character sha256",
        "unknown kind",
        "unknown status",
    ],
)
def test_the_table_refuses_a_row_that_breaks_a_check(
    world: World, row: dict[str, object], constraint: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        insert_row(world, row)

    assert world.query("SELECT count(*) FROM backups") == [(0,)]


# Row: Schedule ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("newest", "missed"),
    [
        (T0 - timedelta(days=1, minutes=1), True),  # before yesterday's 10:00 UTC slot
        (T0 - timedelta(minutes=30), False),  # after it (today's 10:00 is still to come)
        (None, False),  # no daily backup yet: wait for the first time
    ],
)
def test_schedule_reports_a_missed_daily_time(
    world: World, newest: datetime | None, missed: bool
) -> None:
    if newest is not None:
        backup_at(world, newest)
    world.clock.set(T0)

    found = world.read(world.backups.schedule)

    assert found == BackupSchedule(time(10, 0), "UTC", missed)


def test_schedule_ignores_manual_and_pre_migrate_backups(world: World) -> None:
    backup_at(world, T0 - timedelta(days=3))
    backup_at(world, T0 - timedelta(minutes=5), BackupKind.MANUAL)
    world.clock.set(T0)

    assert world.read(world.backups.schedule).missed is True


def test_schedule_reads_the_time_in_the_senders_timezone(world: World) -> None:
    world.set_setting(Key.SENDER_TIMEZONE, "America/Los_Angeles")
    world.set_setting(Key.BACKUP_TIME, time(2, 30))

    found = world.read(world.backups.schedule)

    assert found == BackupSchedule(time(2, 30), "America/Los_Angeles", False)


# Pre-migrate --------------------------------------------------------------------------------------


def test_record_pre_migrate_records_a_verified_pre_migrate_copy_and_rotates(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    old = backup_at(world, T0 - timedelta(days=40))
    world.clock.set(T0)
    taken = world.local.take(BackupKind.PRE_MIGRATE)
    assert taken.verified and taken.path.exists()

    with world.units.open():
        backup_id = world.local.record_pre_migrate(taken)

    row = world.read(ops_queries.last_backup)
    assert (row.id, row.kind, row.status, row.path) == (
        backup_id,
        "pre_migrate",
        "verified",
        str(taken.path),
    )
    assert deleted_at(world)[old] == T0
    assert [r.getMessage() for r in records(caplog)][-2:] == ["backup_completed", "backups_rotated"]


def test_take_never_touches_the_database(world: World) -> None:
    taken = world.local.take(BackupKind.PRE_MIGRATE)  # no unit of work is open

    assert taken.verified
    assert taken.snapshot is not None and taken.snapshot.path == taken.path
    assert (taken.kind, taken.started_at, taken.error, taken.code) == (
        BackupKind.PRE_MIGRATE,
        T0,
        None,
        None,
    )
    assert all_rows(world) == []


def test_record_pre_migrate_refuses_anything_but_a_verified_pre_migrate_copy(
    world: World,
) -> None:
    daily = world.local.take(BackupKind.DAILY)
    world.clock.advance(timedelta(milliseconds=1))

    def broken(target: Path) -> Snapshot:
        raise OSError("disk full")

    failed = make_world(world, broken).local.take(BackupKind.PRE_MIGRATE)

    for taken in (daily, failed):
        with world.units.open(), pytest.raises(ValueError, match="verified pre_migrate"):
            world.local.record_pre_migrate(taken)

    assert all_rows(world) == []


# The lock -----------------------------------------------------------------------------------------


class TickingClock:
    """A thread-safe clock that moves 1 ms on every read, so no two copies share a name."""

    def __init__(self, start: datetime) -> None:
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            self._now += timedelta(milliseconds=1)
            return self._now


def test_two_threads_never_copy_at_once(world: World) -> None:
    inside = 0
    most = 0
    guard = threading.Lock()

    def watched(target: Path) -> Snapshot:
        nonlocal inside, most
        with guard:
            inside += 1
            most = max(most, inside)
        clock_time.sleep(0.05)
        try:
            return snapshot(world.db_file, target)
        finally:
            with guard:
                inside -= 1

    ticking = TickingClock(T0)
    local = LocalBackups(ticking, env="test", local_dir=world.local_dir, snapshot=watched)  # type: ignore[arg-type]
    backups = Backups(ticking, local, notify=world.notify, queue=world.queue)  # type: ignore[arg-type]
    made: list[str] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            with world.units.open():
                made.append(backups.back_up(BackupKind.DAILY))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert most == 1
    assert len(made) == 2
    assert [status for (status,) in world.query("SELECT status FROM backups")] == ["verified"] * 2
    assert len(world.files()) == 2


# Health -------------------------------------------------------------------------------------------


def test_health_issues_list_each_failure_since_the_newest_verified_backup(world: World) -> None:
    def broken(target: Path) -> Snapshot:
        raise OSError("disk full: a@b.example")

    failing = make_world(world, broken)
    assert world.read(ops_queries.health_issues) == ()

    first = backup_at(failing, T0)
    second = backup_at(failing, T0 + timedelta(minutes=1), BackupKind.MANUAL)
    issues = world.read(ops_queries.health_issues)
    assert [(issue.kind, issue.subject, issue.text) for issue in issues] == [
        (BACKUP_FAILED, first, f"Backup failed: daily {first}"),
        (BACKUP_FAILED, second, f"Backup failed: manual {second}"),
    ]
    assert all(issue.detail is not None and "a@b.example" not in issue.detail for issue in issues)
    assert issues[0].detail is not None and issues[0].detail.startswith("Error: OSError: disk full")

    backup_at(world, T0 + timedelta(minutes=2))  # a later backup verifies: the list clears
    assert world.read(ops_queries.health_issues) == ()

    third = backup_at(failing, T0 + timedelta(minutes=3))
    assert [issue.subject for issue in world.read(ops_queries.health_issues)] == [third]


def test_the_health_report_shows_the_last_backup(world: World) -> None:
    health = EngineHealth(
        world.clock,
        world.queue._job_types,  # noqa: SLF001
        issues=main.build_health_issues(budget=_no_budget()),
        readiness=lambda: READINESS,
    )
    assert world.read(health.report).backup is None

    backup_id = world.back_up()

    report = world.read(health.report)
    assert report.backup is not None and report.backup.id == backup_id
    assert report.backup.status == "verified"


def _no_budget() -> Any:
    class NoBudget:
        def health_issues(self) -> tuple[HealthIssue, ...]:
            return ()

    return NoBudget()


def test_the_backup_job_type_is_registered_as_specified() -> None:
    job_types = main.build_job_types(alerts=lambda: None, backups=lambda: None)  # type: ignore[arg-type,return-value]

    found = job_types.get("backup_db")

    assert (
        found.default_lane,
        found.lanes,
        found.priority,
        found.max_runtime,
        found.retry_limit,
        str(found.group),
    ) == (Lane.BULK, frozenset({Lane.BULK}), 80, timedelta(minutes=30), 2, "backup")


def test_build_local_backups_copies_the_data_dirs_database_into_backup_dir_local(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(world.db_file.parent))
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", str(tmp_path / "the backups"))
    config = load_bootstrap()
    world.query("CREATE TABLE planted (id INTEGER PRIMARY KEY)")

    local = main.build_local_backups(config, world.clock)

    assert local.local_dir == config.backup_dir / "local" == tmp_path / "the backups" / "local"
    bound = local._snapshot  # noqa: SLF001 - the binding under test
    assert isinstance(bound, functools.partial)
    assert (bound.func, bound.args) == (snapshot, (config.data_dir / "nextron.db",))
    assert config.data_dir / "nextron.db" == world.db_file
    taken = local.take(BackupKind.DAILY)
    assert taken.verified and taken.path.parent == config.backup_dir / "local"
    assert taken.path.name == "nextron-test-20261117T090000.000Z.db"
    copy = sqlite3.connect(f"{taken.path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        assert copy.execute("SELECT count(*) FROM planted").fetchone() == (0,)
    finally:
        copy.close()
