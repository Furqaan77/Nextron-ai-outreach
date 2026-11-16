"""``SecretsPort``: the keyring adapter and the in-memory fake behave the same (AD-12, AD-14).

``KeyringSecrets`` runs on ``DictKeyring``, a keyring backend in a dict, so no test touches the
real Windows Credential Manager. The default backend is only constructed, never called.
"""

from collections.abc import Callable

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.Windows import WinVaultKeyring
from keyring.compat import properties
from keyring.errors import KeyringError, PasswordDeleteError

from nextron.adapters.secrets import KeyringSecrets
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import (
    MAX_SECRET_LENGTH,
    SECRET_TOO_LONG,
    SecretsPort,
    is_secret_name,
    secret_length,
)
from tests.fakes.secrets import InMemorySecrets

EMOJI = "\U0001f511"  # outside the BMP: two UTF-16 code units
BACKEND_TEXT = "backend detail " + "Pw" + "Leak9"  # must never reach an AdapterError


class DictKeyring(KeyringBackend):
    """A keyring backend in a dict, shared like Credential Manager; ``fail`` makes every call
    raise. It is never chosen by ``keyring.get_keyring()``."""

    @properties.classproperty
    def priority(cls) -> float:
        raise RuntimeError("test backend only")

    def __init__(self, store: dict[tuple[str, str], str] | None = None) -> None:
        super().__init__()
        self.store = store if store is not None else {}
        self.calls: list[str] = []
        self.fail: BaseException | None = None

    def _called(self, operation: str) -> None:
        self.calls.append(operation)
        if self.fail is not None:
            raise self.fail

    def get_password(self, service: str, username: str) -> str | None:
        self._called("get")
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._called("set")
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._called("delete")
        if (service, username) not in self.store:
            raise PasswordDeleteError(service)
        del self.store[(service, username)]


Factory = Callable[[str, dict[tuple[str, str], str]], SecretsPort]


def keyring_secrets(env: str, store: dict[tuple[str, str], str]) -> SecretsPort:
    return KeyringSecrets(env, backend=DictKeyring(store))


def in_memory_secrets(env: str, store: dict[tuple[str, str], str]) -> SecretsPort:
    return InMemorySecrets(env, store)


@pytest.fixture(params=[keyring_secrets, in_memory_secrets], ids=["keyring", "in_memory"])
def make(request: pytest.FixtureRequest) -> Factory:
    return request.param


def test_both_implement_the_port(make: Factory) -> None:
    assert isinstance(make("dev", {}), SecretsPort)


def test_write_read_overwrite_and_delete(make: Factory) -> None:
    store: dict[tuple[str, str], str] = {}
    secrets = make("dev", store)

    assert secrets.read("session/open_code") is None
    secrets.write("session/open_code", "first")
    assert secrets.read("session/open_code") == "first"
    secrets.write("session/open_code", "second")
    assert secrets.read("session/open_code") == "second"
    assert store == {("nextron-engine/dev", "session/open_code"): "second"}
    secrets.delete("session/open_code")
    assert secrets.read("session/open_code") is None
    assert store == {}


def test_deleting_a_missing_entry_does_nothing(make: Factory) -> None:
    secrets = make("dev", {})

    secrets.delete("session/open_code")

    assert secrets.read("session/open_code") is None


@pytest.mark.parametrize("name", ["telegram_bot_token", "gmail/ann.lee+x@example.co.uk", "a/B.c-d"])
def test_names_with_parts_are_accepted(name: str, make: Factory) -> None:
    secrets = make("dev", {})

    secrets.write(name, "value")

    assert secrets.read(name) == "value"


