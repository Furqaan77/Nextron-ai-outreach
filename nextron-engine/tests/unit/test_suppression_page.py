"""The Suppression List panel (``GET /suppression`` and its four POSTs, FR-50, FR-41, AD-13,
AD-16, NFR-7, NFR-36, UX-DR3, UX-DR25, Story 3.5) through ``Dashboard(units=units)`` on a
temporary database at head: the page and its forms; an add (an address, a domain, again, free
webmail, a shared platform, a value that isn't valid) with its notice and the written rows;
an import with its counts and invalid lines, each refused file, a body over the limit and no
session; a search (on the list with the matching row, voided, absent, free webmail, a shared
platform); a Reverse, only on ``active`` ``reply_rule`` rows, its rows of the same opt-out with
their own Reverse forms and each refused Reverse; the Reverse controls' accessible names; the
filters, the pager and a bad query (400); malformed forms (400); the CSRF, ``Origin`` and
``Sec-Fetch-Site`` refusals (403, nothing written); ``no-store`` everywhere; no typed value,
file name, address or reason in any answer; no delete control; the Settings link; not in the
navigation; and ``main.build_app`` building ``Compliance`` on its clock."""

from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.bootstrap import load_bootstrap
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.contactability import key_hash
from nextron.domain.suppression_list import (
    FILE_EMPTY,
    FILE_NOT_UTF8,
    FILE_TOO_LARGE,
    FILE_TOO_MANY,
    FREE_WEBMAIL,
    MAX_VALUES,
    NOT_VALID,
    ONLY_RULE_MATCHED,
    REASON_INVALID,
    REASON_REQUIRED,
    SHARED_PLATFORM,
    UNKNOWN_ENTRY,
)
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM, MAX_UPLOAD_BODY
from tests.asgi import Reply
from tests.dashboard import FORM, ORIGIN, Dashboard, form, upload
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
TODAY = "2026-11-17"
EARLIER = "2026-11-16T09:00:00.000000Z"
PAGE = "/suppression"
# An address that must never come back in an answer. Its domain may: a domain entry shows its
# domain_plain, so only the address tests check "acme-widgets" (in no hint or label) is absent.
ADDRESS = "Zed.Quill@Acme-Widgets.co.uk"
ADDRESS_KEY = key_hash("address", ADDRESS)
DOMAIN = "acme-widgets.co.uk"
DOMAIN_KEY = key_hash("domain", DOMAIN)
SECRETS = ("zed.quill", "quill@", "other-place")
REASON = "misread-the-yes-qz"
CAUSE_REF = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
HEADINGS = ["Kind", "Source", "Date", "Status", "Hash", "Domain"]
LIST_HEADINGS = [*HEADINGS, "Reverse"]
POSTS = ("/suppression/add", "/suppression/search", "/suppression/reverse")
PANEL_ATTRS = {"hx-target": "#suppression-panel", "hx-swap": "outerHTML"}
FEATURED = "suppression-table suppression-featured"
LIST = "suppression-table"
OTHER_CAUSE = "01JD3QZ8X5V9K2M7N4P6R8T0AG"


