"""Suppression List contracts (FR-50, FR-63, AD-3, AD-16, AD-17, Story 3.3), with probes proving
each check catches a violation.

- **The table at head.** ``suppression_entries`` has exactly the spec's columns (types and
  nullability), its six checks, the partial unique index ``uq_suppression_entries_active_key``
  and its four triggers; ``owners.py`` gives every column to ``compliance`` and
  ``pii_registry.py`` classifies ``value_hash`` ``keep_hash``, ``void_reason`` ``redact`` and the
  rest ``none``.
- **One writer.** An AST scan of every module under ``nextron/`` finds each of the repository's
  writers used (called or referenced, through any alias of
  ``nextron.db.repos.compliance.suppression_entries``: ``import ... as x``, a plain dotted
  import, the package or the module imported from its parent, a relative import, or ``from
  ...suppression_entries import insert as y``) only in its one home: ``insert`` in
  ``nextron/app/compliance/api.py``, so the list grows only through ``Compliance.suppress``, and
  ``redact_void_reason`` (Story 3.13) in ``nextron/app/compliance/erasure.py``, so a void reason
  is redacted only by compliance's erasure hook. Every public function of the repository is
  classified as a reader (``get``, ``active_for``, ``voided_for``) or a writer: a new one fails
  until it is classified here.
- **The model stays in the database layer.** ``SuppressionEntryRecord`` is named (as a name, an
  attribute, an import or in a string) only under ``nextron/db/models`` and
  ``nextron/db/repos/compliance``.
- **No SQL writes elsewhere.** No string outside ``nextron/db/alembic/versions`` holds an
  ``INSERT``, ``UPDATE``, ``DELETE`` or ``REPLACE`` statement naming ``suppression_entries``.
- **The table's name stays with its owner.** No string (f-string parts included; docstrings
  skipped) in a module under ``nextron/`` holds ``suppression_entries`` unless the module is
  ``db/models/compliance.py``, a migration, under ``db/repos/compliance`` or ``app/compliance``,
  ``app/queries/compliance.py``, ``domain/backup.py`` (``COUNTED_TABLES``), ``db/owners.py`` or
  ``db/pii_registry.py``. So ``sqlalchemy.table("suppression_entries", ...)``,
  ``Base.metadata.tables["suppression_entries"]``, an f-string ``INSERT INTO {TABLE}`` and a
  ``DROP TABLE`` all fail elsewhere.
- **Permanent.** The compliance repository, API and queries and the ``contactability`` and
  ``suppression_entry`` domain modules hold no function, method, call or import whose name has
  a word starting ``delet``, ``remov``, ``purg``, ``expir`` (expire, expiry), ``ttl``, ``prun``,
  ``evict``, ``truncat`` or ``drop``; no SQL ``DELETE``; no ``timedelta`` or ``relativedelta``;
  no comparison with ``created_at`` or ``voided_at`` (expiry by filtering); and no string, other
  than a docstring, holding an SQL date modifier (``julianday(``, ``strftime(``, ``datetime(``,
  ``date(``, ``'now'``, ``-30 days`` and the like): entries never expire and are never deleted.
  ``ALLOWED`` names the one exception, which deletes no entry: Story 3.14's Telegram store
  (``app/compliance/erasure_stores.py``) calls ``notify``'s ``delete_message`` to delete the
  bot's messages about an erased Contact (AD-17). The same call anywhere else still fails.
"""

import ast
import re
from collections.abc import Iterable
from pathlib import Path

import pytest
from sqlalchemy import Engine

from nextron.db.owners import TABLES
from nextron.db.pii_registry import PII, PiiClass
from tests.contracts.support import PACKAGE

