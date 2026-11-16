"""The ``SecretsPort`` (AD-12, AD-14): named secrets in Windows Credential Manager.

Each environment has its own namespace, the keyring service ``nextron-engine/<env>``, so a
``dev`` process never reads a ``prod`` secret. ``nextron.adapters.secrets.KeyringSecrets`` is the
live adapter; tests use ``tests.fakes.secrets.InMemorySecrets``.

Rules every implementation follows, checked before any backend call; each failure is an
``AdapterError`` with ``retryable=False``:

- **Name:** matches ``SECRET_NAME_PATTERN`` (a lowercase key such as ``session/open_code``,
  optionally followed by ``/``-separated parts such as an address in ``gmail/<address>``) and is
  at most ``MAX_SECRET_NAME_LENGTH`` characters. Otherwise: ``secret_name_invalid``.
- **Value:** a non-empty ``str`` (otherwise ``secret_invalid``) of at most
  ``MAX_SECRET_LENGTH`` UTF-16 code units (otherwise ``secret_too_long`` with the message
  ``SECRET_TOO_LONG``). Credential Manager stores at most 2,560 bytes of UTF-16.
- **Backend failures** become ``keyring_failed`` with the adapter's own message, never the
  backend's text. Deleting a missing entry does nothing.

Secrets never go into files, the database, logs, job payloads, backups or responses.
"""

import re
from typing import Final, Protocol, TypeGuard, runtime_checkable

from nextron.ports.errors import AdapterError

MAX_SECRET_LENGTH: Final = 1280
SECRET_TOO_LONG: Final = "secret exceeds 1,280 characters"
SECRET_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(/[A-Za-z0-9._@+-]+)*$")
MAX_SECRET_NAME_LENGTH: Final = 200

SECRET_NAME_INVALID: Final = "secret_name_invalid"
SECRET_INVALID: Final = "secret_invalid"
SECRET_TOO_LONG_CODE: Final = "secret_too_long"
KEYRING_FAILED: Final = "keyring_failed"


def secret_length(value: str) -> int:
    """The length of ``value`` in UTF-16 code units: a character outside the BMP counts 2."""
    return sum(2 if ord(char) > 0xFFFF else 1 for char in value)


def is_secret_name(name: object) -> TypeGuard[str]:
    """Whether ``name`` is a valid secret name."""
    return (
        isinstance(name, str)
        and len(name) <= MAX_SECRET_NAME_LENGTH
        and SECRET_NAME_PATTERN.fullmatch(name) is not None
    )


def check_secret_name(name: object) -> str:
    """Return ``name``; raise ``AdapterError`` ``secret_name_invalid`` when it is not valid."""
    if not is_secret_name(name):
        raise AdapterError(
            False,
            SECRET_NAME_INVALID,
            "a secret name must match SECRET_NAME_PATTERN and have at most "
            f"{MAX_SECRET_NAME_LENGTH} characters",
        )
    return name


def check_secret_value(value: object) -> str:
    """Return ``value``; raise ``AdapterError`` ``secret_invalid`` when it is not a non-empty
    ``str``, or ``secret_too_long`` when it has more than ``MAX_SECRET_LENGTH`` UTF-16 units."""
    if not isinstance(value, str) or not value:
        raise AdapterError(False, SECRET_INVALID, "a secret value must be a non-empty str")
    if secret_length(value) > MAX_SECRET_LENGTH:
        raise AdapterError(False, SECRET_TOO_LONG_CODE, SECRET_TOO_LONG)
    return value


@runtime_checkable
class SecretsPort(Protocol):
    """Named secrets of one environment."""

    def read(self, name: str) -> str | None:
        """Return the secret called ``name``, or ``None`` when there is none."""
        ...

    def write(self, name: str, value: str) -> None:
        """Store ``value`` as the secret called ``name``, replacing any previous value."""
        ...

    def delete(self, name: str) -> None:
        """Remove the secret called ``name``; do nothing when there is none."""
        ...
