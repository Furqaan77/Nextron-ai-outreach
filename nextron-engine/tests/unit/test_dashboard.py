"""The hardened dashboard shell (AD-13): ``/enter``, sessions, the Host check, the security
headers, ``no-store`` and the base layout, driven through the ASGI interface with fakes. The
CSRF, ``Origin`` and ``Sec-Fetch-Site`` checks and the read-only GETs are in ``test_csrf.py``."""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from nextron.app.ops.api import OPEN_CODE_SECRET
from nextron.web import app as web_app
from nextron.web.app import NAV_SECTIONS, STATIC_DIR
from nextron.web.guard import CONTENT_SECURITY_POLICY
from nextron.web.routing import personal_data
from nextron.web.sessions import SessionStore
from tests.asgi import DEFAULT_HOST, Reply, request
from tests.dashboard import Dashboard, body_hx_headers

SECURITY_HEADERS = {
    b"content-security-policy": (
        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        b"connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"x-content-type-options": b"nosniff",
}
HTMX_CONFIG = (
    '{"allowEval":false,"allowScriptTags":false,"selfRequestsOnly":true,'
    '"historyCacheSize":0,"includeIndicatorStyles":false,'
    '"responseHandling":[{"code":"204","swap":false},{"code":"[23]..","swap":true},'
    '{"code":"[45]..","swap":true,"error":true,"target":"#request-error",'
    '"swapOverride":"textContent"}]}'
)
LOCKED_TEXT = "Run nextron open in a terminal to open the dashboard."
KEY = ("nextron-engine/test", OPEN_CODE_SECRET)


@pytest.fixture
def dashboard() -> Dashboard:
    return Dashboard()


@pytest.fixture
def sessions_created(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every session token any ``SessionStore`` creates during the test."""
    tokens: list[str] = []
    real = SessionStore.create

    def spy(self: SessionStore) -> str:
        tokens.append(real(self))
        return tokens[-1]

    monkeypatch.setattr(SessionStore, "create", spy)
    return tokens


def assert_locked(reply: Reply) -> None:
    assert reply.status == 401
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    assert LOCKED_TEXT in " ".join(text_of(reply.text).split())
    assert reply.all(b"set-cookie") == []
    assert reply.header(b"cache-control") == b"no-store"


def assert_secured(reply: Reply) -> None:
    """The four security headers exactly once each, exact, and no CORS header."""
    for name, value in SECURITY_HEADERS.items():
        assert reply.all(name) == [value], name
    assert not [name for name, _ in reply.headers if name.lower().startswith(b"access-control-")]


class Page(HTMLParser):
    """The tags of a page, each with its attributes, plus its text."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.stack: list[str] = []
        self.nav_links: list[tuple[str | None, str]] = []
        self._href: str | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self.stack.append(tag)
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag) :]

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if "a" in self.stack and "nav" in self.stack and data.strip():
            self.nav_links.append((self._href, data.strip()))

    def find(self, tag: str, **attrs: str) -> list[dict[str, str | None]]:
        return [
            found
            for name, found in self.tags
            if name == tag and all(found.get(key) == value for key, value in attrs.items())
        ]


def text_of(html: str) -> str:
    return "".join(Page(html).text)


# Row: enter -------------------------------------------------------------------------------------


@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=5)])
def test_a_fresh_code_opens_a_session_and_redirects_home(
    age: timedelta, dashboard: Dashboard, sessions_created: list[str], caplog: Any
) -> None:
    code = dashboard.entry.issue_code()
    dashboard.clock.advance(age)
    caplog.set_level(logging.INFO)

    reply = dashboard.enter(code)

    assert reply.status == 303
    assert reply.header(b"location") == b"/"
    assert reply.header(b"cache-control") == b"no-store"
    [token] = sessions_created
    assert reply.all(b"set-cookie") == [
        f"nextron_session={token}; HttpOnly; Path=/; SameSite=Strict".encode()
    ]
    assert len(token) == 43
    assert dashboard.secrets.store == {}
    assert_secured(reply)
    [record] = [r for r in caplog.records if r.name == "nextron.web.app"]
    assert (record.getMessage(), record.levelno) == ("dashboard_session_opened", logging.INFO)
    for logged in caplog.records:
        assert code not in str(vars(logged)) and token not in str(vars(logged))


