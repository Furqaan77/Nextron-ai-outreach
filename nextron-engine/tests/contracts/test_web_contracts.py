"""Dashboard contracts (AD-13), each with a live proof that the check catches a violation.

- **No ``async def`` route handler.** At run time over every route of the built app, and in the
  source (AST) of every module under ``nextron/web``: an ``async def`` decorated with a route
  decorator (``@app.get``, ``@router.post``, ``@app.api_route`` and so on) or passed to
  ``add_api_route``, ``add_route``, ``add_websocket_route`` or ``add_api_websocket_route``
  fails. An ASGI ``async def __call__`` and other async helpers are fine.
- **Every route declares ``personal_data``.** ``undeclared_routes`` of the built app is empty,
  and ``create_app`` refuses to build an app with an undeclared route.
- **Route rules.** ``route_rule_problems`` of the built app is empty: every route is an
  ``APIRoute`` that is exactly a GET run in a read-only unit of work or exactly a POST behind
  ``require_csrf``. The dashboard route class refuses any other route when it is added, and
  ``create_app`` refuses to build an app that breaks the rules.
- **No inline script or style, only self-hosted assets.** No template under
  ``nextron/web/templates`` has an inline ``<script>``, a ``<style>``, a ``style=``, ``on*=``,
  ``hx-on*`` or ``data-hx-on*`` attribute, a ``js:`` or ``javascript:`` value in ``hx-vals``,
  ``hx-vars`` or ``hx-headers`` (or their ``data-`` forms), or a ``javascript:``,
  ``vbscript:`` or ``data:`` URL, even one split by tab, newline or CR characters (browsers
  drop those). Every ``src``, ``srcset`` candidate, ``data`` and ``poster`` URL on any tag, and
  every ``<link href>``, is a local path. No stylesheet under ``nextron/web/static`` has an
  ``@import`` or an external ``url()``.
- **Autoescaping is on** in the environment the app renders with.
- **Forms post through HTMX with a CSRF token.** Every ``<form>`` in the templates has
  ``method="post"`` and ``hx-post``, and calls ``csrf_field()`` before its ``</form>``. Under
  ``Referrer-Policy: no-referrer`` a native form submission sends ``Origin: null``, which the
  guard refuses; HTMX's request keeps the real ``Origin``. A ``<form>`` may also be
  ``method="get"`` (a filter form, Story 2.4) when it has no ``hx-post`` (nor
  ``data-hx-post``), nothing naming ``csrf`` before its ``</form>`` (no ``csrf_field()`` and no
  ``csrf_token``: a token must never reach a URL), and an ``action`` that is a local path: it
  starts with ``/`` followed by neither ``/`` nor a backslash. A ``<form>`` with more than one
  ``method`` fails (a browser takes the first).
- **The safe allowlist.** Outside ``tests/contracts/safe_allowlist.txt``, no template (scanned
  as a whole, so a match may span lines) reaches the ``safe`` filter (``|safe``, a quoted
  ``'safe'`` filter name inside a Jinja tag such as ``map('safe')``, or a ``{% filter safe %}``
  block) or turns autoescaping off (any ``{% autoescape %}`` whose argument isn't literally
  ``true``). No module under ``nextron/web`` names ``Markup`` (a name, an attribute or an
  import, under any alias), names ``__html__`` (a ``def``, a name, an attribute or an
  assignment target) or holds a string that the template scan would flag. A use counts on the
  line where it starts. An allowlist line is ``<posix path>: <stripped source line>`` and allows
  one such line; ``#`` starts a comment line. An entry that matches nothing fails too.
"""

import ast
import functools
import inspect
import re
import shutil
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute
from jinja2 import Environment
from starlette.routing import Route

from nextron.web import app as web_app
from nextron.web.app import NavLink, template_environment
from nextron.web.csrf import require_csrf
from nextron.web.routing import (
    dashboard_route_class,
    personal_data,
    require_declared,
    require_route_rules,
    route_rule_problems,
    undeclared_routes,
)
from tests.contracts.support import ENGINE_ROOT, PACKAGE, copy_with_probe
from tests.dashboard import Dashboard, body_hx_headers
from tests.fakes.units import FakeUnits

SAFE_ALLOWLIST = Path(__file__).with_name("safe_allowlist.txt")

ROUTE_DECORATORS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route", "route"}
    | {"websocket", "websocket_route"}
)
ROUTE_REGISTRARS = frozenset(
    {"add_api_route", "add_route", "add_websocket_route", "add_api_websocket_route"}
)
INLINE_URL = re.compile(r"^[\x00-\x20]*(javascript|vbscript|data):", re.IGNORECASE)
URL_NOISE = re.compile(r"[\t\n\r]")  # browsers drop these anywhere in a URL
CSS_URL = re.compile(r"url\(\s*['\"]?\s*([^'\")\s]*)", re.IGNORECASE)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
URL_ATTRIBUTES = frozenset({"href", "action", "formaction"})  # no inline-code URLs
LOCAL_ATTRIBUTES = frozenset({"src", "data", "poster"})  # local paths only, on any tag
HTMX_VALUE_ATTRIBUTES = frozenset(
    {"hx-vals", "hx-vars", "hx-headers", "data-hx-vals", "data-hx-vars", "data-hx-headers"}
)
SCRIPT_VALUE = re.compile(r"^\s*(js|javascript):", re.IGNORECASE)


# No async def route handler -------------------------------------------------------------------


def async_route_handlers(app: FastAPI) -> list[str]:
    """Each route of ``app`` whose endpoint is a coroutine (or async generator) function."""
    problems = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        target = inspect.unwrap(endpoint)
        if inspect.iscoroutinefunction(target) or inspect.isasyncgenfunction(target):
            path = getattr(route, "path", route)
            problems.append(f"{path} is served by async def {target.__name__}")
    return problems


def _is_route_decorator(node: ast.expr) -> bool:
    call = node.func if isinstance(node, ast.Call) else node
    return isinstance(call, ast.Attribute) and call.attr in ROUTE_DECORATORS


