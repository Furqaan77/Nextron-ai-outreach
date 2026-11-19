"""CSRF-checked POSTs and read-only GETs (AD-13), against the story's I/O matrix: the POST,
origin, no-session, enter and route rows, driven through ``Dashboard`` with test routes that
record each time their handler runs."""

import logging
import threading
from datetime import timedelta
from typing import Any

import pytest
from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute

from nextron.app.ops.api import OPEN_CODE_SECRET
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.routing import dashboard_route_class, personal_data, route_rule_problems
from nextron.web.sessions import SessionStore, session_cookie_values
from tests.asgi import DEFAULT_HOST, Reply, request
from tests.dashboard import FORM, ORIGIN, Dashboard
from tests.fakes.clock import FrozenClock
from tests.fakes.units import FakeUnits

KEY = ("nextron-engine/test", OPEN_CODE_SECRET)
LOCKED_TEXT = b"nextron open"
SECURITY_HEADERS = (
    b"content-security-policy",
    b"x-frame-options",
    b"referrer-policy",
    b"x-content-type-options",
)


class Recorder:
    """Test routes on a dashboard: ``POST /t/post`` and ``GET /t/get`` record each call."""

    def __init__(self, dashboard: Dashboard) -> None:
        self.dashboard = dashboard
        self.posts: list[str] = []
        self.gets: list[dict[str, Any]] = []
        units = dashboard.units
        assert isinstance(units, FakeUnits)

        @personal_data(False)
        def post_target() -> PlainTextResponse:
            self.posts.append("ran")
            return PlainTextResponse("done")

        @personal_data(False)
        def get_target() -> PlainTextResponse:
            self.gets.append(
                {"thread": threading.get_ident(), "open_now": list(units.open_now)}
            )
            return PlainTextResponse("read")

        dashboard.app.add_api_route("/t/post", post_target, methods=["POST"])
        dashboard.app.add_api_route("/t/get", get_target, methods=["GET"])


@pytest.fixture
def dashboard() -> Dashboard:
    return Dashboard()


@pytest.fixture
def recorder(dashboard: Dashboard) -> Recorder:
    return Recorder(dashboard)


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    """A session's cookie and its CSRF token."""
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def assert_refused(reply: Reply) -> None:
    assert reply.status == 403
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.body == SECURITY_CHECK_FAILED.encode()
    assert reply.header(b"cache-control") == b"no-store"
    for name in SECURITY_HEADERS:
        assert reply.header(name) is not None, name


def refusals(caplog: Any) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "request_refused"]


def assert_nothing_secret_logged(caplog: Any, *secrets: str | bytes) -> None:
    for record in caplog.records:
        logged = str(vars(record))
        for secret in secrets:
            text = secret.decode() if isinstance(secret, bytes) else secret
            assert text not in logged


# Row: POST ok -----------------------------------------------------------------------------------


