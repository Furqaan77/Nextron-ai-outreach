"""The Credentials page (AD-12, FR-56, NFR-18, UX-DR27) through ``Dashboard``, with the HTML
parsed: the Panel, Save, Replace, Refused, Remove, Store fails, Malformed and Unchecked rows, the
form rules, the Settings link and the wiring in ``main.build_app``; and its Unsubscribe token
keys section (Story 3.8): the listing, Save key, Bad key, Remove in use, Remove retired, the
refusals, a store failure, malformed forms and the read-only unit each POST runs in."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi.routing import APIRoute

from nextron import main
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory, current, in_transaction
from nextron.web.credentials_page import STORE_FAILED
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM, REPEATED_FIELD, WRONG_FIELDS
from nextron.web.routing import PERSONAL_DATA_ATTRIBUTE
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.migration_chains import run_sql
from tests.unit.test_credentials import TABLE

T = datetime(2026, 11, 17, 9, 30, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 17, 10, 45, 12, 345678, tzinfo=UTC)
SERVICE = "nextron-engine/test"
MARK = "QZXVALUE7"
NON_BMP = "\U0001f511"
KEYS = [key for key, *_ in TABLE]
LABELS = {key: label for key, label, *_ in TABLE}
SECRET_NAMES = {key: name for key, _, name, _ in TABLE}
HINT = (
    "At most 1,280 characters; a character outside the Basic Multilingual Plane, such as an "
    "emoji, counts as 2. Spaces at either end are dropped. The value is never shown."
)
DECLINE = (
    "If your browser offers to save or fill a password on this page, decline: credentials "
    "belong only in Windows Credential Manager."
)
UNREADABLE = "Unknown: Windows Credential Manager could not be read"
VOID = frozenset({"input", "meta", "link", "br", "img"})
UNSUB_ROW = "unsub-key-"


# Parsing the page ------------------------------------------------------------------------------


@dataclass
class Element:
    """One element: its attributes, its text (whitespace collapsed), and the credential row and
    form it sits in."""

    tag: str
    attrs: dict[str, str | None]
    row: str | None
    form: int | None
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join("".join(self.parts).split())


class Page(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[Element] = []
        self.forms: list[Element] = []
        self._open: list[Element] = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        parent = self._open[-1] if self._open else None
        row = parent.row if parent else None
        if tag == "div" and values.get("class") == "credential":
            row = (values.get("id") or "").removeprefix("credential-")
        elif tag == "div" and values.get("class") == "credential unsub-key":
            row = values.get("id")  # unsub-key-<kid>-<key>
        form_index = parent.form if parent else None
        if tag == "form":
            form_index = len(self.forms)
        element = Element(tag, values, row, form_index)
        self.elements.append(element)
        if tag == "form":
            self.forms.append(element)
        if tag not in VOID:
            self._open.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index].tag == tag:
                element = self._open.pop(index)
                if self._open:
                    self._open[-1].parts.append("".join(element.parts))
                return

    def handle_data(self, data: str) -> None:
        if self._open:
            self._open[-1].parts.append(data)

    def find(self, tag: str, *, row: str | None = None, **attrs: str) -> list[Element]:
        return [
            element
            for element in self.elements
            if element.tag == tag
            and (row is None or element.row == row)
            and all(element.attrs.get(name.rstrip("_")) == value for name, value in attrs.items())
        ]

    def texts(self, tag: str, *, row: str | None = None, **attrs: str) -> list[str]:
        return [element.text for element in self.find(tag, row=row, **attrs)]

    @property
    def row_keys(self) -> list[str]:
        return [element.row or "" for element in self.find("div", class_="credential")]

    def state(self, key: str) -> str:
        [text] = self.texts("p", row=key, class_="credential-state")
        return text

    def notices(self, key: str) -> list[tuple[str | None, str]]:
        return [
            (element.attrs.get("role"), element.text)
            for element in self.find("p", row=key)
            if element.attrs.get("role")
        ]

    def row_forms(self, key: str) -> list[Element]:
        return [form_ for form_ in self.forms if form_.row == key]

    @property
    def credential_forms(self) -> list[Element]:
        """The forms of the credential rows; the Google section's and the unsubscribe keys'
        forms aside."""
        return [
            form_
            for form_ in self.forms
            if form_.row is not None and not form_.row.startswith(UNSUB_ROW)
        ]

    @property
    def unsub_rows(self) -> list[str]:
        """The ids of the unsubscribe token key rows, in page order."""
        return [str(e.row) for e in self.find("div", class_="credential unsub-key")]

    def inside(self, form_: Element, tag: str) -> list[Element]:
        index = self.forms.index(form_)
        return [e for e in self.elements if e.form == index and e.tag == tag]

    def hidden(self, form_: Element) -> dict[str, str | None]:
        return {
            str(element.attrs.get("name")): element.attrs.get("value")
            for element in self.inside(form_, "input")
            if element.attrs.get("type") == "hidden"
        }

    def buttons(self, form_: Element) -> list[str]:
        return [element.text for element in self.inside(form_, "button")]


# Fixtures and helpers ---------------------------------------------------------------------------


@pytest.fixture
def secrets() -> InMemorySecrets:
    return InMemorySecrets("test")


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T)


@pytest.fixture
def dashboard(
    secrets: InMemorySecrets, clock: FrozenClock, units: UnitOfWorkFactory
) -> Dashboard:
    # real units of work: the page's Unsubscribe token keys section reads the database
    return Dashboard(secrets=secrets, clock=clock, units=units)


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    """A session's cookie and its CSRF token."""
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def stored(secrets: InMemorySecrets) -> dict[str, str]:
    return {name: value for (service, name), value in secrets.store.items() if service == SERVICE}


