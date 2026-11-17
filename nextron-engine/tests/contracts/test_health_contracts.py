"""Health contracts (FR-58, Story 1.15), each with a live proof.

- **One registry.** Only ``build_health_issues()`` in ``nextron/main.py`` constructs
  ``HealthIssues`` or calls ``register_source``, under any alias (the same AST check as the job,
  trigger and doctor registries, ``tests/contracts/test_job_contracts.py``).
- **It ships frozen** with exactly the ``jobqueue`` and ``budget`` sources, in that order; each
  later owner that adds a source extends ``SOURCES``.
- **Every job type names its group.** Each ``JobType(...)`` call in ``build_job_types`` passes
  ``group=`` (``JobType.group`` defaults to ``other``, so a forgotten group would hide a type
  under "Other" on the health panel).
- **One heartbeat threshold.** ``startup_checks.HEARTBEAT_FRESH_S`` is
  ``HEARTBEAT_FRESH.total_seconds()``: doctor's ``port_free`` and the panel's worker rule share
  ``nextron.domain.health.HEARTBEAT_FRESH``.

The AST checks' proofs run them on a temporary copy of the tree (``tests/contracts/support.py``),
never on the real tree.
"""

import ast
from pathlib import Path

import pytest

from nextron import main, startup_checks
from nextron.domain.health import HEARTBEAT_FRESH, HealthIssues, IssueSource
from tests.contracts.support import ENGINE_ROOT, copy_with_probe
from tests.contracts.test_job_contracts import (
    JOB_TYPES,
    MAIN,
    TRIGGERS,
    Registry,
    registration_problems,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.usage_meter import InMemoryUsageMeter

HEALTH_ISSUES = Registry(
    "build_health_issues", "HealthIssues", "nextron.domain.health", "register_source"
)
SOURCES = ("jobqueue", "budget", "backups")
STARTUP_CHECKS = "nextron/startup_checks.py"
UNUSED = '"""unused"""\n'


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    [found] = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    return found


# One registry -----------------------------------------------------------------------------------


def test_only_build_health_issues_builds_or_fills_the_registry() -> None:
    assert registration_problems(ENGINE_ROOT, HEALTH_ISSUES) == []
    tree = ast.parse((ENGINE_ROOT / MAIN).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(_function(tree, HEALTH_ISSUES.builder))
        if isinstance(node, ast.Call)
    ]
    assert any(getattr(node.func, "id", None) == HEALTH_ISSUES.cls for node in calls)
    assert any(getattr(node.func, "attr", None) == HEALTH_ISSUES.register for node in calls)


PROBES = [
    "from nextron.domain.health import HealthIssues\nregistry = HealthIssues()",
    "from nextron.domain.health import HealthIssues as Issues\nregistry = Issues()",
    "from nextron.domain import health\nregistry = health.HealthIssues()",
    "import nextron.domain.health\nregistry = nextron.domain.health.HealthIssues()",
    "def wire(registry, source):\n    registry.register_source(source)",
    "from nextron.domain.health import HealthIssues\n"
    "def wire(registry, source):\n"
    "    HealthIssues.register_source(registry, source)",
]


@pytest.mark.parametrize("source", PROBES)
@pytest.mark.parametrize("package", ["nextron.app.ops", "nextron.web"])
def test_the_registry_check_catches_registration_outside_main(
    source: str, package: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, package, source + "\n")

    [problem] = registration_problems(root, HEALTH_ISSUES)

    assert problem.startswith(f"{package.replace('.', '/')}/_lint_probe.py:")
    assert ("calls register_source" in problem) or ("constructs HealthIssues" in problem)
    for other in (JOB_TYPES, TRIGGERS):
        assert registration_problems(root, other) == []


def test_the_registry_check_catches_another_function_in_main(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app.ops", UNUSED)
    main_py = root / MAIN
    main_py.write_text(
        main_py.read_text(encoding="utf-8")
        + "\n\ndef late_health_issues(source) -> HealthIssues:\n"
        "    registry = HealthIssues()\n"
        "    registry.register_source(source)\n"
        "    return registry\n",
        encoding="utf-8",
    )

    problems = registration_problems(root, HEALTH_ISSUES)

    assert [problem.split(" ", 1)[1] for problem in problems] == [
        "constructs HealthIssues",
        "calls register_source",
    ]
    assert all(problem.startswith(f"{MAIN}:") for problem in problems)


def test_the_registry_ships_frozen_with_the_jobqueue_budget_and_backups_sources() -> None:
    meter = InMemoryUsageMeter(FrozenClock())
    budget = main.Budget(FrozenClock(), meter, notify=None)  # type: ignore[arg-type]

    issues = main.build_health_issues(budget=budget)

    assert isinstance(issues, HealthIssues)
    assert issues.frozen
    assert tuple(source.name for source in issues.sources) == SOURCES
    jobqueue, budget_source, backups = issues.sources
    assert jobqueue.read is main.jobqueue_queries.health_issues
    assert budget_source.read == budget.health_issues
    assert backups.read is main.ops_queries.health_issues
    with pytest.raises(RuntimeError):
        issues.register_source(IssueSource("late", tuple))
    assert main.build_health_issues(budget=budget) is not issues  # a new registry each time


# Every job type names its group -----------------------------------------------------------------


def ungrouped_job_types(root: Path) -> list[str]:
    """Each ``JobType(...)`` call in ``main.build_job_types`` without a ``group=`` keyword, as
    ``<file>:<line>``."""
    tree = ast.parse((root / MAIN).read_text(encoding="utf-8"))
    problems = []
    for node in ast.walk(_function(tree, JOB_TYPES.builder)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "JobType" and not any(k.arg == "group" for k in node.keywords):
            problems.append(f"{MAIN}:{node.lineno}")
    return problems


def test_every_job_type_registration_names_its_group() -> None:
    tree = ast.parse((ENGINE_ROOT / MAIN).read_text(encoding="utf-8"))
    built = [
        node
        for node in ast.walk(_function(tree, JOB_TYPES.builder))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "JobType"
    ]

    assert built  # the check looks at real registrations
    assert ungrouped_job_types(ENGINE_ROOT) == []


def test_the_group_check_catches_a_registration_without_a_group(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app.ops", UNUSED)
    main_py = root / MAIN
    text = main_py.read_text(encoding="utf-8")
    grouped = "            group=JobGroup.OTHER,"
    assert text.count(grouped) == 2  # send_alert, then resolve_contact_markets (Story 3.2)
    main_py.write_text(text.replace(grouped, "", 1), encoding="utf-8")  # send_alert's only

    [problem] = ungrouped_job_types(root)

    assert problem.startswith(f"{MAIN}:")


# One heartbeat threshold ------------------------------------------------------------------------


def test_doctors_heartbeat_threshold_is_derived_from_the_panels() -> None:
    tree = ast.parse((ENGINE_ROOT / STARTUP_CHECKS).read_text(encoding="utf-8"))
    [value] = [
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "HEARTBEAT_FRESH_S"
    ]

    assert ast.unparse(value) == "HEARTBEAT_FRESH.total_seconds()"
    assert startup_checks.HEARTBEAT_FRESH_S == HEARTBEAT_FRESH.total_seconds() == 120.0
