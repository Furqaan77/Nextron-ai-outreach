"""The Sending Inboxes panel (``GET /inboxes`` and ``POST /inboxes``, FR-32, AD-14, UX-DR26,
Story 4.7) through ``Dashboard(units=units)`` on a temporary database at head: the page lists
the inboxes with the connecting note; a POST registers (``Inbox registered.``, the form
cleared) or shows each refusal's message with the typed values kept; the crowding warning; the
empty state; malformed and repeated fields (400) and a POST without the CSRF token (403);
``no-store``; a label holding ``<script>`` renders escaped; the Settings page link; a
``removed`` inbox left out of the crowding count; the form's ``hx-sync="this:drop"``; and
``main.build_app`` building ``Sending`` on its clock and environment."""

from datetime import UTC, datetime
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
from nextron.domain.sending_inbox import CONNECTING_NOTE
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
PAGE = "/inboxes"
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
HEADINGS = ["Label", "Address", "Sending Domain", "Status"]


class Panel(HTMLParser):
    """The ``#inboxes-panel`` div: its notices (role and text), its warnings, its forms
    (attributes and inputs), its table rows (cells) and its other paragraphs."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.panels = 0
        self.notices: list[tuple[str | None, str]] = []
        self.warnings: list[str] = []
        self.paragraphs: list[str] = []
        self.forms: list[tuple[dict[str, str | None], list[dict[str, str | None]]]] = []
        self.rows: list[list[str]] = []
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
        elif self._text is not None:
            self._text.append(data)

    def value(self, name: str) -> str | None:
        [(_, inputs)] = self.forms
        [found] = [field for field in inputs if field.get("name") == name]
        return found.get("value")


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
    [(attrs, inputs)] = panel.forms
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
        ["Main", FIRST, "nextron-ai.com", f"connecting {CONNECTING_NOTE}"],
        ["Second", "mac@nextron-ai.com", "nextron-ai.com", f"connecting {CONNECTING_NOTE}"],
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

    assert panel.rows == [HEADINGS, ["Main", FIRST, "nextron-ai.com", "active"]]


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
    assert panel.rows[1] == ["Main", FIRST, "nextron-ai.com", f"connecting {CONNECTING_NOTE}"]
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
            "status, created_at) VALUES (?, ?, 'Gone', ?, ?, 'removed', ?)",
            (new_id(), FIRST, domain_id, SENDER, "2026-11-17T09:00:00.000000Z"),
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
    assert reply.text.count('hx-post="') == 1  # only the form's own
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
    assert given == [(clock, {"env": "test"})]
