"""The ``BackupTargetPort`` (AD-9, AD-17, AD-20, OP-6, NFR-17): where encrypted backup copies
go off the PC (Story 1.17).

A target is one folder. ``nextron.adapters.backup_target.FolderBackupTarget`` is the adapter:
it encrypts each file with the Backup passphrase (``backup/passphrase`` in Windows Credential
Manager) on the way in and decrypts it on the way out, so nothing reaches the folder in
plaintext. Tests use ``tests.fakes.backup_target.InMemoryBackupTarget``. A target decides
nothing about environments, names or age: ``ops`` and ``nextron.domain.backup`` name the files
and choose what rotates.

- ``put(source, name) -> StoredCopy(name, sealed_size, plain_size, plain_sha256)`` stores the
  local file ``source`` as ``name``; ``plain_sha256`` is the SHA-256 of the bytes it read, so
  the caller can check them against what it verified. An existing ``name`` is refused
  (``backup_target_exists``).
- ``list() -> tuple[str, ...]``: the names of the files directly in the target, sorted; a
  target that doesn't exist yet has none.
- ``get(name, target) -> FetchedCopy(plain_size, plain_sha256)`` restores ``name`` into the new
  local file ``target`` (Story 1.18's restore test).
- ``delete(name)`` removes it; a missing name does nothing.

``BackupTargets`` is ``Callable[[Path], BackupTargetPort]``: the target for a folder.
``nextron.main`` binds it to ``FolderBackupTarget`` with the environment's secrets.

Every call raises ``AdapterError`` (``nextron.ports.errors``) only, with one of these codes:

=================================  ==========  ==================================================
Code                               Retryable   Meaning
=================================  ==========  ==================================================
``backup_name_invalid``            no          not a plain file name (``check_backup_name``)
``backup_target_exists``           no          ``put``'s name, or ``get``'s target, exists
``backup_target_unavailable``      yes         the folder (or its files) can't be reached
``backup_source_unreadable``       no          ``put``'s local source can't be read
``backup_copy_missing``            no          ``get`` found no file of that name
``backup_bad_format``              no          not a backup file of a known format
``backup_wrong_passphrase``        no          the passphrase doesn't open the file
``backup_tampered``                no          a byte, or a chunk's order, changed
``backup_truncated``               no          the file ends before its last chunk
``credential_not_set``             no          the Backup passphrase isn't set
``keyring_failed``                 no          Credential Manager failed
=================================  ==========  ==================================================

No error carries a path, the passphrase, a key or an exception's text.

**Names.** ``check_backup_name(name)`` returns ``name`` when it is a plain file name: a
non-empty ``str`` of at most ``MAX_NAME_LENGTH`` (255) characters, with no ``\\``, ``/``,
``:`` or control character, and not ``.`` or ``..``; otherwise it raises ``AdapterError``
``backup_name_invalid``, before any I/O.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypeGuard, runtime_checkable

from nextron.ports.errors import AdapterError

MAX_NAME_LENGTH: Final = 255

BACKUP_NAME_INVALID: Final = "backup_name_invalid"
BACKUP_TARGET_EXISTS: Final = "backup_target_exists"
BACKUP_TARGET_UNAVAILABLE: Final = "backup_target_unavailable"  # retryable
BACKUP_SOURCE_UNREADABLE: Final = "backup_source_unreadable"
BACKUP_COPY_MISSING: Final = "backup_copy_missing"
BACKUP_BAD_FORMAT: Final = "backup_bad_format"
BACKUP_WRONG_PASSPHRASE: Final = "backup_wrong_passphrase"
BACKUP_TAMPERED: Final = "backup_tampered"
BACKUP_TRUNCATED: Final = "backup_truncated"

_REFUSED_CHARACTERS: Final = frozenset("\\/:")
_REFUSED_NAMES: Final = frozenset({".", ".."})


@dataclass(frozen=True, slots=True)
class StoredCopy:
    """A file ``put`` stored: its name, its size in the target, and the size and SHA-256 of
    the plaintext it read."""

    name: str
    sealed_size: int
    plain_size: int
    plain_sha256: str


@dataclass(frozen=True, slots=True)
class FetchedCopy:
    """A file ``get`` restored: the size and SHA-256 of the plaintext it wrote."""

    plain_size: int
    plain_sha256: str


def is_backup_name(name: object) -> TypeGuard[str]:
    """Whether ``name`` is a plain file name a target accepts."""
    return (
        isinstance(name, str)
        and 0 < len(name) <= MAX_NAME_LENGTH
        and name not in _REFUSED_NAMES
        and not any(char in _REFUSED_CHARACTERS or ord(char) < 32 for char in name)
    )


def check_backup_name(name: object) -> str:
    """Return ``name``; raise ``AdapterError`` ``backup_name_invalid`` when it isn't a plain
    file name."""
    if not is_backup_name(name):
        raise AdapterError(
            False,
            BACKUP_NAME_INVALID,
            f"a backup name is a plain file name of at most {MAX_NAME_LENGTH} characters",
        )
    return name


@runtime_checkable
class BackupTargetPort(Protocol):
    """One folder of encrypted backup copies."""

    def put(self, source: Path, name: str) -> StoredCopy:
        """Store the local file ``source`` as ``name``; raise ``AdapterError`` on failure."""
        ...

    def list(self) -> tuple[str, ...]:
        """The names of the files directly in the target, sorted."""
        ...

    def get(self, name: str, target: Path) -> FetchedCopy:
        """Restore ``name`` into the new local file ``target``."""
        ...

    def delete(self, name: str) -> None:
        """Remove ``name``; do nothing when it is missing."""
        ...


BackupTargets = Callable[[Path], BackupTargetPort]
