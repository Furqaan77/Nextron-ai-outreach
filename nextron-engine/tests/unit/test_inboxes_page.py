"""The Sending Inboxes panel (``GET /inboxes`` and ``POST /inboxes``, FR-32, AD-14, UX-DR26,
Story 4.7) through ``Dashboard(units=units)`` on a temporary database at head: the page lists
the inboxes with the connecting note; a POST registers (``Inbox registered.``, the form
cleared) or shows each refusal's message with the typed values kept; the crowding warning; the
empty state; malformed and repeated fields (400) and a POST without the CSRF token (403);
``no-store``; a label holding ``<script>`` renders escaped; the Settings page link; a
``removed`` inbox left out of the crowding count; the form's ``hx-sync="this:drop"``; and
``main.build_app`` building ``Sending`` on its clock and environment.

Story 6.5's Sending Domain authentication section (``GET /inboxes/auth`` and ``POST
/inboxes/auth/recheck``): the section on the page and its empty state, a listed domain never
checked, the partial showing a newly recorded result, a Recheck queuing one job and saying so,
an unlisted or unknown domain's alert, malformed forms (400), a POST without the CSRF token
(403), and a hostile stored domain rendered escaped.

Story 6.4's warm-up section per inbox (``POST /inboxes/warmup``): the unrecorded facts and the
prefilled form, a save and its recorded days, an active warmed inbox's cold cap under the
ceiling, a refusal keeping only that inbox's typed values, each bad field's message, an unknown
inbox and an unset timezone, malformed and repeated fields (400), a POST without the CSRF token
(403), and a hostile tool name rendered escaped."""

from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.queries import sending as sending_queries
from nextron.app.setup.api import Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain.domain_auth import REASON_TEXTS, AuthReason
from nextron.domain.sending_inbox import CONNECTING_NOTE
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.fakes.dns import FakeDns
from tests.fakes.postmaster import FakePostmaster
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
PAGE = "/inboxes"
WARMUP = "/inboxes/warmup"
SENDER = "Furqaan Ahmed"
FIRST = "furqaan@nextron-ai.com"
MALFORMED = b"Request refused: malformed form"
REFUSED = b"Request refused: security check failed"
PANEL_FORM = {
    "method": "post",
    "hx-post": "/inboxes",
    "hx-target": "#inboxes-panel",
    "hx-swap": "outerHTML",
    "hx-sync": "this:drop",  # a double click doesn't queue a second POST
}
HEADINGS = ["Label", "Address", "Sending Domain", "Status", "Connected", "Gmail"]


class Panel(HTMLParser):
    """The ``#inboxes-panel`` div: its notices (role and text), its warnings, its forms
    (attributes and inputs), its table rows (cells), its warm-up facts (one list of (name,
    value) per ``<dl>``) and its other paragraphs."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.panels = 0
        self.notices: list[tuple[str | None, str]] = []
        self.warnings: list[str] = []
        self.paragraphs: list[str] = []
        self.forms: list[tuple[dict[str, str | None], list[dict[str, str | None]]]] = []
        self.rows: list[list[str]] = []
        self.facts: list[list[tuple[str, str]]] = []
        self._fact: list[str] | None = None
        self._depth = 0
        self._text: list[str] | None = None
        self._kind: tuple[str, str | None] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "div" and found.get("id") == "inboxes-panel":
            self.panels += 1
            self._depth = 1
            return
        if not self._depth:
            return
        if tag == "div":
            self._depth += 1
        elif tag == "form":
            self.forms.append((found, []))
        elif tag == "input" and self.forms:
            self.forms[-1][1].append(found)
        elif tag == "tr":
            self._row = []
        elif tag == "dl":
            self.facts.append([])
        elif tag in ("dt", "dd"):
            self._fact = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "p" and self._cell is None:
            self._text = []
            self._kind = (found.get("class") or "", found.get("role"))

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        if tag == "div":
            self._depth -= 1
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "dt" and self._fact is not None:
            self.facts[-1].append(("".join(self._fact), ""))
            self._fact = None
        elif tag == "dd" and self._fact is not None:
            name, _ = self.facts[-1][-1]
            self.facts[-1][-1] = (name, "".join(self._fact))
            self._fact = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif tag == "p" and self._text is not None and self._kind is not None:
            text = " ".join("".join(self._text).split())
            css, role = self._kind
            if "notice" in css.split():
                self.notices.append((role, text))
            elif css == "inbox-warning":
                self.warnings.append(text)
            else:
                self.paragraphs.append(text)
            self._text = None
            self._kind = None

    def handle_data(self, data: str) -> None:
        if not self._depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._fact is not None:
            self._fact.append(data)
        elif self._text is not None:
            self._text.append(data)

    def value(self, name: str) -> str | None:
        """A field of the registration form."""
        [(_, inputs)] = [form for form in self.forms if form[0].get("hx-post") == PAGE]
        [found] = [field for field in inputs if field.get("name") == name]
        return found.get("value")

    def warmup_forms(self) -> list[dict[str, str | None]]:
        """Each warm-up form's field values by name, in panel order."""
        return [
            {field.get("name") or "": field.get("value") for field in inputs}
            for attrs, inputs in self.forms
            if attrs.get("hx-post") == WARMUP
        ]


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units, clock=FrozenClock(T))  # env dev


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


@pytest.fixture
def ready(dashboard: Dashboard, units: UnitOfWorkFactory) -> Dashboard:
    """The Sender's name set and the test inboxes allowlisted (the dashboard runs as dev)."""
    allowlist = "\n".join(
        [FIRST, "mac@nextron-ai.com", "a@nextron-ai.com", "b@nextron-ai.com", "c@nextron-ai.com"]
    )
    with units.open():
        Setup(dashboard.clock).save({"sender_name": SENDER, "sink_allowlist": allowlist})
    return dashboard


def get(dashboard: Dashboard, cookie: bytes, path: str = PAGE) -> Reply:
    reply = dashboard.get(path, cookie=cookie)
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    return reply


def panel_of(reply: Reply) -> Panel:
    found = Panel(reply.text)
    assert found.panels == 1
    return found


def post(dashboard: Dashboard, session: tuple[bytes, str], body: bytes) -> Reply:
    cookie, token = session
    return dashboard.post(
        PAGE, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )


