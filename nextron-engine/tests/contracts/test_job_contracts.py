"""Job contracts (AD-10), each with a live proof.

- **One registry.** Only ``build_job_types()`` in ``nextron/main.py`` constructs ``JobTypes`` or
  calls ``register_job_type``, under any alias.
- **It ships frozen and empty** until a story registers a job type.
- **The runner's imports.** ``nextron/jobs/runner.py`` imports only
  ``nextron.app.jobqueue.api``, ``nextron.app.queries.setup``, ``nextron.domain`` and the
  standard library: nothing from ``nextron.ports``, ``nextron.db`` or a third-party package.
- **Indexes and checks.** Every table's indexes (partial ones' ``sqlite_where`` included) and
  checks are the same in the model and in the database at head. Alembic's drift comparison sees
  neither a ``WHERE`` clause nor a check, so this compares the SQL itself; ``jobs`` is the first
  table with either.

The AST checks read the source; their proofs run them on a temporary copy of the tree with a
probe module (``tests/contracts/support.py``), never on the real tree.
"""

import ast
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from nextron import main
from nextron.db.models import Base
from nextron.db.models.jobqueue import JobRecord
from nextron.domain.job import JobTypes
from tests.contracts.support import ENGINE_ROOT, copy_with_probe

REGISTRY = ("nextron/main.py", "build_job_types")
RUNNER = "nextron/jobs/runner.py"
RUNNER_IMPORTS = ("nextron.app.jobqueue.api", "nextron.app.queries.setup", "nextron.domain")


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# One registry -----------------------------------------------------------------------------------


class _RegistrationChecker(ast.NodeVisitor):
    def __init__(self, label: str, tree: ast.Module) -> None:
        self.label = label
        self.problems: list[str] = []
        self.functions: list[str] = []
        self.registry_names = {"JobTypes"} | {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "nextron.domain.job"
            for alias in node.names
            if alias.name == "JobTypes"
        }

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        what = None
        if name == "register_job_type":
            what = "calls register_job_type"
        elif (isinstance(func, ast.Name) and name in self.registry_names) or (
            isinstance(func, ast.Attribute) and name == "JobTypes"
        ):
            what = "constructs JobTypes"
        if what and (self.label, self.functions[:1]) != (REGISTRY[0], [REGISTRY[1]]):
            self.problems.append(f"{self.label}:{node.lineno} {what}")
        self.generic_visit(node)


def registration_problems(root: Path) -> list[str]:
    """Each call outside ``main.build_job_types`` that builds or fills a job type registry."""
    problems = []
    for path in sorted((root / "nextron").rglob("*.py")):
        label = path.relative_to(root).as_posix()
        checker = _RegistrationChecker(label, _parse(path))
        checker.visit(_parse(path))
        problems += checker.problems
    return problems


def test_only_build_job_types_in_main_builds_the_registry() -> None:
    assert registration_problems(ENGINE_ROOT) == []
    tree = _parse(ENGINE_ROOT / REGISTRY[0])
    [build] = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == REGISTRY[1]
    ]
    assert any(isinstance(node, ast.Call) for node in ast.walk(build))


