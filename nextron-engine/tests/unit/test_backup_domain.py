"""``nextron.domain.backup`` (Stories 1.16 and 1.17): copy names, the daily slot across DST and
in UTC, ``missed_daily``, the kind labels and the alert text; the off-PC names and stamps, every
``offpc_rotation`` case of the matrix, ``offpc_problem`` (with case and slash variants),
``same_volume`` (drives and UNC shares), ``is_offpc_folder``, the off-PC labels and text."""

from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import PurePath
from zoneinfo import ZoneInfo

import pytest

from nextron.domain.backup import (
    BACKUP_DB_JOB,
    BACKUP_FAILED,
    COUNTED_TABLES,
    DEFAULT_BACKUP_TIME,
    DEFAULT_ROTATION_DAYS,
    ERASURE_LEDGER_FILE,
    FOLDER_IN_BACKUP_DIR,
    FOLDER_IN_DATA_DIR,
    FOLDER_NOT_SET,
    JOB_KINDS,
    KIND_FIELD,
    LOCAL_DIR_NAME,
    MAX_ROTATION_DAYS,
    MIN_ROTATION_DAYS,
    OFFPC_ROTATION_DAYS,
    OFFPC_SUFFIX,
    BackupKind,
    BackupNotVerified,
    BackupSchedule,
    BackupStatus,
    LedgerStatus,
    OffpcCopyMismatch,
    OffpcStatus,
    backup_failed_text,
    copy_name,
    is_copy_name,
    is_offpc_folder,
    latest_slot,
    missed_daily,
    offpc_failed_text,
    offpc_names,
    offpc_problem,
    offpc_rotation,
    offpc_stamp,
    same_volume,
)
from nextron.domain.errors import DomainError

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
LA = "America/Los_Angeles"


def test_the_constants_are_the_specified_ones() -> None:
    assert (BACKUP_DB_JOB, BACKUP_FAILED, KIND_FIELD, LOCAL_DIR_NAME) == (
        "backup_db",
        "backup_failed",
        "kind",
        "local",
    )
    assert COUNTED_TABLES == ("suppression_entries", "provenance_records")
    assert DEFAULT_BACKUP_TIME == time(10, 0)
    assert (MIN_ROTATION_DAYS, MAX_ROTATION_DAYS, DEFAULT_ROTATION_DAYS) == (7, 35, 35)
    assert JOB_KINDS == frozenset({BackupKind.DAILY, BackupKind.MANUAL})
    assert [str(kind) for kind in BackupKind] == ["daily", "manual", "pre_migrate"]
    assert [str(status) for status in BackupStatus] == ["verified", "failed"]


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (BackupKind.DAILY, "daily"),
        (BackupKind.MANUAL, "manual"),
        (BackupKind.PRE_MIGRATE, "before a migration"),
    ],
)
def test_each_kind_has_its_label(kind: BackupKind, label: str) -> None:
    assert kind.label == label


# Copy names -------------------------------------------------------------------------------------


def test_copy_name_is_the_utc_time_with_milliseconds() -> None:
    at = datetime(2026, 11, 17, 10, 0, 0, 123456, tzinfo=UTC)

    assert copy_name("test", at) == "nextron-test-20261117T100000.123Z.db"


def test_copy_name_converts_to_utc_and_has_no_colon() -> None:
    at = datetime(2026, 11, 17, 2, 5, 9, 7000, tzinfo=ZoneInfo(LA))  # PST, UTC-8

    name = copy_name("prod", at)

    assert name == "nextron-prod-20261117T100509.007Z.db"
    assert ":" not in name