def register(
    dashboard: Dashboard, session: tuple[bytes, str], address: str, label: str = "Main"
) -> Reply:
    reply = post(dashboard, session, form(csrf_token=session[1], address=address, label=label))
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return reply


def stored(units: UnitOfWorkFactory) -> list[tuple[str, str, str]]:
    with units.open(read_only=True):
        return [(view.address, view.label, view.status) for view in sending_queries.inboxes()]


# GET ------------------------------------------------------------------------------------------


def test_with_no_inboxes_the_page_says_so_and_offers_the_form(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, session[0])
    panel = panel_of(reply)

    assert "<title>Nextron: Sending Inboxes</title>" in reply.text
    assert "No Sending Inboxes registered yet." in panel.paragraphs
    assert panel.rows == [] and panel.notices == [] and panel.warnings == []
    [(attrs, inputs)] = panel.forms  # no inbox, so no row Connect form
    assert attrs == PANEL_FORM
    assert [field.get("name") for field in inputs] == ["csrf_token", "address", "label"]
    assert inputs[0] == {"type": "hidden", "name": "csrf_token", "value": session[1]}
    assert panel.value("address") == "" and panel.value("label") == ""


def test_the_page_lists_the_inboxes_with_the_connecting_note(
    ready: Dashboard, session: tuple[bytes, str]
) -> None:
    register(ready, session, "Furqaan@Nextron-AI.com", "Main")
    ready.clock.set(datetime(2026, 11, 17, 9, 5, tzinfo=UTC))
    register(ready, session, "mac@nextron-ai.com", "Second")

    panel = panel_of(get(ready, session[0]))

    assert panel.rows == [
        HEADINGS,
        ["Main", FIRST, "nextron-ai.com", f"connecting {CONNECTING_NOTE}", "", "Connect"],
        [
            "Second",
            "mac@nextron-ai.com",
            "nextron-ai.com",
            f"connecting {CONNECTING_NOTE}",
            "",
            "Connect",
        ],
    ]
    assert panel.warnings == []
    assert "No Sending Inboxes registered yet." not in panel.paragraphs


