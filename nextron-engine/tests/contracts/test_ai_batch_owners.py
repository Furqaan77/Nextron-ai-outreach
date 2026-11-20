"""The Message Batch tables are shared by row owner (Story 4.17, AD-3, AD-8): each service writes
only its own rows. ``nextron.db.ai_batches`` takes a ``BatchOwner`` on every function, so the
check is on the callers: under ``nextron/app/``,

- only ``app/research/`` and ``app/drafting/`` use ``nextron.db.ai_batches`` or ``BatchOwner``;
- every ``BatchOwner.<member>`` under ``app/research/`` is ``RESEARCH``, and under
  ``app/drafting/`` ``DRAFTING``; ``BatchOwner(...)`` (from a text) is never called;
- every call of an ``ai_batches`` function passes its owner (the first argument, or
  ``owner=``) as a ``BatchOwner.<member>`` or as a module-level name bound to one, so a
  parameter can't carry another owner in.

Probes prove each rule catches a violation."""

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from tests.contracts.support import PACKAGE

APP: Final = "app/"
OWNED_DIRS: Final = {"app/research/": "RESEARCH", "app/drafting/": "DRAFTING"}
MODULE: Final = "nextron.db.ai_batches"
ENUM: Final = "BatchOwner"


def _files(root: Path) -> Iterator[tuple[str, ast.Module]]:
    for path in sorted((root / "app").rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _owner_dir(relative: str) -> str | None:
    return next((d for d in OWNED_DIRS if relative.startswith(d)), None)


def _aliases(tree: ast.Module) -> set[str]:
    """The names this module binds to ``nextron.db.ai_batches``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "nextron.db":
            names |= {a.asname or a.name for a in node.names if a.name == "ai_batches"}
        elif isinstance(node, ast.Import):
            names |= {a.asname or a.name for a in node.names if a.name == MODULE}
    return names


def _uses_module(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
            node.module == MODULE
            or (node.module == "nextron.db" and any(a.name == "ai_batches" for a in node.names))
        ):
            return True
        if isinstance(node, ast.Import) and any(a.name == MODULE for a in node.names):
            return True
    return False


def _member(node: ast.expr) -> str | None:
    """``X`` when ``node`` is ``BatchOwner.X``."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == ENUM:
            return node.attr
    return None


def _constants(tree: ast.Module) -> dict[str, str]:
    """Module-level names bound to a ``BatchOwner.X``."""
    found: dict[str, str] = {}
    for statement in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign):
            target, value = statement.target, statement.value
        if isinstance(target, ast.Name) and value is not None and _member(value) is not None:
            found[target.id] = str(_member(value))
    return found


def owner_problems(root: Path = PACKAGE) -> list[str]:
    """Every way app code reaches another owner's batch rows."""
    problems: list[str] = []
    for relative, tree in _files(root):
        owned = _owner_dir(relative)
        wanted = None if owned is None else OWNED_DIRS[owned]
        if owned is None and _uses_module(tree):
            problems.append(f"{relative} uses {MODULE} but owns no batch rows")
        constants = _constants(tree)
        aliases = _aliases(tree)
        for node in ast.walk(tree):
            member = _member(node) if isinstance(node, ast.Attribute) else None
            if member is not None and member != wanted:
                problems.append(f"{relative}:{node.lineno} names {ENUM}.{member}")
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == ENUM:
                problems.append(f"{relative}:{node.lineno} builds a {ENUM} from a value")
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in aliases
            ):
                continue
            given = [kw.value for kw in node.keywords if kw.arg == "owner"] or node.args[:1]
            owner = given[0] if given else None
            named = None if owner is None else _member(owner)
            if named is None and isinstance(owner, ast.Name):
                named = constants.get(owner.id)
            if named is None:
                problems.append(
                    f"{relative}:{node.lineno} calls ai_batches.{func.attr} without a fixed owner"
                )
            elif named != wanted:
                problems.append(f"{relative}:{node.lineno} writes {named} batch rows")
    return problems


def test_each_service_reaches_only_its_own_batch_rows() -> None:
    assert owner_problems() == []


def test_research_is_the_one_batch_owner_built_so_far() -> None:
    users = {
        relative
        for relative, tree in _files(PACKAGE)
        if _uses_module(tree) or _constants(tree)
    }
    assert users and all(user.startswith("app/research/") for user in users)


def _probe(tmp_path: Path, relative: str, source: str) -> list[str]:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return owner_problems(tmp_path)


HEADER: Final = (
    "from nextron.db import ai_batches\nfrom nextron.domain.ai_batch import BatchOwner\n"
)


@pytest.mark.parametrize(
    ("relative", "body", "problem"),
    [
        (
            "app/research/x.py",
            "ai_batches.settle(BatchOwner.DRAFTING, 'i', 's', 'a')\n",
            "names BatchOwner.DRAFTING",
        ),
        (
            "app/drafting/x.py",
            "ai_batches.pending(BatchOwner.RESEARCH, 'r')\n",
            "writes RESEARCH batch rows",
        ),
        (
            "app/research/x.py",
            "def f(owner):\n    ai_batches.pending(owner, 'r')\n",
            "without a fixed owner",
        ),
        (
            "app/research/x.py",
            "ai_batches.queue_item(owner=BatchOwner('drafting'), route='r')\n",
            "builds a BatchOwner from a value",
        ),
        ("app/pipeline/x.py", "", "owns no batch rows"),
        (
            "app/research/x.py",
            "OWNER = BatchOwner.DRAFTING\nai_batches.pending(OWNER, 'r')\n",
            "writes DRAFTING batch rows",
        ),
    ],
)
def test_the_scan_catches_another_owners_rows(
    tmp_path: Path, relative: str, body: str, problem: str
) -> None:
    problems = _probe(tmp_path, relative, HEADER + body)

    assert any(problem in found for found in problems), problems


def test_the_scan_passes_a_fixed_own_owner(tmp_path: Path) -> None:
    body = (
        "OWNER = BatchOwner.RESEARCH\n"
        "ai_batches.pending(OWNER, 'r')\n"
        "ai_batches.settle(BatchOwner.RESEARCH, 'i', 's', 'a')\n"
    )
    assert _probe(tmp_path, "app/research/x.py", HEADER + body) == []
