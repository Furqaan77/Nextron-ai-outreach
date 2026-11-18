"""Backups (the ``ops`` service, Story 1.16) on real units of work over a temporary database at
head, with a temporary backup dir and the real snapshot code: the Daily, Manual, Not verified,
Record fails, Rotation and Schedule rows of the story's matrix, a rotation that fails after
the row is recorded, ``record_pre_migrate``, the table's checks, ``build_local_backups``'s
binding, the lock (two threads never copy at once), ``health_issues`` and the health report's
last backup.

Off-PC copies (Story 1.17), with the real ``FolderBackupTarget`` on a temporary folder
(``ScryptParams(10, 8, 1)``) or ``InMemoryBackupTarget`` for scripted failures: the Copied,
Ledger, Not configured (each reason), Copy fails (unreachable, refused, a hash mismatch, a
fake refusal, an unparsable setting, a failed ledger) and Rotation rows of the matrix, the
``pre_migrate`` copy staying local, a failed off-PC record (logged, the job ``done``),
rotation's own failures, ``health_issues``, raw-SQL violations of the three new checks (and
0007's after 0008), the outcome written once, ``build_offpc_backups``'s binding and the
planted-secret check.
Nothing waits longer than 0.05 s per step."""

import dataclasses
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
from nextron.adapters.backup_target import FolderBackupTarget
from nextron.adapters.crypto import ScryptParams
from nextron.app.ops.api import Backups, EngineHealth, LocalBackups, OffpcBackups, TakenBackup
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
    offpc_names,
)
from nextron.domain.health import HealthIssue
from nextron.domain.job import InvalidJob, Job, JobStatus, Lane
from nextron.domain.readiness import CheckResult, Status
from nextron.domain.settings_schema import Key
from nextron.jobs import handlers
from nextron.ports.backup_target import BackupTargets
from nextron.ports.errors import AdapterError
from tests.fakes.alert import InMemoryAlert
from tests.fakes.backup_target import InMemoryBackupTarget
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
OPS_LOGGER = "nextron.app.ops.api"
SNAPSHOT_LOGGER = "nextron.db.repos.ops.snapshot"
HEAD = head_revision()
READINESS = (CheckResult("python_version", Status.OK, "fine"),)
LOW_SCRYPT = ScryptParams(10, 8, 1)


def no_restore_tests() -> Any:
    """``build_job_types``'s ``restore_tests`` where no ``restore_test`` job runs."""
    raise AssertionError("no restore_test job runs in these tests")


def no_recovery() -> Any:
    """``build_job_types``'s ``recovery`` where no ``check_recovery`` job runs."""
    raise AssertionError("no check_recovery job runs in these tests")


def no_contact_markets() -> Any:
    """``build_job_types``'s ``contact_markets`` where no ``resolve_contact_markets`` job runs."""
    raise AssertionError("no resolve_contact_markets job runs in these tests")


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
    targets: BackupTargets | None = None
    secrets: InMemorySecrets = field(default_factory=InMemorySecrets)
    notify: Notify = field(init=False)
    queue: JobQueue = field(init=False)
    local: LocalBackups = field(init=False)
    offpc: OffpcBackups = field(init=False)
    backups: Backups = field(init=False)

    def __post_init__(self) -> None:
        job_types = main.build_job_types(
            alerts=lambda: self.notify,
            backups=lambda: self.backups,
            restore_tests=no_restore_tests,
            recovery=no_recovery,
            contact_markets=no_contact_markets,
            crawls=lambda: None,  # type: ignore[arg-type,return-value]
            discovery=lambda: None,  # type: ignore[arg-type,return-value]
            research=lambda: None,  # type: ignore[arg-type,return-value]
            extractions=lambda: None,  # type: ignore[arg-type,return-value]
            primary=lambda: None,  # type: ignore[arg-type,return-value]
            test_runs=lambda: None,  # type: ignore[arg-type,return-value]
            job_boards=lambda: None,  # type: ignore[arg-type,return-value]
        )
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
        self.offpc = OffpcBackups(
            self.clock,
            env="test",
            data_dir=self.db_file.parent,
            backup_dir=self.backup_dir,
            targets=self.targets if self.targets is not None else self.folder_target,
        )
        self.backups = Backups(
            self.clock, self.local, notify=self.notify, queue=self.queue, offpc=self.offpc
        )

    def folder_target(self, folder: Path) -> FolderBackupTarget:
        """The real folder adapter on ``folder``, with cheap scrypt parameters."""
        return FolderBackupTarget(folder, secrets=self.secrets, params=LOW_SCRYPT)

    @property
    def backup_dir(self) -> Path:
        return self.tmp_path / "backups"

    @property
    def offpc_dir(self) -> Path:
        return self.tmp_path / "offpc"

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    @property
    def local_dir(self) -> Path:
        return self.backup_dir / "local"

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

    def clear_setting(self, key: Key) -> None:
        """Back to the default, as a blank field on the Settings page does."""
        with self.units.open():
            Setup(self.clock).save({str(key): ""})

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


