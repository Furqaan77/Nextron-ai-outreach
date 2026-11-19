"""Campaign, Offer and Variant contracts (FR-3, AD-3, AD-4, NFR-3, NFR-7, Story 4.2), each with a
probe proving the check catches a violation.

- **One writer of the Offers and Campaigns.** An AST scan of every module under ``nextron/``
  finds the ``offers`` repository's ``insert`` and ``update``, and the ``campaigns`` repository's
  ``insert``, ``update_config``, ``insert_variant``, ``update_variant``, ``set_status``,
  ``set_lia`` and (Story 11.8) ``update_definition`` and ``set_variant_status``, used (called or referenced, through any alias of the module: ``import ... as x``,
  a plain dotted import, ``from nextron.db.repos.setup import campaigns as x``, a relative
  import, or ``from ...campaigns import set_status as y``) only in ``nextron/app/setup/api.py``.
- **One writer of a Campaign's status.** ``set_status`` is used only inside ``_move`` of
  ``nextron/app/setup/api.py``, the write of the ``Campaigns`` transitions (each one checked
  against the Campaign machine first), so no other code moves a Campaign's status.
- **No web module imports a repository.** No module under ``nextron/web`` imports
  ``nextron.db.repos`` or any module in it (absolute or relative): the Campaigns panel goes
  through the ``Campaigns`` use cases and ``queries.setup`` only.
- **Nothing turns off the LIA rule (NFR-7).** No declared setting's key or label names the
  legitimate-interests assessment (its words, lowercased with every other run of characters a
  ``_``, hold ``lia`` or contain ``legitimate``), so none can switch the rule off; and no route
  of the built dashboard app whose path or name names it holds an off token (``disable``,
  ``skip``, ``waive``, ``bypass``, ``off``, ``override``, ``ignore``, ``clear``, ``remove``,
  ``delete``, ``exempt``, ``without``, ``no``, ``not`` or ``never``).
"""

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute

from nextron.domain import settings_schema
from tests.contracts.support import PACKAGE
from tests.dashboard import Dashboard

REPO_PACKAGE = "nextron.db.repos.setup"
WRITERS = {
    "offers": frozenset({"insert", "update"}),
    "campaigns": frozenset(
        {
            "insert",
            "update_config",
            "insert_variant",
            "update_variant",
            "set_status",
            "set_lia",
            "update_definition",  # Story 11.8
            "set_variant_status",
        }
    ),
}
ALLOWED_WRITER = "app/setup/api.py"
STATUS_WRITER = ("app/setup/api.py", "_move")
WEB = PACKAGE / "web"
REPOS = "nextron.db.repos"
OFF_TOKENS = frozenset(
    {
        "disable",
        "disabled",
        "skip",
        "waive",
        "bypass",
        "off",
        "override",
        "ignore",
        "clear",
        "remove",
        "delete",
        "exempt",
        "without",
        "no",
        "not",
        "never",
    }
)


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
    """Every use of one repository's writers in one module, with the function around it."""

    def __init__(self, writers: frozenset[str], modules: set[str], functions: dict[str, str]):
        self.writers = writers
        self.modules = modules  # names (or dotted names) bound to the repository module
        self.functions = functions  # a local name -> the writer it is bound to
        self.stack: list[str] = []
        self.found: list[tuple[int, str, str]] = []  # (line, function, writer)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _scope
    visit_AsyncFunctionDef = _scope
    visit_ClassDef = _scope

    def _where(self) -> str:
        return ".".join(self.stack) or "<module>"

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.writers and _dotted(node.value) in self.modules:
            self.found.append((node.lineno, self._where(), node.attr))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.functions and isinstance(node.ctx, ast.Load):
            self.found.append((node.lineno, self._where(), self.functions[node.id]))


def repo_writes(root: Path, name: str) -> list[tuple[str, int, str, str]]:
    """Every use of the ``name`` repository's writers in a module under the ``nextron`` folder
    ``root``, as ``(path, line, function, writer)``."""
    module_name = f"{REPO_PACKAGE}.{name}"
    writers = WRITERS[name]
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = _package_of(path, root)
        modules: set[str] = set()
        functions: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module_name:
                        modules.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                source = _imported_from(node, package)
                for alias in node.names:
                    if source == REPO_PACKAGE and alias.name == name:
                        modules.add(alias.asname or alias.name)
                    elif source == module_name and alias.name in writers:
                        functions[alias.asname or alias.name] = alias.name
        if not (modules or functions):
            continue
        uses = _WriterUses(writers, modules, functions)
        uses.visit(tree)
        relative = path.relative_to(root).as_posix()
        found += [(relative, line, where, writer) for line, where, writer in uses.found]
    return found


