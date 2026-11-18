"""Backup contracts (Story 1.16; AD-2, AD-17, AD-20), each with a probe proof.

- **No copy except through the backup API.** No module under ``nextron/`` imports ``shutil``,
  calls ``.copy_into(`` or a ``.copy(`` with arguments (``Path.copy``), links a file
  (``hardlink_to``, ``symlink_to``, ``os.link``, ``os.symlink``) or names ``VACUUM INTO``, and
  ``.backup(`` (SQLite's backup API) appears only in ``nextron/db/engine.py``: nothing copies,
  links or reads the live database file's bytes.
- **``backup_db`` and ``restore_test`` are bulk-only with group Backup.** Each registered job
  type runs only on the ``bulk`` lane, so a copy or a restore test never delays a send, and
  shows under Backup on the health panel (Stories 1.16 and 1.18).
- **The triggers are registered after their job types.** ``build_triggers`` registers
  ``Trigger.BACKUP_DB`` and ``Trigger.RESTORE_TEST`` with a ``DailyAt`` each, and only on a
  registry where ``backup_db`` and ``restore_test`` are registered (the registry refuses
  otherwise).
- **Rotation never keeps erased data longer than 35 days.** ``MAX_ROTATION_DAYS`` is 35 (AD-17)
  and the ``backup_rotation_days`` setting refuses anything above it.

The source checks read the tree (AST and text); their proofs run them on a temporary copy with a
probe module (``tests/contracts/support.py``), never on the real tree.
"""

import ast
import re
from datetime import time, timedelta
from pathlib import Path
from typing import Any, Final

import pytest

from nextron import main
from nextron.domain import settings_schema
from nextron.domain.backup import MAX_ROTATION_DAYS
from nextron.domain.job import Job, JobGroup, JobType, JobTypes, Lane, UnknownJobType
from nextron.domain.settings_schema import (
    InvalidSettingValue,
    Key,
    Section,
    Setting,
    SettingType,
    maximum,
    minimum,
    parse,
)
from nextron.jobs.triggers import DailyAt, DailyRun, Trigger
from tests.contracts.support import ENGINE_ROOT, copy_with_probe

ENGINE_PY: Final = "nextron/db/engine.py"
BACKUP_API: Final = re.compile(r"\.backup\(")
NEVER: Final = (
    (re.compile(r"\.copy_into\("), "calls .copy_into("),
    (re.compile(r"VACUUM\s+INTO", re.IGNORECASE), "names VACUUM INTO"),
    (re.compile(r"\bhardlink_to\(|\bsymlink_to\(|\bos\.link\(|\bos\.symlink\("), "links a file"),
)


def _no_backups() -> Any:
    raise AssertionError("no backup_db or restore_test job runs here")


def _daily(at: time) -> Any:
    return lambda: DailyRun(at, "UTC")


def _triggers(job_types: JobTypes) -> Any:
    return main.build_triggers(
        job_types,
        backup_schedule=_daily(time(10)),
        restore_test_schedule=_daily(time(11)),
        recovery_active=lambda: False,
        domain_check_schedule=_daily(time(7)),
    )


# build_job_types's services: none is called here
_NO_SERVICES: Final[dict[str, Any]] = {
    "alerts": _no_backups,
    "backups": _no_backups,
    "restore_tests": _no_backups,
    "recovery": _no_backups,
    "contact_markets": _no_backups,
    "crawls": _no_backups,
    "discovery": _no_backups,
    "research": _no_backups,
    "extractions": _no_backups,
    "primary": _no_backups,
    "test_runs": _no_backups,
    "job_boards": _no_backups,
    "legal_forms": _no_backups,
}


# No copy except through the backup API -----------------------------------------------------------


