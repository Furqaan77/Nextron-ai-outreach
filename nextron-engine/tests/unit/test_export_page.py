"""The Lead page's subject access export (``POST /contacts/{contact_id}/export`` and ``GET
/exports/{ticket}``, ``nextron.web.export_page``; FR-52, AD-13, AD-17, UX-DR3, Story 3.15)
through ``Dashboard(units=...)`` on a temporary database at head with the real subscriber
registry: one form per Contact on the Lead page (outside any ``hx-disable``, its name inside
one, escaped); the POST's 204 and ``HX-Redirect``, the attachment and its headers; refused
references (Story 3.13's ``check_request_ref``: 200, the message, nothing written, never
echoed); unknown Contacts (404); malformed
forms (400); the CSRF, ``Origin`` and ``Sec-Fetch-Site`` refusals (403); the ticket rules (one
use, 60 s on an injected clock, another session, an unknown ticket, the 16 held); the routes'
declarations; nothing logged; and the module's imports."""

import ast
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contact_export import ContactExport
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.redaction import redact
from nextron.domain.segments import validate_segments
from nextron.web import export_page
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.export_page import (
    CONTACT_NOT_FOUND,
    EXPORT_EXPIRED,
    MAX_TICKETS,
    TICKET_SECONDS,
    ExportTickets,
    download_response,
)
from nextron.web.forms import MALFORMED_FORM
from nextron.web.routing import declared_personal_data
from tests.asgi import Reply
from tests.contracts.support import PACKAGE
from tests.dashboard import FORM, ORIGIN, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Node, Tree, queue_on

T = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
EMAIL = "jane.doe@firm.co.uk"
REFERENCE = "Email of 18 Nov 2026"
# Story 3.13's check_request_ref, which the export shares with the erasure
REF_FORM = (
    "The request reference must be 1 to 200 characters on one line, with no space at either "
    "end."
)
REF_ADDRESS = "The request reference must not hold an email address (no @)."
UNKNOWN = "01KA0000000000000000000000"  # a ULID no Contact has
TICKET_PATTERN = re.compile(r"/exports/([A-Za-z0-9_-]{43})")


