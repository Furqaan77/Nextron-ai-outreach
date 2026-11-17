"""``nextron.adapters.backup_target.FolderBackupTarget`` (Story 1.17) on temporary folders, with
``InMemorySecrets`` and ``ScryptParams(10, 8, 1)``: ``put``, ``get``, ``list`` and ``delete``;
no ``.part`` left on any failure; an existing name refused; the name rules (before any I/O); the
passphrase read on every call and ``credential_not_set`` when it is missing; a missing source,
a missing folder parent, a missing copy; and no planted passphrase in any written file or in an
error's ``str``, ``repr`` or formatted traceback. Also the fake, ``InMemoryBackupTarget``."""

import hashlib
import os
import traceback
from pathlib import Path
from typing import Any

import pytest

from nextron.adapters import backup_target as module
from nextron.adapters.backup_target import FolderBackupTarget
from nextron.adapters.crypto import HEADER_SIZE, MAGIC, ScryptParams
from nextron.ports.backup_target import (
    BackupTargetPort,
    FetchedCopy,
    StoredCopy,
    check_backup_name,
    is_backup_name,
)
from nextron.ports.errors import AdapterError
from tests.fakes.backup_target import InMemoryBackupTarget
from tests.fakes.secrets import InMemorySecrets

LOW = ScryptParams(10, 8, 1)
PASSPHRASE = "QZXPLANTEDPASSPHRASE horse battery"
OTHER_PASSPHRASE = "QZXSECONDPASSPHRASE staple"
SECRET_NAME = "backup/passphrase"
NAME = "nextron-test-20261117T090000.000Z.db.enc"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def secrets() -> InMemorySecrets:
    found = InMemorySecrets("test")
    found.write(SECRET_NAME, PASSPHRASE)
    found.calls.clear()
    return found


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "offpc"


@pytest.fixture
def target(folder: Path, secrets: InMemorySecrets) -> FolderBackupTarget:
    return FolderBackupTarget(folder, secrets=secrets, params=LOW)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "local" / "copy.db"
    path.parent.mkdir()
    path.write_bytes(os.urandom(70_000) + b"QZXPLAINTEXTMARKER")
    return path


def raised(call: object, *args: object) -> AdapterError:
    assert callable(call)
    with pytest.raises(AdapterError) as error:
        call(*args)
    return error.value


def leaks(error: AdapterError, *secrets: str) -> list[str]:
    text = "".join(traceback.format_exception(error)) + str(error) + repr(error)
    return [secret for secret in secrets if secret in text]


# put, get, list, delete ------------------------------------------------------------------------


def test_put_encrypts_the_file_into_the_folder_and_get_restores_it(
    target: FolderBackupTarget, folder: Path, source: Path, tmp_path: Path
) -> None:
    plain = source.read_bytes()

    stored = target.put(source, NAME)

    copy = folder / NAME
    assert isinstance(target, BackupTargetPort)
    assert stored == StoredCopy(NAME, copy.stat().st_size, len(plain), digest(plain))
    assert stored.sealed_size == HEADER_SIZE + 2 * 16 + len(plain)  # two chunks
    sealed = copy.read_bytes()
    assert sealed.startswith(MAGIC) and b"QZXPLAINTEXTMARKER" not in sealed
    assert sorted(path.name for path in folder.iterdir()) == [NAME]  # no part left
    restored = tmp_path / "restored.db"

    fetched = target.get(NAME, restored)

    assert fetched == FetchedCopy(len(plain), digest(plain))
    assert restored.read_bytes() == plain


def test_put_creates_only_the_folders_last_level(
    tmp_path: Path, secrets: InMemorySecrets, source: Path
) -> None:
    folder = tmp_path / "share"
    assert not folder.exists()

    FolderBackupTarget(folder, secrets=secrets, params=LOW).put(source, NAME)

    assert (folder / NAME).is_file()


def test_list_names_the_files_directly_in_the_folder_sorted(
    target: FolderBackupTarget, folder: Path, source: Path
) -> None:
    assert target.list() == ()  # a missing folder has none
    target.put(source, "b.enc")
    target.put(source, "a.enc")
    (folder / "nested").mkdir()
    (folder / "nested" / "c.enc").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")

    assert target.list() == ("a.enc", "b.enc", "notes.txt")


def test_delete_removes_a_file_and_a_missing_one_does_nothing(
    target: FolderBackupTarget, folder: Path, source: Path
) -> None:
    target.put(source, NAME)

    target.delete(NAME)
    target.delete(NAME)
    target.delete("never-there.enc")

    assert target.list() == ()
    assert folder.is_dir()


