"""The Settings page (AD-12, UX-DR23) against the story's Page rows: ``GET /settings`` and
``POST /settings`` through ``Dashboard(units=units)`` on a real database at head, with the HTML
parsed. Most rows swap a probe schema in for ``settings_schema.SCHEMA``; the acceptance rows use
the real one."""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import time
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from fastapi import Request

from nextron import main
from nextron.app.queries import setup as setup_queries
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import settings_schema
from nextron.domain.settings_schema import (
    REQUIRED,
    TIMEZONE_REASON,
    Key,
    Section,
    Setting,
    SettingType,
    Slot,
    maximum,
    minimum,
)
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import (
    BAD_BODY,
    MALFORMED_FORM,
    NOT_URLENCODED,
    REPEATED_FIELD,
    WRONG_FIELDS,
    MalformedForm,
    form_fields,
)
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits

PROBE = MappingProxyType(
    {
        "probe_cap": Setting(
            key="probe_cap",
            type=SettingType.INTEGER,
            section=Section.SENDING,
            label="Daily cap",
            unit="emails a day",
            default=30,
            rules=(minimum(1, "1"),),
        ),
        "probe_people": Setting(
            key="probe_people",
            type=SettingType.LIST,
            section=Section.FOOTER_AND_BOOKING,
            label="People",
            unit="one address per line",
            default=("a@example.test", "b@example.test"),
        ),
        "probe_zone": Setting(
            key="probe_zone",
            type=SettingType.TIMEZONE,
            section=Section.SENDING,
            label="Zone",
            unit="IANA timezone name",
            default=REQUIRED,
        ),
        "probe_flag": Setting(
            key="probe_flag",
            type=SettingType.BOOLEAN,
            section=Section.FOOTER_AND_BOOKING,
            label="Flag",
            unit="true or false",
            default=False,
        ),
        "probe_hours": Setting(
            key="probe_hours",
            type=SettingType.AVAILABILITY,
            section=Section.SENDING,
            label="Hours",
            unit="one slot per line, such as Mon 09:00-17:00",
            default=REQUIRED,
            timezone_key="probe_zone",
        ),
    }
)
VALID = {
    "probe_cap": "50",
    "probe_people": "x@example.test\r\n\r\ny@example.test",
    "probe_zone": "Europe/London",
    "probe_flag": "true",
    "probe_hours": "tue 09:00-12:00\r\nmon 09:00-17:00",
}
HOSTILE = '"><script>alert(1)</script>'
JSON = b"application/json"


# Parsing the page ------------------------------------------------------------------------------


@dataclass
class Shown:
    """One field as rendered: its label, control and hint, and its error if any."""

    key: str
    label: str = ""
    label_for: str | None = None
    tag: str = ""
    attrs: dict[str, str | None] = field(default_factory=dict)
    content: str = ""  # a textarea's text
    options: list[tuple[str | None, bool, str]] = field(default_factory=list)
    hint: str = ""
    hint_id: str | None = None
    error: str | None = None
    error_id: str | None = None

    @property
    def value(self) -> str | None:
        if self.tag == "textarea":
            return self.content.removeprefix("\n")  # a browser drops one leading newline
        if self.tag == "select":
            chosen = [value for value, selected, _ in self.options if selected]
            return chosen[0] if chosen else None
        return self.attrs.get("value")