def changes(secrets: InMemorySecrets) -> list[tuple[str, str]]:
    """Every keyring write and delete, ``session/open_code`` aside."""
    return [
        call
        for call in secrets.calls
        if call[0] in ("write", "delete") and not call[1].startswith("session/")
    ]


def post(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    path: str,
    body: bytes,
    *,
    header_token: bool = True,
) -> Reply:
    """POST ``body`` as HTMX does: the token in the header (and, from ``fields``, the field)."""
    cookie, token = session
    return dashboard.post(
        path,
        cookie=cookie,
        token=token if header_token else None,
        headers=[(b"content-type", FORM)],
        body=body,
    )


def fields(token: str, **values: str) -> bytes:
    return form(csrf_token=token, **values)


def save(dashboard: Dashboard, session: tuple[bytes, str], key: str, value: str) -> Reply:
    return post(
        dashboard, session, "/credentials/save", fields(session[1], credential=key, value=value)
    )


def remove(
    dashboard: Dashboard, session: tuple[bytes, str], key: str, *, confirm: bool = False
) -> Reply:
    extra = {"confirm": "yes"} if confirm else {}
    body = fields(session[1], credential=key, **extra)
    return post(dashboard, session, "/credentials/remove", body)


def credentials_page(dashboard: Dashboard, cookie: bytes) -> tuple[Reply, Page]:
    reply = dashboard.get("/credentials", cookie=cookie)
    assert reply.status == 200, reply
    return reply, Page(reply.text)


def row_reply(reply: Reply, key: str) -> Page:
    """The answer to a row POST: 200, no-store, and the row alone for HTMX to swap in."""
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    assert reply.text.startswith(f'<div class="credential" id="credential-{key}">')
    assert reply.text.rstrip().endswith("</div>")
    assert "<html" not in reply.text and "<nav" not in reply.text
    page = Page(reply.text)
    assert page.row_keys == [key]
    return page


def assert_value_field_is_empty(page: Page, key: str) -> None:
    [value_input] = page.find("input", row=key, name="value")
    assert value_input.attrs == {
        "type": "password",
        "id": f"value-{key}",
        "name": "value",
        "autocomplete": "new-password",  # Chrome and Edge ignore "off" on a password field
        "aria-describedby": f"hint-{key}",
    }


def named_controls(page: Page, form_: Element) -> list[tuple[str, str | None]]:
    """Each named control of ``form_`` with its value, in document order, as a browser
    collects them."""
    return [
        (str(element.attrs["name"]), element.attrs.get("value"))
        for element in page.elements
        if element.form == page.forms.index(form_)
        and element.tag in ("input", "select", "textarea", "button")
        and element.attrs.get("name")
    ]


def submit(
    dashboard: Dashboard, session: tuple[bytes, str], page: Page, form_: Element, **typed: str
) -> Reply:
    """Send ``form_`` as HTMX would: its named controls urlencoded (``typed`` fills a field),
    POSTed to its ``hx-post`` with the session's token header."""
    pairs = [(name, typed.get(name, value or "")) for name, value in named_controls(page, form_)]
    return post(dashboard, session, str(form_.attrs["hx-post"]), urlencode(pairs).encode())


# Row: Panel -------------------------------------------------------------------------------------