def test_the_session_opens_the_home_page(dashboard: Dashboard) -> None:
    cookie = dashboard.open_session()

    reply = dashboard.get("/", cookie=cookie)

    assert reply.status == 200
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    assert reply.all(b"cache-control") == [b"no-store"]  # the page carries the CSRF token
    assert Page(reply.text).find("main")


def test_the_session_cookie_is_found_among_other_cookies(dashboard: Dashboard) -> None:
    cookie = dashboard.open_session()

    reply = dashboard.get("/", cookie=b"theme=dark; " + cookie + b"; other=1")

    assert reply.status == 200


# Row: enter, refused ----------------------------------------------------------------------------


def _reused(dashboard: Dashboard) -> bytes:
    code = dashboard.entry.issue_code()
    assert dashboard.enter(code).status == 303
    return b"t=" + code.encode()


def _expired(dashboard: Dashboard) -> bytes:
    code = dashboard.entry.issue_code()
    dashboard.clock.advance(timedelta(minutes=5, microseconds=1))
    return b"t=" + code.encode()


def _future_dated(dashboard: Dashboard) -> bytes:
    code = dashboard.entry.issue_code()
    dashboard.clock.advance(timedelta(microseconds=-1))
    return b"t=" + code.encode()


def _wrong(dashboard: Dashboard) -> bytes:
    dashboard.entry.issue_code()
    return b"t=" + b"W" * 43


def _non_ascii(dashboard: Dashboard) -> bytes:
    dashboard.entry.issue_code()
    return b"t=" + b"%C3%A9" * 43


def _invalid_utf8(dashboard: Dashboard) -> bytes:
    dashboard.entry.issue_code()
    return b"t=%FF%FE"


def _two_codes(dashboard: Dashboard) -> bytes:
    code = dashboard.entry.issue_code().encode()
    return b"t=" + code + b"&t=" + code


def _no_code(dashboard: Dashboard) -> bytes:
    dashboard.entry.issue_code()
    return b""


def _keyring_down(dashboard: Dashboard) -> bytes:
    code = dashboard.entry.issue_code()
    dashboard.secrets.failing.add("read")
    return b"t=" + code.encode()


def _delete_fails(dashboard: Dashboard) -> bytes:
    code = dashboard.entry.issue_code()
    dashboard.secrets.failing.add("delete")
    return b"t=" + code.encode()


REFUSALS: dict[str, tuple[Callable[[Dashboard], bytes], str, bool]] = {
    # case: (prepare -> query string, logged reason, pending code still stored afterwards)
    "reused": (_reused, "absent", False),
    "expired": (_expired, "expired", False),
    "future_dated": (_future_dated, "expired", False),
    "wrong": (_wrong, "mismatch", True),
    "non_ascii": (_non_ascii, "mismatch", True),
    "invalid_utf8": (_invalid_utf8, "mismatch", True),
    "two_codes": (_two_codes, "mismatch", True),
    "no_code": (_no_code, "mismatch", True),
    "keyring_down": (_keyring_down, "unavailable", True),
    "delete_fails": (_delete_fails, "unavailable", True),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_refused_code_gets_the_locked_page_and_no_session(
    case: str, dashboard: Dashboard, sessions_created: list[str], caplog: Any
) -> None:
    prepare, reason, kept = REFUSALS[case]
    query = prepare(dashboard)
    already = list(sessions_created)  # "reused" opened one session on purpose
    caplog.set_level(logging.INFO)

    reply = request(dashboard.app, "/enter", query=query)

    assert_locked(reply)
    assert_secured(reply)
    assert sessions_created == already
    assert (KEY in dashboard.secrets.store) is kept
    [record] = [r for r in caplog.records if r.name == "nextron.web.app"]
    assert (record.getMessage(), record.levelno) == ("dashboard_entry_refused", logging.WARNING)
    assert record.reason == reason
    for presented in parse_qs(query.decode(), keep_blank_values=True).get("t", []):
        assert not presented or presented not in str(vars(record))


def test_two_codes_never_touch_the_pending_code(dashboard: Dashboard) -> None:
    query = _two_codes(dashboard)
    calls = len(dashboard.secrets.calls)

    assert request(dashboard.app, "/enter", query=query).status == 401
    assert dashboard.secrets.calls[calls:] == []


# Row: no session --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/health", "/static/app.css", "/static/htmx-2.0.11.min.js", "/nope", "/enter/"]
)
def test_a_request_without_a_session_gets_the_locked_page(path: str, dashboard: Dashboard) -> None:
    reply = dashboard.get(path)

    assert_locked(reply)
    assert_secured(reply)
    assert b"uptime_s" not in reply.body and b"htmx" not in reply.body


