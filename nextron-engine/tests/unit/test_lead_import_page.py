"""The CSV import page (``GET /leads/import`` and ``POST /leads/import``, FR-59, UX-DR20, AD-13)
through ``Dashboard(units=...)`` on a temporary database at head with the real subscriber
registry, the HTML parsed: the form, a successful import's summary, refusals by field (the
origin and default kept, or reset after a body over the limit), a stopped import, escaping of
every CSV text, the field rules, ``no-store``, CSRF and the home page's link. With the shipped
Markets seeded: the "Blocked by Market" count and table (Story 3.2), labels escaped. With a
domain on the Suppression List (Story 3.4): the "Blocked: suppressed domain" count, its table
after the Market table (labels escaped) and a blocked Contact's note."""

from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import ImportStopped, ImportSummary, NotedRow
from nextron.app.setup.api import Setup
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import suppression_key
from nextron.domain.lead_import import (
    FILE_TOO_LARGE,
    NOTE_BAD_EMAIL,
    NOTE_CONTACT_SUPPRESSED,
    ORIGIN_LENGTH,
    REASON_DENIED,
    REASON_SUPPRESSED,
    BlockedRow,
    RejectedRow,
    SuppressedRow,
)
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM, MAX_UPLOAD_BODY
from tests.asgi import Reply
from tests.dashboard import Dashboard, upload
from tests.fakes.clock import FrozenClock

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
ORIGIN = "Law Society directory, Manchester"
HEADER = "Organization name,Website,Country"
HOSTILE = '<script>alert(1)</script><b hx-post="/x">'