class Ticks:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class World:
    """The dashboard on real units of work, a session, and a Lead with Jane and an email-only
    Contact."""

    dashboard: Dashboard
    units: UnitOfWorkFactory
    engine: Engine
    ticks: Ticks
    cookie: bytes
    token: str
    lead: str
    jane: str
    info: str

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def requests(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT contact_id, kind, status, request_ref FROM data_subject_requests")

    def written(self) -> list[Any]:
        return [
            self.sql(f"SELECT * FROM {table} ORDER BY rowid")
            for table in ("data_subject_requests", "events", "suppression_entries")
        ]

    def post(
        self,
        contact_id: str,
        *,
        cookie: bytes | None = None,
        token: str | None = None,
        **fields: str,
    ) -> Reply:
        session_token = token or self.token
        return self.dashboard.post(
            f"/contacts/{contact_id}/export",
            cookie=cookie or self.cookie,
            token=session_token,
            headers=[(b"content-type", FORM)],
            body=form(csrf_token=session_token, **fields),
        )

    def get(self, path: str, cookie: bytes | None = None) -> Reply:
        return self.dashboard.get(path, cookie=cookie or self.cookie)


def first_segment(engine: Engine) -> str:
    with engine.connect() as connection:
        [(segment,), *_] = connection.exec_driver_sql(
            "SELECT id FROM segments ORDER BY key"
        ).all()
    return str(segment)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    ticks = Ticks()
    dashboard = Dashboard(units=units, clock=FrozenClock(T), monotonic=ticks)
    cookie = dashboard.open_session()
    discovery, research = Discovery(clock, Pipeline(clock), queue_on(clock)), Research(clock)
    segment = first_segment(db_engine)

    @use_case
    def seed() -> tuple[str, str, str]:
        with current().transaction():
            identity = LeadIdentity("Firm LLP", segment, web_domain="firm.co.uk")
            lead = discovery.upsert_lead(identity, LeadSource.CSV).lead_id
            people = []
            for candidate in (
                ContactCandidate(name="Jane <i>Doe</i>", email=EMAIL),
                ContactCandidate(email="info@firm.co.uk"),
            ):
                added = research.add_contact(
                    lead, candidate, source=ProvenanceSource.CSV, detail="CSV", retrieved_at=T
                )
                people.append(added.contact_id)
            return lead, people[0], people[1]

    with units.open():
        lead, jane, info = seed()
    return World(
        dashboard,
        units,
        db_engine,
        ticks,
        cookie,
        dashboard.csrf_token(cookie),
        lead,
        jane,
        info,
    )


def ticket_of(reply: Reply) -> str:
    assert reply.status == 204, reply
    assert reply.body == b""
    assert reply.header(b"cache-control") == b"no-store"
    location = reply.header(b"hx-redirect")
    assert location is not None
    found = TICKET_PATTERN.fullmatch(location.decode("ascii"))
    assert found is not None, location
    return found.group(1)


# The form on the Lead page ----------------------------------------------------------------------


def export_forms(world: World, lead: str | None = None) -> list[Node]:
    reply = world.get(f"/leads/{lead or world.lead}")
    assert reply.status == 200, reply
    return Tree(reply.text).find_all("form", class_="contact-export")


def test_the_lead_page_has_one_export_form_per_contact_outside_hx_disable(world: World) -> None:
    reply = world.get(f"/leads/{world.lead}")
    tree = Tree(reply.text)

    section = tree.find("section", class_="lead-requests")
    assert section.find("h2").text == "Data subject requests"
    forms = section.find_all("form", class_="contact-export")
    assert [node.attrs["hx-post"] for node in forms] == [
        f"/contacts/{world.jane}/export",
        f"/contacts/{world.info}/export",
    ]
    for node, label in zip(forms, ("Jane <i>Doe</i>", "info@firm.co.uk"), strict=True):
        assert node.attrs["method"] == "post"
        assert not any("hx-disable" in up.attrs for up in (node, *node.ancestors()))
        [token] = node.find_all("input", name="csrf_token")
        assert token.attrs["type"] == "hidden" and token.attrs["value"] == world.token
        [reference] = node.find_all("input", name="request_ref")
        assert reference.attrs["maxlength"] == "200" and reference.attrs["type"] == "text"
        assert node.find("button").text == "Export data (JSON)"
        [name] = node.find_all("span")
        assert "hx-disable" in name.attrs and name.text == label
    contacts = tree.find("section", class_="lead-contacts")
    assert list(tree.root.walk()).index(section) > list(tree.root.walk()).index(contacts)


def test_the_contacts_name_is_escaped_inside_hx_disable(world: World) -> None:
    reply = world.get(f"/leads/{world.lead}")

    assert "<i>Doe" not in reply.text
    assert Tree(reply.text).disabled("Jane <i>Doe</i>") == [True, True]  # the row and the form


def test_a_lead_without_contacts_has_no_requests_section(world: World) -> None:
    clock = FrozenClock(T)
    discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
    identity = LeadIdentity("Empty LLP", first_segment(world.engine), web_domain="empty.co.uk")

    @use_case
    def bare() -> str:
        with current().transaction():
            return discovery.upsert_lead(identity, LeadSource.CSV).lead_id

    with world.units.open():
        lead = bare()

    reply = world.get(f"/leads/{lead}")
    assert reply.status == 200
    assert Tree(reply.text).find_all("section", class_="lead-requests") == []


# The POST and the download ----------------------------------------------------------------------


def test_an_export_answers_204_with_a_one_time_download(world: World) -> None:
    ticket = ticket_of(world.post(world.jane, request_ref=REFERENCE))

    assert world.requests() == [(world.jane, "export", "completed", REFERENCE)]
    download = world.get(f"/exports/{ticket}")
    assert download.status == 200
    assert download.header(b"content-type") == b"application/json"
    assert download.header(b"content-disposition") == (
        f'attachment; filename="contact-{world.jane}-export.json"'.encode()
    )
    assert download.header(b"cache-control") == b"no-store"
    file = json.loads(download.text)
    assert file["contact_id"] == world.jane and file["schema_version"] == 1
    assert file["research"]["contact"][0]["email"] == EMAIL
    assert [row["request_ref"] for row in file["compliance"]["requests"]] == [REFERENCE]
    again = world.get(f"/exports/{ticket}")
    assert (again.status, again.text) == (404, EXPORT_EXPIRED)
    assert again.header(b"cache-control") == b"no-store"


@pytest.mark.parametrize(
    ("typed", "message"),
    [
        ("", REF_FORM),
        ("   ", REF_FORM),
        ("  secret ref ", REF_FORM),
        ("q" * 201, REF_FORM),
        ("secret\x07ref", REF_FORM),
        ("secret\nref", REF_FORM),
        ("secret@firm.co.uk", REF_ADDRESS),
    ],
)
def test_a_refused_reference_answers_the_form_with_its_message_and_writes_nothing(
    world: World, typed: str, message: str
) -> None:
    before = world.written()

    reply = world.post(world.jane, request_ref=typed)

    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    assert reply.header(b"hx-redirect") is None
    tree = Tree(reply.text)
    [node] = tree.find_all("form", class_="contact-export")
    assert node.attrs["hx-post"] == f"/contacts/{world.jane}/export"
    assert node.find("p", role="alert").text == message
    assert node.find("input", name="request_ref").attrs.get("value") is None
    if typed.strip():
        assert "secret" not in reply.text and "q" * 201 not in reply.text
    assert world.written() == before


@pytest.mark.parametrize("contact_id", ["nope", UNKNOWN])
@pytest.mark.parametrize("typed", [REFERENCE, ""])
def test_an_unknown_contact_answers_404_and_writes_nothing(
    world: World, contact_id: str, typed: str
) -> None:
    before = world.written()

    reply = world.post(contact_id, request_ref=typed)

    assert (reply.status, reply.text) == (404, CONTACT_NOT_FOUND)
    assert reply.header(b"cache-control") == b"no-store"
    assert world.written() == before


@pytest.mark.parametrize("error", [KeyError("lead_id"), IndexError("list index out of range")])
def test_a_lookup_error_from_a_bug_is_a_500_not_a_404(
    world: World, monkeypatch: pytest.MonkeyPatch, error: LookupError
) -> None:
    def broken(contact_id: str, request_ref: str) -> ContactExport:
        raise error

    monkeypatch.setattr(world.dashboard.erasures, "export_on_request", broken)

    reply = world.dashboard.post(
        f"/contacts/{world.jane}/export",
        cookie=world.cookie,
        token=world.token,
        headers=[(b"content-type", FORM)],
        body=form(csrf_token=world.token, request_ref=REFERENCE),
        catch=True,
    )

    assert reply.status == 500
    assert reply.text != CONTACT_NOT_FOUND
    assert reply.error is error


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"request_ref": REFERENCE, "kind": "erase"},
        {"reference": REFERENCE},
    ],
)
def test_a_malformed_form_answers_400_and_writes_nothing(
    world: World, fields: dict[str, str]
) -> None:
    before = world.written()

    reply = world.post(world.jane, **fields)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert world.written() == before