def test_the_panel_lists_every_credential_in_order_each_not_set(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply, page = credentials_page(dashboard, session[0])

    assert page.row_keys == KEYS
    for key, label, _, required in TABLE:
        assert page.texts("h2", row=key) == [label]
        assert page.texts("p", row=key, class_="credential-need") == [
            "required" if required else "optional"
        ]
        assert page.state(key) == "Not set"
        assert page.notices(key) == []
        [save_form] = page.row_forms(key)  # no Remove while not set
        assert save_form.attrs["hx-post"] == "/credentials/save"
    assert reply.header(b"cache-control") == b"no-store"


def test_a_saved_credential_shows_set_with_its_time_and_never_its_value(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    dashboard.credentials.save("hunter", MARK)

    reply, page = credentials_page(dashboard, session[0])

    assert page.state("hunter") == "Set, last updated 2026-11-17 09:30 UTC"
    assert [page.state(key) for key in KEYS if key != "hunter"] == ["Not set"] * 8
    assert [form_.attrs["hx-post"] for form_ in page.row_forms("hunter")] == [
        "/credentials/save",
        "/credentials/remove",
    ]
    assert stored(secrets)["hunter/api_key"] == MARK
    assert MARK not in reply.text


def test_a_set_credential_without_a_time_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    secrets.store[(SERVICE, "hunter/api_key")] = MARK
    secrets.store[(SERVICE, "meta/anthropic/api_key")] = "2026-11-17T09:30:00.000000Z"

    _, page = credentials_page(dashboard, session[0])

    assert page.state("hunter") == "Set, last update time unknown"
    assert page.state("claude") == "Not set"  # a time alone doesn't make it set


def test_the_page_extends_the_layout_and_links_back_to_settings(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply, page = credentials_page(dashboard, session[0])

    assert page.texts("title") == ["Nextron: Credentials"]
    assert page.texts("h1") == ["Credentials"]
    # the first paragraph after the layout's empty error notice (#request-error)
    assert page.texts("p")[0] == ""
    assert "Windows Credential Manager" in page.texts("p")[1]
    # the brand, the navigation (its sections unchanged: no Credentials link) and the way back
    assert [(a.attrs.get("href"), a.text) for a in page.find("a") if a.row is None] == [
        ("/", "Nextron"),
        ("/queue", "Queue"),
        ("/leads", "Leads/Pipeline"),
        ("/settings", "Settings"),
        ("/health/panel", "Health"),
        ("/settings", "Back to Settings"),
    ]
    assert page.texts("nav") == ["Queue Leads/Pipeline Settings Health"]
    assert DECLINE in page.texts("p")  # browsers may still offer their password manager


@pytest.mark.parametrize("env", ["dev", "test", "prod"])
def test_the_page_names_the_environment_its_values_are_stored_for(
    env: str, units: UnitOfWorkFactory
) -> None:
    dashboard = Dashboard(env=env, units=units)

    _, page = credentials_page(dashboard, dashboard.open_session())

    assert page.texts("p", class_="credential-env") == [
        f"Values on this page are stored for the {env} environment."
    ]


def test_the_page_needs_a_session(dashboard: Dashboard) -> None:
    reply = dashboard.get("/credentials")

    assert reply.status == 401
    assert b"<form" not in reply.body


# Form rules -------------------------------------------------------------------------------------


def test_every_form_posts_through_htmx_to_its_own_row_with_the_csrf_token(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    dashboard.credentials.save("telegram_bot", MARK)
    _, page = credentials_page(dashboard, session[0])

    assert len(page.credential_forms) == len(KEYS) + 1
    # every other form is the Google section's (tests/unit/test_google_page.py)
    assert {
        str(form_.attrs["hx-post"]) for form_ in page.forms if form_.row is None
    } == {"/credentials/google/client"}
    for form_ in page.credential_forms:
        key = form_.row
        assert key in KEYS
        assert {name: form_.attrs.get(name) for name in ("method", "hx-target", "hx-swap")} == {
            "method": "post",
            "hx-target": f"#credential-{key}",
            "hx-swap": "outerHTML",
        }
        assert form_.attrs["hx-post"] in ("/credentials/save", "/credentials/remove")
        hidden = page.hidden(form_)
        assert hidden["csrf_token"] == session[1]
        assert hidden["credential"] == key
        names = [element.attrs.get("name") for element in page.inside(form_, "input")]
        assert names.count("csrf_token") == 1


def test_each_save_form_has_an_empty_password_field_without_maxlength_and_its_hint(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    dashboard.credentials.save("hunter", MARK)
    _, page = credentials_page(dashboard, session[0])

    passwords = page.find("input", type="password")
    assert [element.row for element in passwords] == [
        *KEYS,  # then the unsubscribe token keys of key ids 1 (active) and 2 (next)
        "unsub-key-1-k_tok",
        "unsub-key-1-k_edge",
        "unsub-key-2-k_tok",
        "unsub-key-2-k_edge",
    ]
    for key in KEYS:
        assert_value_field_is_empty(page, key)
        [label] = page.find("label", row=key)
        assert label.attrs == {"for": f"value-{key}"}
        assert label.text == f"New value for {LABELS[key]}"
        assert page.texts("p", row=key, id=f"hint-{key}") == [HINT]
        [save_form, *_] = page.row_forms(key)
        assert page.hidden(save_form) == {"csrf_token": session[1], "credential": key}
        assert page.buttons(save_form) == ["Save"]
    assert not [element for element in page.elements if "maxlength" in element.attrs]
    assert not [e for e in passwords if "value" in e.attrs]


# Row: Save and Replace --------------------------------------------------------------------------


def test_saving_stores_the_stripped_value_and_answers_with_the_row(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    reply = save(dashboard, session, "claude", "  abc  ")
    page = row_reply(reply, "claude")

    assert stored(secrets) == {
        "anthropic/api_key": "abc",
        "meta/anthropic/api_key": "2026-11-17T09:30:00.000000Z",
    }
    assert page.notices("claude") == [("status", "Saved.")]
    assert page.state("claude") == "Set, last updated 2026-11-17 09:30 UTC"
    assert_value_field_is_empty(page, "claude")
    assert page.hidden(page.row_forms("claude")[0])["csrf_token"] == session[1]
    assert [form_.attrs["hx-post"] for form_ in page.row_forms("claude")] == [
        "/credentials/save",
        "/credentials/remove",
    ]
    assert [r.getMessage() for r in caplog.records if r.name == "nextron.app.setup.api"] == [
        "credential_saved"
    ]
    assert "abc" not in reply.text


def test_saving_again_replaces_the_value_and_shows_the_new_time(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets, clock: FrozenClock
) -> None:
    save(dashboard, session, "claude", "abc")
    clock.set(T2)

    page = row_reply(save(dashboard, session, "claude", "xyz"), "claude")

    assert stored(secrets) == {
        "anthropic/api_key": "xyz",
        "meta/anthropic/api_key": "2026-11-17T10:45:12.345678Z",
    }
    assert page.state("claude") == "Set, last updated 2026-11-17 10:45 UTC"


def test_a_save_touches_only_its_own_row(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    dashboard.credentials.save("hunter", MARK)

    save(dashboard, session, "prospeo", "abc")

    assert stored(secrets)["hunter/api_key"] == MARK
    assert set(stored(secrets)) == {
        "hunter/api_key",
        "meta/hunter/api_key",
        "prospeo/api_key",
        "meta/prospeo/api_key",
    }


# Row: Refused -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("   ", "enter a value"),
        ("", "enter a value"),
        (MARK + "x" * 1272, "secret exceeds 1,280 characters"),  # 1,281 characters
        (NON_BMP * 641, "secret exceeds 1,280 characters"),  # 641 characters, 1,282 units
    ],
    ids=["spaces", "empty", "1281 characters", "641 non-BMP"],
)
def test_a_refused_value_answers_200_with_the_reason_and_writes_nothing(
    value: str,
    reason: str,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
) -> None:
    dashboard.credentials.save("hunter", "kept")
    secrets.calls.clear()

    reply = save(dashboard, session, "hunter", value)
    page = row_reply(reply, "hunter")

    assert page.notices("hunter") == [("alert", f"Nothing was saved: {reason}.")]
    assert page.find("p", row="hunter", role="alert")[0].attrs["class"] == "notice notice-refused"
    assert page.state("hunter") == "Set, last updated 2026-11-17 09:30 UTC"  # as stored
    assert_value_field_is_empty(page, "hunter")
    assert changes(secrets) == []
    assert stored(secrets)["hunter/api_key"] == "kept"
    assert MARK not in reply.text


@pytest.mark.parametrize("value", ["x" * 1280, NON_BMP * 640, " " + "x" * 1280 + " "])
def test_exactly_1280_units_is_saved(
    value: str, dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    page = row_reply(save(dashboard, session, "hunter", value), "hunter")

    assert page.notices("hunter") == [("status", "Saved.")]
    assert stored(secrets)["hunter/api_key"] == value.strip()


# Row: Remove ------------------------------------------------------------------------------------


def test_remove_asks_first_and_deletes_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    dashboard.credentials.save("hunter", MARK)
    secrets.calls.clear()

    page = row_reply(remove(dashboard, session, "hunter"), "hunter")

    assert changes(secrets) == []
    assert stored(secrets)["hunter/api_key"] == MARK
    assert page.notices("hunter") == [
        ("alert", "Remove the Hunter API key from Windows Credential Manager?")
    ]
    assert page.state("hunter") == "Set, last updated 2026-11-17 09:30 UTC"
    [confirm_form] = page.row_forms("hunter")
    assert {
        name: confirm_form.attrs.get(name)
        for name in ("method", "hx-post", "hx-target", "hx-swap")
    } == {
        "method": "post",
        "hx-post": "/credentials/remove",
        "hx-target": "#credential-hunter",
        "hx-swap": "outerHTML",
    }
    assert page.hidden(confirm_form) == {
        "csrf_token": session[1],
        "credential": "hunter",
        "confirm": "yes",
    }
    assert page.buttons(confirm_form) == ["Yes, remove it"]
    assert [(a.attrs, a.text) for a in page.find("a", row="hunter")] == [
        ({"href": "/credentials"}, "Cancel")
    ]
    assert page.find("input", type="password") == []


def test_the_rendered_forms_send_what_each_step_needs_and_nothing_more(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    """Each form is sent as a browser would build it from its named controls: Remove only asks,
    "Yes, remove it" deletes, and Save stores the typed value."""
    dashboard.credentials.save("hunter", MARK)
    secrets.calls.clear()
    _, page = credentials_page(dashboard, session[0])
    save_form, remove_form = page.row_forms("hunter")

    assert {name for name, _ in named_controls(page, remove_form)} == {"csrf_token", "credential"}
    assert {name for name, _ in named_controls(page, save_form)} == {
        "csrf_token",
        "credential",
        "value",
    }
    buttons = [button for form_ in page.forms for button in page.inside(form_, "button")]
    assert buttons and not [button for button in buttons if "name" in button.attrs]

    asked = row_reply(submit(dashboard, session, page, remove_form), "hunter")

    assert changes(secrets) == []
    assert asked.state("hunter") == "Set, last updated 2026-11-17 09:30 UTC"
    [confirm_form] = asked.row_forms("hunter")
    assert sorted(named_controls(asked, confirm_form)) == [
        ("confirm", "yes"),
        ("credential", "hunter"),
        ("csrf_token", session[1]),
    ]
    assert not [b for b in asked.inside(confirm_form, "button") if "name" in b.attrs]

    removed = row_reply(submit(dashboard, session, asked, confirm_form), "hunter")

    assert changes(secrets) == [("delete", "hunter/api_key"), ("delete", "meta/hunter/api_key")]
    assert removed.state("hunter") == "Not set"
    [save_again] = removed.row_forms("hunter")

    saved = row_reply(submit(dashboard, session, removed, save_again, value="  new  "), "hunter")

    assert saved.notices("hunter") == [("status", "Saved.")]
    assert stored(secrets)["hunter/api_key"] == "new"


def test_confirmed_remove_deletes_the_entry_and_its_time(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dashboard.credentials.save("hunter", MARK)
    dashboard.credentials.save("claude", "abc")
    secrets.calls.clear()
    caplog.set_level(logging.INFO)

    page = row_reply(remove(dashboard, session, "hunter", confirm=True), "hunter")

    assert changes(secrets) == [("delete", "hunter/api_key"), ("delete", "meta/hunter/api_key")]
    assert set(stored(secrets)) == {"anthropic/api_key", "meta/anthropic/api_key"}
    assert page.notices("hunter") == [("status", "Removed.")]
    assert page.state("hunter") == "Not set"
    assert [form_.attrs["hx-post"] for form_ in page.row_forms("hunter")] == ["/credentials/save"]
    assert_value_field_is_empty(page, "hunter")
    assert [r.getMessage() for r in caplog.records if r.name == "nextron.app.setup.api"] == [
        "credential_removed"
    ]


# Row: Store fails -------------------------------------------------------------------------------


def test_a_failed_save_shows_the_alert_and_the_row_as_stored_now(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dashboard.credentials.save("hunter", "kept")
    secrets.failing = {"write"}
    caplog.set_level(logging.INFO)

    reply = save(dashboard, session, "hunter", MARK)
    page = row_reply(reply, "hunter")

    assert page.notices("hunter") == [("alert", STORE_FAILED)]
    assert page.state("hunter") == "Set, last updated 2026-11-17 09:30 UTC"
    assert_value_field_is_empty(page, "hunter")
    assert stored(secrets)["hunter/api_key"] == "kept"
    [record] = [r for r in caplog.records if r.getMessage() == "credential_store_failed"]
    assert (record.credential, record.operation, record.code) == (  # type: ignore[attr-defined]
        "hunter",
        "save",
        "keyring_failed",
    )
    assert all(MARK not in str(vars(r)) for r in caplog.records)
    assert MARK not in reply.text


def test_a_failed_remove_shows_the_alert_and_the_row_as_stored_now(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    dashboard.credentials.save("hunter", MARK)
    secrets.failing = {"delete"}

    page = row_reply(remove(dashboard, session, "hunter", confirm=True), "hunter")

    assert page.notices("hunter") == [("alert", STORE_FAILED)]
    assert page.state("hunter") == "Set, last updated 2026-11-17 09:30 UTC"
    assert stored(secrets)["hunter/api_key"] == MARK


def test_a_failing_read_shows_every_row_as_unknown(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    dashboard.credentials.save("hunter", MARK)
    secrets.failing = {"read"}

    _, page = credentials_page(dashboard, session[0])

    assert [page.state(key) for key in KEYS] == [UNREADABLE] * len(KEYS)
    assert [form_.attrs["hx-post"] for form_ in page.credential_forms] == [
        "/credentials/save"
    ] * len(KEYS)


def test_a_failed_save_with_a_failing_read_shows_unknown(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    secrets.failing = {"read", "write"}

    page = row_reply(save(dashboard, session, "hunter", MARK), "hunter")

    assert page.notices("hunter") == [("alert", STORE_FAILED)]
    assert page.state("hunter") == UNREADABLE


# Row: Malformed ---------------------------------------------------------------------------------

SAVE, REMOVE = "/credentials/save", "/credentials/remove"
HUNTER = {"credential": "hunter"}
# (case, path, the fields after csrf_token as a dict or raw urlencoded bytes, the logged reason)
MALFORMED: list[tuple[str, str, dict[str, str] | bytes, str]] = [
    ("save: unknown credential", SAVE, {"credential": "nope", "value": MARK}, WRONG_FIELDS),
    ("save: undeclared case", SAVE, {"credential": "Hunter", "value": MARK}, WRONG_FIELDS),
    ("save: no value", SAVE, HUNTER, WRONG_FIELDS),
    ("save: no credential", SAVE, {"value": MARK}, WRONG_FIELDS),
    ("save: extra field", SAVE, {**HUNTER, "value": MARK, "confirm": "yes"}, WRONG_FIELDS),
    ("save: repeated value", SAVE, b"credential=hunter&value=a&value=b", REPEATED_FIELD),
    (
        "save: repeated credential",
        SAVE,
        b"credential=hunter&credential=prospeo&value=a",
        REPEATED_FIELD,
    ),
    ("save: no fields", SAVE, {}, WRONG_FIELDS),
    ("remove: unknown credential", REMOVE, {"credential": "nope"}, WRONG_FIELDS),
    ("remove: confirm=no", REMOVE, {**HUNTER, "confirm": "no"}, WRONG_FIELDS),
    ("remove: confirm=YES", REMOVE, {**HUNTER, "confirm": "YES"}, WRONG_FIELDS),
    ("remove: blank confirm", REMOVE, {**HUNTER, "confirm": ""}, WRONG_FIELDS),
    (
        "remove: repeated confirm",
        REMOVE,
        b"credential=hunter&confirm=yes&confirm=yes",
        REPEATED_FIELD,
    ),
    ("remove: extra value", REMOVE, {**HUNTER, "confirm": "yes", "value": MARK}, WRONG_FIELDS),
    ("remove: no credential", REMOVE, {"confirm": "yes"}, WRONG_FIELDS),
]


@pytest.mark.parametrize(
    ("case", "path", "sent", "reason"), MALFORMED, ids=[case for case, *_ in MALFORMED]
)
def test_a_malformed_form_is_refused_with_400_and_changes_nothing(
    case: str,
    path: str,
    sent: dict[str, str] | bytes,
    reason: str,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dashboard.credentials.save("hunter", "kept")
    secrets.calls.clear()
    caplog.set_level(logging.INFO)
    token = session[1]
    body = fields(token, **sent) if isinstance(sent, dict) else form(csrf_token=token) + b"&" + sent

    reply = post(dashboard, session, path, body)

    assert reply.status == 400
    assert reply.body == MALFORMED_FORM.encode()
    assert reply.header(b"cache-control") == b"no-store"
    assert changes(secrets) == []
    assert stored(secrets)["hunter/api_key"] == "kept"
    [record] = [r for r in caplog.records if r.getMessage() == "request_refused"]
    logged = tuple(getattr(record, name) for name in ("check", "reason", "method", "path"))
    assert logged == ("form", reason, "POST", path)
    assert all(MARK not in str(vars(r)) for r in caplog.records)


# Row: Unchecked ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [SAVE, REMOVE])
@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_a_post_without_the_sessions_token_is_refused_with_403(
    path: str,
    token: str | None,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
) -> None:
    dashboard.credentials.save("hunter", "kept")
    secrets.calls.clear()
    values = {**HUNTER, "value": MARK} if path == SAVE else {**HUNTER, "confirm": "yes"}
    body = form(**values) if token is None else fields(token, **values)

    reply = post(dashboard, session, path, body, header_token=False)

    assert reply.status == 403
    assert reply.body == SECURITY_CHECK_FAILED.encode()
    assert changes(secrets) == []
    assert stored(secrets)["hunter/api_key"] == "kept"


@pytest.mark.parametrize("path", [SAVE, REMOVE])
def test_a_post_without_a_session_is_refused_with_401(
    path: str, dashboard: Dashboard, secrets: InMemorySecrets
) -> None:
    reply = dashboard.post(
        path,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token="x", credential="hunter", value=MARK, confirm="yes"),
    )

    assert reply.status == 401
    assert changes(secrets) == []
    assert stored(secrets) == {}


# The Settings link ------------------------------------------------------------------------------


def test_the_settings_page_links_to_the_credentials_page(units: UnitOfWorkFactory) -> None:
    dashboard = Dashboard(units=units)  # the Settings page reads the settings table

    reply = dashboard.get("/settings", cookie=dashboard.open_session())
    page = Page(reply.text)

    assert reply.status == 200

    [link] = page.find("a", href="/credentials")
    assert link.text == "Credentials page"
    assert "API keys and tokens are on the Credentials page." in page.texts("p")


# Wiring -----------------------------------------------------------------------------------------


def test_build_app_gives_the_entry_and_the_credentials_one_keyring_and_one_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[object] = []
    given: dict[str, tuple[object, object]] = {}
    real_entry, real_credentials = main.build_entry, main.Credentials

    def keyring_secrets(env: str) -> InMemorySecrets:
        built.append(InMemorySecrets(env))
        return built[-1]  # type: ignore[return-value]

    def system_clock() -> FrozenClock:
        built.append(FrozenClock(T))
        return built[-1]  # type: ignore[return-value]

    def build_entry(config: Any, **kwargs: Any) -> Any:
        given["entry"] = (kwargs["secrets"], kwargs["clock"])
        return real_entry(config, **kwargs)

    def credentials(secrets: Any, clock: Any) -> Any:
        given["credentials"] = (secrets, clock)
        return real_credentials(secrets, clock)

    monkeypatch.setattr(main, "KeyringSecrets", keyring_secrets)
    monkeypatch.setattr(main, "SystemClock", system_clock)
    monkeypatch.setattr(main, "build_entry", build_entry)
    monkeypatch.setattr(main, "Credentials", credentials)

    main.build_app(load_bootstrap(), units=FakeUnits())

    clock, secrets = built  # the clock first, then one keyring for the environment
    assert isinstance(secrets, InMemorySecrets) and secrets.service == "nextron-engine/test"
    assert given == {"entry": (secrets, clock), "credentials": (secrets, clock)}


def test_build_app_uses_injected_secrets_for_the_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    secrets = InMemorySecrets("test")
    monkeypatch.setattr(main, "KeyringSecrets", lambda env: pytest.fail("keyring was built"))

    app = main.build_app(load_bootstrap(), units=FakeUnits(), secrets=secrets, clock=FrozenClock(T))

    names = {getattr(route, "name", None) for route in app.routes}
    assert {"credentials_page", "credential_save", "credential_remove"} <= names


# Unsubscribe token keys (Story 3.8) ---------------------------------------------------------------

KEY_TEXT = "QZXUNSUBKEY" + "A" * 31 + "Q"  # 43 base64url characters: a 32-byte key
OTHER_KEY = "QZXOTHERKEY" + "B" * 31 + "Q"
THIRD_KEY = "QZXTHIRDKEY" + "C" * 31 + "Q"
UNSUB_HINT = (
    "A 32-byte key in base64url: 43 characters of A-Z, a-z, 0-9, - and _ (or 44 ending in =). "
    "Spaces at either end are dropped. The value is never shown."
)
NOT_A_KEY = (
    "Nothing was saved: enter a 32-byte key in base64url: 43 characters of A-Z, a-z, 0-9, - "
    "and _ (or 44 ending in =)."
)
IN_USE = "Key 1 still has tokens under 2 years old"


def unsub_post(
    dashboard: Dashboard, session: tuple[bytes, str], path: str, **values: str
) -> Reply:
    return post(dashboard, session, f"/credentials/unsub/{path}", fields(session[1], **values))


def unsub_row(reply: Reply, row_id: str) -> Page:
    """The answer to an unsubscribe-key POST: 200, no-store, and the row alone."""
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.text.startswith(f'<div class="credential unsub-key" id="{row_id}">')
    assert "<html" not in reply.text
    page = Page(reply.text)
    assert page.unsub_rows == [row_id]
    return page


def record_token(units: UnitOfWorkFactory, kid: int, issued_at: datetime, n: int = 0) -> None:
    """An ``unsub_tokens`` row issued at ``issued_at`` under ``kid``."""
    assert units.engine.url.database
    stamp = issued_at.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    run_sql(
        Path(units.engine.url.database),
        "INSERT INTO unsub_tokens (id, token_hash, om_id, kid, issued_at) VALUES "
        f"('01JD3QZ8X5V9K2M7N4P6R8T0{n:02d}', '{n:064x}', '01JD3QZ8X5V9K2M7N4P6R8T1{n:02d}', "
        f"{kid}, '{stamp}')",
    )


def test_the_section_lists_the_active_and_next_key_ids_with_each_key_not_set(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = credentials_page(dashboard, session[0])

    assert page.texts("h2", id="unsub-keys-heading") == ["Unsubscribe token keys"]
    assert page.texts("h3")[:2] == ["Key id 1", "Key id 2"]
    roles = page.texts("p", class_="unsub-kid-role")
    assert roles == [
        "active: new unsubscribe links use this key id; no tokens yet.",
        "next: enter its keys here and in the Worker before switching Unsubscribe token key id "
        "(Settings, Sending) to it; no tokens yet.",
    ]
    [escrow] = page.texts("p", class_="unsub-escrow")
    assert escrow.startswith("Escrow every k_tok: keep a copy off this PC")
    [worker] = page.texts("p", class_="unsub-worker")
    assert worker.startswith("Put every k_edge in the unsubscribe Worker too, as the secret "
                             "K_EDGE_<kid> (K_EDGE_1 for key id 1).")  # fmt: skip
    assert page.unsub_rows == [
        "unsub-key-1-k_tok",
        "unsub-key-1-k_edge",
        "unsub-key-2-k_tok",
        "unsub-key-2-k_edge",
    ]
    for row_id in page.unsub_rows:
        kid, key = row_id.removeprefix(UNSUB_ROW).split("-")
        assert page.state(row_id) == "Not set"
        [save_form] = page.row_forms(row_id)  # no Remove while not set
        assert {name: save_form.attrs.get(name) for name in ("method", "hx-post", "hx-target")} == {
            "method": "post",
            "hx-post": "/credentials/unsub/save",
            "hx-target": f"#{row_id}",
        }
        assert page.hidden(save_form) == {"csrf_token": session[1], "kid": kid, "key": key}
        [value_input] = page.find("input", row=row_id, name="value")
        assert value_input.attrs == {
            "type": "password",
            "id": f"value-{row_id}",
            "name": "value",
            "autocomplete": "new-password",
            "aria-describedby": f"hint-{row_id}",
        }
        assert page.texts("p", row=row_id, id=f"hint-{row_id}") == [UNSUB_HINT]


def test_saving_a_key_stores_its_canonical_text_and_answers_with_the_row(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    reply = unsub_post(dashboard, session, "save", kid="1", key="k_tok", value=f" {KEY_TEXT}= ")

    page = unsub_row(reply, "unsub-key-1-k_tok")
    assert page.state("unsub-key-1-k_tok") == "Set, last updated 2026-11-17 09:30 UTC"
    assert page.notices("unsub-key-1-k_tok") == [("status", "Saved.")]
    assert KEY_TEXT not in reply.text
    assert stored(secrets)["unsub/k_tok/1"] == KEY_TEXT
    assert stored(secrets)["meta/unsub/k_tok/1"] == "2026-11-17T09:30:00.000000Z"
    [remove_form] = page.row_forms("unsub-key-1-k_tok")[1:]
    assert remove_form.attrs["hx-post"] == "/credentials/unsub/remove"
    assert page.hidden(remove_form) == {"csrf_token": session[1], "kid": "1", "key": "k_tok"}


@pytest.mark.parametrize(
    "value",
    [
        "QZXSHORTKEY" + "A" * 30,  # 41 characters: 31 bytes
        "QZX+STD/KEY" + "A" * 31 + "Q",  # standard base64
        "QZXSPACE KEY" + "A" * 30 + "Q",  # a space inside
    ],
    ids=["31_bytes", "standard_base64", "space_inside"],
)
def test_a_bad_key_is_refused_with_200_and_nothing_is_stored(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets, value: str
) -> None:
    reply = unsub_post(dashboard, session, "save", kid="2", key="k_edge", value=value)

    page = unsub_row(reply, "unsub-key-2-k_edge")
    assert page.notices("unsub-key-2-k_edge") == [("alert", NOT_A_KEY)]
    assert page.state("unsub-key-2-k_edge") == "Not set"
    assert value not in reply.text
    assert [name for name in stored(secrets) if name.startswith("unsub/")] == []


def test_removing_a_key_in_use_asks_then_is_refused_and_deletes_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
    units: UnitOfWorkFactory,
) -> None:
    unsub_post(dashboard, session, "save", kid="1", key="k_tok", value=KEY_TEXT)
    unsub_post(dashboard, session, "save", kid="1", key="k_edge", value=OTHER_KEY)
    record_token(units, 1, T - timedelta(days=365))

    asked = unsub_row(
        unsub_post(dashboard, session, "remove", kid="1", key="k_tok"), "unsub-key-1-k_tok"
    )
    assert asked.notices("unsub-key-1-k_tok") == [
        ("alert", "Remove the Token encryption key (k_tok) of key id 1 from Windows Credential "
                  "Manager?"),
    ]  # fmt: skip
    [confirm_form] = asked.row_forms("unsub-key-1-k_tok")
    assert asked.hidden(confirm_form) == {
        "csrf_token": session[1],
        "kid": "1",
        "key": "k_tok",
        "confirm": "yes",
    }
    refused = unsub_post(dashboard, session, "remove", kid="1", key="k_tok", confirm="yes")

    page = unsub_row(refused, "unsub-key-1-k_tok")
    assert page.notices("unsub-key-1-k_tok") == [("alert", f"Nothing was removed: {IN_USE}.")]
    assert stored(secrets)["unsub/k_tok/1"] == KEY_TEXT
    replaced = unsub_post(dashboard, session, "save", kid="1", key="k_edge", value=THIRD_KEY)
    assert unsub_row(replaced, "unsub-key-1-k_edge").notices("unsub-key-1-k_edge") == [
        ("alert", f"Nothing was saved: {IN_USE}.")
    ]
    assert stored(secrets)["unsub/k_edge/1"] == OTHER_KEY
    _, listing = credentials_page(dashboard, session[0])
    assert listing.texts("p", class_="unsub-kid-role")[0] == (
        "active: new unsubscribe links use this key id; newest token 2025-11-17 09:30 UTC."
    )


def test_removing_a_retired_key_deletes_it_and_its_time(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    secrets: InMemorySecrets,
    units: UnitOfWorkFactory,
) -> None:
    unsub_post(dashboard, session, "save", kid="3", key="k_tok", value=KEY_TEXT)
    record_token(units, 3, T - timedelta(days=732))

    reply = unsub_post(dashboard, session, "remove", kid="3", key="k_tok", confirm="yes")

    page = unsub_row(reply, "unsub-key-3-k_tok")
    assert page.notices("unsub-key-3-k_tok") == [("status", "Removed.")]
    assert page.state("unsub-key-3-k_tok") == "Not set"
    assert "unsub/k_tok/3" not in stored(secrets)
    assert "meta/unsub/k_tok/3" not in stored(secrets)
    _, listing = credentials_page(dashboard, session[0])
    assert listing.texts("h3")[:4] == ["Key id 1", "Key id 2", "Key id 3", "Google Desktop client"]
    assert listing.texts("p", class_="unsub-kid-role")[2] == (
        "retired: every link is 2 years old or more; newest token 2024-11-15 09:30 UTC."
    )


def test_a_store_failure_answers_with_the_alert_and_the_row_as_stored_now(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    secrets.failing.add("write")

    reply = unsub_post(dashboard, session, "save", kid="1", key="k_tok", value=KEY_TEXT)

    page = unsub_row(reply, "unsub-key-1-k_tok")
    assert page.notices("unsub-key-1-k_tok") == [("alert", STORE_FAILED)]
    assert page.state("unsub-key-1-k_tok") == "Not set"


def test_saving_the_other_keys_value_is_refused_with_200(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    unsub_post(dashboard, session, "save", kid="1", key="k_tok", value=KEY_TEXT)

    reply = unsub_post(dashboard, session, "save", kid="1", key="k_edge", value=KEY_TEXT + "=")

    page = unsub_row(reply, "unsub-key-1-k_edge")
    assert page.notices("unsub-key-1-k_edge") == [
        ("alert", "Nothing was saved: k_tok and k_edge of a key id must differ.")
    ]
    assert "unsub/k_edge/1" not in stored(secrets)


def test_a_failed_unsubscribe_key_remove_shows_the_alert_and_the_row_as_stored_now(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    unsub_post(dashboard, session, "save", kid="1", key="k_tok", value=KEY_TEXT)
    secrets.failing = {"delete"}

    reply = unsub_post(dashboard, session, "remove", kid="1", key="k_tok", confirm="yes")

    page = unsub_row(reply, "unsub-key-1-k_tok")
    assert page.notices("unsub-key-1-k_tok") == [("alert", STORE_FAILED)]
    assert page.state("unsub-key-1-k_tok") == "Set, last updated 2026-11-17 09:30 UTC"
    assert stored(secrets)["unsub/k_tok/1"] == KEY_TEXT


def test_a_failing_read_shows_every_unsubscribe_row_as_unknown(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    unsub_post(dashboard, session, "save", kid="1", key="k_tok", value=KEY_TEXT)
    secrets.failing = {"read"}

    _, page = credentials_page(dashboard, session[0])

    assert [page.state(row_id) for row_id in page.unsub_rows] == [UNREADABLE] * 4
    assert [len(page.row_forms(row_id)) for row_id in page.unsub_rows] == [1] * 4  # Save only


def test_the_page_reads_only_the_unsubscribe_keys_inside_the_read_transaction(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    reads: list[tuple[str, bool]] = []
    real_read = secrets.read

    def recording_read(name: str) -> str | None:
        reads.append((name, in_transaction()))
        return real_read(name)

    monkeypatch.setattr(secrets, "read", recording_read)

    credentials_page(dashboard, session[0])

    keys = ("unsub/k_tok/", "unsub/k_edge/", "meta/unsub/k_")  # the unsubscribe keys' entries
    inside = [name for name, held in reads if held]
    assert inside and all(name.startswith(keys) for name in inside)
    assert [name for name, held in reads if not held and name.startswith(keys)] == []
    assert reads[-len(inside):] == [(name, True) for name in inside]  # the reads come last
    assert any(name.startswith("google/") for name, held in reads if not held)


def test_a_stored_value_that_is_no_key_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets
) -> None:
    secrets.store[(SERVICE, "unsub/k_edge/1")] = "QZXGARBLED"

    _, page = credentials_page(dashboard, session[0])

    assert page.state("unsub-key-1-k_edge") == (
        "Set, but not a 32-byte base64url key: enter it again"
    )
    assert "QZXGARBLED" not in page.texts("div", class_="credential unsub-key")[1]


@pytest.mark.parametrize(
    "values",
    [
        {"kid": "1", "key": "k_tok"},  # no value
        {"kid": "1", "key": "k_tok", "value": KEY_TEXT, "credential": "hunter"},
        {"kid": "0", "key": "k_tok", "value": KEY_TEXT},
        {"kid": "256", "key": "k_tok", "value": KEY_TEXT},
        {"kid": "01", "key": "k_tok", "value": KEY_TEXT},
        {"kid": "1a", "key": "k_tok", "value": KEY_TEXT},
        {"kid": "1000", "key": "k_tok", "value": KEY_TEXT},
        {"kid": "1", "key": "k_other", "value": KEY_TEXT},
        {"kid": "1", "key": "bearer", "value": KEY_TEXT},
    ],
    ids=range(9),
)
def test_a_malformed_save_is_refused_with_400(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets,
    values: dict[str, str],
) -> None:  # fmt: skip
    reply = unsub_post(dashboard, session, "save", **values)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert changes(secrets) == []


@pytest.mark.parametrize(
    "body",
    [
        "kid=1&key=k_tok&confirm=no",
        "kid=1&key=k_tok&kid=2",
        "kid=1&key=k_tok&key=k_edge",
        "kid=1",
        "kid=1&key=k_tok&value=x",
        "kid=-1&key=k_tok",
    ],
)
def test_a_malformed_remove_is_refused_with_400(
    dashboard: Dashboard, session: tuple[bytes, str], secrets: InMemorySecrets, body: str
) -> None:
    raw = f"csrf_token={session[1]}&{body}".encode()

    reply = post(dashboard, session, "/credentials/unsub/remove", raw)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert changes(secrets) == []


def test_both_posts_run_the_service_inside_a_read_only_unit_of_work(
    dashboard: Dashboard, session: tuple[bytes, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    for name in ("save_unsub_key", "remove_unsub_key"):
        real = getattr(dashboard.credentials, name)

        def spying(*args: Any, real: Any = real) -> Any:
            seen.append(current().read_only)
            return real(*args)

        monkeypatch.setattr(dashboard.credentials, name, spying)

    unsub_post(dashboard, session, "save", kid="4", key="k_tok", value=KEY_TEXT)
    unsub_post(dashboard, session, "remove", kid="4", key="k_tok", confirm="yes")

    assert seen == [True, True]


def test_the_unsubscribe_key_posts_declare_no_personal_data(dashboard: Dashboard) -> None:
    declared = {
        getattr(route, "name", None): getattr(route.endpoint, PERSONAL_DATA_ATTRIBUTE, None)
        for route in dashboard.app.routes
        if isinstance(route, APIRoute)
    }

    assert (declared["unsub_key_save"], declared["unsub_key_remove"]) == (False, False)
