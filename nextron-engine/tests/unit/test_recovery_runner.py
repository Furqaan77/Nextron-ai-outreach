"""The recovery steps' runner (``nextron.jobs.recovery.RecoveryRunner``, Story 1.19) on real units
of work and the real ``Recovery`` over a temporary database at head: ``startup`` marks the
unregistered steps and runs only the ``startup`` handlers; ``check`` runs the others and ends
recovery mode when every step is settled; a handler that raises, or returns something other
than a ``bool``, logs ``recovery_step_failed`` and its step stays pending; ``False`` leaves it
pending; each handler runs in a unit of work of its own, for exactly one use case, even inside
the job runner's unit; and errors from ``Recovery`` itself propagate."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from nextron.app.ops.api import Recovery
from nextron.app.queries import ops as ops_queries
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.recovery import (
    RecoveryContext,
    RecoveryHandler,
    RecoveryStep,
    RecoverySteps,
    StepStatus,
)
from nextron.jobs.recovery import RecoveryRunner
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
LOGGER = "nextron.jobs.recovery"
BACKUP_AT = T0 - timedelta(days=2)


class Owner:
    """A recovery step's owner: its use case records the unit of work (kept, so no two are
    ever the same object) and the context it ran in, and returns the scripted answers in turn
    (an exception is raised)."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.units: list[object] = []
        self.contexts: list[RecoveryContext] = []

    @use_case
    def redo(self, context: RecoveryContext) -> object:
        uow = current()
        with uow.transaction():  # a use case may open its transactions
            pass
        self.units.append(uow)
        self.contexts.append(context)
        answer = self.answers.pop(0) if self.answers else True
        if isinstance(answer, BaseException):
            raise answer
        return answer


def registry(*handlers: RecoveryHandler) -> RecoverySteps:
    steps = RecoverySteps()
    for handler in handlers:
        steps.register_step(handler)
    steps.freeze()
    return steps


def handler(step: RecoveryStep, owner: Owner, *, startup: bool) -> RecoveryHandler:
    run: Callable[[RecoveryContext], bool] = owner.redo  # type: ignore[assignment]
    return RecoveryHandler(step, "an_owner", run, startup)


def begin(units: UnitOfWorkFactory, clock: FrozenClock) -> str:
    with units.open():
        row = Recovery(clock).begin(
            backup_at=BACKUP_AT, source_name="nextron-test-x.db", pre_restore_name=None
        )
    return row.id


def statuses(units: UnitOfWorkFactory) -> dict[RecoveryStep, StepStatus]:
    with units.open(read_only=True):
        row = ops_queries.recovery_state()
    assert row is not None
    return dict(row.steps)


def mode(units: UnitOfWorkFactory) -> str:
    with units.open(read_only=True):
        row = ops_queries.recovery_state()
    assert row is not None
    return row.mode


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


def failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "recovery_step_failed"]


def test_the_runner_takes_only_a_frozen_registry(units: UnitOfWorkFactory, clock: FrozenClock) -> None:
    with pytest.raises(TypeError):
        RecoveryRunner(units, Recovery(clock), object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="freeze"):
        RecoveryRunner(units, Recovery(clock), RecoverySteps())


