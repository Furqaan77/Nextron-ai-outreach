"""The doctor check ``erasure_ledger`` (Story 3.16): ok when the ledger exists and is writable;
fail when it is missing while the database exists, or not writable; warn when it is missing
before the first engine start. It never opens the file."""

import builtins
import os
from pathlib import Path

import pytest

from nextron import startup_checks
from nextron.domain.readiness import Status
from nextron.startup_checks import ERASURE_LEDGER, check_erasure_ledger


@pytest.fixture
def never_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the check must not open the ledger")

    monkeypatch.setattr(builtins, "open", refuse)


def test_a_present_writable_ledger_is_ok(tmp_path: Path, never_opened: None) -> None:
    ledger = tmp_path / "erasure-ledger.jsonl"
    ledger.touch()

    found = check_erasure_ledger(ledger, lambda: True)

    assert (found.id, found.status) == (ERASURE_LEDGER, Status.OK)
    assert found.message == f"erasure ledger {ledger} exists and is writable"


def test_a_ledger_that_is_not_writable_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "erasure-ledger.jsonl"
    ledger.touch()
    asked: list[tuple[object, int]] = []

    def access(path: object, mode: int) -> bool:
        asked.append((path, mode))
        return False

    monkeypatch.setattr(startup_checks.os, "access", access)

    found = check_erasure_ledger(ledger, lambda: True)

    assert asked == [(ledger, os.W_OK)]
    assert found.status == Status.FAIL
    assert "is not writable" in found.message


def test_a_missing_ledger_fails_once_there_is_a_database(tmp_path: Path, never_opened: None) -> None:
    ledger = tmp_path / "erasure-ledger.jsonl"

    found = check_erasure_ledger(ledger, lambda: True)

    assert found.status == Status.FAIL
    assert found.message == (
        f"erasure ledger {ledger} not found; start the engine to create it, or after a restore "
        "put the ledger's copy there (recovery mode holds sending until it is reapplied)"
    )
    assert not ledger.exists()


def test_a_missing_ledger_only_warns_before_the_first_start(tmp_path: Path) -> None:
    ledger = tmp_path / "erasure-ledger.jsonl"

    found = check_erasure_ledger(ledger, lambda: False)

    assert found.status == Status.WARN
    assert found.message == (
        f"erasure ledger {ledger} not found yet; it is created at the first engine start"
    )


def test_a_database_check_that_raises_fails_by_type(tmp_path: Path) -> None:
    def broken() -> bool:
        raise OSError("no")

    found = check_erasure_ledger(tmp_path / "erasure-ledger.jsonl", broken)

    assert found.status == Status.FAIL
    assert found.message.endswith("(OSError)")
