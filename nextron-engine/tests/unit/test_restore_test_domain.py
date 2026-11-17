"""The restore test's rules (``nextron.domain.restore_test``, Story 1.18): the names, kinds and
labels, every ``judge`` branch, ``fetch_failure``, the reason order, the detail cut, the
outcome's own consistency checks, the schedule functions (month and timezone boundaries, the
midnight wrap, ``None``) and the alert text."""

from datetime import UTC, datetime, time, timedelta

import pytest

from nextron.domain.restore_test import (
    DEFAULT_RESTORE_TEST_TIME,
    FETCH_FAILURES,
    MAX_DETAIL_LENGTH,
    RESTORE_DIR_NAME,
    RESTORE_TEST_DELAY,
    RESTORE_TEST_FAILED,
    RESTORE_TEST_JOB,
    CheckOutcome,
    RestoreCheck,
    RestoreFailure,
    RestoreOutcome,
    RestoreResult,
    RestoreTestKind,
    RestoreTestSchedule,
    fetch_failure,
    judge,
    missed_restore_test,
    restore_test_due,
    restore_test_failed_text,
    restore_test_time,
)

HEAD = "0009_1_18_restore_tests"
OLD = "0008_1_17_offpc"
ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
PASS, FAIL, SKIPPED = CheckOutcome.PASS, CheckOutcome.FAIL, CheckOutcome.SKIPPED


def judged(**changes: object) -> RestoreOutcome:
    """``judge`` of a copy at head with 5 suppression and 3 provenance rows, recorded as the
    same, with ``changes`` applied."""
    arguments: dict[str, object] = {
        "integrity": "ok",
        "revision": HEAD,
        "head": HEAD,
        "revision_known": True,
        "counts": (5, 3),
        "recorded": (5, 3),
        **changes,
    }
    return judge(**arguments)  # type: ignore[arg-type]


# Names and labels -------------------------------------------------------------------------------


def test_the_names_and_constants_are_the_specified_ones() -> None:
    assert (RESTORE_TEST_JOB, RESTORE_TEST_FAILED, RESTORE_DIR_NAME) == (
        "restore_test",
        "restore_test_failed",
        "restore_test",
    )
    assert RESTORE_TEST_DELAY == timedelta(hours=1)
    assert DEFAULT_RESTORE_TEST_TIME == time(11, 0)
    assert MAX_DETAIL_LENGTH == 500


def test_the_kinds_results_and_checks_and_their_labels() -> None:
    assert [(kind.value, kind.label) for kind in RestoreTestKind] == [
        ("monthly", "monthly"),
        ("manual", "manual"),
    ]
    assert [result.value for result in RestoreResult] == ["pass", "fail"]
    assert [outcome.value for outcome in CheckOutcome] == ["pass", "fail", "skipped"]
    assert [(check.value, check.label) for check in RestoreCheck] == [
        ("fetch", "fetch and decrypt"),
        ("integrity", "integrity check"),
        ("revision", "Alembic revision"),
        ("suppression", "Suppression List count"),
        ("provenance", "Provenance Records count"),
    ]


def test_the_reasons_and_which_belong_to_the_fetch_check() -> None:
    assert [reason.value for reason in RestoreFailure] == [
        "no_offpc_copy",
        "offpc_not_configured",
        "copy_missing",
        "target_unavailable",
        "passphrase_not_set",
        "wrong_passphrase",
        "tampered",
        "truncated",
        "bad_format",
        "fetch_failed",
        "checksum_mismatch",
        "integrity_failed",
        "revision_behind",
        "revision_unknown",
        "suppression_fewer",
        "provenance_fewer",
    ]
    assert {reason.value for reason in FETCH_FAILURES} == {
        reason.value for reason in list(RestoreFailure)[:11]
    }
    assert all(len(reason.value) <= 32 for reason in RestoreFailure)  # the reason column