def csv_bytes(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


class Parsed(HTMLParser):
    """The import block: its inputs and options, field errors, notices, tables and text."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, str | None]] = []
        self.inputs: dict[str, dict[str, str | None]] = {}
        self.options: list[tuple[str | None, str, bool]] = []
        self.errors: dict[str, str] = {}
        self.tables: list[list[list[str]]] = []
        self.untrusted: list[tuple[dict[str, str | None], str]] = []
        self.texts: list[str] = []
        self._option: tuple[str | None, bool] | None = None
        self._error: str | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._untrusted: list[Any] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "form":
            self.forms.append(found)
        elif tag in ("input", "select") and found.get("name"):
            self.inputs[str(found["name"])] = found
        elif tag == "option":
            self._option = (found.get("value"), "selected" in found)
        elif tag == "p" and (found.get("id") or "").startswith("error-"):
            self._error = str(found["id"]).removeprefix("error-")
            self.errors[self._error] = ""
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "div" and "untrusted" in (found.get("class") or ""):
            self._untrusted = [found, ""]

    def handle_endtag(self, tag: str) -> None:
        if tag == "option":
            self._option = None
        elif tag == "p":
            self._error = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None
        elif tag == "div" and self._untrusted is not None:
            self.untrusted.append((self._untrusted[0], self._untrusted[1]))
            self._untrusted = None

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self.options.append((self._option[0], data.strip(), self._option[1]))
        if self._error is not None:
            self.errors[self._error] += data
        if self._cell is not None:
            self._cell.append(data)
        if self._untrusted is not None:
            self._untrusted[1] += data
        if data.strip():
            self.texts.append(" ".join(data.split()))

    @property
    def text(self) -> str:
        return " ".join(self.texts)


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
def cookie(dashboard: Dashboard) -> bytes:
    return dashboard.open_session()


def post(
    dashboard: Dashboard,
    cookie: bytes,
    file: bytes | None,
    *,
    token: str | None = "",
    origin: str = ORIGIN,
    default_segment: str = "law_private_ai",
    **extra: str,
) -> Reply:
    chosen = dashboard.csrf_token(cookie) if token == "" else token
    texts = {"csrf_token": chosen or "", "origin": origin, "default_segment": default_segment}
    data, headers = upload(file, **texts, **extra)
    return dashboard.post("/leads/import", cookie=cookie, token=chosen, headers=headers, body=data)


def count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one())


# GET -------------------------------------------------------------------------------------------


def test_the_page_shows_the_form(dashboard: Dashboard, cookie: bytes) -> None:
    reply = dashboard.get("/leads/import", cookie=cookie)

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    page = Parsed(reply.text)
    [form] = page.forms
    assert form["method"] == "post"
    assert form["enctype"] == "multipart/form-data"
    assert form["hx-post"] == "/leads/import"
    assert form["hx-encoding"] == "multipart/form-data"
    assert form["hx-disabled-elt"] == "find button"
    assert page.inputs["csrf_token"]["type"] == "hidden"
    origin = page.inputs["origin"]
    assert "required" in origin
    assert (origin["minlength"], origin["maxlength"]) == ("3", "200")
    assert page.inputs["file"]["type"] == "file"
    assert page.inputs["file"]["accept"] == ".csv,text/csv"
    assert page.options == [
        ("", "No default", True),
        ("law_private_ai", "Law-PrivateAI", False),
        ("ops_automation", "Ops-Automation", False),
    ]
    assert "For example" in page.text
    assert page.errors == {}


def test_home_links_to_the_import(dashboard: Dashboard, cookie: bytes) -> None:
    reply = dashboard.get("/", cookie=cookie)

    assert 'href="/leads/import"' in reply.text


def test_without_a_session_the_page_is_locked(dashboard: Dashboard) -> None:
    assert dashboard.get("/leads/import").status == 401


# POST ------------------------------------------------------------------------------------------


def test_a_successful_import_shows_the_summary_above_a_fresh_form(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    data = csv_bytes(
        HEADER + ",Contact name,Email",
        "Smith Law,https://www.smith-law.co.uk/team,UK,Jane Doe,jane@smith-law.co.uk",
        "Smith Law LLP,smith-law.co.uk,GB,,",
        "LinkedIn,https://www.linkedin.com/company/x,GB,,",
        'Noted,noted.com,GB,Ann,"a,b@noted.com"',
    )

    reply = post(dashboard, cookie, data)

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    page = Parsed(reply.text)
    assert "Import finished" in page.text
    assert "Imported: 2. Merged: 1. Rejected: 1." in page.text
    rejected, noted = page.tables
    assert rejected == [["Row", "Organization", "Reason"], ["4", "LinkedIn", REASON_DENIED]]
    assert noted == [["Row", "Organization", "Notes"], ["5", "Noted", NOTE_BAD_EMAIL]]
    assert page.inputs["origin"]["value"] == ""  # a fresh form
    assert page.errors == {}
    assert count(db_engine, "leads") == 2
    assert count(db_engine, "contacts") == 2


def test_rows_blocked_by_market_show_their_count_and_table(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    data = csv_bytes(
        HEADER + ",Contact name",
        "Maple Law,maple.ca,CA,Ann",
        "LinkedIn,https://www.linkedin.com/company/x,GB,",
        "Berg Recht,berg.de,de,",
        "Smith Law,smith.co.uk,GB,Jo",
    )

    page = Parsed(post(dashboard, cookie, data).text)

    assert "Imported: 1. Merged: 0. Rejected: 1. Blocked by Market: 2." in page.text
    rejected, blocked = page.tables
    assert rejected == [["Row", "Organization", "Reason"], ["3", "LinkedIn", REASON_DENIED]]
    assert blocked == [
        ["Row", "Organization", "Reason"],
        ["2", "Maple Law", "Country CA isn't an enabled Market"],
        ["4", "Berg Recht", "Country DE isn't an enabled Market"],
    ]
    assert "Rows blocked by Market" in page.text
    assert count(db_engine, "leads") == 1
    assert count(db_engine, "contacts") == 1


def test_no_blocked_row_shows_a_zero_count_and_no_table(
    dashboard: Dashboard, cookie: bytes
) -> None:
    page = Parsed(post(dashboard, cookie, csv_bytes(HEADER, "A,a.com,GB")).text)

    assert "Rejected: 0. Blocked by Market: 0. Blocked: suppressed domain: 0." in page.text
    assert "Rows blocked by Market" not in page.text
    assert "Rows blocked: suppressed domain" not in page.text
    assert page.tables == []


def test_a_blocked_rows_label_renders_escaped(dashboard: Dashboard, cookie: bytes) -> None:
    data = csv_bytes(HEADER, f'"{HOSTILE.replace(chr(34), chr(34) * 2)}",maple.ca,CA')

    reply = post(dashboard, cookie, data)

    assert "<script>" not in reply.text
    assert '<b hx-post="/x">' not in reply.text
    page = Parsed(reply.text)
    assert [text for _, text in page.untrusted] == [HOSTILE]
    assert all("hx-disable" in attrs for attrs, _ in page.untrusted)
    [blocked] = page.tables
    assert blocked[1][2] == "Country CA isn't an enabled Market"


def suppress_domain(units: UnitOfWorkFactory, domain: str) -> None:
    """An ``active`` domain entry on the Suppression List (Story 3.4)."""
    key = suppression_key("domain", domain)

    @use_case
    def store() -> None:
        with current().transaction():
            entries_repo.insert(
                kind=str(key.kind),
                value_hash=key.value_hash,
                domain_plain=key.domain_plain,
                source="operator",
                cause_ref=None,
                created_at=T,
            )

    with units.open():
        store()


def test_rows_on_a_suppressed_domain_show_their_count_and_table_after_the_markets(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine, real_units: UnitOfWorkFactory
) -> None:
    suppress_domain(real_units, "gone-firm.co.uk")
    data = csv_bytes(
        HEADER + ",Contact name",
        "Maple Law,maple.ca,CA,Ann",
        f'"{HOSTILE.replace(chr(34), chr(34) * 2)}",gone-firm.co.uk,GB,Jo',
        "Smith Law,smith.co.uk,GB,Jo",
        "Gone Again,www.gone-firm.co.uk,GB,",
    )

    reply = post(dashboard, cookie, data)

    assert "<script>" not in reply.text
    page = Parsed(reply.text)
    assert (
        "Imported: 1. Merged: 0. Rejected: 0. Blocked by Market: 1. "
        "Blocked: suppressed domain: 2." in page.text
    )
    market, suppressed = page.tables
    assert market[1] == ["2", "Maple Law", "Country CA isn't an enabled Market"]
    assert suppressed == [
        ["Row", "Organization", "Reason"],
        ["3", HOSTILE, REASON_SUPPRESSED],
        ["5", "Gone Again", REASON_SUPPRESSED],
    ]
    assert "Rows blocked: suppressed domain" in page.text
    assert [text for _, text in page.untrusted] == ["Maple Law", HOSTILE, "Gone Again"]
    assert all("hx-disable" in attrs for attrs, _ in page.untrusted)
    assert count(db_engine, "leads") == 1
    assert count(db_engine, "contacts") == 1


def test_a_blocked_contact_shows_its_note(
    dashboard: Dashboard, cookie: bytes, real_units: UnitOfWorkFactory
) -> None:
    post(dashboard, cookie, csv_bytes(HEADER, "Smith Law,smith.co.uk,GB"))
    suppress_domain(real_units, "smith.co.uk")

    page = Parsed(
        post(dashboard, cookie, csv_bytes(HEADER + ",Contact name", "Smith,smith.co.uk,GB,Jo")).text
    )

    assert "Imported: 0. Merged: 1." in page.text
    [noted] = page.tables
    assert noted[1] == ["2", "Smith", NOTE_CONTACT_SUPPRESSED]


def test_every_csv_text_renders_escaped(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    data = csv_bytes(
        HEADER + ",Contact name,Email",
        f'"{HOSTILE.replace(chr(34), chr(34) * 2)}",linkedin.com/in/x,GB,,',
        f'"{HOSTILE.replace(chr(34), chr(34) * 2)}",ok.com,GB,Ann,"a,b@ok.com"',
    )

    reply = post(dashboard, cookie, data)

    assert "<script>" not in reply.text
    assert '<b hx-post="/x">' not in reply.text
    assert "&lt;script&gt;" in reply.text
    page = Parsed(reply.text)
    assert [text for _, text in page.untrusted] == [HOSTILE, HOSTILE]
    assert all("hx-disable" in attrs for attrs, _ in page.untrusted)


def test_a_refusal_shows_its_error_by_its_field_and_keeps_what_was_typed(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    data = csv_bytes(HEADER, "A,a.com,GB")

    reply = post(dashboard, cookie, data, origin="ab", default_segment="ops_automation")

    assert reply.status == 200
    page = Parsed(reply.text)
    assert page.errors == {"origin": ORIGIN_LENGTH}
    assert page.inputs["origin"]["value"] == "ab"
    assert page.inputs["origin"]["aria-invalid"] == "true"
    assert ("ops_automation", "Ops-Automation", True) in page.options
    assert "Import finished" not in page.text
    assert count(db_engine, "leads") == 0


@pytest.mark.parametrize(
    ("file", "default", "field", "reason"),
    [
        (b"", "law_private_ai", "file", "The file is empty."),
        (b"\xff\xfe,", "law_private_ai", "file", "The file isn't UTF-8 text."),
        (csv_bytes("Name,Country", "A,GB"), "law_private_ai", "file", "The header has no "),
        (csv_bytes(HEADER, "A,a.com,GB"), "no_such_segment", "default_segment", "That Segment"),
    ],
)
def test_file_and_segment_refusals(
    dashboard: Dashboard,
    cookie: bytes,
    db_engine: Engine,
    file: bytes,
    default: str,
    field: str,
    reason: str,
) -> None:
    page = Parsed(post(dashboard, cookie, file, default_segment=default).text)

    assert list(page.errors) == [field]
    assert page.errors[field].startswith(reason)
    assert page.inputs["origin"]["value"] == ORIGIN
    assert count(db_engine, "leads") == 0


def test_a_body_over_the_limit_resets_the_form(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    reply = post(dashboard, cookie, b"x" * (MAX_UPLOAD_BODY + 1), default_segment="ops_automation")

    assert reply.status == 200
    page = Parsed(reply.text)
    assert page.errors == {"file": FILE_TOO_LARGE}
    assert page.inputs["origin"]["value"] == ""
    assert ("", "No default", True) in page.options
    assert count(db_engine, "leads") == 0


def test_a_stopped_import_shows_its_summary_and_the_stop_notice(
    dashboard: Dashboard, cookie: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = ImportSummary(
        200,
        0,
        (RejectedRow(9, "Bad", REASON_DENIED),),
        (NotedRow(3, "N", (NOTE_BAD_EMAIL,)),),
        (BlockedRow(4, "Maple", "CA"),),
        (SuppressedRow(6, "Gone"),),
    )

    def stop(request: object) -> ImportSummary:
        raise ImportStopped(summary, 202)

    monkeypatch.setattr(dashboard.lead_imports, "run", stop)

    data = csv_bytes(HEADER, "A,a.com,GB")

    page = Parsed(post(dashboard, cookie, data, default_segment="ops_automation").text)

    assert "Import stopped" in page.text
    assert "The import stopped at row 202" in page.text
    assert (
        "Imported: 200. Merged: 0. Rejected: 1. Blocked by Market: 1. "
        "Blocked: suppressed domain: 1." in page.text
    )
    assert page.tables[0][1] == ["9", "Bad", REASON_DENIED]
    assert page.tables[1][1] == ["4", "Maple", "Country CA isn't an enabled Market"]
    assert page.tables[2][1] == ["6", "Gone", REASON_SUPPRESSED]
    assert page.tables[3][1] == ["3", "N", NOTE_BAD_EMAIL]
    # what was typed is kept, so the same file can be imported again
    assert page.inputs["origin"]["value"] == ORIGIN
    assert ("ops_automation", "Ops-Automation", True) in page.options
    assert page.errors == {}


@pytest.mark.parametrize(
    "extra",
    [{"surplus": "x"}, {"file_name": "x"}],
)
def test_a_field_other_than_the_forms_own_is_malformed(
    dashboard: Dashboard, cookie: bytes, extra: dict[str, str]
) -> None:
    reply = post(dashboard, cookie, csv_bytes(HEADER, "A,a.com,GB"), **extra)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)


def test_a_missing_file_or_a_urlencoded_body_is_malformed(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    assert post(dashboard, cookie, None).status == 400
    token = dashboard.csrf_token(cookie)
    reply = dashboard.post(
        "/leads/import",
        cookie=cookie,
        token=token,
        headers=[(b"content-type", b"application/x-www-form-urlencoded")],
        body=b"origin=abc&default_segment=",
    )
    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert count(db_engine, "leads") == 0


def test_the_csrf_header_is_required(
    dashboard: Dashboard, cookie: bytes, db_engine: Engine
) -> None:
    for token in (None, "wrong"):
        reply = post(dashboard, cookie, csv_bytes(HEADER, "A,a.com,GB"), token=token)
        assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert count(db_engine, "leads") == 0
