"""Approved Company Fact contracts (FR-72, AD-3, AD-7, Story 4.3), with probes proving each check
catches a violation.

- **One writer of the facts.** An AST scan of every module under ``nextron/`` finds the
  ``company_facts`` repository's ``insert`` and ``retract`` used (called or referenced, through
  any alias of ``nextron.db.repos.playbook.company_facts``: ``import ... as c``, a plain dotted
  import, ``from nextron.db.repos.playbook import company_facts as x``, a relative import, or
  ``from ...company_facts import insert as y``) only in ``nextron/app/playbook/api.py``, so the
  facts change only through ``CompanyFacts`` (the seed's ``seed_facts`` and the Operator's
  ``add``, ``edit`` and ``retract``).
- **The web layer can't reach the writers.** No module under ``nextron/web`` names
  ``seed_facts`` (a name, an attribute, an imported name or a string) or the
  ``nextron.db.repos.playbook`` package or any module in it: the facts page goes through the
  ``CompanyFacts`` use cases and ``queries.playbook`` only.
"""

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

from tests.contracts.support import PACKAGE

REPO_PACKAGE = "nextron.db.repos.playbook"
REPO_NAME = "company_facts"
REPO_MODULE = f"{REPO_PACKAGE}.{REPO_NAME}"
REPO_WRITERS = frozenset({"insert", "retract"})
ALLOWED_WRITER = "app/playbook/api.py"
WEB = PACKAGE / "web"
FORBIDDEN_WEB_NAMES = frozenset({"seed_facts"})


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


