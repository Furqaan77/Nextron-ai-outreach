"""Untrusted text renders inert (AD-13, AD-18, NFR-19, UX-DR3, UX-DR4), on a fixture page served
by a dashboard app, and a GET handler that writes gets 500 (the Render and GET-writes rows).

The fixture page (``tests/fixtures/templates/untrusted_page.html``) extends the real
``base.html`` and puts hostile values through ``untrusted_block``, ``plain`` and ``safe_link``
next to a POST form with ``csrf_field()``. It is served by a test route that renders with the
app's own ``app.state.templates``, with the fixture folder put in front of its loader.
"""

import sqlite3
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from jinja2 import ChoiceLoader, Environment, FileSystemLoader
from markupsafe import Markup
from sqlalchemy import Engine, text

from nextron.db import eventlog
from nextron.db.engine import ReadOnlySqlError
from nextron.db.uow import UnitOfWorkError, UnitOfWorkFactory, current, use_case
from nextron.web.csrf import CSRF_SCOPE_KEY
from nextron.web.routing import personal_data
from nextron.web.sessions import SessionStore
from tests.asgi import DEFAULT_HOST, request
from tests.dashboard import FORM, Dashboard, body_hx_headers

FIXTURE_TEMPLATES = Path(__file__).resolve().parents[1] / "fixtures" / "templates"
ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"

HOSTILE_TEXTS: list[object] = [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    '<button hx-post="/t/save" hx-trigger="load">Click</button>',
    "<a href=\"javascript:alert(1)\">x</a>",
    Markup("<b>bold</b>"),
    "line one\n  line two & <i>three</i>",
]
NOT_LINKS = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " java\tscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "//evil.example/x",
    "http:\\\\evil.example",
    "https://x:99999",
    "http://",
    "https://exa mple.com/",
    "ftp://example.com/file",
    "http://[::1",
    # userinfo: the link would look like a trusted host
    "https://nextronai.com@evil.example/",
    "https://user:secret@example.com/",
    "https://@example.com/",
    # Unicode control, format, space and separator characters
    "https://example.com/\u202egnp.exe",  # right-to-left override (Cf)
    "https://exa\u200bmple.com/",  # zero-width space (Cf)
    "https://example.com/\u0085x",  # next line (Cc)
    "https://example.com/\u2028x",  # line separator (Zl)
    "https://example.com/\u2029x",  # paragraph separator (Zp)
    "https://example.com/\u00a0x",  # no-break space (Zs)
    "https://example.com/x\u3000",  # ideographic space (Zs), not stripped as ASCII whitespace
]
LINKS = {  # URL as given -> the href it renders
    "https://example.com/a?b=1&c=<2>": "https://example.com/a?b=1&c=<2>",
    "https://example.com/@user/caf\u00e9": "https://example.com/@user/caf\u00e9",
    "http://example.org": "http://example.org",
    "HTTPS://Example.NET/Path": "HTTPS://Example.NET/Path",
    "  https://example.com/padded \n": "https://example.com/padded",
    'https://example.com/"onmouseover="alert(1)': 'https://example.com/"onmouseover="alert(1)',
}


class Parsed(HTMLParser):
    """Every tag with its attributes, the text of each element with an id or a class, and the
    links."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.open: list[tuple[str, dict[str, str | None], list[str]]] = []
        self.texts: list[tuple[str, dict[str, str | None], str]] = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags.append((tag, values))
        if tag not in ("input", "meta", "link", "br", "img"):
            self.open.append((tag, values, []))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == tag:
                name, attrs, parts = self.open.pop(index)
                text = "".join(parts)
                self.texts.append((name, attrs, text))
                if self.open:
                    self.open[-1][2].append(text)
                return

    def handle_data(self, data: str) -> None:
        if self.open:
            self.open[-1][2].append(data)

    def find(self, tag: str, **attrs: str) -> list[dict[str, str | None]]:
        return [
            found
            for name, found in self.tags
            if name == tag and all(found.get(key) == value for key, value in attrs.items())
        ]

    def text_of(self, tag: str, **attrs: str) -> list[str]:
        return [
            text
            for name, found, text in self.texts
            if name == tag and all(found.get(key) == value for key, value in attrs.items())
        ]


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[SessionStore, str]]:
    """Each session created, with its store, so a test can read the session's real token."""
    created: list[tuple[SessionStore, str]] = []
    real = SessionStore.create

    def spy(self: SessionStore) -> str:
        created.append((self, real(self)))
        return created[-1][1]

    monkeypatch.setattr(SessionStore, "create", spy)
    return created