TABLE = "suppression_entries"
REPO_PARENT = "nextron.db.repos"
REPO_PACKAGE = f"{REPO_PARENT}.compliance"
REPO_NAME = "suppression_entries"
REPO_MODULE = f"{REPO_PACKAGE}.{REPO_NAME}"
REPO_FILE = PACKAGE / "db" / "repos" / "compliance" / "suppression_entries.py"
READERS = frozenset({"get", "active_for", "voided_for"})
ALLOWED_WRITER = "app/compliance/api.py"
ERASURE_MODULE = "app/compliance/erasure.py"
WRITER_HOMES = {  # each writer, with the one module that may use it
    "insert": ALLOWED_WRITER,
    "redact_void_reason": ERASURE_MODULE,  # Story 3.13
}
WRITERS = frozenset(WRITER_HOMES)
RECORD = "SuppressionEntryRecord"
RECORD_HOMES = ("db/models/", "db/repos/compliance/")
MIGRATIONS = "db/alembic/versions/"
TABLE_NAME_HOMES = (
    "db/models/compliance.py",
    "db/alembic/versions/",
    "db/repos/compliance/",
    "app/compliance/",
    "app/queries/compliance.py",
    "domain/backup.py",
    "db/owners.py",
    "db/pii_registry.py",
)
GUARDED = (
    "db/repos/compliance/",
    "app/compliance/",
    "app/queries/compliance.py",
    "domain/contactability.py",
    "domain/suppression_entry.py",
)
DATE_COLUMNS = frozenset({"created_at", "voided_at"})
# (module, verb, name) uses that delete no entry: AD-17's Telegram deletion (Story 3.14)
ALLOWED = frozenset({("app/compliance/erasure_stores.py", "calls", "delete_message")})
DATE_ARITHMETIC = frozenset({"timedelta", "relativedelta"})
FORBIDDEN_STEMS = (
    "delet",
    "remov",
    "purg",
    "expir",
    "ttl",
    "prun",
    "evict",
    "truncat",
    "drop",
)
SQL_WRITE = re.compile(
    r"\b(?:insert\s+(?:or\s+\w+\s+)?into|replace\s+into|update(?:\s+or\s+\w+)?|delete\s+from)"
    r"\s+(?:\w+\s*\.\s*)?[\"'`\[]?suppression_entries\b",
    re.IGNORECASE,
)
SQL_DELETE = re.compile(r"\bdelete\s+from\b", re.IGNORECASE)
SQL_DATE = re.compile(
    r"\b(?:julianday|strftime|datetime|date)\s*\(|'now'"
    r"|[+-]\s*\d+\s+(?:days?|months?|years?|hours?|minutes?)\b",
    re.IGNORECASE,
)

COLUMNS = [  # (name, declared type, NOT NULL)
    ("id", "VARCHAR(26)", 1),
    ("kind", "VARCHAR(16)", 1),
    ("value_hash", "VARCHAR(64)", 1),
    ("domain_plain", "VARCHAR(253)", 0),
    ("status", "VARCHAR(16)", 1),
    ("source", "VARCHAR(16)", 1),
    ("cause_ref", "VARCHAR(26)", 0),
    ("created_at", "VARCHAR(27)", 1),
    ("voided_at", "VARCHAR(27)", 0),
    ("void_reason", "TEXT", 0),
]
CHECKS = {
    "ck_suppression_entries_kind",
    "ck_suppression_entries_value_hash",
    "ck_suppression_entries_domain_plain",
    "ck_suppression_entries_status",
    "ck_suppression_entries_source",
    "ck_suppression_entries_voided",
}
ACTIVE_KEY_INDEX = (
    "CREATE UNIQUE INDEX uq_suppression_entries_active_key ON suppression_entries "
    "(kind, value_hash) WHERE status = 'active'"
)
TRIGGERS = {
    "trg_suppression_entries_no_delete",
    "trg_suppression_entries_no_id_reuse",
    "trg_suppression_entries_insert_active",
    "trg_suppression_entries_immutable",
}


# The table at head ------------------------------------------------------------------------------


def _master(engine: Engine, kind: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type = ? AND tbl_name = ?", (kind, TABLE)
        ).all()
    return {str(name): " ".join(str(sql).split()) for name, sql in rows if sql is not None}


