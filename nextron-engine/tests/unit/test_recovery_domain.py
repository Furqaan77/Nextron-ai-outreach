"""The recovery domain (``nextron.domain.recovery``, Story 1.19): the constants, the enums and
their labels, ``RecoveryContext``, ``RecoveryHandler`` and the ``RecoverySteps`` registry's
rules, ``settled``, the restore's name functions (every shape, another environment, ``.part``,
case, an impossible stamp) and ``backup_time`` (each source missing, the earliest wins, clamped
to ``restored_at``)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nextron.domain.recovery import (
    CHECK_PERIOD,
    CHECK_RECOVERY_JOB,
    PRE_RESTORE_DIR_NAME,
    RECOVERY_BANNER,
    RESTORE_WORK_DIR_NAME,
    RecoveryContext,
    RecoveryHandler,
    RecoveryMode,
    RecoveryPass,
    RecoveryStep,
    RecoverySteps,
    StepStatus,
    backup_time,
    copy_env,
    copy_format,
    copy_stamp,
    settled,
)

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
STAMP = "20261117T090000.123Z"
STAMP_TIME = datetime(2026, 11, 17, 9, 0, 0, 123000, tzinfo=UTC)


def done(context: RecoveryContext) -> bool:
    return True


def handler(
    step: RecoveryStep = RecoveryStep.REPOLL_UNSUBSCRIBES, **changes: object
) -> RecoveryHandler:
    fields: dict[str, object] = {"step": step, "owner": "compliance", "run": done, "startup": False}
    fields.update(changes)
    return RecoveryHandler(**fields)  # type: ignore[arg-type]


def test_the_constants() -> None:
    assert CHECK_RECOVERY_JOB == "check_recovery"
    assert CHECK_PERIOD == timedelta(minutes=1)
    assert PRE_RESTORE_DIR_NAME == "pre_restore"
    assert RESTORE_WORK_DIR_NAME == "restore"
    assert RECOVERY_BANNER == (
        "Recovery mode is on: sending is held until every recovery step is done."
    )


def test_the_modes_steps_and_statuses_and_their_labels() -> None:
    assert [mode.value for mode in RecoveryMode] == ["on", "off", "superseded"]
    assert [(step.value, step.label) for step in RecoveryStep] == [
        ("reapply_erasure_ledger", "Reapply the erasure ledger"),
        ("mark_oms_unknown", "Mark unfinished sends unknown"),
        ("reingest_mailboxes", "Re-ingest the mailboxes"),
        ("repoll_unsubscribes", "Re-poll the unsubscribe Worker"),
    ]
    assert [(status.value, status.label) for status in StepStatus] == [
        ("pending", "pending"),
        ("done", "done"),
        ("not_applicable", "not applicable"),
    ]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], True),
        (["done", "not_applicable", "done", "done"], True),
        ([StepStatus.NOT_APPLICABLE] * 4, True),
        (["done", "pending", "done", "done"], False),
        ([StepStatus.PENDING], False),
    ],
)
def test_settled(statuses: list[str], expected: bool) -> None:
    assert settled(statuses) is expected


def test_settled_refuses_an_unknown_status() -> None:
    with pytest.raises(ValueError):
        settled(["finished"])


# RecoveryContext and RecoveryHandler -----------------------------------------------------------


def test_a_context_keeps_its_times_in_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    context = RecoveryContext(ULID, T0.astimezone(plus_two), T0 + timedelta(hours=1))

    assert context.backup_at == T0 and context.backup_at.tzinfo is UTC
    assert context.restored_at == T0 + timedelta(hours=1)
    with pytest.raises(AttributeError):
        context.recovery_id = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ((None, T0, T0), TypeError),
        (("", T0, T0), ValueError),
        ((ULID, "2026-11-17", T0), TypeError),
        ((ULID, T0, datetime(2026, 11, 17, 9, 0)), ValueError),  # naive
    ],
)
def test_a_context_refuses_bad_fields(args: tuple[object, ...], error: type[Exception]) -> None:
    with pytest.raises(error):
        RecoveryContext(*args)  # type: ignore[arg-type]


def test_a_handler_holds_its_fields() -> None:
    found = handler(startup=True)

    assert (found.step, found.owner, found.run, found.startup) == (
        RecoveryStep.REPOLL_UNSUBSCRIBES,
        "compliance",
        done,
        True,
    )


@pytest.mark.parametrize(
    ("changes", "error", "field"),
    [
        ({"step": "repoll_unsubscribes"}, TypeError, "step"),
        ({"owner": 3}, TypeError, "owner"),
        ({"owner": "  "}, ValueError, "owner"),
        ({"run": "not callable"}, TypeError, "run"),
        ({"startup": 1}, TypeError, "startup"),
    ],
)
def test_a_handler_refuses_bad_fields(
    changes: dict[str, object], error: type[Exception], field: str
) -> None:
    fields: dict[str, object] = {
        "step": RecoveryStep.REPOLL_UNSUBSCRIBES,
        "owner": "compliance",
        "run": done,
        "startup": False,
    }
    fields.update(changes)

    with pytest.raises(error, match=field):
        RecoveryHandler(**fields)  # type: ignore[arg-type]


def test_a_pass_holds_its_context_and_pending_steps() -> None:
    context = RecoveryContext(ULID, T0, T0)
    found = RecoveryPass(context, (RecoveryStep.MARK_OMS_UNKNOWN,))

    assert (found.context, found.pending) == (context, (RecoveryStep.MARK_OMS_UNKNOWN,))


# The registry ----------------------------------------------------------------------------------


def test_the_registry_registers_gets_and_lists_its_steps() -> None:
    steps = RecoverySteps()
    first = handler(RecoveryStep.REAPPLY_ERASURE_LEDGER, startup=True)
    second = handler(RecoveryStep.REPOLL_UNSUBSCRIBES)

    steps.register_step(first)
    steps.register_step(second)

    assert not steps.frozen
    assert steps.get(RecoveryStep.REAPPLY_ERASURE_LEDGER) is first
    assert steps.get(RecoveryStep.REPOLL_UNSUBSCRIBES) is second
    assert steps.get(RecoveryStep.MARK_OMS_UNKNOWN) is None
    assert steps.registered() == frozenset(
        {RecoveryStep.REAPPLY_ERASURE_LEDGER, RecoveryStep.REPOLL_UNSUBSCRIBES}
    )


def test_an_empty_registry_has_no_steps() -> None:
    steps = RecoverySteps()
    steps.freeze()

    assert steps.frozen
    assert steps.registered() == frozenset()
    assert all(steps.get(step) is None for step in RecoveryStep)


def test_the_registry_refuses_a_step_registered_twice() -> None:
    steps = RecoverySteps()
    steps.register_step(handler())

    with pytest.raises(ValueError, match="repoll_unsubscribes"):
        steps.register_step(handler(owner="someone else"))


def test_the_registry_refuses_anything_but_a_handler() -> None:
    steps = RecoverySteps()

    with pytest.raises(TypeError):
        steps.register_step(done)  # type: ignore[arg-type]


def test_a_frozen_registry_refuses_registration() -> None:
    steps = RecoverySteps()
    steps.freeze()

    with pytest.raises(RuntimeError, match="frozen"):
        steps.register_step(handler())
    assert steps.registered() == frozenset()


# The restore's file names ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        (f"nextron-test-{STAMP}.db", "plain"),
        (f"nextron-test-{STAMP}.db.enc", "encrypted"),
        (f"NEXTRON-TEST-{STAMP}.DB", "plain"),
        (f"nextron-test-{STAMP}.DB.ENC", "encrypted"),
        ("renamed.db", "plain"),
        ("my copy.Db.Enc", "encrypted"),
        (f"nextron-test-{STAMP}.db.enc.part", None),
        (f"nextron-test-{STAMP}.ledger.jsonl.enc", None),
        ("nextron.db-wal", None),
        ("backup.sqlite", None),
        ("", None),
        (None, None),
    ],
)
def test_copy_format(name: object, fmt: str | None) -> None:
    assert copy_format(name) == fmt


@pytest.mark.parametrize(
    ("name", "env", "stamp"),
    [
        (f"nextron-test-{STAMP}.db", "test", STAMP_TIME),
        (f"nextron-prod-{STAMP}.db.enc", "prod", STAMP_TIME),
        (f"nextron-dev-{STAMP}.db", "dev", STAMP_TIME),
        (f"NEXTRON-PROD-{STAMP.lower()}.DB.ENC", "prod", STAMP_TIME),  # any case
        (f"nextron-test-{STAMP}.db.enc.part", None, None),  # cut off while it was written
        (f"nextron-test-{STAMP}.ledger.jsonl.enc", None, None),  # the ledger, not a copy
        (f"nextron-test-{STAMP}.sqlite", None, None),
        (f"copy-of-nextron-test-{STAMP}.db", None, None),
        ("nextron-test-20261117T0900.db", None, None),  # no milliseconds: not the shape
        ("renamed.db", None, None),
        ("nextron.db", None, None),
        (None, None, None),
    ],
)
def test_copy_env_and_copy_stamp(name: object, env: str | None, stamp: datetime | None) -> None:
    assert copy_env(name) == env
    assert copy_stamp(name) == stamp


def test_an_impossible_stamp_has_an_environment_but_no_time() -> None:
    name = "nextron-test-20261317T090000.000Z.db"  # month 13

    assert (copy_env(name), copy_stamp(name)) == ("test", None)


# backup_time ----------------------------------------------------------------------------------

NAME = f"nextron-test-{STAMP}.db"


@pytest.mark.parametrize(
    ("name", "newest_event", "restored_at", "expected"),
    [
        # the stamp is the earliest
        (NAME, STAMP_TIME + timedelta(hours=1), STAMP_TIME + timedelta(days=1), STAMP_TIME),
        # an event before the stamp (a copy renamed with a later stamp) wins
        (NAME, STAMP_TIME - timedelta(hours=2), STAMP_TIME + timedelta(days=1),
         STAMP_TIME - timedelta(hours=2)),
        # no stamp (a renamed file): the newest event
        ("renamed.db", T0, T0 + timedelta(days=3), T0),
        # no event: the stamp
        (NAME, None, STAMP_TIME + timedelta(days=1), STAMP_TIME),
        # neither: the restore itself
        ("renamed.db", None, T0, T0),
        # clamped to restored_at: a stamp and an event in the future
        (NAME, STAMP_TIME + timedelta(days=9), STAMP_TIME - timedelta(days=1),
         STAMP_TIME - timedelta(days=1)),
    ],
)
def test_backup_time_is_the_earliest_known_time(
    name: str, newest_event: datetime | None, restored_at: datetime, expected: datetime
) -> None:
    found = backup_time(name, newest_event, restored_at)

    assert found == expected
    assert found.tzinfo is UTC


def test_backup_time_reads_other_zones_as_the_same_instant() -> None:
    plus_five = timezone(timedelta(hours=5))

    found = backup_time("renamed.db", (T0 - timedelta(hours=1)).astimezone(plus_five), T0)

    assert (found, found.tzinfo) == (T0 - timedelta(hours=1), UTC)


@pytest.mark.parametrize(
    ("newest_event", "restored_at", "error"),
    [
        (datetime(2026, 11, 17, 9, 0), T0, ValueError),
        (None, datetime(2026, 11, 17, 9, 0), ValueError),
        (None, "now", TypeError),
    ],
)
def test_backup_time_refuses_naive_or_missing_times(
    newest_event: object, restored_at: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        backup_time(NAME, newest_event, restored_at)  # type: ignore[arg-type]