def test_an_existing_name_is_refused_and_left_alone(
    target: FolderBackupTarget, folder: Path, source: Path
) -> None:
    folder.mkdir()
    (folder / NAME).write_bytes(b"someone else's file")

    error = raised(target.put, source, NAME)

    assert (error.code, error.retryable) == ("backup_target_exists", False)
    assert (folder / NAME).read_bytes() == b"someone else's file"
    assert sorted(path.name for path in folder.iterdir()) == [NAME]  # no part left


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "a/b", "a\\b", "c:d", "x" * 256, "a\x00b", "line\nbreak", 7, None],
)
def test_a_name_that_is_not_a_plain_file_name_is_refused_before_any_io(
    target: FolderBackupTarget,
    folder: Path,
    source: Path,
    secrets: InMemorySecrets,
    tmp_path: Path,
    name: object,
) -> None:
    for call, args in (
        (target.put, (source, name)),
        (target.get, (name, tmp_path / "out.db")),
        (target.delete, (name,)),
    ):
        error = raised(call, *args)
        assert (error.code, error.retryable) == ("backup_name_invalid", False)

    assert secrets.calls == []  # not even the passphrase was read
    assert not folder.exists()
    assert not (tmp_path / "out.db").exists()
    assert not is_backup_name(name)


def test_a_name_of_255_characters_is_a_plain_file_name() -> None:
    assert check_backup_name("x" * 255) == "x" * 255
    assert is_backup_name("nextron-test-20261117T090000.000Z.ledger.jsonl.enc")


# The passphrase ----------------------------------------------------------------------------------


def test_the_passphrase_is_read_on_every_call_and_never_kept(
    target: FolderBackupTarget, secrets: InMemorySecrets, source: Path, tmp_path: Path
) -> None:
    target.put(source, "first.enc")
    secrets.write(SECRET_NAME, OTHER_PASSPHRASE)  # changed between calls
    target.put(source, "second.enc")

    assert target.get("second.enc", tmp_path / "second.db").plain_size == source.stat().st_size
    error = raised(target.get, "first.enc", tmp_path / "first.db")  # sealed with the old one

    assert error.code == "backup_wrong_passphrase"
    assert not (tmp_path / "first.db").exists()
    reads = [call for call in secrets.calls if call == ("read", SECRET_NAME)]
    assert len(reads) == 4  # put, put, get, get
    assert all(PASSPHRASE not in repr(value) for value in vars(target).values())


def test_a_missing_passphrase_is_credential_not_set_and_writes_nothing(
    folder: Path, source: Path, tmp_path: Path
) -> None:
    target = FolderBackupTarget(folder, secrets=InMemorySecrets("test"), params=LOW)

    for error in (raised(target.put, source, NAME), raised(target.get, NAME, tmp_path / "x")):
        assert (error.code, error.retryable) == ("credential_not_set", False)

    assert not folder.exists()
    assert not (tmp_path / "x").exists()


def test_a_failed_keyring_read_propagates(
    target: FolderBackupTarget, secrets: InMemorySecrets, source: Path, folder: Path
) -> None:
    secrets.failing.add("read")

    assert raised(target.put, source, NAME).code == "keyring_failed"
    assert not folder.exists()


def test_an_empty_passphrase_is_credential_not_set_and_writes_nothing(
    target: FolderBackupTarget,
    secrets: InMemorySecrets,
    folder: Path,
    source: Path,
    tmp_path: Path,
) -> None:
    folder.mkdir()
    secrets.store[(secrets.service, SECRET_NAME)] = ""  # a hand-made entry: write refuses ""

    for error in (raised(target.put, source, NAME), raised(target.get, NAME, tmp_path / "x")):
        assert (error.code, error.retryable) == ("credential_not_set", False)
        assert error.message == "credential backup_passphrase is not set"

    assert list(folder.iterdir()) == []
    assert not (tmp_path / "x").exists()


# Failures leave no part -----------------------------------------------------------------------


def test_a_missing_source_is_unreadable_and_leaves_nothing(
    target: FolderBackupTarget, folder: Path, tmp_path: Path
) -> None:
    error = raised(target.put, tmp_path / "missing.db", NAME)

    assert (error.code, error.retryable) == ("backup_source_unreadable", False)
    assert target.list() == ()


