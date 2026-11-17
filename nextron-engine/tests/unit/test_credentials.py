"""The credential registry, the meta times, ``read_credential`` and the ``Credentials`` service
(AD-12, FR-56, NFR-18) at service level: the Save, Replace, Refused, Remove, Store fails, Meta
drift and Adapter read rows, with ``InMemorySecrets`` and a ``FrozenClock``."""

import logging
import threading
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from nextron.app.setup.api import (
    CredentialState,
    CredentialStatus,
    CredentialStoreFailed,
    Credentials,
)
from nextron.domain import credentials as registry
from nextron.domain.credentials import (
    REGISTRY,
    Credential,
    CredentialKey,
    InvalidCredentialValue,
    UnknownCredential,
    credential,
    decode_updated_at,
    declared,
    encode_updated_at,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET, KEYRING_FAILED, read_credential
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

T = datetime(2026, 11, 17, 9, 30, 0, tzinfo=UTC)
T_TEXT = "2026-11-17T09:30:00.000000Z"
T2 = datetime(2026, 11, 17, 10, 45, 12, 345678, tzinfo=UTC)
T2_TEXT = "2026-11-17T10:45:12.345678Z"
SERVICE = "nextron-engine/test"
MARK = "QZXVALUE7"  # a value no message, record or status may carry
NON_BMP = "\U0001f511"  # two UTF-16 code units

# The story's registry table, verbatim: key, label, keyring name, required.
TABLE = [
    ("claude", "Claude API key", "anthropic/api_key", True),
    ("google_places", "Google Places API key", "google_places/api_key", False),
    ("hunter", "Hunter API key", "hunter/api_key", True),
    ("prospeo", "Prospeo API key", "prospeo/api_key", False),
    ("companies_house", "Companies House API key", "companies_house/api_key", True),
    ("telegram_bot", "Telegram bot token", "telegram/bot_token", True),
    ("unsubscribe_bearer", "Unsubscribe Endpoint bearer secret", "unsub/bearer", True),
    ("backup_passphrase", "Backup passphrase", "backup/passphrase", False),
    ("job_aggregator", "Job aggregator API key", "job_aggregator/api_key", False),
]


@pytest.fixture
def secrets() -> InMemorySecrets:
    return InMemorySecrets("test")


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T)


@pytest.fixture
def service(secrets: InMemorySecrets, clock: FrozenClock) -> Credentials:
    return Credentials(secrets, clock)


def stored(secrets: InMemorySecrets) -> dict[str, str]:
    return {name: value for (service, name), value in secrets.store.items() if service == SERVICE}


def put(secrets: InMemorySecrets, name: str, value: str) -> None:
    secrets.store[(SERVICE, name)] = value


def writes_and_deletes(secrets: InMemorySecrets) -> list[tuple[str, str]]:
    return [call for call in secrets.calls if call[0] in ("write", "delete")]


def events(caplog: pytest.LogCaptureFixture) -> list[tuple[str, dict[str, object]]]:
    """The service's log records: each event with its ``credential``, ``operation``, ``entry``
    and ``code`` fields."""
    fields = ("credential", "operation", "entry", "code")
    return [
        (record.getMessage(), {name: vars(record)[name] for name in fields if name in vars(record)})
        for record in caplog.records
        if record.name == "nextron.app.setup.api"
    ]


def assert_never_logged(caplog: pytest.LogCaptureFixture, value: str) -> None:
    for record in caplog.records:
        assert value not in record.getMessage()
        assert value not in str(vars(record))


# The registry --------------------------------------------------------------------------------


def test_the_registry_is_the_storys_table_in_order() -> None:
    assert [
        (str(found.key), found.label, found.secret_name, found.required) for found in declared()
    ] == TABLE
    assert list(REGISTRY) == [key for key, *_ in TABLE]
    assert [member.value for member in CredentialKey] == [key for key, *_ in TABLE]
    assert all(isinstance(found.key, CredentialKey) for found in declared())


def test_the_required_credentials_are_the_ones_the_story_names() -> None:
    assert [found.label for found in declared() if found.required] == [
        "Claude API key",
        "Hunter API key",
        "Companies House API key",
        "Telegram bot token",
        "Unsubscribe Endpoint bearer secret",
    ]


def test_each_meta_name_is_meta_slash_the_keyring_name() -> None:
    assert credential("hunter").meta_name == "meta/hunter/api_key"
    assert all(found.meta_name == f"meta/{found.secret_name}" for found in declared())