def test_the_session_token_is_43_urlsafe_characters_and_stable(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    cookie, token = session

    assert len(token) == 43
    assert dashboard.csrf_token(cookie) == token  # kept for the session's life


def test_each_session_has_its_own_token(dashboard: Dashboard) -> None:
    first, second = dashboard.open_session(), dashboard.open_session()

    assert dashboard.csrf_token(first) != dashboard.csrf_token(second)


def test_the_store_maps_a_session_to_its_token() -> None:
    store = SessionStore()
    session = store.create()

    token = store.csrf_token(session)

    assert token is not None and len(token) == 43
    assert store.csrf_token(session) == token
    assert store.csrf_token("") is None
    assert store.csrf_token("x" * 43) is None


def test_a_store_without_a_clock_records_no_start() -> None:
    store = SessionStore()
    session = store.create()

    assert store.csrf_token(session) is not None
    assert store.started_at(session) is None


def test_the_start_is_none_for_an_unknown_or_empty_token() -> None:
    store = SessionStore(FrozenClock().now)
    store.create()

    assert store.started_at("") is None
    assert store.started_at("x" * 43) is None


def test_the_start_is_the_clocks_time_at_create_and_does_not_move() -> None:
    clock = FrozenClock()
    store = SessionStore(clock.now)
    created_at = clock.now()
    session = store.create()
    clock.advance(timedelta(hours=2))

    assert store.started_at(session) == created_at
    assert store.started_at(session) == created_at
    later = store.create()
    assert store.started_at(later) == created_at + timedelta(hours=2)
    assert store.started_at(session) == created_at


@pytest.mark.parametrize(
    ("header", "values"),
    [
        ("", []),
        ("nextron_session=abc", ["abc"]),
        (" a=1; nextron_session = abc ;nextron_session=def", ["abc", "def"]),
        ("nextron_session; other=nextron_session", []),
        ("nextron_session_x=abc; nextron_session=", [""]),
    ],
)
def test_session_cookie_values(header: str, values: list[str]) -> None:
    assert list(session_cookie_values(header)) == values


def _header(token: str) -> dict[str, Any]:
    return {"token": token}


def _field(token: str) -> dict[str, Any]:
    return {
        "headers": [(b"content-type", FORM)],
        "body": b"note=hello+there&csrf_token=" + token.encode(),
    }


def _field_with_charset(token: str) -> dict[str, Any]:
    return {
        "headers": [(b"content-type", b"Application/X-WWW-Form-Urlencoded; charset=UTF-8")],
        "body": b"csrf_token=" + token.encode(),
    }


def _both(token: str) -> dict[str, Any]:
    return {**_field(token), "token": token}


def _same_origin(token: str) -> dict[str, Any]:
    return {"token": token, "headers": [(b"sec-fetch-site", b"same-origin")]}


def _two_matching_headers(token: str) -> dict[str, Any]:
    return {"headers": [(b"x-csrf-token", token.encode()), (b"x-csrf-token", token.encode())]}


ACCEPTED = {
    "header": _header,
    "field": _field,
    "field_with_charset": _field_with_charset,
    "header_and_field": _both,
    "same_origin": _same_origin,
    "two_matching_headers": _two_matching_headers,
}


@pytest.mark.parametrize("case", sorted(ACCEPTED))
def test_a_checked_post_runs_the_handler_once(
    case: str, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str], caplog: Any
) -> None:
    cookie, token = session

    reply = dashboard.post("/t/post", cookie=cookie, **ACCEPTED[case](token))

    assert reply.status == 200, reply
    assert reply.body == b"done"
    assert recorder.posts == ["ran"]
    assert refusals(caplog) == []


