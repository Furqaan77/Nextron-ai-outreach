"""Recovery contracts (Story 1.19; AD-2, AD-5, AD-17, AD-20, OP-8), each with a probe proof.

- **One step registry.** Only ``build_recovery_steps()`` in ``nextron/main.py`` constructs
  ``RecoverySteps`` or calls ``register_step``, under any alias (the same AST check as the job,
  trigger, doctor and health registries, ``tests/contracts/test_job_contracts.py``). It ships
  frozen and empty until Stories 3.12, 3.16 and 6.12 register their handlers.
- **The gate.** ``nextron.app.queries.ops.recovery_mode`` exists with the signature
  ``() -> bool``: every later send path calls it and holds its sends while it is true.
- **The trigger.** ``Trigger.CHECK_RECOVERY`` is registered as an ``Every(CHECK_PERIOD,
  at_startup=True)`` with ``active`` set (it fires only while recovery mode is on), on a
  ``check_recovery`` job type that runs on the realtime lane only.
- **The steps.** ``RecoveryStep`` is exactly the acceptance criteria's four, in order: reapply
  the erasure ledger, mark unfinished sends unknown, re-ingest the mailboxes, re-poll the
  unsubscribe Worker.

The AST check's proofs run it on a temporary copy of the tree (``tests/contracts/support.py``);
the other checks' proofs run them on probe objects built here. Neither touches the real tree.
"""

import ast
import inspect
from datetime import time, timedelta
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nextron import main
from nextron.app.queries import ops as ops_queries
from nextron.domain.job import Job, JobGroup, JobType, JobTypes, Lane
from nextron.domain.recovery import CHECK_PERIOD, RecoveryStep, RecoverySteps
from nextron.jobs.triggers import DailyAt, DailyRun, Every, Trigger, Triggers
from tests.contracts.support import ENGINE_ROOT, copy_with_probe
from tests.contracts.test_health_contracts import HEALTH_ISSUES
from tests.contracts.test_job_contracts import (
    JOB_TYPES,
    MAIN,
    TRIGGERS,
    Registry,
    registration_problems,
)

RECOVERY_STEPS = Registry(
    "build_recovery_steps", "RecoverySteps", "nextron.domain.recovery", "register_step"
)
AC_STEPS = (
    "reapply_erasure_ledger",
    "mark_oms_unknown",
    "reingest_mailboxes",
    "repoll_unsubscribes",
)
UNUSED = '"""unused"""\n'


def _never() -> Any:
    raise AssertionError("no job runs here")


# One step registry ------------------------------------------------------------------------------


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    [found] = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    return found


def test_only_build_recovery_steps_builds_or_fills_the_registry() -> None:
    assert registration_problems(ENGINE_ROOT, RECOVERY_STEPS) == []
    tree = ast.parse((ENGINE_ROOT / MAIN).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(_function(tree, RECOVERY_STEPS.builder))
        if isinstance(node, ast.Call)
    ]
    assert any(getattr(node.func, "id", None) == RECOVERY_STEPS.cls for node in calls)


PROBES = [
    "from nextron.domain.recovery import RecoverySteps\nregistry = RecoverySteps()",
    "from nextron.domain.recovery import RecoverySteps as Steps\nregistry = Steps()",
    "from nextron.domain import recovery\nregistry = recovery.RecoverySteps()",
    "import nextron.domain.recovery\nregistry = nextron.domain.recovery.RecoverySteps()",
    "def wire(registry, handler):\n    registry.register_step(handler)",
    "from nextron.domain.recovery import RecoverySteps\n"
    "def wire(registry, handler):\n"
    "    RecoverySteps.register_step(registry, handler)",
]


@pytest.mark.parametrize("source", PROBES)
@pytest.mark.parametrize("package", ["nextron.app.compliance", "nextron.jobs", "nextron.web"])
def test_the_registry_check_catches_registration_outside_main(
    source: str, package: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, package, source + "\n")

    [problem] = registration_problems(root, RECOVERY_STEPS)

    assert problem.startswith(f"{package.replace('.', '/')}/_lint_probe.py:")
    assert ("calls register_step" in problem) or ("constructs RecoverySteps" in problem)
    for other in (JOB_TYPES, TRIGGERS, HEALTH_ISSUES):
        assert registration_problems(root, other) == []


