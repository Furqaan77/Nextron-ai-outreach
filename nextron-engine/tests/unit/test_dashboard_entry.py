"""``DashboardEntry`` (``nextron.app.ops.api``): issuing, withdrawing and consuming the one-time
code through ``SecretsPort`` (AD-13)."""

import json
import re
import threading
import time
from datetime import timedelta

import pytest

from nextron.app.ops.api import OPEN_CODE_SECRET, DashboardEntry, new_open_code
from nextron.domain.open_code import CodeCheck, encode
from nextron.ports.errors import AdapterError
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets

CODE = "C" * 43
KEY = ("nextron-engine/test", OPEN_CODE_SECRET)


@pytest.fixture
def secrets() -> InMemorySecrets:
    return InMemorySecrets()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def entry(secrets: InMemorySecrets, clock: FrozenClock) -> DashboardEntry:
    return DashboardEntry(secrets, clock, new_code=lambda: CODE)


def test_the_secret_name() -> None:
    assert OPEN_CODE_SECRET == "session/open_code"


def test_the_default_code_is_256_bits_of_base64url() -> None:
    codes = {new_open_code() for _ in range(50)}

    assert len(codes) == 50
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", code) for code in codes)


# Row: open, engine up
def test_issue_code_stores_the_code_with_its_issue_time(
    entry: DashboardEntry, secrets: InMemorySecrets, clock: FrozenClock
) -> None:
    code = entry.issue_code()

    assert code == CODE
    assert secrets.store == {KEY: encode(CODE, clock.now())}
    assert json.loads(secrets.store[KEY])["issued_at"] == "2026-11-16T09:00:00.000000Z"


def test_issue_code_replaces_a_pending_code(secrets: InMemorySecrets, clock: FrozenClock) -> None:
    entry = DashboardEntry(secrets, clock)

    first = entry.issue_code()
    second = entry.issue_code()

    assert first != second
    assert entry.consume_code(first) is CodeCheck.MISMATCH
    assert entry.consume_code(second) is CodeCheck.ACCEPTED


def test_issue_code_lets_an_adapter_error_propagate(
    entry: DashboardEntry, secrets: InMemorySecrets
) -> None:
    secrets.failing.add("write")

    with pytest.raises(AdapterError) as caught:
        entry.issue_code()

    assert caught.value.code == "keyring_failed"
    assert secrets.store == {}


def test_withdraw_code_deletes_the_entry(entry: DashboardEntry, secrets: InMemorySecrets) -> None:
    entry.issue_code()

    entry.withdraw_code()

    assert secrets.store == {}
    assert entry.consume_code(CODE) is CodeCheck.ABSENT


# Row: enter
def test_an_accepted_code_is_deleted_and_works_once(
    entry: DashboardEntry, secrets: InMemorySecrets, clock: FrozenClock
) -> None:
    entry.issue_code()
    clock.advance(timedelta(minutes=5))

    assert entry.consume_code(CODE) is CodeCheck.ACCEPTED
    assert secrets.store == {}
    assert entry.consume_code(CODE) is CodeCheck.ABSENT


def test_accepted_deletes_before_reporting_and_a_failed_delete_is_unavailable(
    entry: DashboardEntry, secrets: InMemorySecrets
) -> None:
    entry.issue_code()
    secrets.failing.add("delete")

    assert entry.consume_code(CODE) is CodeCheck.UNAVAILABLE
    assert KEY in secrets.store
    assert secrets.calls[-2:] == [("read", OPEN_CODE_SECRET), ("delete", OPEN_CODE_SECRET)]


def test_a_failed_read_is_unavailable(entry: DashboardEntry, secrets: InMemorySecrets) -> None:
    entry.issue_code()
    secrets.failing.add("read")

    assert entry.consume_code(CODE) is CodeCheck.UNAVAILABLE
    assert KEY in secrets.store


# Row: enter, refused (wrong: code kept)
@pytest.mark.parametrize("presented", ["", "wrong", "C" * 42 + "D", "é" * 43])
def test_a_wrong_code_leaves_the_pending_code_in_place(
    presented: str, entry: DashboardEntry, secrets: InMemorySecrets
) -> None:
    entry.issue_code()
    calls_before = len(secrets.calls)

    assert entry.consume_code(presented) is CodeCheck.MISMATCH
    assert KEY in secrets.store
    assert secrets.calls[calls_before:] == [("read", OPEN_CODE_SECRET)]
    assert entry.consume_code(CODE) is CodeCheck.ACCEPTED


def test_absent_deletes_nothing(entry: DashboardEntry, secrets: InMemorySecrets) -> None:
    assert entry.consume_code(CODE) is CodeCheck.ABSENT
    assert secrets.calls == [("read", OPEN_CODE_SECRET)]


@pytest.mark.parametrize("delete_fails", [False, True])
def test_an_expired_code_is_deleted_on_a_best_effort_basis(
    delete_fails: bool, entry: DashboardEntry, secrets: InMemorySecrets, clock: FrozenClock
) -> None:
    entry.issue_code()
    clock.advance(timedelta(minutes=5, microseconds=1))
    if delete_fails:
        secrets.failing.add("delete")

    assert entry.consume_code(CODE) is CodeCheck.EXPIRED
    assert secrets.calls[-1] == ("delete", OPEN_CODE_SECRET)
    assert (KEY in secrets.store) is delete_fails


def test_a_future_dated_code_is_expired_and_deleted(
    entry: DashboardEntry, secrets: InMemorySecrets, clock: FrozenClock
) -> None:
    entry.issue_code()
    clock.advance(timedelta(microseconds=-1))

    assert entry.consume_code(CODE) is CodeCheck.EXPIRED
    assert secrets.store == {}


@pytest.mark.parametrize("delete_fails", [False, True])
def test_a_malformed_entry_is_deleted_on_a_best_effort_basis(
    delete_fails: bool, entry: DashboardEntry, secrets: InMemorySecrets
) -> None:
    secrets.write(OPEN_CODE_SECRET, '{"code":"x"}')
    if delete_fails:
        secrets.failing.add("delete")

    assert entry.consume_code("x") is CodeCheck.MALFORMED
    assert (KEY in secrets.store) is delete_fails


class SlowSecrets(InMemorySecrets):
    """Reads slowly, so two unguarded consumers would both see the code."""

    def read(self, name: str) -> str | None:
        value = super().read(name)
        time.sleep(0.05)
        return value


# Row: race (two threads consume one code)
def test_two_threads_consuming_one_code_get_exactly_one_accepted(clock: FrozenClock) -> None:
    entry = DashboardEntry(SlowSecrets(), clock, new_code=lambda: CODE)
    entry.issue_code()
    barrier = threading.Barrier(2)
    results: list[CodeCheck] = []

    def consume() -> None:
        barrier.wait()
        results.append(entry.consume_code(CODE))

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == sorted([CodeCheck.ACCEPTED, CodeCheck.ABSENT])