def copy_problems(root: Path) -> list[str]:
    """Each place under ``root/nextron`` that copies or links a file other than through
    SQLite's backup API in ``nextron/db/engine.py``."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        label = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "shutil" for alias in node.names
            ):
                problems.append(f"{label}:{node.lineno} imports shutil")
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == (
                "shutil"
            ):
                problems.append(f"{label}:{node.lineno} imports shutil")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "copy"
                and (node.args or node.keywords)
            ):
                problems.append(f"{label}:{node.lineno} calls .copy( with arguments")
        for number, line in enumerate(source.splitlines(), start=1):
            if BACKUP_API.search(line) and label != ENGINE_PY:
                problems.append(f"{label}:{number} uses .backup( outside {ENGINE_PY}")
            for pattern, what in NEVER:
                if pattern.search(line):
                    problems.append(f"{label}:{number} {what}")
    return problems


def test_nothing_copies_the_database_except_the_backup_api() -> None:
    assert copy_problems(ENGINE_ROOT) == []
    assert BACKUP_API.search((ENGINE_ROOT / ENGINE_PY).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("package", "source", "problem"),
    [
        ("nextron.app.ops", "import shutil\n", "imports shutil"),
        ("nextron.db", "from shutil import copyfile\n", "imports shutil"),
        ("nextron.db.repos.ops", "import shutil as sh\n", "imports shutil"),
        ("nextron.db", "def copy(a, b):\n    a.backup(b)\n", "uses .backup( outside"),
        ("nextron.app.ops", "def copy(p, q):\n    p.copy(q)\n", "calls .copy( with arguments"),
        ("nextron.app.ops", "def copy(p, q):\n    p.copy_into(q)\n", "calls .copy_into("),
        ("nextron.db", "SQL = 'vacuum  into ?'\n", "names VACUUM INTO"),
        ("nextron.db", "def link(p, q):\n    q.hardlink_to(p)\n", "links a file"),
        ("nextron.db", "import os\n\n\ndef link(p, q):\n    os.link(p, q)\n", "links a file"),
    ],
)
def test_the_copy_check_catches_each_other_way_to_copy(
    tmp_path: Path, package: str, source: str, problem: str
) -> None:
    module, root = copy_with_probe(tmp_path, package, source)

    problems = copy_problems(root)

    label = module.replace(".", "/") + ".py"
    assert any(found.startswith(f"{label}:") and problem in found for found in problems), problems


def test_the_copy_check_allows_a_copy_without_arguments(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app.ops", "def keep(d):\n    return d.copy()\n")

    assert copy_problems(root) == []


# backup_db and restore_test are bulk-only with group Backup --------------------------------------

BACKUP_JOB_TYPES: Final = ("backup_db", "restore_test")


def backup_job_problems(job_types: JobTypes, name: str = "backup_db") -> list[str]:
    """What is wrong with the registered job type ``name`` (``backup_db`` or
    ``restore_test``)."""
    found = job_types.get(name)
    problems = []
    if found.lanes != frozenset({Lane.BULK}) or found.default_lane is not Lane.BULK:
        problems.append(f"{name} must run on the bulk lane only")
    if found.group is not JobGroup.BACKUP:
        problems.append(f"{name} must show under Backup")
    return problems


def _registry() -> JobTypes:
    return main.build_job_types(**_NO_SERVICES)


@pytest.mark.parametrize("name", BACKUP_JOB_TYPES)
def test_backup_db_and_restore_test_are_bulk_only_with_group_backup(name: str) -> None:
    assert backup_job_problems(_registry(), name) == []


def _noop(job: Job) -> None:
    del job


@pytest.mark.parametrize("name", BACKUP_JOB_TYPES)
@pytest.mark.parametrize(
    ("lanes", "group", "problem"),
    [
        (frozenset({Lane.BULK, Lane.INTERACTIVE}), JobGroup.BACKUP, "bulk lane only"),
        (frozenset({Lane.BULK}), JobGroup.OTHER, "under Backup"),
    ],
)
def test_the_backup_job_check_catches_another_lane_or_group(
    name: str, lanes: frozenset[Lane], group: JobGroup, problem: str
) -> None:
    job_types = JobTypes()
    job_types.register_job_type(
        JobType(name, Lane.BULK, lanes, 80, timedelta(minutes=30), 2, _noop, group=group)
    )
    job_types.freeze()

    assert any(
        found.startswith(name) and problem in found
        for found in backup_job_problems(job_types, name)
    )


# The triggers are registered after their job types ----------------------------------------------


@pytest.mark.parametrize(
    ("trigger", "fallback"),
    [(Trigger.BACKUP_DB, time(10, 0)), (Trigger.RESTORE_TEST, time(11, 0))],
)
def test_each_backup_trigger_is_a_daily_schedule_on_its_registered_job_type(
    trigger: Trigger, fallback: time
) -> None:
    job_types = _registry()

    triggers = _triggers(job_types)

    [spec] = [spec for spec in triggers.specs() if spec.trigger is trigger]
    assert isinstance(spec.schedule, DailyAt)
    assert spec.job_type in job_types.names()
    assert spec.schedule.fallback == DailyRun(fallback, "UTC")


@pytest.mark.parametrize("missing", BACKUP_JOB_TYPES)
def test_the_registry_refuses_a_backup_trigger_before_its_job_type(missing: str) -> None:
    built = _registry()
    without = JobTypes()
    for name in built.names():
        if name != missing:
            without.register_job_type(built.get(name))
    without.freeze()

    with pytest.raises(UnknownJobType, match=missing):
        _triggers(without)


# Rotation never keeps erased data longer than 35 days -------------------------------------------


def rotation_problems(found: Setting) -> list[str]:
    """What lets ``found`` keep local copies longer than ``MAX_ROTATION_DAYS``."""
    problems = []
    if MAX_ROTATION_DAYS != 35:
        problems.append(f"MAX_ROTATION_DAYS is {MAX_ROTATION_DAYS}, not 35 (AD-17)")
    if found.default > MAX_ROTATION_DAYS:
        problems.append("the default is above the maximum")
    try:
        parse(found, str(MAX_ROTATION_DAYS + 1))
    except InvalidSettingValue as exc:
        if exc.rule != "maximum":
            problems.append(f"{MAX_ROTATION_DAYS + 1} is refused by {exc.rule}, not maximum")
    else:
        problems.append(f"{MAX_ROTATION_DAYS + 1} days is accepted")
    if parse(found, str(MAX_ROTATION_DAYS)) != MAX_ROTATION_DAYS:
        problems.append(f"{MAX_ROTATION_DAYS} days is not accepted")
    return problems


def test_the_rotation_setting_stops_at_35_days() -> None:
    found = settings_schema.setting(Key.BACKUP_ROTATION_DAYS)

    assert MAX_ROTATION_DAYS == 35
    assert rotation_problems(found) == []


def test_the_rotation_check_catches_a_looser_maximum() -> None:
    looser = Setting(
        key=Key.BACKUP_ROTATION_DAYS,
        type=SettingType.INTEGER,
        section=Section.ENGINE,
        label="Keep local backups for (days)",
        unit="7 to 90.",
        default=35,
        rules=(minimum(7, "7"), maximum(90, "90")),
    )

    assert rotation_problems(looser) == ["36 days is accepted"]