def test_copy_name_refuses_a_naive_time_and_a_bad_env() -> None:
    with pytest.raises(ValueError, match="aware"):
        copy_name("test", datetime(2026, 11, 17, 10, 0))
    with pytest.raises(ValueError, match="env"):
        copy_name("Prod", datetime(2026, 11, 17, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="env"):
        copy_name("../x", datetime(2026, 11, 17, 10, 0, tzinfo=UTC))
    with pytest.raises(TypeError):
        copy_name("test", "2026-11-17")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("env", "name", "expected"),
    [
        ("test", "nextron-test-20261117T100000.123Z.db", True),
        ("prod", "nextron-prod-20261117T100000.000Z.db", True),
        ("test", "nextron-prod-20261117T100000.123Z.db", False),  # another environment's
        ("test", "nextron-test-20261117T100000Z.db", False),  # no milliseconds
        ("test", "nextron-test-20261117T100000.123Z.db-wal", False),
        ("test", "nextron.db", False),
        ("test", "nextron-test-20261117T100000.123Z.db.bak", False),
        ("test", "xnextron-test-20261117T100000.123Z.db", False),
        ("te.t", "nextron-test-20261117T100000.123Z.db", False),  # an env never a pattern
    ],
)
def test_is_copy_name_fullmatches_the_shape(env: str, name: str, expected: bool) -> None:
    assert is_copy_name(env, name) is expected


def test_is_copy_name_accepts_every_name_copy_name_makes() -> None:
    at = datetime(2026, 3, 8, 23, 59, 59, 999999, tzinfo=UTC)

    assert is_copy_name("dev", copy_name("dev", at))


# The schedule -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "slot"),
    [
        # before today's 10:00 UTC: yesterday's
        (datetime(2026, 11, 17, 9, 59, 59, tzinfo=UTC), datetime(2026, 11, 16, 10, 0, tzinfo=UTC)),
        # exactly at it: today's
        (datetime(2026, 11, 17, 10, 0, tzinfo=UTC), datetime(2026, 11, 17, 10, 0, tzinfo=UTC)),
        # after it: today's
        (datetime(2026, 11, 17, 23, 0, tzinfo=UTC), datetime(2026, 11, 17, 10, 0, tzinfo=UTC)),
    ],
)
def test_latest_slot_in_utc(now: datetime, slot: datetime) -> None:
    assert latest_slot(now, time(10, 0), "UTC") == slot


@pytest.mark.parametrize(
    ("now", "slot"),
    [
        # Los Angeles, PST (UTC-8): 10:00 local is 18:00 UTC
        (datetime(2026, 11, 17, 17, 59, tzinfo=UTC), datetime(2026, 11, 16, 18, 0, tzinfo=UTC)),
        (datetime(2026, 11, 17, 18, 0, tzinfo=UTC), datetime(2026, 11, 17, 18, 0, tzinfo=UTC)),
        (datetime(2026, 11, 18, 7, 0, tzinfo=UTC), datetime(2026, 11, 17, 18, 0, tzinfo=UTC)),
        # PDT (UTC-7): 10:00 local is 17:00 UTC
        (datetime(2026, 7, 1, 17, 0, tzinfo=UTC), datetime(2026, 7, 1, 17, 0, tzinfo=UTC)),
        # 2026-03-08 is the spring change: the day before is PST, the day itself PDT
        (datetime(2026, 3, 8, 16, 59, tzinfo=UTC), datetime(2026, 3, 7, 18, 0, tzinfo=UTC)),
        (datetime(2026, 3, 8, 17, 0, tzinfo=UTC), datetime(2026, 3, 8, 17, 0, tzinfo=UTC)),
        # 2026-11-01 is the autumn change: the day before is PDT, the day itself PST
        (datetime(2026, 11, 1, 17, 30, tzinfo=UTC), datetime(2026, 10, 31, 17, 0, tzinfo=UTC)),
        (datetime(2026, 11, 1, 18, 0, tzinfo=UTC), datetime(2026, 11, 1, 18, 0, tzinfo=UTC)),
    ],
)
def test_latest_slot_follows_the_local_time_across_daylight_saving(
    now: datetime, slot: datetime
) -> None:
    found = latest_slot(now, time(10, 0), LA)

    assert found == slot
    assert found.utcoffset() == timedelta(0)
    assert found.astimezone(ZoneInfo(LA)).time() == time(10, 0)


def test_latest_slot_takes_any_aware_now() -> None:
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone(timedelta(hours=5)))  # 07:00 UTC

    assert latest_slot(now, time(10, 0), "UTC") == datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def test_latest_slot_refuses_a_naive_now() -> None:
    with pytest.raises(ValueError, match="aware"):
        latest_slot(datetime(2026, 11, 17, 10, 0), time(10, 0), "UTC")


