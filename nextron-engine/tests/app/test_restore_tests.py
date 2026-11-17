"""The restore test (the ``ops`` service's ``RestoreTests``, Story 1.18) on real units of work
over a temporary database at head, with a temporary backup dir, the real snapshot code and
``inspect_copy``. The real ``FolderBackupTarget`` on a temporary folder (``ScryptParams(10, 8,
1)``) runs the Pass, wrong-passphrase, tampered, truncated, missing-file, offline-folder and
no-passphrase rows; ``InMemoryBackupTarget`` runs the scripted failures and the planted copies
(a copy that isn't a database, fails ``integrity_check``, is at an older or unknown revision, or
holds fewer rows than recorded).

Also: the live database's bytes and revision unchanged and no ``-wal``/``-shm`` beside the copy;
spies proving no ``ensure_current``, ``command.upgrade`` or ``create_engine`` call during
``run``; no work folder or plaintext left behind (nor any file in ``%TEMP%``); leftovers and
failed deletes; the monthly check (not due, due, the schedule); a row that can't be recorded
(retried); raw-SQL violations of the table's checks; ``health_issues`` and the health report;
``request_restore_test``'s absorption; the handler; ``build_restore_tests``'s binding; and no
planted passphrase in a row or the log."""

import hashlib
import json
import logging
import sqlite3
import tempfile
import threading
import time as clock_time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.backup_target import FolderBackupTarget
from nextron.adapters.crypto import ScryptParams
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import (
    Backups,
    EngineHealth,
    LocalBackups,
    OffpcBackups,
    RestoreTests,
)
from nextron.app.queries import ops as ops_queries
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db import engine as engine_module
from nextron.db import migrations
from nextron.db.migrations import current_revision, head_revision, known_revisions
from nextron.db.repos.ops import restore_tests as restore_tests_repo
from nextron.db.repos.ops.snapshot import CopyInspection, Snapshot, inspect_copy, snapshot
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.alert import Channel
from nextron.domain.backup import BACKUP_FAILED, BackupKind, offpc_names
from nextron.domain.health import HealthIssue
from nextron.domain.job import InvalidJob, Job, JobStatus, Lane
from nextron.domain.readiness import CheckResult, Status
from nextron.domain.restore_test import RESTORE_TEST_FAILED, RestoreTestKind, RestoreTestSchedule
from nextron.domain.settings_schema import Key
from nextron.jobs.triggers import DailyRun
from nextron.ports.backup_target import BackupTargets
from nextron.ports.errors import AdapterError
from tests.fakes.alert import InMemoryAlert
from tests.fakes.backup_target import InMemoryBackupTarget
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
T0_TEXT = "2026-11-17T09:00:00.000000Z"
OPS_LOGGER = "nextron.app.ops.api"
HEAD = head_revision()
OLD_REVISION = max(known_revisions() - {HEAD})  # the revision before head (ids zero-padded)
LOW_SCRYPT = ScryptParams(10, 8, 1)
PASSPHRASE = "QZXPLANTEDBACKUPPASSPHRASE horse battery"
OFFPC_NAME = "nextron-test-20261117T090000.000Z.db.enc"
HEADER = 40  # the format v1 header
CHUNK = 65_536 + 16  # one sealed chunk: 64 KiB of plaintext and its tag
READINESS = (CheckResult("python_version", Status.OK, "fine"),)
ALL_PASS = ("pass", "pass", "pass", "pass", "pass")
FETCH_FAILED = ("fail", "skipped", "skipped", "skipped", "skipped")
ROW_COLUMNS = (
    "kind, result, backup_id, check_fetch, check_integrity, check_revision, check_suppression, "
    "check_provenance, reason, copy_revision, suppression_count, provenance_count, detail, "
    "started_at, finished_at"
)


def no_recovery() -> Any:
    """``build_job_types``'s ``recovery`` where no ``check_recovery`` job runs."""
    raise AssertionError("no check_recovery job runs in these tests")


def no_contact_markets() -> Any:
    """``build_job_types``'s ``contact_markets`` where no ``resolve_contact_markets`` job runs."""
    raise AssertionError("no resolve_contact_markets job runs in these tests")


def no_crawls() -> Any:
    """``build_job_types``'s ``crawls``, ``discovery`` and ``research`` where no Story 2.5 job
    runs."""
    raise AssertionError("no enrich_lead, canonicalize_lead or purge_page_text job runs here")


