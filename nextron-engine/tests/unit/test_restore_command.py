"""``nextron restore`` (``nextron.main.restore`` and the CLI parser, Story 1.19) against every
restore row of the I/O matrix, on temporary folders as ``NEXTRON_ENV=test``.

The copies are real: a database at head (or an older known revision) made with the real
migrations, then copied with the real ``snapshot`` (SQLite's backup API); the encrypted one goes
through the real ``FolderBackupTarget`` (``ScryptParams(10, 8, 1)``, ``InMemorySecrets``). The
stale ``-wal`` is a real crash-like state: the database file and its ``-wal`` taken while a
connection with ``wal_autocheckpoint=0`` is still open. "Nothing was changed" compares the live
database's bytes; every run also checks that the given file and its folder are unchanged, that
no work folder is left, that the lock is released, and the log records (no full path in
them)."""

import io
import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command

from nextron import __main__ as cli
from nextron import main
from nextron.adapters.backup_target import FolderBackupTarget
from nextron.adapters.crypto import ScryptParams
from nextron.app.ops.api import Recovery
from nextron.bootstrap import BootstrapConfig, load_bootstrap
from nextron.db.engine import create_engine, fk_off_connection
from nextron.db.migrations import alembic_config, ensure_current, head_revision, known_revisions
from nextron.db.models.base import new_id, utc_text
from nextron.db.repos.ops.snapshot import snapshot
from nextron.db.uow import Subscribers, UnitOfWorkFactory
from nextron.domain.backup import copy_name, offpc_names
from nextron.lock import InstanceLock
from tests.fakes.clock import FrozenClock
from tests.fakes.probes import fake_probes
from tests.fakes.secrets import InMemorySecrets

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
STAMP_AT = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
COPY = copy_name("test", STAMP_AT)  # nextron-test-20261116T100000.000Z.db
PRE_RESTORE = copy_name("test", NOW)  # nextron-test-20261117T090000.000Z.db
HEAD = head_revision()
OLD_REVISION = max(known_revisions() - {HEAD})  # the revision before head (ids zero-padded)
LOW_SCRYPT = ScryptParams(10, 8, 1)
PASSPHRASE = "QZXPLANTEDRESTOREPASSPHRASE horse battery"
STEPS = (
    "reapply_erasure_ledger",
    "mark_oms_unknown",
    "reingest_mailboxes",
    "repoll_unsubscribes",
)
NOTHING_CHANGED = "nothing was changed"