@pytest.mark.parametrize(
    ("newest", "missed"),
    [
        (None, False),  # no daily backup yet: a new install waits for its first time
        (datetime(2026, 11, 16, 9, 59, tzinfo=UTC), True),  # before yesterday's slot
        (datetime(2026, 11, 16, 10, 0, tzinfo=UTC), False),  # at the latest slot
        (datetime(2026, 11, 16, 23, 0, tzinfo=UTC), False),  # after it
    ],
)
def test_missed_daily(newest: datetime | None, missed: bool) -> None:
    now = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)  # the latest slot is 2026-11-16 10:00

    assert missed_daily(newest, now, time(10, 0), "UTC") is missed


def test_missed_daily_after_a_shutdown_across_the_slot() -> None:
    newest = datetime(2026, 11, 16, 10, 0, 5, tzinfo=UTC)  # yesterday's run
    now = datetime(2026, 11, 17, 10, 30, tzinfo=UTC)  # the PC was off at today's 10:00

    assert missed_daily(newest, now, time(10, 0), "UTC")


def test_backup_schedule_is_frozen() -> None:
    schedule = BackupSchedule(time(10, 0), "UTC", missed=False)

    with pytest.raises(AttributeError):
        schedule.missed = True  # type: ignore[misc]


# Errors and text --------------------------------------------------------------------------------


def test_backup_not_verified_is_a_domain_error_whose_message_is_the_reason() -> None:
    error = BackupNotVerified("the copy has no Alembic revision")

    assert isinstance(error, DomainError)
    assert error.code == "backup_not_verified"
    assert (error.message, str(error)) == ("the copy has no Alembic revision",) * 2


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (BackupKind.DAILY, f"Backup failed: daily {ULID}"),
        ("manual", f"Backup failed: manual {ULID}"),
        (BackupKind.PRE_MIGRATE, f"Backup failed: pre_migrate {ULID}"),
    ],
)
def test_backup_failed_text_is_ids_and_codes_only(kind: str, text: str) -> None:
    assert backup_failed_text(kind, ULID) == text


def test_backup_failed_text_refuses_anything_but_a_kind_and_a_ulid() -> None:
    with pytest.raises(ValueError):
        backup_failed_text("weekly", ULID)
    with pytest.raises(ValueError):
        backup_failed_text("daily", "not-a-ulid")


# Off-PC copies (Story 1.17) --------------------------------------------------------------------

T = datetime(2026, 11, 17, 9, 0, 0, 123000, tzinfo=UTC)
STAMP = "20261117T090000.123Z"


def test_the_off_pc_constants_are_the_specified_ones() -> None:
    assert (ERASURE_LEDGER_FILE, OFFPC_SUFFIX) == ("erasure-ledger.jsonl", ".enc")
    assert OFFPC_ROTATION_DAYS == MAX_ROTATION_DAYS == 35
    assert [str(status) for status in OffpcStatus] == ["copied", "failed", "not_configured"]
    assert [str(status) for status in LedgerStatus] == ["copied", "absent"]
    assert (FOLDER_NOT_SET, FOLDER_IN_DATA_DIR, FOLDER_IN_BACKUP_DIR) == (
        "folder_not_set",
        "folder_in_data_dir",
        "folder_in_backup_dir",
    )


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (OffpcStatus.COPIED, "copied off the PC"),
        (OffpcStatus.FAILED, "off-PC copy failed"),
        (OffpcStatus.NOT_CONFIGURED, "off-PC not configured"),
    ],
)
def test_each_off_pc_status_has_its_label(status: OffpcStatus, label: str) -> None:
    assert status.label == label


def test_offpc_names_are_the_copy_name_and_the_ledger_name_with_enc() -> None:
    db_name, ledger_name = offpc_names("test", T)

    assert db_name == copy_name("test", T) + ".enc" == f"nextron-test-{STAMP}.db.enc"
    assert ledger_name == f"nextron-test-{STAMP}.ledger.jsonl.enc"
    assert offpc_names("prod", T.astimezone(timezone(timedelta(hours=-5)))) == (
        f"nextron-prod-{STAMP}.db.enc",
        f"nextron-prod-{STAMP}.ledger.jsonl.enc",
    )
    with pytest.raises(ValueError):
        offpc_names("test", T.replace(tzinfo=None))
    with pytest.raises(ValueError):
        offpc_names("Test", T)