class SettingsHtml(HTMLParser):
    """The forms, fieldsets (legend and fields), hidden inputs and notices of a page."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, str | None]] = []
        self.hidden: list[dict[str, str | None]] = []
        self.sections: list[tuple[str, list[Shown]]] = []
        self.notices: list[tuple[str | None, str]] = []
        self.tags: list[str] = []
        self._into: tuple[str, str] | None = None  # (what, end tag) while capturing text
        self._text: list[str] = []
        self._field: Shown | None = None
        self._role: str | None = None
        self.feed(html)
        self.close()

    def _capture(self, what: str, tag: str) -> None:
        self._into, self._text = (what, tag), []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self.tags.append(tag)
        field_ = self._field
        if tag == "form":
            self.forms.append(values)
        elif tag == "fieldset":
            self.sections.append(("", []))
        elif tag == "legend":
            self._capture("legend", tag)
        elif tag == "div" and values.get("class") == "field":
            self._field = Shown(key="")
            self.sections[-1][1].append(self._field)
        elif tag == "label" and field_ is not None:
            field_.label_for = values.get("for")
            self._capture("label", tag)
        elif tag == "input" and values.get("type") == "hidden":
            self.hidden.append(values)
        elif tag in ("input", "textarea", "select") and field_ is not None:
            field_.tag, field_.attrs, field_.key = tag, values, values.get("name") or ""
            if tag == "textarea":
                self._capture("textarea", tag)
        elif tag == "option" and field_ is not None:
            field_.options.append((values.get("value"), "selected" in values, ""))
            self._capture("option", tag)
        elif tag == "p" and values.get("class") == "hint" and field_ is not None:
            field_.hint_id = values.get("id")
            self._capture("hint", tag)
        elif tag == "p" and values.get("class") == "field-error" and field_ is not None:
            field_.error_id = values.get("id")
            self._capture("error", tag)
        elif tag == "p" and values.get("role") and values.get("id") != "request-error":
            # a form's notice; the layout's empty error notice (#request-error) is not one
            self._role = values.get("role")
            self._capture("notice", tag)

    def handle_data(self, data: str) -> None:
        if self._into is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._into is None:
            self._field = None
        if self._into is None or tag != self._into[1]:
            return
        what, text = self._into[0], "".join(self._text)
        self._into = None
        field_ = self._field
        if what == "legend":
            self.sections[-1] = (text, self.sections[-1][1])
        elif what == "notice":
            self.notices.append((self._role, " ".join(text.split())))
        elif field_ is not None:
            if what == "label":
                field_.label = text
            elif what == "textarea":
                field_.content = text
            elif what == "option":
                value, selected, _ = field_.options[-1]
                field_.options[-1] = (value, selected, text)
            elif what == "hint":
                field_.hint = text
            elif what == "error":
                field_.error = text

    @property
    def fields(self) -> dict[str, Shown]:
        return {shown.key: shown for _, shown_fields in self.sections for shown in shown_fields}

    @property
    def legends(self) -> list[str]:
        return [legend for legend, _ in self.sections]


# Fixtures and helpers ---------------------------------------------------------------------------


@pytest.fixture
def probe_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_schema.check_schema(PROBE, keys=None)
    monkeypatch.setattr(settings_schema, "SCHEMA", PROBE)


@pytest.fixture
def dashboard(units: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=units)


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    """A session's cookie and its CSRF token."""
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def save(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    body: bytes,
    *,
    content_type: bytes = FORM,
    header_token: bool = True,
) -> Reply:
    """POST ``body`` to ``/settings`` as HTMX does: the token in the header and the field."""
    cookie, token = session
    return dashboard.post(
        "/settings",
        cookie=cookie,
        token=token if header_token else None,
        headers=[(b"content-type", content_type)],
        body=body,
    )


def fields_body(token: str, **fields: str) -> bytes:
    return form(csrf_token=token, **fields)


def real_body(token: str, **fields: str) -> bytes:
    """A save of the real schema: every declared field blank unless given."""
    texts = {str(found.key): "" for found in settings_schema.declared()}
    return fields_body(token, **{**texts, **fields})


def stored(units: UnitOfWorkFactory) -> dict[str, str]:
    with units.open(read_only=True):
        return setup_queries.stored_texts()


def get_value(units: UnitOfWorkFactory, key: str) -> Any:
    with units.open(read_only=True):
        return setup_queries.get(key)


def seed(units: UnitOfWorkFactory, dashboard: Dashboard, texts: dict[str, str]) -> None:
    with units.open():
        dashboard.setup.save(texts)


def settings_page(dashboard: Dashboard, cookie: bytes) -> tuple[Reply, SettingsHtml]:
    reply = dashboard.get("/settings", cookie=cookie)
    assert reply.status == 200, reply
    return reply, SettingsHtml(reply.text)


# Row: Page GET ----------------------------------------------------------------------------------


@pytest.mark.usefixtures("probe_schema")
def test_the_form_has_one_fieldset_per_section_in_section_order(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])

    assert page.legends == ["Footer and booking", "Sending"]
    assert [[shown.key for shown in fields] for _, fields in page.sections] == [
        ["probe_people", "probe_flag"],
        ["probe_cap", "probe_zone", "probe_hours"],
    ]


@pytest.mark.usefixtures("probe_schema")
def test_each_field_has_its_label_control_and_hint(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])
    fields = page.fields

    assert {key: shown.label for key, shown in fields.items()} == {
        "probe_people": "People",
        "probe_flag": "Flag",
        "probe_cap": "Daily cap",
        "probe_zone": "Zone",
        "probe_hours": "Hours",
    }
    assert {key: shown.tag for key, shown in fields.items()} == {
        "probe_people": "textarea",
        "probe_flag": "select",
        "probe_cap": "input",
        "probe_zone": "input",
        "probe_hours": "textarea",
    }
    assert fields["probe_cap"].attrs["type"] == "text"
    assert [value for value, _, _ in fields["probe_flag"].options] == ["", "true", "false"]
    assert {key: shown.hint for key, shown in fields.items()} == {
        "probe_people": (
            "one address per line; default: a@example.test, b@example.test; "
            "leave blank to use it"
        ),
        "probe_flag": "true or false; default: false; leave blank to use it",
        "probe_cap": "emails a day; default: 30; leave blank to use it",
        "probe_zone": "IANA timezone name; required",
        "probe_hours": "one slot per line, such as Mon 09:00-17:00; required",
    }
    for key, shown in fields.items():
        assert shown.attrs["id"] == shown.label_for == f"setting-{key}"
        assert shown.attrs["aria-describedby"] == shown.hint_id == f"hint-{key}"
        assert "aria-invalid" not in shown.attrs and shown.error is None


