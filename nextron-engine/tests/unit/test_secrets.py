"""``SecretsPort``: the keyring adapter and the in-memory fake behave the same (AD-12, AD-14).

``KeyringSecrets`` runs on ``DictKeyring``, a keyring backend in a dict, and, for the layout of
Credential Manager targets, on ``VaultInMemory``: ``WinVaultKeyring``'s own ``get_password``,
``set_password`` and ``delete_password`` over an in-memory credential table instead of
``win32cred``. No test touches the real Windows Credential Manager. The default backend is only
constructed, never called.
"""

import threading
from collections.abc import Callable

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.Windows import DecodingCredential, WinVaultKeyring
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
from tests.live_guard import allow_live_constructor

EMOJI = "\U0001f511"  # outside the BMP: two UTF-16 code units
BACKEND_TEXT = "backend detail " + "Pw" + "Leak9"  # must never reach an AdapterError
# WinVaultKeyring's own methods, taken before any test's guard replaces them (tests/live_guard.py)
WIN_VAULT_METHODS = {
    name: vars(WinVaultKeyring)[name] for name in ("get_password", "set_password", "delete_password")
}


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


def entry(secrets: SecretsPort, name: str) -> tuple[str, str]:
    """Where ``secrets`` keeps ``name`` in its store, as ``(service, user name)``: the keyring
    adapter at the secret's own target, the fake under its service."""
    if isinstance(secrets, KeyringSecrets):
        return (secrets.target(name), name)
    return (getattr(secrets, "service"), name)


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
    assert store == {entry(secrets, "session/open_code"): "second"}
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
    assert shared == {entry(prod, "telegram_bot_token"): "prod-value"}


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

    assert backend.calls == ["delete", "delete"]  # its own target, then a legacy entry


def test_the_default_backend_is_win_vault_constructed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("the real Credential Manager must not be called")

    for method in ("get_password", "set_password", "delete_password", "get_credential"):
        monkeypatch.setattr(WinVaultKeyring, method, never)
    # Every backend call is neutralised above, so the live constructor may run here.
    allow_live_constructor(monkeypatch, "nextron.adapters.secrets", "default_backend")
    monkeypatch.setattr(keyring, "get_keyring", never)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    secrets = KeyringSecrets("prod")

    assert type(secrets.backend) is WinVaultKeyring
    assert secrets.backend.persist == 2  # CRED_PERSIST_LOCAL_MACHINE, not ENTERPRISE (3)
    assert secrets.service == "nextron-engine/prod"
    assert KeyringSecrets("dev").service == "nextron-engine/dev"


class CountingKeyring(DictKeyring):
    """A dict backend whose calls wait for ``release`` and count how many run at once, as two
    ``WinVaultKeyring`` calls racing on the shared target would."""

    def __init__(self, store: dict[tuple[str, str], str]) -> None:
        super().__init__(store)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.started = 0
        self.active = 0
        self.most = 0
        self._count = threading.Lock()

    def _called(self, operation: str) -> None:
        with self._count:
            self.started += 1
            self.active += 1
            self.most = max(self.most, self.active)
        self.entered.set()
        self.release.wait(timeout=5)
        with self._count:
            self.active -= 1
        super()._called(operation)


@pytest.mark.parametrize("second", ["read", "write", "delete"])
def test_two_instances_never_call_the_backend_at_once(second: str) -> None:
    store: dict[tuple[str, str], str] = {}
    backend = CountingKeyring(store)
    entry, credentials = (KeyringSecrets("dev", backend=backend) for _ in range(2))
    args = ("hunter/api_key", "value") if second == "write" else ("hunter/api_key",)
    first = threading.Thread(target=entry.write, args=("session/open_code", "code"))
    other = threading.Thread(target=getattr(credentials, second), args=args)

    first.start()
    assert backend.entered.wait(timeout=5)
    other.start()
    other.join(timeout=0.3)
    blocked = other.is_alive()  # waiting for the module lock while the first call runs
    started_before_release = backend.started
    backend.release.set()
    first.join(timeout=5)
    other.join(timeout=5)

    assert blocked and started_before_release == 1
    assert backend.most == 1
    assert backend.started >= 2
    assert store[("nextron-engine/dev/session/open_code", "session/open_code")] == "code"


def test_an_injected_backend_keeps_its_own_persistence() -> None:
    backend = DictKeyring()

    KeyringSecrets("dev", backend=backend)

    assert not hasattr(backend, "persist") and "_persist" not in vars(backend)


# One Credential Manager target per secret --------------------------------------------------------


class VaultInMemory(WinVaultKeyring):
    """``WinVaultKeyring``'s own ``get_password``, ``set_password`` and ``delete_password`` (its
    target layout: a value at ``service``, a displaced one moved to ``<user>@<service>``) over
    an in-memory credential table instead of ``win32cred``. ``changed`` records every target
    written or deleted."""

    get_password = WIN_VAULT_METHODS["get_password"]
    set_password = WIN_VAULT_METHODS["set_password"]
    delete_password = WIN_VAULT_METHODS["delete_password"]

    def __init__(self) -> None:
        super().__init__()
        self.targets: dict[str, tuple[str, str]] = {}  # target -> (user name, value)
        self.changed: list[str] = []

    def _read_credential(self, target: str) -> DecodingCredential | None:
        found = self.targets.get(target)
        if found is None:
            return None
        user, value = found
        return DecodingCredential({"UserName": user, "CredentialBlob": value.encode("utf-16-le")})

    def _set_password(self, target: str, username: str, password: str) -> None:
        self.changed.append(target)
        self.targets[target] = (username, password)

    def _delete_password(self, target: str) -> None:
        self.changed.append(target)
        self.targets.pop(target, None)

    def legacy_write(self, service: str, name: str, value: str) -> None:
        """Write as the adapter did before Story 1.9: every secret under one service."""
        self.set_password(service, name, value)
        self.changed.clear()