# Row: write a secret (1,280 BMP characters; 1,281; 640 emoji; 641)
@pytest.mark.parametrize(
    ("value", "stored"),
    [
        ("x" * MAX_SECRET_LENGTH, True),
        ("x" * (MAX_SECRET_LENGTH + 1), False),
        (EMOJI * (MAX_SECRET_LENGTH // 2), True),
        (EMOJI * (MAX_SECRET_LENGTH // 2 + 1), False),
        ("x" + EMOJI * (MAX_SECRET_LENGTH // 2), False),
    ],
)
def test_the_length_limit_counts_utf16_code_units(value: str, stored: bool, make: Factory) -> None:
    store: dict[tuple[str, str], str] = {}
    secrets = make("dev", store)

    if stored:
        secrets.write("api_key", value)
        assert secrets.read("api_key") == value
        return
    with pytest.raises(AdapterError) as caught:
        secrets.write("api_key", value)
    assert (caught.value.retryable, caught.value.code) == (False, "secret_too_long")
    assert caught.value.message == SECRET_TOO_LONG == "secret exceeds 1,280 characters"
    assert store == {}


def test_secret_length_counts_utf16_code_units() -> None:
    assert secret_length("") == 0
    assert secret_length("abc") == 3
    assert secret_length("é€") == 2
    assert secret_length(EMOJI) == 2
    assert secret_length("a" + EMOJI) == 3


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Session",
        "1session",
        "_session",
        "session/",
        "/session",
        "session//code",
        "session code",
        "session/c d",
        "session/é",
        "session\n",
        "session/x\n",
        "s" * 201,
        None,
        7,
        b"session",
    ],
)
@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_an_invalid_name_is_refused_before_any_backend_call(
    name: object, operation: str, make: Factory
) -> None:
    store: dict[tuple[str, str], str] = {}
    secrets = make("dev", store)
    args = (name, "value") if operation == "write" else (name,)

    with pytest.raises(AdapterError) as caught:
        getattr(secrets, operation)(*args)

    assert (caught.value.retryable, caught.value.code) == (False, "secret_name_invalid")
    assert store == {}
    assert getattr(secrets, "calls", None) in (None, [])
    backend = getattr(secrets, "backend", None)
    assert backend is None or backend.calls == []


def test_a_200_character_name_is_accepted() -> None:
    assert is_secret_name("s" * 200)
    assert not is_secret_name("s" * 201)


@pytest.mark.parametrize("value", ["", None, b"bytes", 42, ["x"]])
def test_a_value_that_is_not_a_non_empty_str_is_refused(value: object, make: Factory) -> None:
    store: dict[tuple[str, str], str] = {}
    secrets = make("dev", store)

    with pytest.raises(AdapterError) as caught:
        secrets.write("api_key", value)  # type: ignore[arg-type]

    assert (caught.value.retryable, caught.value.code) == (False, "secret_invalid")
    assert store == {}


# Row: namespaces (prod writes x; dev reads x on a shared backend)
def test_a_dev_process_cannot_read_a_prod_entry(make: Factory) -> None:
    shared: dict[tuple[str, str], str] = {}
    prod, dev = make("prod", shared), make("dev", shared)

    prod.write("telegram_bot_token", "prod-value")

    assert dev.read("telegram_bot_token") is None
    dev.delete("telegram_bot_token")
    assert prod.read("telegram_bot_token") == "prod-value"
    assert shared == {("nextron-engine/prod", "telegram_bot_token"): "prod-value"}


# Row: backend fails (KeyringError, OSError, RuntimeError)
@pytest.mark.parametrize(
    "failure",
    [KeyringError(BACKEND_TEXT), OSError(5, BACKEND_TEXT), RuntimeError(BACKEND_TEXT)],
    ids=["KeyringError", "OSError", "RuntimeError"],
)
@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_a_backend_failure_becomes_keyring_failed_without_its_text(
    failure: BaseException, operation: str
) -> None:
    backend = DictKeyring()
    backend.fail = failure
    secrets = KeyringSecrets("dev", backend=backend)
    args = ("session/open_code", "value") if operation == "write" else ("session/open_code",)

    with pytest.raises(AdapterError) as caught:
        getattr(secrets, operation)(*args)

    error = caught.value
    assert (error.retryable, error.code) == (False, "keyring_failed")
    assert error.message == f"{operation} of secret session/open_code failed"
    assert BACKEND_TEXT not in str(error) and BACKEND_TEXT not in repr(error)
    assert error.__cause__ is failure


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_the_fake_fails_with_the_same_code(operation: str) -> None:
    secrets = InMemorySecrets("dev")
    secrets.failing.add(operation)
    args = ("session/open_code", "value") if operation == "write" else ("session/open_code",)

    with pytest.raises(AdapterError) as caught:
        getattr(secrets, operation)(*args)

    assert (caught.value.retryable, caught.value.code) == (False, "keyring_failed")
    assert caught.value.message == f"{operation} of secret session/open_code failed"


def test_password_delete_error_means_missing() -> None:
    backend = DictKeyring()
    secrets = KeyringSecrets("dev", backend=backend)

    secrets.delete("session/open_code")

    assert backend.calls == ["delete"]


def test_the_default_backend_is_win_vault_constructed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("the real Credential Manager must not be called")

    for method in ("get_password", "set_password", "delete_password", "get_credential"):
        monkeypatch.setattr(WinVaultKeyring, method, never)
    monkeypatch.setattr(keyring, "get_keyring", never)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    secrets = KeyringSecrets("prod")

    assert type(secrets.backend) is WinVaultKeyring
    assert secrets.backend.persist == 2  # CRED_PERSIST_LOCAL_MACHINE, not ENTERPRISE (3)
    assert secrets.service == "nextron-engine/prod"
    assert KeyringSecrets("dev").service == "nextron-engine/dev"


def test_an_injected_backend_keeps_its_own_persistence() -> None:
    backend = DictKeyring()

    KeyringSecrets("dev", backend=backend)

    assert not hasattr(backend, "persist") and "_persist" not in vars(backend)