@pytest.mark.parametrize(
    ("name", "stamp"),
    [
        (f"nextron-test-{STAMP}.db.enc", T),
        (f"nextron-test-{STAMP}.ledger.jsonl.enc", T),
        (f"nextron-test-{STAMP}.db.enc.part", T),
        (f"nextron-test-{STAMP}.ledger.jsonl.enc.part", T),
        (f"nextron-prod-{STAMP}.db.enc", None),  # another environment's
        (f"nextron-test-{STAMP}.db", None),  # a local copy's name
        (f"nextron-test-{STAMP}.db.enc.part.part", None),
        (f"nextron-test-{STAMP}.jsonl.enc", None),
        ("nextron-test-20261317T090000.123Z.db.enc", None),  # month 13
        ("notes.txt", None),
        (f"NEXTRON-TEST-{STAMP}.db.enc", None),
        (7, None),
    ],
)
def test_offpc_stamp_reads_either_shape_with_or_without_part(
    name: str, stamp: datetime | None
) -> None:
    assert offpc_stamp("test", name) == stamp


def _db(env: str, days: float, *, part: bool = False) -> str:
    return offpc_names(env, T - timedelta(days=days))[0] + (".part" if part else "")


def _ledger(env: str, days: float) -> str:
    return offpc_names(env, T - timedelta(days=days))[1]


def test_offpc_rotation_deletes_old_copies_ledgers_and_parts_of_this_environment_only() -> None:
    names = [
        _db("test", 40),
        _ledger("test", 40),
        _db("test", 36),
        _ledger("test", 36),
        _db("test", 34),
        _ledger("test", 34),
        _db("test", 0),
        _db("test", 50, part=True),
        _db("prod", 50),
        "notes.txt",
    ]

    chosen = offpc_rotation("test", names, T, 35)

    assert chosen == tuple(
        sorted(
            [
                _db("test", 40),
                _ledger("test", 40),
                _db("test", 36),
                _ledger("test", 36),
                _db("test", 50, part=True),
            ]
        )
    )


def test_offpc_rotation_keeps_the_newest_complete_copy_and_its_ledger_however_old() -> None:
    names = [
        _db("test", 40),
        _ledger("test", 40),
        _db("test", 45),
        _ledger("test", 45),
        _db("test", 1, part=True),  # newer, but never completed
    ]

    assert offpc_rotation("test", names, T, 35) == (_db("test", 45), _ledger("test", 45))
    assert offpc_rotation("test", [_db("test", 40)], T, 35) == ()
    assert offpc_rotation("test", [_ledger("test", 40)], T, 35) == (_ledger("test", 40),)
    assert offpc_rotation("test", [], T, 35) == ()


def test_offpc_rotation_keeps_a_copy_exactly_at_the_cutoff() -> None:
    names = [_db("test", 35), _db("test", 35 + 1 / 86400), _db("test", 0)]

    assert offpc_rotation("test", names, T, 35) == (_db("test", 35 + 1 / 86400),)