# fetch_failure ----------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", sorted(FETCH_FAILURES))
def test_a_fetch_failure_fails_fetch_and_skips_the_rest(reason: RestoreFailure) -> None:
    outcome = fetch_failure(reason)

    assert outcome == RestoreOutcome(
        RestoreResult.FAIL, (FAIL, SKIPPED, SKIPPED, SKIPPED, SKIPPED), reason, None
    )
    assert outcome.failing == (RestoreCheck.FETCH,)
    assert (outcome.copy_revision, outcome.suppression_count, outcome.provenance_count) == (
        None,
        None,
        None,
    )


def test_a_fetch_failure_keeps_its_detail_cut_to_500_characters() -> None:
    assert fetch_failure(RestoreFailure.FETCH_FAILED, "MemoryError").detail == "MemoryError"
    assert fetch_failure(RestoreFailure.FETCH_FAILED, "x" * 600).detail == "x" * 500


@pytest.mark.parametrize(
    "reason",
    [RestoreFailure.INTEGRITY_FAILED, RestoreFailure.REVISION_BEHIND, "no_such_reason"],
)
def test_a_fetch_failure_refuses_any_other_reason(reason: str) -> None:
    with pytest.raises(ValueError):
        fetch_failure(reason)  # type: ignore[arg-type]


# judge ------------------------------------------------------------------------------------------


def test_a_copy_at_head_with_enough_rows_passes() -> None:
    outcome = judged()

    assert outcome == RestoreOutcome(
        RestoreResult.PASS, (PASS, PASS, PASS, PASS, PASS), None, None, HEAD, 5, 3
    )
    assert outcome.failing == ()


def test_more_rows_than_recorded_pass() -> None:
    assert judged(counts=(9, 4)).result is RestoreResult.PASS


@pytest.mark.parametrize(
    ("recorded", "counts", "checks"),
    [
        ((None, 3), (5, 3), (PASS, PASS, PASS, SKIPPED, PASS)),
        ((5, None), (5, None), (PASS, PASS, PASS, PASS, SKIPPED)),
        ((None, None), (None, None), (PASS, PASS, PASS, SKIPPED, SKIPPED)),
        ((None, None), (7, 2), (PASS, PASS, PASS, SKIPPED, SKIPPED)),
    ],
    ids=["suppression absent", "provenance absent", "both absent", "absent then, present now"],
)
def test_a_table_recorded_absent_is_skipped(
    recorded: tuple[int | None, int | None],
    counts: tuple[int | None, int | None],
    checks: tuple[CheckOutcome, ...],
) -> None:
    outcome = judged(recorded=recorded, counts=counts)

    assert (outcome.result, outcome.checks, outcome.reason, outcome.detail) == (
        RestoreResult.PASS,
        checks,
        None,
        None,
    )
    assert (outcome.suppression_count, outcome.provenance_count) == counts


@pytest.mark.parametrize(
    "integrity",
    ["*** in database main ***", "error: DatabaseError", "", "OK", "ok "],
)
def test_any_integrity_answer_but_exactly_ok_fails_and_skips_the_rest(integrity: str) -> None:
    outcome = judged(integrity=integrity, revision=None, counts=(None, None))

    assert outcome == RestoreOutcome(
        RestoreResult.FAIL,
        (PASS, FAIL, SKIPPED, SKIPPED, SKIPPED),
        RestoreFailure.INTEGRITY_FAILED,
        f"integrity_check: {integrity}",
    )


def test_a_failed_integrity_keeps_no_revision_or_count() -> None:
    outcome = judged(integrity="row 3 missing from index")

    assert (outcome.copy_revision, outcome.suppression_count, outcome.provenance_count) == (
        None,
        None,
        None,
    )


def test_a_long_integrity_answer_is_cut_to_500_characters() -> None:
    outcome = judged(integrity="y" * 600)

    assert outcome.detail == ("integrity_check: " + "y" * 600)[:500]
    assert outcome.detail is not None and len(outcome.detail) == 500


