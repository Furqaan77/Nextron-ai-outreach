"""Market contracts (FR-2, NFR-7, Story 3.1), each with a probe proving the check catches a
violation.

- **(a) No POST route edits a Market rule.** No POST route of the built dashboard app has
  ``market`` (in any case) in its path, its route name or its endpoint's ``__module__`` or
  ``__qualname__``: Markets change only through ``seed/markets.yaml`` and ``nextron seed sync``.
- **(b) The web layer can't reach the Market writers.** No module under ``nextron/web`` names
  ``sync_markets``, ``sync_seed`` or ``seed_sync`` (a name, an attribute, an imported name or a
  string), or imports ``nextron.db.repos.setup.markets`` (AST).
- **(c) A Lead's Market is derived, never stored.** A ``leads`` table in ``Base.metadata``, if
  one exists, has no column whose name contains ``market``. Until Story 2.2 adds ``leads`` the
  check is vacuous; the probe shows it bites.
- **(d) One writer of the Markets.** An AST scan of every module under ``nextron/`` finds the
  ``markets`` repository's ``insert`` and ``update`` used (called or referenced, through any
  alias of ``nextron.db.repos.setup.markets``: ``import ... as m``, a plain dotted import,
  ``from nextron.db.repos.setup import markets as x``, a relative import, or ``from
  ...markets import insert as y``) only inside ``_sync_markets`` of
  ``nextron/app/setup/api.py``, whatever a future writer is called.
"""

import ast
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import Column, Integer, MetaData, String, Table

from nextron.db.models import Base
from tests.contracts.support import PACKAGE
from tests.dashboard import Dashboard

WEB = PACKAGE / "web"
FORBIDDEN_NAMES = frozenset({"sync_markets", "sync_seed", "seed_sync"})
FORBIDDEN_MODULE = "nextron.db.repos.setup.markets"


# (a) No POST route that touches a Market rule ----------------------------------------------------


def market_post_routes(app: FastAPI) -> list[str]:
    """Each POST route whose path, name, endpoint module or endpoint qualname names a Market,
    as ``"POST <path>"``."""
    found = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "POST" not in methods:
            continue
        endpoint = getattr(route, "endpoint", None)
        texts = (
            getattr(route, "path", ""),
            getattr(route, "name", ""),
            getattr(endpoint, "__module__", ""),
            getattr(endpoint, "__qualname__", ""),
        )
        if any("market" in str(text).casefold() for text in texts):
            found.append(f"POST {route.path}")  # type: ignore[attr-defined]
    return found


def test_no_post_route_touches_a_market_rule() -> None:
    app = Dashboard().app

    assert market_post_routes(app) == []
    assert any("POST" in getattr(route, "methods", set()) for route in app.routes)  # not vacuous


def endpoint(qualname: str, module: str = "nextron.web.rules_page") -> Callable[[], Response]:
    """A probe endpoint with the given ``__qualname__`` and ``__module__`` (this test module's
    own name contains "market", so the probes never keep it)."""

    def handler() -> PlainTextResponse:
        return PlainTextResponse("ok")

    handler.__qualname__ = qualname
    handler.__module__ = module
    return handler


def with_post_route(path: str, name: str, handler: Callable[[], Response]) -> FastAPI:
    app = Dashboard().app
    # the dashboard route class adds only checked routes, so append a plain APIRoute
    app.router.routes.append(APIRoute(path, handler, methods=["POST"], name=name))
    return app


@pytest.mark.parametrize(
    ("path", "name", "handler"),
    [
        ("/markets/uk/disable", "rules_disable", endpoint("toggle_rule")),  # the path
        ("/rules/uk/disable", "MARKET_disable", endpoint("toggle_rule")),  # the name, any case
        ("/rules/uk/disable", "rules_disable", endpoint("disable_market")),  # the qualname
        ("/rules/uk/save", "rules_save", endpoint("MarketRules.save")),  # with its class
        ("/rules/uk/save", "rules_save", endpoint("save", "nextron.web.markets_page")),
    ],
)
def test_the_check_names_a_post_route_that_touches_a_market(
    path: str, name: str, handler: Callable[[], Response]
) -> None:
    assert market_post_routes(with_post_route(path, name, handler)) == [f"POST {path}"]