class Panel(HTMLParser):
    """The ``#suppression-panel`` div: its notices (role and text), its other paragraphs (by
    class), its forms (attributes, inputs, select options, buttons and the table they sit in),
    its tables (by class: rows of cells, each row with the forms inside it, and the caption),
    its pager links and every ``id``."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.panels = 0
        self.notices: list[tuple[str | None, str]] = []
        self.paragraphs: dict[str, str] = {}
        self.forms: list[dict[str, Any]] = []
        self.tables: dict[str, list[list[str]]] = {}
        self.row_forms: dict[str, list[int]] = {}
        self.captions: dict[str, str] = {}
        self.links: dict[str, str] = {}
        self.buttons: list[str] = []
        self.ids: list[str] = []
        self._depth = 0
        self._caption: list[str] | None = None
        self._text: list[str] | None = None
        self._p: tuple[str, str | None] | None = None
        self._table: str | None = None
        self._row: list[str] | None = None
        self._row_forms = 0
        self._cell: list[str] | None = None
        self._select: str | None = None
        self._link: str | None = None
        self._button: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "div" and found.get("id") == "suppression-panel":
            self.panels += 1
            self._depth = 1
            return
        if not self._depth:
            return
        if found.get("id"):
            self.ids.append(str(found["id"]))
        if tag == "div":
            self._depth += 1
        elif tag == "form":
            table = self._table if self._row is not None else None
            self.forms.append(
                {"attrs": found, "inputs": [], "selects": {}, "buttons": [], "table": table}
            )
            if self._row is not None:
                self._row_forms += 1
        elif tag == "input" and self.forms:
            self.forms[-1]["inputs"].append(found)
        elif tag == "select" and self.forms:
            self._select = found.get("name") or ""
            self.forms[-1]["selects"][self._select] = []
        elif tag == "option" and self._select is not None:
            self.forms[-1]["selects"][self._select].append(
                (found.get("value"), "selected" in found)
            )
        elif tag == "table":
            self._table = found.get("class") or ""
            self.tables[self._table] = []
            self.row_forms[self._table] = []
        elif tag == "caption":
            self._caption = []
        elif tag == "tr":
            self._row = []
            self._row_forms = 0
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "p" and self._cell is None:
            self._text = []
            self._p = (found.get("class") or "", found.get("role"))
        elif tag == "a":
            self._link = found.get("href")
            self._text = []
        elif tag == "button":
            self._button = []
            if self.forms:
                self.forms[-1]["buttons"].append(found)

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        if tag == "div":
            self._depth -= 1
        elif tag == "select":
            self._select = None
        elif tag == "caption" and self._caption is not None and self._table is not None:
            self.captions[self._table] = " ".join("".join(self._caption).split())
            self._caption = None
        elif tag == "table":
            self._table = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self.tables[self._table].append(self._row)
            self.row_forms[self._table].append(self._row_forms)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "p" and self._text is not None and self._p is not None:
            text = " ".join("".join(self._text).split())
            css, role = self._p
            if "notice" in css.split():
                self.notices.append((role, text))
            else:
                self.paragraphs[css] = text
            self._text = None
            self._p = None
        elif tag == "a" and self._link is not None and self._text is not None:
            self.links[" ".join("".join(self._text).split())] = self._link
            self._link = None
            self._text = None
        elif tag == "button" and self._button is not None:
            self.buttons.append(" ".join("".join(self._button).split()))
            self._button = None

    def handle_data(self, data: str) -> None:
        if not self._depth:
            return
        if self._button is not None:
            self._button.append(data)
        if self._caption is not None:
            self._caption.append(data)
        if self._cell is not None:
            self._cell.append(data)
        elif self._text is not None:
            self._text.append(data)

    def form(self, action: str) -> dict[str, Any]:
        [found] = [
            f
            for f in self.forms
            if f["attrs"].get("hx-post") == action or f["attrs"].get("action") == action
        ]
        return found

    @property
    def rows(self) -> list[list[str]]:
        return self.tables.get("suppression-table", [])

    @property
    def featured(self) -> list[list[str]]:
        return self.tables.get(FEATURED, [])

    @property
    def caption(self) -> str | None:
        return self.captions.get(FEATURED)

    def reversals(self, table: str) -> list[dict[str, Any]]:
        """The Reverse forms in ``table``, in order."""
        return [
            f
            for f in self.forms
            if f["attrs"].get("hx-post") == "/suppression/reverse" and f["table"] == table
        ]

    def reversed_ids(self, table: str) -> list[str | None]:
        return [
            next(i.get("value") for i in f["inputs"] if i.get("name") == "entry_id")
            for f in self.reversals(table)
        ]


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units, clock=FrozenClock(T))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def answered(reply: Reply) -> Reply:
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    for secret in SECRETS:
        assert secret not in reply.text.lower(), secret
    assert REASON not in reply.text
    return reply


def get(dashboard: Dashboard, cookie: bytes, query: str = "") -> Reply:
    return answered(dashboard.get(PAGE, cookie=cookie, query=query.encode()))


def panel_of(reply: Reply) -> Panel:
    found = Panel(reply.text)
    assert found.panels == 1
    return found


def post(dashboard: Dashboard, session: tuple[bytes, str], path: str, **fields: str) -> Reply:
    cookie, token = session
    return dashboard.post(
        path,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, **fields),
    )


def submit(dashboard: Dashboard, session: tuple[bytes, str], path: str, **fields: str) -> Panel:
    reply = answered(post(dashboard, session, path, **fields))
    assert "<title>" not in reply.text  # the panel alone, swapped in by HTMX
    return panel_of(reply)


def send_file(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    data: bytes | None,
    *,
    filename: str = "secret-list-qz.csv",
    **texts: str,
) -> Reply:
    cookie, token = session
    body, headers = upload(data, filename=filename, csrf_token=token, **texts)
    return dashboard.post(
        "/suppression/import", cookie=cookie, token=token, headers=headers, body=body
    )


def entries(engine: Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT id, kind, value_hash, domain_plain, status, source, cause_ref, "
                "created_at, voided_at, void_reason FROM suppression_entries ORDER BY rowid"
            )
        ]


def events(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar_one())


def seed(
    engine: Engine,
    value_hash: str,
    *,
    kind: str = "address",
    source: str = "reply_rule",
    cause_ref: str | None = CAUSE_REF,
    domain_plain: str | None = None,
    created_at: str = EARLIER,
    voided: bool = False,
) -> str:
    entry_id = new_id()
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "INSERT INTO suppression_entries (id, kind, value_hash, domain_plain, status, source, "
            "cause_ref, created_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
            (entry_id, kind, value_hash, domain_plain, source, cause_ref, created_at),
        )
        if voided:
            connection.exec_driver_sql(
                "UPDATE suppression_entries SET status = 'voided', voided_at = ?, "
                "void_reason = 'x' WHERE id = ?",
                (created_at, entry_id),
            )
        connection.commit()
    return entry_id


def row(
    value_hash: str, kind: str = "address", source: str = "operator", **cells: str
) -> list[str]:
    return [
        kind,
        source,
        cells.get("date", TODAY),
        cells.get("status", "active"),
        value_hash[:12],
        cells.get("domain", "—"),
    ]


# GET --------------------------------------------------------------------------------------------


def test_the_empty_page_offers_its_forms(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    reply = get(dashboard, session[0])
    panel = panel_of(reply)

    assert "<title>Nextron: Suppression List</title>" in reply.text
    assert panel.notices == [] and panel.rows == [] and panel.featured == []
    assert panel.paragraphs["suppression-count"] == "The Suppression List is empty."
    add = panel.form("/suppression/add")
    assert add["attrs"] | PANEL_ATTRS == add["attrs"]
    assert add["attrs"]["method"] == "post"
    assert [(i["name"], i.get("value")) for i in add["inputs"]] == [
        ("csrf_token", session[1]),
        ("value", ""),
    ]
    imported = panel.form("/suppression/import")
    assert imported["attrs"]["enctype"] == "multipart/form-data"
    assert imported["attrs"]["hx-encoding"] == "multipart/form-data"
    assert [i["name"] for i in imported["inputs"]] == ["csrf_token", "file"]
    search = panel.form("/suppression/search")
    assert [(i["name"], i.get("value")) for i in search["inputs"]] == [
        ("csrf_token", session[1]),
        ("value", ""),
    ]
    filters = panel.form(PAGE)
    assert filters["attrs"]["method"] == "get"
    assert "hx-post" not in filters["attrs"] and filters["inputs"] == []
    assert [value for value, _ in filters["selects"]["kind"]] == [
        "",
        "address",
        "domain",
        "person",
        "profile",
    ]
    assert [value for value, _ in filters["selects"]["source"]] == [
        "",
        "reply_rule",
        "reply_class",
        "one_click",
        "mailto",
        "operator",
        "import",
        "dsr",
    ]
    assert [value for value, _ in filters["selects"]["status"]] == ["", "active", "voided"]
    assert "Clear filters" not in panel.links


def test_the_page_needs_a_session(dashboard: Dashboard) -> None:
    assert dashboard.get(PAGE).status == 401


def test_the_list_shows_kind_source_date_status_hash_and_only_a_domains_plain_value(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    seed(db_engine, ADDRESS_KEY, source="reply_rule")
    seed(
        db_engine,
        DOMAIN_KEY,
        kind="domain",
        source="operator",
        domain_plain=DOMAIN,
        created_at="2026-11-16T10:00:00.000000Z",
    )
    with db_engine.connect() as connection:  # plain text never in a row, even a void reason
        connection.exec_driver_sql(
            "UPDATE suppression_entries SET status = 'voided', voided_at = created_at, "
            "void_reason = ? WHERE source = 'reply_rule'",
            (REASON,),
        )
        connection.commit()

    reply = dashboard.get(PAGE, cookie=session[0])
    panel = panel_of(reply)

    assert reply.status == 200
    assert panel.rows == [
        LIST_HEADINGS,  # newest first
        [*row(DOMAIN_KEY, "domain", date="2026-11-16", domain=DOMAIN), ""],
        [*row(ADDRESS_KEY, source="reply_rule", date="2026-11-16", status="voided"), ""],
    ]
    assert panel.paragraphs["suppression-count"] == "Showing 1–2 of 2 entries"
    assert REASON not in reply.text
    assert ADDRESS_KEY not in reply.text  # only its first 12 characters


# Add --------------------------------------------------------------------------------------------


def test_adding_an_address_shows_the_new_row_but_never_the_address(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    reply = answered(post(dashboard, session, "/suppression/add", value=f"  {ADDRESS} "))
    panel = panel_of(reply)

    assert "acme-widgets" not in reply.text.lower()
    assert panel.notices == [("status", "Added to the Suppression List")]
    assert panel.caption == "Entries added"
    assert panel.featured == [LIST_HEADINGS, [*row(ADDRESS_KEY), ""]]
    assert panel.rows == [LIST_HEADINGS, [*row(ADDRESS_KEY), ""]]
    assert panel.form("/suppression/add")["inputs"][1]["value"] == ""
    [stored] = entries(db_engine)
    assert stored[1:8] == (
        "address",
        ADDRESS_KEY,
        None,
        "active",
        "operator",
        None,
        "2026-11-17T09:00:00.000000Z",
    )
    assert events(db_engine) == 1


def test_adding_a_domain_shows_it_normalised(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    reply = post(dashboard, session, "/suppression/add", value="https://www.Acme-Widgets.co.uk/x")

    assert reply.status == 200
    panel = panel_of(reply)
    assert panel.notices == [("status", "Added to the Suppression List")]
    assert panel.caption == "Entries added"
    assert panel.featured == [LIST_HEADINGS, [*row(DOMAIN_KEY, "domain", domain=DOMAIN), ""]]
    assert [stored[3] for stored in entries(db_engine)] == [DOMAIN]


def test_adding_again_says_since_when_and_from_which_source(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    seed(db_engine, ADDRESS_KEY, source="reply_rule")
    before = entries(db_engine)

    panel = submit(dashboard, session, "/suppression/add", value=ADDRESS.upper())

    assert panel.notices == [
        ("status", "Already on the list (active since 2026-11-16, source reply_rule)")
    ]
    assert panel.featured == [] and panel.caption is None
    assert entries(db_engine) == before


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("gmail.com", FREE_WEBMAIL),
        ("mail.yahoo.co.uk", FREE_WEBMAIL),
        ("https://facebook.com/acme-widgets", SHARED_PLATFORM),
        ("sites.google.com/view/acme-widgets", SHARED_PLATFORM),
        ("mailto:zed.quill@acme-widgets.co.uk", NOT_VALID),
        ("zed.quill@", NOT_VALID),
        ("not a domain", NOT_VALID),
        ("", NOT_VALID),
    ],
)
def test_a_refused_value_says_why_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    value: str,
    message: str,
) -> None:
    panel = submit(dashboard, session, "/suppression/add", value=value)

    assert panel.notices == [("alert", message)]
    assert panel.form("/suppression/add")["inputs"][1]["value"] == ""
    assert entries(db_engine) == [] and events(db_engine) == 0


def test_a_hostile_value_is_neither_stored_nor_echoed(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    reply = post(dashboard, session, "/suppression/add", value='"><script>alert(1)</script>')

    assert reply.status == 200
    assert "<script>" not in reply.text and "alert(1)" not in reply.text
    assert entries(db_engine) == []


# Import -----------------------------------------------------------------------------------------


def test_an_import_shows_its_counts_and_invalid_lines_and_keeps_no_file_name(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    seed(db_engine, DOMAIN_KEY, kind="domain", source="operator", domain_plain=DOMAIN)
    data = "\r\n".join(
        [
            ADDRESS,  # 1 added
            ADDRESS.lower(),  # 2 already
            DOMAIN,  # 3 already
            "gmail.com",  # 4 invalid
            "email address",  # 5 invalid
            "",
            '"quill@other-place.co.uk",Quill',  # 7 added
        ]
    ).encode("utf-8")

    reply = answered(send_file(dashboard, session, data))
    panel = panel_of(reply)

    assert panel.notices == [
        ("status", "Import finished: 2 added, 2 already suppressed, 2 invalid.")
    ]
    assert panel.paragraphs["suppression-invalid"] == "Invalid lines: 4, 5"
    assert [stored[5] for stored in entries(db_engine)] == ["operator", "import", "import"]
    assert "secret-list-qz" not in reply.text
    assert "other-place" not in reply.text


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", FILE_EMPTY),
        (b"\n  \n", FILE_EMPTY),
        (b"acme-widgets.co.uk\n\xff\xfe", FILE_NOT_UTF8),
        (b"acme-widgets.co.uk\x00", FILE_NOT_UTF8),
        (b"x@acme-widgets.co.uk\n" * (MAX_VALUES + 1), FILE_TOO_MANY),
        (b"acme-widgets.co.uk\n" + b" " * (5 * 1024 * 1024), FILE_TOO_LARGE),
    ],
)
def test_a_refused_file_says_why_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    data: bytes,
    message: str,
) -> None:
    panel = panel_of(answered(send_file(dashboard, session, data)))

    assert panel.notices == [("alert", message)]
    assert entries(db_engine) == []


def test_a_body_over_the_upload_limit_is_refused_unread(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    panel = panel_of(answered(send_file(dashboard, session, b"x" * (MAX_UPLOAD_BODY + 1))))

    assert panel.notices == [("alert", FILE_TOO_LARGE)]
    assert entries(db_engine) == []


def test_an_import_form_with_other_fields_or_no_file_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    cookie, token = session
    for reply in (
        send_file(dashboard, session, b"acme-widgets.co.uk", origin="x"),
        send_file(dashboard, session, None),
        dashboard.post(
            "/suppression/import",
            cookie=cookie,
            token=token,
            headers=[(b"content-type", FORM)],
            body=form(csrf_token=token),
        ),
    ):
        assert (reply.status, reply.text) == (400, MALFORMED_FORM)
        assert reply.header(b"cache-control") == b"no-store"
    assert entries(db_engine) == []


# Search -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "answer"),
    [
        (ADDRESS, "On the list (active since 2026-11-16, source reply_rule)"),
        (f"https://www.{DOMAIN}/team", "Not on the list"),
        ("quill@other-place.co.uk", "Not on the list"),
        ("gmail.com", "Not on the list"),
        ("https://facebook.com/acme-widgets", "Not on the list"),
    ],
)
def test_a_search_hashes_the_value_and_answers(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    value: str,
    answer: str,
) -> None:
    seed(db_engine, ADDRESS_KEY)
    seed(db_engine, DOMAIN_KEY, kind="domain", domain_plain=DOMAIN, voided=True)
    before = entries(db_engine)

    panel = submit(dashboard, session, "/suppression/search", value=value)

    assert panel.notices == [("status", answer)]
    assert panel.form("/suppression/search")["inputs"][1]["value"] == ""
    assert entries(db_engine) == before
    if answer == "Not on the list":
        assert panel.featured == [] and panel.caption is None


def test_a_search_that_finds_an_entry_shows_its_row_with_reverse_when_reversible(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    rule = seed(db_engine, ADDRESS_KEY)
    seed(db_engine, DOMAIN_KEY, kind="domain", source="operator", domain_plain=DOMAIN)

    found_rule = submit(dashboard, session, "/suppression/search", value=ADDRESS)
    found_domain = submit(dashboard, session, "/suppression/search", value=DOMAIN)

    assert found_rule.caption == "Matching entry"
    assert found_rule.featured[1][:6] == row(ADDRESS_KEY, source="reply_rule", date="2026-11-16")
    assert found_rule.reversed_ids(FEATURED) == [rule]
    assert found_domain.notices == [
        ("status", "On the list (active since 2026-11-16, source operator)")
    ]
    assert found_domain.featured == [
        LIST_HEADINGS,
        [*row(DOMAIN_KEY, "domain", date="2026-11-16", domain=DOMAIN), ""],
    ]
    assert found_domain.reversals(FEATURED) == []
    # the same entry in both tables never repeats an id
    assert len(found_rule.ids) == len(set(found_rule.ids))


def test_a_search_for_text_that_is_neither_says_so(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    panel = submit(dashboard, session, "/suppression/search", value="zed.quill@")

    assert panel.notices == [("alert", NOT_VALID)]


# Reverse ----------------------------------------------------------------------------------------


def test_reverse_appears_only_on_active_reply_rule_rows(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    for source in ("reply_class", "one_click", "mailto", "operator", "import", "dsr"):
        seed(db_engine, key_hash("address", f"{source}@x.co.uk"), source=source)
    seed(db_engine, key_hash("address", "voided@x.co.uk"), voided=True)
    rule = seed(db_engine, ADDRESS_KEY, created_at="2026-11-16T10:00:00.000000Z")

    panel = panel_of(get(dashboard, session[0]))

    assert panel.row_forms["suppression-table"] == [0, 1, 0, 0, 0, 0, 0, 0, 0]
    [reverse] = panel.reversals(LIST)
    assert reverse["attrs"]["method"] == "post"
    assert reverse["attrs"] | PANEL_ATTRS == reverse["attrs"]
    assert [(i["name"], i.get("value")) for i in reverse["inputs"]] == [
        ("csrf_token", session[1]),
        ("entry_id", rule),
        ("reason", ""),
    ]
    assert "required" in reverse["inputs"][2] and reverse["inputs"][2]["maxlength"] == "500"


def test_each_reverse_control_names_its_row(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    seed(db_engine, ADDRESS_KEY)
    seed(db_engine, DOMAIN_KEY, kind="domain", domain_plain=DOMAIN)

    panel = panel_of(get(dashboard, session[0]))

    labels = [
        (form["inputs"][2].get("aria-label"), form["buttons"][0].get("aria-label"))
        for form in panel.reversals(LIST)
    ]
    assert sorted(labels) == sorted(
        [
            (
                f"Reason for reversing the address entry {ADDRESS_KEY[:12]}",
                f"Reverse the address entry {ADDRESS_KEY[:12]}",
            ),
            (
                f"Reason for reversing the domain entry {DOMAIN_KEY[:12]}",
                f"Reverse the domain entry {DOMAIN_KEY[:12]}",
            ),
        ]
    )
    assert len(panel.ids) == len(set(panel.ids))


def test_a_reversal_voids_the_entry_and_shows_the_rest_of_its_opt_out(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    first = seed(db_engine, ADDRESS_KEY)
    person_key = key_hash("person", ("Zed Quill", DOMAIN))
    person = seed(db_engine, person_key, kind="person")
    # another person's opt-out, and the same cause from another source: neither is shown
    other = seed(db_engine, key_hash("address", "bo@x.co.uk"), cause_ref=OTHER_CAUSE)
    seed(db_engine, key_hash("address", "x@third.co.uk"), source="one_click")

    panel = submit(dashboard, session, "/suppression/reverse", entry_id=first, reason=REASON)

    assert panel.notices == [
        ("status", "Opt-out reversed. 1 other entry from the same opt-out is still on the list.")
    ]
    voided = [stored for stored in entries(db_engine) if stored[0] == first]
    assert voided == [
        (
            first,
            "address",
            ADDRESS_KEY,
            None,
            "voided",
            "reply_rule",
            CAUSE_REF,
            EARLIER,
            "2026-11-17T09:00:00.000000Z",
            REASON,
        )
    ]
    assert panel.caption == "Still on the list from the same opt-out"
    assert panel.featured == [
        LIST_HEADINGS,
        [*row(person_key, "person", "reply_rule", date="2026-11-16"), "Reason Reverse"],
    ]
    assert panel.reversed_ids(FEATURED) == [person]
    assert sorted(panel.reversed_ids(LIST)) == sorted([person, other])
    assert len(panel.ids) == len(set(panel.ids))
    assert events(db_engine) == 1


@pytest.mark.parametrize(
    ("which", "reason", "message"),
    [
        ("operator", REASON, ONLY_RULE_MATCHED),
        ("voided", REASON, "This opt-out has already been reversed"),
        ("unknown", REASON, UNKNOWN_ENTRY),
        ("junk", REASON, UNKNOWN_ENTRY),
        ("rule", "", REASON_REQUIRED),
        ("rule", "x" * 501, REASON_INVALID),
    ],
)
def test_a_refused_or_forged_reversal_says_why_and_changes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    which: str,
    reason: str,
    message: str,
) -> None:
    targets = {
        "operator": seed(
            db_engine, DOMAIN_KEY, kind="domain", source="operator", domain_plain=DOMAIN
        ),
        "voided": seed(db_engine, key_hash("address", "v@x.co.uk"), voided=True),
        "rule": seed(db_engine, ADDRESS_KEY),
        "unknown": CAUSE_REF,
        "junk": "<script>",
    }
    before = entries(db_engine)

    panel = submit(
        dashboard, session, "/suppression/reverse", entry_id=targets[which], reason=reason
    )

    assert panel.notices == [("alert", message)]
    assert entries(db_engine) == before
    assert events(db_engine) == 0


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/suppression/add", {}),
        ("/suppression/add", {"value": "a", "extra": "b"}),
        ("/suppression/search", {"q": ADDRESS}),
        ("/suppression/reverse", {"entry_id": CAUSE_REF}),
        ("/suppression/reverse", {"reason": REASON}),
        ("/suppression/reverse", {"entry_id": CAUSE_REF, "reason": REASON, "delete": "1"}),
    ],
)
def test_a_form_without_exactly_its_fields_is_malformed(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    path: str,
    body: dict[str, str],
) -> None:
    reply = post(dashboard, session, path, **body)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert reply.header(b"cache-control") == b"no-store"
    assert entries(db_engine) == []


@pytest.mark.parametrize("path", POSTS)
def test_a_repeated_field_is_malformed(
    dashboard: Dashboard, session: tuple[bytes, str], path: str
) -> None:
    fields = {"entry_id": CAUSE_REF, "reason": REASON} if path.endswith("reverse") else {
        "value": DOMAIN
    }
    name = next(iter(fields))
    cookie, token = session
    body = form(csrf_token=token, **fields) + f"&{name}=again".encode()

    reply = dashboard.post(
        path, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)


# The security checks ----------------------------------------------------------------------------


# case: (header token, body token, Origin, extra headers); "token" is the session's
REFUSALS: dict[str, tuple[str | None, str | None, bytes | None, list[tuple[bytes, bytes]]]] = {
    "no token": (None, None, ORIGIN, []),
    "wrong header token": ("wrong", "token", ORIGIN, []),
    "wrong body token": ("token", "wrong", ORIGIN, []),
    "no origin": ("token", "token", None, []),
    "other origin": ("token", "token", b"http://evil.example", []),
    "null origin": ("token", "token", b"null", []),
    "cross site": ("token", "token", ORIGIN, [(b"sec-fetch-site", b"cross-site")]),
    "same site": ("token", "token", ORIGIN, [(b"sec-fetch-site", b"same-site")]),
}


# A multipart body presents no token (the header alone is checked), so the import has no
# "wrong body token" case.
SECURITY_CASES = [
    (path, case)
    for path in [*POSTS, "/suppression/import"]
    for case in sorted(REFUSALS)
    if not (path == "/suppression/import" and case == "wrong body token")
]


@pytest.mark.parametrize(("path", "case"), SECURITY_CASES)
def test_a_post_failing_a_security_check_is_refused_and_writes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    path: str,
    case: str,
) -> None:
    header, field, origin, extra = REFUSALS[case]
    rule = seed(db_engine, ADDRESS_KEY)
    before = entries(db_engine)
    cookie, token = session
    fields = {} if field is None else {"csrf_token": token if field == "token" else field}
    if path == "/suppression/import":
        body, content = upload(b"acme-widgets.co.uk\n", **fields)
    else:
        body = form(**fields, value=DOMAIN, entry_id=rule, reason=REASON)
        content = [(b"content-type", FORM)]

    reply = dashboard.post(
        path,
        cookie=cookie,
        token=token if header == "token" else header,
        origin=origin,
        headers=[*content, *extra],
        body=body,
    )

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert reply.header(b"cache-control") == b"no-store"
    assert entries(db_engine) == before


def test_a_post_needs_a_session(dashboard: Dashboard, db_engine: Engine) -> None:
    for path in POSTS:
        reply = dashboard.post(path, headers=[(b"content-type", FORM)], body=form(value=DOMAIN))
        assert reply.status == 401
    assert entries(db_engine) == []


def test_an_import_needs_a_session(dashboard: Dashboard, db_engine: Engine) -> None:
    body, headers = upload(f"{DOMAIN}\n{ADDRESS}\n".encode(), csrf_token="x")

    reply = dashboard.post("/suppression/import", token="x", headers=headers, body=body)

    assert reply.status == 401
    assert entries(db_engine) == [] and events(db_engine) == 0


# Filters and pages ------------------------------------------------------------------------------


def test_filters_keep_only_matching_rows_and_a_post_answers_unfiltered(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    seed(db_engine, ADDRESS_KEY, source="operator")
    seed(db_engine, DOMAIN_KEY, kind="domain", source="operator", domain_plain=DOMAIN)

    filtered = panel_of(get(dashboard, session[0], "kind=domain&source=&status=active"))
    empty = panel_of(get(dashboard, session[0], "source=dsr"))
    after_post = submit(dashboard, session, "/suppression/search", value=DOMAIN)

    assert [cells[0] for cells in filtered.rows[1:]] == ["domain"]
    assert filtered.paragraphs["suppression-count"] == "Showing 1–1 of 1 entry"
    selects = filtered.form(PAGE)["selects"]
    assert ("domain", True) in selects["kind"] and ("active", True) in selects["status"]
    assert ("", True) in selects["source"]
    assert filtered.links["Clear filters"] == PAGE
    assert empty.paragraphs["suppression-count"] == "No entries match these filters."
    assert len(after_post.rows) == 3
    assert all(not chosen for value, chosen in after_post.form(PAGE)["selects"]["kind"] if value)


def test_the_list_pages_by_fifty_and_the_links_keep_the_filters(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    for number in range(51):
        seed(
            db_engine,
            key_hash("address", f"user{number}@x.co.uk"),
            source="operator",
            created_at=f"2026-11-16T09:{number:02d}:00.000000Z",
        )

    first = panel_of(get(dashboard, session[0], "source=operator"))
    second = panel_of(get(dashboard, session[0], "source=operator&page=2"))
    past = panel_of(get(dashboard, session[0], "page=9"))
    after_post = submit(dashboard, session, "/suppression/search", value=DOMAIN)

    assert first.paragraphs["suppression-count"] == "Showing 1–50 of 51 entries"
    assert len(first.rows) == 51
    assert parse_qsl(urlsplit(first.links["Next"]).query) == [
        ("source", "operator"),
        ("page", "2"),
    ]
    assert second.paragraphs["suppression-count"] == "Showing 51–51 of 51 entries"
    assert parse_qsl(urlsplit(second.links["Previous"]).query) == [
        ("source", "operator"),
        ("page", "1"),
    ]
    assert past.paragraphs["suppression-count"] == "Showing 51–51 of 51 entries"
    assert after_post.paragraphs["suppression-count"] == "Showing 1–50 of 51 entries"


@pytest.mark.parametrize(
    "query",
    [
        "kind=x",
        "kind=ADDRESS",
        "source=operators",
        "status=deleted",
        "kind=address&kind=domain",
        "page=0",
        "page=abc",
        "page=12345678",
        "foo=1",
        "csrf_token=x",
        "value=jane%40firm.co.uk",
    ],
)
def test_a_bad_query_parameter_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], query: str
) -> None:
    reply = dashboard.get(PAGE, cookie=session[0], query=query.encode())

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert reply.header(b"cache-control") == b"no-store"


# No delete, no switch, the link and the wiring --------------------------------------------------


def test_there_is_no_delete_control_and_no_switch(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    for source in ("reply_rule", "operator"):
        seed(db_engine, key_hash("address", f"{source}@x.co.uk"), source=source)

    panel = panel_of(get(dashboard, session[0]))

    posts = {f["attrs"].get("hx-post") for f in panel.forms} - {None}
    assert posts == {
        "/suppression/add",
        "/suppression/import",
        "/suppression/search",
        "/suppression/reverse",
    }
    assert sorted(set(panel.buttons)) == [
        "Add to the list",
        "Filter",
        "Import",
        "Reverse",
        "Search",
    ]
    assert not any(f["attrs"].get("method") not in ("post", "get") for f in panel.forms)
    for verb in ("delete", "remove", "turn off", "disable"):
        assert verb not in " ".join(panel.buttons).lower()


def test_the_settings_page_links_to_the_panel(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/settings", cookie=session[0])

    assert '<a href="/suppression">Suppression List page</a>' in reply.text


def test_the_panel_is_not_in_the_navigation(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/", cookie=session[0])

    assert 'href="/suppression"' not in reply.text


def test_build_app_builds_compliance_on_its_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    clocks: list[FrozenClock] = []
    given: list[object] = []
    codecs: list[object] = []
    real_compliance = main.Compliance

    def system_clock() -> FrozenClock:
        clocks.append(FrozenClock(T))
        return clocks[-1]

    def compliance(clock: Any, **kwargs: Any) -> Any:
        given.append(clock)
        codecs.append(kwargs.get("codec"))
        return real_compliance(clock, **kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "SystemClock", system_clock)
    monkeypatch.setattr(main, "Compliance", compliance)

    main.build_app(load_bootstrap(), units=FakeUnits())

    [clock] = clocks
    assert given == [clock]
    [codec] = codecs  # Story 3.11: the poll decodes unsubscribe tokens through it
    assert isinstance(codec, main.TokenCodec)