def query(path: Path, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        return connection.execute(statement, params).fetchall()


def make_database(
    path: Path,
    *,
    marker: str,
    revision: str | None = None,
    events: Iterable[datetime] = (),
) -> None:
    """A database file at head (or ``revision``) holding a settings row ``restore_marker`` =
    ``marker`` and one event at each time; closed cleanly, so it is one file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(path)
    try:
        if revision is None:
            ensure_current(engine)
        else:
            config = alembic_config()
            with fk_off_connection(engine) as connection, connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, revision)
    finally:
        engine.dispose()
    query(
        path,
        "INSERT INTO settings (id, key, value, updated_at) VALUES (?, 'restore_marker', ?, ?)",
        (new_id(), marker, utc_text(NOW)),
    )
    for at in events:
        query(
            path,
            "INSERT INTO events (id, entity, entity_id, from_status, to_status, cause, "
            "cause_ref, actor, at) VALUES (?, 'jobs', ?, NULL, 'queued', 'test', NULL, "
            "'system', ?)",
            (new_id(), new_id(), utc_text(at)),
        )


def marker(path: Path) -> str:
    [(value,)] = query(path, "SELECT value FROM settings WHERE key = 'restore_marker'")
    return str(value)


def revision_of(path: Path) -> str:
    [(value,)] = query(path, "SELECT version_num FROM alembic_version")
    return str(value)


def recovery_rows(path: Path) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute("SELECT * FROM recovery_state ORDER BY restored_at")
        ]


def everything_in(folder: Path) -> list[str]:
    """Every file and folder under ``folder``, relative to it, sorted."""
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))


def files_in(folder: Path) -> dict[str, bytes]:
    """Every file directly in ``folder`` and its bytes."""
    if not folder.is_dir():
        return {}
    return {entry.name: entry.read_bytes() for entry in folder.iterdir() if entry.is_file()}


@dataclass
class Result:
    code: int
    out: str
    err: str


@dataclass
class Place:
    """This test's environment: the bootstrap config's folders, and the copies it made."""

    config: BootstrapConfig
    tmp_path: Path
    sources: int = 0

    @property
    def db_path(self) -> Path:
        return self.config.data_dir / "nextron.db"

    @property
    def work_dir(self) -> Path:
        return self.config.data_dir / "restore"

    @property
    def pre_restore_dir(self) -> Path:
        return self.config.backup_dir / "pre_restore"

    @property
    def copies(self) -> Path:
        return self.tmp_path / "copies"

    def live(self, marker: str = "live") -> bytes:
        """A live database at head; its bytes."""
        make_database(self.db_path, marker=marker)
        return self.db_path.read_bytes()

    def copy(
        self,
        *,
        name: str = COPY,
        marker: str = "copy",
        revision: str | None = None,
        events: Iterable[datetime] = (),
        statements: Iterable[str] = (),
        prepare: Callable[[Path], None] | None = None,
    ) -> Path:
        """A real backup copy in ``copies``: a database copied with ``snapshot``, changed with
        ``statements`` afterwards."""
        self.sources += 1
        source = self.tmp_path / f"source-{self.sources}" / "nextron.db"
        make_database(source, marker=marker, revision=revision, events=events)
        if prepare is not None:
            prepare(source)
        target = self.copies / name
        snapshot(source, target)
        for statement in statements:
            query(target, statement)
        return target

    def log(self) -> list[dict[str, Any]]:
        log_file = self.config.data_dir / "logs" / "nextron.log"
        if not log_file.exists():
            return []
        return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]

    def events(self, name: str) -> list[dict[str, Any]]:
        return [line for line in self.log() if line["event"] == name]

    def assert_lock_released(self) -> None:
        lock = InstanceLock(self.config.data_dir / "nextron.lock")
        lock.acquire()
        lock.release()

    def assert_no_work_folder(self) -> None:
        assert not self.work_dir.exists() or list(self.work_dir.iterdir()) == []


@pytest.fixture
def place(tmp_path: Path) -> Place:
    return Place(load_bootstrap(), tmp_path)


def run_restore(
    file: Path | str,
    *,
    yes: bool = True,
    secrets: InMemorySecrets | None = None,
    passphrase: str | None = None,
    ask: Callable[[str], str] | None = None,
    clock: FrozenClock | None = None,
) -> Result:
    out, err = io.StringIO(), io.StringIO()
    if secrets is None:
        secrets = InMemorySecrets("test")
        if passphrase is not None:
            secrets.write("backup/passphrase", passphrase)

    def refuse_to_ask(prompt: str) -> str:
        raise AssertionError("nothing should be asked")

    code = main.restore(
        file,
        yes=yes,
        targets=lambda folder: FolderBackupTarget(folder, secrets=secrets, params=LOW_SCRYPT),
        clock=clock if clock is not None else FrozenClock(NOW),
        probes=fake_probes(),
        ask=ask if ask is not None else refuse_to_ask,
        out=out,
        err=err,
    )
    return Result(code, out.getvalue(), err.getvalue())


class Given:
    """The given file and its folder, so a test can assert both are unchanged afterwards."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.before = files_in(file.parent)

    def assert_unchanged(self) -> None:
        assert files_in(self.file.parent) == self.before


def assert_refused(place: Place, result: Result, live: bytes | None, reason: str) -> None:
    """Exit 1, one line ending ``nothing was changed``, ``restore_refused`` with ``reason``,
    and the live database (or its absence) as it was."""
    assert result.code == 1
    assert result.err.count("\n") == 1 and result.err.rstrip("\n").endswith(NOTHING_CHANGED)
    assert result.out == ""
    if live is None:
        assert not place.db_path.exists()
    else:
        assert place.db_path.read_bytes() == live
    [refused] = place.events("restore_refused")
    assert (refused["level"], refused["reason"]) == ("WARNING", reason)
    assert place.events("database_restored") == []
    assert files_in(place.pre_restore_dir) == {}
    place.assert_no_work_folder()
    place.assert_lock_released()


def assert_no_path_in_the_log(place: Place) -> None:
    text = (place.config.data_dir / "logs" / "nextron.log").read_text(encoding="utf-8")
    for folder in (place.tmp_path, place.config.data_dir, place.config.backup_dir):
        assert str(folder) not in text
        assert json.dumps(str(folder))[1:-1] not in text  # as JSON escapes it


# Row: Plain restore -----------------------------------------------------------------------------


def test_a_plain_restore_saves_the_current_database_and_swaps_the_copy_in(
    place: Place,
) -> None:
    old = place.live("live")
    copy = place.copy(marker="copy", events=[STAMP_AT - timedelta(minutes=5)])
    given = Given(copy)
    copy_bytes = copy.read_bytes()

    result = run_restore(copy)

    assert result.code == 0, result.err
    assert result.err == ""
    assert f"Restored {COPY} into {place.db_path}; recovery mode is on" in result.out
    saved = place.pre_restore_dir / PRE_RESTORE
    assert f"The database from before the restore is at {saved}." in result.out
    # the safety copy is the database from before the restore
    assert list(files_in(place.pre_restore_dir)) == [PRE_RESTORE]
    assert marker(saved) == "live"
    assert query(saved, "PRAGMA integrity_check") == [("ok",)]
    # the live database is now the copy, at head, with one on row and four pending steps
    assert marker(place.db_path) == "copy"
    assert revision_of(place.db_path) == HEAD
    [row] = recovery_rows(place.db_path)
    assert (row["mode"], row["source_name"], row["pre_restore_name"], row["ended_at"]) == (
        "on",
        COPY,
        PRE_RESTORE,
        None,
    )
    assert [row[step] for step in STEPS] == ["pending"] * 4
    assert row["restored_at"] == utc_text(NOW)
    assert row["backup_at"] == utc_text(STAMP_AT - timedelta(minutes=5))  # the newest event
    assert row["backup_at"] <= utc_text(STAMP_AT)
    # the given file and its folder are unchanged; nothing is left in the work folder
    given.assert_unchanged()
    assert copy.read_bytes() == copy_bytes
    place.assert_no_work_folder()
    place.assert_lock_released()
    assert old != place.db_path.read_bytes()
    # the log
    [taken] = place.events("pre_restore_backup_taken")
    assert taken["file"] == PRE_RESTORE
    [restored] = place.events("database_restored")
    assert (restored["level"], restored["file"], restored["recovery_id"]) == (
        "INFO",
        COPY,
        row["id"],
    )
    assert (restored["from_revision"], restored["to_revision"], restored["pre_restore"]) == (
        HEAD,
        HEAD,
        PRE_RESTORE,
    )
    assert place.events("restore_candidate_migrated") == []
    assert place.events("restore_refused") == place.events("restore_failed") == []
    assert_no_path_in_the_log(place)


def test_without_events_the_backup_time_is_the_names_stamp(place: Place) -> None:
    place.live()
    copy = place.copy()

    assert run_restore(copy).code == 0

    [row] = recovery_rows(place.db_path)
    assert row["backup_at"] == utc_text(STAMP_AT)


def test_a_renamed_copy_with_no_events_is_backed_up_at_the_restore(place: Place) -> None:
    place.live()
    copy = place.copy(name="last-good.db")

    assert run_restore(copy).code == 0

    [row] = recovery_rows(place.db_path)
    assert (row["backup_at"], row["source_name"]) == (utc_text(NOW), "last-good.db")


def test_a_leftover_work_folder_is_cleared_before_the_restore(place: Place) -> None:
    place.live()
    copy = place.copy()
    leftover = place.work_dir / "nextron-test-crashed"
    leftover.mkdir(parents=True)
    (leftover / "candidate.db").write_bytes(b"plaintext from a crash")
    other_env = place.work_dir / "nextron-dev-keep"
    other_env.mkdir()

    assert run_restore(copy).code == 0

    assert sorted(entry.name for entry in place.work_dir.iterdir()) == ["nextron-dev-keep"]


# Row: Encrypted ---------------------------------------------------------------------------------


def encrypted_copy(place: Place, *, passphrase: str = PASSPHRASE) -> Path:
    """A ``.db.enc`` copy made by the real ``FolderBackupTarget.put``."""
    plain = place.copy(marker="encrypted")
    secrets = InMemorySecrets("test")
    secrets.write("backup/passphrase", passphrase)
    folder = place.tmp_path / "offpc"
    folder.mkdir()
    FolderBackupTarget(folder, secrets=secrets, params=LOW_SCRYPT).put(plain, COPY + ".enc")
    return folder / (COPY + ".enc")


def test_an_encrypted_copy_is_decrypted_and_restored(place: Place) -> None:
    place.live()
    copy = encrypted_copy(place)
    given = Given(copy)

    result = run_restore(copy, passphrase=PASSPHRASE)

    assert result.code == 0, result.err
    assert marker(place.db_path) == "encrypted"
    [row] = recovery_rows(place.db_path)
    assert (row["source_name"], row["backup_at"]) == (COPY + ".enc", utc_text(STAMP_AT))
    given.assert_unchanged()
    place.assert_no_work_folder()
    assert PASSPHRASE not in (place.config.data_dir / "logs" / "nextron.log").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("passphrase", "tamper", "reason"),
    [
        ("another passphrase entirely", False, "backup_wrong_passphrase"),
        (None, False, "credential_not_set"),
        (PASSPHRASE, True, "backup_tampered"),
    ],
    ids=["wrong passphrase", "no passphrase", "tampered"],
)
def test_an_encrypted_copy_that_cannot_be_decrypted_is_refused(
    place: Place, passphrase: str | None, tamper: bool, reason: str
) -> None:
    live = place.live()
    copy = encrypted_copy(place)
    if tamper:
        data = bytearray(copy.read_bytes())
        data[len(data) // 2] ^= 0x01
        copy.write_bytes(bytes(data))
    given = Given(copy)

    result = run_restore(copy, passphrase=passphrase)

    assert_refused(place, result, live, reason)
    assert f"({reason})" in result.err
    given.assert_unchanged()


# Row: Behind head -------------------------------------------------------------------------------


def test_a_copy_behind_head_is_upgraded_then_swapped_in(place: Place) -> None:
    place.live()
    copy = place.copy(revision=OLD_REVISION, marker="older")
    given = Given(copy)

    result = run_restore(copy)

    assert result.code == 0, result.err
    assert (marker(place.db_path), revision_of(place.db_path)) == ("older", HEAD)
    assert revision_of(copy) == OLD_REVISION  # the given file was never upgraded
    given.assert_unchanged()
    [migrated] = place.events("restore_candidate_migrated")
    assert (migrated["from_revision"], migrated["to_revision"]) == (OLD_REVISION, HEAD)
    [restored] = place.events("database_restored")
    assert (restored["from_revision"], restored["to_revision"]) == (OLD_REVISION, HEAD)


def test_a_failed_upgrade_changes_nothing(
    place: Place, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = place.live()
    copy = place.copy(revision=OLD_REVISION)

    def fails(engine: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the upgrade failed")

    monkeypatch.setattr(main, "ensure_current", fails)

    result = run_restore(copy)

    assert result.code == 1
    assert result.err == (
        "restore failed: the copy could not be upgraded (RuntimeError); nothing was changed\n"
    )
    assert place.db_path.read_bytes() == live
    assert files_in(place.pre_restore_dir) == {}  # the safety copy was discarded again
    [failed] = place.events("restore_failed")
    assert (failed["level"], failed["stage"], failed["code"]) == (
        "ERROR",
        "migrate",
        "RuntimeError",
    )
    place.assert_no_work_folder()
    place.assert_lock_released()


def test_a_failed_recovery_row_changes_nothing(
    place: Place, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = place.live()
    copy = place.copy()
    given = Given(copy)

    class Broken(Recovery):
        def begin(self, **kwargs: Any) -> Any:  # type: ignore[override]
            raise RuntimeError("the recovery row could not be written")

    monkeypatch.setattr(main, "Recovery", Broken)

    result = run_restore(copy)

    assert result.code == 1
    assert result.err == (
        "restore failed: recovery mode could not be turned on (RuntimeError); "
        "nothing was changed\n"
    )
    assert place.db_path.read_bytes() == live
    assert files_in(place.pre_restore_dir) == {}  # the safety copy was discarded again
    [failed] = place.events("restore_failed")
    assert (failed["stage"], failed["code"]) == ("recovery", "RuntimeError")
    given.assert_unchanged()
    place.assert_no_work_folder()
    place.assert_lock_released()


# Row: Refused -----------------------------------------------------------------------------------


def corrupt(source: Path) -> None:
    """Add an indexed table with a marker value, to corrupt in the copy afterwards."""
    query(source, "CREATE TABLE corrupt_me (value TEXT)")
    query(source, "CREATE INDEX ix_corrupt_me ON corrupt_me (value)")
    query(source, "INSERT INTO corrupt_me VALUES ('QZXMARKERVALUE')")


def test_a_file_that_is_not_a_database_is_refused(place: Place) -> None:
    live = place.live()
    copy = place.copies / COPY
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(b"this is not an SQLite database at all\n" * 200)
    given = Given(copy)

    result = run_restore(copy)

    assert result.code == 1
    assert result.err == (
        "restore failed: the copy could not be read (DatabaseError); nothing was changed\n"
    )
    assert result.out == ""
    assert place.db_path.read_bytes() == live
    [failed] = place.events("restore_failed")
    assert (failed["level"], failed["stage"], failed["code"]) == (
        "ERROR",
        "candidate",
        "DatabaseError",
    )
    assert files_in(place.pre_restore_dir) == {}
    given.assert_unchanged()
    place.assert_no_work_folder()
    place.assert_lock_released()


def test_a_copy_that_fails_its_integrity_check_is_refused(place: Place) -> None:
    live = place.live()
    copy = place.copy(prepare=corrupt)
    data = copy.read_bytes()
    at = data.rindex(b"QZXMARKERVALUE")
    copy.write_bytes(data[:at] + b"QZXMARKERVALUF" + data[at + 14 :])
    given = Given(copy)

    result = run_restore(copy)

    assert_refused(place, result, live, "integrity_failed")
    assert "the copy failed its integrity check (" in result.err
    given.assert_unchanged()


@pytest.mark.parametrize(
    ("statement", "reason", "says"),
    [
        (
            "UPDATE alembic_version SET version_num = '0099_from_newer_code'",
            "revision_unknown",
            "the copy's revision 0099_from_newer_code is unknown to this code",
        ),
        ("DELETE FROM alembic_version", "no_revision", "the copy has no Alembic revision"),
    ],
    ids=["ahead", "no revision"],
)
def test_a_copy_whose_revision_is_unknown_or_missing_is_refused(
    place: Place, statement: str, reason: str, says: str
) -> None:
    live = place.live()
    copy = place.copy(statements=[statement])
    given = Given(copy)

    result = run_restore(copy)

    assert_refused(place, result, live, reason)
    assert says in result.err
    given.assert_unchanged()


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        (copy_name("prod", STAMP_AT), "other_environment"),
        (copy_name("dev", STAMP_AT) + ".enc", "other_environment"),
        ("nextron-PROD-20261116T100000.000Z.DB", "other_environment"),
        ("backup.sqlite", "not_a_copy"),
        (COPY + ".enc.part", "not_a_copy"),
        ("nextron-test-20261116T100000.000Z.ledger.jsonl.enc", "not_a_copy"),
    ],
)
def test_a_file_named_for_another_environment_or_not_a_copy_is_refused(
    place: Place, name: str, reason: str
) -> None:
    live = place.live()
    copy = place.copy(name=name)
    given = Given(copy)

    result = run_restore(copy)

    assert_refused(place, result, live, reason)
    given.assert_unchanged()


def test_a_plain_copy_with_a_wal_beside_it_is_a_live_database_and_refused(place: Place) -> None:
    live = place.live()
    copy = place.copy()
    (copy.parent / (COPY + "-wal")).write_bytes(b"\x00" * 32)
    given = Given(copy)

    result = run_restore(copy)

    assert_refused(place, result, live, "live_database")
    assert f"{COPY}-wal" in result.err
    given.assert_unchanged()


def test_an_empty_wal_beside_a_plain_copy_is_allowed(place: Place) -> None:
    place.live()
    copy = place.copy()
    (copy.parent / (COPY + "-wal")).write_bytes(b"")

    assert run_restore(copy).code == 0


@pytest.mark.parametrize("what", ["missing", "folder"])
def test_a_missing_file_or_a_folder_is_refused(place: Place, what: str) -> None:
    live = place.live()
    target = place.tmp_path / COPY
    if what == "folder":
        target.mkdir()

    result = run_restore(target)

    assert_refused(place, result, live, "file_missing")


def test_the_live_database_itself_is_refused(place: Place) -> None:
    live = place.live()

    result = run_restore(place.db_path)

    assert_refused(place, result, live, "live_database")


def test_a_file_inside_the_work_folder_is_refused_and_kept(place: Place) -> None:
    live = place.live()
    copy = place.copy()
    inside = place.work_dir / "nextron-test-leftover" / COPY
    inside.parent.mkdir(parents=True)
    inside.write_bytes(copy.read_bytes())

    result = run_restore(inside)

    assert result.code == 1
    assert place.db_path.read_bytes() == live
    assert inside.read_bytes() == copy.read_bytes()  # never swept away
    [refused] = place.events("restore_refused")
    assert refused["reason"] == "in_work_folder"


# Row: Confirm -----------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["yes", " YES\n", "Yes"])
def test_without_yes_typing_yes_restores(place: Place, answer: str) -> None:
    place.live()
    copy = place.copy()
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return answer

    result = run_restore(copy, yes=False, ask=ask)

    assert result.code == 0, result.err
    assert prompts == ["Type yes to restore: "]
    assert (
        f"Restore {COPY} (a plain copy at revision {HEAD}) into {place.db_path}." in result.out
    )
    assert "The current database is saved to" in result.out
    assert marker(place.db_path) == "copy"


@pytest.mark.parametrize(
    "answer", ["no", "", "y", "yes please", EOFError(), KeyboardInterrupt()]
)
def test_without_yes_anything_else_cancels_and_changes_nothing(
    place: Place, answer: str | BaseException
) -> None:
    live = place.live()
    copy = place.copy()
    given = Given(copy)

    def ask(prompt: str) -> str:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    result = run_restore(copy, yes=False, ask=ask)

    assert result.code == 1
    assert result.err == "restore cancelled; nothing was changed\n"
    assert place.db_path.read_bytes() == live
    assert files_in(place.pre_restore_dir) == {}
    given.assert_unchanged()
    place.assert_no_work_folder()
    place.assert_lock_released()
    [refused] = place.events("restore_refused")
    assert refused["reason"] == "cancelled"


# Row: New PC ------------------------------------------------------------------------------------


def test_on_a_new_pc_there_is_no_safety_copy_and_the_swap_creates_the_database(
    place: Place,
) -> None:
    copy = place.copy(marker="new pc")
    given = Given(copy)

    result = run_restore(copy, yes=False, ask=lambda prompt: "yes")

    assert result.code == 0, result.err
    assert "There is no current database to save." in result.out
    assert marker(place.db_path) == "new pc"
    [row] = recovery_rows(place.db_path)
    assert row["pre_restore_name"] is None
    assert not place.pre_restore_dir.exists()
    [restored] = place.events("database_restored")
    assert restored["pre_restore"] is None
    assert place.events("pre_restore_backup_taken") == []
    given.assert_unchanged()
    place.assert_no_work_folder()
    place.assert_lock_released()


# Row: Copy in recovery --------------------------------------------------------------------------


def test_a_copy_taken_during_recovery_mode_supersedes_its_row(place: Place) -> None:
    place.live()
    older = STAMP_AT - timedelta(days=3)

    def in_recovery(source: Path) -> None:
        subscribers = Subscribers()
        subscribers.freeze()
        engine = create_engine(source)
        try:
            with UnitOfWorkFactory(engine, subscribers).open():
                Recovery(FrozenClock(older + timedelta(hours=1))).begin(
                    backup_at=older, source_name="first.db", pre_restore_name=None
                )
        finally:
            engine.dispose()

    copy = place.copy(prepare=in_recovery)
    given = Given(copy)

    assert run_restore(copy).code == 0

    first, second = recovery_rows(place.db_path)
    assert (first["mode"], first["ended_at"]) == ("superseded", utc_text(NOW))
    assert (second["mode"], second["backup_at"]) == ("on", utc_text(older))
    given.assert_unchanged()
    place.assert_no_work_folder()
    place.assert_lock_released()


# Row: Stale WAL ---------------------------------------------------------------------------------


def test_a_stale_wal_beside_the_live_database_is_replaced_by_the_copy(place: Place) -> None:
    copy = place.copy(marker="copy")
    crashed = place.tmp_path / "crashed" / "nextron.db"
    make_database(crashed, marker="live")
    connection = sqlite3.connect(crashed, isolation_level=None)
    try:  # a crash: the database and its -wal while a connection still holds frames
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute(
            "UPDATE settings SET value = 'only in the wal' WHERE key = 'restore_marker'"
        )
        wal = crashed.with_name("nextron.db-wal").read_bytes()
        assert len(wal) > 0
        place.db_path.parent.mkdir(parents=True, exist_ok=True)
        place.db_path.write_bytes(crashed.read_bytes())
        place.db_path.with_name("nextron.db-wal").write_bytes(wal)
    finally:
        connection.close()
    given = Given(copy)

    result = run_restore(copy)

    assert result.code == 0, result.err
    assert marker(place.pre_restore_dir / PRE_RESTORE) == "only in the wal"  # saved first
    assert marker(place.db_path) == "copy"
    [row] = recovery_rows(place.db_path)
    assert row["mode"] == "on"
    left = place.db_path.with_name("nextron.db-wal")
    assert not left.exists() or left.stat().st_size == 0  # the crash's frames are gone
    given.assert_unchanged()
    place.assert_no_work_folder()
    place.assert_lock_released()


# Row: Engine running ----------------------------------------------------------------------------


def test_while_the_engine_holds_the_lock_nothing_happens(place: Place) -> None:
    live = place.live()
    (place.config.data_dir / "logs").mkdir()
    copy = place.copy()
    given = Given(copy)
    engine_lock = InstanceLock(place.config.data_dir / "nextron.lock")
    engine_lock.acquire()
    try:
        before = everything_in(place.config.data_dir)
        result = run_restore(copy)
        after = everything_in(place.config.data_dir)
    finally:
        engine_lock.release()

    assert (result.code, result.out, result.err) == (1, "", "stop the engine before restoring\n")
    assert place.db_path.read_bytes() == live
    assert after == before  # no log file, no work folder
    given.assert_unchanged()
    assert not place.config.backup_dir.exists()


# The bootstrap and the environment checks -------------------------------------------------------


def test_an_unset_environment_exits_2(place: Place, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = place.copy()
    monkeypatch.delenv("NEXTRON_ENV")

    result = run_restore(copy)

    assert result.code == 2
    assert not place.config.data_dir.exists()


def test_a_failed_environment_check_exits_2(place: Place) -> None:
    copy = place.copy()
    err = io.StringIO()

    code = main.restore(
        copy,
        yes=True,
        probes=fake_probes(sqlite_version="3.30.0"),
        clock=FrozenClock(NOW),
        out=io.StringIO(),
        err=err,
    )

    assert code == 2
    assert "startup check failed: sqlite_version" in err.getvalue()
    assert not place.config.data_dir.exists()


# Steps that fail after the safety copy ---------------------------------------------------------


def test_a_failed_safety_copy_refuses_and_says_to_move_the_database_aside(
    place: Place, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = place.live()
    copy = place.copy()

    def cannot_save(db_path: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(main, "save_current", cannot_save)

    result = run_restore(copy)

    assert result.code == 1
    assert result.err.endswith("; nothing was changed\n")
    assert "move nextron.db, nextron.db-wal and nextron.db-shm out of" in result.err
    assert place.db_path.read_bytes() == live
    [failed] = place.events("restore_failed")
    assert (failed["stage"], failed["code"]) == ("pre_restore", "OSError")


def test_a_failed_swap_leaves_the_database_unchanged(
    place: Place, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = place.live()
    copy = place.copy()

    def cannot_swap(candidate: Path, db_path: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main, "swap_in", cannot_swap)

    result = run_restore(copy)

    saved = place.pre_restore_dir / PRE_RESTORE
    assert result.code == 1
    assert result.err == (
        "restore failed: the copy could not be swapped in (OperationalError); "
        f"the database is unchanged; its safety copy is at {saved}\n"
    )
    assert place.db_path.read_bytes() == live
    assert marker(saved) == "live"  # the safety copy stays
    [failed] = place.events("restore_failed")
    assert (failed["stage"], failed["code"]) == ("swap", "OperationalError")
    place.assert_no_work_folder()
    place.assert_lock_released()


def _integrity_fails(found: Any) -> Any:
    return type(found)("row 1 missing from index ix_x", found.revision, found.active_ids)


def _another_revision(found: Any) -> Any:
    return type(found)(found.integrity, OLD_REVISION, found.active_ids)


def _no_on_row(found: Any) -> Any:
    return type(found)(found.integrity, found.revision, ())


def _another_on_row(found: Any) -> Any:
    return type(found)(found.integrity, found.revision, ("01JD3QZ8X5V9K2M7N4P6R8T0ZZ",))


def _raises(found: Any) -> Any:
    raise OSError("the database could not be read back")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (_integrity_fails, "integrity_failed"),
        (_another_revision, "revision_mismatch"),
        (_no_on_row, "recovery_row_mismatch"),
        (_another_on_row, "recovery_row_mismatch"),
        (_raises, "OSError"),
    ],
    ids=["integrity", "revision", "no on row", "another on row", "raises"],
)
def test_a_restored_database_that_cannot_be_verified_names_the_safety_copy(
    place: Place, monkeypatch: pytest.MonkeyPatch, change: Callable[[Any], Any], code: str
) -> None:
    place.live()
    copy = place.copy()
    real_check = main.restored_check
    monkeypatch.setattr(main, "restored_check", lambda db_path: change(real_check(db_path)))

    result = run_restore(copy)

    assert result.code == 1
    saved = place.pre_restore_dir / PRE_RESTORE
    assert result.err == (
        f"the restored database could not be verified ({code}); the database from before the "
        f"restore is at {saved}; do not start the engine: run nextron restore again, or restore "
        "the safety copy\n"
    )
    assert marker(saved) == "live"
    [failed] = place.events("restore_failed")
    assert (failed["level"], failed["stage"], failed["code"]) == ("ERROR", "verify", code)
    assert place.events("database_restored") == []
    place.assert_no_work_folder()
    place.assert_lock_released()


def test_on_a_new_pc_a_failed_verification_says_to_restore_again(
    place: Place, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = place.copy()
    real_check = main.restored_check
    monkeypatch.setattr(main, "restored_check", lambda db_path: _no_on_row(real_check(db_path)))

    result = run_restore(copy)

    assert result.code == 1
    assert result.err == (
        "the restored database could not be verified (recovery_row_mismatch); there was no "
        "database before the restore; do not start the engine: run nextron restore again\n"
    )
    place.assert_lock_released()


# The CLI ----------------------------------------------------------------------------------------


def test_the_parser_knows_restore_with_a_file_and_yes() -> None:
    parsed = cli.build_parser().parse_args(["restore", "C:\\backups\\x.db"])
    assert (parsed.command, parsed.file, parsed.yes) == ("restore", "C:\\backups\\x.db", False)
    assert cli.build_parser().parse_args(["restore", "x.db.enc", "--yes"]).yes is True


def test_the_parser_needs_the_file(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.build_parser().parse_args(["restore"])
    assert exited.value.code == 2


def test_cli_dispatches_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    def fake_restore(file: object, **kwargs: object) -> int:
        calls.append((file, kwargs))
        return 7

    monkeypatch.setattr(cli.engine, "restore", fake_restore)

    assert cli.main(["restore", "x.db", "--yes"]) == 7
    assert cli.main(["restore", "y.db.enc"]) == 7
    assert calls == [("x.db", {"yes": True}), ("y.db.enc", {"yes": False})]



# Row: New PC, the erasure ledger (Story 3.16) ----------------------------------------------------

LEDGER_BYTES = b'{"v":1,"type":"voided","entry_id":"01JD3QZ8X5V9K2M7N4P6R8T0WA","at":"x"}\n'


def ledger_copy(place: Place, at: datetime, data: bytes = LEDGER_BYTES) -> str:
    """An off-PC ledger copy beside the encrypted copy, made by the real ``put``."""
    folder = place.tmp_path / "offpc"
    source = place.tmp_path / f"ledger-{at:%H%M%S}.jsonl"
    source.write_bytes(data)
    secrets = InMemorySecrets("test")
    secrets.write("backup/passphrase", PASSPHRASE)
    name = offpc_names("test", at)[1]
    FolderBackupTarget(folder, secrets=secrets, params=LOW_SCRYPT).put(source, name)
    return name


def ledger_path(place: Place) -> Path:
    return place.config.backup_dir / "erasure-ledger.jsonl"


def test_on_a_new_pc_the_newest_offpc_ledger_is_decrypted_into_the_backup_dir(
    place: Place,
) -> None:
    copy = encrypted_copy(place)
    ledger_copy(place, STAMP_AT - timedelta(days=1), b"old\n")
    newest = ledger_copy(place, STAMP_AT)
    given = Given(copy)

    result = run_restore(copy, passphrase=PASSPHRASE)

    assert result.code == 0, result.err
    assert ledger_path(place).read_bytes() == LEDGER_BYTES
    assert f"erasure ledger recovered from {newest}" in result.out
    assert not ledger_path(place).with_name("erasure-ledger.jsonl.part").exists()
    [recovered] = place.events("erasure_ledger_recovered")
    assert recovered["file"] == newest
    given.assert_unchanged()
    assert_no_path_in_the_log(place)


def test_a_ledger_in_the_backup_dir_is_kept_as_it_is(place: Place) -> None:
    copy = encrypted_copy(place)
    ledger_copy(place, STAMP_AT)
    ledger_path(place).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(place).write_bytes(b"current\n")

    result = run_restore(copy, passphrase=PASSPHRASE)

    assert result.code == 0, result.err
    assert ledger_path(place).read_bytes() == b"current\n"
    assert "erasure ledger" not in result.out
    assert place.events("erasure_ledger_recovered") == []
    assert place.events("erasure_ledger_not_recovered") == []


def test_a_stale_part_file_is_cleared_before_the_ledger_is_decrypted(place: Place) -> None:
    copy = encrypted_copy(place)
    ledger_copy(place, STAMP_AT)
    part = ledger_path(place).with_name("erasure-ledger.jsonl.part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"half")

    result = run_restore(copy, passphrase=PASSPHRASE)

    assert result.code == 0, result.err
    assert ledger_path(place).read_bytes() == LEDGER_BYTES
    assert not part.exists()


@pytest.mark.parametrize("case", ["no ledger copy", "tampered", "plain copy"])
def test_without_a_usable_ledger_the_restore_warns_and_goes_on(place: Place, case: str) -> None:
    if case == "plain copy":
        copy = place.copy(marker="plain")
    else:
        copy = encrypted_copy(place)
    if case == "tampered":
        name = ledger_copy(place, STAMP_AT)
        target = place.tmp_path / "offpc" / name
        data = bytearray(target.read_bytes())
        data[len(data) // 2] ^= 0x01
        target.write_bytes(bytes(data))

    result = run_restore(copy, passphrase=PASSPHRASE)

    assert result.code == 0, result.err
    assert marker(place.db_path) in {"encrypted", "plain"}
    assert (
        f"no erasure ledger found: recovery mode holds sending until {ledger_path(place)} exists"
        in result.out
    )
    assert not ledger_path(place).exists()
    assert not ledger_path(place).with_name("erasure-ledger.jsonl.part").exists()
    [warned] = place.events("erasure_ledger_not_recovered")
    assert warned["level"] == "WARNING"
    assert warned["code"] == ("backup_tampered" if case == "tampered" else "not_found")
    assert_no_path_in_the_log(place)
