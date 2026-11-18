"""Eligibility contracts (FR-18, AD-16, NFR-7, Story 3.6), each with probes proving the check
catches a violation.

- **(a) No POST route touches Eligibility.** No POST route of the built dashboard app has
  ``eligib`` (in any case) in its path, its route name or its endpoint's ``__module__`` or
  ``__qualname__``: nothing on the dashboard can switch a check off or record a Decision.
- **(b) No setting can switch Eligibility off.** ``eligib`` is one of the protected stems of
  ``tests/contracts/test_settings_contracts.py``, and the declared settings schema passes that
  check.
- **(c) No Eligibility result is stored.** No table or column name in ``Base.metadata`` contains
  ``eligib``, except the Market rule ``markets.eligible_legal_forms``.
- **(d) Only compliance evaluates.** An AST scan of every module under ``nextron/`` finds
  ``nextron.domain.eligibility.evaluate`` named (imported, a star import included, used as an
  attribute of any alias of the module, fetched with ``getattr`` or written as a dotted string)
  outside ``domain/eligibility.py`` only in ``app/compliance/api.py``, so every consumer calls
  ``compliance.evaluate_eligibility``. The same forms through ``nextron.app.compliance.api``,
  which imports ``evaluate``, count too (``compliance_api.evaluate``).
- **(e) Its inputs are the rules and the facts, never a setting or a cache.**
  ``domain/eligibility.py`` and ``app/compliance/api.py`` don't import or name
  ``settings_schema``, name no ``nextron.app.queries.setup`` reader but ``markets``, and use no
  ``cache``, ``lru_cache`` or ``cached_property``.
"""

import ast
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.routing import APIRoute
from sqlalchemy import Column, Integer, MetaData, String, Table

from nextron.db.models import Base
from nextron.domain import settings_schema
from tests.contracts.support import PACKAGE
from tests.contracts.test_market_contracts import (
    _dotted,
    _imported_from,
    _package_of,
    endpoint,
    probe_tree,
    with_post_route,
)
from tests.contracts.test_settings_contracts import (
    PROTECTED_STEMS,
    probe,
    protected_feature_problems,
    setting,
)
from tests.dashboard import Dashboard

STEM = "eligib"

# (a) No POST route that touches Eligibility -------------------------------------------------------


def eligibility_post_routes(app: FastAPI) -> list[str]:
    """Each POST route whose path, name, endpoint module or endpoint qualname names
    Eligibility, as ``"POST <path>"``."""
    found = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "POST" not in methods:
            continue
        handler = getattr(route, "endpoint", None)
        texts = (
            getattr(route, "path", ""),
            getattr(route, "name", ""),
            getattr(handler, "__module__", ""),
            getattr(handler, "__qualname__", ""),
        )
        if any(STEM in str(text).casefold() for text in texts):
            found.append(f"POST {route.path}")  # type: ignore[attr-defined]
    return found


def test_no_post_route_touches_eligibility() -> None:
    app = Dashboard().app

    assert eligibility_post_routes(app) == []
    assert any("POST" in getattr(route, "methods", set()) for route in app.routes)  # not vacuous


@pytest.mark.parametrize(
    ("path", "name", "handler"),
    [
        ("/eligibility/skip", "rules_skip", endpoint("toggle")),  # the path
        ("/contacts/x/allow", "ELIGIBILITY_override", endpoint("toggle")),  # the name, any case
        ("/contacts/x/allow", "allow", endpoint("mark_eligible")),  # the qualname
        ("/contacts/x/allow", "allow", endpoint("Eligibility.override")),  # with its class
        ("/contacts/x/allow", "allow", endpoint("save", "nextron.web.eligibility_page")),
    ],
)
def test_the_route_check_names_a_post_route_that_touches_eligibility(
    path: str, name: str, handler: Callable[[], Response]
) -> None:
    assert eligibility_post_routes(with_post_route(path, name, handler)) == [f"POST {path}"]


def test_the_route_check_ignores_other_post_routes_and_any_get_route() -> None:
    app = with_post_route("/contacts/x/allow", "allow", endpoint("save"))
    page = endpoint("eligibility_page", "nextron.web.eligibility_page")
    app.router.routes.append(APIRoute("/eligibility", page, methods=["GET"], name="eligible"))

    assert eligibility_post_routes(app) == []


# (b) No setting can switch Eligibility off --------------------------------------------------------


def test_no_setting_can_switch_eligibility_off() -> None:
    assert STEM in PROTECTED_STEMS
    assert protected_feature_problems(settings_schema.SCHEMA) == []


