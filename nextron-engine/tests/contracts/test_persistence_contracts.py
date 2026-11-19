"""Persistence contracts (AD-2, AD-3, AD-4, AD-17, SC-3, SC-4, SC-8), each with a live proof.

- **One configurator.** Only ``nextron/db/engine.py`` creates an SQLAlchemy engine
  (``create_engine``, ``engine_from_config``, ``create_async_engine``,
  ``async_engine_from_config``, ``create_mock_engine``, ``create_pool_from_url``) or calls
  ``sqlite3.connect``, under any alias.
- **One registry.** Only ``build_subscribers()`` in ``nextron/main.py`` constructs
  ``Subscribers`` or calls ``register_subscriber``.
- **Owner and PII coverage.** ``owners.TABLES`` and ``pii_registry.PII`` match
  ``Base.metadata`` exactly: every column has exactly one writer (an owner or a shared writer)
  and exactly one class, nothing stale is listed, and every ``events`` column is ``none``.
- **Metadata rules.** Each table's primary key is exactly one ``id`` column of type ``Ulid``; no
  column uses SQLAlchemy ``DateTime``; no foreign key uses ``ON DELETE CASCADE``; and
  ``nextron/db/models/__init__.py`` imports every model module.
- **Alembic.** One linear chain with a single head; each revision id equals its file stem,
  fullmatches ``^\\d{4}_[a-z0-9_]+$``, has at most 31 characters and starts with its 1-based
  position; no branch labels or dependencies; every ``downgrade()`` raises
  ``NotImplementedError``; ``env.py`` configures ``render_as_batch=True`` and
  ``target_metadata=Base.metadata``; and there is no ``alembic.ini``, ``script.py.mako`` or
  ``__init__.py`` in the script directory.
- **Drift.** ``compare_metadata`` between the models and a database at head is empty.
- **Triggers.** The three ``events`` triggers exist at head (a batch rebuild drops them).
- **Reparenting.** For every column of a table other than ``leads`` with a foreign key to
  ``leads.id``, each owner that writes it in ``owners.TABLES`` registers a ``reparent_lead``
  bound to a class of ``nextron.app.<owner>.api`` for ``LeadsMerged`` in
  ``main.build_subscribers()``, so a merge never strands a Lead-scoped row on the merged Lead.

The AST checks read the source; their proofs run them on a temporary copy of the tree with a
probe module (``tests/contracts/support.py``), never on the real tree.
"""

import ast
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.types import TypeDecorator

from nextron import main
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.sequencing.api import Sequencing
from nextron.db.migrations import SCRIPT_LOCATION, alembic_config
from nextron.db.models import Base, Ulid, UtcDateTime
from nextron.db.owners import OWNERS, SHARED_WRITERS, TABLES
from nextron.db.pii_registry import PII, PiiClass
from nextron.domain.notifications import LeadsMerged
from tests.contracts.support import ENGINE_ROOT, copy_with_probe
from tests.fakes.clock import FrozenClock

CONFIGURATOR = "nextron/db/engine.py"
ENGINE_FACTORIES = frozenset(
    {
        "create_engine",
        "engine_from_config",
        "create_async_engine",
        "async_engine_from_config",
        "create_mock_engine",
        "create_pool_from_url",
    }
)
SQLITE_ROOTS = frozenset({"sqlite3", "_sqlite3"})
REGISTRY = ("nextron/main.py", "build_subscribers")
REVISION_ID = re.compile(r"\d{4}_[a-z0-9_]+")  # matched with fullmatch
MAX_REVISION_LENGTH = 31
HEAD = "0047_6_6_reputation"
EVENTS_TRIGGERS = frozenset(
    {"trg_events_no_update", "trg_events_no_delete", "trg_events_no_id_reuse"}
)
COMPANY_FACTS_TRIGGERS = frozenset(  # Story 4.3: append-only fact versions
    {
        "trg_company_facts_no_delete",
        "trg_company_facts_no_id_reuse",
        "trg_company_facts_immutable",
    }
)
CAMPAIGNS_TRIGGERS = frozenset(  # Story 4.2: no UK Campaign runs without its LIA (NFR-3)
    {"trg_campaigns_lia_insert", "trg_campaigns_lia_update"}
)
VARIANTS_TRIGGERS = frozenset({"trg_variants_at_most_two"})  # Story 4.2: at most 2 Variants
EVIDENCE_TRIGGERS = frozenset(  # Story 2.9: Evidence is immutable
    {"trg_evidence_no_delete", "trg_evidence_immutable"}
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _sources(root: Path) -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(root).as_posix(), _parse(path))
        for path in sorted((root / "nextron").rglob("*.py"))
    ]


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


