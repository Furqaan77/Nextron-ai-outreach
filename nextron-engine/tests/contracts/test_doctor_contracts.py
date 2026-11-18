"""Doctor contracts (FR-56, Story 1.14), each with a live proof.

- **One registry.** Only ``build_doctor_checks()`` in ``nextron/main.py`` constructs
  ``DoctorChecks`` or calls ``register_check``, under any alias (the same AST check as the job
  and trigger registries, ``tests/contracts/test_job_contracts.py``).
- **It ships frozen** with exactly Story 1.14's eleven checks, Story 1.17's ``offpc_backup``,
  Story 3.8's ``unsub_token_keys`` and Story 3.12's ``unsub_worker`` (fourteen), in the order
  doctor prints them; each later story that adds a check extends ``CHECKS``.

The AST check's proofs run it on a temporary copy of the tree with a probe module
(``tests/contracts/support.py``), never on the real tree.
"""

import ast
from pathlib import Path

import pytest

from nextron import main
from nextron.startup_checks import DoctorCheck, DoctorChecks
from tests.contracts.support import ENGINE_ROOT, copy_with_probe
from tests.contracts.test_job_contracts import (
    JOB_TYPES,
    MAIN,
    TRIGGERS,
    Registry,
    registration_problems,
)

DOCTOR_CHECKS = Registry(
    "build_doctor_checks", "DoctorChecks", "nextron.startup_checks", "register_check"
)
CHECKS = (
    "python_version",
    "sqlite_version",
    "tzdata",
    "prod_env_not_persistent",
    "data_dir_outside_onedrive",
    "backup_dir_separate",
    "port_free",
    "required_credentials",
    "internal_notify_grant",
    "offpc_backup",
    "required_settings",
    "database_revision",
    "unsub_token_keys",
    "unsub_worker",
)
UNUSED = '"""unused"""\n'


def test_only_build_doctor_checks_builds_or_fills_the_registry() -> None:
    assert registration_problems(ENGINE_ROOT, DOCTOR_CHECKS) == []
    tree = ast.parse((ENGINE_ROOT / MAIN).read_text(encoding="utf-8"))
    [build] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == DOCTOR_CHECKS.builder
    ]
    calls = [node for node in ast.walk(build) if isinstance(node, ast.Call)]
    assert any(getattr(node.func, "id", None) == DOCTOR_CHECKS.cls for node in calls)
    assert any(getattr(node.func, "attr", None) == DOCTOR_CHECKS.register for node in calls)


PROBES = [
    "from nextron.startup_checks import DoctorChecks\nregistry = DoctorChecks()",
    "from nextron.startup_checks import DoctorChecks as Checks\nregistry = Checks()",
    "from nextron import startup_checks\nregistry = startup_checks.DoctorChecks()",
    "import nextron.startup_checks\nregistry = nextron.startup_checks.DoctorChecks()",
    "def wire(registry, check):\n    registry.register_check(check)",
    "from nextron.startup_checks import DoctorChecks\n"
    "def wire(registry, check):\n"
    "    DoctorChecks.register_check(registry, check)",
]


@pytest.mark.parametrize("source", PROBES)
@pytest.mark.parametrize("package", ["nextron.app.ops", "nextron.web"])
def test_the_registry_check_catches_registration_outside_main(
    source: str, package: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, package, source + "\n")

    [problem] = registration_problems(root, DOCTOR_CHECKS)

    assert problem.startswith(f"{package.replace('.', '/')}/_lint_probe.py:")
    assert ("calls register_check" in problem) or ("constructs DoctorChecks" in problem)
    for other in (JOB_TYPES, TRIGGERS):
        assert registration_problems(root, other) == []


def test_the_registry_check_catches_another_function_in_main(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app.ops", UNUSED)
    main_py = root / MAIN
    main_py.write_text(
        main_py.read_text(encoding="utf-8")
        + "\n\ndef late_doctor_checks(check) -> DoctorChecks:\n"
        "    registry = DoctorChecks()\n"
        "    registry.register_check(check)\n"
        "    return registry\n",
        encoding="utf-8",
    )

    problems = registration_problems(root, DOCTOR_CHECKS)

    assert [problem.split(" ", 1)[1] for problem in problems] == [
        "constructs DoctorChecks",
        "calls register_check",
    ]
    assert all(problem.startswith(f"{MAIN}:") for problem in problems)


def test_the_registry_ships_frozen_with_the_fourteen_checks_in_order() -> None:
    checks = main.build_doctor_checks()

    assert isinstance(checks, DoctorChecks)
    assert checks.frozen
    assert tuple(check.id for check in checks.checks) == CHECKS
    assert all(isinstance(check, DoctorCheck) for check in checks.checks)
    late = DoctorCheck("late", lambda context: checks.checks[0].run(context))
    with pytest.raises(RuntimeError):
        checks.register_check(late)
    assert main.build_doctor_checks() is not checks  # a new registry each time