def suppression_row(n: int) -> str:
    """An INSERT of the ``n``-th valid ``active`` Suppression List entry (Story 3.3's table)."""
    return (
        "INSERT INTO suppression_entries (id, kind, value_hash, status, source, created_at) "
        f"VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}', 'address', '{n:064x}', 'active', "
        "'operator', '2026-11-18T09:00:00.000000Z')"
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class World:
    """The ``ops`` backups and restore tests on real units of work and a frozen clock."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    tmp_path: Path
    targets: BackupTargets | None = None
    inspect: Callable[[Path], CopyInspection] = inspect_copy
    secrets: InMemorySecrets = field(default_factory=InMemorySecrets)
    notify: Notify = field(init=False)
    queue: JobQueue = field(init=False)
    offpc: OffpcBackups = field(init=False)
    backups: Backups = field(init=False)
    tests: RestoreTests = field(init=False)

    def __post_init__(self) -> None:
        job_types = main.build_job_types(
            alerts=lambda: self.notify,
            backups=lambda: self.backups,
            restore_tests=lambda: self.tests,
            recovery=no_recovery,
            contact_markets=no_contact_markets,
            crawls=no_crawls,
            discovery=no_crawls,
            research=no_crawls,
            extractions=no_crawls,
        )
        self.queue = JobQueue(self.clock, job_types)
        self.notify = Notify(
            self.clock, self.queue, alert_ports={Channel.TELEGRAM: lambda: InMemoryAlert()}
        )
        local = LocalBackups(
            self.clock, env="test", local_dir=self.backup_dir / "local", snapshot=self.copy
        )
        self.offpc = OffpcBackups(
            self.clock,
            env="test",
            data_dir=self.db_file.parent,
            backup_dir=self.backup_dir,
            targets=self.targets if self.targets is not None else self.folder_target,
        )
        self.backups = Backups(
            self.clock, local, notify=self.notify, queue=self.queue, offpc=self.offpc
        )
        self.tests = RestoreTests(
            self.clock,
            env="test",
            backup_dir=self.backup_dir,
            offpc=self.offpc,
            inspect=self.inspect,
            notify=self.notify,
            queue=self.queue,
        )

    def folder_target(self, folder: Path) -> FolderBackupTarget:
        return FolderBackupTarget(folder, secrets=self.secrets, params=LOW_SCRYPT)

    def copy(self, target: Path) -> Snapshot:
        return snapshot(self.db_file, target)

    @property
    def backup_dir(self) -> Path:
        return self.tmp_path / "backups"

    @property
    def restore_dir(self) -> Path:
        return self.backup_dir / "restore_test"

    @property
    def offpc_dir(self) -> Path:
        return self.tmp_path / "offpc"

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    def back_up(self) -> str:
        with self.units.open():
            return self.backups.back_up(BackupKind.DAILY)

    def run(self, kind: RestoreTestKind = RestoreTestKind.MANUAL) -> str | None:
        with self.units.open():
            return self.tests.run(kind)

    def query(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return connection.execute(statement, params).fetchall()
        finally:
            connection.close()

    def read(self, call: Callable[[], Any]) -> Any:
        with self.units.open(read_only=True):
            return call()

    def set_setting(self, key: Key, value: object) -> None:
        with self.units.open():
            Setup(self.clock).set(key, value)

    def row(self, test_id: str) -> dict[str, Any]:
        [found] = self.query(f"SELECT {ROW_COLUMNS} FROM restore_tests WHERE id = ?", (test_id,))
        return dict(zip([name.strip() for name in ROW_COLUMNS.split(",")], found, strict=True))

    def rows(self) -> int:
        [(count,)] = self.query("SELECT count(*) FROM restore_tests")
        return int(count)


@pytest.fixture
def world(units: UnitOfWorkFactory, db_engine: Engine, tmp_path: Path) -> World:
    return World(units, db_engine, FrozenClock(T0), tmp_path)


def make_world(world: World, **changes: Any) -> World:
    return World(
        world.units,
        world.engine,
        world.clock,
        world.tmp_path,
        changes.get("targets", world.targets),
        changes.get("inspect", world.inspect),
        secrets=world.secrets,
    )


def configure(world: World, *, passphrase: bool = True) -> Path:
    world.set_setting(Key.BACKUP_OFFPC_DIR, str(world.offpc_dir))
    if passphrase:
        world.secrets.write("backup/passphrase", PASSPHRASE)
    return world.offpc_dir


def copied_backup(world: World) -> str:
    """A daily backup copied off the PC; its row's ULID."""
    configure(world)
    backup_id = world.back_up()
    [(status,)] = world.query("SELECT offpc_status FROM backups WHERE id = ?", (backup_id,))
    assert status == "copied"
    return backup_id


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == OPS_LOGGER]


def messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in records(caplog)]


def files_under(folder: Path) -> set[Path]:
    return {path for path in folder.rglob("*") if path.is_file()} if folder.exists() else set()


def assert_no_work_folder(world: World) -> None:
    """The restore folder holds nothing: every work folder and its plaintext copy are gone."""
    if world.restore_dir.is_dir():
        assert list(world.restore_dir.iterdir()) == []


LIVE_FILES = ("nextron.db", "nextron.db-wal")  # the -shm index changes on every read


def live_bytes(world: World) -> dict[str, bytes]:
    folder = world.db_file.parent
    return {name: (folder / name).read_bytes() for name in LIVE_FILES if (folder / name).exists()}


def assert_failed(
    world: World,
    test_id: str | None,
    reason: str,
    checks: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
    *,
    kind: str = "manual",
    detail: str | None = None,
) -> dict[str, Any]:
    """The test failed for ``reason``: its row, its alert on both channels, its log line and
    its panel issue."""
    assert test_id is not None
    row = world.row(test_id)
    assert (row["kind"], row["result"], row["reason"]) == (kind, "fail", reason)
    found = tuple(row[f"check_{name}"] for name in ("fetch", "integrity", "revision"))
    found += (row["check_suppression"], row["check_provenance"])
    assert found == checks
    if detail is not None:
        assert row["detail"] == detail
    text = f"Restore test failed: {kind} {test_id}, {reason}"
    assert world.query(
        "SELECT kind, subject, channel, text FROM alerts WHERE kind = ?", (RESTORE_TEST_FAILED,)
    ) == [(RESTORE_TEST_FAILED, test_id, "telegram", text)]
    assert world.query(
        "SELECT kind, subject, text FROM notifications WHERE kind = ?", (RESTORE_TEST_FAILED,)
    ) == [(RESTORE_TEST_FAILED, test_id, text)]
    [failed] = [r for r in records(caplog) if r.getMessage() == "restore_test_failed"]
    assert failed.levelno == logging.ERROR
    found_fields = (failed.test_id, failed.kind, failed.reason)  # type: ignore[attr-defined]
    assert found_fields == (test_id, kind, reason)
    issues = [i for i in world.read(ops_queries.health_issues) if i.kind == RESTORE_TEST_FAILED]
    assert [(i.subject, i.text) for i in issues] == [(test_id, text)]
    assert_no_work_folder(world)
    assert str(world.tmp_path) not in caplog.text  # file names and codes only, never a path
    return row


# Row: Pass ----------------------------------------------------------------------------------------


