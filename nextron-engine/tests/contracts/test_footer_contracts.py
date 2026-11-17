"""Footer contracts (FR-23, NFR-7, Story 4.11): checks that guard the footer against a setting
or a dashboard control that could remove or shorten it. Each part has a probe proving the check
catches a violation. ``FOOTER_WORDS`` are ``footer``, ``signature``, ``optout``, ``opt_out``,
``privacy``, ``postal``, ``legal``, ``solicitation`` and ``unsubscribe``, matched as substrings
of a text lowercased with every run of characters other than ``a-z0-9`` read as ``_`` (so
"Opt-out" and ``/opt-out`` both read ``opt_out``).

- **(a) The footer settings are fixed text.** Every key of ``FOOTER_SETTING_KEYS`` is declared
  in ``Section.FOOTER_AND_BOOKING`` as a ``string``, and is ``REQUIRED`` or has a non-blank
  default, except ``us_solicitation_label`` (it only adds a line). The Footer and booking
  settings are exactly ``FOOTER_SETTING_KEYS``, in that order.
- **(b) No other setting names the footer.** No declared setting outside
  ``FOOTER_SETTING_KEYS`` holds one of ``FOOTER_WORDS`` in its key or label. The one exception,
  ``NAMED_NOT_FOOTER``, is ``unsub_active_kid`` ("Unsubscribe token key id", Story 3.8): the
  integer id, 1 to 255, of the unsubscribe token keys new links are made with, which can't hold
  or hide footer text (a kid without keys refuses the enqueue, so no email goes out without a
  working link). It passes only with exactly that key, that label and the ``integer`` type.
- **(c) No route names the footer, and one handler writes settings.** No route of the built
  dashboard app holds one of ``FOOTER_WORDS`` in its path or name. Under ``nextron/web``, the
  only call of ``.save(...)`` or ``.set(...)`` on a receiver named ``setup`` (a name or an
  attribute) is in the ``settings_save`` function, the ``POST /settings`` handler; and
  ``Setup.save`` refuses to clear a set required footer key (rule ``required``, nothing stored).
- **(d) Two references are banned from the web layer.** No module under ``nextron/web`` imports
  ``nextron.db.repos.drafting`` (in any form, relative imports resolved) or names
  ``render_footer_template`` (AST). Rendering a footer through ``nextron.domain.render`` is not
  checked here.
"""

import ast
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute

from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import settings_schema
from nextron.domain.render import FOOTER_SETTING_KEYS
from nextron.domain.settings_schema import (
    REQUIRED,
    InvalidSettings,
    Key,
    Section,
    Setting,
    SettingType,
)
from tests.contracts.support import PACKAGE
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock

WEB = PACKAGE / "web"
LABEL_ONLY: Final = Key.US_SOLICITATION_LABEL.value  # adds a line, so it may be blank
FOOTER_WORDS: Final = (
    "footer",
    "signature",
    "optout",
    "opt_out",
    "privacy",
    "postal",
    "legal",
    "solicitation",
    "unsubscribe",
)
# key -> the exact label of a setting outside the footer that names a footer word but is an
# integer id, never footer text (the type is checked too).
NAMED_NOT_FOOTER: Final[Mapping[str, str]] = MappingProxyType(
    {"unsub_active_kid": "Unsubscribe token key id"}  # which unsubscribe keys (Story 3.8)
)
SETTINGS_WRITERS: Final = frozenset({"save", "set"})
SETTINGS_ROUTE: Final = ("POST", "/settings", "settings_save")
DRAFTING_REPOS: Final = "nextron.db.repos.drafting"
RENDERER: Final = "render_footer_template"
REQUIRED_FOOTER: Final = {
    "sender_signature": "Furqaan, Nextron AI",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 Example Street, London EC1A 1AA",
    "privacy_notice_url": "https://nextronai.com/privacy",
}