@pytest.mark.parametrize(
    ("source", "what"),
    [
        ("from nextron.domain.job import JobTypes\nregistry = JobTypes()", "constructs"),
        ("from nextron.domain.job import JobTypes as Types\nregistry = Types()", "constructs"),
        ("from nextron.domain import job\nregistry = job.JobTypes()", "constructs"),
        ("import nextron.domain.job\nregistry = nextron.domain.job.JobTypes()", "constructs"),
        ("def wire(registry, job_type):\n    registry.register_job_type(job_type)", "calls"),
        (
            "from nextron.domain.job import JobTypes\n"
            "def wire(registry, job_type):\n"
            "    JobTypes.register_job_type(registry, job_type)",
            "calls",
        ),
    ],
)
def test_the_registry_check_catches_registration_outside_main(
    source: str, what: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", source + "\n")

    [problem] = registration_problems(root)

    assert problem.startswith("nextron/jobs/_lint_probe.py:")
    assert what in problem


def test_the_registry_check_catches_another_function_in_main(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", '"""unused"""\n')
    main_py = root / "nextron" / "main.py"
    main_py.write_text(
        main_py.read_text(encoding="utf-8")
        + "\n\ndef late_job_types(job_type) -> JobTypes:\n"
        "    registry = JobTypes()\n"
        "    registry.register_job_type(job_type)\n"
        "    return registry\n",
        encoding="utf-8",
    )

    problems = registration_problems(root)

    assert [problem.split(" ", 1)[1] for problem in problems] == [
        "constructs JobTypes",
        "calls register_job_type",
    ]
    assert all(problem.startswith("nextron/main.py:") for problem in problems)


def test_build_job_types_is_frozen_and_empty() -> None:
    job_types = main.build_job_types()

    assert isinstance(job_types, JobTypes)
    assert job_types.frozen
    assert job_types.names() == ()
    assert main.build_job_types() is not job_types  # a new registry each time


# The runner's imports ---------------------------------------------------------------------------


def _targets(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:  # a relative import: inside nextron.jobs
        base = "nextron.jobs" + (f".{node.module}" if node.module else "")
        return [f"{base}.{alias.name}" for alias in node.names]
    module = node.module or ""
    if module.startswith("nextron"):
        # ``from nextron.app.queries import setup`` imports the module nextron.app.queries.setup
        return [f"{module}.{alias.name}" for alias in node.names]
    return [module]


def _allowed(target: str) -> bool:
    if target == "nextron" or target.startswith("nextron."):
        return any(target == p or target.startswith(p + ".") for p in RUNNER_IMPORTS) or any(
            # a name imported from an allowed module, such as nextron.app.jobqueue.api.Claim
            target.rpartition(".")[0] == p for p in RUNNER_IMPORTS
        )
    return target.split(".")[0] in sys.stdlib_module_names


def runner_import_problems(root: Path) -> list[str]:
    """Each import in the runner outside its allowed modules and the standard library."""
    path = root / RUNNER
    problems = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            problems += [
                f"{RUNNER}:{node.lineno} imports {target}"
                for target in _targets(node)
                if not _allowed(target)
            ]
    return problems


def test_the_runner_imports_only_the_queue_the_settings_query_the_domain_and_stdlib() -> None:
    assert runner_import_problems(ENGINE_ROOT) == []
    source = (ENGINE_ROOT / RUNNER).read_text(encoding="utf-8")
    assert "nextron.app.jobqueue.api" in source and "nextron.app.queries" in source


@pytest.mark.parametrize(
    ("line", "target"),
    [
        ("from nextron.ports.errors import AdapterError", "nextron.ports.errors.AdapterError"),
        ("import nextron.ports.clock", "nextron.ports.clock"),
        ("from nextron.db import uow", "nextron.db.uow"),
        ("from nextron.db.uow import current", "nextron.db.uow.current"),
        ("from nextron.app.setup import api", "nextron.app.setup.api"),
        ("from nextron.app.queries import jobqueue", "nextron.app.queries.jobqueue"),
        ("from nextron.adapters.clock import SystemClock", "nextron.adapters.clock.SystemClock"),
        ("import sqlalchemy", "sqlalchemy"),
        (
            "from apscheduler.schedulers.asyncio import AsyncIOScheduler",
            "apscheduler.schedulers.asyncio",
        ),
    ],
)
def test_the_runner_import_check_catches_any_other_import(
    line: str, target: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", '"""unused"""\n')
    runner = root / RUNNER
    runner.write_text(
        runner.read_text(encoding="utf-8").replace(
            "import contextvars\n", f"import contextvars\n{line}\n", 1
        ),
        encoding="utf-8",
    )

    [problem] = runner_import_problems(root)

    assert problem.endswith(f"imports {target}")


@pytest.mark.parametrize(
    "line",
    [
        "from nextron.app.queries import setup as settings_reader",
        "from nextron.domain.ids import is_ulid",
        "import nextron.domain.job",
        "from collections import deque",
    ],
)
def test_the_runner_import_check_allows_its_own_modules(line: str, tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.jobs", '"""unused"""\n')
    runner = root / RUNNER
    runner.write_text(
        runner.read_text(encoding="utf-8").replace(
            "import contextvars\n", f"import contextvars\n{line}\n", 1
        ),
        encoding="utf-8",
    )

    assert runner_import_problems(root) == []


# Indexes and checks -----------------------------------------------------------------------------

_CHECK = re.compile(r"CONSTRAINT (ck_\w+) CHECK \((.*)\),?$")


def _squeeze(sql: str) -> str:
    return " ".join(sql.split())


def _checks(table_sql: str) -> dict[str, str]:
    return {
        match[1]: match[2]
        for line in table_sql.splitlines()
        if (match := _CHECK.match(line.strip()))
    }


def _model_schema(table: Table) -> tuple[dict[str, str], dict[str, str]]:
    dialect = sqlite.dialect()
    indexes = {
        str(index.name): _squeeze(str(CreateIndex(index).compile(dialect=dialect)))
        for index in table.indexes
    }
    return indexes, _checks(str(CreateTable(table).compile(dialect=dialect)))


def _stored_schema(engine: Engine, name: str) -> tuple[dict[str, str], dict[str, str]]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT type, name, sql FROM sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL",
            (name,),
        ).all()
    indexes = {index: _squeeze(sql) for kind, index, sql in rows if kind == "index"}
    tables = [sql for kind, _, sql in rows if kind == "table"]
    return indexes, _checks("\n".join(tables))


def schema_differences(engine: Engine) -> list[str]:
    """Each index or check of a model table that differs between the model and ``engine``."""
    problems = []
    for table in Base.metadata.sorted_tables:
        model_indexes, model_checks = _model_schema(table)
        stored_indexes, stored_checks = _stored_schema(engine, table.name)
        for kind, model, stored in (
            ("index", model_indexes, stored_indexes),
            ("check", model_checks, stored_checks),
        ):
            for name in sorted(model.keys() | stored.keys()):
                if model.get(name) != stored.get(name):
                    problems.append(
                        f"{table.name}: {kind} {name}: model {model.get(name)!r}, "
                        f"database {stored.get(name)!r}"
                    )
    return problems


def test_every_tables_indexes_and_checks_match_the_database_at_head(db_engine: Engine) -> None:
    assert schema_differences(db_engine) == []
    assert {table.name for table in Base.metadata.sorted_tables} >= {"events", "settings", "jobs"}


def test_the_jobs_partial_indexes_and_checks_are_the_specified_ones() -> None:
    indexes, checks = _model_schema(JobRecord.__table__)  # type: ignore[arg-type]

    assert indexes["uq_jobs_live_key"].endswith("WHERE status IN ('queued','running')")
    assert indexes["ix_jobs_running_concurrency"].endswith("WHERE status = 'running'")
    assert set(checks) == {
        "ck_jobs_status",
        "ck_jobs_lane",
        "ck_jobs_attempts",
        "ck_jobs_priority",
        "ck_jobs_lease",
    }


@pytest.mark.parametrize(
    ("statements", "named"),
    [
        (
            [
                "DROP INDEX uq_jobs_live_key",
                "CREATE UNIQUE INDEX uq_jobs_live_key ON jobs (idempotency_key)",
            ],
            "jobs: index uq_jobs_live_key",
        ),
        (
            [
                "DROP INDEX ix_jobs_running_concurrency",
                "CREATE INDEX ix_jobs_running_concurrency ON jobs (concurrency_key) "
                "WHERE status = 'queued'",
            ],
            "jobs: index ix_jobs_running_concurrency",
        ),
        (["DROP INDEX ix_jobs_lease"], "jobs: index ix_jobs_lease"),
        (["DROP INDEX ix_events_at"], "events: index ix_events_at"),
        (
            ["CREATE INDEX ix_settings_updated_at ON settings (updated_at) WHERE value = ''"],
            "settings: index ix_settings_updated_at",
        ),
    ],
)
def test_the_schema_check_catches_an_index_the_model_does_not_declare(
    statements: list[str], named: str, db_engine: Engine
) -> None:
    with db_engine.connect() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.commit()

    [problem] = schema_differences(db_engine)

    assert problem.startswith(named)


def test_the_schema_check_catches_a_changed_check(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        connection.exec_driver_sql("ALTER TABLE jobs RENAME TO jobs_old")
        [table_sql] = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'jobs_old'"
        ).one()
        connection.exec_driver_sql(
            table_sql.replace("jobs_old", "jobs", 1).replace("attempts >= 0", "attempts >= 1")
        )
        connection.commit()

    problems = schema_differences(db_engine)

    assert any(problem.startswith("jobs: check ck_jobs_attempts") for problem in problems)