@pytest.mark.parametrize(
    "cookie",
    [
        b"nextron_session=",
        b"nextron_session=" + b"x" * 43,
        b"nextron_session",
        b"other_session=VALID",
        b"Nextron_Session=VALID",
        b"nextron_session=VALID" + b"x",
    ],
)
def test_a_bad_cookie_gets_the_locked_page(cookie: bytes, dashboard: Dashboard) -> None:
    valid = dashboard.open_session().split(b"=", 1)[1]

    reply = dashboard.get("/", cookie=cookie.replace(b"VALID", valid))

    assert_locked(reply)


# Row: restart -----------------------------------------------------------------------------------


def test_a_session_does_not_survive_a_restart() -> None:
    secrets = Dashboard().secrets
    first = Dashboard(secrets=secrets)
    cookie = first.open_session()
    assert first.get("/", cookie=cookie).status == 200

    second = Dashboard(secrets=secrets)  # the engine restarted

    assert_locked(second.get("/", cookie=cookie))


# Row: host --------------------------------------------------------------------------------------


@pytest.fixture
def spied(dashboard: Dashboard) -> list[str]:
    """A ``/spy`` route that records each time its handler runs."""
    calls: list[str] = []

    @personal_data(False)
    def spy() -> PlainTextResponse:
        calls.append("ran")
        return PlainTextResponse("spy")

    dashboard.app.add_api_route("/spy", spy, methods=["GET"], name="spy")
    return calls


BAD_HOSTS: dict[str, list[tuple[bytes, bytes]]] = {
    "evil": [(b"host", b"evil.example")],
    "evil_with_port": [(b"host", b"evil.example:8766")],
    "no_port": [(b"host", b"127.0.0.1")],
    "other_port": [(b"host", b"127.0.0.1:1")],
    "trailing_dot": [(b"host", b"localhost.:8766")],
    "ipv6": [(b"host", b"[::1]:8766")],
    "upper_case": [(b"host", b"LOCALHOST:8766")],
    "padded": [(b"host", b" 127.0.0.1:8766")],
    "missing": [],
    "two": [(b"host", DEFAULT_HOST), (b"host", DEFAULT_HOST)],
    "two_different": [(b"host", DEFAULT_HOST), (b"host", b"evil.example")],
}


@pytest.mark.parametrize("case", sorted(BAD_HOSTS))
def test_an_unexpected_host_is_refused_before_any_handler_runs(
    case: str, dashboard: Dashboard, spied: list[str]
) -> None:
    cookie = dashboard.open_session()

    reply = request(dashboard.app, "/spy", headers=[*BAD_HOSTS[case], (b"cookie", cookie)])

    assert reply.status == 400
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == b"Bad request: unexpected Host header"
    assert reply.header(b"cache-control") == b"no-store"
    assert_secured(reply)
    assert spied == []


def test_an_unexpected_host_never_reaches_enter(dashboard: Dashboard) -> None:
    code = dashboard.entry.issue_code()
    calls = len(dashboard.secrets.calls)

    reply = request(
        dashboard.app, "/enter", query=b"t=" + code.encode(), headers=[(b"host", b"evil.example")]
    )

    assert reply.status == 400
    assert dashboard.secrets.calls[calls:] == []
    assert KEY in dashboard.secrets.store


