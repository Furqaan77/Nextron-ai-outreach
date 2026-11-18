"""Sequence contracts (FR-4, AD-3, AD-12, Story 4.1), with probes proving the check catches a
violation.

- **One writer of the Sequences.** An AST scan of every module under ``nextron/`` finds the
  ``sequences`` repository's ``insert`` and ``update`` used (called or referenced, through any
  alias of ``nextron.db.repos.setup.sequences``: ``import ... as s``, a plain dotted import,
  ``from nextron.db.repos.setup import sequences as x``, a relative import, or ``from
  ...sequences import insert as y``) only inside ``_sync_sequences`` of
  ``nextron/app/setup/api.py`` (the seed bootstrap) and ``SequenceEditor.edit`` of
  ``nextron/app/setup/sequence_editing.py`` (Story 11.7's editing screen), whatever a future
  writer is called. So Sequences and their Step briefs change only through those two.
"""

import ast
from pathlib import Path

import pytest

from tests.contracts.support import PACKAGE

REPO_PACKAGE = "nextron.db.repos.setup"
REPO_NAME = "sequences"
REPO_MODULE = f"{REPO_PACKAGE}.{REPO_NAME}"
REPO_WRITERS = frozenset({"insert", "update"})
ALLOWED_WRITER = ("app/setup/api.py", "_sync_sequences")
EDITOR = ("app/setup/sequence_editing.py", "SequenceEditor.edit")
ALLOWED_WRITERS = frozenset({ALLOWED_WRITER, EDITOR})


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
    """Every use of the sequences repository's writers in one module, with the function
    around it."""

    def __init__(self, modules: set[str], functions: set[str]) -> None:
        self.modules = modules  # names (or dotted names) bound to the repository module
        self.functions = functions  # names bound to insert or update themselves
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


def sequence_repo_writes(root: Path) -> list[tuple[str, int, str, str]]:
    """Every use of the sequences repository's ``insert`` or ``update`` in a module under the
    ``nextron`` folder ``root``, as ``(path, line, function, writer)``."""
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


def sequence_writer_problems(root: Path) -> list[str]:
    """Each use of the sequences repository's writers outside ``_sync_sequences`` and
    ``SequenceEditor.edit``."""
    return [
        f"{path}:{line} {function} uses the sequences repository's {writer}"
        for path, line, function, writer in sequence_repo_writes(root)
        if (path, function) not in ALLOWED_WRITERS
    ]


def test_only_sync_sequences_writes_the_sequences() -> None:
    writes = sequence_repo_writes(PACKAGE)

    assert sequence_writer_problems(PACKAGE) == []
    assert sorted(writer for *_, writer in writes) == ["insert", "update"]  # not vacuous
    assert {(path, function) for path, _, function, _ in writes} == ALLOWED_WRITERS


def probe_tree(tmp_path: Path, relative: str, source: str) -> Path:
    root = tmp_path / "nextron"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return root


WRITER_PROBES = [
    (
        "app/other/probe.py",
        "from nextron.db.repos.setup import sequences as sequences_repo\n\n\n"
        "def sneaky(sequence, at):\n    sequences_repo.update('id', sequence, 2, at)\n",
        "app/other/probe.py:5 sneaky uses the sequences repository's update",
    ),
    (
        "app/setup/api.py",
        "from nextron.db.repos.setup import sequences as sequences_repo\n\n\n"
        "def _sync_sequences(chosen, now):\n    sequences_repo.insert(chosen[0], now)\n\n\n"
        "def _edit_brief(sequence, now):\n    sequences_repo.update('id', sequence, 2, now)\n",
        "app/setup/api.py:9 _edit_brief uses the sequences repository's update",
    ),
    (
        "app/setup/api.py",
        "from nextron.db.repos.setup import sequences as sequences_repo\n\n\n"
        "def _sync_markets(chosen, now):\n    sequences_repo.insert(chosen[0], now)\n",
        "app/setup/api.py:5 _sync_markets uses the sequences repository's insert",
    ),
    (
        "app/other/probe.py",
        "from nextron.db.repos.setup.sequences import insert as add\n\n\n"
        "def _sync_sequences(sequence, at):\n    add(sequence, at)\n",
        "app/other/probe.py:5 _sync_sequences uses the sequences repository's add",
    ),
    (
        "app/other/probe.py",
        "import nextron.db.repos.setup.sequences\n\n"
        "nextron.db.repos.setup.sequences.insert(None, None)\n",
        "app/other/probe.py:3 <module> uses the sequences repository's insert",
    ),
    (
        "app/other/probe.py",
        "import nextron.db.repos.setup.sequences as s\n\n\n"
        "class Writer:\n    def save(self, sequence, at):\n        write = s.update\n"
        "        write('id', sequence, 2, at)\n",
        "app/other/probe.py:6 Writer.save uses the sequences repository's update",
    ),
    (
        "db/repos/setup/helper.py",
        "from . import sequences\n\n\ndef copy(sequence, at):\n    sequences.insert(sequence, at)\n",
        "db/repos/setup/helper.py:5 copy uses the sequences repository's insert",
    ),
]


@pytest.mark.parametrize(("relative", "source", "found"), WRITER_PROBES)
def test_the_writer_check_names_a_write_outside_sync_sequences(
    relative: str, source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, relative, source)

    assert sequence_writer_problems(root) == [found]


def test_the_writer_check_allows_reads_and_the_one_writer(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        "app/setup/api.py",
        "from nextron.db.repos.setup import markets as markets_repo\n"
        "from nextron.db.repos.setup import sequences as sequences_repo\n"
        "from sqlalchemy import insert, update\n\n\n"
        "def _sync_sequences(chosen, now):\n    sequences_repo.insert(chosen[0], now)\n"
        "    sequences_repo.update('id', chosen[0], 2, now)\n\n\n"
        "def read():\n    insert('x')\n    update('x')\n    markets_repo.insert(None, None)\n"
        "    return sequences_repo.all_sequences(), sequences_repo.by_key('default')\n",
    )

    assert sequence_writer_problems(root) == []
    assert len(sequence_repo_writes(root)) == 2