class _WriterUses(ast.NodeVisitor):
    """Every use of the repository's writers in one module, with the function around it."""

    def __init__(self, modules: set[str], functions: set[str]) -> None:
        self.modules = modules  # names (or dotted names) bound to the repository module
        self.functions = functions  # names bound to insert or retract themselves
        self.stack: list[str] = []
        self.found: list[tuple[int, str, str]] = []  # (line, function, writer)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scope
    visit_AsyncFunctionDef = _scope
    visit_ClassDef = _scope

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in REPO_WRITERS and _dotted(node.value) in self.modules:
            self.found.append((node.lineno, ".".join(self.stack) or "<module>", node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.functions and isinstance(node.ctx, ast.Load):
            self.found.append((node.lineno, ".".join(self.stack) or "<module>", node.id))


def fact_repo_writes(root: Path) -> list[tuple[str, int, str, str]]:
    """Every use of the repository's ``insert`` or ``retract`` in a module under the ``nextron``
    folder ``root``, as ``(path, line, function, writer)``."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = _package_of(path, root)
        modules: set[str] = set()
        functions: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == REPO_MODULE:
                        modules.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                source = _imported_from(node, package)
                for alias in node.names:
                    if source == REPO_PACKAGE and alias.name == REPO_NAME:
                        modules.add(alias.asname or alias.name)
                    elif source == REPO_MODULE and alias.name in REPO_WRITERS:
                        functions.add(alias.asname or alias.name)
        if not (modules or functions):
            continue
        uses = _WriterUses(modules, functions)
        uses.visit(tree)
        relative = path.relative_to(root).as_posix()
        found += [(relative, line, function, writer) for line, function, writer in uses.found]
    return found


def fact_writer_problems(root: Path) -> list[str]:
    """Each use of the repository's writers outside ``app/playbook/api.py``."""
    return [
        f"{path}:{line} {function} uses the company_facts repository's {writer}"
        for path, line, function, writer in fact_repo_writes(root)
        if path != ALLOWED_WRITER
    ]


def test_only_the_playbook_api_writes_the_facts() -> None:
    writes = fact_repo_writes(PACKAGE)

    assert fact_writer_problems(PACKAGE) == []
    assert sorted({writer for *_, writer in writes}) == ["insert", "retract"]  # not vacuous
    assert {path for path, *_ in writes} == {ALLOWED_WRITER}


def probe_tree(tmp_path: Path, relative: str, source: str) -> Path:
    root = tmp_path / "nextron"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return root


WRITER_PROBES = [
    (
        "app/setup/api.py",
        "from nextron.db.repos.playbook import company_facts as facts_repo\n\n\n"
        "def sneaky(fact_id, at):\n    facts_repo.retract(fact_id, at)\n",
        "app/setup/api.py:5 sneaky uses the company_facts repository's retract",
    ),
    (
        "app/ops/api.py",
        "from nextron.db.repos.playbook.company_facts import insert as add\n\n\n"
        "def seed(fact_id, content, at):\n    add(fact_id, 1, content, 'seed', at)\n",
        "app/ops/api.py:5 seed uses the company_facts repository's add",
    ),
    (
        "app/other/probe.py",
        "import nextron.db.repos.playbook.company_facts\n\n"
        "nextron.db.repos.playbook.company_facts.insert('x', 1, None, 'seed', None)\n",
        "app/other/probe.py:3 <module> uses the company_facts repository's insert",
    ),
    (
        "web/probe.py",
        "import nextron.db.repos.playbook.company_facts as c\n\n\n"
        "class Page:\n    def post(self, fact_id, at):\n        undo = c.retract\n"
        "        undo(fact_id, at)\n",
        "web/probe.py:6 Page.post uses the company_facts repository's retract",
    ),
    (
        "db/repos/playbook/helper.py",
        "from . import company_facts\n\n\ndef copy(fact_id, content, at):\n"
        "    company_facts.insert(fact_id, 2, content, 'seed', at)\n",
        "db/repos/playbook/helper.py:5 copy uses the company_facts repository's insert",
    ),
]


@pytest.mark.parametrize(("relative", "source", "found"), WRITER_PROBES)
def test_the_writer_check_names_a_write_outside_the_playbook_api(
    relative: str, source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, relative, source)

    assert fact_writer_problems(root) == [found]


def test_the_writer_check_allows_reads_and_the_one_writer(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        "app/playbook/api.py",
        "from nextron.db.repos.playbook import company_facts as facts_repo\n"
        "from sqlalchemy import insert\n\n\n"
        "def save(fact_id, content, at):\n    facts_repo.insert(fact_id, 1, content, 'seed', at)\n"
        "    facts_repo.retract(fact_id, at)\n\n\n"
        "def read():\n    insert('x')\n"
        "    return facts_repo.all_versions(), facts_repo.versions_of('x')\n",
    )
    reader = root / "app" / "queries" / "playbook.py"
    reader.parent.mkdir(parents=True)
    reader.write_text(
        "from nextron.db.repos.playbook import company_facts as repo\n\n\n"
        "def facts():\n    return repo.all_versions()\n",
        encoding="utf-8",
    )

    assert fact_writer_problems(root) == []
    assert len(fact_repo_writes(root)) == 2


# The web layer -----------------------------------------------------------------------------------


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
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield node.lineno, module
            for alias in node.names:
                yield node.lineno, alias.name
                yield node.lineno, f"{module}.{alias.name}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def web_fact_writer_uses(root: Path) -> list[str]:
    """Each line of a module under ``root`` that names ``seed_facts`` or the playbook
    repositories, as ``<path>:<line> names <what>``."""
    problems = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        label = path.relative_to(root).as_posix()
        for line, name in _names(tree):
            if name in FORBIDDEN_WEB_NAMES:
                problems.append(f"{label}:{line} names {name}")
            elif name == REPO_PACKAGE or name.startswith(f"{REPO_PACKAGE}."):
                problems.append(f"{label}:{line} names {REPO_PACKAGE}")
    return sorted(set(problems))


def test_no_web_module_names_the_fact_writers() -> None:
    assert (WEB / "facts_page.py").is_file()  # not vacuous
    assert web_fact_writer_uses(WEB) == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("facts.seed_facts(checked, now)\n", "probe.py:1 names seed_facts"),
        ("from nextron.app.playbook.api import seed_facts\n", "probe.py:1 names seed_facts"),
        ("getattr(facts, 'seed_facts')\n", "probe.py:1 names seed_facts"),
        (
            "from nextron.db.repos.playbook import company_facts\n",
            "probe.py:1 names nextron.db.repos.playbook",
        ),
        (
            "import nextron.db.repos.playbook.company_facts as repo\n",
            "probe.py:1 names nextron.db.repos.playbook",
        ),
        (
            "x = 1\nfrom nextron.db.repos.playbook.company_facts import retract\n",
            "probe.py:2 names nextron.db.repos.playbook",
        ),
    ],
)
def test_the_web_check_catches_a_probe_file(source: str, found: str, tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(source, encoding="utf-8")

    assert found in web_fact_writer_uses(tmp_path)


def test_the_web_check_allows_the_use_cases_and_the_queries(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(
        "from nextron.app.queries import playbook\n"
        "from nextron.app.playbook.api import CompanyFacts\n\n"
        "facts = playbook.company_facts()\nCompanyFacts(clock).retract('legal_ai')\n",
        encoding="utf-8",
    )

    assert web_fact_writer_uses(tmp_path) == []
