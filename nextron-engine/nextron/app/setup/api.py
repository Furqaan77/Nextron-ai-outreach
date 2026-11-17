"""Public command API of the ``setup`` service.

Other services and the delivery layers change ``setup`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Setup`` (AD-12) stores operator settings in the ``settings`` table, each as the canonical text
of a setting declared in ``nextron.domain.settings_schema``. Each method is a ``@use_case``
that reads the clock once and opens one ``uow.transaction()``.

- ``set(key, value)`` stores one typed value: an undeclared key raises ``UnknownSetting``, then
  ``encode`` raises ``InvalidSettingValue`` for a value it refuses; otherwise the canonical text
  is upserted with ``updated_at`` = now (the row keeps its ``id``).
- ``save(texts)`` stores what the Settings page submitted, all or nothing. An undeclared key
  raises ``UnknownSetting`` before anything is read. Each text is stripped, then:

  - blank, on a key with a default: the row is deleted, so the default applies;
  - blank, on a required key that isn't set: nothing changes (a setting can stay unset);
  - blank, on a required key that is set: a problem with rule ``required`` (it can't be
    cleared);
  - otherwise ``parse``; the canonical text is written only when it differs from the stored
    one, so an unchanged key keeps its ``updated_at``. A key without a row compares with its
    default text instead: the page shows the default, and sending it back unchanged leaves the
    key unset, so a default changed in a later version still reaches it.

  Any problem raises ``InvalidSettings`` (every refused key with its rule) before anything is
  written. It returns every stored text after the save, as ``{key: text}``.

Both log ``settings_changed`` with ``keys``, the changed key names comma-joined, when at least
one key changed; never a value.

``Credentials`` (AD-12, FR-56, NFR-18) stores the external credentials declared in
``nextron.domain.credentials`` in Windows Credential Manager through ``SecretsPort``. It touches
no database, so its methods are not ``@use_case``s and need no unit of work; one
``threading.Lock`` runs them one at a time. Each credential has two keyring entries: its own
(``secret_name``), which holds the value, and ``meta/<secret_name>``, which holds only the
value's last-updated time (``encode_updated_at``). An undeclared key raises
``UnknownCredential`` before any keyring call.

- ``statuses()`` gives a ``CredentialStatus`` per declared credential, in registry order;
  ``status(key)`` gives one. The state is read from the credential's own entry: present is
  ``set``, absent is ``not set``, and the value is dropped at once (Credential Manager has no
  existence-only call). Only when it is ``set`` is ``updated_at`` decoded from the meta entry;
  a missing, garbled or unreadable meta entry leaves it ``None`` ("last update time unknown").
  An ``AdapterError`` reading the credential's own entry gives ``unknown``, with no time. Either
  read failure logs ``credential_read_failed`` (WARNING) with ``credential``, ``entry``
  (``value`` or ``meta``) and the error's ``code``.
- ``save(key, text)`` strips ``text``. A blank result raises ``InvalidCredentialValue`` with
  reason ``ENTER_A_VALUE``, and more than ``MAX_SECRET_LENGTH`` UTF-16 units raises it with
  reason ``SECRET_TOO_LONG``; either way nothing is written. Otherwise it writes the stripped
  value, then the meta entry (the secret first, so a failed meta write can only understate the
  time), logs ``credential_saved`` and returns the ``set`` status with the new time.
- ``remove(key)`` deletes the credential's entry, then its meta entry, logs
  ``credential_removed`` and returns the ``not set`` status.
- An ``AdapterError`` while saving or removing logs ``credential_store_failed`` (``credential``,
  ``operation`` = ``save`` or ``remove``, and the error's ``code``) and raises
  ``CredentialStoreFailed``; ``status(key)`` then shows what is stored.

Every log record names the credential's key, never its value.
"""

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, NoReturn

from nextron.db.repos.setup import settings as settings_repo
from nextron.db.uow import current, use_case
from nextron.domain import credentials as credential_registry
from nextron.domain import settings_schema
from nextron.domain.credentials import (
    Credential,
    InvalidCredentialValue,
    decode_updated_at,
    encode_updated_at,
)
from nextron.domain.settings_schema import (
    REQUIRED,
    REQUIRED_RULE,
    InvalidSettings,
    InvalidSettingValue,
)
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import (
    MAX_SECRET_LENGTH,
    SECRET_TOO_LONG,
    SecretsPort,
    secret_length,
)

SETTINGS_CHANGED: Final = "settings_changed"
CANNOT_CLEAR: Final = "is required and can't be cleared once set"
ENTER_A_VALUE: Final = "enter a value"
CREDENTIAL_SAVED: Final = "credential_saved"
CREDENTIAL_REMOVED: Final = "credential_removed"
CREDENTIAL_STORE_FAILED: Final = "credential_store_failed"
CREDENTIAL_READ_FAILED: Final = "credential_read_failed"
SAVE: Final = "save"
REMOVE: Final = "remove"
VALUE_ENTRY: Final = "value"  # the credential's own keyring entry
META_ENTRY: Final = "meta"  # its meta/<name> entry

log = logging.getLogger(__name__)


def _log_changed(keys: list[str]) -> None:
    if keys:
        log.info(SETTINGS_CHANGED, extra={"keys": ",".join(keys)})