def words(text: str) -> str:
    """``text`` lowercased, with every run of characters other than ``a-z0-9`` as one ``_``."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def string_setting(key: str, **changes: object) -> Setting:
    values: dict[str, object] = {
        "key": key,
        "type": SettingType.STRING,
        "section": Section.FOOTER_AND_BOOKING,
        "label": key.replace("_", " ").capitalize(),
        "unit": "a unit",
        "default": REQUIRED,
        **changes,
    }
    return Setting(**values)  # type: ignore[arg-type]


def probe(
    schema: Mapping[str, Setting] = settings_schema.SCHEMA, *settings: Setting
) -> Mapping[str, Setting]:
    """``schema`` with ``settings`` added or replacing the ones of the same key."""
    return MappingProxyType({**schema, **{found.key: found for found in settings}})


# (a) The footer settings are fixed text -----------------------------------------------------------


def footer_setting_problems(
    schema: Mapping[str, Setting], keys: tuple[str, ...] = FOOTER_SETTING_KEYS
) -> list[str]:
    """Each way ``schema``'s footer settings could hide or shorten a footer line."""
    problems = []
    for key in keys:
        found = schema.get(key)
        if found is None:
            problems.append(f"{key}: not declared")
            continue
        if found.section is not Section.FOOTER_AND_BOOKING:
            problems.append(f"{key}: not in {Section.FOOTER_AND_BOOKING.value}")
        if found.type is not SettingType.STRING:
            problems.append(f"{key}: a {found.type}, not a string")
        if key == LABEL_ONLY or found.default is REQUIRED:
            continue
        if not (isinstance(found.default, str) and found.default.strip()):
            problems.append(f"{key}: neither required nor a non-blank default")
    in_section = tuple(
        str(found.key)
        for found in schema.values()
        if found.section is Section.FOOTER_AND_BOOKING
    )
    if in_section != keys:
        problems.append(f"{Section.FOOTER_AND_BOOKING.value} holds {', '.join(in_section)}")
    return problems


def test_the_footer_settings_are_fixed_text() -> None:
    assert footer_setting_problems(settings_schema.SCHEMA) == []
    assert len(FOOTER_SETTING_KEYS) == 6  # not vacuous


@pytest.mark.parametrize(
    ("replacement", "problem"),
    [
        (
            string_setting("legal_name", section=Section.ENGINE),
            "legal_name: not in Footer and booking",
        ),
        (
            string_setting("postal_address", type=SettingType.LIST, default=()),
            "postal_address: a list, not a string",
        ),
        (
            string_setting("optout_line", default=""),
            "optout_line: neither required nor a non-blank default",
        ),
        (
            string_setting("sender_signature", default="  "),
            "sender_signature: neither required nor a non-blank default",
        ),
        (
            string_setting("booking_link", default=""),
            "Footer and booking holds sender_signature, legal_name, postal_address, "
            "optout_line, privacy_notice_url, us_solicitation_label, booking_link",
        ),
    ],
    ids=["section", "type", "blank-default", "whitespace-default", "extra-setting"],
)
def test_the_footer_settings_check_catches_a_probe(replacement: Setting, problem: str) -> None:
    assert problem in footer_setting_problems(probe(settings_schema.SCHEMA, replacement))


def test_the_footer_settings_check_catches_a_missing_or_reordered_key() -> None:
    schema = dict(settings_schema.SCHEMA)
    del schema["postal_address"]
    assert "postal_address: not declared" in footer_setting_problems(MappingProxyType(schema))

    reordered = dict(settings_schema.SCHEMA)
    reordered["legal_name"] = reordered.pop("legal_name")  # now last
    assert any(
        problem.startswith("Footer and booking holds")
        for problem in footer_setting_problems(MappingProxyType(reordered))
    )


def test_the_us_label_alone_may_be_blank() -> None:
    assert footer_setting_problems(
        probe(settings_schema.SCHEMA, string_setting("us_solicitation_label", default=""))
    ) == []


# (b) No other setting names the footer -----------------------------------------------------------


def stray_footer_settings(
    schema: Mapping[str, Setting], keys: tuple[str, ...] = FOOTER_SETTING_KEYS
) -> list[str]:
    """Each setting outside ``keys`` whose key or label holds one of ``FOOTER_WORDS``."""
    problems = []
    for key, found in schema.items():
        if key in keys:
            continue
        if NAMED_NOT_FOOTER.get(key) == found.label and found.type is SettingType.INTEGER:
            continue  # an integer key id (Story 3.8), never footer text
        text = f"{words(key)}_{words(found.label)}"
        named = [word for word in FOOTER_WORDS if word in text]
        if named:
            problems.append(f"{key}: names {', '.join(named)}")
    return problems


def test_no_other_setting_names_the_footer() -> None:
    assert stray_footer_settings(settings_schema.SCHEMA) == []