def test_a_repeated_reference_is_malformed(world: World) -> None:
    before = world.written()

    reply = world.dashboard.post(
        f"/contacts/{world.jane}/export",
        cookie=world.cookie,
        token=world.token,
        headers=[(b"content-type", FORM)],
        body=f"csrf_token={world.token}&request_ref=a&request_ref=b".encode(),
    )

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert world.written() == before


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


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_post_failing_a_security_check_is_refused_and_writes_nothing(
    world: World, case: str
) -> None:
    header, field, origin, extra = REFUSALS[case]
    fields = {} if field is None else {"csrf_token": world.token if field == "token" else field}
    before = world.written()

    reply = world.dashboard.post(
        f"/contacts/{world.jane}/export",
        cookie=world.cookie,
        token=world.token if header == "token" else header,
        origin=origin,
        headers=[(b"content-type", FORM), *extra],
        body=form(**fields, request_ref=REFERENCE),
    )

    assert (reply.status, reply.text) == (403, SECURITY_CHECK_FAILED)
    assert reply.header(b"cache-control") == b"no-store"
    assert world.written() == before


def test_both_routes_need_a_session(world: World) -> None:
    before = world.written()

    posted = world.dashboard.post(
        f"/contacts/{world.jane}/export",
        headers=[(b"content-type", FORM)],
        body=form(request_ref=REFERENCE),
    )
    ticket = ticket_of(world.post(world.jane, request_ref=REFERENCE))
    fetched = world.dashboard.get(f"/exports/{ticket}")

    assert posted.status == 401 and fetched.status == 401
    assert world.requests() == [(world.jane, "export", "completed", REFERENCE)]
    assert world.written() != before
    assert world.get(f"/exports/{ticket}").status == 200  # the locked GET didn't consume it


# The ticket rules -------------------------------------------------------------------------------


def test_a_ticket_expires_after_60_seconds(world: World) -> None:
    kept = ticket_of(world.post(world.jane, request_ref=REFERENCE))
    late = ticket_of(world.post(world.info, request_ref=REFERENCE))

    world.ticks.now += TICKET_SECONDS - 0.001
    assert world.get(f"/exports/{kept}").status == 200
    world.ticks.now += 0.001
    reply = world.get(f"/exports/{late}")

    assert (reply.status, reply.text) == (404, EXPORT_EXPIRED)
    assert TICKET_SECONDS == 60


