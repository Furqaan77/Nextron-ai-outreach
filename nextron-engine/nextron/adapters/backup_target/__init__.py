"""The folder backup target (AD-9, AD-17, AD-20, OP-6, NFR-17, Story 1.17): encrypted copies in
one folder, a second drive or a network share, through ``nextron.adapters.crypto``.

Import rule: ``nextron.ports``, ``nextron.domain``, ``nextron.adapters.crypto`` and the
standard library. Nothing here logs.

**``FolderBackupTarget(folder, *, secrets, params=DEFAULT_SCRYPT)``** implements
``BackupTargetPort`` for ``folder``. ``nextron.main`` builds one per backup through
``functools.partial(FolderBackupTarget, secrets=secrets)``. It decides nothing about
environments, names or age; it only encrypts, writes, lists, decrypts and deletes inside
``folder``. ``params`` are the scrypt parameters it writes; a reader takes each file's own.

- **Names.** Every call first checks the name (``check_backup_name``: a plain file name, no
  ``\\``, ``/`` or ``:``, not ``.`` or ``..``, at most 255 characters): ``backup_name_invalid``,
  before any I/O.
- **The passphrase.** ``put`` and ``get`` then read it with ``read_credential(secrets,
  "backup_passphrase")`` (keyring ``backup/passphrase``), on every call, and never keep it;
  ``credential_not_set`` and ``keyring_failed`` propagate, and an empty value (a hand-made
  entry) is ``credential_not_set`` too, before any I/O.
- **``put(source, name)``**, in order: ``folder.mkdir(exist_ok=True)`` (its last level only,
  never its parents, so a wrong or unplugged drive fails instead of growing a new tree); open
  ``source`` (``rb``) and ``<folder>\\<name>.part`` (``xb``); ``seal`` the one into the other,
  then flush and ``os.fsync``; rename the part to ``<name>``, which Windows refuses when the
  name exists (``backup_target_exists``). Any failure after the part was created deletes it
  (best effort), so a half-written file never has a final name, and nothing is ever written in
  plaintext. It streams the file: there is no ``shutil`` and no file copy.
- **``get(name, target)``** opens ``<folder>\\<name>`` (missing: ``backup_copy_missing``, but
  ``backup_target_unavailable`` when the folder itself isn't there) and the new file ``target``
  (``xb``; an existing one: ``backup_target_exists``), then ``unseal``s; a failure deletes
  ``target`` (only when this call created it) and raises.
- **``list()``**: the names of the regular files directly in ``folder``, sorted; a missing
  folder gives ``()``.
- **``delete(name)``** removes ``<folder>\\<name>``; a missing one does nothing. A folder that
  isn't there (an unplugged drive or an offline share, which Windows reports as a missing file)
  is ``backup_target_unavailable``, so rotation never marks a copy deleted that it couldn't
  reach.

An ``OSError`` on the folder, a file in it or ``get``'s ``target`` is the retryable
``backup_target_unavailable``; one on ``put``'s ``source`` is ``backup_source_unreadable``.
Every error is an ``AdapterError`` with this module's own text, raised ``from None`` (or passed
on from ``crypto``): none carries a path, the passphrase, a key or an exception's text.
"""

import contextlib
import os
from pathlib import Path
from typing import BinaryIO, Final