def test_offpc_rotation_refuses_a_naive_now_and_a_non_integer_day_count() -> None:
    with pytest.raises(ValueError):
        offpc_rotation("test", [], T.replace(tzinfo=None), 35)
    with pytest.raises(TypeError):
        offpc_rotation("test", [], T, 35.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        offpc_rotation("test", [], T, True)


DATA = r"C:\Users\mac\AppData\Local\Nextron\prod"
BACKUPS = r"C:\Users\mac\AppData\Local\Nextron\backups\prod"


@pytest.mark.parametrize(
    ("folder", "problem"),
    [
        ("", "folder_not_set"),
        ("   ", "folder_not_set"),
        (DATA, "folder_in_data_dir"),
        (DATA.upper(), "folder_in_data_dir"),
        (DATA.replace("\\", "/") + "/", "folder_in_data_dir"),
        (DATA + r"\offpc", "folder_in_data_dir"),
        (BACKUPS, "folder_in_backup_dir"),
        (BACKUPS.lower() + r"\Off-PC", "folder_in_backup_dir"),
        (BACKUPS.replace("\\", "/") + "/local", "folder_in_backup_dir"),
        (r"C:\Users\mac\AppData\Local\Nextron", None),  # holds both, but is neither
        (r"C:\Users\mac\AppData\Local\Nextron\prod-offpc", None),  # a sibling, not inside
        (r"E:\NextronBackups", None),
        (r"\\nas\backups\nextron", None),
    ],
)
def test_offpc_problem(folder: str, problem: str | None) -> None:
    assert offpc_problem(folder, DATA, BACKUPS) == problem
    assert offpc_problem(folder, PurePath(DATA), PurePath(BACKUPS)) == problem


@pytest.mark.parametrize(
    ("folder", "data_dir", "same"),
    [
        (r"C:\Backups", r"C:\Data", True),
        (r"c:\Backups", r"C:\Data", True),
        ("c:/Backups", r"C:\Data", True),
        (r"E:\Backups", r"C:\Data", False),
        (r"\\nas\backups\x", r"\\NAS\Backups\data", True),
        ("//nas/backups/x", r"\\nas\backups", True),
        (r"\\nas\other\x", r"\\nas\backups\data", False),
        (r"\\nas\backups\x", r"C:\Data", False),
        ("", r"C:\Data", False),
        ("relative", "relative-too", False),
    ],
)
def test_same_volume_compares_the_drive_or_share(folder: str, data_dir: str, same: bool) -> None:
    assert same_volume(folder, data_dir) is same


@pytest.mark.parametrize(
    ("text", "valid"),
    [
        ("", True),
        ("  ", True),
        (r"E:\NextronBackups", True),
        ("e:/NextronBackups/", True),
        ("E:\\", True),
        (r"\\nas\backups", True),
        (r"\\nas\backups\nextron", True),
        ("//nas/backups/nextron", True),
        ("NextronBackups", False),
        (r"E:NextronBackups", False),  # relative to the drive's current folder
        ("E:", False),
        (r"\NextronBackups", False),  # no drive
        (r"\\nas", False),  # a server with no share
        (r"\\nas\\", False),
        (r"E:\a\..\b", False),
        (r"\\nas\backups\..\other", False),
        (r"\\?\E:\NextronBackups", False),  # a device path
        (r"\\.\E:\NextronBackups", False),
        ("//?/E:/NextronBackups", False),
        (r"\\?\UNC\nas\backups", False),
        (r"1:\NextronBackups", False),
        (r"E:\Backups:ads", False),  # an NTFS stream
        (r"E:\Back<ups", False),
        (r"E:\Back>ups", False),
        (r'E:\Back"ups', False),
        (r"E:\Back|ups", False),
        (r"E:\Back?ups", False),
        (r"E:\Back*ups", False),
        (r"\\nas\back*ups", False),  # the share name too
        (r"\\na|s\backups", False),  # and the server name
        (r"E:\CON", False),
        (r"E:\Backups\nul", False),
        (r"E:\aux.txt\x", False),  # with an extension
        (r"E:\Com1", False),
        (r"E:\LPT9.log", False),
        (r"\\nas\PRN", False),
        (r"E:\Backups.", False),  # Windows drops the dot: E:\Backups
        (r"E:\Backups ", False),  # and a trailing space
        (r"E:\Backups.\x", False),
        (r"\\nas\backups.\x", False),
        (r"\\nas.\backups", False),
        (r"E:\COM10", True),  # not reserved
        (r"E:\console", True),
        (r"E:\Nextron Backups\v1.2", True),
        (None, False),
        (7, False),
    ],
)
def test_is_offpc_folder(text: object, valid: bool) -> None:
    assert is_offpc_folder(text) is valid


def test_offpc_failed_text_is_ids_and_codes_only() -> None:
    assert offpc_failed_text(BackupKind.DAILY, ULID) == f"Off-PC backup copy failed: daily {ULID}"
    assert offpc_failed_text("manual", ULID) == f"Off-PC backup copy failed: manual {ULID}"
    with pytest.raises(ValueError):
        offpc_failed_text("weekly", ULID)
    with pytest.raises(ValueError):
        offpc_failed_text("daily", "not-a-ulid")


def test_offpc_copy_mismatch_is_a_domain_error() -> None:
    error = OffpcCopyMismatch("the off-PC copy's SHA-256 differs from the verified backup's")

    assert isinstance(error, DomainError)
    assert error.code == "offpc_copy_mismatch"