def make_world(
    world: World,
    snap: Callable[[Path], Snapshot] | None = None,
    *,
    targets: BackupTargets | None = None,
) -> World:
    return World(
        world.units,
        world.engine,
        world.clock,
        world.tmp_path,
        snap if snap is not None else world.snap,
        targets=targets if targets is not None else world.targets,
        secrets=world.secrets,
    )


def suppression_row(n: int) -> str:
    """An INSERT of the ``n``-th valid ``active`` Suppression List entry (Story 3.3's table)."""
    return (
        "INSERT INTO suppression_entries (id, kind, value_hash, status, source, created_at) "
        f"VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}', 'address', '{n:064x}', 'active', "
        "'operator', '2026-11-18T09:00:00.000000Z')"
    )


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
    # both counted tables exist at head (Stories 2.3 and 3.3), empty here
    assert (row.suppression_count, row.provenance_count) == (0, 0)
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
    world.query(suppression_row(1))
    world.query(suppression_row(2))
    # both counted tables exist at head (Stories 2.3 and 3.3); provenance_records is empty

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

    assert made == Snapshot(target, target.stat().st_size, sha256(target), HEAD, 0, 0)


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
    backups = Backups(
        ticking,  # type: ignore[arg-type]
        local,
        notify=world.notify,
        queue=world.queue,
        offpc=world.offpc,
    )
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
    job_types = main.build_job_types(
        alerts=lambda: None,  # type: ignore[arg-type,return-value]
        backups=lambda: None,  # type: ignore[arg-type,return-value]
        restore_tests=no_restore_tests,
        recovery=no_recovery,
        contact_markets=no_contact_markets,
        crawls=lambda: None,  # type: ignore[arg-type,return-value]
        discovery=lambda: None,  # type: ignore[arg-type,return-value]
        research=lambda: None,  # type: ignore[arg-type,return-value]
        extractions=lambda: None,  # type: ignore[arg-type,return-value]
        primary=lambda: None,  # type: ignore[arg-type,return-value]
        test_runs=lambda: None,  # type: ignore[arg-type,return-value]
        job_boards=lambda: None,  # type: ignore[arg-type,return-value]
    )

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


# Off-PC copies (Story 1.17) ----------------------------------------------------------------------

PASSPHRASE = "QZXPLANTEDBACKUPPASSPHRASE horse battery"
PLANTED_KEY = "QZXPLANTEDHUNTERAPIKEY"
OFFPC_NAME = "nextron-test-20261117T090000.000Z.db.enc"
LEDGER_NAME = "nextron-test-20261117T090000.000Z.ledger.jsonl.enc"
OFFPC_COLUMNS = (
    "offpc_status, offpc_name, offpc_ledger, offpc_at, offpc_deleted_at, offpc_error"
)
T0_TEXT = "2026-11-17T09:00:00.000000Z"


def configure(world: World, folder: Path | None = None, *, passphrase: bool = True) -> Path:
    """Set the off-PC folder (``<tmp>\\offpc`` unless given) and the Backup passphrase."""
    chosen = folder if folder is not None else world.offpc_dir
    world.set_setting(Key.BACKUP_OFFPC_DIR, str(chosen))
    if passphrase:
        world.secrets.write("backup/passphrase", PASSPHRASE)
    return chosen


def offpc_of(world: World, backup_id: str) -> tuple[Any, ...]:
    [row] = world.query(f"SELECT {OFFPC_COLUMNS} FROM backups WHERE id = ?", (backup_id,))
    return tuple(row)


def offpc_files(world: World) -> list[str]:
    folder = world.offpc_dir
    return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