def async_handler_problems(root: Path) -> list[str]:
    """Each ``async def`` under ``root/nextron/web`` that is registered as a route handler."""
    problems = []
    for path in sorted((root / "nextron" / "web").rglob("*.py")):
        label = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        async_defs = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
        async_names = {node.name for node in async_defs}  # for the registrar lookup only
        for node in async_defs:
            if any(_is_route_decorator(decorator) for decorator in node.decorator_list):
                problems.append(f"{label}:{node.lineno} route handler {node.name} is async def")
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ROUTE_REGISTRARS
            ):
                continue
            arguments = [*node.args[1:2], *(k.value for k in node.keywords if k.arg == "endpoint")]
            for argument in arguments:
                if isinstance(argument, ast.Name) and argument.id in async_names:
                    problems.append(
                        f"{label}:{node.lineno} route handler {argument.id} is async def"
                    )
    return problems


def test_no_route_handler_is_async_at_run_time() -> None:
    app = Dashboard().app

    assert [route.path for route in app.routes] == [
        "/enter",
        "/",
        "/health",
        "/static/{name}",
        "/settings",
        "/settings",
        "/settings/campaigns/activate",
        "/settings/campaigns/pause",
        "/settings/campaigns/resume",
        "/settings/campaigns/lia",
        "/facts",
        "/facts/add",
        "/facts/edit",
        "/facts/retract",
        "/ai",
        "/ai/check",
        "/ai/rollback",
        "/credentials",
        "/credentials/save",
        "/credentials/remove",
        "/credentials/unsub/save",
        "/credentials/unsub/remove",
        "/credentials/google/client",
        "/credentials/google/grant",
        "/credentials/google/grant/{grant}",
        "/credentials/google/remove",
        "/notifications",
        "/notifications/dismiss",
        "/quotas",
        "/health/panel",
        "/health/panel/live",
        "/health/jobs/dismiss",
        "/health/backup",
        "/health/restore-test",
        "/leads/import",
        "/leads/import",
        "/inboxes",
        "/inboxes",
        "/inboxes/warmup",
        "/inboxes/auth",
        "/inboxes/auth/recheck",
        "/suppression",
        "/suppression/add",
        "/suppression/import",
        "/suppression/search",
        "/suppression/reverse",
        "/contacts/{contact_id}/export",
        "/exports/{ticket}",
        "/leads",
        "/leads/pipeline",  # Story 9.7
        "/leads/{lead_id}/contacts/{contact_id}/erase",  # Story 3.13
        "/leads/{lead_id}/contacts/{contact_id}/erase",
        "/leads/{lead_id}",
        "/leads/{lead_id}/rescore",
        "/leads/{lead_id}/primary",
        "/leads/{lead_id}/find-email",
    ]
    assert async_route_handlers(app) == []


def test_no_route_handler_under_nextron_web_is_async_def() -> None:
    assert async_handler_problems(ENGINE_ROOT) == []


def test_the_run_time_check_catches_an_async_handler() -> None:
    app = Dashboard().app

    @personal_data(False)
    async def slow() -> PlainTextResponse:
        return PlainTextResponse("slow")

    # the dashboard route class refuses an async GET, so add a plain APIRoute behind its back
    app.router.routes.append(APIRoute("/slow", slow, methods=["GET"]))

    assert async_route_handlers(app) == ["/slow is served by async def slow"]


ASYNC_PROBE = '''"""A probe web module for the async handler check."""

from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()


{body}
'''


@pytest.mark.parametrize(
    "body",
    [
        '@app.get("/x")\nasync def x() -> None:\n    pass',
        '@router.post("/x", name="x")\nasync def x() -> None:\n    pass',
        '@app.api_route("/x", methods=["GET"])\nasync def x() -> None:\n    pass',
        '@app.websocket("/ws")\nasync def ws() -> None:\n    pass',
        'async def x() -> None:\n    pass\n\n\napp.add_api_route("/x", x)',
        'async def x() -> None:\n    pass\n\n\nrouter.add_api_route("/x", endpoint=x)',
        'async def ws() -> None:\n    pass\n\n\napp.add_api_websocket_route("/ws", ws)',
        # a later async def of the same name must not hide the decorated handler
        '@app.get("/x")\nasync def x() -> None:\n    pass\n\n\nasync def x() -> None:\n    pass',
    ],
)
def test_the_source_check_catches_an_async_route_handler(body: str, tmp_path: Path) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.web", ASYNC_PROBE.format(body=body))

    [problem] = async_handler_problems(root)

    assert problem.startswith("nextron/web/_lint_probe.py:")
    assert "is async def" in problem


@pytest.mark.parametrize(
    "body",
    [
        '@app.get("/x")\ndef x() -> None:\n    pass',
        "class Middleware:\n    async def __call__(self, scope, receive, send) -> None:\n"
        "        pass",
        "async def helper() -> None:\n    pass",
        'def x() -> None:\n    pass\n\n\napp.add_api_route("/x", x)',
    ],
)
def test_the_source_check_allows_sync_handlers_and_other_async_code(
    body: str, tmp_path: Path
) -> None:
    _, root = copy_with_probe(tmp_path, "nextron.web", ASYNC_PROBE.format(body=body))

    assert async_handler_problems(root) == []


# Every route declares personal_data ----------------------------------------------------------


def test_every_route_of_the_app_declares_personal_data() -> None:
    assert undeclared_routes(Dashboard().app) == []


def test_undeclared_routes_names_an_undeclared_route() -> None:
    app = Dashboard().app

    def undeclared() -> PlainTextResponse:
        return PlainTextResponse("undeclared")

    # the dashboard route class refuses GET and POST together, so add a plain APIRoute
    app.router.routes.append(APIRoute("/undeclared", undeclared, methods=["GET", "POST"]))

    assert undeclared_routes(app) == ["GET POST /undeclared"]
    with pytest.raises(RuntimeError, match="undeclared: GET POST /undeclared"):
        require_declared(app)