def test_a_known_older_revision_is_behind() -> None:
    outcome = judged(revision=OLD, revision_known=True)

    assert (outcome.result, outcome.checks, outcome.reason, outcome.detail) == (
        RestoreResult.FAIL,
        (PASS, PASS, FAIL, PASS, PASS),
        RestoreFailure.REVISION_BEHIND,
        f"copy revision {OLD}; code head {HEAD}",
    )
    assert outcome.copy_revision == OLD
    assert outcome.failing == (RestoreCheck.REVISION,)


@pytest.mark.parametrize(
    ("revision", "known", "shown"),
    [
        ("0099_from_newer_code", False, "0099_from_newer_code"),
        (None, False, "none"),
        (None, True, "none"),
    ],
    ids=["unknown", "none", "none even if called known"],
)
def test_an_unknown_or_missing_revision_is_unknown(
    revision: str | None, known: bool, shown: str
) -> None:
    outcome = judged(revision=revision, revision_known=known)

    assert (outcome.reason, outcome.detail) == (
        RestoreFailure.REVISION_UNKNOWN,
        f"copy revision {shown}; code head {HEAD}",
    )
    assert outcome.checks[2] is FAIL


@pytest.mark.parametrize(
    ("counts", "checks", "reason", "detail"),
    [
        (
            (4, 3),
            (PASS, PASS, PASS, FAIL, PASS),
            RestoreFailure.SUPPRESSION_FEWER,
            "suppression_entries: copy 4, backup 5",
        ),
        (
            (5, 2),
            (PASS, PASS, PASS, PASS, FAIL),
            RestoreFailure.PROVENANCE_FEWER,
            "provenance_records: copy 2, backup 3",
        ),
        (
            (None, 3),
            (PASS, PASS, PASS, FAIL, PASS),
            RestoreFailure.SUPPRESSION_FEWER,
            "suppression_entries: copy absent, backup 5",
        ),
        (
            (5, None),
            (PASS, PASS, PASS, PASS, FAIL),
            RestoreFailure.PROVENANCE_FEWER,
            "provenance_records: copy absent, backup 3",
        ),
    ],
    ids=["fewer suppression", "fewer provenance", "suppression absent", "provenance absent"],
)
def test_a_copy_with_fewer_rows_than_recorded_fails(
    counts: tuple[int | None, int | None],
    checks: tuple[CheckOutcome, ...],
    reason: RestoreFailure,
    detail: str,
) -> None:
    outcome = judged(counts=counts)

    assert (outcome.result, outcome.checks, outcome.reason, outcome.detail) == (
        RestoreResult.FAIL,
        checks,
        reason,
        detail,
    )


def test_both_counts_fail_and_the_reason_is_the_first() -> None:
    outcome = judged(counts=(1, 0))

    assert outcome.checks == (PASS, PASS, PASS, FAIL, FAIL)
    assert outcome.reason is RestoreFailure.SUPPRESSION_FEWER
    assert outcome.detail == (
        "suppression_entries: copy 1, backup 5; provenance_records: copy 0, backup 3"
    )
    assert outcome.failing == (RestoreCheck.SUPPRESSION, RestoreCheck.PROVENANCE)


def test_a_revision_failure_comes_before_the_count_failures() -> None:
    outcome = judged(revision=OLD, counts=(1, 0))

    assert outcome.checks == (PASS, PASS, FAIL, FAIL, FAIL)
    assert outcome.reason is RestoreFailure.REVISION_BEHIND
    assert outcome.detail == (
        f"copy revision {OLD}; code head {HEAD}; suppression_entries: copy 1, backup 5; "
        "provenance_records: copy 0, backup 3"
    )


def test_the_joined_detail_is_cut_to_500_characters() -> None:
    outcome = judged(revision="r" * 600, revision_known=False, counts=(1, 0))

    assert outcome.detail is not None and len(outcome.detail) == 500
    assert outcome.detail.startswith("copy revision rrr")


