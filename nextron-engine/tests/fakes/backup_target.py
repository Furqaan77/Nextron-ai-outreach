"""``InMemoryBackupTarget``: the in-memory fake of
``nextron.ports.backup_target.BackupTargetPort``.

It stores each file's plaintext in ``files`` (name to bytes), checks names like the folder
adapter (``check_backup_name``) and refuses an existing name (``backup_target_exists``). ``put``
reads its local ``source`` (an ``OSError`` there is ``backup_source_unreadable``); ``get`` writes
``target`` exclusively (an existing one is ``backup_target_exists``, any other ``OSError``
``backup_target_unavailable``), so every call raises ``AdapterError`` only, as the port says.
``calls`` records every operation that got past the name check, as
``(operation, name)`` (``list`` has the name ``""``).

**Scripted failures.** ``failures`` maps an operation (``put``, ``list``, ``get``, ``delete``),
or ``"<operation>:<name>"`` for one name only, to the exception that call raises; the call is
recorded and changes nothing. ``mismatched`` names files whose ``StoredCopy.plain_sha256`` is
reported wrong (all zeros), as if the bytes changed while they were read.
"""

import hashlib
from pathlib import Path
from typing import Final

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

WRONG_SHA256: Final = "0" * 64


class InMemoryBackupTarget:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.failures: dict[str, BaseException] = {}
        self.mismatched: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _call(self, operation: str, name: str = "") -> None:
        self.calls.append((operation, name))
        failure = self.failures.get(f"{operation}:{name}", self.failures.get(operation))
        if failure is not None:
            raise failure

    def put(self, source: Path, name: str) -> StoredCopy:
        check_backup_name(name)
        self._call("put", name)
        try:
            data = Path(source).read_bytes()
        except OSError:
            raise AdapterError(
                False, BACKUP_SOURCE_UNREADABLE, "the source can't be read"
            ) from None
        if name in self.files:
            raise AdapterError(False, BACKUP_TARGET_EXISTS, "that name already exists")
        self.files[name] = data
        digest = WRONG_SHA256 if name in self.mismatched else hashlib.sha256(data).hexdigest()
        return StoredCopy(name, len(data), len(data), digest)

    def list(self) -> tuple[str, ...]:
        self._call("list")
        return tuple(sorted(self.files))

    def get(self, name: str, target: Path) -> FetchedCopy:
        check_backup_name(name)
        self._call("get", name)
        if name not in self.files:
            raise AdapterError(False, BACKUP_COPY_MISSING, "there is no copy of that name")
        data = self.files[name]
        try:
            with open(target, "xb") as written:
                written.write(data)
        except FileExistsError:
            raise AdapterError(
                False, BACKUP_TARGET_EXISTS, "the file to restore into already exists"
            ) from None
        except OSError:
            raise AdapterError(
                True, BACKUP_TARGET_UNAVAILABLE, "the file to restore into can't be written"
            ) from None
        return FetchedCopy(len(data), hashlib.sha256(data).hexdigest())

    def delete(self, name: str) -> None:
        check_backup_name(name)
        self._call("delete", name)
        self.files.pop(name, None)