@pytest.mark.usefixtures("probe_schema")
def test_each_field_shows_the_stored_text_else_the_default_text_else_blank(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    seed(units, dashboard, {"probe_cap": "45", "probe_hours": "Mon 09:00-17:00"})

    _, page = settings_page(dashboard, session[0])

    assert {key: shown.value for key, shown in page.fields.items()} == {
        "probe_people": "a@example.test\nb@example.test",  # the default
        "probe_flag": "false",  # the default
        "probe_cap": "45",  # stored
        "probe_zone": "",  # required and not set
        "probe_hours": "Mon 09:00-17:00",  # stored
    }


@pytest.mark.usefixtures("probe_schema")
def test_the_form_posts_through_htmx_with_the_sessions_csrf_token(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply, page = settings_page(dashboard, session[0])

    [attrs] = page.forms
    assert attrs == {
        "class": "settings",
        "method": "post",
        "hx-post": "/settings",
        "hx-target": "this",
        "hx-swap": "outerHTML",
    }
    assert page.hidden == [{"type": "hidden", "name": "csrf_token", "value": session[1]}]
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.header(b"content-type") == b"text/html; charset=utf-8"
    assert page.notices == []


def test_the_page_extends_the_layout_and_the_nav_shows_settings(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply, page = settings_page(dashboard, session[0])

    assert "<nav" in reply.text and 'href="/settings">Settings</a>' in reply.text
    assert 'href="/health/panel">Health</a>' in reply.text
    assert page.tags.count("form") == 1 and "h1" in page.tags
    assert "<title>Nextron: Settings</title>" in reply.text


def test_the_page_needs_a_session(dashboard: Dashboard) -> None:
    reply = dashboard.get("/settings")

    assert reply.status == 401
    assert b"<form" not in reply.body


@pytest.mark.usefixtures("probe_schema")
def test_showing_the_defaults_stores_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    settings_page(dashboard, session[0])

    assert stored(units) == {}


@pytest.mark.usefixtures("probe_schema")
def test_a_stored_text_that_no_longer_passes_is_flagged_on_the_page(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed(units, dashboard, {"probe_cap": "45", "probe_zone": "Asia/Tokyo"})
    stricter = Setting(**{**_fields(PROBE["probe_cap"]), "rules": (maximum(40, "40"),)})
    tightened = MappingProxyType({**PROBE, "probe_cap": stricter})
    settings_schema.check_schema(tightened, keys=None)
    monkeypatch.setattr(settings_schema, "SCHEMA", tightened)

    _, page = settings_page(dashboard, session[0])
    fields = page.fields

    assert fields["probe_cap"].value == "45"
    assert fields["probe_cap"].error == "must be at most 40"
    assert fields["probe_cap"].attrs["aria-invalid"] == "true"
    assert fields["probe_cap"].attrs["aria-describedby"] == "hint-probe_cap error-probe_cap"
    assert [key for key, shown in fields.items() if shown.error] == ["probe_cap"]
    assert page.notices == []  # nothing was refused: no "nothing was saved" alert


@pytest.mark.usefixtures("probe_schema")
def test_saving_the_page_unchanged_stores_nothing_for_defaulted_settings(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    _, page = settings_page(dashboard, session[0])
    shown = {key: field_.value or "" for key, field_ in page.fields.items()}

    reply = save(dashboard, session, fields_body(session[1], **shown))

    assert SettingsHtml(reply.text).notices == [("status", "Settings saved.")]
    assert stored(units) == {}  # the defaults stay defaults, the required ones unset


BLANK_AND_BOOLEANS = MappingProxyType(
    {
        "probe_extra": Setting(
            key="probe_extra",
            type=SettingType.LIST,
            section=Section.ENGINE,
            label="Extra",
            unit="one per line",
            default=(),
        ),
        "probe_flag": Setting(
            key="probe_flag",
            type=SettingType.BOOLEAN,
            section=Section.ENGINE,
            label="Flag",
            unit="true or false",
            default=True,
        ),
        "probe_must": Setting(
            key="probe_must",
            type=SettingType.BOOLEAN,
            section=Section.ENGINE,
            label="Must",
            unit="true or false",
            default=REQUIRED,
        ),
    }
)


def test_a_blank_default_shows_as_none_and_booleans_offer_a_blank_choice(
    dashboard: Dashboard, session: tuple[bytes, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_schema.check_schema(BLANK_AND_BOOLEANS, keys=None)
    monkeypatch.setattr(settings_schema, "SCHEMA", BLANK_AND_BOOLEANS)

    _, page = settings_page(dashboard, session[0])
    fields = page.fields

    assert fields["probe_extra"].hint == "one per line; default: none; leave blank to use it"
    assert fields["probe_extra"].value == ""
    assert [text for _, _, text in fields["probe_flag"].options] == [
        "use the default",
        "true",
        "false",
    ]
    assert [text for _, _, text in fields["probe_must"].options] == ["not set", "true", "false"]
    assert (fields["probe_flag"].value, fields["probe_must"].value) == ("true", "")


def _fields(found: Setting) -> dict[str, Any]:
    return {name: getattr(found, name) for name in Setting.__dataclass_fields__}


# Acceptance: the real schema ---------------------------------------------------------------------


def test_the_sender_section_shows_the_sender_timezone_as_required(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])

    assert page.legends == [
        "Footer and booking",
        "Sender",
        "Alerts and recipients",
        "Sending",
        "Budget and limits",
        "Engine",
    ]
    shown = page.fields["sender_timezone"]
    assert (shown.key, shown.label, shown.value) == ("sender_timezone", "Sender's timezone", "")
    assert shown.hint == "IANA timezone name, such as America/Los_Angeles; required"


def test_the_engine_section_shows_the_empty_sink_allowlist(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])

    assert list(page.fields) == [
        "sender_signature",
        "legal_name",
        "postal_address",
        "optout_line",
        "privacy_notice_url",
        "us_solicitation_label",
        "sender_name",
        "sender_timezone",
        "alert_chat_id",
        "unsub_active_kid",
        "total_daily_ceiling",
        "unsub_worker_host",
        "dnsbl_zones",
        "quota_hunter_credits",
        "quota_prospeo_credits",
        "quota_places_enterprise",
        "quota_places_pro",
        "quota_job_aggregator",
        "ai_budget_monthly",
        "ai_budget_alert_pct",
        "sink_allowlist",
        "bulk_threads",
        "backup_time",
        "backup_rotation_days",
        "backup_offpc_dir",
        "crawl_max_pages",
        "ats_hosts",
        "lead_score_fit_weight",
        "lead_score_signal_weight",
    ]
    shown = page.fields["sink_allowlist"]
    assert (shown.tag, shown.label, shown.value) == ("textarea", "Dev and test allowlist", "")
    assert shown.hint == (
        "One email address or numeric Telegram chat id per line. Outside prod, email and alerts "
        "reach only these; everything else is written to the sink folder. Only these addresses "
        "can be registered as Sending Inboxes there.; default: none; leave blank to use it"
    )


FOOTER_KEYS = [
    "sender_signature",
    "legal_name",
    "postal_address",
    "optout_line",
    "privacy_notice_url",
    "us_solicitation_label",
]


def test_the_footer_section_shows_the_footer_settings_in_footer_order_each_editable(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])

    legend, shown_fields = page.sections[0]
    assert legend == "Footer and booking"
    assert [shown.key for shown in shown_fields] == FOOTER_KEYS
    for shown in shown_fields:
        assert shown.tag == "input" and shown.attrs.get("name") == shown.key
        assert "disabled" not in shown.attrs and "readonly" not in shown.attrs
    assert [shown.label for shown in shown_fields] == [
        "Sender signature",
        "Legal name",
        "Postal address",
        "Opt-out line",
        "Privacy notice URL",
        "US solicitation label",
    ]
    assert page.fields["legal_name"].hint.endswith("No email is sent while it is blank.; required")
    assert page.fields["privacy_notice_url"].hint.endswith(
        "No UK email is sent while it is blank.; required"
    )
    assert page.fields["optout_line"].value == (
        "Not relevant? Reply 'no' and I won't contact you again."
    )


def test_the_footer_saves_through_the_page_and_a_set_value_cant_be_cleared(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    token = session[1]
    footer = {
        "sender_signature": "Furqaan, Nextron AI",
        "legal_name": "Nextron AI Ltd",
        "postal_address": "1 Example Street, London EC1A 1AA",
        "privacy_notice_url": "https://nextronai.com/privacy",
    }

    saved = save(dashboard, session, real_body(token, **footer))

    assert saved.status == 200
    assert {key: get_value(units, key) for key in footer} == footer
    refused = save(dashboard, session, real_body(token, **{**footer, "legal_name": ""}))
    shown = SettingsHtml(refused.text).fields["legal_name"]
    assert shown.attrs["aria-invalid"] == "true" and shown.error is not None
    assert get_value(units, Key.LEGAL_NAME) == "Nextron AI Ltd"


def test_the_alerts_section_shows_the_required_telegram_chat_id(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])

    shown = page.fields["alert_chat_id"]
    assert (shown.tag, shown.label, shown.value) == ("input", "Telegram alert chat id", "")
    assert shown.hint == (
        "The numeric id of the Telegram chat that receives ops alerts, such as -1001234567890; "
        "user names (@name) aren't accepted.; required"
    )


def test_the_telegram_chat_id_saves_a_numeric_id_and_refuses_a_user_name(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    token = session[1]

    saved = save(dashboard, session, real_body(token, alert_chat_id=" -1001234567890 "))

    assert saved.status == 200
    assert get_value(units, Key.ALERT_CHAT_ID) == "-1001234567890"
    for refused_text in ("@ops_channel", "12 34", "chat"):
        refused = save(dashboard, session, real_body(token, alert_chat_id=refused_text))
        field = SettingsHtml(refused.text).fields["alert_chat_id"]
        assert field.attrs["aria-invalid"] == "true" and field.error is not None
        assert "numeric Telegram chat id" in field.error
        assert get_value(units, Key.ALERT_CHAT_ID) == "-1001234567890"


def test_the_sink_allowlist_saves_addresses_and_chat_ids_and_refuses_anything_else(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    token = session[1]

    saved = save(dashboard, session, real_body(token, sink_allowlist="a@b.co\r\n123"))

    assert SettingsHtml(saved.text).notices == [("status", "Settings saved.")]
    assert stored(units) == {"sink_allowlist": "a@b.co\n123"}
    assert get_value(units, Key.SINK_ALLOWLIST) == ("a@b.co", "123")
    for refused_text in ("@chan", "a@b", "12a"):
        refused = save(dashboard, session, real_body(token, sink_allowlist=refused_text))
        field = SettingsHtml(refused.text).fields["sink_allowlist"]
        assert field.error == "each line must be an email address or a numeric Telegram chat id"
        assert field.value == refused_text
        assert stored(units) == {"sink_allowlist": "a@b.co\n123"}


def test_saving_mars_base_stores_nothing_and_america_los_angeles_is_saved(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    token = session[1]

    refused = save(dashboard, session, real_body(token, sender_timezone="Mars/Base"))
    refused_page = SettingsHtml(refused.text)

    assert refused.status == 200
    assert refused_page.fields["sender_timezone"].error == TIMEZONE_REASON
    assert stored(units) == {}

    saved = save(dashboard, session, real_body(token, sender_timezone="America/Los_Angeles"))
    saved_page = SettingsHtml(saved.text)

    assert saved.status == 200
    assert saved_page.notices == [("status", "Settings saved.")]
    assert saved_page.fields["sender_timezone"].value == "America/Los_Angeles"
    assert get_value(units, Key.SENDER_TIMEZONE) == "America/Los_Angeles"


def test_the_engine_section_shows_bulk_threads_with_its_default(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    _, page = settings_page(dashboard, session[0])

    shown = page.fields["bulk_threads"]
    assert (shown.label, shown.value) == ("Bulk worker threads", "1")
    assert shown.hint == (
        "1 or 2. Threads for crawls, enrichment and other bulk work; a change takes effect when "
        "the engine restarts.; default: 1; leave blank to use it"
    )


def test_bulk_threads_saves_2_and_refuses_0_and_3(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    token = session[1]

    saved = save(dashboard, session, real_body(token, bulk_threads="2"))

    assert SettingsHtml(saved.text).notices == [("status", "Settings saved.")]
    assert stored(units) == {"bulk_threads": "2"}
    assert get_value(units, Key.BULK_THREADS) == 2
    for refused_text, reason in (("0", "must be at least 1"), ("3", "must be at most 2")):
        refused = save(dashboard, session, real_body(token, bulk_threads=refused_text))
        field = SettingsHtml(refused.text).fields["bulk_threads"]
        assert (field.error, field.value) == (reason, refused_text)
        assert stored(units) == {"bulk_threads": "2"}


def test_the_sending_section_shows_the_unsubscribe_token_key_id_and_saves_it(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    _, page = settings_page(dashboard, session[0])

    shown = page.fields["unsub_active_kid"]
    assert (shown.label, shown.value) == ("Unsubscribe token key id", "1")
    assert shown.hint.endswith("already sent keep working.; default: 1; leave blank to use it")

    saved = save(dashboard, session, real_body(session[1], unsub_active_kid="2"))

    assert SettingsHtml(saved.text).notices == [("status", "Settings saved.")]
    assert get_value(units, Key.UNSUB_ACTIVE_KID) == 2
    for refused_text, reason in (("0", "must be at least 1"), ("256", "must be at most 255")):
        refused = save(dashboard, session, real_body(session[1], unsub_active_kid=refused_text))
        field = SettingsHtml(refused.text).fields["unsub_active_kid"]
        assert (field.error, field.value) == (reason, refused_text)
        assert stored(units) == {"unsub_active_kid": "2"}


def test_the_budget_section_shows_each_limit_with_its_default(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply, page = settings_page(dashboard, session[0])

    shown = {
        key: (page.fields[key].label, page.fields[key].value)
        for key in (
            "quota_hunter_credits",
            "quota_prospeo_credits",
            "quota_places_enterprise",
            "quota_places_pro",
            "quota_job_aggregator",
            "ai_budget_monthly",
            "ai_budget_alert_pct",
        )
    }
    assert shown == {
        "quota_hunter_credits": ("Hunter credits a month", "50"),
        "quota_prospeo_credits": ("Prospeo credits a month", "100"),
        "quota_places_enterprise": ("Places Enterprise requests a month", "1000"),
        "quota_places_pro": ("Places Pro requests a month", "0"),
        "quota_job_aggregator": ("Job-aggregator requests a month", "0"),
        "ai_budget_monthly": ("AI budget a month", "20.00"),
        "ai_budget_alert_pct": ("AI spend alert at (%)", "80"),
    }
    for key in shown:
        assert "UTC calendar month" in page.fields[key].hint
    assert "Google's free monthly allowance" in page.fields["quota_places_pro"].hint
    assert 'href="/quotas"' in reply.text and "Quotas and cost page" in reply.text


def test_a_places_quota_above_googles_free_allowance_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    token = session[1]

    for key, refused_text, reason in (
        ("quota_places_enterprise", "1001", "must be at most 1,000"),
        ("quota_places_pro", "5001", "must be at most 5,000"),
        ("ai_budget_monthly", "100.01", "must be at most $100"),
        ("ai_budget_alert_pct", "0", "must be at least 1"),
    ):
        refused = save(dashboard, session, real_body(token, **{key: refused_text}))
        field = SettingsHtml(refused.text).fields[key]
        assert (field.error, field.value) == (reason, refused_text)
        assert stored(units) == {}

    saved = save(
        dashboard,
        session,
        real_body(token, quota_places_pro="5000", ai_budget_monthly="35.5"),
    )

    assert SettingsHtml(saved.text).notices == [("status", "Settings saved.")]
    assert stored(units) == {"quota_places_pro": "5000", "ai_budget_monthly": "35.50"}
    assert get_value(units, Key.AI_BUDGET_MONTHLY) == 35_500_000


# Row: Page POST ok ------------------------------------------------------------------------------


@pytest.mark.usefixtures("probe_schema")
def test_a_valid_save_stores_everything_and_shows_the_canonical_values(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    reply = save(dashboard, session, fields_body(session[1], **VALID))
    page = SettingsHtml(reply.text)

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    assert page.notices == [("status", "Settings saved.")]
    assert {key: shown.value for key, shown in page.fields.items()} == {
        "probe_people": "x@example.test\ny@example.test",
        "probe_flag": "true",
        "probe_cap": "50",
        "probe_zone": "Europe/London",
        "probe_hours": "Mon 09:00-17:00\nTue 09:00-12:00",
    }
    assert stored(units) == {
        "probe_cap": "50",
        "probe_flag": "true",
        "probe_hours": "Mon 09:00-17:00\nTue 09:00-12:00",
        "probe_people": "x@example.test\ny@example.test",
        "probe_zone": "Europe/London",
    }
    assert get_value(units, "probe_hours") == (
        Slot(0, time(9), time(17)),
        Slot(1, time(9), time(12)),
    )


@pytest.mark.usefixtures("probe_schema")
def test_the_answer_is_the_form_alone_for_htmx_to_swap_in(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = save(dashboard, session, fields_body(session[1], **VALID))

    assert reply.text.lstrip().startswith("<form ")
    assert reply.text.rstrip().endswith("</form>")
    assert "<html" not in reply.text and "<nav" not in reply.text
    [attrs] = SettingsHtml(reply.text).forms
    assert (attrs["hx-target"], attrs["hx-swap"]) == ("this", "outerHTML")
    assert SettingsHtml(reply.text).hidden[0]["value"] == session[1]


@pytest.mark.usefixtures("probe_schema")
def test_blank_fields_reset_defaults_and_leave_a_required_setting_unset(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    seed(units, dashboard, {"probe_cap": "45", "probe_flag": "true"})
    blank = {key: "" for key in PROBE}

    reply = save(dashboard, session, fields_body(session[1], **blank))
    page = SettingsHtml(reply.text)

    assert page.notices == [("status", "Settings saved.")]
    assert stored(units) == {}
    assert page.fields["probe_cap"].value == "30"  # the default again
    assert page.fields["probe_flag"].value == "false"
    assert page.fields["probe_zone"].value == ""


# Row: Page POST invalid -------------------------------------------------------------------------


@pytest.mark.usefixtures("probe_schema")
def test_an_invalid_field_shows_its_reason_and_nothing_is_saved(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    seed(units, dashboard, {"probe_cap": "45"})
    submitted = {**VALID, "probe_cap": "0", "probe_zone": HOSTILE}

    reply = save(dashboard, session, fields_body(session[1], **submitted))
    page = SettingsHtml(reply.text)
    fields = page.fields

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    assert page.notices == [
        ("alert", "Nothing was saved. Correct the fields marked below, then save again.")
    ]
    assert {key: shown.value for key, shown in fields.items()} == submitted  # as typed
    assert {key: shown.error for key, shown in fields.items() if shown.error} == {
        "probe_cap": "must be at least 1",
        "probe_zone": TIMEZONE_REASON,
    }
    for key in ("probe_cap", "probe_zone"):
        assert fields[key].attrs["aria-invalid"] == "true"
        assert fields[key].error_id == f"error-{key}"
        assert fields[key].attrs["aria-describedby"] == f"hint-{key} error-{key}"
    for key in ("probe_people", "probe_flag", "probe_hours"):
        assert "aria-invalid" not in fields[key].attrs
    assert stored(units) == {"probe_cap": "45"}


@pytest.mark.usefixtures("probe_schema")
def test_submitted_values_are_escaped(dashboard: Dashboard, session: tuple[bytes, str]) -> None:
    submitted = {
        **VALID,
        "probe_zone": HOSTILE,
        "probe_people": f"</textarea>{HOSTILE}",
        "probe_flag": HOSTILE,
    }

    reply = save(dashboard, session, fields_body(session[1], **submitted))
    page = SettingsHtml(reply.text)

    assert "<script>" not in reply.text and "</textarea><" not in reply.text
    assert page.fields["probe_zone"].value == HOSTILE
    assert page.fields["probe_people"].value == f"</textarea>{HOSTILE}"
    assert page.fields["probe_flag"].error == "must be true or false"
    assert "script" not in page.tags


@pytest.mark.usefixtures("probe_schema")
def test_clearing_a_required_setting_that_is_set_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], units: UnitOfWorkFactory
) -> None:
    seed(units, dashboard, {"probe_zone": "Asia/Tokyo"})

    reply = save(dashboard, session, fields_body(session[1], **{**VALID, "probe_zone": " "}))

    error = SettingsHtml(reply.text).fields["probe_zone"].error
    assert error == "is required and can't be cleared once set"
    assert stored(units) == {"probe_zone": "Asia/Tokyo"}


# Row: Page POST malformed -----------------------------------------------------------------------


def _without(key: str) -> dict[str, str]:
    return {name: text for name, text in VALID.items() if name != key}


MALFORMED: list[tuple[str, Any, str]] = [
    ("a missing field", lambda token: fields_body(token, **_without("probe_flag")), WRONG_FIELDS),
    ("an unknown field", lambda token: fields_body(token, **VALID, probe_x="1"), WRONG_FIELDS),
    (
        "a repeated field",
        lambda token: fields_body(token, **VALID) + b"&probe_cap=51",
        REPEATED_FIELD,
    ),
    (
        "a bad escape",
        lambda token: fields_body(token, **_without("probe_cap")) + b"&probe_cap=%FF",
        BAD_BODY,
    ),
    ("an empty field name", lambda token: fields_body(token, **VALID) + b"&=x", WRONG_FIELDS),
    ("an empty pair", lambda token: fields_body(token, **VALID) + b"&&", BAD_BODY),
    ("a field without =", lambda token: fields_body(token, **VALID) + b"&probe_cap", BAD_BODY),
    ("no fields", lambda token: b"", WRONG_FIELDS),
]


@pytest.mark.usefixtures("probe_schema")
@pytest.mark.parametrize(
    ("case", "body", "reason"), MALFORMED, ids=[case for case, _, _ in MALFORMED]
)
def test_a_malformed_form_is_refused_with_400(
    case: str,
    body: Any,
    reason: str,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    reply = save(dashboard, session, body(session[1]))

    assert_malformed(reply)
    assert stored(units) == {}
    assert_refusal_logged(caplog, reason)


@pytest.mark.usefixtures("probe_schema")
@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        (
            b"multipart/form-data; boundary=x",
            b'--x\r\nContent-Disposition: form-data; name="probe_cap"\r\n\r\n50\r\n--x--\r\n',
        ),
        (JSON, json.dumps(VALID).encode()),
        (b"text/plain", form(**VALID)),
    ],
)
def test_a_body_that_isnt_urlencoded_is_refused_with_400(
    content_type: bytes,
    body: bytes,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    reply = save(dashboard, session, body, content_type=content_type)  # the token in the header

    assert_malformed(reply)
    assert stored(units) == {}
    assert_refusal_logged(caplog, NOT_URLENCODED)


def assert_malformed(reply: Reply) -> None:
    assert reply.status == 400
    assert reply.body == MALFORMED_FORM.encode()
    assert reply.header(b"content-type") == b"text/plain; charset=utf-8"
    assert reply.header(b"cache-control") == b"no-store"


def assert_refusal_logged(caplog: pytest.LogCaptureFixture, reason: str) -> None:
    [record] = [r for r in caplog.records if r.getMessage() == "request_refused"]
    logged = tuple(getattr(record, name) for name in ("check", "reason", "method", "path"))
    assert logged == ("form", reason, "POST", "/settings")
    assert "Europe/London" not in str(vars(record)) and "probe_" not in str(vars(record))


# Row: Page POST unchecked -----------------------------------------------------------------------


@pytest.mark.usefixtures("probe_schema")
@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_a_post_without_the_sessions_token_is_refused_with_403(
    token: str | None,
    dashboard: Dashboard,
    session: tuple[bytes, str],
    units: UnitOfWorkFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    body = form(**VALID) if token is None else fields_body(token, **VALID)

    reply = save(dashboard, session, body, header_token=False)

    assert reply.status == 403
    assert reply.body == SECURITY_CHECK_FAILED.encode()
    assert stored(units) == {}
    [record] = [r for r in caplog.records if r.getMessage() == "request_refused"]
    assert getattr(record, "check") == "csrf_token"


def test_a_post_without_a_session_is_refused(
    dashboard: Dashboard, units: UnitOfWorkFactory
) -> None:
    reply = dashboard.post(
        "/settings",
        headers=[(b"content-type", FORM)],
        body=form(csrf_token="x", sender_timezone="Europe/London"),
    )

    assert reply.status == 401
    assert stored(units) == {}


# form_fields on its own -------------------------------------------------------------------------


def run_form_fields(headers: list[tuple[bytes, bytes]], body: bytes) -> dict[str, list[str]]:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "headers": headers, "path": "/x"}
    return asyncio.run(form_fields(Request(scope, receive)))


def test_form_fields_keeps_blank_values_and_repeats() -> None:
    fields = run_form_fields([(b"content-type", FORM)], b"a=1&b=&a=caf%C3%A9+x")

    assert fields == {"a": ["1", "café x"], "b": [""]}


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ([], b"a=1"),
        ([(b"content-type", FORM), (b"content-type", FORM)], b"a=1"),
        ([(b"content-type", JSON)], b"a=1"),
        ([(b"content-type", FORM)], "a=café".encode()),  # not ASCII
        ([(b"content-type", FORM)], b"a=%FF"),
        ([(b"content-type", FORM)], b"a=1&&b=2"),
        ([(b"content-type", FORM)], b"&".join([b"a=1"] * 1001)),
    ],
)
def test_form_fields_refuses_what_isnt_one_strict_urlencoded_form(
    headers: list[tuple[bytes, bytes]], body: bytes
) -> None:
    with pytest.raises(MalformedForm) as raised:
        run_form_fields(headers, body)

    assert raised.value.check == "form"


# Wiring -----------------------------------------------------------------------------------------


def test_build_app_gives_the_entry_and_the_settings_one_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    clocks: list[FrozenClock] = []
    given: dict[str, object] = {}
    real_entry, real_setup = main.build_entry, main.Setup

    def system_clock() -> FrozenClock:
        clocks.append(FrozenClock())
        return clocks[-1]

    def build_entry(config: Any, **kwargs: Any) -> Any:
        given["entry"] = kwargs["clock"]
        return real_entry(config, **kwargs)

    def setup(clock: Any) -> Any:
        given["setup"] = clock
        return real_setup(clock)

    monkeypatch.setattr(main, "SystemClock", system_clock)
    monkeypatch.setattr(main, "build_entry", build_entry)
    monkeypatch.setattr(main, "Setup", setup)

    main.build_app(load_bootstrap(), units=FakeUnits(), secrets=InMemorySecrets("test"))

    [clock] = clocks
    assert given == {"entry": clock, "setup": clock}