@pytest.mark.parametrize("host", [DEFAULT_HOST, b"localhost:8766"])
def test_both_local_hosts_are_allowed(host: bytes, dashboard: Dashboard, spied: list[str]) -> None:
    cookie = dashboard.open_session()

    reply = request(dashboard.app, "/spy", headers=[(b"host", host), (b"cookie", cookie)])

    assert reply.status == 200
    assert spied == ["ran"]


# Row: headers -----------------------------------------------------------------------------------


@pytest.fixture
def test_routes(dashboard: Dashboard) -> FastAPI:
    """Test-only routes: personal (True), non-personal (False), undeclared, one that sets its
    own security and CORS headers, and one that crashes."""
    app = dashboard.app

    @personal_data(True)
    def personal() -> PlainTextResponse:
        return PlainTextResponse("personal")

    @personal_data(False)
    def public() -> PlainTextResponse:
        return PlainTextResponse("public", headers={"Cache-Control": "max-age=60"})

    def undeclared() -> PlainTextResponse:
        return PlainTextResponse("undeclared")

    @personal_data(True)
    def leaky() -> Response:
        return Response(
            "leaky",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Credentials": "true",
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": "default-src *",
                "Referrer-Policy": "unsafe-url",
                "Cache-Control": "public, max-age=600",
            },
        )

    @personal_data(False)
    def crash() -> HTMLResponse:
        raise RuntimeError("the handler broke")

    for path, endpoint in [
        ("/t/personal", personal),
        ("/t/public", public),
        ("/t/undeclared", undeclared),
        ("/t/leaky", leaky),
        ("/t/crash", crash),
    ]:
        app.add_api_route(path, endpoint, methods=["GET"])
    return app


def test_security_headers_on_200_303_400_401_404_and_500(
    dashboard: Dashboard, test_routes: FastAPI
) -> None:
    code = dashboard.entry.issue_code()
    entered = dashboard.enter(code)
    cookie = entered.header(b"set-cookie").split(b";", 1)[0]  # type: ignore[union-attr]
    with_session = [(b"host", DEFAULT_HOST), (b"cookie", cookie)]
    crashed = request(test_routes, "/t/crash", headers=with_session, catch=True)
    replies = {
        200: dashboard.get("/", cookie=cookie),
        303: entered,
        400: request(test_routes, "/", headers=[(b"host", b"evil.example")]),
        401: dashboard.get("/"),
        404: dashboard.get("/nope", cookie=cookie),
        500: crashed,
    }

    for status, reply in replies.items():
        assert reply.status == status
        assert_secured(reply)
    assert CONTENT_SECURITY_POLICY.encode() == SECURITY_HEADERS[b"content-security-policy"]


def test_a_crash_before_the_response_is_a_500_and_is_raised_again(
    dashboard: Dashboard, test_routes: FastAPI
) -> None:
    cookie = dashboard.open_session()

    reply = request(
        test_routes, "/t/crash", headers=[(b"host", DEFAULT_HOST), (b"cookie", cookie)], catch=True
    )

    assert reply.status == 500
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == b"Internal error"
    assert reply.header(b"cache-control") == b"no-store"
    assert isinstance(reply.error, RuntimeError)
    assert str(reply.error) == "the handler broke"


def test_handler_headers_are_replaced_and_cors_headers_removed(
    dashboard: Dashboard, test_routes: FastAPI
) -> None:
    cookie = dashboard.open_session()

    reply = request(
        test_routes,
        "/t/leaky",
        headers=[(b"host", DEFAULT_HOST), (b"cookie", cookie), (b"origin", b"http://evil.example")],
    )

    assert reply.status == 200
    assert_secured(reply)
    assert reply.all(b"cache-control") == [b"no-store"]


def test_a_request_with_an_origin_gets_no_cors_headers(dashboard: Dashboard) -> None:
    cookie = dashboard.open_session()

    reply = request(
        dashboard.app,
        "/health",
        headers=[(b"host", DEFAULT_HOST), (b"cookie", cookie), (b"origin", b"http://evil.example")],
    )

    assert reply.status == 200
    assert_secured(reply)


