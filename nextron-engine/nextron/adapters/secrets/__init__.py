"""The keyring adapter of ``SecretsPort`` (AD-12, AD-14): Windows Credential Manager.

Import rule: ``nextron.ports``, ``nextron.domain`` and ``keyring``.

Every secret of an environment lives under the keyring service ``nextron-engine/<env>``, with
the secret's name as the keyring user name, so a ``dev`` process never reads a ``prod`` entry.
The backend is an explicitly constructed ``WinVaultKeyring``, never ``keyring.get_keyring()``:
``PYTHON_KEYRING_BACKEND`` or a keyringrc file could otherwise send secrets to a plaintext file
backend. The default backend persists entries as ``"local machine"``
(``CRED_PERSIST_LOCAL_MACHINE``) instead of keyring's ``CRED_PERSIST_ENTERPRISE``, so secrets
don't roam with a roaming profile; an injected backend is left as it is.

``WinVaultKeyring`` keeps one secret at the target ``nextron-engine/<env>`` and every other one
at ``<name>@nextron-engine/<env>`` (Credential Manager > Windows Credentials).

Names and values are checked before any backend call (``nextron.ports.secrets``). Any backend
exception becomes ``AdapterError(False, "keyring_failed", "<operation> of secret <name>
failed")``, without the exception's text; ``PasswordDeleteError`` on delete means the entry is
missing, so deleting it does nothing.
"""

from typing import Final

from keyring.backend import KeyringBackend
from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import PasswordDeleteError

from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED, check_secret_name, check_secret_value

SERVICE_PREFIX: Final = "nextron-engine"
PERSIST: Final = "local machine"  # CRED_PERSIST_LOCAL_MACHINE: never roams


def service_name(env: str) -> str:
    """The keyring service that holds ``env``'s secrets."""
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

    def read(self, name: str) -> str | None:
        check_secret_name(name)
        try:
            return self._backend.get_password(self.service, name)
        except Exception as exc:
            raise AdapterError(False, KEYRING_FAILED, f"read of secret {name} failed") from exc

    def write(self, name: str, value: str) -> None:
        check_secret_name(name)
        check_secret_value(value)
        try:
            self._backend.set_password(self.service, name, value)
        except Exception as exc:
            raise AdapterError(False, KEYRING_FAILED, f"write of secret {name} failed") from exc

    def delete(self, name: str) -> None:
        check_secret_name(name)
        try:
            self._backend.delete_password(self.service, name)
        except PasswordDeleteError:
            return  # the entry is missing
        except Exception as exc:
            raise AdapterError(False, KEYRING_FAILED, f"delete of secret {name} failed") from exc