def own_targets(name: str, env: str = "dev") -> set[str]:
    """Every target that may hold ``name``: its own, keyring's copy of it, and the legacy
    ones."""
    service = f"nextron-engine/{env}"
    return {
        f"{service}/{name}",
        f"{name}@{service}/{name}",
        service,  # the shared legacy target, which held the last one written
        f"{name}@{service}",
    }


def test_a_secret_lives_at_its_own_target_with_its_name_as_the_user() -> None:
    vault = VaultInMemory()
    secrets = KeyringSecrets("dev", backend=vault)

    secrets.write("session/open_code", "code")
    secrets.write("hunter/api_key", "key")

    assert vault.targets == {
        "nextron-engine/dev/session/open_code": ("session/open_code", "code"),
        "nextron-engine/dev/hunter/api_key": ("hunter/api_key", "key"),
    }
    assert secrets.target("hunter/api_key") == "nextron-engine/dev/hunter/api_key"
    assert secrets.read("session/open_code") == "code"
    assert secrets.read("hunter/api_key") == "key"


def test_a_write_drops_keyrings_copy_of_the_previous_value() -> None:
    vault = VaultInMemory()
    secrets = KeyringSecrets("dev", backend=vault)

    secrets.write("hunter/api_key", "first")
    secrets.write("hunter/api_key", "second")

    # set_password moved "first" to hunter/api_key@<target>; the adapter deleted that copy
    assert vault.targets == {"nextron-engine/dev/hunter/api_key": ("hunter/api_key", "second")}
    assert secrets.read("hunter/api_key") == "second"


def test_writing_a_touches_only_as_targets() -> None:
    vault = VaultInMemory()
    secrets = KeyringSecrets("dev", backend=vault)
    vault.legacy_write("nextron-engine/dev", "b", "legacy-b")  # b holds the shared target
    secrets.write("c", "new-c")
    secrets.write("a", "first")
    before = {target: held for target, held in vault.targets.items() if held[0] != "a"}
    vault.changed.clear()

    secrets.write("a", "second")

    assert set(vault.changed) <= own_targets("a")
    assert {target: held for target, held in vault.targets.items() if held[0] != "a"} == before
    assert secrets.read("b") == "legacy-b"
    assert secrets.read("c") == "new-c"
    assert secrets.read("a") == "second"


def test_a_legacy_entry_moves_to_its_own_target_on_first_read() -> None:
    vault = VaultInMemory()
    vault.legacy_write("nextron-engine/dev", "a", "A")
    vault.legacy_write("nextron-engine/dev", "b", "B")  # moves a to a@nextron-engine/dev
    assert vault.targets == {
        "a@nextron-engine/dev": ("a", "A"),
        "nextron-engine/dev": ("b", "B"),
    }
    secrets = KeyringSecrets("dev", backend=vault)

    assert secrets.read("a") == "A"
    assert vault.targets == {
        "nextron-engine/dev/a": ("a", "A"),
        "nextron-engine/dev": ("b", "B"),  # b's legacy entry is untouched
    }
    assert secrets.read("b") == "B"
    assert vault.targets == {
        "nextron-engine/dev/a": ("a", "A"),
        "nextron-engine/dev/b": ("b", "B"),
    }
    vault.changed.clear()

    assert (secrets.read("a"), secrets.read("b"), secrets.read("missing")) == ("A", "B", None)
    assert vault.changed == []  # moved once; later reads change nothing


def test_a_legacy_entry_of_another_environment_is_never_moved() -> None:
    vault = VaultInMemory()
    vault.legacy_write("nextron-engine/prod", "a", "prod-A")

    assert KeyringSecrets("dev", backend=vault).read("a") is None
    assert vault.targets == {"nextron-engine/prod": ("a", "prod-A")}


def test_delete_removes_both_layouts_and_nothing_else() -> None:
    vault = VaultInMemory()
    vault.legacy_write("nextron-engine/dev", "a", "legacy-A")
    vault.legacy_write("nextron-engine/dev", "b", "legacy-B")
    secrets = KeyringSecrets("dev", backend=vault)
    vault.targets["nextron-engine/dev/a"] = ("a", "new-A")  # a move that never finished

    secrets.delete("a")

    assert vault.targets == {"nextron-engine/dev": ("b", "legacy-B")}
    assert secrets.read("a") is None
    assert secrets.read("b") == "legacy-B"
    secrets.delete("a")  # nothing left: does nothing


def test_writing_over_a_legacy_entry_deletes_it() -> None:
    vault = VaultInMemory()
    vault.legacy_write("nextron-engine/dev", "a", "legacy-A")
    secrets = KeyringSecrets("dev", backend=vault)

    secrets.write("a", "new-A")

    assert vault.targets == {"nextron-engine/dev/a": ("a", "new-A")}