class Setup:
    """Operator settings: one typed value at a time, or a whole Settings page at once."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def set(self, key: str, value: object) -> None:
        """Store ``value`` for the declared setting ``key``."""
        now = self._clock.now()
        found = settings_schema.setting(key)
        text = settings_schema.encode(found, value)
        name = str(found.key)
        with current().transaction():
            settings_repo.upsert(name, text, now)
        _log_changed([name])

    @use_case
    def save(self, texts: Mapping[str, str]) -> dict[str, str]:
        """Store the submitted ``texts`` (``{key: text}``, any subset of the declared keys),
        all or nothing; return every stored text afterwards."""
        now = self._clock.now()
        chosen = {key: settings_schema.setting(key) for key in texts}
        with current().transaction():
            stored = settings_repo.stored_texts()
            problems: dict[str, InvalidSettingValue] = {}
            writes: dict[str, str] = {}
            deletes: list[str] = []
            changed: list[str] = []  # in submission order
            for key, raw in texts.items():
                found = chosen[key]
                name = str(found.key)
                if not isinstance(raw, str):
                    problems[name] = InvalidSettingValue(name, found.type, "must be text")
                    continue
                text = raw.strip()
                before = stored.get(name)
                if not text:
                    if found.default is not REQUIRED:
                        if before is not None:
                            deletes.append(name)
                            changed.append(name)
                    elif before is not None:
                        problems[name] = InvalidSettingValue(name, REQUIRED_RULE, CANNOT_CLEAR)
                    continue
                try:
                    canonical = settings_schema.encode(found, settings_schema.parse(found, text))
                except InvalidSettingValue as refused:
                    problems[name] = refused
                    continue
                # an unset key compares with its default text, so an unchanged default stays unset
                unchanged = before if before is not None else settings_schema.default_text(found)
                if canonical != unchanged:
                    writes[name] = canonical
                    changed.append(name)
            if problems:
                raise InvalidSettings(problems)
            for name in deletes:
                settings_repo.delete(name)
            for name, canonical in writes.items():
                settings_repo.upsert(name, canonical, now)
            after = settings_repo.stored_texts()
        _log_changed(changed)
        return after


# Credentials ----------------------------------------------------------------------------------


class CredentialState(StrEnum):
    """Whether a credential's keyring entry exists, as Credential Manager answered."""

    SET = "set"
    NOT_SET = "not set"
    UNKNOWN = "unknown"  # Credential Manager could not be read


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """A credential's state, and its last-updated time when it is set and the time is known."""

    credential: Credential
    state: CredentialState
    updated_at: datetime | None


class CredentialStoreFailed(Exception):
    """Credential Manager failed while saving or removing the credential ``key``."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Windows Credential Manager failed for credential {key}")
        self.key = str(key)


class Credentials:
    """External credentials in Windows Credential Manager: saved and removed, never shown."""

    def __init__(self, secrets: SecretsPort, clock: Clock) -> None:
        self._secrets = secrets
        self._clock = clock
        self._lock = threading.Lock()

    def statuses(self) -> tuple[CredentialStatus, ...]:
        """The status of every declared credential, in registry order."""
        declared = credential_registry.declared()
        with self._lock:
            return tuple(self._status(found) for found in declared)

    def status(self, key: str) -> CredentialStatus:
        """The status of the declared credential ``key``."""
        found = credential_registry.credential(key)
        with self._lock:
            return self._status(found)

    def save(self, key: str, text: str) -> CredentialStatus:
        """Store ``text``, stripped, as the declared credential ``key``, with its time."""
        found = credential_registry.credential(key)
        value = text.strip() if isinstance(text, str) else ""
        if not value:
            raise InvalidCredentialValue(found.key, ENTER_A_VALUE)
        if secret_length(value) > MAX_SECRET_LENGTH:
            raise InvalidCredentialValue(found.key, SECRET_TOO_LONG)
        with self._lock:
            stamp = encode_updated_at(self._clock.now())
            try:
                self._secrets.write(found.secret_name, value)
                self._secrets.write(found.meta_name, stamp)
            except AdapterError as failed:
                _store_failed(found, SAVE, failed)
            log.info(CREDENTIAL_SAVED, extra={"credential": str(found.key)})
            return CredentialStatus(found, CredentialState.SET, decode_updated_at(stamp))

    def remove(self, key: str) -> CredentialStatus:
        """Delete the declared credential ``key`` and its time."""
        found = credential_registry.credential(key)
        with self._lock:
            try:
                self._secrets.delete(found.secret_name)
                self._secrets.delete(found.meta_name)
            except AdapterError as failed:
                _store_failed(found, REMOVE, failed)
            log.info(CREDENTIAL_REMOVED, extra={"credential": str(found.key)})
            return CredentialStatus(found, CredentialState.NOT_SET, None)

    def _status(self, found: Credential) -> CredentialStatus:
        try:
            present = self._secrets.read(found.secret_name) is not None  # the value isn't kept
        except AdapterError as failed:
            _read_failed(found, VALUE_ENTRY, failed)
            return CredentialStatus(found, CredentialState.UNKNOWN, None)
        if not present:
            return CredentialStatus(found, CredentialState.NOT_SET, None)
        try:
            updated_at = decode_updated_at(self._secrets.read(found.meta_name))
        except AdapterError as failed:
            _read_failed(found, META_ENTRY, failed)
            updated_at = None
        return CredentialStatus(found, CredentialState.SET, updated_at)


def _read_failed(found: Credential, entry: str, failed: AdapterError) -> None:
    log.warning(
        CREDENTIAL_READ_FAILED,
        extra={"credential": str(found.key), "entry": entry, "code": failed.code},
    )


def _store_failed(found: Credential, operation: str, failed: AdapterError) -> NoReturn:
    log.warning(
        CREDENTIAL_STORE_FAILED,
        extra={"credential": str(found.key), "operation": operation, "code": failed.code},
    )
    raise CredentialStoreFailed(found.key) from None