def writer_problems(root: Path) -> list[str]:
    """Each use of the Offer or Campaign writers outside ``app/setup/api.py``, and each use of
    ``set_status`` outside its ``_move``."""
    problems = []
    for name in WRITERS:
        for path, line, where, writer in repo_writes(root, name):
            if path != ALLOWED_WRITER:
                problems.append(f"{path}:{line} {where} uses the {name} repository's {writer}")
            elif writer == "set_status" and name == "campaigns" and (path, where) != STATUS_WRITER:
                problems.append(f"{path}:{line} {where} moves a Campaign's status")
    return problems


def test_only_the_setup_api_writes_the_offers_and_campaigns() -> None:
    assert writer_problems(PACKAGE) == []
    offers = repo_writes(PACKAGE, "offers")
    campaigns = repo_writes(PACKAGE, "campaigns")
    assert {writer for *_, writer in offers} == set(WRITERS["offers"])  # not vacuous
    assert {writer for *_, writer in campaigns} == set(WRITERS["campaigns"])
    assert [(p, w) for p, _, w, writer in campaigns if writer == "set_status"] == [STATUS_WRITER]


def probe_tree(tmp_path: Path, relative: str, source: str) -> Path:
    root = tmp_path / "nextron"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return root


WRITER_PROBES = [
    (
        "app/ops/api.py",
        "from nextron.db.repos.setup import campaigns as c\n\n\n"
        "def sneaky(campaign_id, at):\n    c.set_status(campaign_id, 'draft', 'active', at)\n",
        "app/ops/api.py:5 sneaky uses the campaigns repository's set_status",
    ),
    (
        "app/sequencing/api.py",
        "from nextron.db.repos.setup.campaigns import set_lia as record\n\n\n"
        "def enroll(campaign_id, lia, at):\n    record(campaign_id, lia, at)\n",
        "app/sequencing/api.py:5 enroll uses the campaigns repository's set_lia",
    ),
    (
        "web/app.py",
        "import nextron.db.repos.setup.offers\n\n\n"
        "def save(offer, at):\n    nextron.db.repos.setup.offers.update('id', offer, 2, at)\n",
        "web/app.py:5 save uses the offers repository's update",
    ),
    (
        "db/repos/setup/copy.py",
        "from . import campaigns\n\n\ndef copy(c, at):\n    campaigns.insert_variant('id', c, at)\n",
        "db/repos/setup/copy.py:5 copy uses the campaigns repository's insert_variant",
    ),
    (
        "app/setup/api.py",
        "from nextron.db.repos.setup import campaigns as campaigns_repo\n\n\n"
        "class Campaigns:\n    def force(self, cid, at):\n"
        "        campaigns_repo.set_status(cid, 'draft', 'active', at)\n",
        "app/setup/api.py:6 Campaigns.force moves a Campaign's status",
    ),
]


@pytest.mark.parametrize(("relative", "source", "problem"), WRITER_PROBES)
def test_the_writer_check_catches_a_probe(
    tmp_path: Path, relative: str, source: str, problem: str
) -> None:
    assert writer_problems(probe_tree(tmp_path, relative, source)) == [problem]


def test_the_writer_check_allows_reads_and_the_one_status_writer(tmp_path: Path) -> None:
    source = (
        "from nextron.db.repos.setup import campaigns as campaigns_repo\n\n\n"
        "def _move(stored, moved, now):\n"
        "    campaigns_repo.set_status(stored.id, stored.status, moved.to, now)\n\n\n"
        "def read():\n    return campaigns_repo.all_campaigns()\n"
    )
    root = probe_tree(tmp_path, "app/setup/api.py", source)
    (root / "app" / "queries").mkdir(parents=True)
    (root / "app" / "queries" / "setup.py").write_text(
        "from nextron.db.repos.setup import campaigns\n\n\ndef read(i):\n"
        "    return campaigns.by_id(i)\n",
        encoding="utf-8",
    )

    assert writer_problems(root) == []