def test_the_check_passes_a_post_route_that_names_no_market() -> None:
    app = with_post_route("/rules/uk/save", "rules_save", endpoint("save"))

    assert market_post_routes(app) == []


def test_the_check_ignores_a_get_route_that_names_markets() -> None:
    app = Dashboard().app
    page = endpoint("markets_page", "nextron.web.markets_page")
    app.router.routes.append(APIRoute("/markets", page, methods=["GET"], name="markets_page"))

    assert market_post_routes(app) == []


# (b) The web layer never names the Market writers ------------------------------------------------


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


def market_writer_uses(root: Path) -> list[str]:
    """Each line of a module under ``root`` that names a Market writer, as ``<path>:<line>
    names <what>``."""
    problems = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, name in _names(tree):
            if name in FORBIDDEN_NAMES:
                problems.append(f"{path.relative_to(root).as_posix()}:{line} names {name}")
            elif name == FORBIDDEN_MODULE or name.startswith(f"{FORBIDDEN_MODULE}."):
                problems.append(
                    f"{path.relative_to(root).as_posix()}:{line} names {FORBIDDEN_MODULE}"
                )
    return problems


def test_no_web_module_names_a_market_writer() -> None:
    assert sorted(WEB.rglob("*.py"))  # not vacuous
    assert market_writer_uses(WEB) == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("setup.sync_markets([])\n", "probe.py:1 names sync_markets"),
        ("x = 1\nresult = setup.sync_seed(a, b)\n", "probe.py:2 names sync_seed"),
        ("from nextron.app.setup.api import sync_seed\n", "probe.py:1 names sync_seed"),
        ("Setup(clock).seed_sync(checked, now)\n", "probe.py:1 names seed_sync"),
        ("getattr(setup, 'sync_markets')\n", "probe.py:1 names sync_markets"),
        (
            "from nextron.db.repos.setup import markets\n",
            "probe.py:1 names nextron.db.repos.setup.markets",
        ),
        (
            "import nextron.db.repos.setup.markets as m\n",
            "probe.py:1 names nextron.db.repos.setup.markets",
        ),
        (
            "from nextron.db.repos.setup.markets import insert\n",
            "probe.py:1 names nextron.db.repos.setup.markets",
        ),
    ],
)
def test_the_check_catches_a_probe_file(source: str, found: str, tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(source, encoding="utf-8")

    assert found in market_writer_uses(tmp_path)


def test_the_check_allows_reading_the_markets(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(
        "from nextron.app.queries import setup\nmarkets = setup.markets()\n", encoding="utf-8"
    )

    assert market_writer_uses(tmp_path) == []


# (c) A Lead never stores its Market --------------------------------------------------------------


def lead_market_columns(metadata: MetaData) -> list[str]:
    """Each column of a ``leads`` table whose name contains ``market``."""
    table = metadata.tables.get("leads")
    if table is None:
        return []
    return [f"leads.{column.name}" for column in table.columns if "market" in column.name]


def test_no_lead_column_stores_a_market() -> None:
    assert lead_market_columns(Base.metadata) == []


@pytest.mark.parametrize("column", ["market_id", "market", "lead_market_code"])
def test_the_check_catches_a_lead_market_column(column: str) -> None:
    probe = MetaData()
    Table(
        "leads",
        probe,
        Column("id", Integer, primary_key=True),
        Column("country_code", String(2)),
        Column(column, String(16)),
    )

    assert lead_market_columns(probe) == [f"leads.{column}"]


def test_a_market_column_elsewhere_is_not_a_lead_column() -> None:
    probe = MetaData()
    Table("footer_snapshots", probe, Column("market_id", String(26), primary_key=True))
    Table("leads", probe, Column("id", Integer, primary_key=True), Column("code", String(2)))

    assert lead_market_columns(probe) == []


# (d) One writer of the Markets -------------------------------------------------------------------

REPO_PACKAGE = "nextron.db.repos.setup"
REPO_WRITERS = frozenset({"insert", "update"})
ALLOWED_WRITER = ("app/setup/api.py", "_sync_markets")


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
    """Every use of the markets repository's writers in one module, with the function around
    it."""

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


def market_repo_writes(root: Path) -> list[tuple[str, int, str, str]]:
    """Every use of the markets repository's ``insert`` or ``update`` in a module under the
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
                    if alias.name == FORBIDDEN_MODULE:
                        modules.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                source = _imported_from(node, package)
                for alias in node.names:
                    if source == REPO_PACKAGE and alias.name == "markets":
                        modules.add(alias.asname or alias.name)
                    elif source == FORBIDDEN_MODULE and alias.name in REPO_WRITERS:
                        functions.add(alias.asname or alias.name)
        if not (modules or functions):
            continue
        uses = _WriterUses(modules, functions)
        uses.visit(tree)
        relative = path.relative_to(root).as_posix()
        found += [(relative, line, function, writer) for line, function, writer in uses.found]
    return found


def market_writer_problems(root: Path) -> list[str]:
    """Each use of the markets repository's writers outside ``_sync_markets``."""
    return [
        f"{path}:{line} {function} uses the markets repository's {writer}"
        for path, line, function, writer in market_repo_writes(root)
        if (path, function) != ALLOWED_WRITER
    ]


def test_only_sync_markets_writes_the_markets() -> None:
    writes = market_repo_writes(PACKAGE)

    assert market_writer_problems(PACKAGE) == []
    assert sorted(writer for *_, writer in writes) == ["insert", "update"]  # not vacuous


def probe_tree(tmp_path: Path, relative: str, source: str) -> Path:
    root = tmp_path / "nextron"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return root


WRITER_PROBES = [
    (
        "app/other/probe.py",
        "from nextron.db.repos.setup import markets as markets_repo\n\n\n"
        "def sneaky(market, at):\n    markets_repo.update('id', market, 2, at)\n",
        "app/other/probe.py:5 sneaky uses the markets repository's update",
    ),
    (
        "app/setup/api.py",
        "from nextron.db.repos.setup import markets as markets_repo\n\n\n"
        "def _sync_markets(chosen, now):\n    markets_repo.insert(chosen[0], now)\n\n\n"
        "def _enable(market, now):\n    markets_repo.update('id', market, 2, now)\n",
        "app/setup/api.py:9 _enable uses the markets repository's update",
    ),
    (
        "app/other/probe.py",
        "from nextron.db.repos.setup.markets import insert as add\n\n\n"
        "def _sync_markets(market, at):\n    add(market, at)\n",
        "app/other/probe.py:5 _sync_markets uses the markets repository's add",
    ),
    (
        "app/other/probe.py",
        "import nextron.db.repos.setup.markets\n\n"
        "nextron.db.repos.setup.markets.insert(None, None)\n",
        "app/other/probe.py:3 <module> uses the markets repository's insert",
    ),
    (
        "app/other/probe.py",
        "import nextron.db.repos.setup.markets as m\n\n\n"
        "class Writer:\n    def save(self, market, at):\n        write = m.update\n"
        "        write('id', market, 2, at)\n",
        "app/other/probe.py:6 Writer.save uses the markets repository's update",
    ),
    (
        "db/repos/setup/helper.py",
        "from . import markets\n\n\ndef copy(market, at):\n    markets.insert(market, at)\n",
        "db/repos/setup/helper.py:5 copy uses the markets repository's insert",
    ),
]


@pytest.mark.parametrize(("relative", "source", "found"), WRITER_PROBES)
def test_the_writer_check_names_a_write_outside_sync_markets(
    relative: str, source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, relative, source)

    assert market_writer_problems(root) == [found]


def test_the_writer_check_allows_reads_and_the_one_writer(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        "app/setup/api.py",
        "from nextron.db.repos.setup import markets as markets_repo\n"
        "from sqlalchemy import insert, update\n\n\n"
        "def _sync_markets(chosen, now):\n    markets_repo.insert(chosen[0], now)\n"
        "    markets_repo.update('id', chosen[0], 2, now)\n\n\n"
        "def read():\n    insert('x')\n    update('x')\n    return markets_repo.all_markets()\n",
    )

    assert market_writer_problems(root) == []
    assert len(market_repo_writes(root)) == 2