def test_a_cross_origin_preflight_gets_no_cors_headers(dashboard: Dashboard) -> None:
    reply = request(
        dashboard.app,
        "/health",
        method="OPTIONS",
        headers=[
            (b"host", DEFAULT_HOST),
            (b"origin", b"http://evil.example"),
            (b"access-control-request-method", b"GET"),
        ],
    )

    assert reply.status == 401
    assert_secured(reply)


# Row: no-store ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/t/personal", [b"no-store"]),
        ("/t/public", [b"max-age=60"]),
        ("/t/undeclared", [b"no-store"]),
        ("/nope", [b"no-store"]),
        ("/static/nope.js", [b"no-store"]),
        ("/health", []),
        ("/static/app.css", []),
    ],
)
def test_no_store_unless_the_route_declared_no_personal_data(
    path: str, expected: list[bytes], dashboard: Dashboard, test_routes: FastAPI
) -> None:
    cookie = dashboard.open_session()

    reply = dashboard.get(path, cookie=cookie)

    assert reply.all(b"cache-control") == expected
    assert_secured(reply)


def test_the_401_page_is_not_stored(dashboard: Dashboard) -> None:
    assert dashboard.get("/").all(b"cache-control") == [b"no-store"]


# Static assets ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content_type"),
    [
        ("htmx-2.0.11.min.js", b"text/javascript; charset=utf-8"),
        ("app.css", b"text/css; charset=utf-8"),
    ],
)
def test_static_assets_are_served_as_vendored(
    name: str, content_type: bytes, dashboard: Dashboard
) -> None:
    cookie = dashboard.open_session()

    reply = dashboard.get(f"/static/{name}", cookie=cookie)

    assert reply.status == 200
    assert reply.header(b"content-type") == content_type
    assert reply.body == (STATIC_DIR / name).read_bytes()


@pytest.mark.parametrize("name", ["nope.js", "..", "base.html", "app.CSS", "%2e%2e"])
def test_any_other_static_name_is_404(name: str, dashboard: Dashboard) -> None:
    cookie = dashboard.open_session()

    assert dashboard.get(f"/static/{name}", cookie=cookie).status == 404


def test_static_assets_are_read_once_when_the_app_is_built(
    dashboard: Dashboard, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie = dashboard.open_session()
    monkeypatch.setattr(web_app, "STATIC_DIR", STATIC_DIR / "missing")

    assert dashboard.get("/static/app.css", cookie=cookie).status == 200


# Other ASGI scopes ------------------------------------------------------------------------------


def test_a_websocket_is_closed(dashboard: Dashboard) -> None:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "path": "/ws",
        "raw_path": b"/ws",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", DEFAULT_HOST)],
        "scheme": "ws",
        "subprotocols": [],
    }
    asyncio.run(dashboard.app(scope, receive, send))

    assert [message["type"] for message in sent] == ["websocket.close"]


def test_lifespan_passes_through(dashboard: Dashboard) -> None:
    sent: list[dict[str, Any]] = []
    incoming = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive() -> dict[str, Any]:
        return next(incoming)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(dashboard.app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send))

    assert [message["type"] for message in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]


# Layout -----------------------------------------------------------------------------------------


def home_page(dashboard: Dashboard) -> Page:
    cookie = dashboard.open_session()
    reply = dashboard.get("/", cookie=cookie)
    assert reply.status == 200
    return Page(reply.text)