def decrypted(world: World, name: str, folder: Path | None = None) -> tuple[str, bytes]:
    """The SHA-256 and the bytes the real adapter restores from ``name``."""
    target = world.tmp_path / f"restored-{name}"
    fetched = FolderBackupTarget(
        folder if folder is not None else world.offpc_dir, secrets=world.secrets
    ).get(name, target)
    data = target.read_bytes()
    assert fetched.plain_sha256 == hashlib.sha256(data).hexdigest()
    return fetched.plain_sha256, data


def run_backup_job(world: World) -> None:
    """Run one ``backup_db`` tick through the queue and its handler, then finish the job."""
    with world.units.open():
        world.queue.enqueue_tick("backup_db")
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None
    handler = world.queue._job_types.get("backup_db").handler  # noqa: SLF001
    assert handler is not None
    with world.units.open():
        assert handler(claim.job) is None  # the use case returns: the off-PC step never raises
    with world.units.open():
        assert world.queue.complete(claim)


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in records(caplog)]


# Row: Copied


def test_a_verified_backup_is_encrypted_and_copied_off_the_pc(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    configure(world)

    backup_id = world.back_up(BackupKind.DAILY)

    assert offpc_files(world) == [OFFPC_NAME]  # no part left, no ledger to copy
    assert offpc_of(world, backup_id) == ("copied", OFFPC_NAME, "absent", T0_TEXT, None, None)
    row = world.read(ops_queries.last_backup)
    assert (row.status, row.offpc_status, row.offpc_at) == ("verified", "copied", T0)
    digest, data = decrypted(world, OFFPC_NAME)
    assert digest == row.sha256 == sha256(Path(row.path))
    assert data == Path(row.path).read_bytes()
    sealed = (world.offpc_dir / OFFPC_NAME).read_bytes()
    assert b"SQLite format 3" not in sealed and data[:100] not in sealed
    [copied] = [r for r in records(caplog) if r.getMessage() == "offpc_backup_copied"]
    assert copied.levelno == logging.INFO
    assert (copied.backup_id, copied.file, copied.ledger) == (backup_id, OFFPC_NAME, "absent")  # type: ignore[attr-defined]
    assert messages(caplog).index("backup_completed") < messages(caplog).index(
        "offpc_backup_copied"
    )
    assert world.query("SELECT count(*) FROM alerts") == [(0,)]
    assert world.read(ops_queries.health_issues) == ()


def test_a_manual_backup_goes_off_the_pc_too(world: World) -> None:
    configure(world)

    backup_id = world.back_up(BackupKind.MANUAL)

    assert offpc_of(world, backup_id)[:3] == ("copied", OFFPC_NAME, "absent")


# Row: Ledger


def test_the_erasure_ledger_goes_off_the_pc_beside_the_copy(world: World) -> None:
    configure(world)
    ledger = world.backup_dir / "erasure-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b'{"erased_at": "2026-11-16T10:00:00Z", "hash": "ab12"}\n')

    backup_id = world.back_up()

    assert offpc_files(world) == [OFFPC_NAME, LEDGER_NAME]
    assert offpc_of(world, backup_id)[:3] == ("copied", OFFPC_NAME, "copied")
    assert decrypted(world, LEDGER_NAME)[1] == ledger.read_bytes()
    assert b"erased_at" not in (world.offpc_dir / LEDGER_NAME).read_bytes()


# Row: Not configured


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("blank", "folder_not_set"),
        ("data dir", "folder_in_data_dir"),
        ("inside the data dir", "folder_in_data_dir"),
        ("backup dir", "folder_in_backup_dir"),
        ("inside the backup dir", "folder_in_backup_dir"),
        ("no passphrase", "passphrase_not_set"),
    ],
)
def test_an_off_pc_copy_that_is_not_configured_is_recorded_without_an_alert(
    world: World, caplog: pytest.LogCaptureFixture, case: str, reason: str
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    folders = {
        "data dir": world.db_file.parent,
        "inside the data dir": world.db_file.parent / "offpc",
        "backup dir": world.backup_dir,
        "inside the backup dir": world.backup_dir / "offpc",
        "no passphrase": world.offpc_dir,
    }
    if case == "blank":
        world.secrets.write("backup/passphrase", PASSPHRASE)
    else:
        configure(world, folders[case], passphrase=case != "no passphrase")

    backup_id = world.back_up()

    assert world.read(ops_queries.last_backup).status == "verified"  # the local backup is fine
    assert offpc_of(world, backup_id) == ("not_configured", None, None, T0_TEXT, None, None)
    assert [path for path in world.tmp_path.rglob("*.enc*")] == []  # nothing sent anywhere
    assert not world.offpc_dir.exists()
    assert world.query("SELECT count(*) FROM alerts") == [(0,)]
    assert world.query("SELECT count(*) FROM notifications") == [(0,)]
    [skipped] = [r for r in records(caplog) if r.getMessage() == "offpc_backup_skipped"]
    assert skipped.levelno == logging.INFO
    assert (skipped.backup_id, skipped.reason) == (backup_id, reason)  # type: ignore[attr-defined]
    assert world.read(ops_queries.health_issues) == ()


# Row: Copy fails


def assert_offpc_failed(
    world: World, backup_id: str, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    status, name, ledger, at, deleted, error = offpc_of(world, backup_id)
    assert (status, name, ledger, at, deleted) == ("failed", None, None, T0_TEXT, None)
    assert error.startswith(f"{code}: ")
    text = f"Off-PC backup copy failed: daily {backup_id}"
    assert world.query("SELECT kind, subject, channel, text, status FROM alerts") == [
        (BACKUP_FAILED, backup_id, "telegram", text, "pending")
    ]
    assert world.query("SELECT kind, subject, text FROM notifications") == [
        (BACKUP_FAILED, backup_id, text)
    ]
    [failed] = [r for r in records(caplog) if r.getMessage() == "offpc_backup_failed"]
    assert failed.levelno == logging.ERROR
    assert (failed.backup_id, failed.code) == (backup_id, code)  # type: ignore[attr-defined]
    assert world.read(ops_queries.health_issues) == (
        HealthIssue(BACKUP_FAILED, backup_id, text, detail=f"Error: {error}"),
    )
    assert world.read(ops_queries.last_backup).status == "verified"
    assert world.query("SELECT status FROM jobs WHERE type = 'backup_db'") == [("done",)]
    assert [path for path in world.tmp_path.rglob("*.part")] == []
    assert str(world.tmp_path) not in caplog.text  # file names and codes only


def test_an_unreachable_folder_fails_the_off_pc_copy_and_alerts(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    configure(world, world.tmp_path / "unplugged" / "backups")

    run_backup_job(world)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "backup_target_unavailable", caplog)
    assert not (world.tmp_path / "unplugged").exists()  # no tree is grown


def test_a_refused_name_fails_the_off_pc_copy_and_leaves_the_file_alone(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    folder = configure(world)
    folder.mkdir()
    (folder / OFFPC_NAME).write_bytes(b"someone else's file")

    run_backup_job(world)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "backup_target_exists", caplog)
    assert (folder / OFFPC_NAME).read_bytes() == b"someone else's file"


def test_a_hash_mismatch_deletes_the_off_pc_copy_and_fails(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    configure(world)

    def misreported(target: Path) -> Snapshot:
        made = snapshot(world.db_file, target)
        return dataclasses.replace(made, sha256="f" * 64)  # not the bytes the copy holds

    mismatched = make_world(world, misreported)

    run_backup_job(mismatched)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "offpc_copy_mismatch", caplog)
    assert offpc_files(world) == []  # the copy was deleted


def test_a_passphrase_that_cannot_be_read_fails_the_off_pc_copy_and_alerts(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    configure(world)  # the real FolderBackupTarget; only credential_not_set is not_configured
    world.secrets.failing.add("read")

    run_backup_job(world)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "keyring_failed", caplog)
    assert not world.offpc_dir.exists()


def test_a_folder_the_target_refuses_through_the_fake_fails(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    fake = InMemoryBackupTarget()
    fake.failures["put"] = AdapterError(False, "backup_source_unreadable", "unreadable")
    failing = make_world(world, targets=lambda folder: fake)
    configure(failing)

    run_backup_job(failing)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "backup_source_unreadable", caplog)


def test_a_stored_folder_that_no_longer_parses_fails_the_off_pc_copy(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world.secrets.write("backup/passphrase", PASSPHRASE)
    world.query(
        "INSERT INTO settings (id, key, value, updated_at) VALUES (?, ?, ?, ?)",
        ("01JD3QZ8X5V9K2M7N4P6R8T0AB", "backup_offpc_dir", "relative\\folder", T0_TEXT),
    )

    run_backup_job(world)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "invalid_setting", caplog)


def test_a_ledger_copy_that_fails_after_the_database_copy_fails_and_keeps_the_copy(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    fake = InMemoryBackupTarget()
    fake.failures[f"put:{LEDGER_NAME}"] = AdapterError(True, "backup_target_unavailable", "x")
    failing = make_world(world, targets=lambda folder: fake)
    configure(failing)
    ledger = world.backup_dir / "erasure-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"{}\n")

    run_backup_job(failing)

    [(backup_id,)] = world.query("SELECT id FROM backups")
    assert_offpc_failed(world, backup_id, "backup_target_unavailable", caplog)
    assert list(fake.files) == [OFFPC_NAME]  # stays until rotation (decision 9)


# pre_migrate stays local


def test_a_pre_migrate_copy_stays_local(world: World) -> None:
    configure(world)
    taken = world.local.take(BackupKind.PRE_MIGRATE)

    with world.units.open():
        backup_id = world.local.record_pre_migrate(taken)

    assert offpc_of(world, backup_id) == (None,) * 6
    assert not world.offpc_dir.exists()


# The off-PC record fails


@pytest.mark.parametrize("where", ["destination", "set_offpc", "alert"])
def test_a_failed_off_pc_record_is_logged_and_the_backup_still_succeeds(
    world: World,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    def locked(*args: object, **kwargs: object) -> Any:
        raise sqlite3.OperationalError("database is locked")

    if where == "destination":
        configure(world)
        monkeypatch.setattr(OffpcBackups, "destination", locked)
    elif where == "set_offpc":
        configure(world)
        monkeypatch.setattr(backups_repo, "set_offpc", locked)
    else:  # a failed copy whose alert can't be raised
        configure(world, world.tmp_path / "unplugged" / "backups")
        monkeypatch.setattr(world.notify, "alert", locked)

    run_backup_job(world)

    [(backup_id, status)] = world.query("SELECT id, status FROM backups")
    assert status == "verified"
    assert offpc_of(world, backup_id) == (None,) * 6  # rolled back: nothing recorded
    assert world.query("SELECT status FROM jobs WHERE type = 'backup_db'") == [("done",)]
    [failed] = [r for r in records(caplog) if r.getMessage() == "offpc_record_failed"]
    assert failed.levelno == logging.ERROR
    assert (failed.backup_id, failed.code) == (backup_id, "OperationalError")  # type: ignore[attr-defined]
    assert failed.exc_info is not None
    names = messages(caplog)
    assert not {"offpc_backup_copied", "offpc_backup_failed", "offpc_backup_skipped"} & set(names)
    assert "backup_record_failed" not in names
    if where == "destination":
        assert not world.offpc_dir.exists()
    if where == "set_offpc":
        assert offpc_files(world) == [OFFPC_NAME]  # the copy was made; rotation finds it later


# Row: Rotation


def test_off_pc_rotation_deletes_copies_ledgers_and_parts_older_than_35_days(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    configure(world)
    ledger = world.backup_dir / "erasure-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"{}\n")
    old = [backup_at(world, T0 - timedelta(days=days)) for days in (40, 36, 34)]
    names = {backup_id: offpc_of(world, backup_id)[1] for backup_id in old}
    long_ago = T0 - timedelta(days=50)
    planted = [
        offpc_names("test", long_ago)[0] + ".part",  # a stale part
        offpc_names("prod", long_ago)[0],  # another environment's
        "notes.txt",  # not a backup
    ]
    for name in planted:
        (world.offpc_dir / name).write_bytes(b"x")
    assert len(offpc_files(world)) == 9  # nothing was old enough before now
    caplog.clear()

    new = backup_at(world, T0)

    kept_names = [names[old[2]], offpc_names("test", T0 - timedelta(days=34))[1]]
    assert offpc_files(world) == sorted(
        [*kept_names, OFFPC_NAME, LEDGER_NAME, *planted[1:]]
    )
    marks = {backup_id: offpc_of(world, backup_id)[4] for backup_id in [*old, new]}
    assert marks[old[0]] == marks[old[1]] == T0_TEXT
    assert marks[old[2]] is None and marks[new] is None
    [rotated] = [r for r in records(caplog) if r.getMessage() == "offpc_backups_rotated"]
    assert (rotated.levelno, rotated.count, rotated.backup_id) == (logging.INFO, 5, new)  # type: ignore[attr-defined]

    caplog.clear()
    backup_at(world, T0 + timedelta(minutes=1))  # nothing more is old enough
    assert "offpc_backups_rotated" not in messages(caplog)


def test_off_pc_rotation_keeps_the_newest_copy_even_when_it_is_40_days_old(
    world: World,
) -> None:
    configure(world)
    kept = backup_at(world, T0 - timedelta(days=40))
    world.clock.set(T0)

    @use_case
    def rotate() -> int:
        return world.offpc.rotate(world.offpc_dir, kept)

    with world.units.open():
        assert rotate() == 0

    assert len(offpc_files(world)) == 1
    assert offpc_of(world, kept)[4] is None


def test_off_pc_rotation_runs_only_after_a_copied_backup(world: World) -> None:
    configure(world)
    old = backup_at(world, T0 - timedelta(days=40))
    backup_at(world, T0 - timedelta(days=39))
    world.clear_setting(Key.BACKUP_OFFPC_DIR)  # not configured now

    backup_at(world, T0)

    assert len(offpc_files(world)) == 2
    assert offpc_of(world, old)[4] is None


def test_a_failed_off_pc_delete_is_logged_and_the_others_still_go(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    fake = InMemoryBackupTarget()
    faked = make_world(world, targets=lambda folder: fake)
    configure(faked)
    first, second = (backup_at(faked, T0 - timedelta(days=days)) for days in (40, 36))
    first_name, second_name = offpc_of(faked, first)[1], offpc_of(faked, second)[1]
    fake.failures[f"delete:{first_name}"] = AdapterError(True, "backup_target_unavailable", "x")
    faked.clock.set(T0)
    caplog.clear()

    run_backup_job(faked)

    assert sorted(fake.files) == sorted([first_name, OFFPC_NAME])
    assert offpc_of(faked, first)[4] is None  # still there: not marked
    assert offpc_of(faked, second)[4] == T0_TEXT
    [rotated] = [r for r in records(caplog) if r.getMessage() == "offpc_backups_rotated"]
    assert rotated.count == 1  # type: ignore[attr-defined]
    [failed] = [r for r in records(caplog) if r.getMessage() == "offpc_rotation_failed"]
    assert failed.levelno == logging.ERROR
    assert failed.code == "backup_target_unavailable"  # type: ignore[attr-defined]
    [(new_id,)] = world.query("SELECT id FROM backups WHERE started_at = ?", (T0_TEXT,))
    assert failed.backup_id == new_id  # type: ignore[attr-defined]
    assert offpc_of(faked, new_id)[0] == "copied"
    assert world.query("SELECT status FROM jobs WHERE type = 'backup_db'") == [("done",)]


def test_a_folder_that_cannot_be_listed_only_logs_the_rotation_failure(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    fake = InMemoryBackupTarget()
    fake.failures["list"] = AdapterError(True, "backup_target_unavailable", "x")
    faked = make_world(world, targets=lambda folder: fake)
    configure(faked)

    backup_id = faked.back_up()

    assert offpc_of(faked, backup_id)[0] == "copied"
    [failed] = [r for r in records(caplog) if r.getMessage() == "offpc_rotation_failed"]
    assert (failed.backup_id, failed.code) == (backup_id, "backup_target_unavailable")  # type: ignore[attr-defined]


# health_issues


def test_health_issues_list_each_off_pc_failure_since_the_newest_copied_or_skipped_one(
    world: World,
) -> None:
    unplugged = world.tmp_path / "unplugged" / "backups"
    configure(world, unplugged)
    first = backup_at(world, T0)
    second = backup_at(world, T0 + timedelta(minutes=1), BackupKind.MANUAL)

    issues = world.read(ops_queries.health_issues)

    assert [(issue.kind, issue.subject, issue.text) for issue in issues] == [
        (BACKUP_FAILED, first, f"Off-PC backup copy failed: daily {first}"),
        (BACKUP_FAILED, second, f"Off-PC backup copy failed: manual {second}"),
    ]
    assert all(
        issue.detail is not None and issue.detail.startswith("Error: backup_target_unavailable")
        for issue in issues
    )

    world.set_setting(Key.BACKUP_OFFPC_DIR, str(world.offpc_dir))
    backup_at(world, T0 + timedelta(minutes=2))  # copied: the list clears
    assert world.read(ops_queries.health_issues) == ()

    world.set_setting(Key.BACKUP_OFFPC_DIR, str(unplugged))
    third = backup_at(world, T0 + timedelta(minutes=3))
    assert [issue.subject for issue in world.read(ops_queries.health_issues)] == [third]

    world.clear_setting(Key.BACKUP_OFFPC_DIR)
    backup_at(world, T0 + timedelta(minutes=4))  # not configured: the list clears too
    assert world.read(ops_queries.health_issues) == ()


def test_local_failures_come_before_off_pc_failures(world: World) -> None:
    configure(world, world.tmp_path / "unplugged" / "backups")
    offpc_failed = backup_at(world, T0)

    def broken(target: Path) -> Snapshot:
        raise OSError("disk full")

    local_failed = backup_at(make_world(world, broken), T0 + timedelta(minutes=1))

    issues = world.read(ops_queries.health_issues)

    assert [issue.text for issue in issues] == [
        f"Backup failed: daily {local_failed}",
        f"Off-PC backup copy failed: daily {offpc_failed}",
    ]


# The off-PC checks


COPIED_ROW: dict[str, object] = {
    **VERIFIED_ROW,
    "offpc_status": "copied",
    "offpc_name": OFFPC_NAME,
    "offpc_ledger": "absent",
    "offpc_at": T0_TEXT,
    "offpc_deleted_at": None,
    "offpc_error": None,
}
OFFPC_FAILED_ROW: dict[str, object] = {
    **COPIED_ROW,
    "offpc_status": "failed",
    "offpc_name": None,
    "offpc_ledger": None,
    "offpc_error": "backup_target_unavailable: the folder could not be reached",
}
NOT_CONFIGURED_ROW: dict[str, object] = {
    **COPIED_ROW,
    "offpc_status": "not_configured",
    "offpc_name": None,
    "offpc_ledger": None,
}


def test_valid_rows_of_each_off_pc_status_are_accepted(world: World) -> None:
    rows = [
        VERIFIED_ROW,
        COPIED_ROW,
        {**COPIED_ROW, "offpc_ledger": "copied", "offpc_deleted_at": T0_TEXT},
        OFFPC_FAILED_ROW,
        NOT_CONFIGURED_ROW,
        FAILED_ROW,
    ]
    for number, row in enumerate(rows):
        insert_row(world, {**row, "id": f"01JD3QZ8X5V9K2M7N4P6R8T0A{number}"})

    assert world.query("SELECT count(*) FROM backups") == [(len(rows),)]


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        ({**COPIED_ROW, "offpc_status": "sent"}, "ck_backups_offpc_status"),
        ({**COPIED_ROW, "offpc_ledger": "maybe"}, "ck_backups_offpc_ledger"),
        ({**VERIFIED_ROW, "offpc_name": OFFPC_NAME}, "ck_backups_offpc"),
        ({**VERIFIED_ROW, "offpc_ledger": "absent"}, "ck_backups_offpc"),
        ({**VERIFIED_ROW, "offpc_at": T0_TEXT}, "ck_backups_offpc"),
        ({**VERIFIED_ROW, "offpc_deleted_at": T0_TEXT}, "ck_backups_offpc"),
        ({**VERIFIED_ROW, "offpc_error": "x"}, "ck_backups_offpc"),
        ({**COPIED_ROW, "offpc_name": None}, "ck_backups_offpc"),
        ({**COPIED_ROW, "offpc_ledger": None}, "ck_backups_offpc"),
        ({**COPIED_ROW, "offpc_at": None}, "ck_backups_offpc"),
        ({**COPIED_ROW, "offpc_error": "x"}, "ck_backups_offpc"),
        ({**COPIED_ROW, **FAILED_ROW}, "ck_backups_offpc"),  # a failed backup's columns
        ({**OFFPC_FAILED_ROW, "offpc_error": None}, "ck_backups_offpc"),
        ({**OFFPC_FAILED_ROW, "offpc_at": None}, "ck_backups_offpc"),
        ({**OFFPC_FAILED_ROW, "offpc_name": OFFPC_NAME}, "ck_backups_offpc"),
        ({**OFFPC_FAILED_ROW, "offpc_ledger": "absent"}, "ck_backups_offpc"),
        ({**OFFPC_FAILED_ROW, "offpc_deleted_at": T0_TEXT}, "ck_backups_offpc"),
        ({**NOT_CONFIGURED_ROW, "offpc_at": None}, "ck_backups_offpc"),
        ({**NOT_CONFIGURED_ROW, "offpc_name": OFFPC_NAME}, "ck_backups_offpc"),
        ({**NOT_CONFIGURED_ROW, "offpc_ledger": "absent"}, "ck_backups_offpc"),
        ({**NOT_CONFIGURED_ROW, "offpc_error": "x"}, "ck_backups_offpc"),
        ({**NOT_CONFIGURED_ROW, "offpc_deleted_at": T0_TEXT}, "ck_backups_offpc"),
        ({**COPIED_ROW, "sha256": None}, "ck_backups_outcome"),  # 0007's checks still apply
        ({**COPIED_ROW, "size_bytes": -1}, "ck_backups_counts"),
        ({**COPIED_ROW, "kind": "weekly"}, "ck_backups_kind"),
    ],
    ids=[
        "unknown off-PC status",
        "unknown ledger status",
        "no status with a name",
        "no status with a ledger",
        "no status with a time",
        "no status with a deletion",
        "no status with an error",
        "copied without a name",
        "copied without a ledger",
        "copied without a time",
        "copied with an error",
        "copied on a failed backup",
        "failed without an error",
        "failed without a time",
        "failed with a name",
        "failed with a ledger",
        "failed with a deletion",
        "not configured without a time",
        "not configured with a name",
        "not configured with a ledger",
        "not configured with an error",
        "not configured with a deletion",
        "0007 outcome",
        "0007 counts",
        "0007 kind",
    ],
)
def test_the_table_refuses_an_off_pc_outcome_that_breaks_a_check(
    world: World, row: dict[str, object], constraint: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=rf"{constraint}$"):
        insert_row(world, row)

    assert world.query("SELECT count(*) FROM backups") == [(0,)]


def test_an_off_pc_outcome_is_recorded_once(world: World) -> None:
    backup_id = world.back_up()  # not configured: recorded
    later = datetime(2026, 11, 18, tzinfo=UTC)

    @use_case
    def record_again() -> int:
        with current().transaction():
            return backups_repo.set_offpc(
                backup_id, status="copied", name=OFFPC_NAME, ledger="absent", at=later, error=None
            )

    with world.units.open():
        assert record_again() == 0

    assert offpc_of(world, backup_id)[0] == "not_configured"


# Planted secrets


def test_no_planted_secret_reaches_a_copy_a_row_or_the_log(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    configure(world)
    world.secrets.write("hunter/api_key", PLANTED_KEY)
    ledger = world.backup_dir / "erasure-ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"{}\n")
    copied = world.back_up()
    world.set_setting(Key.BACKUP_OFFPC_DIR, str(world.tmp_path / "unplugged" / "x"))
    failed = backup_at(world, T0 + timedelta(minutes=1))
    assert (offpc_of(world, copied)[0], offpc_of(world, failed)[0]) == ("copied", "failed")

    files = [path for path in world.tmp_path.rglob("*") if path.is_file()]
    assert any(path.suffix == ".enc" for path in files)
    connection = sqlite3.connect(world.db_file)
    try:
        dump = "\n".join(connection.iterdump())
    finally:
        connection.close()
    logged = caplog.text + "".join(
        str(vars(record)) for record in caplog.records
    )
    for planted in (PASSPHRASE, "QZXPLANTED", PLANTED_KEY):
        for path in files:
            data = path.read_bytes()
            assert planted.encode() not in data and planted.encode("utf-16-le") not in data
        assert planted not in dump
        assert planted not in logged


# Composition


def test_build_offpc_backups_binds_the_environment_its_dirs_and_the_targets(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(world.db_file.parent))
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", str(tmp_path / "the backups"))
    config = load_bootstrap()
    fake = InMemoryBackupTarget()
    asked: list[Path] = []

    def targets(folder: Path) -> InMemoryBackupTarget:
        asked.append(folder)
        return fake

    offpc = main.build_offpc_backups(config, world.clock, targets)
    world.set_setting(Key.BACKUP_OFFPC_DIR, str(world.offpc_dir))

    assert world.read(offpc.destination) == world.offpc_dir
    taken = world.local.take(BackupKind.DAILY)
    outcome = offpc.send(taken, world.offpc_dir)
    assert (outcome.status, outcome.name) == ("copied", OFFPC_NAME)
    assert asked == [world.offpc_dir]
    world.set_setting(Key.BACKUP_OFFPC_DIR, str(tmp_path / "the backups" / "offpc"))
    assert world.read(offpc.destination) == "folder_in_backup_dir"