@pytest.mark.parametrize(
    ("name", "value"), [("counts", (1,)), ("recorded", (1, 2, 3)), ("counts", ())]
)
def test_judge_refuses_counts_that_are_not_one_per_table(name: str, value: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match=name):
        judged(**{name: value})


# The outcome's own checks -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "checks", "reason", "detail"),
    [
        (RestoreResult.PASS, (PASS, FAIL, PASS, PASS, PASS), None, None),
        (RestoreResult.PASS, (PASS,) * 5, RestoreFailure.TAMPERED, None),
        (RestoreResult.PASS, (PASS,) * 5, None, "a detail"),
        (RestoreResult.FAIL, (PASS,) * 5, RestoreFailure.TAMPERED, None),
        (RestoreResult.FAIL, (FAIL,) + (SKIPPED,) * 4, None, None),
        (RestoreResult.FAIL, (FAIL,) + (SKIPPED,) * 4, RestoreFailure.TAMPERED, "x" * 501),
        (RestoreResult.PASS, (PASS,) * 4, None, None),
        (RestoreResult.PASS, ("pass",) * 5, None, None),
        ("pass", (PASS,) * 5, None, None),
    ],
    ids=[
        "pass with a failing check",
        "pass with a reason",
        "pass with a detail",
        "fail with no failing check",
        "fail with no reason",
        "fail with a long detail",
        "four checks",
        "plain strings for checks",
        "a plain string for the result",
    ],
)
def test_an_inconsistent_outcome_is_refused(
    result: object, checks: tuple[object, ...], reason: object, detail: str | None
) -> None:
    with pytest.raises(ValueError):
        RestoreOutcome(result, checks, reason, detail)  # type: ignore[arg-type]


def test_check_reads_one_checks_outcome() -> None:
    outcome = judged(counts=(5, 0))

    assert [outcome.check(check) for check in RestoreCheck] == [PASS, PASS, PASS, PASS, FAIL]
    assert outcome.check("provenance") is FAIL  # type: ignore[arg-type]


# The schedule -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backup_time", "expected"),
    [
        (time(10, 0), time(11, 0)),
        (time(2, 30), time(3, 30)),
        (time(22, 59), time(23, 59)),
        (time(23, 0), time(0, 0)),
        (time(23, 30), time(0, 30)),
    ],
)
def test_the_restore_test_time_is_an_hour_after_the_backup_wrapping_past_midnight(
    backup_time: time, expected: time
) -> None:
    assert restore_test_time(backup_time) == expected


def test_the_restore_test_time_refuses_anything_but_a_naive_time() -> None:
    with pytest.raises(TypeError):
        restore_test_time("10:00")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        restore_test_time(time(10, 0, tzinfo=UTC))


NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("newest", "now", "timezone", "due"),
    [
        (None, NOW, "UTC", True),
        (datetime(2026, 11, 1, 0, 0, tzinfo=UTC), NOW, "UTC", False),
        (datetime(2026, 10, 31, 23, 59, tzinfo=UTC), NOW, "UTC", True),
        (datetime(2025, 11, 20, tzinfo=UTC), NOW, "UTC", True),  # the same month, a year ago
        (datetime(2026, 12, 2, tzinfo=UTC), NOW, "UTC", False),  # a clock set back: not due
        # 2026-11-01 03:00 UTC is still October in Los Angeles (UTC-7 until 2026-11-01 09:00)
        (datetime(2026, 11, 1, 3, 0, tzinfo=UTC), NOW, "America/Los_Angeles", True),
        (datetime(2026, 11, 1, 3, 0, tzinfo=UTC), NOW, "UTC", False),
        # 2026-10-31 23:30 UTC is already November in Tokyo
        (datetime(2026, 10, 31, 23, 30, tzinfo=UTC), NOW, "Asia/Tokyo", False),
        (datetime(2026, 10, 31, 23, 30, tzinfo=UTC), NOW, "UTC", True),
        # now itself on a month boundary: 2026-12-01 04:00 UTC is still November in New York
        (
            datetime(2026, 11, 2, tzinfo=UTC),
            datetime(2026, 12, 1, 4, 0, tzinfo=UTC),
            "America/New_York",
            False,
        ),
        (datetime(2026, 11, 2, tzinfo=UTC), datetime(2026, 12, 1, 4, 0, tzinfo=UTC), "UTC", True),
    ],
)
def test_a_monthly_test_is_due_once_per_calendar_month_in_the_timezone(
    newest: datetime | None, now: datetime, timezone: str, due: bool
) -> None:
    assert restore_test_due(newest, now, timezone) is due