def test_htmx_is_configured_through_the_meta_tag_before_the_script(dashboard: Dashboard) -> None:
    page = home_page(dashboard)

    [meta] = page.find("meta", name="htmx-config")
    assert meta["content"] == HTMX_CONFIG
    assert json.loads(HTMX_CONFIG) == {
        "allowEval": False,
        "allowScriptTags": False,
        "selfRequestsOnly": True,
        "historyCacheSize": 0,
        "includeIndicatorStyles": False,
        "responseHandling": [
            {"code": "204", "swap": False},
            {"code": "[23]..", "swap": True},
            {
                "code": "[45]..",
                "swap": True,
                "error": True,
                "target": "#request-error",
                "swapOverride": "textContent",
            },
        ],
    }
    [script] = page.find("script")
    assert script == {"src": "/static/htmx-2.0.11.min.js", "defer": None}
    order = [
        tag for tag, attrs in page.tags if tag == "script" or attrs.get("name") == "htmx-config"
    ]
    assert order == ["meta", "script"]
    assert page.find("link", rel="stylesheet", href="/static/app.css")


def test_every_asset_is_self_hosted(dashboard: Dashboard) -> None:
    page = home_page(dashboard)

    sources = [attrs.get("src") for tag, attrs in page.tags if "src" in attrs]
    sources += [attrs.get("href") for tag, attrs in page.tags if tag == "link"]
    assert sources
    for source in sources:
        assert source and source.startswith("/static/") and not source.startswith("//")
    assert not page.find("style")
    assert not [attrs for _, attrs in page.tags if "style" in attrs]


def test_the_layout_has_a_brand_nav_notifications_and_main(dashboard: Dashboard) -> None:
    page = home_page(dashboard)

    assert page.find("header")
    assert page.find("a", href="/", **{"class": "brand"})
    assert page.find("nav")
    assert page.find("section", id="notifications", **{"aria-live": "polite"})
    assert page.find("p", id="request-error", role="alert", **{"class": "request-error"})
    assert page.find(
        "div", id="notification-list", **{"hx-get": "/notifications", "hx-trigger": "load"}
    )
    assert page.find("main")


def test_the_layout_gives_htmx_the_sessions_csrf_token(dashboard: Dashboard) -> None:
    first, second = dashboard.open_session(), dashboard.open_session()

    tokens = [
        body_hx_headers(dashboard.get("/", cookie=cookie).text)["X-CSRF-Token"]
        for cookie in (first, second, first)
    ]

    assert tokens[0] == tokens[2] != tokens[1]
    assert all(isinstance(token, str) and len(token) == 43 for token in tokens)


def test_the_locked_page_carries_no_csrf_token(dashboard: Dashboard) -> None:
    reply = dashboard.get("/")

    assert reply.status == 401
    assert b"hx-headers" not in reply.body and b"csrf" not in reply.body.lower()


def test_the_nav_lists_only_the_sections_that_exist(dashboard: Dashboard) -> None:
    assert home_page(dashboard).nav_links == [("/settings", "Settings")]


def test_the_nav_lists_existing_sections_in_order(dashboard: Dashboard) -> None:
    app = dashboard.app

    @personal_data(False)
    def section() -> PlainTextResponse:
        return PlainTextResponse("section")

    # added out of order (Settings is the real page); a POST-only route and an unknown name
    # don't count
    app.add_api_route("/health-panel", section, methods=["GET"], name="health_page")
    app.add_api_route("/queue", section, methods=["POST"], name="queue_page")
    app.add_api_route("/other", section, methods=["GET"], name="other_page")
    app.add_api_route("/funnel", section, methods=["GET"], name="funnel_page")

    assert home_page(dashboard).nav_links == [
        ("/funnel", "Funnel"),
        ("/settings", "Settings"),
        ("/health-panel", "Health"),
    ]


def test_nav_sections_are_the_six_ux_sections() -> None:
    assert NAV_SECTIONS == (
        ("queue_page", "Queue"),
        ("signal_inbox_page", "Signal Inbox"),
        ("pipeline_page", "Leads/Pipeline"),
        ("funnel_page", "Funnel"),
        ("settings_page", "Settings"),
        ("health_page", "Health"),
    )


def test_the_locked_page_has_no_assets_and_no_data(dashboard: Dashboard) -> None:
    reply = dashboard.get("/")
    page = Page(reply.text)

    assert not page.find("script") and not page.find("link") and not page.find("img")
    assert not page.find("style")
    assert "Run nextron open in a terminal" in " ".join("".join(page.text).split())
