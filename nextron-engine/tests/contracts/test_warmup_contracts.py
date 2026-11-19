"""The engine never runs warm-up exchanges (FR-33, Story 6.4): warm-up is done by hand or by an
outside tool, and the engine only records its facts and counts the tool's sends toward the
ceiling.

- **No job type and no trigger.** No job type ``main.build_job_types`` registers, and no
  ``Trigger`` (declared or registered by ``main.build_triggers``), names warm-up.
- **No port or adapter.** No module, class, function or method under ``nextron/ports``,
  ``nextron/adapters`` or ``nextron/jobs`` (and no name in ``nextron/main.py``) mentions
  ``warm``: no port method can send or run warm-up mail.
- **No warm-up module elsewhere.** The only files under ``nextron/`` named for warm-up are
  migrations; and the only functions anywhere under ``nextron/`` whose name mentions ``warm``
  are the record, read and check ones in ``ALLOWED_WARM_FUNCTIONS``, never one that sends or
  runs anything.

Each check runs on the real tree and on a probe that breaks it."""

import ast
from collections.abc import Iterable
from datetime import time
from pathlib import Path
from typing import Final

from nextron import main
from nextron.jobs.triggers import DailyRun, Trigger
from tests.contracts.test_job_contracts import registry

ENGINE_ROOT: Final = Path(__file__).resolve().parents[2]
NEXTRON: Final = ENGINE_ROOT / "nextron"
WARM: Final = "warm"
NO_WARM_DIRS: Final = ("ports", "adapters", "jobs")
MIGRATIONS: Final = Path("db", "alembic", "versions")
# Every function under nextron/ whose name mentions warm-up: each records or reads its facts.
ALLOWED_WARM_FUNCTIONS: Final = frozenset(
    {
        "record_warmup",  # nextron.app.sending.api.Sending
        "_record_warmup",
        "update_warmup",  # nextron.db.repos.sending.inboxes
        "check_warmup",  # nextron.domain.sending_inbox
        "warmup_days",  # nextron.domain.sending_policy
        "_warmup_ok",  # nextron.domain.send_gate: the warmup_ok predicate's check
        "stored_warmup",  # nextron.web.inboxes_page
        "submitted_warmup",
        "warmup_facts",
        "warmup_saved",
        "inbox_warmup",  # nextron.web.app: the warm-up form's POST
    }
)


def _mentions(text: str) -> bool:
    return WARM in text.lower()


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _names(tree: ast.AST) -> Iterable[str]:
    """Every module-level and nested name a tree defines or uses: classes, functions, methods,
    arguments, attributes, plain names and imported names."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield node.name
        elif isinstance(node, ast.arg):
            yield node.arg
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.alias):
            yield node.name
            if node.asname:
                yield node.asname


def no_warm_problems(root: Path) -> list[str]:
    """Each warm-up name in ``ports``, ``adapters``, ``jobs`` and ``main.py`` under ``root``."""
    files = [path for name in NO_WARM_DIRS for path in _python_files(root / name)]
    files.append(root / "main.py")
    problems = []
    for path in files:
        if not path.exists():
            continue
        relative = path.relative_to(root).as_posix()
        if _mentions(relative):
            problems.append(f"{relative}: the module is named for warm-up")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        problems.extend(
            f"{relative}: {name}" for name in sorted(set(_names(tree))) if _mentions(name)
        )
    return problems


def warm_function_problems(root: Path) -> list[str]:
    """Each file under ``root`` named for warm-up (migrations aside) and each function whose
    name mentions warm-up but isn't in ``ALLOWED_WARM_FUNCTIONS``."""
    problems = []
    for path in _python_files(root):
        relative = path.relative_to(root)
        if _mentions(relative.name) and relative.parent != MIGRATIONS:
            problems.append(f"{relative.as_posix()}: the module is named for warm-up")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and _mentions(node.name)
                and node.name not in ALLOWED_WARM_FUNCTIONS
            ):
                problems.append(f"{relative.as_posix()}: {node.name}")
    return problems


# Job types and triggers -------------------------------------------------------------------------


def test_no_job_type_runs_warmup() -> None:
    names = registry().names()
    assert names  # the registry is real
    assert [name for name in names if _mentions(name)] == []


def test_no_trigger_runs_warmup() -> None:
    assert [trigger for trigger in Trigger if _mentions(trigger.value)] == []
    triggers = main.build_triggers(
        registry(),
        backup_schedule=lambda: DailyRun(time(3, 15), "UTC"),
        restore_test_schedule=lambda: DailyRun(time(4, 15), "UTC"),
        recovery_active=lambda: True,
        domain_check_schedule=lambda: DailyRun(time(7, 0), "UTC"),
    )
    assert triggers.specs()
    assert [spec for spec in triggers.specs() if _mentions(spec.trigger.value)] == []


# Ports, adapters, jobs and modules --------------------------------------------------------------


def test_no_port_adapter_or_job_mentions_warmup() -> None:
    assert no_warm_problems(NEXTRON) == []


def test_only_the_record_and_read_functions_mention_warmup() -> None:
    assert warm_function_problems(NEXTRON) == []


def test_the_checks_catch_a_warmup_port_method_job_and_module(tmp_path: Path) -> None:
    root = tmp_path / "nextron"
    (root / "ports").mkdir(parents=True)
    (root / "jobs").mkdir()
    (root / "app").mkdir()
    (root / "ports" / "mail.py").write_text(
        "class MailPort:\n    def send_warmup(self, to: str) -> None: ...\n", encoding="utf-8"
    )
    (root / "jobs" / "handlers.py").write_text('NAME = "x"\nrun_warm = NAME\n', encoding="utf-8")
    (root / "main.py").write_text("def build():\n    return 1\n", encoding="utf-8")
    (root / "app" / "warmup_runner.py").write_text(
        "def exchange_warmup_mail() -> None: ...\n", encoding="utf-8"
    )

    assert no_warm_problems(root) == [
        "ports/mail.py: send_warmup",
        "jobs/handlers.py: run_warm",
    ]
    assert warm_function_problems(root) == [
        "app/warmup_runner.py: the module is named for warm-up",
        "app/warmup_runner.py: exchange_warmup_mail",
        "ports/mail.py: send_warmup",
    ]