def test_restore_test_due_refuses_naive_times() -> None:
    with pytest.raises(ValueError):
        restore_test_due(None, datetime(2026, 11, 17), "UTC")
    with pytest.raises(ValueError):
        restore_test_due(datetime(2026, 10, 1), NOW, "UTC")


@pytest.mark.parametrize(
    ("newest", "now", "at", "timezone", "missed"),
    [
        (None, NOW, time(11, 0), "UTC", False),  # a new database waits for its first slot
        (datetime(2026, 10, 3, tzinfo=UTC), NOW, time(11, 0), "UTC", True),  # slot 2026-11-16
        (datetime(2026, 11, 3, tzinfo=UTC), NOW, time(11, 0), "UTC", False),  # done this month
        # the 1st at 09:00, before the 11:00 slot: the latest slot was 31 October
        (
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
            time(11, 0),
            "UTC",
            False,
        ),
        # the 1st at 11:00 exactly: today's slot is this month
        (
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, 11, 0, tzinfo=UTC),
            time(11, 0),
            "UTC",
            True,
        ),
        # a slot just after midnight (00:30) on the 1st, and now 00:45 on the 1st
        (
            datetime(2026, 10, 5, tzinfo=UTC),
            datetime(2026, 11, 1, 0, 45, tzinfo=UTC),
            time(0, 30),
            "UTC",
            True,
        ),
        # in New York the 1st's 11:00 slot is 16:00 UTC; at 15:00 UTC it hasn't come yet
        (
            datetime(2026, 10, 5, tzinfo=UTC),
            datetime(2026, 11, 1, 15, 0, tzinfo=UTC),
            time(11, 0),
            "America/New_York",
            False,
        ),
        (
            datetime(2026, 10, 5, tzinfo=UTC),
            datetime(2026, 11, 1, 16, 0, tzinfo=UTC),
            time(11, 0),
            "America/New_York",
            True,
        ),
    ],
)
def test_a_missed_restore_test_is_made_up_only_after_this_months_slot(
    newest: datetime | None, now: datetime, at: time, timezone: str, missed: bool
) -> None:
    assert missed_restore_test(newest, now, at, timezone) is missed


def test_the_schedule_is_frozen() -> None:
    schedule = RestoreTestSchedule(time(11, 0), "UTC", False)

    with pytest.raises(AttributeError):
        schedule.missed = True  # type: ignore[misc]


# Text -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "reason"),
    [("monthly", "no_offpc_copy"), ("manual", "revision_behind"), ("monthly", "tampered")],
)
def test_the_alert_text_carries_the_kind_the_id_and_the_reason_only(
    kind: str, reason: str
) -> None:
    assert restore_test_failed_text(kind, ULID, reason) == (
        f"Restore test failed: {kind} {ULID}, {reason}"
    )


@pytest.mark.parametrize(
    ("kind", "test_id", "reason"),
    [
        ("weekly", ULID, "tampered"),
        ("monthly", "not-a-ulid", "tampered"),
        ("monthly", ULID, "mac@example.com"),
    ],
)
def test_the_alert_text_refuses_anything_but_a_kind_a_ulid_and_a_reason(
    kind: str, test_id: str, reason: str
) -> None:
    with pytest.raises(ValueError):
        restore_test_failed_text(kind, test_id, reason)