def test_create_app_refuses_undeclared_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    def declares_nothing(shows: bool):  # type: ignore[no-untyped-def]
        return lambda endpoint: endpoint

    monkeypatch.setattr(web_app, "personal_data", declares_nothing)

    with pytest.raises(RuntimeError) as caught:
        Dashboard()

    for route in (
        "GET /enter",
        "GET /",
        "GET /health",
        "GET /static/{name}",
        "GET /settings",
        "GET /credentials",
        "GET /credentials/google/grant/{grant}",
        "GET /facts",
        "GET /ai",
        "GET /quotas",
        "GET /health/panel",
        "GET /health/panel/live",
        "GET /inboxes",
        "GET /inboxes/auth",
        "GET /suppression",
        "GET /exports/{ticket}",
    ):
        assert route in str(caught.value)
    for route in (
        "POST /settings",
        "POST /settings/campaigns/activate",
        "POST /settings/campaigns/pause",
        "POST /settings/campaigns/resume",
        "POST /settings/campaigns/lia",
        "POST /credentials/save",
        "POST /credentials/remove",
        "POST /credentials/google/client",
        "POST /credentials/google/grant",
        "POST /credentials/google/remove",
        "POST /facts/add",
        "POST /facts/edit",
        "POST /facts/retract",
        "POST /ai/check",
        "POST /ai/rollback",
        "POST /health/jobs/dismiss",
        "POST /health/backup",
        "POST /inboxes",
        "POST /inboxes/warmup",
        "POST /inboxes/auth/recheck",
        "POST /suppression/add",
        "POST /suppression/import",
        "POST /suppression/search",
        "POST /suppression/reverse",
        "POST /leads/{lead_id}/primary",
        "POST /contacts/{contact_id}/export",
        "POST /leads/{lead_id}/find-email",
    ):
        assert route in str(caught.value)


@pytest.mark.parametrize("value", [None, 0, 1, "False"])
def test_personal_data_takes_a_bool(value: object) -> None:
    with pytest.raises(TypeError):
        personal_data(value)  # type: ignore[arg-type]


# Route rules ----------------------------------------------------------------------------------


@personal_data(False)
def _endpoint() -> PlainTextResponse:
    return PlainTextResponse("x")


def test_every_route_of_the_app_keeps_the_route_rules() -> None:
    app = Dashboard().app

    assert route_rule_problems(app) == []
    assert {type(route) for route in app.routes} == {app.router.route_class}


def test_route_rule_problems_names_each_route_that_breaks_them() -> None:
    app = Dashboard().app
    app.router.routes.extend(
        [
            APIRoute("/plain-get", _endpoint, methods=["GET"]),
            APIRoute("/plain-post", _endpoint, methods=["POST"]),
            APIRoute("/put", _endpoint, methods=["PUT"]),
            APIRoute("/both", _endpoint, methods=["GET", "POST"]),
            Route("/starlette", _endpoint, methods=["GET"]),
        ]
    )

    assert route_rule_problems(app) == [
        "GET /plain-get: a GET outside a read-only unit of work",
        "POST /plain-post: a POST whose first dependency isn't require_csrf",
        "PUT /put: methods other than exactly GET or exactly POST",
        "GET POST /both: methods other than exactly GET or exactly POST",
        "GET /starlette: not an APIRoute",
    ]
    with pytest.raises(RuntimeError, match="route rules broken: GET /plain-get"):
        require_route_rules(app)


def test_a_post_depending_on_another_function_named_require_csrf_is_named() -> None:
    async def lookalike() -> None:
        pass

    lookalike.__name__ = lookalike.__qualname__ = "require_csrf"  # the name, not the check
    app = Dashboard().app
    app.router.routes.append(
        APIRoute("/fake", _endpoint, methods=["POST"], dependencies=[Depends(lookalike)])
    )

    assert route_rule_problems(app) == [
        "POST /fake: a POST whose first dependency isn't require_csrf"
    ]


def test_a_post_that_runs_another_dependency_before_require_csrf_is_named() -> None:
    async def first() -> None:  # it would run, side effects and all, before the token check
        pass

    app = Dashboard().app
    app.router.routes.append(
        APIRoute(
            "/late",
            _endpoint,
            methods=["POST"],
            dependencies=[Depends(first), Depends(require_csrf)],
        )
    )

    assert route_rule_problems(app) == [
        "POST /late: a POST whose first dependency isn't require_csrf"
    ]


def test_a_get_that_only_copied_the_read_only_mark_is_named() -> None:
    app = Dashboard().app
    [home] = [route for route in app.routes if getattr(route, "name", None) == "home"]

    @functools.wraps(home.endpoint)  # copies the wrapper's attributes, mark included
    def impostor() -> PlainTextResponse:
        return PlainTextResponse("x")

    app.router.routes.append(APIRoute("/impostor", impostor, methods=["GET"]))

    assert route_rule_problems(app) == ["GET /impostor: a GET outside a read-only unit of work"]


def test_an_included_router_is_named() -> None:
    """FastAPI's ``include_router`` adds one route for the whole router, whatever route class
    built its routes; the rules can't see inside it, so it is refused. Add dashboard routes to
    the app itself."""
    app = Dashboard().app
    router = APIRouter()
    router.add_api_route("/elsewhere", _endpoint, methods=["GET"])

    app.include_router(router)

    included = app.routes[-1]
    assert getattr(included, "path", None) is None
    assert route_rule_problems(app) == [f"<{type(included).__name__}>: not an APIRoute"]


@pytest.mark.parametrize(
    "methods", [["PUT"], ["GET", "POST"], ["HEAD"], ["GET", "HEAD"], ["DELETE"], ["PATCH"]]
)
def test_the_route_class_refuses_other_methods_when_the_route_is_added(
    methods: list[str],
) -> None:
    app = Dashboard().app

    with pytest.raises(ValueError, match="exactly GET .* or exactly POST"):
        app.add_api_route("/x", _endpoint, methods=methods)


def test_the_route_class_refuses_an_async_or_generator_get() -> None:
    app = Dashboard().app

    @personal_data(False)
    async def slow() -> PlainTextResponse:
        return PlainTextResponse("slow")

    @personal_data(False)
    def rows() -> object:
        yield "x"

    for endpoint in (slow, rows):
        with pytest.raises(TypeError, match="plain sync def"):
            app.add_api_route("/x", endpoint, methods=["GET"])