def test_another_sessions_ticket_is_refused_and_burned(world: World) -> None:
    ticket = ticket_of(world.post(world.jane, request_ref=REFERENCE))
    other = world.dashboard.open_session()

    stolen = world.get(f"/exports/{ticket}", cookie=other)
    owner = world.get(f"/exports/{ticket}")

    assert (stolen.status, stolen.text) == (404, EXPORT_EXPIRED)
    assert (owner.status, owner.text) == (404, EXPORT_EXPIRED)  # a ticket is presented once


@pytest.mark.parametrize("ticket", ["unknown", "A" * 43, "%2e%2e"])
def test_an_unknown_ticket_is_404_without_echoing_it(world: World, ticket: str) -> None:
    reply = world.get(f"/exports/{ticket}")

    assert (reply.status, reply.text) == (404, EXPORT_EXPIRED)
    assert reply.header(b"cache-control") == b"no-store"


def test_at_most_16_tickets_are_held_and_the_oldest_is_dropped() -> None:
    ticks = Ticks()
    tickets = ExportTickets(ticks)
    export = ContactExport("contact-x-export.json", b"{}")

    issued = [tickets.issue("session", export) for _ in range(MAX_TICKETS + 1)]

    assert MAX_TICKETS == 16 and len(tickets) == 16
    assert tickets.take(issued[0], "session") is None
    assert tickets.take(issued[1], "session") is export
    assert tickets.take(issued[-1], "session") is export
    assert len(set(issued)) == len(issued)


def test_expired_tickets_are_dropped_when_another_is_issued() -> None:
    ticks = Ticks()
    tickets = ExportTickets(ticks)
    export = ContactExport("contact-x-export.json", b"{}")
    tickets.issue("session", export)
    ticks.now += TICKET_SECONDS

    tickets.issue("session", export)

    assert len(tickets) == 1


def test_a_ticket_is_bound_to_its_sessions_token() -> None:
    tickets = ExportTickets(Ticks())
    export = ContactExport("contact-x-export.json", b"{}")
    first = tickets.issue("session-a", export)
    second = tickets.issue("session-a", export)

    assert tickets.take(first, "session-b") is None
    assert tickets.take(second, None) is None
    assert len(tickets) == 0
    with pytest.raises(ValueError):
        tickets.issue("", export)
    with pytest.raises(TypeError):
        tickets.issue("session-a", b"{}")  # type: ignore[arg-type]


def test_the_download_response_is_an_uncached_json_attachment() -> None:
    response = download_response(ContactExport("contact-01J-export.json", b'{"a": 1}'))

    assert response.status_code == 200
    assert response.body == b'{"a": 1}'
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-disposition"] == (
        'attachment; filename="contact-01J-export.json"'
    )
    assert response.headers["cache-control"] == "no-store"


def test_a_ticket_in_a_logged_path_is_redacted() -> None:
    ticket = ExportTickets(Ticks()).issue(
        "session", ContactExport("contact-x-export.json", b"{}")
    )

    assert ticket not in redact(f'"GET /exports/{ticket} HTTP/1.1" 200')


# Declarations, logging and imports --------------------------------------------------------------


def test_both_routes_declare_personal_data(world: World) -> None:
    routes = {
        route.name: route  # type: ignore[attr-defined]
        for route in world.dashboard.app.routes
        if getattr(route, "name", None) in ("contact_export", "contact_export_download")
    }

    assert routes["contact_export"].methods == {"POST"}  # type: ignore[attr-defined]
    assert routes["contact_export_download"].methods == {"GET"}  # type: ignore[attr-defined]
    for route in routes.values():
        assert declared_personal_data(route.endpoint) is True  # type: ignore[attr-defined]


def test_nothing_in_the_export_path_is_logged(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    ticket = ticket_of(world.post(world.jane, request_ref=REFERENCE))
    world.get(f"/exports/{ticket}")
    world.post(world.jane, request_ref="")
    world.get(f"/exports/{ticket}")

    logged = " ".join(
        [record.getMessage() for record in caplog.records]
        + [str(record.__dict__) for record in caplog.records]
    )
    for value in (ticket, f"contact-{world.jane}-export.json", REFERENCE, "Jane", EMAIL):
        assert value not in logged, value


def test_the_module_imports_no_service_or_database() -> None:
    tree = ast.parse((PACKAGE / "web" / "export_page.py").read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    modules += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]

    assert not [name for name in modules if name.startswith("nextron.db")]
    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert all(name.startswith("nextron.app.queries.") for name in app_modules), app_modules
    assert export_page.FORM_TEMPLATE == "contact_export_form.html"