# No web module imports a repository --------------------------------------------------------------


def web_repository_imports(root: Path) -> list[str]:
    """Each import of ``nextron.db.repos`` (or a module in it) in a module under ``root/web``."""
    problems = []
    for path in sorted((root / "web").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        package = _package_of(path, root)
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                source = _imported_from(node, package)
                names = [source, *(f"{source}.{alias.name}" for alias in node.names)]
            if any(name == REPOS or name.startswith(f"{REPOS}.") for name in names):
                problems.append(f"{relative}:{node.lineno} imports a repository")
    return problems


def test_no_web_module_imports_a_repository() -> None:
    assert web_repository_imports(PACKAGE) == []
    assert (WEB / "campaigns_page.py").is_file()  # the panel module is among those scanned


@pytest.mark.parametrize(
    "source",
    [
        "from nextron.db.repos.setup import campaigns\n",
        "import nextron.db.repos.setup.offers as offers\n",
        "from nextron.db import repos\n",
        "from ..db.repos.setup.campaigns import by_id\n",
    ],
)
def test_the_web_import_check_catches_a_probe(tmp_path: Path, source: str) -> None:
    root = probe_tree(tmp_path, "web/campaigns_page.py", source)

    assert web_repository_imports(root) == ["web/campaigns_page.py:1 imports a repository"]


# Nothing turns off the LIA rule (NFR-7) ----------------------------------------------------------


def words(text: str) -> list[str]:
    """``text`` lowercased, split at every run of characters other than ``a-z0-9``."""
    return [word for word in re.split(r"[^a-z0-9]+", text.lower()) if word]


def names_the_lia(text: str) -> bool:
    found = words(text)
    return "lia" in found or any("legitimate" in word for word in found)


@dataclass(frozen=True)
class Declared:
    key: str
    label: str


def lia_setting_problems(declared: Iterable[Declared]) -> list[str]:
    """Each setting whose key or label names the LIA: no setting may change the rule."""
    return [
        f"{found.key}: names the legitimate-interests assessment"
        for found in declared
        if names_the_lia(f"{found.key} {found.label}")
    ]


def test_no_setting_names_the_lia() -> None:
    declared = [Declared(str(found.key), found.label) for found in settings_schema.declared()]

    assert declared  # not vacuous
    assert lia_setting_problems(declared) == []


@pytest.mark.parametrize(
    ("found", "problem"),
    [
        (Declared("lia_required", "UK LIA required"), "lia_required"),
        (Declared("uk_mode", "Skip the legitimate-interests check"), "uk_mode"),
        (Declared("x", "Legitimate interests"), "x"),
    ],
)
def test_the_setting_check_catches_a_probe(found: Declared, problem: str) -> None:
    others = [Declared("sender_timezone", "Sender's timezone"), Declared("liaison", "Liaison")]

    assert lia_setting_problems([*others, found]) == [
        f"{problem}: names the legitimate-interests assessment"
    ]


def lia_route_problems(app: FastAPI) -> list[str]:
    """Each route whose path or name names the LIA and holds an off token."""
    found = []
    for route in app.routes:
        text = f"{getattr(route, 'path', '')} {getattr(route, 'name', '')}"
        if names_the_lia(text) and OFF_TOKENS & set(words(text)):
            found.append(str(getattr(route, "path", "")))
    return found


def test_no_route_turns_off_the_lia_rule() -> None:
    app = Dashboard().app

    assert lia_route_problems(app) == []
    lia_routes = [r for r in app.routes if names_the_lia(getattr(r, "path", ""))]
    assert [getattr(r, "name", "") for r in lia_routes] == ["campaign_lia"]  # not vacuous


def handler() -> PlainTextResponse:
    return PlainTextResponse("ok")


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/settings/campaigns/lia/skip", "campaign_lia_skip"),
        ("/settings/campaigns/waive", "campaign_lia_waive"),
        ("/settings/lia/disable", "lia_rule"),
        ("/settings/campaigns/clear-lia", "campaign_clear"),
        ("/settings/campaigns/activate-without-legitimate-interests", "campaign_activate_x"),
    ],
)
def test_the_route_check_catches_a_probe(path: str, name: str) -> None:
    app = Dashboard().app
    app.router.routes.append(APIRoute(path, handler, methods=["POST"], name=name))

    assert lia_route_problems(app) == [path]