def test_the_registry_check_catches_another_function_in_main(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app.ops", UNUSED)
    main_py = root / MAIN
    main_py.write_text(
        main_py.read_text(encoding="utf-8")
        + "\n\ndef late_recovery_steps(handler) -> RecoverySteps:\n"
        "    registry = RecoverySteps()\n"
        "    registry.register_step(handler)\n"
        "    return registry\n",
        encoding="utf-8",
    )

    problems = registration_problems(root, RECOVERY_STEPS)

    assert [problem.split(" ", 1)[1] for problem in problems] == [
        "constructs RecoverySteps",
        "calls register_step",
    ]
    assert all(problem.startswith(f"{MAIN}:") for problem in problems)


def test_the_registry_ships_frozen_and_empty() -> None:
    steps = main.build_recovery_steps()

    assert isinstance(steps, RecoverySteps)
    assert steps.frozen
    assert steps.registered() == frozenset()
    assert main.build_recovery_steps() is not steps  # a new registry each time


# The gate ---------------------------------------------------------------------------------------


def gate_problems(module: object) -> list[str]:
    """What is wrong with ``module.recovery_mode`` as the send paths' gate: it must exist, be
    callable, take no parameter and return ``bool``."""
    gate = getattr(module, "recovery_mode", None)
    if gate is None:
        return ["recovery_mode is missing"]
    if not callable(gate):
        return ["recovery_mode is not callable"]
    signature = inspect.signature(gate, eval_str=True)
    problems = []
    if signature.parameters:
        problems.append("recovery_mode takes parameters")
    if signature.return_annotation is not bool:
        problems.append("recovery_mode does not return bool")
    return problems


def test_queries_ops_recovery_mode_is_the_gate() -> None:
    assert gate_problems(ops_queries) == []


def _takes_an_argument(owner: str) -> bool:
    return False


def _returns_a_row() -> object:
    return None


def _unannotated():  # type: ignore[no-untyped-def]  # noqa: ANN202
    return False


@pytest.mark.parametrize(
    ("probe", "problem"),
    [
        (SimpleNamespace(), "recovery_mode is missing"),
        (SimpleNamespace(recovery_mode=True), "recovery_mode is not callable"),
        (SimpleNamespace(recovery_mode=_takes_an_argument), "recovery_mode takes parameters"),
        (SimpleNamespace(recovery_mode=_returns_a_row), "recovery_mode does not return bool"),
        (SimpleNamespace(recovery_mode=_unannotated), "recovery_mode does not return bool"),
    ],
)
def test_the_gate_check_catches_each_wrong_gate(probe: object, problem: str) -> None:
    assert problem in gate_problems(probe)


# The trigger ------------------------------------------------------------------------------------


def trigger_problems(triggers: Triggers, job_types: JobTypes) -> list[str]:
    """What is wrong with ``Trigger.CHECK_RECOVERY``'s registration."""
    found = [spec for spec in triggers.specs() if spec.trigger is Trigger.CHECK_RECOVERY]
    if not found:
        return ["check_recovery is not registered"]
    schedule = found[0].schedule
    problems = []
    if not isinstance(schedule, Every):
        return ["check_recovery is not an Every"]
    if schedule.period != CHECK_PERIOD:
        problems.append("check_recovery's period is not CHECK_PERIOD")
    if schedule.at_startup is not True:
        problems.append("check_recovery does not fire at startup")
    if schedule.active is None:
        problems.append("check_recovery has no active")
    job_type = job_types.get(found[0].job_type)
    if job_type.lanes != frozenset({Lane.REALTIME}) or job_type.default_lane is not Lane.REALTIME:
        problems.append("check_recovery must run on the realtime lane only")
    return problems


def _main_registries() -> tuple[Triggers, JobTypes]:
    job_types = main.build_job_types(
        alerts=_never,
        backups=_never,
        restore_tests=_never,
        recovery=_never,
        contact_markets=_never,
        crawls=_never,
        discovery=_never,
        research=_never,
        extractions=_never,
        primary=_never,
        test_runs=_never,
    )
    triggers = main.build_triggers(
        job_types,
        backup_schedule=lambda: DailyRun(time(10), "UTC"),
        restore_test_schedule=lambda: DailyRun(time(11), "UTC"),
        recovery_active=lambda: False,
    )
    return triggers, job_types


def test_check_recovery_is_an_active_every_minute_at_startup_on_the_realtime_lane() -> None:
    triggers, job_types = _main_registries()

    assert trigger_problems(triggers, job_types) == []
    order = [spec.trigger for spec in triggers.specs()]
    # registered right after restore_test (Story 2.5's purge_page_text comes after it)
    assert order.index(Trigger.CHECK_RECOVERY) == order.index(Trigger.RESTORE_TEST) + 1


def _noop(job: Job) -> None:
    del job


def _probe(
    schedule: Every | DailyAt | None, lanes: frozenset[Lane]
) -> tuple[Triggers, JobTypes]:
    job_types = JobTypes()
    default = Lane.REALTIME if Lane.REALTIME in lanes else next(iter(lanes))
    job_types.register_job_type(
        JobType(
            "check_recovery",
            default,
            lanes,
            85,
            timedelta(minutes=10),
            2,
            _noop,
            group=JobGroup.BACKUP,
        )
    )
    job_types.freeze()
    triggers = Triggers(job_types)
    if schedule is not None:
        triggers.register_trigger(Trigger.CHECK_RECOVERY, schedule)
    triggers.freeze()
    return triggers, job_types


def _on() -> bool:
    return True


@pytest.mark.parametrize(
    ("schedule", "lanes", "problem"),
    [
        (None, frozenset({Lane.REALTIME}), "is not registered"),
        (
            DailyAt(lambda: DailyRun(time(1), "UTC"), fallback=DailyRun(time(1), "UTC")),
            frozenset({Lane.REALTIME}),
            "is not an Every",
        ),
        (
            Every(2 * CHECK_PERIOD, at_startup=True, active=_on),
            frozenset({Lane.REALTIME}),
            "period",
        ),
        (Every(CHECK_PERIOD, active=_on), frozenset({Lane.REALTIME}), "at startup"),
        (Every(CHECK_PERIOD, at_startup=True), frozenset({Lane.REALTIME}), "no active"),
        (
            Every(CHECK_PERIOD, at_startup=True, active=_on),
            frozenset({Lane.REALTIME, Lane.BULK}),
            "realtime lane only",
        ),
        (
            Every(CHECK_PERIOD, at_startup=True, active=_on),
            frozenset({Lane.BULK}),
            "realtime lane only",
        ),
    ],
)
def test_the_trigger_check_catches_each_wrong_registration(
    schedule: Every | DailyAt | None, lanes: frozenset[Lane], problem: str
) -> None:
    triggers, job_types = _probe(schedule, lanes)

    found = trigger_problems(triggers, job_types)

    assert any(problem in item for item in found), found


# The steps --------------------------------------------------------------------------------------


def step_problems(steps: type[StrEnum]) -> list[str]:
    """Whether ``steps`` is exactly the acceptance criteria's four recovery steps, in order."""
    found = tuple(step.value for step in steps)
    return [] if found == AC_STEPS else [f"the recovery steps are {found}, not {AC_STEPS}"]


def test_the_recovery_steps_are_exactly_the_four_of_the_acceptance_criteria() -> None:
    assert step_problems(RecoveryStep) == []


class _Reordered(StrEnum):
    MARK_OMS_UNKNOWN = "mark_oms_unknown"
    REAPPLY_ERASURE_LEDGER = "reapply_erasure_ledger"
    REINGEST_MAILBOXES = "reingest_mailboxes"
    REPOLL_UNSUBSCRIBES = "repoll_unsubscribes"


class _Missing(StrEnum):
    REAPPLY_ERASURE_LEDGER = "reapply_erasure_ledger"
    MARK_OMS_UNKNOWN = "mark_oms_unknown"
    REINGEST_MAILBOXES = "reingest_mailboxes"


class _Extra(StrEnum):
    REAPPLY_ERASURE_LEDGER = "reapply_erasure_ledger"
    MARK_OMS_UNKNOWN = "mark_oms_unknown"
    REINGEST_MAILBOXES = "reingest_mailboxes"
    REPOLL_UNSUBSCRIBES = "repoll_unsubscribes"
    SKIP_SUPPRESSION = "skip_suppression"


@pytest.mark.parametrize("probe", [_Reordered, _Missing, _Extra])
def test_the_step_check_catches_a_reordered_missing_or_extra_step(probe: type[StrEnum]) -> None:
    assert step_problems(probe) != []