@pytest.mark.parametrize(
    ("found", "problem"),
    [
        (setting("eligibility_off"), "eligibility_off: mentions eligib and holds off"),
        (setting("bypass_eligible"), "bypass_eligible: mentions eligib and holds bypass"),
        (
            setting("eligibility_checks", label="Eligibility checks: disabled"),
            "eligibility_checks: mentions eligib and holds disabled",
        ),
    ],
    ids=["off", "bypass", "label"],
)
def test_the_setting_check_catches_an_eligibility_off_switch(found: object, problem: str) -> None:
    assert protected_feature_problems(probe(found)) == [problem]  # type: ignore[arg-type]


# (c) No Eligibility result is stored --------------------------------------------------------------

ALLOWED_COLUMNS = frozenset({"markets.eligible_legal_forms"})


def stored_eligibility(metadata: MetaData) -> list[str]:
    """Each table, and each column outside ``ALLOWED_COLUMNS``, whose name contains
    ``eligib``."""
    found = []
    for table in metadata.tables.values():
        if STEM in table.name.casefold():
            found.append(table.name)
        for column in table.columns:
            named = f"{table.name}.{column.name}"
            if STEM in column.name.casefold() and named not in ALLOWED_COLUMNS:
                found.append(named)
    return sorted(found)


def test_no_eligibility_result_is_stored() -> None:
    assert stored_eligibility(Base.metadata) == []
    markets = Base.metadata.tables["markets"]
    assert "eligible_legal_forms" in markets.columns  # the allowance is still needed


@pytest.mark.parametrize(
    ("table", "column", "found"),
    [
        ("eligibility_decisions", "reason", ["eligibility_decisions"]),
        ("contacts", "eligible", ["contacts.eligible"]),
        ("contacts", "last_ELIGIBILITY_at", ["contacts.last_ELIGIBILITY_at"]),
        ("leads", "eligible_legal_forms", ["leads.eligible_legal_forms"]),  # only markets'
    ],
)
def test_the_storage_check_catches_an_eligibility_table_or_column(
    table: str, column: str, found: list[str]
) -> None:
    metadata = MetaData()
    Table(table, metadata, Column("id", Integer, primary_key=True), Column(column, String(8)))

    assert stored_eligibility(metadata) == found


def test_the_storage_check_allows_the_market_rule() -> None:
    metadata = MetaData()
    Table(
        "markets",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("eligible_legal_forms", String(8)),
    )

    assert stored_eligibility(metadata) == []


# (d) Only compliance evaluates --------------------------------------------------------------------

EVALUATE_MODULE = "nextron.domain.eligibility"
EVALUATE = "evaluate"
EVALUATE_HOMES = frozenset({"domain/eligibility.py", "app/compliance/api.py"})
# Each module that holds ``evaluate``, as (its package, its name there): the core, and the
# compliance API, which imports it (so ``compliance_api.evaluate`` reaches the core too).
EVALUATE_MODULES = {
    EVALUATE_MODULE: ("nextron.domain", "eligibility"),
    "nextron.app.compliance.api": ("nextron.app.compliance", "api"),
}


def _module_names(tree: ast.AST, package: str) -> tuple[set[str], list[int]]:
    """The names bound to a module of ``EVALUATE_MODULES`` in ``tree``, and the lines that
    import ``evaluate`` itself (or everything) from one."""
    modules: set[str] = set()
    imports: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in EVALUATE_MODULES:
                    modules.add(alias.asname or alias.name)
                elif alias.name.startswith(tuple(f"{module}." for module in EVALUATE_MODULES)):
                    imports.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            source = _imported_from(node, package)
            for alias in node.names:
                if (source, alias.name) in EVALUATE_MODULES.values():
                    modules.add(alias.asname or alias.name)
                elif source in EVALUATE_MODULES and alias.name in (EVALUATE, "*"):
                    imports.append(node.lineno)
    return modules, imports


def _evaluate_lines(tree: ast.AST, package: str) -> list[int]:
    """Each line of ``tree`` that names ``nextron.domain.eligibility.evaluate``."""
    modules, lines = _module_names(tree, package)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == EVALUATE:
            if _dotted(node.value) in modules:
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and _dotted(node.func) == "getattr":
            target, *rest = node.args or [None]
            named = rest[0] if rest else None
            if (
                target is not None
                and _dotted(target) in modules
                and isinstance(named, ast.Constant)
                and named.value == EVALUATE
            ):
                lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value in {
            f"{module}.{EVALUATE}" for module in EVALUATE_MODULES
        }:
            lines.append(node.lineno)
    return sorted(set(lines))