@pytest.mark.parametrize(
    ("found", "problem"),
    [
        (string_setting("footer_lines", section=Section.ENGINE), "footer_lines: names footer"),
        (
            string_setting("sign_off", label="Short signature", section=Section.SENDER),
            "sign_off: names signature",
        ),
        (string_setting("hide_optout", section=Section.ENGINE), "hide_optout: names optout"),
        (
            string_setting("unsub_mode", label="Opt-out handling", section=Section.ENGINE),
            "unsub_mode: names opt_out",
        ),
        (
            string_setting("uk_notice", label="UK Privacy line", section=Section.ENGINE),
            "uk_notice: names privacy",
        ),
        (string_setting("show_postal_address"), "show_postal_address: names postal"),
        (string_setting("hide_legal_name"), "hide_legal_name: names legal"),
        (
            string_setting("us_mode", label="US solicitation text", section=Section.ENGINE),
            "us_mode: names solicitation",
        ),
        (
            string_setting("reply_words", label="Unsubscribe words", section=Section.ENGINE),
            "reply_words: names unsubscribe",
        ),
        # the one exception holds only for its own key, label and integer type
        (
            string_setting(
                "unsub_active_kid", label="Unsubscribe token key id", section=Section.SENDING
            ),
            "unsub_active_kid: names unsubscribe",
        ),
        (
            string_setting(
                "unsub_active_kid",
                type=SettingType.INTEGER,
                label="Unsubscribe footer key id",
                section=Section.SENDING,
                default=1,
            ),
            "unsub_active_kid: names footer, unsubscribe",
        ),
        (
            string_setting(
                "unsub_footer_kid",
                type=SettingType.INTEGER,
                label="Unsubscribe token key id",
                section=Section.SENDING,
                default=1,
            ),
            "unsub_footer_kid: names footer, unsubscribe",
        ),
    ],
)
def test_the_stray_setting_check_catches_a_probe(found: Setting, problem: str) -> None:
    assert stray_footer_settings(probe(settings_schema.SCHEMA, found)) == [problem]


def test_the_stray_setting_check_passes_the_unsubscribe_token_key_id() -> None:
    found = settings_schema.SCHEMA[Key.UNSUB_ACTIVE_KID]

    assert (found.label, found.type) == ("Unsubscribe token key id", SettingType.INTEGER)
    assert stray_footer_settings(probe(settings_schema.SCHEMA, found)) == []


# (c) No route names the footer; one handler writes settings --------------------------------------


def footer_routes(app: FastAPI) -> list[str]:
    """Each route whose path or name, read through ``words``, holds one of ``FOOTER_WORDS``,
    as ``<path>``."""
    found = []
    for route in app.routes:
        texts = (words(str(getattr(route, "path", ""))), words(str(getattr(route, "name", ""))))
        if any(word in text for word in FOOTER_WORDS for text in texts):
            found.append(str(getattr(route, "path", "")))
    return found


def test_no_route_names_the_footer() -> None:
    app = Dashboard().app

    assert footer_routes(app) == []
    assert len(app.routes) > 10  # not vacuous


def handler() -> PlainTextResponse:
    return PlainTextResponse("ok")


@pytest.mark.parametrize(
    ("path", "name", "methods"),
    [
        ("/settings/footer/hide", "settings_hide", ["POST"]),
        ("/settings/opt-out", "settings_stop", ["POST"]),
        ("/settings/address", "Postal_Address_save", ["POST"]),
        ("/legal-name/clear", "clear", ["POST"]),
        ("/unsubscribe/off", "off", ["POST"]),
        ("/drafts/edit", "SIGNATURE_edit", ["POST"]),
        ("/OptOut", "toggle", ["POST"]),
        ("/settings/privacy", "privacy_page", ["GET"]),  # a GET page counts too
    ],
)
def test_the_route_check_catches_a_probe(path: str, name: str, methods: list[str]) -> None:
    app = Dashboard().app
    app.router.routes.append(APIRoute(path, handler, methods=methods, name=name))

    assert footer_routes(app) == [path]


def _settings_writes(tree: ast.AST) -> list[tuple[int, str | None]]:
    """Each ``setup.save(...)``/``setup.set(...)`` call (on a name or an attribute called
    ``setup``), with the name of the function it is in (``None`` at module level)."""
    found: list[tuple[int, str | None]] = []

    def visit(node: ast.AST, function: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            inside = function
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                inside = child.name
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in SETTINGS_WRITERS
            ):
                owner = child.func.value
                owner_name = (
                    owner.id
                    if isinstance(owner, ast.Name)
                    else owner.attr
                    if isinstance(owner, ast.Attribute)
                    else None
                )
                if owner_name == "setup":
                    found.append((child.lineno, inside))
            visit(child, inside)

    visit(tree, None)
    return found


def settings_writers(root: Path) -> dict[str, list[str]]:
    """``{function: ["<path>:<line>", ...]}`` for every ``setup.save``/``setup.set`` call under
    ``root``."""
    writers: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, function in _settings_writes(tree):
            where = f"{path.relative_to(root).as_posix()}:{line}"
            writers.setdefault(function or "(module)", []).append(where)
    return writers


def route_of(app: FastAPI, function: str) -> list[tuple[str, str, str]]:
    """``(method, path, name)`` of each route whose endpoint is called ``function``."""
    return [
        (method, str(getattr(route, "path", "")), str(getattr(route, "name", "")))
        for route in app.routes
        if getattr(getattr(route, "endpoint", None), "__name__", None) == function
        for method in sorted(getattr(route, "methods", None) or ())
    ]


