"""The Signal Inbox page (``GET /signals``, ``POST /signals`` and ``POST
/signals/<id>/reprocess``; Story 2.16, UX-DR19) through ``Dashboard(units=...)`` on a temporary
database at head with the real subscriber registry and the shipped Segments: the form, a
submission, refusals by field with what was typed kept, the list (escaped text inside
``hx-disable``, the never-fetched note, the Lead link, what a flagged item is missing), a
reprocess, malformed forms, and the Lead page's Evidence source label."""

from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from nextron.domain.signal_inbox_item import NEVER_FETCHED_NOTE, TEXT_REQUIRED, URL_INVALID
from nextron.web.forms import MALFORMED_FORM
from tests.asgi import Reply
from tests.dashboard import Dashboard, form
from tests.fakes.clock import FrozenClock

T = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
FORM = b"application/x-www-form-urlencoded"
PAGE = "/signals"
HOSTILE = '<script>alert(1)</script><b hx-post="/x">re-key</b>'
POST_URL = "https://www.linkedin.com/posts/dana-cole-1"
SITE = "northgate-supply.co.uk"


class Parsed(HTMLParser):
    """The panel: forms, inputs and textareas, untrusted blocks and the page text."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, str | None]] = []
        self.fields: dict[str, dict[str, str | None]] = {}
        self.untrusted: list[str] = []
        self.links: list[str] = []
        self.texts: list[str] = []
        self.scripts = 0
        self._depth = 0
        self._chunk: list[str] = []
        self._textarea: str | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "form":
            self.forms.append(found)
        elif tag in ("input", "select", "textarea") and found.get("name"):
            self.fields.setdefault(str(found["name"]), found)
            if tag == "textarea":
                self._textarea = str(found["name"])
                self.fields[self._textarea]["value"] = ""
        elif tag == "a" and found.get("href"):
            self.links.append(str(found["href"]))
        elif tag == "script":
            self.scripts += 1
        if tag == "div":
            if self._depth:
                self._depth += 1
            elif "hx-disable" in found and found.get("class") == "untrusted":
                self._depth = 1
                self._chunk = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea":
            self._textarea = None
        if tag == "div" and self._depth:
            self._depth -= 1
            if not self._depth:
                self.untrusted.append("".join(self._chunk))

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self.fields[self._textarea]["value"] = (
                str(self.fields[self._textarea]["value"]) + data
            )
        if self._depth:
            self._chunk.append(data)
        self.texts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.texts).split())


@pytest.fixture
def real_units(db_engine: Engine) -> UnitOfWorkFactory:
    clock = FrozenClock(T)
    found = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    markets = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
    with found.open():
        Setup(clock).sync_segments(segments)
    with found.open():
        Setup(clock).sync_markets(markets)
    return found


@pytest.fixture
def dashboard(real_units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=real_units, clock=FrozenClock(T))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def post(
    dashboard: Dashboard, session: tuple[bytes, str], path: str = PAGE, **fields: str
) -> Reply:
    cookie, token = session
    return dashboard.post(
        path,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, **fields),
    )


def sql(engine: Engine, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        result = connection.exec_driver_sql(statement, params)
        rows = [tuple(row) for row in result.all()] if result.returns_rows else []
        connection.commit()
    return rows


def test_the_page_shows_five_item_fieldsets_and_the_nav_link(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get(PAGE, cookie=session[0])

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    parsed = Parsed(reply.text)
    [submit] = [found for found in parsed.forms if found.get("hx-post") == PAGE]
    assert submit["method"] == "post"
    for number in range(1, 6):
        for name in ("url", "text", "author", "company"):
            assert f"{name}_{number}" in parsed.fields
    assert "csrf_token" in parsed.fields
    assert PAGE in parsed.links  # the nav's Signal Inbox entry
    assert "No items yet." in parsed.text()


def test_without_a_session_the_page_is_locked(dashboard: Dashboard) -> None:
    assert dashboard.get(PAGE).status in (401, 403, 303)


def test_a_submission_stores_the_items_and_lists_them_escaped(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    reply = post(
        dashboard,
        session,
        url_1=POST_URL,
        text_1=HOSTILE,
        author_1="Dana Cole",
        url_3=f"https://{SITE}/news",
        text_3="We opened a new warehouse.",
        company_3="Northgate Supply",
    )

    assert reply.status == 200
    parsed = Parsed(reply.text)
    assert "Submitted." in parsed.text()
    rows = sql(
        db_engine, "SELECT url, text, author, company, status FROM signal_inbox_items ORDER BY rowid"
    )
    assert rows == [
        (POST_URL, HOSTILE, "Dana Cole", None, "pending"),
        (f"https://{SITE}/news", "We opened a new warehouse.", None, "Northgate Supply", "pending"),
    ]
    assert sql(db_engine, "SELECT type, lane FROM jobs ORDER BY rowid") == [
        ("process_signal_item", "interactive"),
        ("process_signal_item", "interactive"),
    ]
    assert HOSTILE in parsed.untrusted  # escaped text, inside hx-disable
    assert POST_URL in parsed.untrusted
    assert parsed.scripts == 0
    assert "<script>" not in reply.text
    # only the LinkedIn URL is on the FR-49 denylist
    assert parsed.text().count(NEVER_FETCHED_NOTE) == 1
    assert parsed.text().count("Pending") == 2
    # a fresh form after a submission
    assert parsed.fields["url_1"].get("value") == ""


def test_a_refused_submission_keeps_what_was_typed_and_stores_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    reply = post(
        dashboard,
        session,
        url_1="https://ok.example.com/1",
        text_1="Fine text",
        url_2="not a url",
        author_2="Dana",
    )

    assert reply.status == 200
    parsed = Parsed(reply.text)
    assert URL_INVALID in parsed.text()
    assert TEXT_REQUIRED in parsed.text()
    assert parsed.fields["url_2"]["aria-invalid"] == "true"
    assert parsed.fields["url_2"]["value"] == "not a url"
    assert parsed.fields["text_1"]["value"] == "Fine text"
    assert sql(db_engine, "SELECT COUNT(*) FROM signal_inbox_items") == [(0,)]
    assert sql(db_engine, "SELECT COUNT(*) FROM jobs") == [(0,)]


@pytest.mark.parametrize(
    "fields",
    [{"url_6": "https://a.example.com", "text_6": "x"}, {"other": "x"}],
)
def test_a_field_other_than_the_forms_own_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], fields: dict[str, str]
) -> None:
    reply = post(dashboard, session, **fields)

    assert reply.status == 400
    assert reply.text == MALFORMED_FORM


def test_the_csrf_token_is_required(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    reply = dashboard.post(
        PAGE,
        cookie=session[0],
        headers=[(b"content-type", FORM)],
        body=form(url_1=POST_URL, text_1="x"),
    )

    assert reply.status == 403


def flag(engine: Engine, item_id: str) -> None:
    sql(
        engine,
        "UPDATE signal_inbox_items SET status = 'flagged', flag_reason = 'organization_unknown', "
        "parsed_segment = 'ops_automation', parsed_signal_type = 'ops_rekeying_post', "
        "parsed_quote = 'We re-key orders by hand.', extracted_by = 'ai:signal_inbox_parse@v1' "
        "WHERE id = ?",
        (item_id,),
    )


def test_a_flagged_item_shows_what_is_missing_and_reprocesses_to_a_lead(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    post(dashboard, session, url_1=POST_URL, text_1="We re-key orders by hand.")
    [(item_id,)] = sql(db_engine, "SELECT id FROM signal_inbox_items")
    flag(db_engine, item_id)

    page = Parsed(dashboard.get(PAGE, cookie=session[0]).text)
    assert "What is missing: No organization website was identified" in page.text()
    path = f"/signals/{item_id}/reprocess"
    [reprocess] = [found for found in page.forms if found.get("hx-post") == path]
    assert reprocess["method"] == "post"

    reply = post(dashboard, session, path, lead="", website=SITE, segment="ops_automation")

    assert reply.status == 200
    parsed = Parsed(reply.text)
    assert "Reprocessed: the item is linked to its Lead." in parsed.text()
    assert "Evidence" not in parsed.text()  # no quote may have been stored
    [(lead_id, status)] = sql(db_engine, "SELECT lead_id, status FROM signal_inbox_items")
    assert status == "processed"
    assert f"/leads/{lead_id}" in parsed.links
    # the Lead page names the Evidence's source
    lead_page = Parsed(dashboard.get(f"/leads/{lead_id}", cookie=session[0]).text)
    assert "Signal Inbox (manual)" in lead_page.text()


def test_a_refused_reprocess_answers_its_notice(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    post(dashboard, session, url_1=POST_URL, text_1="We re-key orders by hand.")
    [(item_id,)] = sql(db_engine, "SELECT id FROM signal_inbox_items")
    flag(db_engine, item_id)

    reply = post(
        dashboard, session, f"/signals/{item_id}/reprocess", lead="", website="", segment=""
    )

    assert reply.status == 200
    assert "Enter either an existing Lead, or a website and a Segment." in Parsed(reply.text).text()
    assert sql(db_engine, "SELECT status FROM signal_inbox_items") == [("flagged",)]


@pytest.mark.parametrize(
    ("path", "fields"),
    [
        ("/signals/not-an-id/reprocess", {"lead": "", "website": "", "segment": ""}),
        ("/signals/01ARZ3NDEKTSV4RRFFQ69G5FAV/reprocess", {"lead": ""}),
    ],
)
def test_a_malformed_reprocess_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], path: str, fields: dict[str, str]
) -> None:
    reply = post(dashboard, session, path, **fields)

    assert reply.status == 400


# Story 4.16: the "Draft Step 1 when ready" checkbox ---------------------------------------------


def test_the_draft_when_ready_box_is_ticked_by_default(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    parsed = Parsed(dashboard.get(PAGE, cookie=session[0]).text)

    box = parsed.fields["draft_when_ready"]
    assert (box["type"], box["value"]) == ("checkbox", "on")
    assert "checked" in box
    assert "Draft Step 1 when ready" in parsed.text()


@pytest.mark.parametrize(("ticked", "stored"), [(True, 1), (False, 0)])
def test_the_box_applies_to_every_item_of_the_submission(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    ticked: bool,
    stored: int,
) -> None:
    box = {"draft_when_ready": "on"} if ticked else {}
    reply = post(
        dashboard,
        session,
        url_1=POST_URL,
        text_1="We rekey orders.",
        url_2=f"https://{SITE}/news",
        text_2="We opened a new warehouse.",
        **box,
    )

    assert reply.status == 200
    assert sql(
        db_engine,
        "SELECT draft_when_ready, draft_outcome, draft_decided_at FROM signal_inbox_items",
    ) == [(stored, None, None), (stored, None, None)]
    assert "checked" in Parsed(reply.text).fields["draft_when_ready"]  # a fresh form: ticked


@pytest.mark.parametrize("ticked", [True, False])
def test_a_refused_submission_keeps_the_box_as_it_was(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine, ticked: bool
) -> None:
    box = {"draft_when_ready": "on"} if ticked else {}
    reply = post(dashboard, session, url_1="not a url", text_1="Text", **box)

    assert reply.status == 200
    assert ("checked" in Parsed(reply.text).fields["draft_when_ready"]) is ticked
    assert sql(db_engine, "SELECT COUNT(*) FROM signal_inbox_items") == [(0,)]


@pytest.mark.parametrize("value", ["yes", "", "ON"])
def test_any_other_box_value_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine, value: str
) -> None:
    reply = post(dashboard, session, url_1=POST_URL, text_1="x", draft_when_ready=value)

    assert reply.status == 400
    assert reply.text == MALFORMED_FORM
    assert sql(db_engine, "SELECT COUNT(*) FROM signal_inbox_items") == [(0,)]


def test_the_box_given_twice_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    cookie, token = session
    reply = dashboard.post(
        PAGE,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, url_1=POST_URL, text_1="x", draft_when_ready="on")
        + b"&draft_when_ready=on",
    )

    assert reply.status == 400
    assert sql(db_engine, "SELECT COUNT(*) FROM signal_inbox_items") == [(0,)]