@contextmanager
def no_engine_or_migration(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Spies on ``ensure_current``, ``command.upgrade`` and ``create_engine`` (both of them):
    each call is recorded, and none runs."""
    calls: list[str] = []

    def spy(name: str) -> Callable[..., Any]:
        def called(*args: object, **kwargs: object) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} was called")

        return called

    monkeypatch.setattr(migrations, "ensure_current", spy("ensure_current"))
    monkeypatch.setattr(main, "ensure_current", spy("ensure_current"))
    monkeypatch.setattr(command, "upgrade", spy("command.upgrade"))
    monkeypatch.setattr(engine_module, "create_engine", spy("create_engine"))
    monkeypatch.setattr(engine_module, "create_read_only_engine", spy("create_read_only_engine"))
    monkeypatch.setattr("sqlalchemy.create_engine", spy("sqlalchemy.create_engine"))
    yield calls


def test_a_restore_test_of_a_good_copy_passes_and_leaves_nothing_behind(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.query(suppression_row(1))
    world.query(suppression_row(2))
    # both counted tables exist at head (Stories 2.3 and 3.3); provenance_records is empty
    backup_id = copied_backup(world)
    system_temp = world.tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    before = files_under(world.tmp_path)
    live = live_bytes(world)
    inspected: list[Path] = []

    def watched(copy: Path) -> CopyInspection:
        inspected.append(copy)
        # when the copy is read, the live database is exactly as it was
        assert live_bytes(world) == live
        found = inspect_copy(copy)
        assert sorted(p.name for p in copy.parent.iterdir()) == ["copy.db"]  # no -wal, -shm
        return found

    watching = make_world(world, inspect=watched)
    with no_engine_or_migration(monkeypatch) as calls:
        test_id = watching.run(RestoreTestKind.MANUAL)

    assert calls == []
    assert test_id is not None
    row = world.row(test_id)
    assert row == {
        "kind": "manual",
        "result": "pass",
        "backup_id": backup_id,
        "check_fetch": "pass",
        "check_integrity": "pass",
        "check_revision": "pass",
        "check_suppression": "pass",
        "check_provenance": "pass",
        "reason": None,
        "copy_revision": HEAD,
        "suppression_count": 2,
        "provenance_count": 0,
        "detail": None,
        "started_at": T0_TEXT,
        "finished_at": T0_TEXT,
    }
    [copy] = inspected
    assert copy.parent.parent == world.restore_dir
    assert copy.parent.name.startswith("nextron-test-")
    assert_no_work_folder(world)
    assert world.restore_dir.is_dir()
    assert files_under(world.tmp_path) - {world.db_file.with_name("nextron.db-wal")} <= before
    assert list(system_temp.iterdir()) == []  # nothing in %TEMP%
    assert current_revision(world.engine) == HEAD
    [passed] = [r for r in records(caplog) if r.getMessage() == "restore_test_passed"]
    assert passed.levelno == logging.INFO
    found_fields = (passed.test_id, passed.kind, passed.backup_id)  # type: ignore[attr-defined]
    assert found_fields == (test_id, "manual", backup_id)
    assert world.query("SELECT count(*) FROM alerts") == [(0,)]
    assert world.read(ops_queries.health_issues) == ()
    assert world.query("SELECT count(*) FROM events WHERE entity = 'restore_tests'") == [(0,)]
    logged = caplog.text + "".join(str(vars(record)) for record in caplog.records)
    assert PASSPHRASE not in logged and "QZXPLANTED" not in logged
    assert all("QZXPLANTED" not in str(value) for value in row.values())


def test_a_table_recorded_absent_is_skipped_and_the_test_still_passes(world: World) -> None:
    # both counted tables exist at head (Stories 2.3 and 3.3): the live database loses one
    world.query("DROP TABLE suppression_entries")
    backup_id = copied_backup(world)

    test_id = world.run()

    assert test_id is not None
    row = world.row(test_id)
    assert (row["result"], row["backup_id"], row["copy_revision"]) == ("pass", backup_id, HEAD)
    assert (row["check_suppression"], row["check_provenance"]) == ("skipped", "pass")
    assert (row["suppression_count"], row["provenance_count"], row["reason"]) == (None, 0, None)


def test_the_newest_copied_copy_is_tested_and_a_rotated_one_never(world: World) -> None:
    first = copied_backup(world)
    world.clock.advance(timedelta(minutes=1))
    second = world.back_up()
    world.clock.advance(timedelta(minutes=1))
    world.secrets.delete("backup/passphrase")
    world.back_up()  # not_configured: never the copy under test

    test_id = world.run()
    assert test_id is not None and world.row(test_id)["backup_id"] == second

    world.query("UPDATE backups SET offpc_deleted_at = ? WHERE id = ?", (T0_TEXT, second))
    world.secrets.write("backup/passphrase", PASSPHRASE)
    again = world.run()
    assert again is not None and world.row(again)["backup_id"] == first


# Row: No copy -------------------------------------------------------------------------------------


def test_no_copy_off_the_pc_fails_with_no_offpc_copy(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    test_id = world.run(RestoreTestKind.MONTHLY)

    row = assert_failed(world, test_id, "no_offpc_copy", FETCH_FAILED, caplog, kind="monthly")
    assert (row["backup_id"], row["detail"], row["copy_revision"]) == (None, None, None)
    assert not world.restore_dir.exists()  # no work folder was even made


def test_a_backup_that_was_never_copied_off_the_pc_is_no_copy(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world.back_up()  # not_configured: the folder is blank

    assert_failed(world, world.run(), "no_offpc_copy", FETCH_FAILED, caplog)


# Row: Not configured ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("folder", "detail"),
    [
        ("", "folder_not_set"),
        ("data dir", "folder_in_data_dir"),
        ("backup dir", "folder_in_backup_dir"),
    ],
)
def test_a_folder_that_is_not_configured_now_fails_with_offpc_not_configured(
    world: World, caplog: pytest.LogCaptureFixture, folder: str, detail: str
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    backup_id = copied_backup(world)
    chosen = {"": "", "data dir": str(world.db_file.parent), "backup dir": str(world.backup_dir)}
    with world.units.open():
        Setup(world.clock).save({"backup_offpc_dir": chosen[folder]})

    row = assert_failed(
        world, world.run(), "offpc_not_configured", FETCH_FAILED, caplog, detail=detail
    )
    assert row["backup_id"] == backup_id


def test_a_stored_folder_that_no_longer_parses_is_offpc_not_configured(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    copied_backup(world)
    world.query(
        "UPDATE settings SET value = ? WHERE key = 'backup_offpc_dir'", ("relative\\folder",)
    )

    assert_failed(
        world,
        world.run(),
        "offpc_not_configured",
        FETCH_FAILED,
        caplog,
        detail="invalid_setting_value",
    )


# Row: Fetch errors (the real adapter) -------------------------------------------------------------


def damage(world: World, change: Callable[[bytes], bytes]) -> None:
    path = world.offpc_dir / OFFPC_NAME
    path.write_bytes(change(path.read_bytes()))


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing file", "copy_missing"),
        ("folder offline", "target_unavailable"),
        ("no passphrase", "passphrase_not_set"),
        ("wrong passphrase", "wrong_passphrase"),
        ("a changed byte", "tampered"),
        ("cut inside a chunk", "tampered"),
        ("cut at a chunk boundary", "truncated"),
        ("not a backup file", "bad_format"),
    ],
)
def test_each_fetch_error_of_the_real_adapter_is_its_reason(
    world: World, caplog: pytest.LogCaptureFixture, case: str, reason: str
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world.query("CREATE TABLE filler (data BLOB)")
    world.query("INSERT INTO filler VALUES (randomblob(300000))")
    copied_backup(world)
    sealed = (world.offpc_dir / OFFPC_NAME).stat().st_size
    assert sealed > HEADER + 2 * CHUNK  # at least three chunks: each cut lands in the copy
    if case == "missing file":
        (world.offpc_dir / OFFPC_NAME).unlink()
    elif case == "folder offline":
        (world.offpc_dir / OFFPC_NAME).unlink()
        world.offpc_dir.rmdir()  # an unplugged drive reads as a missing folder
    elif case == "no passphrase":
        world.secrets.delete("backup/passphrase")
    elif case == "wrong passphrase":
        world.secrets.write("backup/passphrase", "another passphrase entirely")
    elif case == "a changed byte":
        damage(world, lambda data: data[:500] + bytes([data[500] ^ 1]) + data[501:])
    elif case == "cut inside a chunk":
        damage(world, lambda data: data[: HEADER + CHUNK + 100])
    elif case == "cut at a chunk boundary":
        damage(world, lambda data: data[: HEADER + CHUNK])
    else:
        damage(world, lambda data: b"NOPE" + data[4:])

    row = assert_failed(world, world.run(), reason, FETCH_FAILED, caplog, detail=None)
    assert row["detail"] is None


@pytest.mark.parametrize(
    ("failure", "reason", "detail"),
    [
        (MemoryError(), "fetch_failed", "MemoryError"),
        (ValueError("scrypt refused"), "fetch_failed", "ValueError"),
        (AdapterError(False, "backup_name_invalid", "no"), "fetch_failed", "backup_name_invalid"),
        (AdapterError(False, "backup_target_exists", "no"), "fetch_failed", "backup_target_exists"),
        (AdapterError(True, "backup_target_unavailable", "offline"), "target_unavailable", None),
        (AdapterError(False, "backup_copy_missing", "gone"), "copy_missing", None),
        (AdapterError(False, "credential_not_set", "unset"), "passphrase_not_set", None),
        (AdapterError(False, "backup_wrong_passphrase", "no"), "wrong_passphrase", None),
        (AdapterError(False, "backup_tampered", "changed"), "tampered", None),
        (AdapterError(False, "backup_truncated", "cut"), "truncated", None),
        (AdapterError(False, "backup_bad_format", "bad"), "bad_format", None),
    ],
    ids=[
        "MemoryError",
        "ValueError",
        "another code",
        "target exists",
        "unavailable",
        "missing",
        "no passphrase",
        "wrong passphrase",
        "tampered",
        "truncated",
        "bad format",
    ],
)
def test_a_scripted_fetch_failure_is_its_reason_or_fetch_failed(
    world: World,
    caplog: pytest.LogCaptureFixture,
    failure: BaseException,
    reason: str,
    detail: str | None,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    fake = InMemoryBackupTarget()
    faked = make_world(world, targets=lambda folder: fake)
    copied_backup(faked)
    fake.failures["get"] = failure

    row = assert_failed(faked, faked.run(), reason, FETCH_FAILED, caplog, detail=detail)
    assert row["detail"] == detail
    assert ("get", OFFPC_NAME) in fake.calls


# Row: Checksum ------------------------------------------------------------------------------------


def test_a_fetched_copy_whose_sha256_differs_is_checksum_mismatch(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    fake = InMemoryBackupTarget()
    faked = make_world(world, targets=lambda folder: fake)
    copied_backup(faked)
    fake.files[OFFPC_NAME] = fake.files[OFFPC_NAME] + b"\x00"

    assert_failed(faked, faked.run(), "checksum_mismatch", FETCH_FAILED, caplog)


# Planted copies: integrity, revision and counts ---------------------------------------------------


def plant(
    world: World,
    fake: InMemoryBackupTarget,
    data: bytes,
    *,
    suppression: int | None = None,
    provenance: int | None = None,
) -> str:
    """A ``copied`` backup row whose off-PC copy is ``data`` (in ``fake``), with the recorded
    counts; its ULID."""
    name = offpc_names("test", T0)[0]
    fake.files[name] = data
    backup_id = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
    world.query(
        "INSERT INTO backups (id, kind, status, started_at, finished_at, path, size_bytes, "
        "sha256, db_revision, suppression_count, provenance_count, offpc_status, offpc_name, "
        "offpc_ledger, offpc_at) VALUES (?, 'daily', 'verified', ?, ?, 'C:/b/local/x.db', ?, ?, "
        "?, ?, ?, 'copied', ?, 'absent', ?)",
        (
            backup_id,
            T0_TEXT,
            T0_TEXT,
            len(data),
            sha256(data),
            HEAD,
            suppression,
            provenance,
            name,
            T0_TEXT,
        ),
    )
    return backup_id


def changed_copy(world: World, *statements: str) -> bytes:
    """A real copy of the live database, then changed with ``statements``."""
    target = world.tmp_path / "planted" / "copy.db"
    snapshot(world.db_file, target)
    connection = sqlite3.connect(target, isolation_level=None)
    try:
        for statement in statements:
            connection.execute(statement)
    finally:
        connection.close()  # the last connection checkpoints the WAL into the file
    data = target.read_bytes()
    target.unlink()
    return data


@pytest.fixture
def planted(world: World) -> tuple[World, InMemoryBackupTarget]:
    world.secrets.write("backup/passphrase", PASSPHRASE)
    world.set_setting(Key.BACKUP_OFFPC_DIR, str(world.offpc_dir))
    fake = InMemoryBackupTarget()
    return make_world(world, targets=lambda folder: fake), fake


def test_a_copy_that_is_not_a_database_fails_its_integrity_check(
    planted: tuple[World, InMemoryBackupTarget], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world, fake = planted
    plant(world, fake, b"this is not a database at all\n" * 200)

    row = assert_failed(
        world,
        world.run(),
        "integrity_failed",
        ("pass", "fail", "skipped", "skipped", "skipped"),
        caplog,
        detail="integrity_check: error: DatabaseError",
    )
    assert (row["copy_revision"], row["suppression_count"], row["provenance_count"]) == (
        None,
        None,
        None,
    )


def test_a_copy_that_fails_integrity_check_fails_with_its_first_line(
    planted: tuple[World, InMemoryBackupTarget], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world, fake = planted
    data = changed_copy(
        world,
        "CREATE TABLE marked (value TEXT)",
        "CREATE INDEX ix_marked_value ON marked (value)",
        "INSERT INTO marked VALUES ('QZXMARKERVALUE')",
    )
    assert data.count(b"QZXMARKERVALUE") == 2  # the row and its index entry
    at = data.rindex(b"QZXMARKERVALUE")  # change one of them: the index no longer matches
    plant(world, fake, data[:at] + b"QZXMARKERVALUF" + data[at + 14 :])

    row = assert_failed(
        world,
        world.run(),
        "integrity_failed",
        ("pass", "fail", "skipped", "skipped", "skipped"),
        caplog,
    )
    assert row["detail"] in (
        "integrity_check: row 1 missing from index ix_marked_value",
        "integrity_check: wrong # of entries in index ix_marked_value",
    )


@pytest.mark.parametrize(
    ("revision", "reason"),
    [(OLD_REVISION, "revision_behind"), ("0099_from_newer_code", "revision_unknown")],
)
def test_a_copy_at_another_revision_fails_its_revision_check(
    planted: tuple[World, InMemoryBackupTarget],
    caplog: pytest.LogCaptureFixture,
    revision: str,
    reason: str,
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world, fake = planted
    changed = changed_copy(world, f"UPDATE alembic_version SET version_num = '{revision}'")
    plant(world, fake, changed)

    row = assert_failed(
        world,
        world.run(),
        reason,
        ("pass", "pass", "fail", "skipped", "skipped"),
        caplog,
        detail=f"copy revision {revision}; code head {HEAD}",
    )
    assert row["copy_revision"] == revision


def test_a_copy_with_no_readable_revision_is_revision_unknown(
    planted: tuple[World, InMemoryBackupTarget], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world, fake = planted
    plant(world, fake, changed_copy(world, "DELETE FROM alembic_version"))

    assert_failed(
        world,
        world.run(),
        "revision_unknown",
        ("pass", "pass", "fail", "skipped", "skipped"),
        caplog,
        detail=f"copy revision none; code head {HEAD}",
    )


def test_a_copy_with_fewer_rows_than_recorded_fails_both_counts_with_the_first_reason(
    planted: tuple[World, InMemoryBackupTarget], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world, fake = planted
    data = changed_copy(
        world,
        suppression_row(1),
        # both counted tables exist at head (Stories 2.3 and 3.3); provenance_records is empty
    )
    plant(world, fake, data, suppression=5, provenance=3)

    row = assert_failed(
        world,
        world.run(),
        "suppression_fewer",
        ("pass", "pass", "pass", "fail", "fail"),
        caplog,
        detail=(
            "suppression_entries: copy 1, backup 5; provenance_records: copy 0, backup 3"
        ),
    )
    assert (row["copy_revision"], row["suppression_count"], row["provenance_count"]) == (
        HEAD,
        1,
        0,
    )


def test_a_copy_missing_a_recorded_table_fails_that_count(
    planted: tuple[World, InMemoryBackupTarget], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    world, fake = planted
    # provenance_records exists at head (Story 2.3): this copy lost it
    plant(world, fake, changed_copy(world, "DROP TABLE provenance_records"), provenance=3)

    assert_failed(
        world,
        world.run(),
        "provenance_fewer",
        ("pass", "pass", "pass", "skipped", "fail"),
        caplog,
        detail="provenance_records: copy absent, backup 3",
    )


def test_an_inspect_that_raises_is_an_integrity_failure(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    def broken(copy: Path) -> CopyInspection:
        raise RuntimeError("the inspection broke")

    broken_world = make_world(world, inspect=broken)
    copied_backup(broken_world)

    assert_failed(
        broken_world,
        broken_world.run(),
        "integrity_failed",
        ("pass", "fail", "skipped", "skipped", "skipped"),
        caplog,
        detail="integrity_check: error: RuntimeError",
    )


# Row: Monthly -------------------------------------------------------------------------------------


def insert_test(world: World, kind: str, started_at: str, result: str = "pass") -> None:
    test_id = f"01JD3QZ8X5V9K2M7N4P6R8T{world.rows():03d}"
    checks = ALL_PASS if result == "pass" else FETCH_FAILED
    world.query(
        "INSERT INTO restore_tests (id, kind, result, started_at, finished_at, backup_id, "
        "check_fetch, check_integrity, check_revision, check_suppression, check_provenance, "
        "reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            test_id,
            kind,
            result,
            started_at,
            started_at,
            "01JD3QZ8X5V9K2M7N4P6R8T0AB" if result == "pass" else None,
            *checks,
            None if result == "pass" else "no_offpc_copy",
        ),
    )


def test_a_monthly_tick_when_a_monthly_test_ran_this_month_does_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    insert_test(world, "monthly", "2026-11-01T11:00:00.000000Z", result="fail")
    copied_backup(world)

    assert world.run(RestoreTestKind.MONTHLY) is None

    assert world.rows() == 1
    [not_due] = [r for r in records(caplog) if r.getMessage() == "restore_test_not_due"]
    assert not_due.levelno == logging.INFO
    assert not_due.kind == "monthly"  # type: ignore[attr-defined]
    assert not world.restore_dir.exists()
    assert world.query("SELECT count(*) FROM alerts") == [(0,)]


def test_a_monthly_tick_runs_when_the_last_monthly_test_was_last_month(world: World) -> None:
    insert_test(world, "monthly", "2026-10-31T23:00:00.000000Z")
    insert_test(world, "manual", "2026-11-16T11:00:00.000000Z")  # manual tests don't count
    copied_backup(world)

    test_id = world.run(RestoreTestKind.MONTHLY)

    assert test_id is not None
    assert (world.row(test_id)["kind"], world.row(test_id)["result"]) == ("monthly", "pass")
    assert world.run(RestoreTestKind.MONTHLY) is None  # the next tick this month
    assert world.run(RestoreTestKind.MANUAL) is not None  # a manual test always runs


def test_the_month_is_read_in_the_senders_timezone(world: World) -> None:
    world.set_setting(Key.SENDER_TIMEZONE, "Asia/Tokyo")
    insert_test(world, "monthly", "2026-10-31T23:30:00.000000Z")  # 1 November in Tokyo
    copied_backup(world)

    assert world.run(RestoreTestKind.MONTHLY) is None


# Row: Schedule ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("newest", "missed"),
    [
        (None, False),  # a new database waits for its first slot
        ("2026-10-20T11:00:00.000000Z", True),  # 16 November's 11:00 slot passed this month
        ("2026-11-02T11:00:00.000000Z", False),  # tested this month already
    ],
)
def test_the_schedule_is_an_hour_after_the_backup_and_catches_up_a_missed_month(
    world: World, newest: str | None, missed: bool
) -> None:
    if newest is not None:
        insert_test(world, "monthly", newest)

    assert world.read(world.tests.schedule) == RestoreTestSchedule(time(11, 0), "UTC", missed)


def test_the_schedule_follows_the_backup_time_and_the_senders_timezone(world: World) -> None:
    world.set_setting(Key.BACKUP_TIME, time(23, 30))
    world.set_setting(Key.SENDER_TIMEZONE, "America/New_York")

    assert world.read(world.tests.schedule) == RestoreTestSchedule(
        time(0, 30), "America/New_York", False
    )


def test_read_restore_test_schedule_is_the_daily_run_of_the_schedule(world: World) -> None:
    insert_test(world, "monthly", "2026-10-20T11:00:00.000000Z")

    found = main.read_restore_test_schedule(world.units, world.tests)

    assert found == DailyRun(time(11, 0), "UTC", catch_up=True)


# Row: Leftovers -----------------------------------------------------------------------------------


def test_leftover_work_folders_of_this_environment_are_deleted_first(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    copied_backup(world)
    leftover = world.restore_dir / "nextron-test-crashed"
    leftover.mkdir(parents=True)
    (leftover / "copy.db").write_bytes(b"plaintext from a crash")
    other_env = world.restore_dir / "nextron-dev-abc"
    other_env.mkdir()
    (other_env / "copy.db").write_bytes(b"another environment's")
    unrelated = world.restore_dir / "notes.txt"
    unrelated.write_bytes(b"keep me")

    test_id = world.run()

    assert test_id is not None and world.row(test_id)["result"] == "pass"
    assert not leftover.exists()
    assert sorted(path.name for path in world.restore_dir.iterdir()) == [
        "nextron-dev-abc",
        "notes.txt",
    ]
    assert (other_env / "copy.db").read_bytes() == b"another environment's"
    assert "restore_test_cleanup_failed" not in messages(caplog)


def test_a_failed_delete_is_logged_and_the_result_stands(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    def leaves_a_folder(copy: Path) -> CopyInspection:
        (copy.parent / "a folder").mkdir()  # files are deleted, never a subfolder
        return inspect_copy(copy)

    leaving = make_world(world, inspect=leaves_a_folder)
    copied_backup(leaving)

    test_id = leaving.run()

    assert test_id is not None and leaving.row(test_id)["result"] == "pass"
    [cleanup] = [r for r in records(caplog) if r.getMessage() == "restore_test_cleanup_failed"]
    assert cleanup.levelno == logging.WARNING
    code = cleanup.code  # type: ignore[attr-defined]
    assert code in ("PermissionError", "IsADirectoryError", "OSError")
    [work] = list(leaving.restore_dir.iterdir())
    assert [path.name for path in work.iterdir()] == ["a folder"]  # the plaintext copy is gone

    caplog.clear()
    (work / "a folder").rmdir()
    assert world.run() is not None  # the next test deletes the leftover
    assert_no_work_folder(world)


def test_a_backup_dir_that_cannot_hold_the_work_folder_is_fetch_failed(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    copied_backup(world)
    world.restore_dir.write_bytes(b"a file where the restore folder should be")

    assert_failed(
        world, world.run(), "fetch_failed", FETCH_FAILED, caplog, detail="FileExistsError"
    )
    assert world.restore_dir.read_bytes() == b"a file where the restore folder should be"


def test_a_leftover_that_is_a_junction_is_never_followed(world: World) -> None:
    winapi = pytest.importorskip("_winapi")
    outside = world.tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_bytes(b"not the engine's to delete")
    world.restore_dir.mkdir(parents=True)
    linked = world.restore_dir / "nextron-test-linked"
    try:
        winapi.CreateJunction(str(outside), str(linked))
    except (AttributeError, OSError) as exc:
        pytest.skip(f"a junction can't be made here ({type(exc).__name__})")
    try:
        copied_backup(world)

        test_id = world.run()

        assert test_id is not None and world.row(test_id)["result"] == "pass"
        assert marker.read_bytes() == b"not the engine's to delete"
        assert linked.is_junction()
    finally:
        linked.rmdir()  # removes the junction itself, never its target


@pytest.mark.parametrize("case", ["a monthly tick that isn't due", "no copy"])
def test_every_run_clears_leftover_work_folders_first(world: World, case: str) -> None:
    leftover = world.restore_dir / "nextron-test-crashed"
    leftover.mkdir(parents=True)
    (leftover / "copy.db").write_bytes(b"plaintext from a crash")
    if case == "no copy":
        test_id = world.run()
        assert test_id is not None and world.row(test_id)["reason"] == "no_offpc_copy"
    else:
        insert_test(world, "monthly", "2026-11-01T11:00:00.000000Z")
        assert world.run(RestoreTestKind.MONTHLY) is None
        assert world.rows() == 1

    assert not leftover.exists()
    assert list(world.restore_dir.iterdir()) == []


# The lock -----------------------------------------------------------------------------------------


def test_two_threads_never_test_at_once(world: World, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    inside = 0
    most = 0
    guard = threading.Lock()

    def watched(copy: Path) -> CopyInspection:
        nonlocal inside, most
        with guard:
            inside += 1
            most = max(most, inside)
        clock_time.sleep(0.05)
        try:
            return inspect_copy(copy)
        finally:
            with guard:
                inside -= 1

    watching = make_world(world, inspect=watched)
    copied_backup(watching)
    made: list[str | None] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            made.append(watching.run(RestoreTestKind.MANUAL))
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert most == 1
    assert len(made) == 2 and None not in made
    assert [result for (result,) in world.query("SELECT result FROM restore_tests")] == [
        "pass",
        "pass",
    ]
    assert "restore_test_cleanup_failed" not in messages(caplog)


# Row: Record fails --------------------------------------------------------------------------------


def broken_insert(**kwargs: object) -> str:
    raise RuntimeError("the database is locked")


def test_a_row_that_cannot_be_recorded_raises_a_retryable_error(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)
    copied_backup(world)
    monkeypatch.setattr(restore_tests_repo, "insert", broken_insert)

    with pytest.raises(AdapterError) as raised:
        world.run()

    assert (raised.value.retryable, raised.value.code) == (True, "restore_test_not_recorded")
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert world.rows() == 0
    [failed] = [r for r in records(caplog) if r.getMessage() == "restore_test_record_failed"]
    assert failed.levelno == logging.ERROR
    assert (failed.kind, failed.code) == ("manual", "RuntimeError")  # type: ignore[attr-defined]
    assert_no_work_folder(world)


def test_a_failed_alert_rolls_the_failed_row_back_and_raises(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_alert(*args: object) -> None:
        raise RuntimeError("notify broke")

    monkeypatch.setattr(world.notify, "alert", broken_alert)

    with pytest.raises(AdapterError) as raised:
        world.run()  # no copy: a failed test, whose alert breaks

    assert raised.value.retryable
    assert world.rows() == 0


def test_a_restore_test_job_whose_row_cannot_be_recorded_is_retried(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied_backup(world)
    with world.units.open():
        world.tests.request_restore_test()
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None and claim.job.type == "restore_test"
    handler = world.queue._job_types.get("restore_test").handler  # noqa: SLF001
    assert handler is not None
    monkeypatch.setattr(restore_tests_repo, "insert", broken_insert)

    with world.units.open(), pytest.raises(AdapterError) as raised:
        handler(claim.job)
    with world.units.open():
        status = world.queue.fail(claim, raised.value)

    assert status is JobStatus.QUEUED  # a retry, not dead


# The table's checks -------------------------------------------------------------------------------

PASS_ROW: dict[str, object] = {
    "id": "01JD3QZ8X5V9K2M7N4P6R8T0AB",
    "kind": "monthly",
    "result": "pass",
    "started_at": T0_TEXT,
    "finished_at": T0_TEXT,
    "backup_id": "01JD3QZ8X5V9K2M7N4P6R8T0AC",
    "check_fetch": "pass",
    "check_integrity": "pass",
    "check_revision": "pass",
    "check_suppression": "skipped",
    "check_provenance": "pass",
    "reason": None,
    "copy_revision": HEAD,
    "suppression_count": None,
    "provenance_count": 4,
    "detail": None,
}
FAIL_ROW: dict[str, object] = {
    **PASS_ROW,
    "id": "01JD3QZ8X5V9K2M7N4P6R8T0AD",
    "result": "fail",
    "backup_id": None,
    "check_fetch": "fail",
    "check_integrity": "skipped",
    "check_revision": "skipped",
    "check_suppression": "skipped",
    "check_provenance": "skipped",
    "reason": "no_offpc_copy",
    "copy_revision": None,
    "provenance_count": None,
}


def insert_row(world: World, row: dict[str, object]) -> None:
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    world.query(f"INSERT INTO restore_tests ({columns}) VALUES ({marks})", tuple(row.values()))


def test_valid_pass_and_fail_rows_are_accepted(world: World) -> None:
    insert_row(world, PASS_ROW)
    insert_row(world, FAIL_ROW)
    insert_row(
        world,
        {
            **FAIL_ROW,
            "id": "01JD3QZ8X5V9K2M7N4P6R8T0AE",
            "kind": "manual",
            "backup_id": "01JD3QZ8X5V9K2M7N4P6R8T0AC",
            "check_fetch": "pass",
            "check_integrity": "pass",
            "check_revision": "fail",
            "check_suppression": "fail",
            "check_provenance": "pass",
            "reason": "revision_behind",
            "detail": f"copy revision {OLD_REVISION}; code head {HEAD}",
        },
    )

    assert world.rows() == 3


@pytest.mark.parametrize(
    ("row", "constraint"),
    [
        ({**PASS_ROW, "reason": "tampered"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "detail": "a detail"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "backup_id": None}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "check_fetch": "skipped"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "check_integrity": "fail"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "check_revision": "skipped"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "check_suppression": "fail"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "check_provenance": "fail"}, "ck_restore_tests_outcome"),
        ({**FAIL_ROW, "reason": None}, "ck_restore_tests_outcome"),
        ({**FAIL_ROW, "check_fetch": "pass"}, "ck_restore_tests_outcome"),
        ({**PASS_ROW, "kind": "weekly"}, "ck_restore_tests_kind"),
        ({**PASS_ROW, "result": "maybe"}, "ck_restore_tests_result"),
        ({**FAIL_ROW, "check_fetch": "failed"}, "ck_restore_tests_check_fetch"),
        ({**PASS_ROW, "check_integrity": "ok"}, "ck_restore_tests_check_integrity"),
        ({**PASS_ROW, "check_revision": "PASS"}, "ck_restore_tests_check_revision"),
        ({**FAIL_ROW, "check_suppression": "n/a"}, "ck_restore_tests_check_suppression"),
        ({**FAIL_ROW, "check_provenance": ""}, "ck_restore_tests_check_provenance"),
        ({**PASS_ROW, "suppression_count": -1}, "ck_restore_tests_counts"),
        ({**PASS_ROW, "provenance_count": -1}, "ck_restore_tests_counts"),
    ],
    ids=[
        "pass with a reason",
        "pass with a detail",
        "pass without a backup",
        "pass with fetch skipped",
        "pass with integrity failed",
        "pass with revision skipped",
        "pass with suppression failed",
        "pass with provenance failed",
        "fail without a reason",
        "fail with no failing check",
        "unknown kind",
        "unknown result",
        "unknown fetch outcome",
        "unknown integrity outcome",
        "unknown revision outcome",
        "unknown suppression outcome",
        "unknown provenance outcome",
        "negative suppression count",
        "negative provenance count",
    ],
)
def test_the_table_refuses_a_row_that_breaks_a_check(
    world: World, row: dict[str, object], constraint: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=rf"{constraint}$"):
        insert_row(world, row)

    assert world.rows() == 0


# Health -------------------------------------------------------------------------------------------


def test_health_issues_list_each_failed_test_since_the_newest_passed_one(world: World) -> None:
    assert world.read(ops_queries.health_issues) == ()
    first = world.run(RestoreTestKind.MONTHLY)  # no copy
    world.clock.advance(timedelta(minutes=1))
    second = world.run(RestoreTestKind.MANUAL)
    assert first is not None and second is not None

    issues = world.read(ops_queries.health_issues)
    assert issues == (
        HealthIssue(
            RESTORE_TEST_FAILED,
            first,
            f"Restore test failed: monthly {first}, no_offpc_copy",
            detail="Error: no_offpc_copy",
        ),
        HealthIssue(
            RESTORE_TEST_FAILED,
            second,
            f"Restore test failed: manual {second}, no_offpc_copy",
            detail="Error: no_offpc_copy",
        ),
    )

    world.clock.advance(timedelta(minutes=1))
    copied_backup(world)
    assert world.run() is not None  # a later test passes: the list clears
    assert world.read(ops_queries.health_issues) == ()

    world.clock.advance(timedelta(minutes=1))
    with world.units.open():
        Setup(world.clock).save({"backup_offpc_dir": ""})  # off-PC switched off since
    third = world.run()
    [issue] = world.read(ops_queries.health_issues)
    assert (issue.subject, issue.detail) == (third, "Error: folder_not_set")


def test_a_failed_test_comes_after_the_backup_failures_and_its_detail_is_redacted(
    world: World,
) -> None:
    insert_row(world, {**FAIL_ROW, "detail": "a@b.example broke it"})
    world.query(
        "INSERT INTO backups (id, kind, status, started_at, finished_at, path, error) VALUES "
        "('01JD3QZ8X5V9K2M7N4P6R8T0AF', 'daily', 'failed', ?, ?, 'C:/x.db', 'OSError: full')",
        (T0_TEXT, T0_TEXT),
    )

    issues = world.read(ops_queries.health_issues)

    assert [issue.kind for issue in issues] == [BACKUP_FAILED, RESTORE_TEST_FAILED]
    assert issues[1].detail is not None and "a@b.example" not in issues[1].detail


def test_the_health_report_shows_the_last_restore_test(world: World) -> None:
    health = EngineHealth(
        world.clock,
        world.queue._job_types,  # noqa: SLF001
        issues=main.build_health_issues(budget=_no_budget()),
        readiness=lambda: READINESS,
    )
    assert world.read(health.report).restore_test is None

    test_id = world.run()

    report = world.read(health.report)
    assert report.restore_test is not None
    assert (report.restore_test.id, report.restore_test.result) == (test_id, "fail")
    assert [issue.subject for issue in report.issues] == [test_id]


def _no_budget() -> Any:
    class NoBudget:
        def health_issues(self) -> tuple[HealthIssue, ...]:
            return ()

    return NoBudget()


# Run restore test now -----------------------------------------------------------------------------


def test_request_restore_test_queues_one_manual_job_and_absorbs_a_click_while_it_is_live(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=OPS_LOGGER)

    with world.units.open():
        first = world.tests.request_restore_test()
    with world.units.open():
        second = world.tests.request_restore_test()

    assert first.created and not second.created
    assert second.job_id == first.job_id
    [(job_type, lane, key, payload, status)] = world.query(
        "SELECT type, lane, idempotency_key, payload, status FROM jobs"
    )
    assert (job_type, lane, key, status) == (
        "restore_test",
        "bulk",
        "restore_test:global:2026-11-17",
        "queued",
    )
    assert json.loads(payload) == {"v": 1, "kind": "manual"}
    assert world.rows() == 0  # the request tests nothing; the job does
    requested = [r for r in records(caplog) if r.getMessage() == "restore_test_requested"]
    assert [(r.job_id, r.job_created) for r in requested] == [  # type: ignore[attr-defined]
        (first.job_id, True),
        (first.job_id, False),
    ]


MANUAL_PAYLOAD = {"v": 1, "kind": "manual"}


def test_the_manual_job_runs_a_manual_test_and_a_tick_the_monthly_check(world: World) -> None:
    copied_backup(world)
    handler = world.queue._job_types.get("restore_test").handler  # noqa: SLF001
    assert handler is not None

    manual = Job("01JD3QZ8X5V9K2M7N4P6R8T0AB", "restore_test", Lane.BULK, MANUAL_PAYLOAD, 1)
    tick = Job("01JD3QZ8X5V9K2M7N4P6R8T0AC", "restore_test", Lane.BULK, {"v": 1}, 1)

    with world.units.open():
        assert handler(manual) is None  # type: ignore[func-returns-value]
    with world.units.open():
        assert handler(tick) is None  # type: ignore[func-returns-value]

    assert world.query("SELECT kind, result FROM restore_tests ORDER BY kind") == [
        ("manual", "pass"),
        ("monthly", "pass"),
    ]


@pytest.mark.parametrize("kind", ["weekly", "", 1, None])
def test_the_handler_refuses_any_other_kind(world: World, kind: object) -> None:
    handler = world.queue._job_types.get("restore_test").handler  # noqa: SLF001
    assert handler is not None
    job = Job("01JD3QZ8X5V9K2M7N4P6R8T0AB", "restore_test", Lane.BULK, {"v": 1, "kind": kind}, 1)

    with world.units.open(), pytest.raises(InvalidJob):
        handler(job)

    assert world.rows() == 0


@pytest.mark.parametrize("kind", ["weekly", "daily"])
def test_run_takes_a_restore_test_kind_only(world: World, kind: str) -> None:
    with world.units.open(), pytest.raises(ValueError):
        world.tests.run(kind)  # type: ignore[arg-type]


# Composition --------------------------------------------------------------------------------------


def test_build_restore_tests_binds_the_environment_the_backup_dir_and_inspect_copy(
    world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEXTRON_BACKUP_DIR", str(tmp_path / "the backups"))
    config = load_bootstrap()

    built = main.build_restore_tests(config, world.clock, world.offpc, world.notify, world.queue)

    assert built.restore_dir == tmp_path / "the backups" / "restore_test"
    assert built._inspect is inspect_copy  # noqa: SLF001 - the binding under test
    assert built._offpc is world.offpc  # noqa: SLF001
    assert built._env == "test"  # noqa: SLF001
    assert not (tmp_path / "the backups").exists()  # building reaches no folder