def test_an_active_inbox_has_no_connecting_note_and_a_removed_one_is_not_listed(
    ready: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    register(ready, session, FIRST, "Main")
    register(ready, session, "mac@nextron-ai.com", "Gone")
    with db_engine.connect() as connection:
        connection.exec_driver_sql(
            "UPDATE sending_inboxes SET status = 'active' WHERE label = 'Main'"
        )
        connection.exec_driver_sql(
            "UPDATE sending_inboxes SET status = 'removed' WHERE label = 'Gone'"
        )
        connection.commit()

    panel = panel_of(get(ready, session[0]))

    assert panel.rows == [HEADINGS, ["Main", FIRST, "nextron-ai.com", "active", "", ""]]


def test_the_page_needs_a_session(dashboard: Dashboard) -> None:
    assert dashboard.get(PAGE).status == 401


# POST -----------------------------------------------------------------------------------------


def test_a_registration_says_so_and_clears_the_form(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = register(ready, session, "Furqaan@Nextron-AI.com", "  Main  ")
    panel = panel_of(reply)

    assert "<title>" not in reply.text  # the panel alone, swapped in by HTMX
    assert panel.notices == [("status", "Inbox registered.")]
    assert panel.value("address") == "" and panel.value("label") == ""
    assert panel.rows[1] == [
        "Main",
        FIRST,
        "nextron-ai.com",
        f"connecting {CONNECTING_NOTE}",
        "",
        "Connect",
    ]
    assert stored(units) == [(FIRST, "Main", "connecting")]


@pytest.mark.parametrize(
    ("address", "label", "message"),
    [
        ("x@nextronai.com", "Main", "Cold email never goes from nextronai.com"),
        ("x@mail.NextronAI.com", "Main", "Cold email never goes from nextronai.com"),
        ("info@nextron-ai.com", "Main", "Use a personal address in the Sender's name"),
        ("Sales+x@nextron-ai.com", "Main", "Use a personal address in the Sender's name"),
        ("not-an-email", "Main", "Enter a valid email address, such as furqaan@yourdomain.com"),
        ("a@co.uk", "Main", "Enter a valid email address, such as furqaan@yourdomain.com"),
        (FIRST, "", "Enter a label of 1 to 80 characters on one line"),
        (FIRST, "x" * 81, "Enter a label of 1 to 80 characters on one line"),
        (FIRST, "Main\ninbox", "Enter a label of 1 to 80 characters on one line"),
        (
            "stranger@nextron-ai.com",
            "Main",
            "Outside prod, only allowlisted test inboxes can be registered",
        ),
    ],
)
def test_each_refusal_shows_its_message_and_keeps_the_typed_values(
    ready: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    address: str,
    label: str,
    message: str,
) -> None:
    panel = panel_of(register(ready, session, address, label))

    assert panel.notices == [("alert", message)]
    assert panel.value("address") == address
    assert panel.value("label") == label
    assert stored(units) == []


def test_a_duplicate_is_refused_with_its_message(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(ready, session, FIRST)

    panel = panel_of(register(ready, session, "FURQAAN@nextron-ai.com", "Again"))

    assert panel.notices == [("alert", "Inbox already registered")]
    assert panel.value("address") == "FURQAAN@nextron-ai.com"
    assert stored(units) == [(FIRST, "Main", "connecting")]


def test_an_unset_sender_name_is_refused_with_its_message(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    with units.open():
        Setup(dashboard.clock).save({"sink_allowlist": FIRST})

    panel = panel_of(register(dashboard, session, FIRST))

    assert panel.notices == [("alert", "Set the Sender's name on the Settings page first")]
    assert stored(units) == []


def test_a_fourth_inbox_on_a_domain_is_stored_with_the_crowding_warning(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    for name in ("a", "b", "c"):
        panel = panel_of(register(ready, session, f"{name}@nextron-ai.com", name))
        assert panel.warnings == []

    panel = panel_of(register(ready, session, FIRST, "Fourth"))

    assert panel.notices == [("status", "Inbox registered.")]
    assert panel.warnings == ["More than 3 inboxes on nextron-ai.com raises deliverability risk"]
    assert len(stored(units)) == 4
    assert panel_of(get(ready, session[0])).warnings == panel.warnings


def test_a_removed_inbox_is_left_out_of_the_crowding_count(
    ready: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    for name in ("a", "b", "c"):
        register(ready, session, f"{name}@nextron-ai.com", name)
    with db_engine.connect() as connection:
        [(domain_id,)] = connection.exec_driver_sql("SELECT id FROM sending_domains").all()
        connection.exec_driver_sql(
            "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, "
            "status, created_at, status_changed_at) VALUES (?, ?, 'Gone', ?, ?, 'removed', ?, ?)",
            (new_id(), FIRST, domain_id, SENDER, *["2026-11-17T09:00:00.000000Z"] * 2),
        )
        connection.commit()

    panel = panel_of(get(ready, session[0]))

    assert panel.warnings == []
    assert [row[0] for row in panel.rows[1:]] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"address": FIRST}, "label missing"),
        ({"label": "Main"}, "address missing"),
        ({"address": FIRST, "label": "Main", "status": "active"}, "an extra field"),
        ({}, "nothing"),
    ],
)
def test_a_form_without_exactly_address_and_label_is_malformed(
    ready: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    body: dict[str, str],
    why: str,
) -> None:
    reply = post(ready, session, form(csrf_token=session[1], **body))

    assert reply.status == 400, why
    assert reply.body == MALFORMED
    assert reply.header(b"cache-control") == b"no-store"
    assert stored(units) == []


@pytest.mark.parametrize("repeated", ["address", "label"])
def test_a_repeated_field_is_malformed(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, repeated: str
) -> None:
    body = form(csrf_token=session[1], address=FIRST, label="Main") + (
        f"&{repeated}=again".encode()
    )

    reply = post(ready, session, body)

    assert reply.status == 400
    assert reply.body == MALFORMED
    assert stored(units) == []


def test_a_post_without_the_csrf_token_is_refused_with_403(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = ready.post(
        PAGE,
        cookie=session[0],
        headers=[(b"content-type", FORM)],
        body=form(address=FIRST, label="Main"),
    )

    assert reply.status == 403
    assert reply.body == REFUSED
    assert stored(units) == []


def test_a_post_needs_a_session(ready: Dashboard, units: UnitOfWorkFactory) -> None:
    reply = ready.post(
        PAGE, headers=[(b"content-type", FORM)], body=form(address=FIRST, label="Main")
    )

    assert reply.status == 401
    assert stored(units) == []


# Escaping and the Settings link ---------------------------------------------------------------


def test_a_hostile_label_renders_as_escaped_text(
    ready: Dashboard, session: tuple[bytes, str]
) -> None:
    hostile = '<script>alert(1)</script><b hx-post="/x">'
    register(ready, session, FIRST, hostile)

    reply = get(ready, session[0])

    assert "<script>" not in reply.text and "<b hx-post" not in reply.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in reply.text
    # only the forms' own: the panel's, the inbox's warm-up (Story 6.4) and the domain's
    # Recheck (Story 6.5)
    assert reply.text.count('hx-post="') == 5  # with Connect inbox and the row's Connect
    assert panel_of(reply).rows[1][0] == hostile


def test_a_refused_hostile_value_is_kept_escaped_in_the_form(
    ready: Dashboard, session: tuple[bytes, str]
) -> None:
    hostile = '"><script>alert(1)</script>'
    reply = register(ready, session, hostile, hostile)

    assert "<script>" not in reply.text
    panel = panel_of(reply)
    assert panel.value("address") == hostile and panel.value("label") == hostile


def test_the_settings_page_links_to_the_panel(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, session[0], "/settings")

    assert '<a href="/inboxes">Sending Inboxes page</a>' in reply.text


def test_the_panel_is_not_in_the_navigation(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, session[0], "/")

    assert 'href="/inboxes"' not in reply.text


# Wiring -----------------------------------------------------------------------------------------


def test_build_app_builds_sending_on_its_clock_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    clocks: list[FrozenClock] = []
    given: list[tuple[object, dict[str, Any]]] = []
    real_sending = main.Sending

    def system_clock() -> FrozenClock:
        clocks.append(FrozenClock(T))
        return clocks[-1]

    def sending(clock: Any, **kwargs: Any) -> Any:
        given.append((clock, kwargs))
        return real_sending(clock, **kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "SystemClock", system_clock)
    monkeypatch.setattr(main, "Sending", sending)

    main.build_app(load_bootstrap(), units=FakeUnits())

    [clock] = clocks
    [(given_clock, kwargs)] = given
    assert given_clock is clock
    assert kwargs["env"] == "test"
    assert set(kwargs) == {"env", "domain_checks", "notify"}  # Story 6.2: connect needs both


# Sending Domain authentication (Story 6.5) ------------------------------------------------------

AUTH = "/inboxes/auth"
RECHECK = "/inboxes/auth/recheck"
AUTH_HEADINGS = [
    "Sending Domain",
    "Last checked",
    "SPF",
    "DKIM",
    "DMARC",
    "Alignment",
    "DNSBL",
    "Spam rate",
    "Cold sends",
    "Recheck",
]
AUTH_SECTION = {
    "id": "domain-auth",
    "hx-get": "/inboxes/auth",
    "hx-trigger": "every 30s",
    "hx-swap": "outerHTML",
    "aria-labelledby": "domain-auth-heading",
}
RECHECK_FORM = {
    "method": "post",
    "hx-post": "/inboxes/auth/recheck",
    "hx-target": "#domain-auth",
    "hx-swap": "outerHTML",
    "hx-sync": "this:drop",
}
DOMAIN = "nextron-ai.com"
SPF_OK = "v=spf1 include:_spf.google.com ~all"
DKIM_OK = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA"
DMARC_OK = "v=DMARC1; p=none"
UNKNOWN_ID = "01JD3QZ8X5V9K2M7N4P6R8T0D9"


class AuthSection(HTMLParser):
    """The ``#domain-auth`` section: its attributes, heading, notices (role and text), other
    paragraphs (class and text), table rows (cells), the cells marked ``auth-blocked`` and its
    forms (attributes and inputs)."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict[str, str | None]] = []
        self.heading = ""
        self.notices: list[tuple[str | None, str]] = []
        self.paragraphs: list[tuple[str, str]] = []
        self.rows: list[list[str]] = []
        self.blocked: list[str] = []
        self.forms: list[tuple[dict[str, str | None], list[dict[str, str | None]]]] = []
        self._inside = False
        self._text: list[str] | None = None
        self._kind: tuple[str, str | None] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_blocked = False
        self._heading: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "section" and found.get("id") == "domain-auth":
            self.sections.append(found)
            self._inside = True
            return
        if not self._inside:
            return
        if tag == "h2":
            self._heading = []
        elif tag == "form":
            self.forms.append((found, []))
        elif tag == "input" and self.forms:
            self.forms[-1][1].append(found)
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
            self._cell_blocked = found.get("class") == "auth-blocked"
        elif tag == "p":
            self._text = []
            self._kind = (found.get("class") or "", found.get("role"))

    def handle_endtag(self, tag: str) -> None:
        if not self._inside:
            return
        if tag == "section":
            self._inside = False
        elif tag == "h2" and self._heading is not None:
            self.heading = "".join(self._heading)
            self._heading = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = " ".join(" ".join(self._cell).split())
            self._row.append(text)
            if self._cell_blocked:
                self.blocked.append(text)
            self._cell = None
        elif tag == "p" and self._text is not None and self._kind is not None:
            text = " ".join("".join(self._text).split())
            css, role = self._kind
            if "notice" in css.split():
                self.notices.append((role, text))
            else:
                self.paragraphs.append((css, text))
            self._text = None
            self._kind = None

    def handle_data(self, data: str) -> None:
        if not self._inside:
            return
        if self._heading is not None:
            self._heading.append(data)
        if self._cell is not None:
            self._cell.append(data)
        elif self._text is not None:
            self._text.append(data)


def auth_of(reply: Reply) -> AuthSection:
    found = AuthSection(reply.text)
    assert len(found.sections) == 1
    assert found.sections[0] == AUTH_SECTION
    assert found.heading == "Sending Domain authentication"
    return found


def get_auth(dashboard: Dashboard, cookie: bytes) -> Reply:
    reply = dashboard.get(AUTH, cookie=cookie)
    assert reply.status == 200, reply
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    assert reply.header(b"cache-control") == b"no-store"  # it carries the CSRF token
    return reply


def recheck(dashboard: Dashboard, session: tuple[bytes, str], body: bytes) -> Reply:
    cookie, token = session
    return dashboard.post(
        RECHECK, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )


def domain_id(units: UnitOfWorkFactory) -> str:
    with units.open(read_only=True):
        [view] = sending_queries.inboxes()
    return view.sending_domain_id


def check_jobs(db_engine: Engine) -> list[tuple[str, str, str]]:
    with db_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT type, lane, idempotency_key FROM jobs WHERE type = 'check_domains'"
        ).all()
    return [tuple(row) for row in rows]  # type: ignore[misc]


def recorded_check(dashboard: Dashboard, units: UnitOfWorkFactory, sending_domain_id: str) -> None:
    with units.open():
        dashboard.domain_checks.check_domain(sending_domain_id)


def test_the_page_shows_the_authentication_section_after_the_panel_with_its_empty_state(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, session[0])
    section = auth_of(reply)

    assert reply.text.index('id="inboxes-panel"') < reply.text.index('id="domain-auth"')
    assert ("auth-empty", "No Sending Domains to check yet.") in section.paragraphs
    assert (section.rows, section.forms, section.notices) == ([], [], [])
    assert auth_of(get_auth(dashboard, session[0])).paragraphs == section.paragraphs


def test_a_listed_domain_never_checked_is_blocked_with_a_recheck_form(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(ready, session, FIRST)
    found_id = domain_id(units)

    reply = get_auth(ready, session[0])
    section = auth_of(reply)

    assert "<title>" not in reply.text  # the section alone, swapped in by HTMX
    assert section.rows == [
        AUTH_HEADINGS,
        [DOMAIN, *["Not checked yet"] * 7, "Cold sends blocked: not checked yet", "Recheck"],
    ]
    assert section.blocked == ["Cold sends blocked: not checked yet"]
    [(attrs, inputs)] = section.forms
    assert attrs == RECHECK_FORM
    assert inputs == [
        {"type": "hidden", "name": "csrf_token", "value": session[1]},
        {"type": "hidden", "name": "sending_domain_id", "value": found_id},
    ]
    assert f'aria-label="Recheck {DOMAIN}"' in reply.text


def test_the_section_shows_a_newly_recorded_result(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(ready, session, FIRST)
    found_id = domain_id(units)
    dns = ready.dns
    assert isinstance(dns, FakeDns)
    dns.txt_answers.update(
        {
            DOMAIN: [SPF_OK],
            f"google._domainkey.{DOMAIN}": [DKIM_OK],
            f"_dmarc.{DOMAIN}": [DMARC_OK],
        }
    )
    recorded_check(ready, units, found_id)

    passing = auth_of(get_auth(ready, session[0]))

    assert passing.rows[1] == [
        DOMAIN,
        "2026-11-17 09:00 UTC",
        "Pass",
        "Pass",
        "Pass",
        "Pass",
        "Clean",
        "No data",
        "Cold sends allowed",
        "Recheck",
    ]
    assert passing.blocked == []
    ready.clock.set(datetime(2026, 11, 17, 10, 30, tzinfo=UTC))
    dns.txt_answers[DOMAIN] = ["v=spf1 include:_spf.example.net -all"]
    dns.txt_answers[f"_dmarc.{DOMAIN}"] = []
    recorded_check(ready, units, found_id)

    failing = auth_of(get(ready, session[0]))  # the page shows it too

    assert failing.rows[1] == [
        DOMAIN,
        "2026-11-17 10:30 UTC",
        f"Fail: {REASON_TEXTS[AuthReason.SPF_NO_GOOGLE]}",
        "Pass",
        f"Fail: {REASON_TEXTS[AuthReason.DMARC_MISSING]}",
        "Pass",
        "Clean",
        "No data",
        "Cold sends blocked: SPF, DMARC failing",
        "Recheck",
    ]
    assert failing.blocked == ["Cold sends blocked: SPF, DMARC failing"]


def test_a_listing_and_a_pausing_spam_rate_are_highlighted(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(ready, session, FIRST)
    found_id = domain_id(units)
    dns, postmaster = ready.dns, ready.postmaster
    assert isinstance(dns, FakeDns)
    assert isinstance(postmaster, FakePostmaster)
    dns.txt_answers.update(
        {
            DOMAIN: [SPF_OK],
            f"google._domainkey.{DOMAIN}": [DKIM_OK],
            f"_dmarc.{DOMAIN}": [DMARC_OK],
        }
    )
    dns.a_answers[f"{DOMAIN}.dbl.spamhaus.org"] = ("127.0.1.2",)
    postmaster.rates[DOMAIN] = 3000
    recorded_check(ready, units, found_id)

    section = auth_of(get_auth(ready, session[0]))

    assert section.rows[1][6:8] == ["Listed (dbl.spamhaus.org)", "0.30%"]
    assert section.blocked == ["Listed (dbl.spamhaus.org)", "0.30%"]


def test_a_recheck_queues_one_job_and_says_so_each_time(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    register(ready, session, FIRST)
    found_id = domain_id(units)
    body = form(csrf_token=session[1], sending_domain_id=found_id)
    notice = ("status", f"Recheck queued for {DOMAIN}; the result appears here when it lands.")

    first = recheck(ready, session, body)
    second = recheck(ready, session, body)

    for reply in (first, second):
        assert reply.status == 200, reply
        assert "<title>" not in reply.text
        assert auth_of(reply).notices == [notice]
    assert check_jobs(db_engine) == [
        ("check_domains", "interactive", f"check_domains:{found_id}:tick")
    ]


def test_a_recheck_of_an_unlisted_or_unknown_domain_alerts_and_queues_nothing(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    register(ready, session, FIRST)
    found_id = domain_id(units)
    with db_engine.connect() as connection:
        connection.exec_driver_sql("UPDATE sending_inboxes SET status = 'removed'")
        connection.commit()
    alert = ("alert", "That Sending Domain has no inboxes to check.")

    for target in (found_id, UNKNOWN_ID):
        reply = recheck(ready, session, form(csrf_token=session[1], sending_domain_id=target))

        assert reply.status == 200, reply
        section = auth_of(reply)
        assert section.notices == [alert]
        assert ("auth-empty", "No Sending Domains to check yet.") in section.paragraphs
    assert check_jobs(db_engine) == []


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({}, "nothing"),
        ({"sending_domain_id": "not-a-ulid"}, "not a ULID"),
        ({"sending_domain_id": UNKNOWN_ID, "domain": DOMAIN}, "an extra field"),
        ({"domain": DOMAIN}, "the wrong field"),
    ],
)
def test_a_recheck_form_without_exactly_one_ulid_is_malformed(
    ready: Dashboard,
    session: tuple[bytes, str],
    db_engine: Engine,
    fields: dict[str, str],
    why: str,
) -> None:
    reply = recheck(ready, session, form(csrf_token=session[1], **fields))

    assert reply.status == 400, why
    assert reply.body == MALFORMED
    assert check_jobs(db_engine) == []


def test_a_repeated_domain_id_is_malformed(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    register(ready, session, FIRST)
    found_id = domain_id(units)
    body = form(csrf_token=session[1], sending_domain_id=found_id) + (
        f"&sending_domain_id={found_id}".encode()
    )

    reply = recheck(ready, session, body)

    assert reply.status == 400
    assert reply.body == MALFORMED
    assert check_jobs(db_engine) == []


def test_a_recheck_without_the_csrf_token_is_refused_with_403(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    register(ready, session, FIRST)
    reply = ready.post(
        RECHECK,
        cookie=session[0],
        headers=[(b"content-type", FORM)],
        body=form(sending_domain_id=domain_id(units)),
    )

    assert reply.status == 403
    assert reply.body == REFUSED
    assert check_jobs(db_engine) == []


def test_the_section_needs_a_session(dashboard: Dashboard) -> None:
    assert dashboard.get(AUTH).status == 401
    assert dashboard.post(RECHECK, headers=[(b"content-type", FORM)]).status == 401


def test_a_hostile_stored_domain_renders_as_escaped_text(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Engine
) -> None:
    hostile = '<script>alert(1)</script>"><b hx-post="/x">.com'
    stored_id = new_id()
    with db_engine.connect() as connection:  # past the app's checks, as a corrupted row would be
        connection.exec_driver_sql(
            "INSERT INTO sending_domains (id, domain, created_at) VALUES (?, ?, ?)",
            (stored_id, hostile, "2026-11-17T09:00:00.000000Z"),
        )
        connection.exec_driver_sql(
            "INSERT INTO sending_inboxes (id, address, label, sending_domain_id, from_name, "
            "status, created_at, status_changed_at) "
            "VALUES (?, 'a@b.example', 'Main', ?, 'F', 'active', ?, ?)",
            (new_id(), stored_id, *["2026-11-17T09:00:00.000000Z"] * 2),
        )
        connection.commit()

    reply = get_auth(dashboard, session[0])

    assert "<script>" not in reply.text and "<b hx-post" not in reply.text
    assert reply.text.count('hx-post="') == 1  # only the Recheck form's own
    assert auth_of(reply).rows[1][0] == hostile


# Warm-up and Daily Caps (Story 6.4) -------------------------------------------------------------

WARMUP_FORM = {
    "method": "post",
    "hx-post": "/inboxes/warmup",
    "hx-target": "#inboxes-panel",
    "hx-swap": "outerHTML",
    "hx-sync": "this:drop",
}
WARMUP_NAMES = [
    "csrf_token",
    "inbox_id",
    "warmup_started_on",
    "warmup_method",
    "warm_level",
    "warmup_tool_daily",
    "ramp_step",
    "ramp_target",
]
GOOD = {
    "warmup_started_on": "2026-11-01",
    "warmup_method": "Manual",
    "warm_level": "12",
    "warmup_tool_daily": "0",
    "ramp_step": "5",
    "ramp_target": "30",
}


@pytest.fixture
def zoned(ready: Dashboard, units: UnitOfWorkFactory) -> Dashboard:
    """``ready`` with the Sender's timezone set (09:00 UTC is 14:30 in Kolkata)."""
    with units.open():
        Setup(ready.clock).save({"sender_timezone": "Asia/Kolkata"})
    return ready


def inbox_id_of(units: UnitOfWorkFactory, address: str = FIRST) -> str:
    with units.open(read_only=True):
        [found] = [view.id for view in sending_queries.inboxes() if view.address == address]
    return found


def post_warmup(dashboard: Dashboard, session: tuple[bytes, str], **fields: str) -> Reply:
    cookie, token = session
    return dashboard.post(
        WARMUP,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, **fields),
    )


def save_warmup(
    dashboard: Dashboard, session: tuple[bytes, str], inbox_id: str, **changes: str
) -> Reply:
    reply = post_warmup(dashboard, session, inbox_id=inbox_id, **{**GOOD, **changes})
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return reply


def stored_warmup(units: UnitOfWorkFactory) -> list[tuple[Any, ...]]:
    with units.open(read_only=True):
        return [
            (
                view.warmup_started_on,
                view.warmup_method,
                view.warm_level,
                view.warmup_tool_daily,
                view.ramp_step,
                view.ramp_target,
            )
            for view in sending_queries.inboxes()
        ]


def test_each_inbox_shows_its_unrecorded_warmup_and_a_prefilled_form(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(zoned, session, FIRST, "Main")
    inbox_id = inbox_id_of(units)

    panel = panel_of(get(zoned, session[0]))

    assert panel.facts == [
        [
            ("Recorded warm-up", "Not recorded"),
            ("Today's cap", "0 (inbox not active)"),
            ("Cap used", "0 of 0"),
            ("Ceiling", "50 a day, 0 of them warm-up tool sends"),
        ]
    ]
    [(attrs, inputs)] = [entry for entry in panel.forms if entry[0].get("hx-post") == WARMUP]
    assert attrs == WARMUP_FORM
    assert [field.get("name") for field in inputs] == WARMUP_NAMES
    assert inputs[0] == {"type": "hidden", "name": "csrf_token", "value": session[1]}
    assert inputs[1] == {"type": "hidden", "name": "inbox_id", "value": inbox_id}
    assert panel.warmup_forms() == [
        {
            "csrf_token": session[1],
            "inbox_id": inbox_id,
            "warmup_started_on": "",
            "warmup_method": "",
            "warm_level": "",
            "warmup_tool_daily": "0",
            "ramp_step": "5",
            "ramp_target": "30",
        }
    ]


def test_a_saved_warmup_says_so_and_shows_the_recorded_days(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(zoned, session, FIRST, "Main")
    inbox_id = inbox_id_of(units)

    reply = save_warmup(zoned, session, inbox_id)
    panel = panel_of(reply)

    assert "<title>" not in reply.text  # the panel alone, swapped in by HTMX
    assert panel.notices == [("status", "Warm-up saved.")]
    assert panel.facts[0][0] == ("Recorded warm-up", "16 days")
    assert panel.warmup_forms()[0]["warmup_method"] == "manual"  # as stored
    assert panel.warmup_forms()[0]["warmup_started_on"] == "2026-11-01"
    assert stored_warmup(units) == [(date(2026, 11, 1), "manual", 12, 0, 5, 30)]


def test_an_active_warmed_inbox_shows_its_cold_cap_under_the_ceiling(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    register(zoned, session, FIRST, "Main")
    inbox_id = inbox_id_of(units)
    with db_engine.connect() as connection:
        connection.exec_driver_sql("UPDATE sending_inboxes SET status = 'active'")
        connection.commit()

    panel = panel_of(
        save_warmup(
            zoned,
            session,
            inbox_id,
            warmup_started_on="2026-10-01",
            warm_level="30",
            warmup_tool_daily="25",
        )
    )

    assert panel.facts == [
        [
            ("Recorded warm-up", "47 days"),
            ("Today's cap", "25"),
            ("Cap used", "0 of 25"),
            ("Ceiling", "50 a day, 25 of them warm-up tool sends"),
        ]
    ]


def test_a_refused_warmup_shows_the_message_and_keeps_that_inboxs_typed_values(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(zoned, session, FIRST, "Main")
    register(zoned, session, "mac@nextron-ai.com", "Second")
    first, second = inbox_id_of(units), inbox_id_of(units, "mac@nextron-ai.com")

    panel = panel_of(save_warmup(zoned, session, second, warmup_started_on="2026-11-18"))

    assert panel.notices == [("alert", "The warm-up start date can't be after today")]
    forms = {values["inbox_id"]: values for values in panel.warmup_forms()}
    assert forms[second]["warmup_started_on"] == "2026-11-18"
    assert forms[second]["warmup_method"] == "Manual"  # as typed
    assert forms[first]["warmup_started_on"] == ""  # the other inbox shows storage
    assert stored_warmup(units) == [(None, None, None, 0, 5, 30)] * 2


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"warm_level": "101"}, "Enter the warm level as a whole number from 0 to 100"),
        ({"warm_level": "abc"}, "Enter the warm level as a whole number from 0 to 100"),
        ({"warmup_started_on": "1 Nov"}, "Enter the warm-up start date as YYYY-MM-DD"),
        ({"ramp_target": "51"}, "Enter the ramp target as a whole number from 1 to 50"),
    ],
)
def test_each_bad_field_is_refused_with_its_message(
    zoned: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    changes: dict[str, str],
    message: str,
) -> None:
    register(zoned, session, FIRST, "Main")

    panel = panel_of(save_warmup(zoned, session, inbox_id_of(units), **changes))

    assert panel.notices == [("alert", message)]
    assert stored_warmup(units) == [(None, None, None, 0, 5, 30)]


def test_an_unknown_inbox_and_an_unset_timezone_are_refused(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(ready, session, FIRST, "Main")

    no_zone = panel_of(save_warmup(ready, session, inbox_id_of(units)))
    with units.open():
        Setup(ready.clock).save({"sender_timezone": "UTC"})
    unknown = panel_of(save_warmup(ready, session, new_id()))

    assert no_zone.notices == [("alert", "Set the Sender's timezone on the Settings page first")]
    assert unknown.notices == [("alert", "That inbox isn't registered")]
    assert stored_warmup(units) == [(None, None, None, 0, 5, 30)]


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"ramp_target": None}, "a field missing"),
        ({"status": "active"}, "an extra field"),
        ({"inbox_id": "not-a-ulid"}, "an inbox id that isn't a ULID"),
        ({"inbox_id": None}, "no inbox id"),
    ],
)
def test_a_warmup_form_without_exactly_its_fields_is_malformed(
    zoned: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    change: dict[str, str | None],
    why: str,
) -> None:
    register(zoned, session, FIRST, "Main")
    fields: dict[str, str | None] = {"inbox_id": inbox_id_of(units), **GOOD, **change}

    reply = post_warmup(
        zoned, session, **{name: value for name, value in fields.items() if value is not None}
    )

    assert reply.status == 400, why
    assert reply.body == MALFORMED
    assert stored_warmup(units) == [(None, None, None, 0, 5, 30)]