def test_a_post_from_localhost_with_the_localhost_origin_runs(
    dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    cookie, token = session
    host = b"localhost:8766"

    reply = request(
        dashboard.app,
        "/t/post",
        method="POST",
        headers=[
            (b"host", host),
            (b"cookie", cookie),
            (b"origin", b"http://" + host),
            (b"x-csrf-token", token.encode()),
        ],
    )

    assert reply.status == 200
    assert recorder.posts == ["ran"]


# Row: POST token refused ------------------------------------------------------------------------


def _no_token(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {}


def _wrong(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"token": "W" * 43}


def _empty(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"token": ""}


def _prefix(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"token": token[:-1]}


def _another_sessions(dashboard: Dashboard, token: str) -> dict[str, Any]:
    other = dashboard.open_session()
    return {"token": dashboard.csrf_token(other)}


def _the_session_token(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"token": "SESSION"}  # replaced by the session token in the test


def _one_of_two_headers_wrong(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"headers": [(b"x-csrf-token", token.encode()), (b"x-csrf-token", b"W" * 43)]}


def _header_ok_field_wrong(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"token": token, "headers": [(b"content-type", FORM)], "body": b"csrf_token=wrong"}


def _header_ok_field_blank(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {"token": token, "headers": [(b"content-type", FORM)], "body": b"csrf_token="}


def _field_ok_twice_one_wrong(dashboard: Dashboard, token: str) -> dict[str, Any]:
    body = b"csrf_token=" + token.encode() + b"&csrf_token=wrong"
    return {"headers": [(b"content-type", FORM)], "body": body}


def _non_ascii_body(dashboard: Dashboard, token: str) -> dict[str, Any]:
    body = "note=café&csrf_token=".encode() + token.encode()
    return {"token": token, "headers": [(b"content-type", FORM)], "body": body}


def _too_many_fields(dashboard: Dashboard, token: str) -> dict[str, Any]:
    body = b"&".join([b"a=1"] * 1000 + [b"csrf_token=" + token.encode()])
    return {"token": token, "headers": [(b"content-type", FORM)], "body": body}


def _json_body(dashboard: Dashboard, token: str) -> dict[str, Any]:
    body = b'{"csrf_token": "' + token.encode() + b'"}'
    return {"headers": [(b"content-type", b"application/json")], "body": body}


def _multipart_body(dashboard: Dashboard, token: str) -> dict[str, Any]:
    body = (
        b'--b\r\nContent-Disposition: form-data; name="csrf_token"\r\n\r\n'
        + token.encode()
        + b"\r\n--b--\r\n"
    )
    return {"headers": [(b"content-type", b"multipart/form-data; boundary=b")], "body": body}


def _two_content_types(dashboard: Dashboard, token: str) -> dict[str, Any]:
    return {
        "token": token,
        "headers": [(b"content-type", FORM), (b"content-type", b"text/plain")],
        "body": b"csrf_token=" + token.encode(),
    }


TOKEN_REFUSALS = {
    "none": _no_token,
    "wrong": _wrong,
    "empty": _empty,
    "prefix": _prefix,
    "another_sessions": _another_sessions,
    "the_session_token": _the_session_token,
    "one_of_two_headers_wrong": _one_of_two_headers_wrong,
    "header_ok_field_wrong": _header_ok_field_wrong,
    "header_ok_field_blank": _header_ok_field_blank,
    "field_ok_twice_one_wrong": _field_ok_twice_one_wrong,
    "non_ascii_body": _non_ascii_body,
    "too_many_fields": _too_many_fields,
    "json_body": _json_body,
    "multipart_body": _multipart_body,
    "two_content_types": _two_content_types,
}


@pytest.mark.parametrize("case", sorted(TOKEN_REFUSALS))
def test_a_post_without_its_sessions_token_is_refused_and_changes_nothing(
    case: str, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str], caplog: Any
) -> None:
    cookie, token = session
    sent = TOKEN_REFUSALS[case](dashboard, token)
    if sent.get("token") == "SESSION":
        sent["token"] = cookie.split(b"=", 1)[1].decode()
    caplog.set_level(logging.INFO)

    reply = dashboard.post("/t/post", cookie=cookie, query=b"next=query-secret", **sent)

    assert_refused(reply)
    assert recorder.posts == []
    [record] = refusals(caplog)
    assert (record.name, record.levelno, record.check, record.method, record.path) == (
        "nextron.web.csrf",
        logging.WARNING,
        "csrf_token",
        "POST",
        "/t/post",
    )
    assert_nothing_secret_logged(caplog, token, cookie.split(b"=", 1)[1], "query-secret")


# Row: POST origin refused -----------------------------------------------------------------------

ORIGIN_REFUSALS: dict[str, tuple[bytes | None, list[tuple[bytes, bytes]]]] = {
    # case: (Origin, extra headers)
    "missing": (None, []),
    "null": (b"null", []),
    "other_port": (b"http://127.0.0.1:8765", []),
    "https": (b"https://127.0.0.1:8766", []),
    "trailing_slash": (ORIGIN + b"/", []),
    "localhost_on_127": (b"http://localhost:8766", []),
    "upper_case": (b"HTTP://127.0.0.1:8766", []),
    "evil": (b"http://evil.example", []),
    "two": (ORIGIN, [(b"origin", ORIGIN)]),
}
FETCH_SITE_REFUSALS: dict[str, list[bytes]] = {
    "cross_site": [b"cross-site"],
    "same_site": [b"same-site"],
    "none": [b"none"],
    "upper_case": [b"Same-Origin"],
    "doubled": [b"same-origin", b"same-origin"],
}


@pytest.mark.parametrize("case", sorted(ORIGIN_REFUSALS))
def test_a_post_with_the_wrong_origin_is_refused_before_routing(
    case: str, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str], caplog: Any
) -> None:
    cookie, token = session
    origin, extra = ORIGIN_REFUSALS[case]

    reply = dashboard.post(
        "/t/post",
        cookie=cookie,
        token=token,
        origin=origin,
        headers=extra,
        query=b"next=query-secret",
    )

    assert_refused(reply)
    assert recorder.posts == []
    [record] = refusals(caplog)
    assert (record.name, record.levelno, record.check, record.method, record.path) == (
        "nextron.web.guard",
        logging.WARNING,
        "origin",
        "POST",
        "/t/post",
    )
    assert_nothing_secret_logged(caplog, token, b"evil.example", "query-secret")


@pytest.mark.parametrize("case", sorted(FETCH_SITE_REFUSALS))
def test_a_post_with_another_fetch_site_is_refused_before_routing(
    case: str, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str], caplog: Any
) -> None:
    cookie, token = session
    headers = [(b"sec-fetch-site", value) for value in FETCH_SITE_REFUSALS[case]]

    reply = dashboard.post("/t/post", cookie=cookie, token=token, headers=headers)

    assert_refused(reply)
    assert recorder.posts == []
    [record] = refusals(caplog)
    assert (record.name, record.check, record.method, record.path) == (
        "nextron.web.guard",
        "fetch_site",
        "POST",
        "/t/post",
    )
    assert_nothing_secret_logged(caplog, token, b"cross-site", b"same-site")