def test_a_folder_whose_parent_is_missing_is_unavailable_and_no_tree_is_grown(
    tmp_path: Path, secrets: InMemorySecrets, source: Path
) -> None:
    folder = tmp_path / "unplugged" / "backups"
    target = FolderBackupTarget(folder, secrets=secrets, params=LOW)

    error = raised(target.put, source, NAME)

    assert (error.code, error.retryable) == ("backup_target_unavailable", True)
    assert not (tmp_path / "unplugged").exists()


def test_a_folder_that_is_a_file_is_unavailable(
    tmp_path: Path, secrets: InMemorySecrets, source: Path
) -> None:
    folder = tmp_path / "a-file"
    folder.write_bytes(b"not a folder")
    target = FolderBackupTarget(folder, secrets=secrets, params=LOW)

    for error in (raised(target.put, source, NAME), raised(target.list)):
        assert (error.code, error.retryable) == ("backup_target_unavailable", True)
    assert folder.read_bytes() == b"not a folder"


def test_a_source_that_fails_mid_read_leaves_no_part(
    target: FolderBackupTarget, folder: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailsAfterOneRead:
        """The source file, whose second read fails as a disk error would."""

        def __init__(self, handle: Any) -> None:
            self._handle = handle
            self._reads = 0

        def read(self, size: int = -1) -> bytes:
            self._reads += 1
            if self._reads > 1:
                raise OSError(5, "QZXSOURCEERRORTEXT")
            return bytes(self._handle.read(size))

        def __enter__(self) -> "FailsAfterOneRead":
            return self

        def __exit__(self, *exc: object) -> None:
            self._handle.close()

    def opening(path: Any, mode: str = "r") -> Any:
        handle = open(path, mode)  # noqa: SIM115 - closed by the module's with
        return FailsAfterOneRead(handle) if mode == "rb" else handle  # only put's source

    monkeypatch.setattr(module, "open", opening, raising=False)

    error = raised(target.put, source, NAME)

    assert (error.code, error.retryable) == ("backup_source_unreadable", False)
    assert "QZXSOURCEERRORTEXT" not in "".join(traceback.format_exception(error))
    assert target.list() == ()  # the part was deleted


def test_a_failed_flush_to_the_disk_leaves_no_part(
    target: FolderBackupTarget, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fsync(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(module.os, "fsync", fsync)

    error = raised(target.put, source, NAME)

    assert (error.code, error.retryable) == ("backup_target_unavailable", True)
    assert target.list() == ()


def test_a_failed_seal_leaves_no_part(
    target: FolderBackupTarget, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_seal(source: object, sink: object, passphrase: str, **kwargs: object) -> None:
        sink.write(b"half a header")  # type: ignore[attr-defined]
        raise AdapterError(False, "backup_bad_format", "the scrypt parameters are out of range")

    monkeypatch.setattr(module, "seal", broken_seal)

    assert raised(target.put, source, NAME).code == "backup_bad_format"
    assert target.list() == ()


def test_a_stale_part_of_the_same_name_is_left_alone(
    target: FolderBackupTarget, folder: Path, source: Path
) -> None:
    folder.mkdir()
    stale = folder / f"{NAME}.part"
    stale.write_bytes(b"a crashed write")

    error = raised(target.put, source, NAME)

    assert error.code == "backup_target_unavailable"
    assert stale.read_bytes() == b"a crashed write"


# get ----------------------------------------------------------------------------------------------


def test_get_of_a_missing_name_creates_no_target(
    target: FolderBackupTarget, folder: Path, tmp_path: Path
) -> None:
    folder.mkdir()

    error = raised(target.get, NAME, tmp_path / "restored.db")

    assert (error.code, error.retryable) == ("backup_copy_missing", False)
    assert not (tmp_path / "restored.db").exists()


def test_a_folder_that_is_not_there_is_unavailable_to_get_and_delete(
    target: FolderBackupTarget, folder: Path, tmp_path: Path
) -> None:
    assert not folder.exists()  # an unplugged drive or an offline share reads the same

    fetched = raised(target.get, NAME, tmp_path / "restored.db")
    deleted = raised(target.delete, NAME)

    for error in (fetched, deleted):
        assert (error.code, error.retryable) == ("backup_target_unavailable", True)
    assert not (tmp_path / "restored.db").exists()
    assert not folder.exists()
    assert target.list() == ()  # list still reads a missing folder as empty


def test_get_never_overwrites_an_existing_target(
    target: FolderBackupTarget, source: Path, tmp_path: Path
) -> None:
    target.put(source, NAME)
    existing = tmp_path / "restored.db"
    existing.write_bytes(b"keep me")

    error = raised(target.get, NAME, existing)

    assert error.code == "backup_target_exists"
    assert existing.read_bytes() == b"keep me"


@pytest.mark.parametrize(
    ("damage", "code"),
    [
        (lambda data: data[:HEADER_SIZE + 100] + b"\xff" + data[HEADER_SIZE + 101 :], "tampered"),
        (lambda data: data[: HEADER_SIZE + 65_536 + 16], "truncated"),
        (lambda data: b"NOPE" + data[4:], "bad_format"),
    ],
)
def test_a_damaged_copy_deletes_the_target_and_raises(
    target: FolderBackupTarget,
    folder: Path,
    source: Path,
    tmp_path: Path,
    damage: object,
    code: str,
) -> None:
    target.put(source, NAME)
    copy = folder / NAME
    copy.write_bytes(damage(copy.read_bytes()))  # type: ignore[operator]
    restored = tmp_path / "restored.db"

    error = raised(target.get, NAME, restored)

    assert error.code == f"backup_{code}"
    assert not restored.exists()


# Secrecy ----------------------------------------------------------------------------------------


def test_no_planted_passphrase_reaches_a_file_or_an_error(
    target: FolderBackupTarget,
    folder: Path,
    source: Path,
    tmp_path: Path,
    secrets: InMemorySecrets,
) -> None:
    secrets.write("hunter/api_key", "QZXPLANTEDHUNTERKEY")
    target.put(source, NAME)
    target.put(source, "second.enc")
    restored = tmp_path / "restored.db"
    target.get(NAME, restored)
    copy = folder / NAME
    copy.write_bytes(copy.read_bytes()[:-1] + b"\x00")
    errors = [
        raised(target.get, NAME, tmp_path / "tampered.db"),
        raised(target.put, source, NAME),
        raised(target.put, tmp_path / "missing.db", "third.enc"),
        raised(target.get, "missing.enc", tmp_path / "missing.db"),
    ]
    secrets.write(SECRET_NAME, OTHER_PASSPHRASE)
    errors.append(raised(target.get, "second.enc", tmp_path / "other.db"))

    written = [path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()]
    for planted in (PASSPHRASE, OTHER_PASSPHRASE, "QZXPLANTEDPASSPHRASE", "QZXPLANTEDHUNTERKEY"):
        assert all(planted.encode() not in data for data in written)
        assert all(planted.encode("utf-16-le") not in data for data in written)
    for error in errors:
        assert leaks(error, PASSPHRASE, OTHER_PASSPHRASE, "QZXPLANTED", str(tmp_path)) == []
    assert [error.code for error in errors] == [
        "backup_tampered",
        "backup_target_exists",
        "backup_source_unreadable",
        "backup_copy_missing",
        "backup_wrong_passphrase",
    ]


# The fake ---------------------------------------------------------------------------------------


def test_the_fake_stores_plaintext_and_can_be_scripted_to_fail(
    source: Path, tmp_path: Path
) -> None:
    fake = InMemoryBackupTarget()
    assert isinstance(fake, BackupTargetPort)
    plain = source.read_bytes()

    assert fake.put(source, NAME) == StoredCopy(NAME, len(plain), len(plain), digest(plain))
    assert fake.files == {NAME: plain}
    assert fake.get(NAME, tmp_path / "out.db") == FetchedCopy(len(plain), digest(plain))
    assert (tmp_path / "out.db").read_bytes() == plain
    existing = raised(fake.get, NAME, tmp_path / "out.db")  # never overwritten
    assert (existing.code, existing.retryable) == ("backup_target_exists", False)
    assert (tmp_path / "out.db").read_bytes() == plain
    unwritable = raised(fake.get, NAME, tmp_path / "missing" / "out.db")
    assert (unwritable.code, unwritable.retryable) == ("backup_target_unavailable", True)
    assert raised(fake.put, source, NAME).code == "backup_target_exists"
    fake.mismatched.add("other.enc")
    assert fake.put(source, "other.enc").plain_sha256 == "0" * 64
    fake.failures["delete:other.enc"] = AdapterError(True, "backup_target_unavailable", "x")
    assert raised(fake.delete, "other.enc").code == "backup_target_unavailable"
    fake.delete(NAME)
    assert fake.list() == ("other.enc",)
    fake.failures["list"] = AdapterError(True, "backup_target_unavailable", "x")
    assert raised(fake.list).code == "backup_target_unavailable"
    assert raised(fake.put, source, "a/b").code == "backup_name_invalid"
    assert ("delete", NAME) in fake.calls