def test_the_route_class_puts_require_csrf_first_on_a_post() -> None:
    async def other() -> None:
        pass

    route_class = dashboard_route_class(FakeUnits())
    route = route_class("/x", _endpoint, methods=["POST"], dependencies=[Depends(other)])

    assert [dependency.call for dependency in route.dependant.dependencies] == [
        require_csrf,
        other,
    ]


def test_create_app_refuses_an_app_that_breaks_the_route_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_app, "dashboard_route_class", lambda units: APIRoute)

    with pytest.raises(RuntimeError) as caught:
        Dashboard()

    for route in (
        "GET /enter",
        "GET /",
        "GET /health",
        "GET /static/{name}",
        "GET /settings",
        "GET /credentials",
        "GET /credentials/google/grant/{grant}",
        "GET /facts",
        "GET /ai",
        "GET /quotas",
        "GET /health/panel",
        "GET /health/panel/live",
        "GET /inboxes",
        "GET /inboxes/auth",
        "GET /suppression",
        "GET /exports/{ticket}",
    ):
        assert f"{route}: a GET outside a read-only unit of work" in str(caught.value)
    for route in (
        "POST /settings",
        "POST /settings/campaigns/activate",
        "POST /settings/campaigns/pause",
        "POST /settings/campaigns/resume",
        "POST /settings/campaigns/lia",
        "POST /credentials/save",
        "POST /credentials/remove",
        "POST /credentials/google/client",
        "POST /credentials/google/grant",
        "POST /credentials/google/remove",
        "POST /facts/add",
        "POST /facts/edit",
        "POST /facts/retract",
        "POST /ai/check",
        "POST /ai/rollback",
        "POST /health/jobs/dismiss",
        "POST /inboxes",
        "POST /inboxes/warmup",
        "POST /inboxes/auth/recheck",
        "POST /suppression/add",
        "POST /suppression/import",
        "POST /suppression/search",
        "POST /suppression/reverse",
        "POST /leads/{lead_id}/primary",
        "POST /contacts/{contact_id}/export",
        "POST /leads/{lead_id}/find-email",
    ):
        assert f"{route}: a POST whose first dependency isn't require_csrf" in str(caught.value)


# No inline script or style -------------------------------------------------------------------