def test_a_native_form_submission_is_refused(
    dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str], caplog: Any
) -> None:
    """Under Referrer-Policy: no-referrer a browser's native form POST sends Origin: null, so
    every form posts through HTMX (hx-post); a native fallback is refused, token or not."""
    cookie, token = session

    reply = dashboard.post(
        "/t/post",
        cookie=cookie,
        origin=b"null",
        headers=[
            (b"sec-fetch-site", b"same-origin"),
            (b"sec-fetch-mode", b"navigate"),
            (b"content-type", FORM),
        ],
        body=b"note=hello&csrf_token=" + token.encode(),
    )

    assert_refused(reply)
    assert recorder.posts == []
    [record] = refusals(caplog)
    assert (record.name, record.check) == ("nextron.web.guard", "origin")


def test_the_same_form_posted_through_htmx_runs(
    dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    """HTMX's request keeps the real Origin and adds the hx-headers token."""
    cookie, token = session

    reply = dashboard.post(
        "/t/post",
        cookie=cookie,
        token=token,
        headers=[
            (b"sec-fetch-site", b"same-origin"),
            (b"sec-fetch-mode", b"cors"),
            (b"hx-request", b"true"),
            (b"content-type", FORM),
        ],
        body=b"note=hello&csrf_token=" + token.encode(),
    )

    assert reply.status == 200
    assert recorder.posts == ["ran"]


@pytest.mark.parametrize(("method", "path"), [("POST", "/nope"), ("PUT", "/t/post")])
def test_an_unknown_path_or_another_method_with_a_bad_origin_is_refused_too(
    method: str, path: str, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    cookie, token = session

    reply = request(
        dashboard.app,
        path,
        method=method,
        headers=[
            (b"host", DEFAULT_HOST),
            (b"cookie", cookie),
            (b"origin", b"http://evil.example"),
            (b"x-csrf-token", token.encode()),
        ],
    )

    assert_refused(reply)
    assert recorder.posts == []


@pytest.mark.parametrize(("method", "status"), [("PUT", 405), ("DELETE", 405)])
def test_another_method_from_the_right_origin_reaches_no_handler(
    method: str, status: int, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    cookie, token = session

    reply = request(
        dashboard.app,
        "/t/post",
        method=method,
        headers=[
            (b"host", DEFAULT_HOST),
            (b"cookie", cookie),
            (b"origin", ORIGIN),
            (b"x-csrf-token", token.encode()),
        ],
    )

    assert reply.status == status
    assert recorder.posts == []


def test_a_get_with_any_origin_or_fetch_site_is_not_checked(
    dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    cookie, _ = session

    reply = request(
        dashboard.app,
        "/t/get",
        headers=[
            (b"host", DEFAULT_HOST),
            (b"cookie", cookie),
            (b"origin", b"http://evil.example"),
            (b"sec-fetch-site", b"cross-site"),
        ],
    )

    assert reply.status == 200


# Row: no session --------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/t/post", "/", "/nope"])
def test_a_cross_site_post_without_a_session_gets_the_locked_page(
    path: str, dashboard: Dashboard, recorder: Recorder, caplog: Any
) -> None:
    reply = dashboard.post(path, origin=b"http://evil.example", token="W" * 43)

    assert reply.status == 401
    assert LOCKED_TEXT in reply.body
    assert reply.header(b"cache-control") == b"no-store"
    assert recorder.posts == []
    assert refusals(caplog) == []


def test_a_post_with_a_dead_session_gets_the_locked_page(
    dashboard: Dashboard, recorder: Recorder
) -> None:
    reply = dashboard.post("/t/post", cookie=b"nextron_session=" + b"x" * 43, token="W" * 43)

    assert reply.status == 401
    assert recorder.posts == []


# Row: enter -------------------------------------------------------------------------------------

CROSS_SITE_ENTRIES = {
    "cross_site": [b"cross-site"],
    "same_site": [b"same-site"],
    "same_origin": [b"same-origin"],
    "upper_case_none": [b"None"],
    "two": [b"none", b"none"],
    "none_and_cross_site": [b"none", b"cross-site"],
}


@pytest.mark.parametrize("case", sorted(CROSS_SITE_ENTRIES))
def test_enter_refuses_a_navigation_the_browser_did_not_start(
    case: str, dashboard: Dashboard, caplog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = dashboard.entry.issue_code()
    calls = len(dashboard.secrets.calls)
    created: list[str] = []
    monkeypatch.setattr(SessionStore, "create", lambda self: created.append("x") or "x")
    headers = [(b"host", DEFAULT_HOST)]
    headers += [(b"sec-fetch-site", value) for value in CROSS_SITE_ENTRIES[case]]
    caplog.set_level(logging.INFO)

    reply = request(dashboard.app, "/enter", query=b"t=" + code.encode(), headers=headers)

    assert reply.status == 401
    assert LOCKED_TEXT in reply.body
    assert reply.all(b"set-cookie") == []
    assert reply.header(b"cache-control") == b"no-store"
    assert created == []
    assert dashboard.secrets.calls[calls:] == []  # the code was never looked at
    assert KEY in dashboard.secrets.store
    [record] = [r for r in caplog.records if r.name == "nextron.web.app"]
    assert (record.getMessage(), record.levelno, record.reason) == (
        "dashboard_entry_refused",
        logging.WARNING,
        "cross_site",
    )
    assert_nothing_secret_logged(caplog, code)
    assert dashboard.enter(code).status == 303  # the pending code still works


@pytest.mark.parametrize("fetch_site", [None, b"none"])
def test_enter_accepts_a_navigation_the_browser_started(
    fetch_site: bytes | None, dashboard: Dashboard
) -> None:
    code = dashboard.entry.issue_code()
    headers = [(b"host", DEFAULT_HOST)]
    if fetch_site is not None:
        headers.append((b"sec-fetch-site", fetch_site))

    reply = request(dashboard.app, "/enter", query=b"t=" + code.encode(), headers=headers)

    assert reply.status == 303
    assert reply.header(b"set-cookie") is not None


# Row: GET ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/t/get", "/", "/health", "/static/app.css"])
def test_each_get_handler_runs_inside_one_read_only_unit_on_its_own_thread(
    path: str, dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    cookie, _ = session
    units = dashboard.units
    assert isinstance(units, FakeUnits)
    before = len(units.opened)

    reply = dashboard.get(path, cookie=cookie)

    assert reply.status == 200
    [(read_only, thread)] = units.opened[before:]
    assert read_only is True
    assert thread != threading.get_ident()  # the threadpool, not the event loop's thread
    assert units.open_now == []
    if path == "/t/get":
        [call] = recorder.gets
        assert call["thread"] == thread
        [unit] = call["open_now"]
        assert (unit.read_only, unit.thread) == (True, thread)


def test_a_post_handler_opens_no_unit_for_itself(
    dashboard: Dashboard, recorder: Recorder, session: tuple[bytes, str]
) -> None:
    cookie, token = session
    units = dashboard.units
    assert isinstance(units, FakeUnits)
    before = len(units.opened)

    assert dashboard.post("/t/post", cookie=cookie, token=token).status == 200

    assert units.opened[before:] == []  # a POST handler opens its own write unit of work


def test_the_wrapper_keeps_the_personal_data_declaration_and_signature(
    dashboard: Dashboard,
) -> None:
    [home] = [r for r in dashboard.app.routes if getattr(r, "name", None) == "home"]

    assert getattr(home.endpoint, "__nextron_personal_data__") is False
    assert [param.name for param in home.dependant.query_params] == []
    assert home.dependant.request_param_name == "request"


# Row: routes ------------------------------------------------------------------------------------


@personal_data(False)
def _endpoint() -> PlainTextResponse:
    return PlainTextResponse("x")


@pytest.mark.parametrize("methods", [["PUT"], ["GET", "POST"], ["HEAD"]])
def test_a_route_with_other_methods_is_refused_when_added(
    methods: list[str], dashboard: Dashboard
) -> None:
    with pytest.raises(ValueError):
        dashboard.app.add_api_route("/x", _endpoint, methods=methods)


def test_an_async_get_is_refused_when_added(dashboard: Dashboard) -> None:
    @personal_data(False)
    async def slow() -> PlainTextResponse:
        return PlainTextResponse("slow")

    with pytest.raises(TypeError):
        dashboard.app.add_api_route("/x", slow, methods=["GET"])


class AsyncCallable:
    """An endpoint object whose ``__call__`` is async: FastAPI would run it on the event loop,
    outside the read-only unit of work."""

    __nextron_personal_data__ = False

    async def __call__(self) -> PlainTextResponse:
        return PlainTextResponse("ran outside the unit")


class SyncCallable:
    __nextron_personal_data__ = False

    def __call__(self) -> PlainTextResponse:
        return PlainTextResponse("x")


@pytest.mark.parametrize("endpoint", [AsyncCallable(), SyncCallable()], ids=["async", "sync"])
def test_a_callable_object_get_is_refused_when_added(
    endpoint: object, dashboard: Dashboard
) -> None:
    with pytest.raises(TypeError, match="plain sync def"):
        dashboard.app.add_api_route("/x", endpoint, methods=["GET"])  # type: ignore[arg-type]

    assert "/x" not in [getattr(route, "path", None) for route in dashboard.app.routes]


class Handlers:
    @personal_data(False)
    def page(self) -> PlainTextResponse:
        return PlainTextResponse("from a method")


def test_a_sync_method_get_is_accepted_and_runs_in_a_read_only_unit(
    dashboard: Dashboard,
) -> None:
    units = dashboard.units
    assert isinstance(units, FakeUnits)
    dashboard.app.add_api_route("/method", Handlers().page, methods=["GET"])
    cookie = dashboard.open_session()
    before = len(units.opened)

    reply = dashboard.get("/method", cookie=cookie)

    assert reply.status == 200
    assert [read_only for read_only, _ in units.opened[before:]] == [True]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_plain_api_route_is_named_by_the_rule_check(method: str, dashboard: Dashboard) -> None:
    dashboard.app.router.routes.append(APIRoute("/plain", _endpoint, methods=[method]))

    [problem] = route_rule_problems(dashboard.app)

    assert problem.startswith(f"{method} /plain: ")


def test_a_plain_post_route_would_run_without_the_check(dashboard: Dashboard) -> None:
    """Why the rule check matters: a POST added behind the route class's back has no token
    check, so the guard's header checks are all that stop it."""
    ran: list[str] = []
    cookie = dashboard.open_session()

    @personal_data(False)
    def unchecked() -> PlainTextResponse:
        ran.append("ran")
        return PlainTextResponse("ran")

    dashboard.app.router.routes.append(APIRoute("/unchecked", unchecked, methods=["POST"]))

    assert dashboard.post("/unchecked", cookie=cookie).status == 200
    assert ran == ["ran"]
    assert route_rule_problems(dashboard.app) == [
        "POST /unchecked: a POST whose first dependency isn't require_csrf"
    ]


def test_the_route_class_is_per_units_object() -> None:
    first, second = FakeUnits(), FakeUnits()

    assert dashboard_route_class(first) is not dashboard_route_class(second)


def test_two_apps_read_through_their_own_units() -> None:
    first, second = FakeUnits(), FakeUnits()
    one = Dashboard(units=first)
    Dashboard(units=second)

    one.get("/static/app.css", cookie=one.open_session())

    assert first.opened and not second.opened


def test_the_handler_sees_the_request_after_the_body_was_checked(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    """``require_csrf`` reads a form body; the handler still gets the request."""
    cookie, token = session
    seen: list[str] = []

    @personal_data(False)
    def echo(request: Request) -> PlainTextResponse:
        seen.append(request.headers["content-type"])
        return PlainTextResponse("ok")

    dashboard.app.add_api_route("/t/echo", echo, methods=["POST"])

    reply = dashboard.post(
        "/t/echo",
        cookie=cookie,
        headers=[(b"content-type", FORM)],
        body=b"csrf_token=" + token.encode(),
    )

    assert reply.status == 200
    assert seen == [FORM.decode()]