def test_only_the_settings_post_writes_settings() -> None:
    writers = settings_writers(WEB)

    assert list(writers) == ["settings_save"]  # not vacuous: the page does save
    assert route_of(Dashboard().app, "settings_save") == [SETTINGS_ROUTE]


@pytest.mark.parametrize(
    ("source", "function"),
    [
        ("def hide_footer(setup):\n    setup.set('optout_line', 'x')\n", "hide_footer"),
        ("def shorten(self):\n    self.setup.save({'legal_name': ''})\n", "shorten"),
        ("setup.save({})\n", "(module)"),
        (
            "def outer():\n    def inner(setup):\n        return setup.save({})\n",
            "inner",
        ),
    ],
)
def test_the_writer_check_catches_a_probe(source: str, function: str, tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(source, encoding="utf-8")

    assert function in settings_writers(tmp_path)


def test_the_writer_check_ignores_other_saves(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(
        "def f(credentials, store):\n    credentials.save('k', 'v')\n    store.set(1)\n",
        encoding="utf-8",
    )

    assert settings_writers(tmp_path) == {}


@pytest.mark.parametrize("key", sorted(REQUIRED_FOOTER))
def test_the_settings_save_cant_clear_a_set_required_footer_key(
    units: UnitOfWorkFactory, key: str
) -> None:
    setup = Setup(FrozenClock())
    with units.open():
        setup.save(REQUIRED_FOOTER)

    with pytest.raises(InvalidSettings) as raised, units.open():
        setup.save({key: ""})

    assert raised.value.problems[key].rule == "required"
    with units.open(read_only=True):
        assert setup_queries.stored_texts() == REQUIRED_FOOTER


# (d) Two references are banned from the web layer ------------------------------------------------


def _module_name(path: Path, root: Path, package: str) -> list[str]:
    parts = [*package.split("."), *path.relative_to(root).with_suffix("").parts]
    return parts[:-1] if parts[-1] == "__init__" else parts


def _imported(node: ast.ImportFrom, package_parts: list[str]) -> str:
    """The absolute module an ``ImportFrom`` names."""
    if not node.level:
        return node.module or ""
    base = package_parts[: len(package_parts) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def footer_reaches(root: Path, package: str = "nextron.web") -> list[str]:
    """Each line of a module under ``root`` (the package ``package``) that imports
    ``nextron.db.repos.drafting`` or names ``render_footer_template``, as ``<path>:<line>
    <what>``."""
    problems = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parts = _module_name(path, root, package)
        package_parts = parts if path.name == "__init__.py" else parts[:-1]
        where = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            modules: Iterable[str] = ()
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = _imported(node, package_parts)
                modules = [base, *(f"{base}.{alias.name}" for alias in node.names)]
                if any(alias.name == RENDERER for alias in node.names):
                    problems.append(f"{where}:{node.lineno} names {RENDERER}")
            elif isinstance(node, ast.Name) and node.id == RENDERER:
                problems.append(f"{where}:{node.lineno} names {RENDERER}")
            elif isinstance(node, ast.Attribute) and node.attr == RENDERER:
                problems.append(f"{where}:{node.lineno} names {RENDERER}")
            if any(
                module == DRAFTING_REPOS or module.startswith(f"{DRAFTING_REPOS}.")
                for module in modules
            ):
                problems.append(f"{where}:{node.lineno} imports {DRAFTING_REPOS}")
    return problems


def test_no_web_module_renders_or_snapshots_a_footer() -> None:
    assert sorted(WEB.rglob("*.py"))  # not vacuous
    assert footer_reaches(WEB) == []


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import nextron.db.repos.drafting\n", "probe.py:1 imports"),
        ("import nextron.db.repos.drafting.footer_snapshots as f\n", "probe.py:1 imports"),
        ("from nextron.db.repos import drafting\n", "probe.py:1 imports"),
        ("from nextron.db.repos.drafting import footer_snapshots\n", "probe.py:1 imports"),
        ("x = 1\nfrom ..db.repos.drafting import footer_snapshots\n", "probe.py:2 imports"),
        ("from ..db.repos import drafting as d\n", "probe.py:1 imports"),
        ("from nextron.domain.markets import render_footer_template\n", "probe.py:1 names"),
        ("text = markets.render_footer_template(t, v)\n", "probe.py:1 names"),
        ("text = render_footer_template(t, v)\n", "probe.py:1 names"),
    ],
)
def test_the_web_check_catches_a_probe(source: str, found: str, tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(source, encoding="utf-8")

    assert any(problem.startswith(found) for problem in footer_reaches(tmp_path))


def test_the_web_check_allows_the_setup_reads(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text(
        "from nextron.app.queries import setup\n"
        "from nextron.db.repos import setup as repos\n"
        "footer = setup.footer_settings()\n",
        encoding="utf-8",
    )

    assert footer_reaches(tmp_path) == []