def test_credential_finds_a_key_by_member_or_text() -> None:
    assert credential(CredentialKey.HUNTER) is credential("hunter") is REGISTRY["hunter"]


@pytest.mark.parametrize(
    ("key", "shown"),
    [
        ("nope", "unknown credential: nope"),
        ("Hunter", "unknown credential: (not a credential name)"),
        (f"hunter {MARK}", "unknown credential: (not a credential name)"),
        (None, "unknown credential: (not a credential name)"),
        (["hunter"], "unknown credential: (not a credential name)"),
    ],
)
def test_an_undeclared_key_raises_unknown_credential(key: object, shown: str) -> None:
    with pytest.raises(UnknownCredential) as raised:
        credential(key)  # type: ignore[arg-type]

    assert raised.value.code == "unknown_credential"
    assert str(raised.value) == shown


def test_credential_and_declared_read_the_registry_when_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_credential = Credential(CredentialKey.HUNTER, "Probe key", "probe/api_key", False)
    monkeypatch.setattr(registry, "REGISTRY", MappingProxyType({"hunter": probe_credential}))

    assert declared() == (probe_credential,)
    assert credential("hunter") is probe_credential
    with pytest.raises(UnknownCredential):
        credential("claude")


def test_invalid_credential_value_names_the_key_and_reason_only() -> None:
    error = InvalidCredentialValue(CredentialKey.HUNTER, "enter a value")

    assert (error.code, error.key, error.reason) == (
        "invalid_credential",
        "hunter",
        "enter a value",
    )
    assert str(error) == "hunter: enter a value"


def test_declaring_a_bad_registry_raises_type_error() -> None:
    claude = credential("claude")

    with pytest.raises(TypeError, match="declared twice"):
        registry._declare(*declared(), claude)
    with pytest.raises(TypeError, match="not declared: job_aggregator"):
        registry._declare(*declared()[:-1])
    extra = Credential("extra", "Extra", "extra/key", False)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="not a CredentialKey member"):
        registry._declare(*declared(), extra)


# Times ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("at", "text"),
    [
        (T, T_TEXT),
        (T2, T2_TEXT),
        # another offset is written in UTC
        (datetime(2026, 11, 17, 1, 30, tzinfo=timezone(timedelta(hours=-8))), T_TEXT),
        (datetime(1, 1, 1, tzinfo=UTC), "0001-01-01T00:00:00.000000Z"),
    ],
)
def test_encode_updated_at_writes_utc_with_microseconds(at: datetime, text: str) -> None:
    assert encode_updated_at(at) == text
    assert decode_updated_at(text) == at
    assert decode_updated_at(text).tzinfo is UTC  # type: ignore[union-attr]


def test_encode_updated_at_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        encode_updated_at(datetime(2026, 11, 17, 9, 30))


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "garbage",
        "2026-11-17T09:30:00Z",
        "2026-11-17T09:30:00.000000",
        "2026-11-17T09:30:00.000000+00:00",
        " 2026-11-17T09:30:00.000000Z",
        "2026-11-17T09:30:00.000000Z\n",
        "2026-02-30T09:30:00.000000Z",  # no such date
        "2026-11-17T24:30:00.000000Z",
        "２０２６-11-17T09:30:00.000000Z",  # fullwidth digits
        20261117,
    ],
)
def test_decode_updated_at_returns_none_for_anything_else(text: object) -> None:
    assert decode_updated_at(text) is None


# read_credential (row: Adapter read) -------------------------------------------------------------


def test_read_credential_returns_a_set_value(secrets: InMemorySecrets) -> None:
    put(secrets, "hunter/api_key", MARK)

    assert read_credential(secrets, CredentialKey.HUNTER) == MARK
    assert read_credential(secrets, "hunter") == MARK
    assert secrets.calls == [("read", "hunter/api_key"), ("read", "hunter/api_key")]


def test_read_credential_raises_credential_not_set_for_an_unset_key(
    secrets: InMemorySecrets,
) -> None:
    put(secrets, "meta/hunter/api_key", T_TEXT)  # a time alone doesn't make it set

    with pytest.raises(AdapterError) as raised:
        read_credential(secrets, CredentialKey.HUNTER)

    error = raised.value
    assert (error.retryable, error.code, error.message) == (
        False,
        CREDENTIAL_NOT_SET,
        "credential hunter is not set",
    )