def test_the_table_has_the_spec_columns(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        found = connection.exec_driver_sql(f"PRAGMA table_info({TABLE})").all()

    assert [(name, declared, notnull) for _, name, declared, notnull, *_ in found] == COLUMNS


def test_the_table_has_its_checks_index_and_four_triggers(db_engine: Engine) -> None:
    [table_sql] = _master(db_engine, "table").values()

    assert set(re.findall(r"CONSTRAINT (ck_\w+) CHECK", table_sql)) == CHECKS
    assert _master(db_engine, "index") == {"uq_suppression_entries_active_key": ACTIVE_KEY_INDEX}
    assert set(_master(db_engine, "trigger")) == TRIGGERS


def test_owners_and_the_pii_registry_cover_every_column() -> None:
    names = [name for name, *_ in COLUMNS]

    assert TABLES[TABLE] == {"compliance": tuple(names)}
    assert PII[TABLE] == {
        name: PiiClass.KEEP_HASH
        if name == "value_hash"
        else PiiClass.REDACT
        if name == "void_reason"
        else PiiClass.NONE
        for name in names
    }


# Shared AST helpers -----------------------------------------------------------------------------


def _modules(root: Path) -> Iterable[tuple[str, Path, ast.Module]]:
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        yield relative, path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_of(path: Path, root: Path) -> str:
    """The dotted package a module at ``path`` under the ``nextron`` folder ``root`` is in."""
    parts = path.relative_to(root.parent).with_suffix("").parts
    return ".".join(parts[:-1])


def _imported_from(node: ast.ImportFrom, package: str) -> str:
    if not node.level:
        return node.module or ""
    base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return None if inner is None else f"{inner}.{node.attr}"
    return None


def _names(tree: ast.AST) -> Iterable[tuple[int, str]]:
    """Every name, attribute, imported name, imported module and string in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.Attribute):
            yield node.lineno, node.attr
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
                if alias.asname:
                    yield node.lineno, alias.asname
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, node.module or ""
            for alias in node.names:
                yield node.lineno, alias.name
                if alias.asname:
                    yield node.lineno, alias.asname
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of the module's, its classes' and its functions' docstring constants."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def _strings(tree: ast.Module) -> Iterable[ast.Constant]:
    """Every string constant in ``tree`` but a docstring, f-string parts included."""
    docstrings = _docstrings(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node


def probe_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "nextron"
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


# One writer -------------------------------------------------------------------------------------


class _WriterUses(ast.NodeVisitor):
    """Every use of the repository's writer in one module, with the function around it."""

    def __init__(self, modules: set[str], functions: dict[str, str]) -> None:
        self.modules = modules  # names (or dotted names) bound to the repository module
        self.functions = functions  # names bound to a writer itself, with the writer's name
        self.stack: list[str] = []
        self.found: list[tuple[int, str, str, str]] = []  # (line, function, name, writer)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scope
    visit_AsyncFunctionDef = _scope
    visit_ClassDef = _scope

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in WRITERS and _dotted(node.value) in self.modules:
            scope = ".".join(self.stack) or "<module>"
            self.found.append((node.lineno, scope, node.attr, node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.functions and isinstance(node.ctx, ast.Load):
            scope = ".".join(self.stack) or "<module>"
            self.found.append((node.lineno, scope, node.id, self.functions[node.id]))


def _bindings(tree: ast.Module, package: str) -> tuple[set[str], dict[str, str]]:
    """The names bound to the repository module and to its writers in one module."""
    modules: set[str] = set()
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == REPO_MODULE:
                    modules.add(alias.asname or alias.name)
                elif alias.name == REPO_PACKAGE:
                    modules.add(f"{alias.asname or alias.name}.{REPO_NAME}")
        elif isinstance(node, ast.ImportFrom):
            source = _imported_from(node, package)
            for alias in node.names:
                bound = alias.asname or alias.name
                if source == REPO_PACKAGE and alias.name == REPO_NAME:
                    modules.add(bound)
                elif source == REPO_PARENT and alias.name == "compliance":
                    modules.add(f"{bound}.{REPO_NAME}")
                elif source == REPO_MODULE and alias.name in WRITERS:
                    functions[bound] = alias.name
    return modules, functions


def suppression_repo_writes(root: Path) -> list[tuple[str, int, str, str, str]]:
    """Every use of one of the repository's writers in a module under the ``nextron`` folder
    ``root``, as ``(path, line, function, name used, writer)``."""
    found = []
    for relative, path, tree in _modules(root):
        modules, functions = _bindings(tree, _package_of(path, root))
        if not (modules or functions):
            continue
        uses = _WriterUses(modules, functions)
        uses.visit(tree)
        found += [
            (relative, line, function, name, writer)
            for line, function, name, writer in uses.found
        ]
    return found


def suppression_writer_problems(root: Path) -> list[str]:
    """Each use of one of the repository's writers outside its home (``WRITER_HOMES``)."""
    return [
        f"{path}:{line} {function} uses the suppression_entries repository's {name}"
        for path, line, function, name, writer in suppression_repo_writes(root)
        if path != WRITER_HOMES[writer]
    ]


def test_only_the_compliance_api_writes_the_suppression_list() -> None:
    writes = suppression_repo_writes(PACKAGE)

    assert suppression_writer_problems(PACKAGE) == []
    assert {writer for *_, writer in writes} == {"insert", "redact_void_reason"}  # not vacuous
    assert {(path, writer) for path, *_, writer in writes} == {
        (ALLOWED_WRITER, "insert"),
        (ERASURE_MODULE, "redact_void_reason"),
    }


WRITER_PROBES = [
    (
        "app/pipeline/api.py",
        "from nextron.db.repos.compliance import suppression_entries as repo\n\n\n"
        "def sneaky(at):\n    repo.insert(kind='address', value_hash='a', domain_plain=None, "
        "source='operator', cause_ref=None, created_at=at)\n",
        "app/pipeline/api.py:5 sneaky uses the suppression_entries repository's insert",
    ),
    (
        "app/ops/api.py",
        "from nextron.db.repos.compliance.suppression_entries import insert as add\n\n\n"
        "def seed(at):\n    add(kind='domain')\n",
        "app/ops/api.py:5 seed uses the suppression_entries repository's add",
    ),
    (
        "app/other/probe.py",
        "import nextron.db.repos.compliance.suppression_entries\n\n"
        "nextron.db.repos.compliance.suppression_entries.insert()\n",
        "app/other/probe.py:3 <module> uses the suppression_entries repository's insert",
    ),
    (
        "web/probe.py",
        "import nextron.db.repos.compliance.suppression_entries as s\n\n\n"
        "class Page:\n    def post(self):\n        write = s.insert\n        write()\n",
        "web/probe.py:6 Page.post uses the suppression_entries repository's insert",
    ),
    (
        "jobs/probe.py",
        "from nextron.db.repos import compliance\n\n\n"
        "def handler():\n    compliance.suppression_entries.insert()\n",
        "jobs/probe.py:5 handler uses the suppression_entries repository's insert",
    ),
    (
        "jobs/other.py",
        "import nextron.db.repos.compliance as c\n\n\n"
        "def handler():\n    c.suppression_entries.insert()\n",
        "jobs/other.py:5 handler uses the suppression_entries repository's insert",
    ),
    (
        "db/repos/compliance/helper.py",
        "from . import suppression_entries\n\n\ndef copy():\n"
        "    suppression_entries.insert()\n",
        "db/repos/compliance/helper.py:5 copy uses the suppression_entries repository's insert",
    ),
    (  # Story 3.13: only the erasure hook redacts a void reason
        "app/compliance/api.py",
        "from nextron.db.repos.compliance import suppression_entries as entries_repo\n\n\n"
        "def tidy(entry_id):\n    entries_repo.redact_void_reason(entry_id)\n",
        "app/compliance/api.py:5 tidy uses the suppression_entries repository's "
        "redact_void_reason",
    ),
    (  # and the erasure module never writes an entry
        "app/compliance/erasure.py",
        "from nextron.db.repos.compliance.suppression_entries import insert as add\n\n\n"
        "def sneaky():\n    add(kind='person')\n",
        "app/compliance/erasure.py:5 sneaky uses the suppression_entries repository's add",
    ),
]


@pytest.mark.parametrize(("relative", "source", "found"), WRITER_PROBES)
def test_the_writer_check_names_a_write_outside_the_compliance_api(
    relative: str, source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, {relative: source})

    assert suppression_writer_problems(root) == [found]


def test_the_writer_check_allows_reads_and_the_one_writer(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "app/compliance/api.py": (
                "from nextron.db.repos.compliance import suppression_entries as entries_repo\n"
                "from sqlalchemy import insert\n\n\n"
                "def suppress(at):\n    entries_repo.active_for('address', 'a')\n"
                "    entries_repo.insert(kind='address')\n    insert('x')\n"
            ),
            "app/queries/compliance.py": (
                "from nextron.db.repos.compliance import suppression_entries as repo\n\n\n"
                "def entry(entry_id):\n    return repo.get(entry_id)\n"
            ),
        },
    )

    assert suppression_writer_problems(root) == []
    assert len(suppression_repo_writes(root)) == 1


def test_the_writer_check_allows_the_erasure_hooks_redaction(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "app/compliance/erasure.py": (
                "from nextron.db.repos.compliance import suppression_entries as entries_repo\n\n\n"
                "def erase_pii(entry_id):\n    entries_repo.voided_for('address', 'a')\n"
                "    entries_repo.redact_void_reason(entry_id)\n"
            ),
        },
    )

    assert suppression_writer_problems(root) == []
    assert [writer for *_, writer in suppression_repo_writes(root)] == ["redact_void_reason"]


def repository_classification_problems(path: Path) -> list[str]:
    """Each public function of the repository module at ``path`` that is neither a reader nor
    the writer, and each classified one it lacks."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    public = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    classified = READERS | WRITERS
    problems = [
        f"{name} is neither a reader nor the writer"
        for name in sorted(public)
        if name not in classified
    ]
    problems += [f"{name} is classified but missing" for name in sorted(classified - public)]
    return problems


def test_every_public_repository_function_is_a_reader_or_the_writer() -> None:
    assert repository_classification_problems(REPO_FILE) == []


@pytest.mark.parametrize(
    ("extra", "found"),
    [
        (
            "\n\ndef void(entry_id, at, reason):\n    pass\n",
            "void is neither a reader nor the writer",
        ),
        ("\n\ndef remove(entry_id):\n    pass\n", "remove is neither a reader nor the writer"),
        ("\n\nasync def touch(entry_id):\n    pass\n", "touch is neither a reader nor the writer"),
    ],
)
def test_the_classification_check_catches_an_unclassified_function(
    extra: str, found: str, tmp_path: Path
) -> None:
    path = tmp_path / "suppression_entries.py"
    path.write_text(REPO_FILE.read_text(encoding="utf-8") + extra, encoding="utf-8")

    assert repository_classification_problems(path) == [found]


def test_the_classification_check_catches_a_missing_function(tmp_path: Path) -> None:
    path = tmp_path / "suppression_entries.py"
    path.write_text(
        "def get(entry_id):\n    pass\n\n\ndef _helper():\n    pass\n", encoding="utf-8"
    )

    assert repository_classification_problems(path) == [
        "active_for is classified but missing",
        "insert is classified but missing",
        "redact_void_reason is classified but missing",
        "voided_for is classified but missing",
    ]


# The model stays in the database layer ----------------------------------------------------------


def record_name_problems(root: Path) -> list[str]:
    """Each line outside ``db/models`` and ``db/repos/compliance`` naming the model."""
    problems = set()
    for relative, _, tree in _modules(root):
        if relative.startswith(RECORD_HOMES):
            continue
        for line, name in _names(tree):
            if RECORD in name:
                problems.add(f"{relative}:{line} names {RECORD}")
    return sorted(problems)


def test_only_the_database_layer_names_the_model() -> None:
    assert record_name_problems(PACKAGE) == []
    homes = [
        relative
        for relative, _, tree in _modules(PACKAGE)
        if any(RECORD in name for _, name in _names(tree))
    ]
    assert "db/repos/compliance/suppression_entries.py" in homes  # not vacuous


@pytest.mark.parametrize(
    ("source", "found"),
    [
        (
            "from nextron.db.models.compliance import SuppressionEntryRecord\n",
            "app/compliance/api.py:1 names SuppressionEntryRecord",
        ),
        (
            "from nextron.db import models\n\nrecord = models.SuppressionEntryRecord\n",
            "app/compliance/api.py:3 names SuppressionEntryRecord",
        ),
        (
            "import importlib\n\n"
            "importlib.import_module('nextron.db.models').SuppressionEntryRecord\n",
            "app/compliance/api.py:3 names SuppressionEntryRecord",
        ),
        (
            "name = 'SuppressionEntryRecord'\n",
            "app/compliance/api.py:1 names SuppressionEntryRecord",
        ),
    ],
)
def test_the_model_check_catches_a_use_outside_the_database_layer(
    source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, {"app/compliance/api.py": source})

    assert record_name_problems(root) == [found]


def test_the_model_check_allows_the_models_and_the_compliance_repositories(
    tmp_path: Path,
) -> None:
    root = probe_tree(
        tmp_path,
        {
            "db/models/__init__.py": "from .compliance import SuppressionEntryRecord\n",
            "db/repos/compliance/x.py": "from nextron.db.models import SuppressionEntryRecord\n",
        },
    )

    assert record_name_problems(root) == []


# No SQL writes elsewhere ------------------------------------------------------------------------


def sql_write_problems(root: Path) -> list[str]:
    """Each string outside the migrations holding an SQL write naming the table."""
    problems = []
    for relative, _, tree in _modules(root):
        if relative.startswith(MIGRATIONS):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SQL_WRITE.search(node.value)
            ):
                problems.append(f"{relative}:{node.lineno} writes {TABLE} in SQL")
    return problems


def test_no_string_outside_the_migrations_writes_the_table_in_sql() -> None:
    assert sql_write_problems(PACKAGE) == []
    migration = PACKAGE / "db" / "alembic" / "versions" / "0022_3_3_suppression_entries.py"
    assert SQL_WRITE.search(migration.read_text(encoding="utf-8")) is None  # triggers only
    assert SQL_WRITE.search("DELETE FROM suppression_entries")  # the pattern works


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM suppression_entries",
        "delete from suppression_entries where id = ?",
        "DELETE FROM main.suppression_entries",
        'DELETE FROM "suppression_entries"',
        "UPDATE suppression_entries SET status = 'active'",
        "UPDATE OR REPLACE suppression_entries SET kind = 'domain'",
        "INSERT INTO suppression_entries VALUES (1)",
        "INSERT OR REPLACE INTO suppression_entries SELECT * FROM x",
        "INSERT OR IGNORE INTO [suppression_entries] DEFAULT VALUES",
        "REPLACE INTO suppression_entries VALUES (1)",
        "insert  into\n  suppression_entries (id) values (?)",
    ],
)
def test_the_sql_check_catches_a_write_in_any_module(statement: str, tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path, {"app/ops/api.py": f"x = 1\nSTATEMENT = {statement!r}\n"}
    )

    assert sql_write_problems(root) == [f"app/ops/api.py:2 writes {TABLE} in SQL"]


def test_the_sql_check_allows_reads_other_tables_and_the_migrations(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "db/repos/ops/snapshot.py": (
                "A = 'SELECT count(*) FROM suppression_entries'\n"
                "B = 'DELETE FROM suppression_entries_archive'\n"
                "C = 'UPDATE other SET suppression_entries = 1'\n"
                '"""The triggers refuse any DELETE on suppression_entries."""\n'
            ),
            "db/alembic/versions/0099_x.py": "S = 'DELETE FROM suppression_entries'\n",
        },
    )

    assert sql_write_problems(root) == []


# The table's name stays with its owner ----------------------------------------------------------


def table_name_problems(root: Path) -> list[str]:
    """Each string naming the table outside the modules allowed to."""
    problems = []
    for relative, _, tree in _modules(root):
        if relative.startswith(TABLE_NAME_HOMES):
            continue
        problems += [
            f"{relative}:{node.lineno} names {TABLE}"
            for node in _strings(tree)
            if TABLE in node.value
        ]
    return sorted(set(problems))


def test_only_the_owner_and_the_registries_name_the_table() -> None:
    assert table_name_problems(PACKAGE) == []
    naming = {
        relative
        for relative, _, tree in _modules(PACKAGE)
        if any(TABLE in node.value for node in _strings(tree))
    }
    assert {"domain/backup.py", "app/compliance/api.py"} <= naming  # seen, and allowed
    assert all(relative.startswith(TABLE_NAME_HOMES) for relative in naming)


@pytest.mark.parametrize(
    ("source", "found"),
    [
        (
            "TABLE = 'suppression_entries'\nSQL = f'INSERT INTO {TABLE} VALUES (1)'\n",
            "app/ops/api.py:1 names suppression_entries",
        ),
        (
            "x = 1\nSQL = f'DELETE FROM suppression_entries WHERE id = {x}'\n",
            "app/ops/api.py:2 names suppression_entries",
        ),
        (
            "import sqlalchemy as sa\n\n"
            "T = sa.table('suppression_entries', sa.column('id'))\n",
            "app/ops/api.py:3 names suppression_entries",
        ),
        (
            "from nextron.db.models import Base\n\n"
            "T = Base.metadata.tables['suppression_entries']\n",
            "app/ops/api.py:3 names suppression_entries",
        ),
        (
            "S = 'DROP TABLE suppression_entries'\n",
            "app/ops/api.py:1 names suppression_entries",
        ),
        (
            "def f():\n    '''Docs.'''\n    return 'suppression_entries'\n",
            "app/ops/api.py:3 names suppression_entries",
        ),
    ],
)
def test_the_table_name_check_catches_the_name_outside_its_homes(
    source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, {"app/ops/api.py": source})

    assert table_name_problems(root) == [found]


def test_the_table_name_check_allows_the_homes_and_docstrings(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "db/owners.py": "TABLES = {'suppression_entries': {}}\n",
            "domain/backup.py": "COUNTED_TABLES = ('suppression_entries', 'provenance_records')\n",
            "app/compliance/api.py": "ENTITY = 'suppression_entries'\n",
            "db/alembic/versions/0099_x.py": "op.drop_table('suppression_entries')\n",
            "domain/suppression_entry.py": (
                '"""Owns ``suppression_entries.status``."""\n\n\n'
                "class Machine:\n    '''One row per suppression_entries change.'''\n\n"
                "    def run(self):\n        '''Writes suppression_entries.'''\n"
            ),
        },
    )

    assert table_name_problems(root) == []


# Permanent --------------------------------------------------------------------------------------


def _forbidden(name: str) -> bool:
    return any(
        word.startswith(stem) for word in name.lower().split("_") for stem in FORBIDDEN_STEMS
    )


def _operand_name(node: ast.expr) -> str | None:
    """A compared operand's name: a ``Name``'s id or an ``Attribute``'s last part."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def permanence_problems(root: Path) -> list[str]:
    """Each delete, removal or expiry in the compliance repository, API and queries and the
    suppression domain modules."""
    problems = []
    for relative, _, tree in _modules(root):
        if not relative.startswith(GUARDED):
            continue
        problems += [
            f"{relative}:{node.lineno} holds an SQL date modifier"
            for node in _strings(tree)
            if SQL_DATE.search(node.value)
        ]
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            names: list[tuple[str, str]] = []
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(("defines", node.name))
            elif isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                names.append(("calls", called))
            elif isinstance(node, ast.Import | ast.ImportFrom):
                for alias in node.names:
                    names += [("imports", part) for part in alias.name.split(".")]
                    if alias.asname:
                        names.append(("imports", alias.asname))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if SQL_DELETE.search(node.value):
                    problems.append(f"{relative}:{line} holds SQL DELETE")
            elif isinstance(node, ast.Compare):
                for operand in (node.left, *node.comparators):
                    compared = _operand_name(operand)
                    if compared in DATE_COLUMNS:
                        problems.append(f"{relative}:{line} compares {compared}")
            if isinstance(node, ast.Name | ast.Attribute):
                named = node.id if isinstance(node, ast.Name) else node.attr
                if named in DATE_ARITHMETIC:
                    problems.append(f"{relative}:{line} uses {named}")
            for verb, name in names:
                if (relative, verb, name) in ALLOWED:
                    continue
                if name in DATE_ARITHMETIC:
                    problems.append(f"{relative}:{line} uses {name}")
                elif _forbidden(name):
                    problems.append(f"{relative}:{line} {verb} {name}")
    return sorted(set(problems))


def test_the_compliance_code_never_deletes_or_expires_an_entry() -> None:
    assert permanence_problems(PACKAGE) == []
    guarded = [
        relative for relative, _, _ in _modules(PACKAGE) if relative.startswith(GUARDED)
    ]
    assert {
        "db/repos/compliance/suppression_entries.py",
        "app/compliance/api.py",
        "app/queries/compliance.py",
        "domain/contactability.py",
        "domain/suppression_entry.py",
    } <= set(guarded)  # not vacuous


@pytest.mark.parametrize(
    ("relative", "source", "found"),
    [
        (
            "db/repos/compliance/suppression_entries.py",
            "def delete(entry_id):\n    pass\n",
            "db/repos/compliance/suppression_entries.py:1 defines delete",
        ),
        (
            "db/repos/compliance/suppression_entries.py",
            "def remove_entry(entry_id):\n    pass\n",
            "db/repos/compliance/suppression_entries.py:1 defines remove_entry",
        ),
        (
            "db/repos/compliance/other.py",
            "from sqlalchemy import delete\n",
            "db/repos/compliance/other.py:1 imports delete",
        ),
        (
            "db/repos/compliance/other.py",
            "import sqlalchemy as sa\n\nsa.delete(x)\n",
            "db/repos/compliance/other.py:3 calls delete",
        ),
        (
            "db/repos/compliance/other.py",
            "session.execute('DELETE FROM suppression_entries')\n",
            "db/repos/compliance/other.py:1 holds SQL DELETE",
        ),
        (
            "app/compliance/api.py",
            "class Compliance:\n    def purge_old(self):\n        pass\n",
            "app/compliance/api.py:2 defines purge_old",
        ),
        (
            "app/compliance/api.py",
            "def run():\n    entries.expire_all()\n",
            "app/compliance/api.py:2 calls expire_all",
        ),
        (
            "app/compliance/api.py",
            "EXPIRY_DAYS = 3\n\n\ndef expiry_of(entry):\n    pass\n",
            "app/compliance/api.py:4 defines expiry_of",
        ),
        (
            "app/compliance/api.py",
            "def entry_ttl():\n    pass\n",
            "app/compliance/api.py:1 defines entry_ttl",
        ),
        (
            "app/compliance/api.py",
            "def run():\n    prune(1)\n",
            "app/compliance/api.py:2 calls prune",
        ),
        (
            "app/compliance/api.py",
            "def evict_stale():\n    pass\n",
            "app/compliance/api.py:1 defines evict_stale",
        ),
        (
            "app/compliance/api.py",
            "def run():\n    table.truncate()\n",
            "app/compliance/api.py:2 calls truncate",
        ),
        (
            "app/queries/compliance.py",
            "def drop_voided():\n    pass\n",
            "app/queries/compliance.py:1 defines drop_voided",
        ),
        (
            "app/queries/compliance.py",
            "from datetime import timedelta\n",
            "app/queries/compliance.py:1 uses timedelta",
        ),
        (
            "app/compliance/api.py",
            "import datetime\n\nKEEP = datetime.timedelta(days=30)\n",
            "app/compliance/api.py:3 uses timedelta",
        ),
        (
            "domain/contactability.py",
            "def purge_key(key):\n    pass\n",
            "domain/contactability.py:1 defines purge_key",
        ),
        # expiry by filtering
        (
            "app/compliance/api.py",
            "def stale(entry, cutoff):\n    return entry.created_at < cutoff\n",
            "app/compliance/api.py:2 compares created_at",
        ),
        (
            "domain/suppression_entry.py",
            "def old(voided_at, now):\n    return now > voided_at\n",
            "domain/suppression_entry.py:2 compares voided_at",
        ),
        (
            "db/repos/compliance/suppression_entries.py",
            "statement = select(Record).where(Record.created_at >= since)\n",
            "db/repos/compliance/suppression_entries.py:1 compares created_at",
        ),
        (
            "domain/contactability.py",
            "from dateutil.relativedelta import relativedelta\n",
            "domain/contactability.py:1 uses relativedelta",
        ),
        (
            "app/queries/compliance.py",
            "import dateutil\n\nKEEP = dateutil.relativedelta.relativedelta(months=1)\n",
            "app/queries/compliance.py:3 uses relativedelta",
        ),
        (
            "db/repos/compliance/suppression_entries.py",
            "Q = 'SELECT id FROM t WHERE julianday(created_at) > 1'\n",
            "db/repos/compliance/suppression_entries.py:1 holds an SQL date modifier",
        ),
        (
            "db/repos/compliance/suppression_entries.py",
            "Q = \"SELECT strftime('%s', created_at) FROM t\"\n",
            "db/repos/compliance/suppression_entries.py:1 holds an SQL date modifier",
        ),
        (
            "db/repos/compliance/suppression_entries.py",
            "x = 1\nQ = 'WHERE created_at < datetime(?)'\n",
            "db/repos/compliance/suppression_entries.py:2 holds an SQL date modifier",
        ),
        (
            "db/repos/compliance/suppression_entries.py",
            "Q = 'WHERE created_at < date(?)'\n",
            "db/repos/compliance/suppression_entries.py:1 holds an SQL date modifier",
        ),
        (
            "app/queries/compliance.py",
            "Q = \"WHERE created_at < 'now'\"\n",
            "app/queries/compliance.py:1 holds an SQL date modifier",
        ),
        (
            "app/compliance/api.py",
            "MODIFIER = '-30 days'\n",
            "app/compliance/api.py:1 holds an SQL date modifier",
        ),
        (
            "app/compliance/api.py",
            "x = 1\nMODIFIER = f'+{x} months' if x else '+1 month'\n",
            "app/compliance/api.py:2 holds an SQL date modifier",
        ),
    ],
)
def test_the_permanence_check_catches_a_delete_or_an_expiry(
    relative: str, source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, {relative: source})

    assert found in permanence_problems(root)


def test_the_permanence_check_allows_only_the_named_telegram_deletion(tmp_path: Path) -> None:
    call = "def run(notify):\n    notify.delete_message('1', '2')\n"
    root = probe_tree(
        tmp_path,
        {
            "app/compliance/erasure_stores.py": call,
            "app/compliance/api.py": call,
            "app/compliance/erasure.py": "def delete_message():\n    pass\n",
        },
    )

    assert permanence_problems(root) == [
        "app/compliance/api.py:2 calls delete_message",
        "app/compliance/erasure.py:1 defines delete_message",
    ]


def test_the_permanence_check_looks_only_at_compliance(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "app/jobqueue/api.py": "from datetime import timedelta\n\ndef purge():\n    pass\n",
            "db/repos/ops/backups.py": "def delete_copy(path):\n    pass\n",
            "app/compliance/api.py": (
                '"""Entries are never deleted; nothing removes or expires one."""\n\n\n'
                "def suppress(subject):\n    entries_repo.insert(kind='address')\n"
            ),
        },
    )

    assert permanence_problems(root) == []


def test_the_permanence_check_allows_docstrings_and_other_comparisons(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        {
            "app/compliance/api.py": (
                '"""Never filtered by datetime(\'now\', \'-30 days\') or julianday(x)."""\n\n\n'
                "def suppress(found, key):\n"
                "    '''Compares no date: strftime('%s') is only in this docstring.'''\n"
                "    return found.status == 'active' and key.kind != 'domain'\n"
            ),
            "domain/contactability.py": "UPDATE = 'update(kind)'\n",
        },
    )

    assert permanence_problems(root) == []