def test_startup_and_check_do_nothing_while_recovery_mode_is_off(
    units: UnitOfWorkFactory, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    owner = Owner()
    runner = RecoveryRunner(
        units,
        Recovery(clock),
        registry(handler(RecoveryStep.REAPPLY_ERASURE_LEDGER, owner, startup=True)),
    )

    runner.startup()

    assert runner.check() is False
    assert owner.units == []
    assert [r for r in caplog.records if r.name == LOGGER] == []


def test_startup_marks_unregistered_steps_and_runs_only_the_startup_handlers(
    units: UnitOfWorkFactory, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    recovery_id = begin(units, clock)
    at_start, later = Owner(True), Owner(False, True)
    runner = RecoveryRunner(
        units,
        Recovery(clock),
        registry(
            handler(RecoveryStep.REAPPLY_ERASURE_LEDGER, at_start, startup=True),
            handler(RecoveryStep.REPOLL_UNSUBSCRIBES, later, startup=False),
        ),
    )

    runner.startup()

    assert statuses(units) == {
        RecoveryStep.REAPPLY_ERASURE_LEDGER: StepStatus.DONE,
        RecoveryStep.MARK_OMS_UNKNOWN: StepStatus.NOT_APPLICABLE,
        RecoveryStep.REINGEST_MAILBOXES: StepStatus.NOT_APPLICABLE,
        RecoveryStep.REPOLL_UNSUBSCRIBES: StepStatus.PENDING,
    }
    assert later.units == []  # not a startup handler
    assert at_start.contexts == [RecoveryContext(recovery_id, BACKUP_AT, T0)]
    [on] = [r for r in caplog.records if r.getMessage() == "recovery_mode_on"]
    assert (on.name, on.levelno, on.recovery_id, on.pending) == (  # type: ignore[attr-defined]
        LOGGER,
        logging.WARNING,
        recovery_id,
        "reapply_erasure_ledger,repoll_unsubscribes",
    )

    assert runner.check() is True  # the other handler answered False: still pending
    assert statuses(units)[RecoveryStep.REPOLL_UNSUBSCRIBES] is StepStatus.PENDING
    assert len(at_start.units) == 1  # a startup handler never runs from check

    clock.advance(timedelta(minutes=1))
    assert runner.check() is False  # now True: done, and recovery mode ends
    assert statuses(units)[RecoveryStep.REPOLL_UNSUBSCRIBES] is StepStatus.DONE
    assert mode(units) == "off"
    assert len(later.units) == 2
    assert failures(caplog) == []


def test_with_no_handlers_startup_settles_every_step_and_check_ends_recovery(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    begin(units, clock)
    runner = RecoveryRunner(units, Recovery(clock), registry())

    runner.startup()

    assert set(statuses(units).values()) == {StepStatus.NOT_APPLICABLE}
    assert mode(units) == "on"  # only check ends it
    assert runner.check() is False
    assert mode(units) == "off"


def test_a_startup_handler_that_is_not_done_waits_for_the_next_start(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    begin(units, clock)
    owner = Owner(False, True)
    runner = RecoveryRunner(
        units,
        Recovery(clock),
        registry(handler(RecoveryStep.REAPPLY_ERASURE_LEDGER, owner, startup=True)),
    )

    runner.startup()
    assert runner.check() is True  # check never runs a startup handler
    assert runner.check() is True
    assert len(owner.units) == 1

    runner.startup()  # the next engine start

    assert statuses(units)[RecoveryStep.REAPPLY_ERASURE_LEDGER] is StepStatus.DONE
    assert runner.check() is False


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        (RuntimeError("boom"), "RuntimeError"),
        ("yes", "TypeError"),  # not a bool
        (None, "TypeError"),
        (1, "TypeError"),
    ],
)
def test_a_failing_or_non_bool_handler_logs_and_its_step_stays_pending(
    units: UnitOfWorkFactory,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
    answer: object,
    code: str,
) -> None:
    caplog.set_level(logging.INFO)
    recovery_id = begin(units, clock)
    owner = Owner(answer)
    runner = RecoveryRunner(
        units,
        Recovery(clock),
        registry(handler(RecoveryStep.REINGEST_MAILBOXES, owner, startup=False)),
    )

    assert runner.check() is True

    assert statuses(units)[RecoveryStep.REINGEST_MAILBOXES] is StepStatus.PENDING
    assert mode(units) == "on"
    [failed] = failures(caplog)
    assert (failed.name, failed.levelno) == (LOGGER, logging.ERROR)
    assert (failed.recovery_id, failed.step, failed.code) == (  # type: ignore[attr-defined]
        recovery_id,
        "reingest_mailboxes",
        code,
    )
    assert failed.exc_info is not None


def test_a_failing_handler_does_not_stop_the_others(
    units: UnitOfWorkFactory, clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    begin(units, clock)
    broken, fine = Owner(RuntimeError("boom")), Owner(True)
    runner = RecoveryRunner(
        units,
        Recovery(clock),
        registry(
            handler(RecoveryStep.MARK_OMS_UNKNOWN, broken, startup=False),
            handler(RecoveryStep.REPOLL_UNSUBSCRIBES, fine, startup=False),
        ),
    )

    assert runner.check() is True

    found = statuses(units)
    assert found[RecoveryStep.MARK_OMS_UNKNOWN] is StepStatus.PENDING
    assert found[RecoveryStep.REPOLL_UNSUBSCRIBES] is StepStatus.DONE
    assert len(failures(caplog)) == 1


def test_each_handler_runs_in_a_unit_of_its_own_inside_the_job_runners_unit(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    begin(units, clock)
    first, second = Owner(True), Owner(True)
    runner = RecoveryRunner(
        units,
        Recovery(clock),
        registry(
            handler(RecoveryStep.MARK_OMS_UNKNOWN, first, startup=False),
            handler(RecoveryStep.REINGEST_MAILBOXES, second, startup=False),
        ),
    )

    with units.open():  # the job runner's unit around the check_recovery handler
        outer = current()
        still_on = runner.check()
        assert current() is outer

    assert still_on is False  # both done, the other two not applicable
    [first_unit], [second_unit] = first.units, second.units
    assert first_unit is not outer and second_unit is not outer
    assert first_unit is not second_unit


def test_errors_from_recovery_itself_propagate(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    class Broken(Recovery):
        @use_case
        def pending(self, registered: frozenset[RecoveryStep]) -> None:  # type: ignore[override]
            raise OSError("the database is gone")

    runner = RecoveryRunner(units, Broken(clock), registry())

    with pytest.raises(OSError):
        runner.startup()
    with pytest.raises(OSError):
        runner.check()


def test_an_error_marking_a_step_done_propagates(
    units: UnitOfWorkFactory, clock: FrozenClock
) -> None:
    begin(units, clock)

    class Broken(Recovery):
        @use_case
        def mark_done(self, recovery_id: str, step: RecoveryStep) -> bool:
            raise OSError("the database is gone")

    runner = RecoveryRunner(
        units,
        Broken(clock),
        registry(handler(RecoveryStep.MARK_OMS_UNKNOWN, Owner(True), startup=False)),
    )

    with pytest.raises(OSError):
        runner.check()