def test_read_credential_refuses_an_undeclared_key_before_any_read(
    secrets: InMemorySecrets,
) -> None:
    with pytest.raises(UnknownCredential):
        read_credential(secrets, "nope")

    assert secrets.calls == []


def test_read_credential_passes_on_a_failed_read(secrets: InMemorySecrets) -> None:
    secrets.failing = {"read"}

    with pytest.raises(AdapterError) as raised:
        read_credential(secrets, CredentialKey.HUNTER)

    assert raised.value.code == KEYRING_FAILED


# Row: Save and Replace ------------------------------------------------------------------------


def test_save_strips_the_value_and_writes_it_then_its_time(
    service: Credentials, secrets: InMemorySecrets, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    status = service.save(CredentialKey.CLAUDE, "  abc  ")

    assert stored(secrets) == {"anthropic/api_key": "abc", "meta/anthropic/api_key": T_TEXT}
    assert writes_and_deletes(secrets) == [
        ("write", "anthropic/api_key"),
        ("write", "meta/anthropic/api_key"),
    ]
    assert status == CredentialStatus(credential("claude"), CredentialState.SET, T)
    assert service.status("claude") == status
    assert events(caplog) == [("credential_saved", {"credential": "claude"})]
    assert_never_logged(caplog, "abc")


def test_saving_again_replaces_the_value_and_the_time(
    service: Credentials, secrets: InMemorySecrets, clock: FrozenClock
) -> None:
    service.save("claude", "abc")
    clock.set(T2)

    status = service.save("claude", "xyz")

    assert stored(secrets) == {"anthropic/api_key": "xyz", "meta/anthropic/api_key": T2_TEXT}
    assert status.updated_at == T2
    assert service.status("claude").updated_at == T2


def test_save_reads_the_clock_once(secrets: InMemorySecrets) -> None:
    class CountingClock(FrozenClock):
        reads = 0

        def now(self) -> datetime:
            self.reads += 1
            return super().now()

    clock = CountingClock(T)

    Credentials(secrets, clock).save("hunter", MARK)

    assert clock.reads == 1


def test_save_touches_only_its_own_credential(
    service: Credentials, secrets: InMemorySecrets
) -> None:
    service.save("claude", "abc")

    service.save("hunter", MARK)

    assert stored(secrets) == {
        "anthropic/api_key": "abc",
        "meta/anthropic/api_key": T_TEXT,
        "hunter/api_key": MARK,
        "meta/hunter/api_key": T_TEXT,
    }


# Row: Refused ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "enter a value"),
        ("   ", "enter a value"),
        ("\t\r\n 　", "enter a value"),
        ("x" * 1281, "secret exceeds 1,280 characters"),
        (NON_BMP * 641, "secret exceeds 1,280 characters"),  # 1,282 UTF-16 units
        ("x" * 1279 + NON_BMP, "secret exceeds 1,280 characters"),  # 1,281 units
        (f"  {MARK}{'y' * 1280}  ", "secret exceeds 1,280 characters"),
    ],
)
def test_a_blank_or_too_long_value_is_refused_and_nothing_is_written(
    text: str,
    reason: str,
    service: Credentials,
    secrets: InMemorySecrets,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    service.save("hunter", "kept")
    secrets.calls.clear()

    with pytest.raises(InvalidCredentialValue) as raised:
        service.save("hunter", text)

    assert (raised.value.key, raised.value.reason) == ("hunter", reason)
    assert str(raised.value) == f"hunter: {reason}"
    assert secrets.calls == []
    assert stored(secrets)["hunter/api_key"] == "kept"  # a blank value never deletes anything
    assert_never_logged(caplog, MARK)


@pytest.mark.parametrize(
    "text",
    ["x" * 1280, NON_BMP * 640, f"  {'x' * 1280}  ", "x" * 1278 + NON_BMP],
    ids=["1280 ascii", "640 non-BMP", "1280 after stripping", "1278 + one non-BMP"],
)
def test_exactly_1280_units_after_stripping_is_accepted(
    text: str, service: Credentials, secrets: InMemorySecrets
) -> None:
    service.save("hunter", text)

    assert stored(secrets)["hunter/api_key"] == text.strip()


def test_a_value_that_isnt_text_is_refused(
    service: Credentials, secrets: InMemorySecrets
) -> None:
    with pytest.raises(InvalidCredentialValue):
        service.save("hunter", None)  # type: ignore[arg-type]

    assert secrets.calls == []


@pytest.mark.parametrize("method", ["status", "save", "remove"])
def test_an_undeclared_key_is_refused_before_any_keyring_call(
    method: str, service: Credentials, secrets: InMemorySecrets
) -> None:
    arguments = ("nope", "abc") if method == "save" else ("nope",)

    with pytest.raises(UnknownCredential):
        getattr(service, method)(*arguments)

    assert secrets.calls == []


# Row: Remove ----------------------------------------------------------------------------------


def test_remove_deletes_the_entry_then_its_time(
    service: Credentials, secrets: InMemorySecrets, caplog: pytest.LogCaptureFixture
) -> None:
    service.save("hunter", MARK)
    service.save("claude", "abc")
    secrets.calls.clear()
    caplog.set_level(logging.INFO)

    status = service.remove("hunter")

    assert writes_and_deletes(secrets) == [
        ("delete", "hunter/api_key"),
        ("delete", "meta/hunter/api_key"),
    ]
    assert stored(secrets) == {"anthropic/api_key": "abc", "meta/anthropic/api_key": T_TEXT}
    assert status == CredentialStatus(credential("hunter"), CredentialState.NOT_SET, None)
    assert service.status("hunter") == status
    assert events(caplog) == [("credential_removed", {"credential": "hunter"})]


def test_removing_a_credential_that_isnt_set_does_nothing_harmful(
    service: Credentials, secrets: InMemorySecrets
) -> None:
    assert service.remove("hunter").state is CredentialState.NOT_SET
    assert stored(secrets) == {}


# Row: Store fails -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failing", "method", "operation"),
    [("write", "save", "save"), ("delete", "remove", "remove")],
)
def test_a_failed_save_or_remove_logs_and_raises_credential_store_failed(
    failing: str,
    method: str,
    operation: str,
    service: Credentials,
    secrets: InMemorySecrets,
    caplog: pytest.LogCaptureFixture,
) -> None:
    put(secrets, "hunter/api_key", MARK)
    put(secrets, "meta/hunter/api_key", T_TEXT)
    secrets.failing = {failing}
    caplog.set_level(logging.INFO)
    arguments = ("hunter", "new value") if method == "save" else ("hunter",)

    with pytest.raises(CredentialStoreFailed) as raised:
        getattr(service, method)(*arguments)

    assert raised.value.key == "hunter"
    assert MARK not in str(raised.value) and "new value" not in str(raised.value)
    assert raised.value.__suppress_context__
    assert events(caplog) == [
        (
            "credential_store_failed",
            {"credential": "hunter", "operation": operation, "code": KEYRING_FAILED},
        )
    ]
    assert stored(secrets) == {"hunter/api_key": MARK, "meta/hunter/api_key": T_TEXT}
    assert service.status("hunter") == CredentialStatus(
        credential("hunter"), CredentialState.SET, T
    )
    assert_never_logged(caplog, MARK)
    assert_never_logged(caplog, "new value")