def test_a_repeated_warmup_field_is_malformed(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(zoned, session, FIRST, "Main")
    cookie, token = session
    body = form(csrf_token=token, inbox_id=inbox_id_of(units), **GOOD) + b"&warm_level=13"

    reply = zoned.post(
        WARMUP, cookie=cookie, token=token, headers=[(b"content-type", FORM)], body=body
    )

    assert reply.status == 400
    assert reply.body == MALFORMED
    assert stored_warmup(units) == [(None, None, None, 0, 5, 30)]


def test_a_warmup_post_without_the_csrf_token_is_refused_with_403(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(zoned, session, FIRST, "Main")

    reply = zoned.post(
        WARMUP,
        cookie=session[0],
        headers=[(b"content-type", FORM)],
        body=form(inbox_id=inbox_id_of(units), **GOOD),
    )

    assert reply.status == 403
    assert reply.body == REFUSED
    assert stored_warmup(units) == [(None, None, None, 0, 5, 30)]


def test_a_hostile_tool_name_is_stored_and_rendered_escaped(
    zoned: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(zoned, session, FIRST, "Main")
    hostile = '"><script>alert(1)</script>'

    reply = save_warmup(zoned, session, inbox_id_of(units), warmup_method=hostile)
    again = get(zoned, session[0])

    for text in (reply.text, again.text):
        assert "<script>" not in text
        assert panel_of_text(text).warmup_forms()[0]["warmup_method"] == hostile
    assert stored_warmup(units)[0][1] == hostile


def panel_of_text(text: str) -> Panel:
    found = Panel(text)
    assert found.panels == 1
    return found


# Connecting through Gmail OAuth (Story 6.2) ---------------------------------------------------

CONNECT = "/inboxes/connect"
RECONNECT = "/inboxes/reconnect"
CONNECT_BUTTON = {"type": "submit", "hx-post": CONNECT}


class ConnectStatus(HTMLParser):
    """The ``#inbox-connect-status`` div: its attributes and its notices (role and text)."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: list[dict[str, str | None]] = []
        self.notices: list[tuple[str | None, str]] = []
        self._inside = False
        self._role: str | None = None
        self._text: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "div" and found.get("id") == "inbox-connect-status":
            self.attrs.append(found)
            self._inside = True
        elif self._inside and tag == "p" and "notice" in (found.get("class") or "").split():
            self._role, self._text = found.get("role"), []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._text is not None:
            self.notices.append((self._role, " ".join("".join(self._text).split())))
            self._text = None
        elif tag == "div" and self._inside:
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)


@pytest.fixture
def gated(ready: Dashboard, units: UnitOfWorkFactory) -> Dashboard:
    """``ready`` with the OQ-A2 gate on and the Desktop client imported."""
    from nextron.domain.google_oauth import CLIENT_SECRET_NAME, ClientConfig, encode_client_config
    from tests.fakes.google_oauth import CLIENT_ID

    with units.open():
        Setup(ready.clock).save({"gmail_internal_app_confirmed": "true"})
    ready.secrets.write(
        CLIENT_SECRET_NAME, encode_client_config(ClientConfig(CLIENT_ID, "GOCSPX-secret"))
    )
    return ready


def connect(
    dashboard: Dashboard, session: tuple[bytes, str], address: str = FIRST, label: str = "Main"
) -> Reply:
    cookie, token = session
    reply = dashboard.post(
        CONNECT,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, address=address, label=label),
    )
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return reply


def test_the_form_has_a_connect_inbox_button(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = get(dashboard, session[0])

    assert '<button type="submit" hx-post="/inboxes/connect">Connect inbox</button>' in reply.text
    [status] = ConnectStatus(reply.text).attrs
    assert status == {"id": "inbox-connect-status"}  # nothing waits: no polling


def test_connect_inbox_starts_one_flow_and_the_panel_polls_while_it_waits(
    gated: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = connect(gated, session)

    assert len(gated.browser.urls) == 1 and len(gated.listeners) == 1 and len(gated.jobs) == 1
    status = ConnectStatus(reply.text)
    assert status.attrs == [
        {
            "id": "inbox-connect-status",
            "hx-get": CONNECT,
            "hx-trigger": "every 2s",
            "hx-swap": "outerHTML",
        }
    ]
    [(role, text)] = status.notices
    assert role == "status" and text.startswith(f"Waiting for Google sign-in for {FIRST}")
    assert stored(units) == []  # nothing is stored before Google answers
    polled = gated.get(CONNECT, cookie=session[0])
    assert polled.status == 200 and polled.header(b"cache-control") == b"no-store"
    assert ConnectStatus(polled.text).notices == status.notices
    assert len(gated.listeners) == 1  # the GET started nothing


def test_a_completed_connect_shows_connected_and_stops_polling(
    gated: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    from urllib.parse import parse_qs, urlsplit

    from nextron.domain.sending_inbox import GMAIL_SCOPE
    from nextron.ports.google_oauth import TokenSet

    connect(gated, session)
    state = parse_qs(urlsplit(gated.browser.urls[-1]).query)["state"][0]
    gated.listeners[-1].answer = {"state": (state,), "code": ("4/code",)}
    gated.oauth.exchanges.append(
        TokenSet(access_token="ya29.a", expires_in=3599, scope=GMAIL_SCOPE, refresh_token="1//r")
    )
    gated.gmail.profiles["ya29.a"] = FIRST
    gated.run_jobs()

    polled = gated.get(CONNECT, cookie=session[0])
    status = ConnectStatus(polled.text)
    assert status.attrs == [{"id": "inbox-connect-status"}]
    assert status.notices == [
        ("status", f"Connected {FIRST}. Reload the page to see its status.")
    ]
    assert stored(units) == [(FIRST, "Main", "active")]
    panel = panel_of(get(gated, session[0]))
    assert panel.rows[1][3:] == ["active", "2026-11-17 09:00 UTC", ""]


def test_a_refused_connect_shows_its_message_keeps_the_values_and_starts_nothing(
    gated: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = connect(gated, session, "sales@nextron-ai.com", "Team")

    assert ConnectStatus(reply.text).notices == [("alert", "Role addresses can't send")]
    panel = panel_of(reply)
    assert panel.value("address") == "sales@nextron-ai.com" and panel.value("label") == "Team"
    assert gated.browser.urls == [] and gated.listeners == [] and stored(units) == []


def test_with_the_gate_off_connect_inbox_is_refused(
    ready: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = connect(ready, session)

    assert ConnectStatus(reply.text).notices == [
        (
            "alert",
            "Connecting inboxes waits for the Gmail spike (Story 6.1) to confirm the Internal "
            "OAuth app",
        )
    ]
    assert ready.browser.urls == [] and ready.listeners == []


def test_a_connecting_row_reconnects_through_its_own_form(
    gated: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    register(gated, session, FIRST, "Main")
    panel = panel_of(get(gated, session[0]))
    [(attrs, inputs)] = [entry for entry in panel.forms if entry[0].get("hx-post") == RECONNECT]
    assert attrs["method"] == "post" and attrs["hx-target"] == "#inboxes-panel"
    inbox_id = inbox_id_of(units)
    assert [field.get("name") for field in inputs] == ["csrf_token", "inbox_id"]
    assert inputs[1]["value"] == inbox_id

    cookie, token = session
    reply = gated.post(
        RECONNECT,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, inbox_id=inbox_id),
    )

    assert reply.status == 200
    assert len(gated.listeners) == 1
    assert ConnectStatus(reply.text).notices[0][1].startswith("Waiting for Google sign-in")


@pytest.mark.parametrize(
    "body",
    [
        {"inbox_id": "not-a-ulid"},
        {"inbox_id": "01JD3QZ8X5V9K2M7N4P6R8T0E1", "address": "x@y.com"},
        {},
    ],
)
def test_a_malformed_reconnect_is_refused_with_400(
    gated: Dashboard, session: tuple[bytes, str], body: dict[str, str]
) -> None:
    cookie, token = session
    reply = gated.post(
        RECONNECT,
        cookie=cookie,
        token=token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=token, **body),
    )

    assert reply.status == 400 and reply.body == MALFORMED
    assert gated.listeners == []


@pytest.mark.parametrize("path", [CONNECT, RECONNECT])
def test_a_connect_post_without_the_csrf_token_is_refused_with_403(
    gated: Dashboard, session: tuple[bytes, str], path: str
) -> None:
    reply = gated.post(
        path,
        cookie=session[0],
        headers=[(b"content-type", FORM)],
        body=form(address=FIRST, label="Main"),
    )

    assert reply.status == 403 and reply.body == REFUSED
    assert gated.listeners == [] and gated.browser.urls == []


def test_no_get_route_starts_a_flow(gated: Dashboard, session: tuple[bytes, str]) -> None:
    for path in (PAGE, CONNECT, "/inboxes/auth"):
        gated.get(path, cookie=session[0])
    assert gated.listeners == [] and gated.browser.urls == []
    reply = gated.get(RECONNECT, cookie=session[0])
    assert reply.status in (404, 405)  # POST only
    assert gated.listeners == []

def test_an_auth_failed_row_reconnects_and_a_paused_row_shows_its_cause(
    ready: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory, db_engine: Engine
) -> None:
    from nextron.db import eventlog
    from nextron.db.uow import current, use_case

    register(ready, session, FIRST, "Main")
    register(ready, session, "mac@nextron-ai.com", "Second")
    failed_id = inbox_id_of(units)
    paused_id = inbox_id_of(units, "mac@nextron-ai.com")
    with db_engine.connect() as connection:
        connection.exec_driver_sql(
            "UPDATE sending_inboxes SET status = 'auth_failed' WHERE label = 'Main'"
        )
        connection.exec_driver_sql(
            "UPDATE sending_inboxes SET status = 'paused' WHERE label = 'Second'"
        )
        connection.commit()

    @use_case
    def paused_by_operator() -> None:
        with current().transaction():
            eventlog.append(
                entity="sending_inboxes",
                entity_id=paused_id,
                from_status="active",
                to_status="paused",
                cause="operator",
                cause_ref=None,
                actor="operator",
                at=T,
            )

    with units.open():
        paused_by_operator()

    panel = panel_of(get(ready, session[0]))

    by_label = {row[0]: row for row in panel.rows[1:]}
    assert by_label["Main"][3:] == ["auth_failed", "", "Reconnect"]
    assert by_label["Second"][3:] == ["paused (operator)", "", ""]
    [(attrs, inputs)] = [entry for entry in panel.forms if entry[0].get("hx-post") == RECONNECT]
    assert attrs["method"] == "post"
    assert [(field.get("name"), field.get("value")) for field in inputs][1:] == [
        ("inbox_id", failed_id)
    ]


def test_the_connect_notices_for_a_failed_revoke_and_a_pause_at_once() -> None:
    from nextron.app.sending.connect import ConnectOutcome, Revocation
    from nextron.app.sending.connect import ConnectStatus as Status
    from nextron.domain.sending_inbox import ConnectFailure, PauseCause
    from nextron.web.inboxes_page import connect_view

    failed = connect_view(
        Status(
            None,
            None,
            ConnectOutcome(
                FIRST, ConnectFailure.DIFFERENT_ACCOUNT, revocation=Revocation.FAILED
            ),
        )
    )
    assert failed.notice is not None and failed.notice.role == "alert"
    assert failed.notice.text == (
        "Signed in as a different account The new grant couldn't be revoked; remove Nextron's "
        "access at myaccount.google.com."
    )
    assert failed.notice.text.endswith("remove Nextron's access at myaccount.google.com.")
    paused = connect_view(
        Status(None, None, ConnectOutcome(FIRST, None, new_id(), PauseCause.BLOCKLIST))
    )
    assert paused.notice is not None and paused.reload
    assert paused.notice.text == (
        f"Connected {FIRST}; it was paused at once (blocklist). Reload the page to see its status."
    )
