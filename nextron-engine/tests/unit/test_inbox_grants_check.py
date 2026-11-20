"""Story 6.2's doctor check ``sending_inbox_grants`` (``check_sending_inbox_grants``, AD-12,
OQ-A2): the gate setting's warning, no connected inbox, each inbox's problem by id, Google
unreachable as a warning, both forms' ``ok``, read failures and crashes; no message holds an
address."""

from collections.abc import Sequence

import pytest

from nextron.startup_checks import (
    SENDING_INBOX_GRANTS,
    CheckResult,
    Status,
    check_sending_inbox_grants,
)

A = ("01JD3QZ8X5V9K2M7N4P6R8T0E1", "furqaan@nextron-mail.com")
B = ("01JD3QZ8X5V9K2M7N4P6R8T0E2", "mac@nextron-mail.com")


def run(
    *,
    confirmed: bool = True,
    inboxes: Sequence[tuple[str, str]] = (A, B),
    problems: dict[str, str | None] | None = None,
    network: bool = True,
) -> CheckResult:
    found = problems or {}
    result = check_sending_inbox_grants(
        lambda: confirmed, lambda: inboxes, lambda address: found.get(address), network=network
    )
    assert result.id == SENDING_INBOX_GRANTS
    assert "@" not in result.message
    return result


def test_the_gate_off_warns_whatever_the_inboxes() -> None:
    result = run(confirmed=False, problems={A[1]: "not_stored"})
    assert result.status is Status.WARN
    assert "gmail_internal_app_confirmed is false" in result.message
    assert "Story 6.1" in result.message


def test_no_connected_inbox_is_ok() -> None:
    assert run(inboxes=()) == CheckResult(
        SENDING_INBOX_GRANTS, Status.OK, "no Sending Inbox is connected"
    )


def test_each_inbox_problem_fails_by_id() -> None:
    result = run(problems={A[1]: "invalid_grant", B[1]: "different_account"})
    assert result.status is Status.FAIL
    assert f"{A[0]}: invalid_grant" in result.message
    assert f"{B[0]}: different_account" in result.message
    assert "reconnect each one on the Sending Inboxes page" in result.message


def test_google_unreachable_only_warns() -> None:
    result = run(problems={A[1]: "unreachable"})
    assert result.status is Status.WARN and A[0] in result.message


def test_both_forms_say_ok() -> None:
    assert run(network=True).message == (
        "2 Sending Inbox grant(s) refreshed and each Gmail profile matches"
    )
    assert run(network=False).message == (
        "2 Sending Inbox grant(s) stored; the health panel doesn't refresh them "
        "(nextron doctor does)"
    )


def test_a_read_failure_fails_and_a_crash_is_that_inboxs_problem() -> None:
    def broken() -> bool:
        raise OSError("disk")

    result = check_sending_inbox_grants(broken, lambda: [A], lambda address: None, network=True)
    assert result.status is Status.FAIL and "OSError" in result.message

    def crashes(address: str) -> str | None:
        raise ValueError(address)

    result = check_sending_inbox_grants(lambda: True, lambda: [A], crashes, network=True)
    assert result.status is Status.FAIL
    assert "check crashed (ValueError)" in result.message and "@" not in result.message


@pytest.mark.parametrize("network", [True, False])
def test_without_a_checker_it_warns(network: bool) -> None:
    result = check_sending_inbox_grants(lambda: True, lambda: [A], None, network=network)
    assert result.status is Status.WARN
