"""The keyring adapter of ``SecretsPort`` (AD-12, AD-14): Windows Credential Manager.

Import rule: ``nextron.ports``, ``nextron.domain`` and ``keyring``.

Every secret of an environment lives in that environment's namespace, ``nextron-engine/<env>``
(``service``), so a ``dev`` process never reads a ``prod`` entry. The backend is an explicitly
constructed ``WinVaultKeyring``, never ``keyring.get_keyring()``: ``PYTHON_KEYRING_BACKEND`` or a
keyringrc file could otherwise send secrets to a plaintext file backend. The default backend
persists entries as ``"local machine"`` (``CRED_PERSIST_LOCAL_MACHINE``) instead of keyring's
``CRED_PERSIST_ENTERPRISE``, so secrets don't roam with a roaming profile; an injected backend
is left as it is.

**One Credential Manager target per secret.** Each secret lives at the keyring service
``<service>/<name>`` (``target(name)``) with ``<name>`` as the user name, so in Credential
Manager > Windows Credentials the secret ``session/open_code`` of ``dev`` is the target
``nextron-engine/dev/session/open_code``. ``WinVaultKeyring`` moves whatever sits at a target to
``<user>@<target>`` before overwriting it; since every target holds one secret, that only ever
touches the same secret's own entries, so two writers of different secrets (in this process or
in ``nextron open``'s) can no longer lose or delete each other's entry.

- ``write(name, value)`` sets ``target(name)``, then deletes ``WinVaultKeyring``'s copy of the
  previous value (``<name>@<target>``) and any legacy entry (see below). A missing entry is
  ignored.
- ``read(name)`` reads ``target(name)``. When there is none, it looks for a legacy entry: one
  found is moved to ``target(name)`` (written there, then the legacy entry deleted) and
  returned. The move is lazy, so startup never touches Credential Manager.
- ``delete(name)`` removes both layouts.

**Legacy entries.** Before Story 1.9 every secret lived under the shared service
``nextron-engine/<env>`` (keyring keeps one at the target ``nextron-engine/<env>`` and the rest
at ``<name>@nextron-engine/<env>``). ``WinVaultKeyring.delete_password(service, name)`` removes
only entries whose user name is ``name``, so deleting a legacy entry never touches another
secret.

Every backend call of every ``KeyringSecrets`` in this process still runs under one
module-level ``threading.Lock`` (``_BACKEND_LOCK``), which also keeps a read's move and a write's
clean-up whole.

Names and values are checked before any backend call (``nextron.ports.secrets``). Any backend
exception becomes ``AdapterError(False, "keyring_failed", "<operation> of secret <name>
failed")``, without the exception's text; ``PasswordDeleteError`` on delete means the entry is
missing, so deleting it does nothing.
"""

import threading
from typing import Final

from keyring.backend import KeyringBackend
from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import PasswordDeleteError

from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED, check_secret_name, check_secret_value

SERVICE_PREFIX: Final = "nextron-engine"
PERSIST: Final = "local machine"  # CRED_PERSIST_LOCAL_MACHINE: never roams

# Serialises every backend call of every instance in this process (see the module docstring).
_BACKEND_LOCK: Final = threading.Lock()


def service_name(env: str) -> str:
    """The keyring namespace that holds ``env``'s secrets."""
    return f"{SERVICE_PREFIX}/{env}"


def default_backend() -> WinVaultKeyring:
    """Credential Manager, with entries kept on this machine. Constructing it calls nothing."""
    backend = WinVaultKeyring()
    backend.persist = PERSIST
    return backend


class KeyringSecrets:
    """``SecretsPort`` on keyring; ``backend`` defaults to ``default_backend()``."""

    def __init__(self, env: str, *, backend: KeyringBackend | None = None) -> None:
        self.service = service_name(env)
        self._backend = backend if backend is not None else default_backend()

    @property
    def backend(self) -> KeyringBackend:
        return self._backend

    def target(self, name: str) -> str:
        """The keyring service (the Credential Manager target) that holds the secret ``name``."""
        return f"{self.service}/{name}"

    def _delete_quietly(self, service: str, name: str) -> None:
        try:
            self._backend.delete_password(service, name)
        except PasswordDeleteError:
            pass  # the entry is missing

    def _set(self, name: str, value: str) -> None:
        target = self.target(name)
        self._backend.set_password(target, name, value)
        # WinVaultKeyring copied the previous value to <name>@<target>; drop that copy.
        self._delete_quietly(f"{name}@{target}", name)
        self._delete_quietly(self.service, name)  # a legacy entry

    def read(self, name: str) -> str | None:
        check_secret_name(name)
        try:
            with _BACKEND_LOCK:
                value = self._backend.get_password(self.target(name), name)
                if value is not None:
                    return value
                legacy = self._backend.get_password(self.service, name)
                if legacy is None:
                    return None
                self._set(name, legacy)  # moves it: written at its target, legacy deleted
                return legacy
        except Exception as exc:
            raise AdapterError(False, KEYRING_FAILED, f"read of secret {name} failed") from exc

    def write(self, name: str, value: str) -> None:
        check_secret_name(name)
        check_secret_value(value)
        try:
            with _BACKEND_LOCK:
                self._set(name, value)
        except Exception as exc:
            raise AdapterError(False, KEYRING_FAILED, f"write of secret {name} failed") from exc

    def delete(self, name: str) -> None:
        check_secret_name(name)
        try:
            with _BACKEND_LOCK:
                self._delete_quietly(self.target(name), name)  # and WinVault's <name>@ copy
                self._delete_quietly(self.service, name)  # a legacy entry
        except Exception as exc:
            raise AdapterError(False, KEYRING_FAILED, f"delete of secret {name} failed") from exc