class _TemplateChecker(HTMLParser):
    def __init__(self, label: str) -> None:
        super().__init__(convert_charrefs=True)
        self.label = label
        self.problems: list[str] = []
        self._in_script = False

    def _problem(self, why: str) -> None:
        self.problems.append(f"{self.label}:{self.getpos()[0]} {why}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for name, value in attrs:
            url = URL_NOISE.sub("", value or "")
            if name == "style":
                self._problem(f"<{tag}> has a style attribute")
            if name.startswith(("on", "hx-on", "data-hx-on")):
                self._problem(f"<{tag}> has an {name} attribute")
            if name in HTMX_VALUE_ATTRIBUTES and value and SCRIPT_VALUE.match(value):
                self._problem(f"<{tag}> has a script value in {name}")
            inline = INLINE_URL.match(url) if name in URL_ATTRIBUTES else None
            if inline:
                self._problem(f"<{tag}> has a {inline[1].lower()}: URL")
            if name in LOCAL_ATTRIBUTES and not _is_local(url):
                self._problem(f"<{tag}> loads {url or 'an empty URL'}")
            if name == "srcset":
                for candidate in url.split(","):
                    source = (candidate.split() or [""])[0]
                    if not _is_local(source):
                        self._problem(f"<{tag}> loads {source or 'an empty URL'}")
        if tag == "style":
            self._problem("<style> element")
        if tag == "script":
            self._in_script = bool(values.get("src"))  # without src: reported once, here
            if not values.get("src"):
                self._problem("inline <script> without src")
        if tag == "link" and not _is_local(URL_NOISE.sub("", values.get("href") or "")):
            self._problem(f"<link> loads {values.get('href')}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self._problem("inline code inside <script>")


def _is_local(url: str) -> bool:
    return url.startswith("/") and not url.startswith("//") and "\\" not in url


def css_problems(label: str, text: str) -> list[str]:
    """``@import`` and external ``url()`` in a stylesheet; comments don't count."""
    text = CSS_COMMENT.sub("", text)
    problems = []
    if re.search(r"@import", text, re.IGNORECASE):
        problems.append(f"{label} has an @import")
    for target in CSS_URL.findall(text):
        if ":" in target or target.startswith("//") or "\\" in target:
            problems.append(f"{label} has an external url({target})")
    return problems


def inline_code_problems(root: Path) -> list[str]:
    """Each inline script or style in ``root``'s templates and stylesheets."""
    web = root / "nextron" / "web"
    problems = []
    for path in sorted((web / "templates").rglob("*.html")):
        checker = _TemplateChecker(path.relative_to(root).as_posix())
        checker.feed(path.read_text(encoding="utf-8"))
        checker.close()
        problems += checker.problems
    for path in sorted((web / "static").rglob("*.css")):
        problems += css_problems(path.relative_to(root).as_posix(), path.read_text("utf-8"))
    return problems


def test_templates_and_stylesheets_have_no_inline_code() -> None:
    templates = sorted(p.name for p in (PACKAGE / "web" / "templates").glob("*.html"))

    assert templates == [
        "ai_content.html",
        "ai_content_panel.html",
        "away_banner.html",
        "base.html",
        "campaigns_panel.html",
        "contact_erase.html",
        "contact_erase_panel.html",
        "contact_export_form.html",
        "credential_row.html",
        "credentials.html",
        "erasure_receipt.html",
        "facts.html",
        "facts_panel.html",
        "google_client_row.html",
        "google_grant_row.html",
        "health.html",
        "health_panel.html",
        "home.html",
        "inbox_auth_panel.html",
        "inboxes.html",
        "inboxes_panel.html",
        "lead.html",
        "lead_contacts.html",
        "lead_drafts.html",  # Story 4.12
        "lead_import.html",
        "lead_import_form.html",
        "lead_legal_form.html",
        "lead_missing.html",
        "lead_score.html",
        "leads.html",
        "locked.html",
        "macros.html",
        "notifications.html",
        "pipeline.html",
        "quotas.html",
        "settings.html",
        "settings_form.html",
        "suppression.html",
        "suppression_panel.html",
        "unsub_key_row.html",
    ]
    assert inline_code_problems(ENGINE_ROOT) == []


def web_copy(tmp_path: Path) -> Path:
    root = tmp_path / "engine"
    shutil.copytree(PACKAGE / "web", root / "nextron" / "web")
    return root


@pytest.mark.parametrize(
    ("template", "why"),
    [
        ("<script>alert(1)</script>", "inline <script> without src"),
        ('<script src="/static/x.js">alert(1)</script>', "inline code inside <script>"),
        ('<script src="https://cdn.example/x.js"></script>', "<script> loads https://"),
        ('<script src="//cdn.example/x.js"></script>', "<script> loads //"),
        ('<link rel="stylesheet" href="https://cdn.example/x.css">', "<link> loads https://"),
        ("<style>p { color: red }</style>", "<style> element"),
        ('<p style="color: red">x</p>', "style attribute"),
        ('<button onclick="go()">x</button>', "onclick attribute"),
        ('<img src="/x.png" onerror="go()">', "onerror attribute"),
        ('<a href="javascript:go()">x</a>', "javascript: URL"),
        ('<a href=" JavaScript:go()">x</a>', "javascript: URL"),
        ('<form action="javascript:go()"></form>', "javascript: URL"),
        # javascript: split by tab, newline or CR (HTMLParser decodes the character references)
        ('<a href="java&#9;script:go()">x</a>', "javascript: URL"),
        ('<a href="java&#10;script:go()">x</a>', "javascript: URL"),
        ('<a href="&#13;javascript:go()">x</a>', "javascript: URL"),
        ('<a href="data:text/html,x">x</a>', "data: URL"),
        # htmx inline handlers and script values
        ('<button hx-on:click="go()">x</button>', "hx-on:click attribute"),
        ('<div hx-on::after-request="go()"></div>', "hx-on::after-request attribute"),
        ('<button data-hx-on:click="go()">x</button>', "data-hx-on:click attribute"),
        ("<div hx-vals='js:{a: go()}'></div>", "script value in hx-vals"),
        ('<div hx-vars="javascript:go()"></div>', "script value in hx-vars"),
        ('<div hx-headers="js:go()"></div>', "script value in hx-headers"),
        ("<div data-hx-vals='js:{a: go()}'></div>", "script value in data-hx-vals"),
        ('<div data-hx-headers=" JS:go()"></div>', "script value in data-hx-headers"),
        # external URLs on any tag
        ('<img src="https://cdn.example/x.png">', "<img> loads https://"),
        ('<img src="/&#9;/cdn.example/x.png">', "<img> loads //cdn.example"),
        ('<img src="javascript:go()">', "<img> loads javascript:"),
        ('<iframe src="https://evil.example/"></iframe>', "<iframe> loads https://"),
        ('<video><source src="//cdn.example/x.mp4"></video>', "<source> loads //"),
        ('<audio src="https://cdn.example/x.mp3"></audio>', "<audio> loads https://"),
        ('<embed src="https://cdn.example/x.swf">', "<embed> loads https://"),
        ('<object data="https://cdn.example/x.swf"></object>', "<object> loads https://"),
        ('<video poster="https://cdn.example/x.png"></video>', "<video> loads https://"),
        (
            '<img src="/static/a.png" srcset="/static/a.png 1x, https://cdn.example/b.png 2x">',
            "<img> loads https://cdn.example/b.png",
        ),
    ],
)
def test_the_template_check_catches_inline_code(template: str, why: str, tmp_path: Path) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(template, "utf-8")

    [problem] = inline_code_problems(root)

    assert problem.startswith("nextron/web/templates/_probe.html:1 ")
    assert why.lower() in problem.lower()


@pytest.mark.parametrize(
    ("css", "why"),
    [
        ('@import url("/static/other.css");', "@import"),
        ("@IMPORT 'x.css';", "@import"),
        ('body { background: url("https://cdn.example/x.png") }', "external url"),
        ("body { background: url(//cdn.example/x.png) }", "external url"),
        ("body { background: url(data:image/png;base64,AAAA) }", "external url"),
    ],
)
def test_the_stylesheet_check_catches_imports_and_external_urls(
    css: str, why: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "static" / "_probe.css").write_text(css, "utf-8")

    problems = inline_code_problems(root)

    assert problems and all(p.startswith("nextron/web/static/_probe.css ") for p in problems)
    assert why in problems[0]


def test_the_checks_allow_local_assets(tmp_path: Path) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(
        '<link rel="stylesheet" href="/static/app.css">'
        '<script src="/static/htmx-2.0.11.min.js" defer></script>'
        '<a href="https://example.com/" rel="noopener noreferrer">ok</a>'
        '<img src="/static/x.png" srcset="/static/x.png 1x, /static/x@2x.png 2x" alt="">'
        '<div hx-get="/queue" hx-vals=\'{"page": 2}\' hx-headers=\'{"X-A": "b"}\'></div>',
        "utf-8",
    )
    (root / "nextron" / "web" / "static" / "_probe.css").write_text(
        'body { background: url("/static/x.png") }', "utf-8"
    )

    assert inline_code_problems(root) == []


# Autoescaping ---------------------------------------------------------------------------------


def autoescape_problems(environment: Environment) -> list[str]:
    """Problems with ``environment``: autoescaping off, or not escaping a planted payload."""
    problems = []
    if environment.autoescape is not True:
        problems.append(f"autoescape is {environment.autoescape!r}, not True")
    rendered = environment.from_string("{{ value }}").render(value="<script>x</script>")
    if "<script>" in rendered:
        problems.append("a rendered value was not escaped")
    return problems


def test_the_app_renders_with_autoescaping_on(monkeypatch: pytest.MonkeyPatch) -> None:
    environments: list[Environment] = []
    real = web_app.template_environment

    def spy() -> Environment:
        environments.append(real())
        return environments[-1]

    monkeypatch.setattr(web_app, "template_environment", spy)
    Dashboard()

    [environment] = environments
    assert autoescape_problems(environment) == []


def test_the_base_layout_escapes_what_it_is_given() -> None:
    nav = [NavLink('<img src=x onerror="go()">', '/x" onmouseover="go()')]
    token = "'><script>go()</script>"

    html = template_environment().get_template("base.html").render(nav=nav, csrf_token=token)

    assert "<img src=x" not in html and 'onmouseover="go()"' not in html
    assert "&lt;img src=x onerror=&#34;go()&#34;&gt;" in html
    assert "<script>go()" not in html
    assert body_hx_headers(html) == {"X-CSRF-Token": token}


def test_the_autoescape_check_catches_an_unescaped_environment() -> None:
    problems = autoescape_problems(Environment(autoescape=False))

    assert problems == ["autoescape is False, not True", "a rendered value was not escaped"]


# Forms post through HTMX with a CSRF token ----------------------------------------------------

FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
FORM_END = re.compile(r"</form\s*>", re.IGNORECASE)
POST_METHOD = re.compile(r"""\smethod\s*=\s*(["']?)post\1(?=[\s>/])""", re.IGNORECASE)
GET_METHOD = re.compile(r"""\smethod\s*=\s*(["']?)get\1(?=[\s>/])""", re.IGNORECASE)
HX_POST = re.compile(r"\shx-post\s*=", re.IGNORECASE)
ANY_HX_POST = re.compile(r"\s(?:data-)?hx-post\s*=", re.IGNORECASE)
# a local path: "/", then anything but a second "/" or a "\" (either makes it another host)
LOCAL_ACTION = re.compile(
    r"""\saction\s*=\s*(["']?)/(?![/\\])[^"'\s>]*\1(?=[\s>/])""", re.IGNORECASE
)
CSRF_FIELD_CALL = re.compile(r"\{\{-?\s*csrf_field\(\)\s*-?\}\}")
CSRF_MENTION = re.compile(r"csrf", re.IGNORECASE)
METHOD_ATTRIBUTE = re.compile(r"\smethod\s*=", re.IGNORECASE)


def form_problems(root: Path) -> list[str]:
    """Each ``<form>`` in ``root``'s templates that breaks the forms contract: a POST form (any
    form that isn't ``method="get"``) without ``method="post"``, without ``hx-post``, or without
    a ``csrf_field()`` call before its ``</form>``; a GET form with ``hx-post``, with anything
    naming ``csrf`` before its ``</form>``, or without a local ``action``; and a form with more
    than one ``method`` (a browser takes the first, which the checks might not)."""
    problems = []
    for path in sorted((root / "nextron" / "web" / "templates").rglob("*.html")):
        label = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for start in FORM_TAG.finditer(text):
            line = text.count("\n", 0, start.start()) + 1
            end = FORM_END.search(text, start.end())
            inside = text[start.end() : end.start() if end else len(text)]
            if len(METHOD_ATTRIBUTE.findall(start[0])) > 1:
                problems.append(f"{label}:{line} <form> with more than one method")
                continue
            if GET_METHOD.search(start[0]):
                if ANY_HX_POST.search(start[0]):
                    problems.append(f"{label}:{line} GET <form> with hx-post")
                if CSRF_MENTION.search(inside):
                    problems.append(f"{label}:{line} GET <form> with a CSRF token before </form>")
                if not LOCAL_ACTION.search(start[0]):
                    problems.append(f"{label}:{line} GET <form> without a local action")
                continue
            if not POST_METHOD.search(start[0]):
                problems.append(f"{label}:{line} <form> without method=\"post\"")
            if not HX_POST.search(start[0]):
                problems.append(f"{label}:{line} <form> without hx-post")
            if not CSRF_FIELD_CALL.search(inside):
                problems.append(f"{label}:{line} <form> without csrf_field() before </form>")
    return problems


def test_every_form_posts_with_a_csrf_field() -> None:
    assert form_problems(ENGINE_ROOT) == []


@pytest.mark.parametrize(
    ("template", "why"),
    [
        ('<form hx-post="/x">{{ csrf_field() }}</form>', 'without method="post"'),
        ('<form method="got" hx-post="/x">{{ csrf_field() }}</form>', 'without method="post"'),
        ('<form method="postal" hx-post="/x">{{ csrf_field() }}</form>', 'without method="post"'),
        ('<form data-method="post" hx-post="/x">{{ csrf_field() }}</form>', "without method="),
        # a native submission sends Origin: null under no-referrer, which the guard refuses
        ('<form method="post">{{ csrf_field() }}</form>', "without hx-post"),
        ('<form method="post" data-hx-post="/x">{{ csrf_field() }}</form>', "without hx-post"),
        ('<form method="post" hx-post="/x"></form>', "without csrf_field()"),
        ('<form method="post" hx-post="/x"></form>{{ csrf_field() }}', "without csrf_field()"),
        ('<form method="post" hx-post="/x">{{ csrf_token }}</form>', "without csrf_field()"),
        ('<FORM METHOD="POST" HX-POST="/x">', "without csrf_field()"),
        (
            '<form method="post" method="get" action="/x" hx-post="/x">{{ csrf_field() }}</form>',
            "with more than one method",
        ),
        ('<form method="get" method="post" action="/x"></form>', "with more than one method"),
    ],
)
def test_the_form_check_catches_a_form_without_post_hx_post_or_csrf_field(
    template: str, why: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(template, "utf-8")

    [problem] = form_problems(root)

    assert problem.startswith("nextron/web/templates/_probe.html:1 ")
    assert why in problem


@pytest.mark.parametrize(
    "template",
    [
        '<form method="post" hx-post="/x">{{ csrf_field() }}<button>Go</button></form>',
        "<form method='POST' hx-post='/x'>\n{{- csrf_field() -}}\n</form>",
        '<form class="f" method=post hx-post="/x">{{csrf_field()}}</form>',
    ],
)
def test_the_form_check_allows_an_htmx_post_form_with_a_csrf_field(
    template: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(template, "utf-8")

    assert form_problems(root) == []


@pytest.mark.parametrize(
    ("template", "why"),
    [
        ('<form method="get" action="/x" hx-post="/x"></form>', "GET <form> with hx-post"),
        ('<form method="get" action="/x" data-hx-post="/x"></form>', "GET <form> with hx-post"),
        ('<form method="get" action="/x">{{ csrf_field() }}</form>', "with a CSRF token"),
        (
            '<form method="get" action="/x"><input name="t" value="{{ csrf_token }}"></form>',
            "with a CSRF token",
        ),
        ('<form method="get" action="/x">\n{{ csrf_field() }}', "with a CSRF token"),  # no end
        ('<form method="get"></form>', "without a local action"),
        ('<form method="get" action=""></form>', "without a local action"),
        ('<form method="get" action="x"></form>', "without a local action"),
        ('<form method="get" action="https://evil.example/x"></form>', "without a local action"),
        ('<form method="get" action="//evil.example/x"></form>', "without a local action"),
        ('<form method="get" action="/\\evil.example/x"></form>', "without a local action"),
        ('<form method="get" action="{{ next }}"></form>', "without a local action"),
        ('<form method="get" data-action="/x"></form>', "without a local action"),
    ],
)
def test_the_form_check_catches_a_get_form_with_hx_post_a_token_or_no_local_action(
    template: str, why: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(template, "utf-8")

    [problem] = form_problems(root)

    assert problem.startswith("nextron/web/templates/_probe.html:1 GET <form> ")
    assert why in problem


@pytest.mark.parametrize(
    "template",
    [
        '<form method="get" action="/leads"><input name="q"><button>Filter</button></form>',
        "<form class='f' method=GET action=/leads role='search'>\n</form>",
        '<form method="get" action="/leads?x=1" role="search"></form>',
    ],
)
def test_the_form_check_allows_a_get_form_to_a_local_path_without_a_token(
    template: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(template, "utf-8")

    assert form_problems(root) == []


# The safe allowlist ----------------------------------------------------------------------------

SAFE_FILTER = re.compile(r"\|\s*safe\b", re.IGNORECASE)  # \s* spans lines in the whole text
SAFE_BLOCK = re.compile(r"\{%[-+]?\s*filter\s+safe\b", re.IGNORECASE)
AUTOESCAPE_OFF = re.compile(r"\{%[-+]?\s*autoescape\b(?!\s+true\s*[-+]?%\})")  # not literally true
JINJA_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
SAFE_NAME = re.compile(r"""(['"])safe\1""")  # the filter named as a string, as in map('safe')
MARKUP = "Markup"
HTML_DUNDER = "__html__"


def template_safe_offsets(text: str) -> list[int]:
    """Where ``text``, read as a template, reaches the ``safe`` filter or turns autoescaping
    off: the offset each match starts at."""
    offsets = [
        match.start()
        for pattern in (SAFE_FILTER, SAFE_BLOCK, AUTOESCAPE_OFF)
        for match in pattern.finditer(text)
    ]
    for tag in JINJA_TAG.finditer(text):
        offsets += [tag.start() + match.start() for match in SAFE_NAME.finditer(tag[0])]
    return offsets


def read_allowlist(path: Path) -> Counter[str]:
    """The allowlist's entries, ``<posix path>: <stripped source line>``, each counted."""
    entries: Counter[str] = Counter()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries[line] += 1
    return entries


def _markup_lines(tree: ast.AST) -> set[int]:
    """Lines that name ``Markup`` (also through an import alias), name ``__html__`` or hold a
    string the template scan flags."""
    aliases = {MARKUP}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] == MARKUP and alias.asname:
                    aliases.add(alias.asname)
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(
                alias.name.rsplit(".", 1)[-1] in aliases or alias.asname in aliases
                for alias in node.names
            ):
                lines.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id in aliases:
            lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == MARKUP:
            lines.add(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == HTML_DUNDER:
            lines.add(node.lineno)
        elif (isinstance(node, ast.Name) and node.id == HTML_DUNDER) or (
            isinstance(node, ast.Attribute) and node.attr == HTML_DUNDER
        ):
            lines.add(node.lineno)  # an assignment target is a Name or an Attribute too
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and template_safe_offsets(node.value)
        ):
            lines.add(node.lineno)
    return lines


def safe_uses(root: Path) -> Counter[str]:
    """Every use the allowlist must cover under ``root``, as ``<posix path>: <stripped line>``."""
    uses: Counter[str] = Counter()
    web = root / "nextron" / "web"
    for path in sorted((web / "templates").rglob("*.html")):
        label = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        source = text.splitlines()
        numbers = {text.count("\n", 0, offset) + 1 for offset in template_safe_offsets(text)}
        for number in sorted(numbers):
            uses[f"{label}: {source[number - 1].strip()}"] += 1
    for path in sorted(web.rglob("*.py")):
        label = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        source = text.splitlines()
        for number in sorted(_markup_lines(ast.parse(text, filename=str(path)))):
            uses[f"{label}: {source[number - 1].strip()}"] += 1
    return uses


def safe_allowlist_problems(root: Path, allowlist: Path) -> list[str]:
    """Uses the allowlist doesn't cover, then allowlist entries that match nothing."""
    uses, allowed = safe_uses(root), read_allowlist(allowlist)
    problems = [f"not allowlisted: {use}" for use in sorted((uses - allowed).elements())]
    problems += [f"stale allowlist entry: {entry}" for entry in sorted((allowed - uses).elements())]
    return problems


def test_the_safe_filter_and_markup_appear_only_where_allowlisted() -> None:
    assert safe_allowlist_problems(ENGINE_ROOT, SAFE_ALLOWLIST) == []


def test_the_allowlist_covers_only_the_templating_helpers() -> None:
    assert {entry.split(":", 1)[0] for entry in read_allowlist(SAFE_ALLOWLIST)} == {
        "nextron/web/templating.py"
    }


@pytest.mark.parametrize(
    ("name", "text", "use"),
    [
        ("_probe.html", "<p>{{ text|safe }}</p>", "<p>{{ text|safe }}</p>"),
        ("_probe.html", "<p>{{ text | safe }}</p>", "<p>{{ text | safe }}</p>"),
        ("_probe.html", "<p>{{ text|e|safe }}</p>", "<p>{{ text|e|safe }}</p>"),
        ("_probe.html", "{% filter safe %}{{ text }}{% endfilter %}", "{% filter safe %}"),
        ("_probe.html", "{%- filter upper|safe %}x{% endfilter %}", "{%- filter upper|safe %}"),
        ("_probe.html", "{% autoescape false %}{{ x }}{% endautoescape %}", "{% autoescape"),
        ("_probe.html", "{% autoescape False %}{{ x }}{% endautoescape %}", "{% autoescape"),
        ("_probe.html", "{% autoescape 0 %}{{ x }}{% endautoescape %}", "{% autoescape 0 %}"),
        ("_probe.html", "{% autoescape none %}{{ x }}{% endautoescape %}", "{% autoescape none"),
        ("_probe.html", "{% autoescape some_flag %}{{ x }}{% endautoescape %}", "some_flag"),
        ("_probe.html", "{%- autoescape True -%}{{ x }}{% endautoescape %}", "{%- autoescape"),
        ("_probe.html", "<p>{{ x |\n safe }}</p>", "<p>{{ x |"),
        ("_probe.html", "<p>\n{{ x\n  | safe }}</p>", "| safe }}</p>"),
        ("_probe.html", "{{ [x]|map('safe')|join }}", "{{ [x]|map('safe')|join }}"),
        ("_probe.html", '{{ [x]|map("safe")|join }}', '{{ [x]|map("safe")|join }}'),
        ("_probe.html", "{{ items\n|map(\n'safe') }}", "'safe') }}"),
        ("_probe.py", "from markupsafe import Markup", "from markupsafe import Markup"),
        ("_probe.py", "from markupsafe import Markup as M", "from markupsafe import Markup as M"),
        ("_probe.py", "import markupsafe\nx = markupsafe.Markup('<b>')", "x = markupsafe.Markup"),
        ("_probe.py", "from jinja2.utils import markupsafe as m\nx = m.Markup", "x = m.Markup"),
        ("_probe.py", "class Html:\n    def __html__(self):\n        return ''", "def __html__"),
        ("_probe.py", "T = '{{ x|safe }}'", "T = '{{ x|safe }}'"),
        ("_probe.py", 'T = """<p>\n{{ x | safe }}</p>"""', 'T = """<p>'),
        ("_probe.py", "T = '{{ x |\\n safe }}'", "T = '{{ x |"),
        ("_probe.py", "T = '{% autoescape false %}'", "T = '{% autoescape false %}'"),
        ("_probe.py", "T = '{% filter safe %}x{% endfilter %}'", "T = '{% filter safe %}"),
        ("_probe.py", "T = \"{{ xs|map('safe')|join }}\"", "T = \"{{ xs|map('safe')"),
        ("_probe.py", "class Html:\n    __html__ = str", "__html__ = str"),
        ("_probe.py", "def mark(x):\n    x.__html__ = str", "x.__html__ = str"),
        ("_probe.py", "def show(x):\n    return x.__html__()", "return x.__html__()"),
    ],
)
def test_the_allowlist_check_catches_a_use_outside_the_allowlist(
    name: str, text: str, use: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    folder = "templates" if name.endswith(".html") else ""
    probe = root / "nextron" / "web" / folder / name
    probe.write_text(text, "utf-8")

    problems = safe_allowlist_problems(root, SAFE_ALLOWLIST)

    assert problems, text
    assert all(problem.startswith("not allowlisted: nextron/web/") for problem in problems)
    assert any(use in problem for problem in problems)


@pytest.mark.parametrize(
    "text",
    [
        "{% autoescape true %}{{ x }}{% endautoescape %}",
        "{%- autoescape true -%}{{ x }}{%- endautoescape -%}",
        '<p class="safe">safe</p>',
        "{{ x|plain }} is safe",
        "{{ x|safest }}",
        '{{ "safe word"|upper }}',
    ],
)
def test_the_allowlist_check_allows_other_uses_of_the_word_safe(
    text: str, tmp_path: Path
) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(text, "utf-8")

    assert safe_allowlist_problems(root, SAFE_ALLOWLIST) == []


def test_an_alias_of_markup_is_caught_where_it_is_used(tmp_path: Path) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "_probe.py").write_text(
        "from markupsafe import Markup as M\n\nX = 1\nY = M('<b>')\n", "utf-8"
    )

    assert safe_allowlist_problems(root, SAFE_ALLOWLIST) == [
        "not allowlisted: nextron/web/_probe.py: Y = M('<b>')",
        "not allowlisted: nextron/web/_probe.py: from markupsafe import Markup as M",
    ]


def test_an_allowlisted_use_passes_and_a_second_copy_does_not(tmp_path: Path) -> None:
    root = web_copy(tmp_path)
    (root / "nextron" / "web" / "templates" / "_probe.html").write_text(
        "{{ a|safe }}\n{{ a|safe }}\n", "utf-8"
    )
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text(
        SAFE_ALLOWLIST.read_text(encoding="utf-8")
        + "# a comment line\n\nnextron/web/templates/_probe.html: {{ a|safe }}\n",
        "utf-8",
    )

    assert safe_allowlist_problems(root, allowlist) == [
        "not allowlisted: nextron/web/templates/_probe.html: {{ a|safe }}"
    ]


def test_a_stale_allowlist_entry_fails(tmp_path: Path) -> None:
    root = web_copy(tmp_path)
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text(
        SAFE_ALLOWLIST.read_text(encoding="utf-8")
        + "nextron/web/templates/home.html: {{ gone|safe }}\n",
        "utf-8",
    )

    assert safe_allowlist_problems(root, allowlist) == [
        "stale allowlist entry: nextron/web/templates/home.html: {{ gone|safe }}"
    ]
