"""``InMemorySecrets``: the in-memory fake of ``nextron.ports.secrets.SecretsPort``.

It checks names and values exactly like ``KeyringSecrets`` and raises the same error codes. Its
``store`` is keyed ``(service, name)`` with the service ``nextron-engine/<env>``, and can be
shared between instances to show that environments don't see each other's entries. ``failing``
names the operations (``read``, ``write``, ``delete``) that fail with ``keyring_failed``, as a
locked or broken Credential Manager would; ``calls`` records every operation that got past
validation.
"""

from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED, check_secret_name, check_secret_value

Store = dict[tuple[str, str], str]


class InMemorySecrets:
    def __init__(self, env: str = "test", store: Store | None = None) -> None:
        self.service = f"nextron-engine/{env}"
        self.store: Store = store if store is not None else {}
        self.failing: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def _call(self, operation: str, name: str) -> None:
        self.calls.append((operation, name))
        if operation in self.failing:
            raise AdapterError(False, KEYRING_FAILED, f"{operation} of secret {name} failed")

    def read(self, name: str) -> str | None:
        check_secret_name(name)
        self._call("read", name)
        return self.store.get((self.service, name))

    def write(self, name: str, value: str) -> None:
        check_secret_name(name)
        check_secret_value(value)
        self._call("write", name)
        self.store[(self.service, name)] = value

    def delete(self, name: str) -> None:
        check_secret_name(name)
        self._call("delete", name)
        self.store.pop((self.service, name), None)