def serve_fixture_page(dashboard: Dashboard, saved: list[str]) -> None:
    """``GET /t/untrusted`` renders the fixture page; ``POST /t/save`` records each save."""
    templates: Environment = dashboard.app.state.templates
    assert templates.loader is not None
    templates.loader = ChoiceLoader([FileSystemLoader(FIXTURE_TEMPLATES), templates.loader])

    @personal_data(True)
    def untrusted(request: Request) -> HTMLResponse:
        page = templates.get_template("untrusted_page.html").render(
            nav=[],
            csrf_token=request.scope[CSRF_SCOPE_KEY],
            texts=HOSTILE_TEXTS,
            sender='<script>alert("sender")</script>',
            said=Markup('<img src=x onerror="alert(2)">'),
            urls=[*NOT_LINKS, *LINKS],
            titled_url="https://example.com/",
            titled_text='<b onclick="go()">Example</b>',
            nothing=None,
        )
        return HTMLResponse(page)

    @personal_data(False)
    def save() -> PlainTextResponse:
        saved.append("saved")
        return PlainTextResponse("saved")

    dashboard.app.add_api_route("/t/untrusted", untrusted, methods=["GET"])
    dashboard.app.add_api_route("/t/save", save, methods=["POST"])


@pytest.fixture
def rendered(sessions: list[tuple[SessionStore, str]]) -> dict[str, Any]:
    dashboard = Dashboard()
    saved: list[str] = []
    serve_fixture_page(dashboard, saved)
    cookie = dashboard.open_session()
    store, session_token = sessions[-1]
    reply = dashboard.get("/t/untrusted", cookie=cookie)
    assert reply.status == 200, reply
    return {
        "dashboard": dashboard,
        "cookie": cookie,
        "token": store.csrf_token(session_token),
        "html": reply.text,
        "page": Parsed(reply.text),
        "saved": saved,
    }


# Row: Render ------------------------------------------------------------------------------------


def test_hostile_markup_renders_as_escaped_text(rendered: dict[str, Any]) -> None:
    page: Parsed = rendered["page"]
    html: str = rendered["html"]

    assert [attrs for tag, attrs in page.tags if tag == "script"] == [
        {"src": "/static/htmx-2.0.11.min.js", "defer": None}
    ]
    assert not page.find("img") and not page.find("b") and not page.find("i")
    assert not [attrs for _, attrs in page.tags if any(k.startswith("on") for k in attrs)]
    hx_tags = [tag for tag, attrs in page.tags if "hx-post" in attrs or "hx-trigger" in attrs]
    # only the page's own form posts through HTMX; the layout's notification list loads once
    assert hx_tags == ["div", "form"]
    assert [attrs for tag, attrs in page.tags if "hx-trigger" in attrs] == [
        {"id": "notification-list", "hx-get": "/notifications", "hx-trigger": "load, call-outcome-saved from:body"}
    ]
    assert [attrs for tag, attrs in page.tags if tag == "button"] == [{"type": "submit"}]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert" not in html and "<b>" not in html


def test_each_untrusted_block_shows_its_text_exactly_inside_hx_disable(
    rendered: dict[str, Any],
) -> None:
    page: Parsed = rendered["page"]

    blocks = page.find("div", **{"class": "untrusted"})
    assert len(blocks) == len(HOSTILE_TEXTS) + 1
    assert all(block == {"class": "untrusted", "hx-disable": None} for block in blocks)
    texts = page.text_of("div", **{"class": "untrusted"})
    assert texts[: len(HOSTILE_TEXTS)] == [str(value) for value in HOSTILE_TEXTS]


def test_a_called_untrusted_block_wraps_its_body_and_plain_escapes_markup(
    rendered: dict[str, Any],
) -> None:
    page: Parsed = rendered["page"]

    [called] = page.text_of("div", **{"class": "untrusted"})[len(HOSTILE_TEXTS) :]
    assert called == 'From <script>alert("sender")</script><img src=x onerror="alert(2)">'
    assert page.text_of("p", **{"class": "said"}) == ['<img src=x onerror="alert(2)">']
    assert page.text_of("p", id="nothing") == ["[]"]


def test_only_http_and_https_links_render_each_with_noopener_noreferrer(
    rendered: dict[str, Any],
) -> None:
    page: Parsed = rendered["page"]

    links = [attrs for tag, attrs in page.tags if tag == "a" and attrs.get("class") != "brand"]
    assert [link["href"] for link in links] == [*LINKS.values(), "https://example.com/"]
    assert all(set(link) == {"href", "rel"} for link in links)
    assert all(link["rel"] == "noopener noreferrer" for link in links)
    items = page.text_of("li")
    assert items[: len(NOT_LINKS)] == NOT_LINKS  # shown as plain text
    assert items[len(NOT_LINKS) :] == list(LINKS.values())  # the link text is the stripped URL
    assert page.text_of("a", href="https://example.com/") == ['<b onclick="go()">Example</b>']


