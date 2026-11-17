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
- ``GET /settings`` (``settings_page``) and ``POST /settings`` (``settings_save``): the Settings
  page and its save, both ``@personal_data(True)``; see ``nextron.web.settings_page``. The POST
  saves through ``setup`` (``Setup.save``) in its own unit of work.
- ``GET /credentials`` (``credentials_page``, ``@personal_data(True)``, because its Google
  section shows each grant's account address), ``POST /credentials/save`` (``credential_save``)
  and ``POST /credentials/remove`` (``credential_remove``): the write-only Credentials page and
  its per-row save and two-step remove, the POSTs ``@personal_data(False)``; see
  ``nextron.web.credentials_page``. They go through ``credentials`` (``Credentials``), which
  touches no database, so the POSTs open no unit of work.
- ``POST /credentials/google/client`` (``google_client_import``), ``POST
  /credentials/google/grant`` (``google_grant_start``), ``GET /credentials/google/grant/{grant}``
  (``google_grant_row``) and ``POST /credentials/google/remove`` (``google_grant_remove``): the
  Google section's client import, Grant, polled row and two-step remove; see
  ``nextron.web.google_page``. They go through ``google`` (``GoogleAccess``), which touches no
  database. Only ``google_grant_start`` starts a consent flow; Google's redirect is served by
  the flow's own listener, never by a dashboard route.
- ``GET /notifications`` (``notifications_area``) and ``POST /notifications/dismiss``
  (``notification_dismiss``), both ``@personal_data(True)``: the notification area (UX-DR5),
  which the base layout loads once per page view (``hx-trigger="load"``; it isn't polled), and
  its Dismiss button. The area (``notifications.html``) starts with "Engine health: <n> error
  needs attention" (or "<n> errors need attention"), linked to the health panel, when
  ``health.issue_count()`` is above 0. It is empty when nothing is undismissed and no error
  needs attention; otherwise it shows "Notifications (<count>)", the newest ``AREA_LIMIT`` (5),
  each with its text (``|plain``), its time as ``YYYY-MM-DD HH:MM UTC`` and a Dismiss form, and
  "and <n> more" when there are more. The POST's fields, apart from ``csrf_token``, must be
  exactly one ``notification`` holding a ULID (anything else is ``MalformedForm``, 400); it
  dismisses through ``notify`` (``Notify.dismiss``) in its own unit of work, then renders the
  area again in a read-only one.
- ``GET /quotas`` (``quotas_page``, ``@personal_data(False)``): the Quotas and cost page
  (UX-DR28), rendered from ``budget.status()`` (the ``ops`` service's ``Budget``) through
  ``nextron.web.quotas_page``, and linked from the Settings page; see that module.
- ``GET /health/panel`` (``health_page``, the navigation's Health section), ``GET
  /health/panel/live`` (``health_panel``, the panel alone, which HTMX re-fetches every 30 s) and
  ``POST /health/jobs/dismiss`` (``health_job_dismiss``), all ``@personal_data(True)``: the
  health panel (FR-58, UX-DR29), rendered from ``health.report()`` (the ``ops`` service's
  ``EngineHealth``) through ``nextron.web.health_page``; see that module. The POST's fields,
  apart from ``csrf_token``, must be exactly one ``job`` holding a ULID (anything else is
  ``MalformedForm``, 400); it dismisses the dead job through ``queue`` (``JobQueue.dismiss``) in
  its own unit of work, then renders the panel again in a read-only one. (``GET /health`` keeps
  its JSON.)

``Guard`` (``nextron.web.guard``) checks ``Host``, the session, and ``Origin`` and
``Sec-Fetch-Site`` on unsafe methods in front of every route, and stamps the security headers on
every response. Sessions live in a ``SessionStore`` made here, so a restart drops them all. A
``SecurityCheckFailed`` from ``require_csrf`` is answered with 403 by
``nextron.web.csrf.security_check_failed``, and a ``MalformedForm`` with 400 by
``nextron.web.forms.malformed_form``. The interactive API docs and the OpenAPI schema are off,
because the dashboard serves no third-party scripts.

Pages render with ``app.state.templates`` (``template_environment()``: autoescaping on, plus the
``nextron.web.templating`` helpers). ``page(request, template, **context)`` passes ``context``,
``nav`` and the session's ``csrf_token``, which ``base.html`` puts in ``<body hx-headers>`` for
HTMX and ``csrf_field()`` puts in forms, and answers with ``Cache-Control: no-store``, whatever
the route declared, because the page carries that token.

**Error notices.** ``base.html``'s ``<meta name="htmx-config">`` sets HTMX's
``responseHandling``: a 204 swaps nothing, a 2xx or 3xx swaps as usual, and any 4xx or 5xx body
is put, as text (``swapOverride: textContent``, never parsed as HTML), in
``#request-error`` inside ``section#notifications``. So a refused action (a 403 CSRF refusal,
a 400 malformed form, a 500) shows its plain-text answer, and a request without a session gets
the guard's plain-text 401 (``nextron.web.guard``). A request that gets no answer at all (the
engine is down) shows nothing, and a notice stays until the page reloads.

The base layout's navigation lists, in ``NAV_SECTIONS`` order, each section whose page route
(a GET route with that name) exists; a later story adds the route and its link appears.

``create_app(..., lifespan=None)`` hands ``lifespan`` to ``FastAPI``; ``Guard`` passes lifespan
scopes through untouched, so the job runner, the scheduler and the heartbeat writer that
``nextron.main`` wires there start and stop with the server.
"""

import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from starlette.routing import BaseRoute

from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Budget, DashboardEntry, EngineHealth
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import (
    Credentials,
    CredentialStoreFailed,
    GoogleAccess,
    GrantStatus,
    Setup,
)
from nextron.domain.credentials import InvalidCredentialValue
from nextron.domain.google_oauth import ClientNotImported, InvalidClientFile
from nextron.domain.ids import is_ulid
from nextron.domain.open_code import CodeCheck
from nextron.domain.settings_schema import InvalidSettings
from nextron.web import credentials_page as credential_rows
from nextron.web import google_page as google_rows
from nextron.web import health_page as health_rows
from nextron.web import quotas_page as quota_rows
from nextron.web import templating
from nextron.web.csrf import CSRF_SCOPE_KEY, SecurityCheckFailed, security_check_failed
from nextron.web.forms import (
    REPEATED_FIELD,
    WRONG_FIELDS,
    FormFields,
    MalformedForm,
    form_fields,
    malformed_form,
)
from nextron.web.guard import Guard
from nextron.web.routing import (
    Units,
    dashboard_route_class,
    personal_data,
    require_declared,
    require_route_rules,
)
from nextron.web.sessions import SESSION_COOKIE, SessionStore
from nextron.web.settings_page import (
    FORM_TEMPLATE,
    PAGE_TEMPLATE,
    form_context,
    shown_values,
    stored_problems,
    submitted_texts,
)

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
NOTIFICATIONS_TEMPLATE: Final = "notifications.html"
AREA_LIMIT: Final = 5  # the newest notifications the area lists
NOTIFICATION_FIELD: Final = "notification"
CSRF_FIELD_NAME: Final = "csrf_token"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M UTC"

# An async context manager factory taking the app: what FastAPI(lifespan=...) accepts.
type Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NavLink:
    label: str
    href: str


@dataclass(frozen=True, slots=True)
class NotificationItem:
    """One notification as the area shows it: its id, its text and its time in UTC."""

    id: str
    text: str
    time: str


def health_count_text(errors: int) -> str | None:
    """The area's engine health line for ``errors`` errors, or ``None`` when there are none."""
    if errors <= 0:
        return None
    if errors == 1:
        return "Engine health: 1 error needs attention"
    return f"Engine health: {errors:,} errors need attention"


def area_context(
    found: notify_queries.OpenNotifications, *, errors: int = 0
) -> dict[str, object]:
    """What ``notifications.html`` renders: the engine health line, the count, the listed items
    and how many more."""
    items = [
        NotificationItem(row.id, row.text, f"{row.created_at:{TIME_FORMAT}}")
        for row in found.newest
    ]
    return {
        "health_text": health_count_text(errors),
        "count": found.count,
        "items": items,
        "more": max(found.count - len(items), 0),
    }


def submitted_notification(fields: FormFields) -> str:
    """The one ``notification`` ULID a Dismiss form posts; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD_NAME} != {NOTIFICATION_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[NOTIFICATION_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    if not is_ulid(values[0]):
        raise MalformedForm(WRONG_FIELDS)
    return values[0]


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
    setup: Setup,
    credentials: Credentials,
    google: GoogleAccess,
    notify: Notify,
    budget: Budget,
    health: EngineHealth,
    queue: JobQueue,
    units: Units,
    monotonic: Callable[[], float] = time.monotonic,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    """Build the dashboard app for ``127.0.0.1:<port>``; its GET handlers read through
    ``units.open(read_only=True)`` and its POST handlers that write the database open
    ``units.open()``. ``setup`` stores the settings, ``credentials`` the external credentials
    and ``google`` the Google client and grants (both in Credential Manager, not the database);
    ``notify`` dismisses notifications; ``budget`` gives the Quotas and cost page its figures;
    ``health`` gives the health panel its report and the notification area its error count;
    ``queue`` dismisses a dead job.
    ``monotonic`` is injectable so tests control the uptime. ``lifespan`` goes to
    ``FastAPI(lifespan=...)``: ``nextron.main`` passes the one that starts and stops the job
    runner, the scheduler and the heartbeat writer (``engine_lifespan``).
    Raises ``RuntimeError`` when a route doesn't declare ``personal_data`` or breaks the route
    rules."""
    started = monotonic()
    engine_health = health  # the name health is the GET /health handler's below
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
        lifespan=lifespan,
    )
    app.router.route_class = dashboard_route_class(units)
    app.add_exception_handler(SecurityCheckFailed, security_check_failed)
    app.add_exception_handler(MalformedForm, malformed_form)
    app.state.templates = templates

    def page(request: Request, template: str, **context: object) -> HTMLResponse:
        body = templates.get_template(template).render(
            **context, nav=nav_links(request.app), csrf_token=request.scope.get(CSRF_SCOPE_KEY)
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

    @app.get("/settings", name="settings_page")
    @personal_data(True)
    def settings_page(request: Request) -> HTMLResponse:
        stored = setup_queries.stored_texts()
        context = form_context(shown_values(stored), problems=stored_problems(stored))
        return page(request, PAGE_TEMPLATE, **context)

    @app.post("/settings", name="settings_save")
    @personal_data(True)
    def settings_save(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        texts = submitted_texts(fields)
        try:
            with units.open():
                stored = setup.save(texts)
        except InvalidSettings as refused:
            # 200, so HTMX swaps the form in; nothing was saved
            context = form_context(texts, problems=refused.problems, refused=True)
            return page(request, FORM_TEMPLATE, **context)
        return page(request, FORM_TEMPLATE, **form_context(shown_values(stored), saved=True))

    def row_answer(request: Request, row: credential_rows.CredentialRow) -> HTMLResponse:
        return page(request, credential_rows.ROW_TEMPLATE, row=row)

    def store_failed_row(request: Request, key: str) -> HTMLResponse:
        # 200, so HTMX swaps the row in, showing what Credential Manager holds now
        notice = credential_rows.store_failed()
        return row_answer(
            request, credential_rows.credential_row(credentials.status(key), notice=notice)
        )

    @app.get("/credentials", name="credentials_page")
    @personal_data(True)  # the Google section shows each grant's account address
    def credentials_page(request: Request) -> HTMLResponse:
        rows = credential_rows.page_rows(credentials.statuses())
        client = google.client_status()
        return page(
            request,
            credential_rows.PAGE_TEMPLATE,
            rows=rows,
            env=env,
            client=google_rows.client_row(client),
            google_rows=google_rows.grant_rows(google.grant_statuses(), client),
        )

    @app.post("/credentials/save", name="credential_save")
    @personal_data(False)
    def credential_save(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        key, text = credential_rows.submitted_save(fields)
        try:
            status = credentials.save(key, text)
        except InvalidCredentialValue as refused:
            # 200, so HTMX swaps the row in; nothing was written, and the field is empty again
            notice = credential_rows.refused(refused.reason)
            return row_answer(
                request, credential_rows.credential_row(credentials.status(key), notice=notice)
            )
        except CredentialStoreFailed:
            return store_failed_row(request, key)
        return row_answer(
            request, credential_rows.credential_row(status, notice=credential_rows.saved())
        )

    @app.post("/credentials/remove", name="credential_remove")
    @personal_data(False)
    def credential_remove(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        key, confirmed = credential_rows.submitted_remove(fields)
        if not confirmed:  # the first click only asks; nothing is deleted
            row = credential_rows.credential_row(credentials.status(key), confirming=True)
            return row_answer(request, row)
        try:
            status = credentials.remove(key)
        except CredentialStoreFailed:
            return store_failed_row(request, key)
        return row_answer(
            request, credential_rows.credential_row(status, notice=credential_rows.removed())
        )

    def client_answer(request: Request, row: google_rows.ClientRow) -> HTMLResponse:
        return page(request, google_rows.CLIENT_ROW_TEMPLATE, client=row)

    def client_refused(request: Request, notice: credential_rows.Notice) -> HTMLResponse:
        # 200, so HTMX swaps the row in; it shows what Credential Manager holds now
        return client_answer(request, google_rows.client_row(google.client_status(), notice=notice))

    def grant_answer(
        request: Request,
        grant: str,
        *,
        status: GrantStatus | None = None,
        notice: credential_rows.Notice | None = None,
        confirming: bool = False,
    ) -> HTMLResponse:
        current = status if status is not None else google.grant_status(grant)
        row = google_rows.grant_row(
            current, google.client_status(), notice=notice, confirming=confirming
        )
        return page(request, google_rows.GRANT_ROW_TEMPLATE, row=row)

    @app.post("/credentials/google/client", name="google_client_import")
    @personal_data(False)
    def google_client_import(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        path_text = google_rows.submitted_path(fields)
        try:
            result = google.import_client(path_text)
        except InvalidClientFile as refused:  # nothing stored; the file is where it was
            return client_refused(request, google_rows.not_imported(refused.reason))
        except CredentialStoreFailed:
            return client_refused(request, google_rows.store_failed())
        notice = google_rows.imported(result.file_deleted)
        return client_answer(
            request, google_rows.client_row(result.status, notice=notice, reload_hint=True)
        )

    @app.post("/credentials/google/grant", name="google_grant_start")
    @personal_data(True)
    def google_grant_start(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        grant = google_rows.submitted_grant(fields)
        try:
            status = google.start_grant(grant)  # the only call that starts a consent flow
        except ClientNotImported:
            return grant_answer(request, grant, notice=google_rows.import_first())
        except CredentialStoreFailed:
            return grant_answer(request, grant, notice=google_rows.store_failed())
        return grant_answer(request, grant, status=status)

    @app.get("/credentials/google/grant/{grant}", name="google_grant_row")
    @personal_data(True)
    def google_grant_row(request: Request, grant: str) -> Response:
        if not google_rows.is_grant(grant):
            return PlainTextResponse("Not found", status_code=404, headers=NO_STORE)
        return grant_answer(request, grant)

    @app.post("/credentials/google/remove", name="google_grant_remove")
    @personal_data(True)
    def google_grant_remove(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        grant, confirmed = google_rows.submitted_remove(fields)
        if not confirmed:  # the first click only asks; nothing is revoked or deleted
            return grant_answer(request, grant, confirming=True)
        try:
            removal = google.remove_grant(grant)
        except CredentialStoreFailed:
            return grant_answer(request, grant, notice=google_rows.store_failed())
        notice = google_rows.removed_notice(removal.revocation)
        return grant_answer(request, grant, status=removal.status, notice=notice)

    def notification_list(request: Request) -> HTMLResponse:
        found = notify_queries.open_notifications(AREA_LIMIT)
        context = area_context(found, errors=engine_health.issue_count())
        return page(request, NOTIFICATIONS_TEMPLATE, **context)

    @app.get("/notifications", name="notifications_area")
    @personal_data(True)
    def notifications_area(request: Request) -> HTMLResponse:
        return notification_list(request)

    @app.post("/notifications/dismiss", name="notification_dismiss")
    @personal_data(True)
    def notification_dismiss(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        notification_id = submitted_notification(fields)
        with units.open():
            notify.dismiss(notification_id)
        with units.open(read_only=True):
            return notification_list(request)

    @app.get("/quotas", name="quotas_page")
    @personal_data(False)  # usage rows hold codes and counts, never personal data
    def quotas_page(request: Request) -> HTMLResponse:
        context = quota_rows.page_context(budget.status())
        return page(request, quota_rows.PAGE_TEMPLATE, **context)

    def health_answer(request: Request, template: str) -> HTMLResponse:
        return page(request, template, **health_rows.panel_context(engine_health.report()))

    @app.get("/health/panel", name="health_page")
    @personal_data(True)  # a dead job's last error is free text
    def health_page(request: Request) -> HTMLResponse:
        return health_answer(request, health_rows.PAGE_TEMPLATE)

    @app.get("/health/panel/live", name="health_panel")
    @personal_data(True)
    def health_panel(request: Request) -> HTMLResponse:
        return health_answer(request, health_rows.PANEL_TEMPLATE)

    @app.post("/health/jobs/dismiss", name="health_job_dismiss")
    @personal_data(True)
    def health_job_dismiss(
        request: Request, fields: Annotated[FormFields, Depends(form_fields)]
    ) -> HTMLResponse:
        job_id = health_rows.submitted_job(fields)
        with units.open():
            queue.dismiss(job_id)
        with units.open(read_only=True):
            return health_answer(request, health_rows.PANEL_TEMPLATE)

    require_declared(app)
    require_route_rules(app)
    app.add_middleware(Guard, port=port, sessions=sessions, locked_page=locked_page)
    return app