def evaluate_uses(root: Path) -> list[tuple[str, int]]:
    """Every line of a module under the ``nextron`` folder ``root`` that names ``evaluate``, as
    ``(path, line)``."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(root).as_posix()
        found += [(relative, line) for line in _evaluate_lines(tree, _package_of(path, root))]
    return found


def evaluate_problems(root: Path) -> list[str]:
    """Each use of ``evaluate`` outside its homes."""
    return [
        f"{path}:{line} names {EVALUATE_MODULE}.{EVALUATE}"
        for path, line in evaluate_uses(root)
        if path not in EVALUATE_HOMES
    ]


def test_only_compliance_evaluates_eligibility() -> None:
    uses = evaluate_uses(PACKAGE)

    assert evaluate_problems(PACKAGE) == []
    assert [path for path, _ in uses].count("app/compliance/api.py") >= 1  # not vacuous


EVALUATE_PROBES = [
    ("web/probe.py", "from nextron.domain.eligibility import evaluate\n", 1),
    (
        "app/sequencing/api.py",
        "from nextron.domain.eligibility import Decision, evaluate as decide\n",
        1,
    ),
    (
        "app/other.py",
        "import nextron.domain.eligibility\n\nnextron.domain.eligibility.evaluate(1, 2, 3, 4)\n",
        3,
    ),
    ("app/other.py", "import nextron.domain.eligibility as e\n\nx = e.evaluate\n", 3),
    (
        "app/other.py",
        "from nextron.domain import eligibility as el\n\n\ndef f():\n    return el.evaluate\n",
        5,
    ),
    ("domain/helper.py", "from . import eligibility\n\neligibility.evaluate\n", 3),
    ("domain/helper.py", "from .eligibility import evaluate\n", 1),
    ("app/other.py", "from nextron.domain.eligibility import *\n", 1),
    ("app/other.py", "import nextron.domain.eligibility as e\ngetattr(e, 'evaluate')\n", 2),
    ("app/other.py", "TARGET = 'nextron.domain.eligibility.evaluate'\n", 1),
    # the compliance service may call it only from its API module
    ("app/compliance/helper.py", "from nextron.domain.eligibility import evaluate\n", 1),
    # the core reached through the compliance API, which imports it
    ("app/sequencing/api.py", "from nextron.app.compliance.api import evaluate\n", 1),
    ("app/compliance/helper.py", "from .api import evaluate as decide\n", 1),
    (
        "app/sequencing/api.py",
        "from nextron.app.compliance import api as compliance_api\n\n\n"
        "def enroll(facts):\n    return compliance_api.evaluate(*facts)\n",
        5,
    ),
    (
        "web/probe.py",
        "import nextron.app.compliance.api as c\n\ngetattr(c, 'evaluate')\n",
        3,
    ),
    (
        "app/other.py",
        "import nextron.app.compliance.api\n\nnextron.app.compliance.api.evaluate(1, 2, 3, 4)\n",
        3,
    ),
]


@pytest.mark.parametrize(("relative", "source", "line"), EVALUATE_PROBES)
def test_the_evaluate_check_names_a_use_outside_compliance(
    relative: str, source: str, line: int, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, relative, source)

    named = f"{relative}:{line} names nextron.domain.eligibility.evaluate"
    assert evaluate_problems(root) == [named]


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("app/compliance/api.py", "from nextron.domain.eligibility import evaluate\n"),
        ("web/leads_page.py", "from nextron.domain.eligibility import Decision\n"),
        ("app/other.py", "from nextron.domain import markets\n\nmarkets.evaluate\n"),
        ("app/other.py", "def f(rules):\n    return rules.evaluate()\n"),
        ("app/other.py", "from nextron.domain import eligibility\n\neligibility.Decision\n"),
        (
            "web/app.py",
            "from nextron.app.compliance import api as compliance_api\n\n"
            "compliance_api.Compliance(clock).evaluate_eligibility(contact_id)\n",
        ),
    ],
)
def test_the_evaluate_check_allows_the_api_and_other_names(
    relative: str, source: str, tmp_path: Path
) -> None:
    assert evaluate_problems(probe_tree(tmp_path, relative, source)) == []


# (e) Rules and facts, never a setting or a cache --------------------------------------------------

INPUT_MODULES = ("domain/eligibility.py", "app/compliance/api.py")
SETUP_QUERIES = "nextron.app.queries.setup"
SETUP_READERS_ALLOWED = frozenset({"markets"})
CACHES = frozenset({"cache", "lru_cache", "cached_property"})


def _setup_aliases(tree: ast.AST, package: str) -> tuple[set[str], list[tuple[int, str]]]:
    """The names bound to ``nextron.app.queries.setup``, and each name imported from it."""
    modules: set[str] = set()
    imported: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == SETUP_QUERIES:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            source = _imported_from(node, package)
            for alias in node.names:
                if source == "nextron.app.queries" and alias.name == "setup":
                    modules.add(alias.asname or alias.name)
                elif source == SETUP_QUERIES:
                    imported.append((node.lineno, alias.name))
    return modules, imported


def _imported_modules(node: ast.Import | ast.ImportFrom, package: str) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
        return
    source = _imported_from(node, package)
    yield source
    for alias in node.names:
        yield f"{source}.{alias.name}"


def _input_problems(tree: ast.AST, package: str) -> list[tuple[int, str]]:
    problems: list[tuple[int, str]] = []
    modules, imported = _setup_aliases(tree, package)
    problems += [
        (line, f"reads queries.setup.{name}")
        for line, name in imported
        if name not in SETUP_READERS_ALLOWED
    ]
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import | ast.ImportFrom):
            for module in _imported_modules(node, package):
                if "settings_schema" in module.split("."):
                    problems.append((line, "imports settings_schema"))
            for alias in node.names:
                for name in (alias.name.rpartition(".")[2], alias.asname):
                    if name in CACHES:
                        problems.append((line, f"uses {name}"))
        elif isinstance(node, ast.Name | ast.Attribute):
            named = node.id if isinstance(node, ast.Name) else node.attr
            if named == "settings_schema":
                problems.append((line, "names settings_schema"))
            elif named in CACHES:
                problems.append((line, f"uses {named}"))
            if (
                isinstance(node, ast.Attribute)
                and _dotted(node.value) in modules
                and node.attr not in SETUP_READERS_ALLOWED
            ):
                problems.append((line, f"reads queries.setup.{node.attr}"))
        elif isinstance(node, ast.Call) and _dotted(node.func) == "getattr":
            target, *rest = node.args or [None]
            named_arg = rest[0] if rest else None
            if (
                target is not None
                and _dotted(target) in modules
                and isinstance(named_arg, ast.Constant)
                and named_arg.value not in SETUP_READERS_ALLOWED
            ):
                problems.append((line, f"reads queries.setup.{named_arg.value}"))
    return problems


def input_problems(root: Path) -> list[str]:
    """Each setting, setup reader or cache the Eligibility modules under ``root`` use."""
    found = []
    for relative in INPUT_MODULES:
        path = root / relative
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found += [
            f"{relative}:{line} {what}"
            for line, what in _input_problems(tree, _package_of(path, root))
        ]
    return sorted(set(found))


def test_eligibility_reads_only_the_markets_and_caches_nothing() -> None:
    assert all((PACKAGE / relative).is_file() for relative in INPUT_MODULES)  # not vacuous
    assert input_problems(PACKAGE) == []
    api = ast.parse((PACKAGE / "app/compliance/api.py").read_text(encoding="utf-8"))
    modules, _ = _setup_aliases(api, "nextron.app.compliance")
    assert modules  # the one allowed reader is still read through an alias


INPUT_PROBES = [
    ("app/compliance/api.py", "from nextron.domain import settings_schema\n", "1 imports"),
    ("domain/eligibility.py", "import nextron.domain.settings_schema as s\n", "1 imports"),
    ("domain/eligibility.py", "from .settings_schema import Key\n", "1 imports"),
    (
        "app/compliance/api.py",
        "from nextron.app.queries import setup as setup_queries\n\nsetup_queries.get('x')\n",
        "3 reads queries.setup.get",
    ),
    (
        "app/compliance/api.py",
        "import nextron.app.queries.setup as s\n\ns.footer_settings()\n",
        "3 reads queries.setup.footer_settings",
    ),
    (
        "app/compliance/api.py",
        "from nextron.app.queries.setup import get\n",
        "1 reads queries.setup.get",
    ),
    (
        "app/compliance/api.py",
        "from nextron.app.queries import setup\n\ngetattr(setup, 'stored_texts')()\n",
        "3 reads queries.setup.stored_texts",
    ),
    ("domain/eligibility.py", "from functools import lru_cache\n", "1 uses lru_cache"),
    (
        "domain/eligibility.py",
        "import functools\n\n\n@functools.cache\ndef f():\n    pass\n",
        "4 uses cache",
    ),
    (
        "app/compliance/api.py",
        "from functools import cached_property as cp\n",
        "1 uses cached_property",
    ),
]


@pytest.mark.parametrize(("relative", "source", "found"), INPUT_PROBES)
def test_the_input_check_catches_a_setting_reader_or_cache(
    relative: str, source: str, found: str, tmp_path: Path
) -> None:
    root = probe_tree(tmp_path, relative, source)

    assert f"{relative}:{found}" in " | ".join(input_problems(root))


def test_the_input_check_allows_reading_the_markets(tmp_path: Path) -> None:
    root = probe_tree(
        tmp_path,
        "app/compliance/api.py",
        "from nextron.app.queries import setup as setup_queries\n"
        "from nextron.app.queries.setup import markets\n\n"
        "rules = setup_queries.markets()\nother = markets()\n"
        "x = getattr(setup_queries, 'markets')\n",
    )

    assert input_problems(root) == []