from nextron.adapters.crypto import DEFAULT_SCRYPT, ScryptParams, Sealed, seal, unseal
from nextron.domain.credentials import CredentialKey
from nextron.ports.backup_target import (
    BACKUP_COPY_MISSING,
    BACKUP_SOURCE_UNREADABLE,
    BACKUP_TARGET_EXISTS,
    BACKUP_TARGET_UNAVAILABLE,
    FetchedCopy,
    StoredCopy,
    check_backup_name,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET, SecretsPort, read_credential

PART_SUFFIX: Final = ".part"
FOLDER_UNAVAILABLE: Final = "the off-PC backup folder could not be reached or written"
SOURCE_UNREADABLE: Final = "the backup copy to send could not be read"


class _Reading:
    """``file``'s reads, with an ``OSError`` raised as ``AdapterError(retryable, code)``."""

    def __init__(self, file: BinaryIO, retryable: bool, code: str, message: str) -> None:
        self._file = file
        self._retryable = retryable
        self._code = code
        self._message = message

    def read(self, size: int, /) -> bytes:
        try:
            return self._file.read(size)
        except OSError:
            raise AdapterError(self._retryable, self._code, self._message) from None


class _Writing:
    """``file``'s writes, with an ``OSError`` raised as ``backup_target_unavailable``."""

    def __init__(self, file: BinaryIO) -> None:
        self._file = file

    def write(self, data: bytes, /) -> int:
        try:
            return self._file.write(data)
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None


def _remove(path: Path) -> None:
    """Delete ``path``, best effort: a file that can't be deleted stays."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


class FolderBackupTarget:
    """``BackupTargetPort`` over one folder; see the module docstring."""

    def __init__(
        self, folder: Path, *, secrets: SecretsPort, params: ScryptParams = DEFAULT_SCRYPT
    ) -> None:
        self._folder = Path(folder)
        self._secrets = secrets
        self._params = params

    @property
    def folder(self) -> Path:
        return self._folder

    def _passphrase(self) -> str:
        """The Backup passphrase, read now; an empty one counts as not set."""
        passphrase = read_credential(self._secrets, CredentialKey.BACKUP_PASSPHRASE)
        if not passphrase:
            raise AdapterError(
                False, CREDENTIAL_NOT_SET, "credential backup_passphrase is not set"
            ) from None
        return passphrase

    def _unavailable_unless_folder(self) -> None:
        """Raise ``backup_target_unavailable`` when the folder isn't there: Windows reports an
        unplugged drive or an offline share as a missing file."""
        if not self._folder.is_dir():
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None

    def put(self, source: Path, name: str) -> StoredCopy:
        check_backup_name(name)
        passphrase = self._passphrase()
        try:
            self._folder.mkdir(exist_ok=True)  # the last level only: never its parents
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
        try:
            reader = open(source, "rb")  # noqa: SIM115 - closed by the with below
        except OSError:
            raise AdapterError(False, BACKUP_SOURCE_UNREADABLE, SOURCE_UNREADABLE) from None
        part = self._folder / f"{name}{PART_SUFFIX}"
        with reader:
            try:
                writer = open(part, "xb")  # noqa: SIM115 - closed by the with below
            except OSError:
                raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
            stored = False
            try:
                sealed = self._write(reader, writer, passphrase)
                self._rename(part, self._folder / name)
                stored = True
            finally:
                if not stored:
                    _remove(part)
        return StoredCopy(name, sealed.sealed_size, sealed.plain_size, sealed.plain_sha256)

    def _write(self, reader: BinaryIO, writer: BinaryIO, passphrase: str) -> Sealed:
        """Seal ``reader`` into ``writer``, flush it to the disk and close it."""
        try:
            with writer:
                sealed = seal(
                    _Reading(reader, False, BACKUP_SOURCE_UNREADABLE, SOURCE_UNREADABLE),
                    _Writing(writer),
                    passphrase,
                    params=self._params,
                )
                writer.flush()
                os.fsync(writer.fileno())
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
        return sealed

    @staticmethod
    def _rename(part: Path, final: Path) -> None:
        try:
            part.rename(final)  # Windows refuses an existing name: never replaced
        except FileExistsError:
            raise AdapterError(
                False, BACKUP_TARGET_EXISTS, "an off-PC copy of that name already exists"
            ) from None
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None

    def list(self) -> tuple[str, ...]:
        try:
            with os.scandir(self._folder) as entries:
                names = [entry.name for entry in entries if entry.is_file(follow_symlinks=False)]
        except FileNotFoundError:
            return ()
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
        return tuple(sorted(names))

    def get(self, name: str, target: Path) -> FetchedCopy:
        check_backup_name(name)
        passphrase = self._passphrase()
        try:
            reader = open(self._folder / name, "rb")  # noqa: SIM115 - closed by the with below
        except FileNotFoundError:
            self._unavailable_unless_folder()
            raise AdapterError(
                False, BACKUP_COPY_MISSING, "there is no off-PC copy of that name"
            ) from None
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
        with reader:
            try:
                writer = open(target, "xb")  # noqa: SIM115 - closed by the with below
            except FileExistsError:
                raise AdapterError(
                    False, BACKUP_TARGET_EXISTS, "the file to restore into already exists"
                ) from None
            except OSError:
                raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
            restored = False
            try:
                try:
                    with writer:
                        opened = unseal(
                            _Reading(reader, True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE),
                            _Writing(writer),
                            passphrase,
                        )
                except OSError:
                    raise AdapterError(
                        True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE
                    ) from None
                restored = True
            finally:
                if not restored:
                    _remove(Path(target))
        return FetchedCopy(opened.plain_size, opened.plain_sha256)

    def delete(self, name: str) -> None:
        check_backup_name(name)
        self._unavailable_unless_folder()
        try:
            (self._folder / name).unlink(missing_ok=True)
        except OSError:
            raise AdapterError(True, BACKUP_TARGET_UNAVAILABLE, FOLDER_UNAVAILABLE) from None