def test_the_page_carries_the_sessions_csrf_token_for_htmx_and_the_form(
    rendered: dict[str, Any],
) -> None:
    page: Parsed = rendered["page"]
    token = rendered["token"]

    assert isinstance(token, str) and len(token) == 43
    assert body_hx_headers(rendered["html"]) == {"X-CSRF-Token": token}
    [form] = page.find("form")
    assert form == {"method": "post", "action": "/t/save", "hx-post": "/t/save"}
    assert page.find("input") == [{"type": "hidden", "name": "csrf_token", "value": token}]


def test_the_forms_hidden_token_makes_a_checked_post(rendered: dict[str, Any]) -> None:
    dashboard: Dashboard = rendered["dashboard"]
    [field] = rendered["page"].find("input", name="csrf_token")
    body = b"csrf_token=" + str(field["value"]).encode()

    reply = dashboard.post(
        "/t/save", cookie=rendered["cookie"], headers=[(b"content-type", FORM)], body=body
    )

    assert reply.status == 200
    assert rendered["saved"] == ["saved"]


def test_csrf_field_without_a_token_raises() -> None:
    templates: Environment = Dashboard().app.state.templates

    with pytest.raises(RuntimeError, match="csrf_token"):
        templates.from_string("{{ csrf_field() }}").render()


# Row: GET writes --------------------------------------------------------------------------------


def events_count(engine: Engine) -> int:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        [(count,)] = reader.execute("SELECT count(*) FROM events").fetchall()
        return int(count)
    finally:
        reader.close()


def _append_event() -> None:
    eventlog.append(
        entity="events",
        entity_id=ULID,
        from_status=None,
        to_status="created",
        cause="test",
        cause_ref=None,
        actor="operator",
        at=datetime(2026, 11, 17, 9, 0, tzinfo=UTC),
    )


def _insert() -> None:
    current().session.execute(
        text(
            "INSERT INTO events (id, entity, entity_id, to_status, cause, actor, at) "
            "VALUES (:id, 'events', :id, 'x', 'x', 'operator', '2026-11-17T09:00:00.000000Z')"
        ),
        {"id": ULID},
    )


def _query_only_off() -> None:
    current().session.execute(text("PRAGMA query_only=OFF"))


@use_case
def _record_event() -> None:
    with current().transaction():
        _append_event()


def _transaction() -> None:
    with current().transaction():
        _append_event()


WRITES = {
    "eventlog_append": (_append_event, ReadOnlySqlError),
    "insert": (_insert, ReadOnlySqlError),
    "query_only_off": (_query_only_off, ReadOnlySqlError),
    "use_case": (_record_event, UnitOfWorkError),
    "transaction": (_transaction, UnitOfWorkError),
}


@pytest.mark.parametrize("case", sorted(WRITES))
def test_a_get_that_writes_gets_500_and_writes_nothing(
    case: str, units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    write, error = WRITES[case]
    dashboard = Dashboard(units=units)

    @personal_data(False)
    def writes() -> PlainTextResponse:
        write()
        return PlainTextResponse("wrote")

    dashboard.app.add_api_route("/t/writes", writes, methods=["GET"])
    cookie = dashboard.open_session()

    reply = request(
        dashboard.app,
        "/t/writes",
        headers=[(b"host", DEFAULT_HOST), (b"cookie", cookie)],
        catch=True,
    )

    assert reply.status == 500
    assert reply.body == b"Internal error"
    assert isinstance(reply.error, error)
    assert events_count(db_engine) == 0
    with units.open():  # the pooled connection then writes normally
        _record_event()
    assert events_count(db_engine) == 1


def test_a_get_that_reads_through_real_units_works(
    units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    dashboard = Dashboard(units=units)

    @personal_data(False)
    def reads() -> PlainTextResponse:
        count = current().session.execute(text("SELECT count(*) FROM events")).scalar_one()
        return PlainTextResponse(f"{count} events; read_only={current().read_only}")

    dashboard.app.add_api_route("/t/reads", reads, methods=["GET"])
    cookie = dashboard.open_session()

    reply = dashboard.get("/t/reads", cookie=cookie)

    assert reply.status == 200
    assert reply.body == b"0 events; read_only=True"
