"""The dashboard's FastAPI application factory (AD-13, FR-56, FR-58 seed).

Routes, each a sync ``def`` declaring ``@personal_data`` (``nextron.web.routing``); every GET
runs in a read-only unit of work and every POST is CSRF-checked (``dashboard_route_class``):

- ``GET /enter`` (``enter``): only a navigation the browser started itself is accepted: a
  ``Sec-Fetch-Site`` header other than exactly one ``none`` (a link from another website, or
  from a server on another ``127.0.0.1`` port) gets 401 with the locked page before the code is
  looked at, logged with ``reason=cross_site``, and the pending code stays. Otherwise exactly
  one ``t`` that ``DashboardEntry.consume_code`` accepts starts a session and redirects (303)
  to ``/`` with the ``nextron_session`` cookie (``HttpOnly; Path=/; SameSite=Strict``).
  Anything else gets 401 with the locked page and no session. Both are
  ``Cache-Control: no-store``, and both are logged (``dashboard_session_opened``, or
  ``dashboard_entry_refused`` with ``reason``), never with the code or the token.
- ``GET /`` (``home``): the base layout.
- ``GET /health`` (``health``): the environment, the engine version and the process uptime in
  whole seconds, and nothing else.
- ``GET /static/{name}`` (``static``): the vendored HTMX build and ``app.css``, read once when
  the app is built; any other name is 404.

``Guard`` (``nextron.web.guard``) checks ``Host``, the session, and ``Origin`` and
``Sec-Fetch-Site`` on unsafe methods in front of every route, and stamps the security headers on
every response. Sessions live in a ``SessionStore`` made here, so a restart drops them all. A
``SecurityCheckFailed`` from ``require_csrf`` is answered with 403 by
``nextron.web.csrf.security_check_failed``. The interactive API docs and the OpenAPI schema are
off, because the dashboard serves no third-party scripts.

Pages render with ``app.state.templates`` (``template_environment()``: autoescaping on, plus the
``nextron.web.templating`` helpers). ``page()`` passes ``nav`` and the session's ``csrf_token``,
which ``base.html`` puts in ``<body hx-headers>`` for HTMX and ``csrf_field()`` puts in forms, and
answers with ``Cache-Control: no-store``, whatever the route declared, because the page carries
that token.

The base layout's navigation lists, in ``NAV_SECTIONS`` order, each section whose page route
(a GET route with that name) exists; a later story adds the route and its link appears.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.routing import BaseRoute

from nextron.app.ops.api import DashboardEntry
from nextron.domain.open_code import CodeCheck
from nextron.web import templating
from nextron.web.csrf import CSRF_SCOPE_KEY, SecurityCheckFailed, security_check_failed
from nextron.web.guard import Guard
from nextron.web.routing import (
    Units,
    dashboard_route_class,
    personal_data,
    require_declared,
    require_route_rules,
)
from nextron.web.sessions import SESSION_COOKIE, SessionStore

WEB_DIR: Final = Path(__file__).resolve().parent
TEMPLATES_DIR: Final = WEB_DIR / "templates"
STATIC_DIR: Final = WEB_DIR / "static"
STATIC_TYPES: Final = {
    "htmx-2.0.11.min.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
# (route name, label), in navigation order
NAV_SECTIONS: Final = (
    ("queue_page", "Queue"),
    ("signal_inbox_page", "Signal Inbox"),
    ("pipeline_page", "Leads/Pipeline"),
    ("funnel_page", "Funnel"),
    ("settings_page", "Settings"),
    ("health_page", "Health"),
)
NO_STORE: Final = {"Cache-Control": "no-store"}
BROWSER_STARTED: Final = "none"  # Sec-Fetch-Site of a navigation the user started
CROSS_SITE: Final = "cross_site"

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NavLink:
    label: str
    href: str


def template_environment() -> Environment:
    """The Jinja2 environment of the dashboard's templates: autoescaping on, plus the
    ``plain``, ``safe_link`` and ``csrf_field`` helpers (``nextron.web.templating``)."""
    environment = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)
    return templating.install(environment)


def _route_path(routes: list[BaseRoute], name: str) -> str | None:
    for route in routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "name", None) == name and "GET" in methods:
            return str(getattr(route, "path"))
    return None


def nav_links(app: FastAPI) -> list[NavLink]:
    """The ``NAV_SECTIONS`` whose page route exists in ``app``, in order."""
    links = []
    for name, label in NAV_SECTIONS:
        path = _route_path(app.routes, name)
        if path is not None:
            links.append(NavLink(label, path))
    return links


def create_app(
    *,
    env: str,
    version: str,
    port: int,
    entry: DashboardEntry,
    units: Units,
    monotonic: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Build the dashboard app for ``127.0.0.1:<port>``; its GET handlers read through
    ``units.open(read_only=True)``. ``monotonic`` is injectable so tests control the uptime.
    Raises ``RuntimeError`` when a route doesn't declare ``personal_data`` or breaks the route
    rules."""
    started = monotonic()
    templates = template_environment()
    sessions = SessionStore()
    assets = {name: (STATIC_DIR / name).read_bytes() for name in STATIC_TYPES}
    locked_page = templates.get_template("locked.html").render().encode("utf-8")
    app = FastAPI(
        title="Nextron Outreach Engine",
        version=version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.router.route_class = dashboard_route_class(units)
    app.add_exception_handler(SecurityCheckFailed, security_check_failed)
    app.state.templates = templates

    def page(request: Request, template: str) -> HTMLResponse:
        body = templates.get_template(template).render(
            nav=nav_links(request.app), csrf_token=request.scope.get(CSRF_SCOPE_KEY)
        )
        return HTMLResponse(body, headers=NO_STORE)  # it carries the session's CSRF token

    def locked() -> HTMLResponse:
        return HTMLResponse(locked_page, status_code=401, headers=NO_STORE)

    @app.get("/enter", name="enter")
    @personal_data(False)
    def enter(request: Request) -> Response:
        fetch_sites = request.headers.getlist("sec-fetch-site")
        if fetch_sites and fetch_sites != [BROWSER_STARTED]:
            # Not a navigation the browser started: refused before the code is looked at.
            log.warning("dashboard_entry_refused", extra={"reason": CROSS_SITE})
            return locked()
        codes = request.query_params.getlist("t")
        # Not exactly one code: refused without touching the pending code.
        result = entry.consume_code(codes[0]) if len(codes) == 1 else CodeCheck.MISMATCH
        if result is not CodeCheck.ACCEPTED:
            log.warning("dashboard_entry_refused", extra={"reason": result})
            return locked()
        token = sessions.create()
        log.info("dashboard_session_opened")
        response = RedirectResponse("/", status_code=303, headers=NO_STORE)
        response.headers.append(
            "set-cookie", f"{SESSION_COOKIE}={token}; HttpOnly; Path=/; SameSite=Strict"
        )
        return response

    @app.get("/", name="home")
    @personal_data(False)
    def home(request: Request) -> HTMLResponse:
        return page(request, "home.html")

    @app.get("/health", name="health")
    @personal_data(False)
    def health() -> dict[str, str | int]:
        return {"env": env, "version": version, "uptime_s": int(monotonic() - started)}

    @app.get("/static/{name}", name="static")
    @personal_data(False)
    def static(name: str) -> Response:
        content = assets.get(name)
        if content is None:
            return PlainTextResponse("Not found", status_code=404, headers=NO_STORE)
        return Response(content, media_type=STATIC_TYPES[name])

    require_declared(app)
    require_route_rules(app)
    app.add_middleware(Guard, port=port, sessions=sessions, locked_page=locked_page)
    return app
