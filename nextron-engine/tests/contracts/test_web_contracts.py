"""Dashboard contracts (AD-13), each with a live proof that the check catches a violation.

- **No ``async def`` route handler.** At run time over every route of the built app, and in the
  source (AST) of every module under ``nextron/web``: an ``async def`` decorated with a route
  decorator (``@app.get``, ``@router.post``, ``@app.api_route`` and so on) or passed to
  ``add_api_route``, ``add_route``, ``add_websocket_route`` or ``add_api_websocket_route``
  fails. An ASGI ``async def __call__`` and other async helpers are fine.
- **Every route declares ``personal_data``.** ``undeclared_routes`` of the built app is empty,
  and ``create_app`` refuses to build an app with an undeclared route.
- **No inline script or style, only self-hosted assets.** No template under
  ``nextron/web/templates`` has an inline ``<script>``, a ``<style>``, a ``style=``, ``on*=``,
  ``hx-on*`` or ``data-hx-on*`` attribute, a ``js:`` or ``javascript:`` value in ``hx-vals``,
  ``hx-vars`` or ``hx-headers`` (or their ``data-`` forms), or a ``javascript:``,
  ``vbscript:`` or ``data:`` URL, even one split by tab, newline or CR characters (browsers
  drop those). Every ``src``, ``srcset`` candidate, ``data`` and ``poster`` URL on any tag, and
  every ``<link href>``, is a local path. No stylesheet under ``nextron/web/static`` has an
  ``@import`` or an external ``url()``.
- **Autoescaping is on** in the environment the app renders with.
"""

import ast
import inspect
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from jinja2 import Environment

from nextron.web import app as web_app
from nextron.web.app import NavLink, template_environment
from nextron.web.routing import personal_data, require_declared, undeclared_routes
from tests.contracts.support import ENGINE_ROOT, PACKAGE, copy_with_probe
from tests.dashboard import Dashboard

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

    assert [route.path for route in app.routes] == ["/enter", "/", "/health", "/static/{name}"]
    assert async_route_handlers(app) == []


def test_no_route_handler_under_nextron_web_is_async_def() -> None:
    assert async_handler_problems(ENGINE_ROOT) == []


def test_the_run_time_check_catches_an_async_handler() -> None:
    app = Dashboard().app

    @personal_data(False)
    async def slow() -> PlainTextResponse:
        return PlainTextResponse("slow")

    app.add_api_route("/slow", slow, methods=["GET"])

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

    app.add_api_route("/undeclared", undeclared, methods=["GET", "POST"])

    assert undeclared_routes(app) == ["GET POST /undeclared"]
    with pytest.raises(RuntimeError, match="undeclared: GET POST /undeclared"):
        require_declared(app)


def test_create_app_refuses_undeclared_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    def declares_nothing(shows: bool):  # type: ignore[no-untyped-def]
        return lambda endpoint: endpoint

    monkeypatch.setattr(web_app, "personal_data", declares_nothing)

    with pytest.raises(RuntimeError) as caught:
        Dashboard()

    for route in ("GET /enter", "GET /", "GET /health", "GET /static/{name}"):
        assert route in str(caught.value)


@pytest.mark.parametrize("value", [None, 0, 1, "False"])
def test_personal_data_takes_a_bool(value: object) -> None:
    with pytest.raises(TypeError):
        personal_data(value)  # type: ignore[arg-type]


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

    assert templates == ["base.html", "home.html", "locked.html"]
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

    html = template_environment().get_template("base.html").render(nav=nav)

    assert "<img src=x" not in html and 'onmouseover="go()"' not in html
    assert "&lt;img src=x onerror=&#34;go()&#34;&gt;" in html


def test_the_autoescape_check_catches_an_unescaped_environment() -> None:
    problems = autoescape_problems(Environment(autoescape=False))

    assert problems == ["autoescape is False, not True", "a rendered value was not escaped"]