class FailingMetaWrites(InMemorySecrets):
    """Credential Manager that stores the value but fails on the ``meta/`` entry."""

    def write(self, name: str, value: str) -> None:
        if name.startswith("meta/"):
            raise AdapterError(False, KEYRING_FAILED, f"write of secret {name} failed")
        super().write(name, value)


def test_a_failed_meta_write_leaves_the_value_set_with_an_unknown_time(
    clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    secrets = FailingMetaWrites("test")
    service = Credentials(secrets, clock)
    caplog.set_level(logging.INFO)

    with pytest.raises(CredentialStoreFailed):
        service.save("hunter", MARK)

    assert stored(secrets) == {"hunter/api_key": MARK}
    assert service.status("hunter") == CredentialStatus(
        credential("hunter"), CredentialState.SET, None
    )
    assert [message for message, _ in events(caplog)] == ["credential_store_failed"]


def test_a_failing_read_shows_every_credential_as_unknown_and_logs_each(
    service: Credentials, secrets: InMemorySecrets, caplog: pytest.LogCaptureFixture
) -> None:
    put(secrets, "hunter/api_key", MARK)
    secrets.failing = {"read"}
    caplog.set_level(logging.INFO)

    statuses = service.statuses()

    assert [(str(s.credential.key), s.state, s.updated_at) for s in statuses] == [
        (key, CredentialState.UNKNOWN, None) for key, *_ in TABLE
    ]
    assert events(caplog) == [
        (
            "credential_read_failed",
            {"credential": key, "entry": "value", "code": KEYRING_FAILED},
        )
        for key, *_ in TABLE
    ]
    assert [r.levelno for r in caplog.records] == [logging.WARNING] * len(TABLE)
    assert_never_logged(caplog, MARK)


class FailingMetaReads(InMemorySecrets):
    def read(self, name: str) -> str | None:
        if name.startswith("meta/"):
            raise AdapterError(False, KEYRING_FAILED, f"read of secret {name} failed")
        return super().read(name)


def test_an_unreadable_meta_entry_leaves_the_credential_set_with_an_unknown_time(
    clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    secrets = FailingMetaReads("test")
    put(secrets, "hunter/api_key", MARK)
    put(secrets, "meta/hunter/api_key", T_TEXT)
    caplog.set_level(logging.INFO)

    status = Credentials(secrets, clock).status("hunter")

    assert (status.state, status.updated_at) == (CredentialState.SET, None)
    assert events(caplog) == [
        (
            "credential_read_failed",
            {"credential": "hunter", "entry": "meta", "code": KEYRING_FAILED},
        )
    ]
    assert_never_logged(caplog, MARK)


# Row: Meta drift ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "state", "updated_at"),
    [
        ({"hunter/api_key": MARK}, CredentialState.SET, None),  # meta missing
        ({"hunter/api_key": MARK, "meta/hunter/api_key": "garbage"}, CredentialState.SET, None),
        ({"meta/hunter/api_key": T_TEXT}, CredentialState.NOT_SET, None),  # meta, no entry
        ({}, CredentialState.NOT_SET, None),
        ({"hunter/api_key": MARK, "meta/hunter/api_key": T_TEXT}, CredentialState.SET, T),
    ],
    ids=["meta missing", "meta garbage", "meta without entry", "nothing", "both"],
)
def test_the_state_comes_from_the_entry_and_only_the_time_from_meta(
    entries: dict[str, str],
    state: CredentialState,
    updated_at: datetime | None,
    service: Credentials,
    secrets: InMemorySecrets,
) -> None:
    for name, value in entries.items():
        put(secrets, name, value)

    status = service.status("hunter")

    assert (status.state, status.updated_at) == (state, updated_at)
    if state is CredentialState.NOT_SET:  # the time is read only for a set credential
        assert secrets.calls == [("read", "hunter/api_key")]


def test_statuses_lists_every_credential_in_registry_order(
    service: Credentials, secrets: InMemorySecrets
) -> None:
    service.save("hunter", MARK)

    statuses = service.statuses()

    assert [str(status.credential.key) for status in statuses] == [key for key, *_ in TABLE]
    assert {str(s.credential.key): s.state for s in statuses if s.state is CredentialState.SET} == {
        "hunter": CredentialState.SET
    }
    assert MARK not in repr(statuses)


def test_another_environments_entries_are_not_seen(clock: FrozenClock) -> None:
    store: dict[tuple[str, str], str] = {}
    Credentials(InMemorySecrets("prod", store), clock).save("hunter", MARK)

    assert Credentials(InMemorySecrets("test", store), clock).status("hunter").state is (
        CredentialState.NOT_SET
    )


# One call at a time ---------------------------------------------------------------------------


class BlockingReads(InMemorySecrets):
    """Reads that wait for ``release`` and count how many start and run at once."""

    def __init__(self) -> None:
        super().__init__("test")
        self.entered = threading.Event()
        self.release = threading.Event()
        self.started = 0
        self.active = 0
        self.most = 0
        self._count = threading.Lock()

    def read(self, name: str) -> str | None:
        with self._count:
            self.started += 1
            self.active += 1
            self.most = max(self.most, self.active)
        self.entered.set()
        self.release.wait(timeout=5)
        with self._count:
            self.active -= 1
        return super().read(name)


def test_the_service_runs_one_call_at_a_time(clock: FrozenClock) -> None:
    secrets = BlockingReads()
    service = Credentials(secrets, clock)
    results: list[CredentialStatus] = []
    first = threading.Thread(target=lambda: results.append(service.status("hunter")))
    second = threading.Thread(target=lambda: results.append(service.status("claude")))

    first.start()
    assert secrets.entered.wait(timeout=5)
    second.start()
    second.join(timeout=0.3)
    blocked = second.is_alive()  # waiting for the lock while the first call reads
    started_before_release = secrets.started
    secrets.release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert blocked and started_before_release == 1
    assert secrets.most == 1  # two read-path calls competed, and never overlapped
    assert secrets.started == 2
    assert [str(status.credential.key) for status in results] == ["hunter", "claude"]