# One configurator -----------------------------------------------------------------------------


def _is_sqlalchemy(module: str) -> bool:
    return module == "sqlalchemy" or module.startswith("sqlalchemy.")


def _is_sqlite(module: str) -> bool:
    return module.split(".")[0] in SQLITE_ROOTS


def _engine_creation_in(label: str, tree: ast.Module) -> list[str]:
    found: set[tuple[int, str]] = set()
    sqlalchemy_names: set[str] = set()  # names bound to sqlalchemy or something inside it
    sqlite_names: set[str] = set()  # names bound to sqlite3, sqlite3.dbapi2 or _sqlite3

    def flag(node: ast.stmt | ast.expr, what: str) -> None:
        found.add((node.lineno, what))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if _is_sqlalchemy(alias.name):
                    sqlalchemy_names.add(bound)
                if _is_sqlite(alias.name):
                    sqlite_names.add(bound)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if _is_sqlalchemy(node.module):
                    if alias.name == "*" or alias.name in ENGINE_FACTORIES:
                        flag(node, f"imports {alias.name} from {node.module}")
                    sqlalchemy_names.add(alias.asname or alias.name)
                if _is_sqlite(node.module):
                    if alias.name in ("*", "connect"):
                        flag(node, f"imports {alias.name} from {node.module}")
                    sqlite_names.add(alias.asname or alias.name)

    def creates(owner: ast.expr, name: str) -> bool:
        root = _root_name(owner)
        return (name in ENGINE_FACTORIES and root in sqlalchemy_names) or (
            name == "connect" and root in sqlite_names
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and creates(node.value, node.attr):
            flag(node, f"uses {ast.unparse(node)}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and creates(node.args[0], node.args[1].value)
        ):
            flag(node, f"uses {ast.unparse(node)}")
    return [f"{label}:{line} {what}" for line, what in sorted(found)]


def engine_creation_problems(root: Path) -> list[str]:
    """Each place outside ``engine.py`` that creates an engine or calls ``sqlite3.connect``."""
    problems = []
    for label, tree in _sources(root):
        if label != CONFIGURATOR:
            problems += _engine_creation_in(label, tree)
    return problems


def test_only_engine_py_creates_engines_or_sqlite_connections() -> None:
    assert engine_creation_problems(ENGINE_ROOT) == []


def test_the_configurator_itself_is_what_the_check_would_catch() -> None:
    tree = _parse(ENGINE_ROOT / CONFIGURATOR)

    assert _engine_creation_in(CONFIGURATOR, tree) != []


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy\nsqlalchemy.create_engine('sqlite://')",
        "import sqlalchemy as sa\nengine = sa.create_engine('sqlite://')",
        "from sqlalchemy import create_engine",
        "from sqlalchemy import create_engine as make_engine",
        "from sqlalchemy.engine import engine_from_config",
        "from sqlalchemy import engine\nengine.engine_from_config({})",
        "from sqlalchemy.ext.asyncio import create_async_engine",
        "import sqlalchemy.ext.asyncio\nsqlalchemy.ext.asyncio.async_engine_from_config({})",
        "from sqlalchemy.ext import asyncio as aio\naio.create_async_engine('x')",
        "from sqlalchemy import create_mock_engine",
        "from sqlalchemy import create_pool_from_url",
        "from sqlalchemy import *",
        "import sqlalchemy\nmake = getattr(sqlalchemy, 'create_engine')",
        "import sqlite3\nsqlite3.connect('x.db')",
        "import sqlite3 as db\ndb.connect('x.db')",
        "from sqlite3 import connect",
        "from sqlite3 import connect as open_database",
        "from sqlite3 import dbapi2\ndbapi2.connect('x.db')",
        "import sqlite3.dbapi2 as dbapi\ndbapi.connect('x.db')",
        "import sqlite3.dbapi2\nsqlite3.dbapi2.connect('x.db')",
        "import _sqlite3\n_sqlite3.connect('x.db')",
        "from sqlite3 import *",
        "import sqlite3\nopen_database = getattr(sqlite3, 'connect')",
        "def later() -> None:\n    db.connect('x.db')\n\nimport sqlite3 as db",
    ],
)
def test_the_configurator_check_catches_another_engine_or_connection(
    source: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app", source + "\n")

    problems = engine_creation_problems(root)

    assert problems
    assert all(problem.startswith("nextron/app/_lint_probe.py:") for problem in problems)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nfrom nextron.db.engine import create_engine\n"
        "create_engine(Path('x.db'))",
        "from pathlib import Path\nfrom nextron.db import engine\nengine.create_engine(Path('x'))",
        "import sqlite3\nversion = sqlite3.sqlite_version",
        "import sqlalchemy as sa\ncolumn = sa.Column('id', sa.Integer)",
        "from sqlalchemy import Engine, event",
    ],
)
def test_the_configurator_check_allows_everything_else(source: str, tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app", source + "\n")

    assert engine_creation_problems(root) == []


# One registry ---------------------------------------------------------------------------------


class _RegistrationChecker(ast.NodeVisitor):
    def __init__(self, label: str, tree: ast.Module) -> None:
        self.label = label
        self.problems: list[str] = []
        self.functions: list[str] = []
        self.registry_names = {"Subscribers"} | {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "nextron.db.uow"
            for alias in node.names
            if alias.name == "Subscribers"
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
        if name == "register_subscriber":
            what = "calls register_subscriber"
        elif (isinstance(func, ast.Name) and name in self.registry_names) or (
            isinstance(func, ast.Attribute) and name == "Subscribers"
        ):
            what = "constructs Subscribers"
        if what and (self.label, self.functions[:1]) != (REGISTRY[0], [REGISTRY[1]]):
            self.problems.append(f"{self.label}:{node.lineno} {what}")
        self.generic_visit(node)


def registration_problems(root: Path) -> list[str]:
    """Each call outside ``main.build_subscribers`` that builds or fills the registry."""
    problems = []
    for label, tree in _sources(root):
        checker = _RegistrationChecker(label, tree)
        checker.visit(tree)
        problems += checker.problems
    return problems


def test_only_build_subscribers_in_main_registers_subscribers() -> None:
    assert registration_problems(ENGINE_ROOT) == []
    tree = _parse(ENGINE_ROOT / REGISTRY[0])
    [build] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == REGISTRY[1]
    ]
    assert any(isinstance(node, ast.Call) for node in ast.walk(build))


@pytest.mark.parametrize(
    ("source", "what"),
    [
        ("from nextron.db.uow import Subscribers\nregistry = Subscribers()", "constructs"),
        ("from nextron.db.uow import Subscribers as Registry\nregistry = Registry()", "constructs"),
        ("from nextron.db import uow\nregistry = uow.Subscribers()", "constructs"),
        ("import nextron.db.uow\nregistry = nextron.db.uow.Subscribers()", "constructs"),
        ("def wire(registry, kind, fn):\n    registry.register_subscriber(kind, fn)", "calls"),
        (
            "from nextron.db.uow import Subscribers\n"
            "def wire(registry, kind, command):\n"
            "    Subscribers.register_subscriber(registry, kind, command)",
            "calls",
        ),
    ],
)
def test_the_registry_check_catches_registration_outside_main(
    source: str, what: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app", source + "\n")

    [problem] = registration_problems(root)

    assert problem.startswith("nextron/app/_lint_probe.py:")
    assert what in problem


def test_the_registry_check_catches_another_function_in_main(tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.app", '"""unused"""\n')
    main_py = root / "nextron" / "main.py"
    main_py.write_text(
        main_py.read_text(encoding="utf-8")
        + "\n\ndef late_subscribers(kind, command) -> Subscribers:\n"
        "    registry = Subscribers()\n"
        "    registry.register_subscriber(kind, command)\n"
        "    return registry\n",
        encoding="utf-8",
    )

    problems = registration_problems(root)

    assert [problem.split(" ", 1)[1] for problem in problems] == [
        "constructs Subscribers",
        "calls register_subscriber",
    ]
    assert all(problem.startswith("nextron/main.py:") for problem in problems)


# Owner and PII coverage -----------------------------------------------------------------------


def coverage_problems(
    metadata: MetaData,
    tables: Mapping[str, Mapping[str, Sequence[str]]] = TABLES,
    pii: Mapping[str, Mapping[str, object]] = PII,
    writers: Sequence[str] = (*OWNERS, *SHARED_WRITERS),
) -> list[str]:
    """Every way ``tables`` and ``pii`` fail to match ``metadata`` exactly."""
    problems = []
    for table in metadata.tables.values():
        mapping = tables.get(table.name, {})
        classes = pii.get(table.name, {})
        for column in table.columns:
            name = f"{table.name}.{column.name}"
            owners_of = [writer for writer, columns in mapping.items() if column.name in columns]
            if not owners_of:
                problems.append(f"{name} is unmapped in owners.TABLES")
            elif len(owners_of) > 1:
                problems.append(f"{name} has {len(owners_of)} writers: {', '.join(owners_of)}")
            if column.name not in classes:
                problems.append(f"{name} is unclassified in pii_registry.PII")
    for table_name, mapping in tables.items():
        table = metadata.tables.get(table_name)
        if table is None:
            problems.append(f"owners.TABLES lists {table_name}, which is not a model table")
            continue
        for writer, columns in mapping.items():
            if writer not in writers:
                problems.append(f"owners.TABLES[{table_name!r}] has unknown writer {writer!r}")
            if len(columns) != len(set(columns)):
                problems.append(f"owners.TABLES[{table_name!r}][{writer!r}] repeats a column")
            for column in columns:
                if column not in table.columns:
                    problems.append(f"owners.TABLES lists {table_name}.{column}, which is stale")
    for table_name, classes in pii.items():
        table = metadata.tables.get(table_name)
        if table is None:
            problems.append(f"pii_registry.PII lists {table_name}, which is not a model table")
            continue
        for column, pii_class in classes.items():
            if column not in table.columns:
                problems.append(f"pii_registry.PII lists {table_name}.{column}, which is stale")
            if not isinstance(pii_class, PiiClass):
                problems.append(f"{table_name}.{column} has class {pii_class!r}, not a PiiClass")
            elif table_name == "events" and pii_class is not PiiClass.NONE:
                problems.append(
                    f"events.{column} is {pii_class.value}; events holds no personal data"
                )
    return problems


def metadata_copy() -> MetaData:
    copy = MetaData(naming_convention=dict(Base.metadata.naming_convention))
    for table in Base.metadata.tables.values():
        table.to_metadata(copy)
    return copy


def test_every_column_has_one_writer_and_one_pii_class() -> None:
    assert coverage_problems(Base.metadata) == []
    assert set(TABLES) == set(PII) == set(Base.metadata.tables)


def test_every_events_column_is_none_and_written_by_the_event_log() -> None:
    columns = [column.name for column in Base.metadata.tables["events"].columns]

    assert PII["events"] == dict.fromkeys(columns, PiiClass.NONE)
    assert TABLES["events"] == {"db.eventlog": tuple(columns)}


def test_the_coverage_check_names_an_unmapped_dummy_column() -> None:
    metadata = metadata_copy()
    metadata.tables["events"].append_column(Column("dummy", String(10)))

    assert coverage_problems(metadata) == [
        "events.dummy is unmapped in owners.TABLES",
        "events.dummy is unclassified in pii_registry.PII",
    ]


def test_the_coverage_check_names_a_table_missing_from_both_maps() -> None:
    metadata = metadata_copy()
    Table("probe", metadata, Column("id", Ulid, primary_key=True))

    assert coverage_problems(metadata) == [
        "probe.id is unmapped in owners.TABLES",
        "probe.id is unclassified in pii_registry.PII",
    ]


def test_the_coverage_check_names_stale_doubled_and_unknown_entries() -> None:
    columns = TABLES["events"]["db.eventlog"]
    tables = {
        **TABLES,
        "events": {"db.eventlog": (*columns, "ghost"), "pipeline": ("at",), "cron": ()},
        "gone": {"setup": ("id",)},
    }
    pii: dict[str, dict[str, object]] = {
        **PII,
        "events": {**PII["events"], "actor": PiiClass.REDACT, "ghost": PiiClass.NONE, "at": "none"},
        "gone": {"id": PiiClass.NONE},
    }

    assert coverage_problems(Base.metadata, tables, pii) == [
        "events.at has 2 writers: db.eventlog, pipeline",
        "owners.TABLES lists events.ghost, which is stale",
        "owners.TABLES['events'] has unknown writer 'cron'",
        "owners.TABLES lists gone, which is not a model table",
        "events.actor is redact; events holds no personal data",
        "events.at has class 'none', not a PiiClass",
        "pii_registry.PII lists events.ghost, which is stale",
        "pii_registry.PII lists gone, which is not a model table",
    ]


# Reparenting ----------------------------------------------------------------------------------

LEADS_ID = "leads.id"
REPARENT = "reparent_lead"


def reparenting_owners(subscribers: Sequence[object]) -> set[str]:
    """The owners whose ``reparent_lead`` is among ``subscribers``: each bound to an instance
    of a class defined in ``nextron.app.<owner>.api``."""
    owners = set()
    for subscriber in subscribers:
        bound_to = getattr(subscriber, "__self__", None)
        if getattr(subscriber, "__name__", None) != REPARENT or bound_to is None:
            continue
        parts = type(bound_to).__module__.split(".")
        if len(parts) == 4 and parts[:2] == ["nextron", "app"] and parts[3] == "api":
            owners.add(parts[2])
    return owners


def reparent_problems(
    metadata: MetaData,
    subscribers: Sequence[object],
    tables: Mapping[str, Mapping[str, Sequence[str]]] = TABLES,
) -> list[str]:
    """Each Lead-scoped column whose writer registers no ``reparent_lead`` for
    ``LeadsMerged``."""
    registered = reparenting_owners(subscribers)
    problems = []
    for table in metadata.tables.values():
        if table.name == "leads":
            continue
        for column in table.columns:
            if not any(key.target_fullname == LEADS_ID for key in column.foreign_keys):
                continue
            name = f"{table.name}.{column.name}"
            mapping = tables.get(table.name, {})
            writers = [writer for writer, names in mapping.items() if column.name in names]
            if not writers:
                problems.append(f"{name} references {LEADS_ID} but has no writer")
            for writer in writers:
                if writer not in registered:
                    problems.append(
                        f"{name} references {LEADS_ID}, but {writer} registers no {REPARENT} "
                        "for LeadsMerged"
                    )
    return problems


def lead_scoped_columns(metadata: MetaData) -> list[str]:
    return [
        f"{table.name}.{column.name}"
        for table in metadata.tables.values()
        if table.name != "leads"
        for column in table.columns
        if any(key.target_fullname == LEADS_ID for key in column.foreign_keys)
    ]


def test_every_owner_of_a_lead_scoped_row_reparents_it_on_a_merge() -> None:
    subscribers = main.build_subscribers().subscribers_of(LeadsMerged)

    assert lead_scoped_columns(Base.metadata) == [
        "crawl_runs.lead_id",  # Story 2.5
        "crawled_pages.lead_id",
        "ats_links.lead_id",
        "job_postings.lead_id",  # Story 2.11
        "contacts.lead_id",  # Story 2.3
        "enrollments.lead_id",  # Story 4.8
        "evidence.lead_id",  # Story 2.9
        "signals.lead_id",
        "extraction_runs.lead_id",
    ]
    assert reparenting_owners(subscribers) == {"pipeline", "research", "sequencing"}
    assert reparent_problems(Base.metadata, subscribers) == []


def test_the_reparent_check_names_a_table_whose_owner_registers_nothing() -> None:
    metadata = metadata_copy()
    Table(
        "probe_rows",
        metadata,
        Column("id", Ulid, primary_key=True),
        Column("lead_id", Ulid, ForeignKey("leads.id")),
    )
    tables = {**TABLES, "probe_rows": {"scoring": ("id", "lead_id")}}
    subscribers = main.build_subscribers().subscribers_of(LeadsMerged)

    assert reparent_problems(metadata, subscribers, tables) == [
        "probe_rows.lead_id references leads.id, but scoring registers no reparent_lead for "
        "LeadsMerged"
    ]


def test_the_reparent_check_names_a_missing_registration() -> None:
    clock = FrozenClock()
    # research's other command is no reparent_lead
    subscribers = [
        Pipeline(clock).reparent_lead,
        Research(clock).add_contact,
        Sequencing(clock).reparent_lead,
    ]

    assert reparent_problems(Base.metadata, subscribers) == [
        f"{column} references leads.id, but research registers no reparent_lead for LeadsMerged"
        for column in (
            "crawl_runs.lead_id",  # Story 2.5
            "crawled_pages.lead_id",
            "ats_links.lead_id",
            "job_postings.lead_id",  # Story 2.11
            "contacts.lead_id",  # Story 2.3
            "evidence.lead_id",  # Story 2.9
            "signals.lead_id",
            "extraction_runs.lead_id",
        )
    ]


# Metadata rules -------------------------------------------------------------------------------


def _is_datetime(column_type: object) -> bool:
    if isinstance(column_type, TypeDecorator):
        impl = column_type.impl
        return _is_datetime(impl) or (isinstance(impl, type) and issubclass(impl, DateTime))
    return isinstance(column_type, DateTime)


def metadata_problems(metadata: MetaData) -> list[str]:
    """Primary keys, datetimes and cascades that break the model-base rules."""
    problems = []
    for table in metadata.tables.values():
        key = list(table.primary_key.columns)
        if [column.name for column in key] != ["id"] or not isinstance(key[0].type, Ulid):
            problems.append(f"{table.name}: the primary key must be one Ulid column named id")
        for column in table.columns:
            if _is_datetime(column.type):
                problems.append(f"{table.name}.{column.name} uses DateTime; use UtcDateTime")
            for foreign_key in column.foreign_keys:
                if (foreign_key.ondelete or "").upper() == "CASCADE":
                    problems.append(f"{table.name}.{column.name} uses ON DELETE CASCADE")
    return problems


def test_the_models_follow_the_key_and_datetime_rules() -> None:
    assert metadata_problems(Base.metadata) == []
    assert any(isinstance(column.type, UtcDateTime) for column in Base.metadata.tables["events"].c)


@pytest.mark.parametrize(
    ("columns", "problem"),
    [
        ([Column("id", Integer, primary_key=True)], "bad: the primary key"),
        ([Column("key", Ulid, primary_key=True)], "bad: the primary key"),
        (
            [Column("id", Ulid, primary_key=True), Column("n", Integer, primary_key=True)],
            "bad: the primary key",
        ),
        ([Column("id", String(26), primary_key=True)], "bad: the primary key"),
        ([Column("id", Ulid, primary_key=True), Column("at", DateTime)], "bad.at uses DateTime"),
        (
            [Column("id", Ulid, primary_key=True), Column("seen", DateTime(timezone=True))],
            "bad.seen uses DateTime",
        ),
        (
            [
                Column("id", Ulid, primary_key=True),
                Column("event_id", Ulid, ForeignKey("events.id", ondelete="CASCADE")),
            ],
            "bad.event_id uses ON DELETE CASCADE",
        ),
    ],
)
def test_the_metadata_check_catches_a_bad_table(
    columns: list[Column[object]], problem: str
) -> None:
    metadata = metadata_copy()
    Table("bad", metadata, *columns)

    [found] = metadata_problems(metadata)
    assert found.startswith(problem)


def unimported_model_modules(root: Path) -> list[str]:
    """Model modules that ``nextron/db/models/__init__.py`` does not import."""
    models = root / "nextron" / "db" / "models"
    modules = {path.stem for path in models.glob("*.py")} - {"__init__"}
    imported: set[str] = set()
    for node in ast.walk(_parse(models / "__init__.py")):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 1:
                module = f"nextron.db.models.{module}".rstrip(".")
            if module == "nextron.db.models":
                imported |= {alias.name for alias in node.names}
            elif module.startswith("nextron.db.models."):
                imported.add(module.split(".")[3])
        elif isinstance(node, ast.Import):
            imported |= {
                alias.name.split(".")[3]
                for alias in node.names
                if alias.name.startswith("nextron.db.models.")
            }
    return sorted(modules - imported)


def test_the_models_package_imports_every_model_module(tmp_path: Path) -> None:
    assert unimported_model_modules(ENGINE_ROOT) == []

    _, root = copy_with_probe(tmp_path, "nextron.db.models", '"""A model module."""\n')

    assert unimported_model_modules(root) == ["_lint_probe"]


# Alembic --------------------------------------------------------------------------------------


def env_problems(env_py: Path) -> list[str]:
    """``env.py`` must configure the handed-over connection, batch mode and the metadata."""
    calls = [
        node
        for node in ast.walk(_parse(env_py))
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "context.configure"
    ]
    if len(calls) != 1:
        return [f"env.py calls context.configure {len(calls)} times; expected once"]
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    problems = []
    batch = keywords.get("render_as_batch")
    if not (isinstance(batch, ast.Constant) and batch.value is True):
        problems.append("env.py does not configure render_as_batch=True")
    target = keywords.get("target_metadata")
    if target is None or ast.unparse(target) != "Base.metadata":
        problems.append("env.py does not configure target_metadata=Base.metadata")
    if "connection" not in keywords:
        problems.append("env.py does not configure the handed-over connection")
    return problems


def chain_problems(location: Path) -> list[str]:
    """Everything that makes the script directory at ``location`` more than one linear chain."""
    problems = []
    for unwanted in ("__init__.py", "script.py.mako", "alembic.ini", "versions/__init__.py"):
        if (location / unwanted).exists():
            problems.append(f"the script directory holds {unwanted}")
    problems += env_problems(location / "env.py")
    try:
        script = ScriptDirectory.from_config(alembic_config(location))
        revisions = list(reversed(list(script.walk_revisions())))  # base first
        heads = script.get_heads()
    except Exception as exc:  # noqa: BLE001 - a chain that won't load is the finding
        return [*problems, f"the chain does not load: {type(exc).__name__}: {exc}"]
    if len(heads) != 1:
        problems.append(f"the chain has {len(heads)} heads: {', '.join(sorted(heads))}")
    stems = {path.stem for path in (location / "versions").glob("*.py")}
    if stray := stems - {revision.revision for revision in revisions}:
        problems.append(f"versions/ holds files that are not revisions: {', '.join(sorted(stray))}")
    for position, revision in enumerate(revisions, start=1):
        rev = revision.revision
        if revision.path is None or Path(revision.path).stem != rev:
            problems.append(f"{rev}: the id is not its file name")
        if not REVISION_ID.fullmatch(rev):
            problems.append(f"{rev}: the id does not match ^\\d{{4}}_[a-z0-9_]+$")
        elif int(rev[:4]) != position:
            problems.append(f"{rev}: the id starts {rev[:4]}, but it is revision {position}")
        if len(rev) > MAX_REVISION_LENGTH:
            problems.append(f"{rev}: the id has {len(rev)} characters; at most 31")
        expected_down = revisions[position - 2].revision if position > 1 else None
        if revision.down_revision != expected_down:
            problems.append(f"{rev}: revises {revision.down_revision!r}, not {expected_down!r}")
        if revision.branch_labels:
            problems.append(f"{rev}: has branch labels")
        if revision.dependencies:
            problems.append(f"{rev}: has dependencies")
        try:
            revision.module.downgrade()
        except NotImplementedError:
            pass
        except Exception as exc:  # noqa: BLE001 - anything else is a downgrade that tried
            raised = type(exc).__name__
            problems.append(f"{rev}: downgrade() raises {raised}, not NotImplementedError")
        else:
            problems.append(f"{rev}: downgrade() does not raise NotImplementedError")
    return problems


def test_alembic_is_one_linear_forward_only_chain() -> None:
    assert chain_problems(SCRIPT_LOCATION) == []
    assert not (ENGINE_ROOT / "alembic.ini").exists()
    assert list(SCRIPT_LOCATION.rglob("*.mako")) == []


REVISION = '''"""A probe revision."""

revision = {revision!r}
down_revision = {down!r}
branch_labels = {labels!r}
depends_on = {depends!r}


def upgrade() -> None:
    pass


def downgrade() -> None:
    {downgrade}
'''


def probe_chain(tmp_path: Path, *revisions: dict[str, object]) -> Path:
    """A copy of the real script directory plus the probe ``revisions`` after its head."""
    location = tmp_path / "alembic"
    shutil.copytree(SCRIPT_LOCATION, location, ignore=shutil.ignore_patterns("__pycache__"))
    head = ScriptDirectory.from_config(alembic_config(SCRIPT_LOCATION)).get_current_head()
    for fields in revisions:
        values = {
            "down": head,
            "labels": None,
            "depends": None,
            "downgrade": "raise NotImplementedError",
            **fields,
        }
        stem = str(values.pop("file", values["revision"]))
        (location / "versions" / f"{stem}.py").write_text(
            REVISION.format(**values), encoding="utf-8"
        )
        head = str(values["revision"])
    return location


@pytest.mark.parametrize(
    ("revisions", "problem"),
    [
        ([{"revision": "0048_next"}], None),
        ([{"revision": "0048-next"}], "the chain does not load"),
        ([{"revision": "0048_Next"}], "the id does not match"),
        ([{"revision": "3_next"}], "the id does not match"),
        ([{"revision": "0049_next"}], "the id starts 0049, but it is revision 48"),
        ([{"revision": "0048_" + "x" * 27}], "at most 31"),
        ([{"revision": "0048_next", "file": "0048_other"}], "the id is not its file name"),
        ([{"revision": "0048_next", "labels": ("side",)}], "has branch labels"),
        ([{"revision": "0048_next", "depends": HEAD}], "has dependencies"),
        ([{"revision": "0048_next", "downgrade": "pass"}], "does not raise NotImplementedError"),
        ([{"revision": "0048_next", "downgrade": "raise RuntimeError"}], "raises RuntimeError"),
        (
            [{"revision": "0048_a"}, {"revision": "0049_b", "down": HEAD}],
            "the chain has 2 heads",
        ),
    ],
)
def test_the_chain_check_catches_a_bad_revision(
    revisions: list[dict[str, object]], problem: str | None, tmp_path: Path
) -> None:
    problems = chain_problems(probe_chain(tmp_path, *revisions))

    if problem is None:
        assert problems == []
    else:
        assert any(problem in found for found in problems), problems


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        ("    render_as_batch=True,\n", "", "does not configure render_as_batch=True"),
        ("render_as_batch=True", "render_as_batch=False", "does not configure render_as_batch"),
        ("target_metadata=Base.metadata", "target_metadata=None", "target_metadata=Base.metadata"),
        ("    connection=connection,\n", "", "the handed-over connection"),
    ],
)
def test_the_chain_check_catches_an_env_py_without_batch_mode(
    old: str, new: str, problem: str, tmp_path: Path
) -> None:
    location = probe_chain(tmp_path)
    env_py = location / "env.py"
    source = env_py.read_text(encoding="utf-8")
    assert old in source
    env_py.write_text(source.replace(old, new), encoding="utf-8")

    [found] = env_problems(env_py)
    assert problem in found


@pytest.mark.parametrize("unwanted", ["__init__.py", "script.py.mako", "versions/__init__.py"])
def test_the_chain_check_catches_cli_scaffolding(unwanted: str, tmp_path: Path) -> None:
    location = probe_chain(tmp_path)
    (location / unwanted).write_text("", encoding="utf-8")

    assert f"the script directory holds {unwanted}" in chain_problems(location)


# Drift and triggers ---------------------------------------------------------------------------


def drift(engine: Engine, metadata: MetaData) -> list[object]:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, metadata))


def test_the_models_match_the_database_at_head(db_engine: Engine) -> None:
    assert drift(db_engine, Base.metadata) == []


def test_the_drift_check_catches_a_column_no_migration_made(db_engine: Engine) -> None:
    metadata = metadata_copy()
    metadata.tables["events"].append_column(Column("dummy", String(10)))

    [difference] = drift(db_engine, metadata)

    assert difference[0] == "add_column"  # type: ignore[index]
    assert difference[3].name == "dummy"  # type: ignore[index]


def test_the_drift_check_catches_an_index_no_model_declares(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        connection.exec_driver_sql("CREATE INDEX ix_events_actor ON events (actor)")
        connection.commit()

    [difference] = drift(db_engine, Base.metadata)

    assert difference[0] == "remove_index"  # type: ignore[index]


def events_triggers(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {
            name
            for (name,) in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'events'"
            )
        }


def test_the_events_triggers_exist_at_head(db_engine: Engine) -> None:
    assert events_triggers(db_engine) == EVENTS_TRIGGERS


def company_facts_triggers(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {
            name
            for (name,) in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'company_facts'"
            )
        }


def test_the_company_facts_triggers_exist_at_head(db_engine: Engine) -> None:
    assert company_facts_triggers(db_engine) == COMPANY_FACTS_TRIGGERS


def table_triggers(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {
            name
            for (name,) in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
                (table,),
            )
        }


def test_the_campaigns_and_variants_triggers_exist_at_head(db_engine: Engine) -> None:
    assert table_triggers(db_engine, "campaigns") == CAMPAIGNS_TRIGGERS
    assert table_triggers(db_engine, "variants") == VARIANTS_TRIGGERS


def test_the_evidence_triggers_exist_at_head(db_engine: Engine) -> None:
    assert table_triggers(db_engine, "evidence") == EVIDENCE_TRIGGERS


def test_the_trigger_check_catches_a_batch_rebuild_of_events(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("events", recreate="always"):
            pass
        connection.commit()

    assert EVENTS_TRIGGERS - events_triggers(db_engine) == EVENTS_TRIGGERS
    assert drift(db_engine, Base.metadata) == []  # only the trigger test notices
