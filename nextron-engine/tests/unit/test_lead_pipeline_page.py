"""The Lead page's Pipeline panel (Story 9.1; FR-45, FR-37, FR-63, AD-4, AD-13) through
``Dashboard(units=...)`` on a temporary database at head: the moves offered at each stage (never
an automatic one), the three POSTs for each row of the I/O matrix (a forged automatic stage, an
illegal move, booking and rebooking a call, closing and reopening, the deal value and notes and
their refusals, malformed forms and unknown Leads), CSRF, ``no-store``, hostile notes escaped
inside ``hx-disable``, the opt-out line, and the queries-only import rule."""

import ast
import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import pytest

from nextron.app.compliance.api import Compliance
from nextron.domain.contactability import ContactSubject, SuppressionScope
from nextron.domain.contacts import ContactCandidate
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.suppression_entry import SuppressionSource
from nextron.web import lead_pipeline
from nextron.web.forms import MALFORMED_FORM
from nextron.web.lead_pipeline import OPTED_OUT, REFUSALS
from nextron.domain.manual_stages import StageRefusal
from tests.asgi import Reply
from tests.contracts.support import PACKAGE
from tests.dashboard import FORM
from tests.unit.test_leads_page import (  # noqa: F401 (the fixture)
    Tree,
    World,
    sync_markets,
    world,
)

S = PipelineStage
HOSTILE = '<script>alert(1)</script> <b hx-get="/x">Pat</b>\nline two'


def post(world: World, path: str, *pairs: tuple[str, str], token: bool = True) -> Reply:
    return world.dashboard.post(
        path,
        cookie=world.cookie,
        token=world.dashboard.csrf_token(world.cookie) if token else None,
        headers=[(b"content-type", FORM)],
        body=urlencode(pairs).encode("ascii"),
    )


def panel_of(reply: Reply) -> Tree:
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return Tree(reply.text)


def move(world: World, lead: str, value: str) -> Tree:
    return panel_of(post(world, f"/leads/{lead}/stage", ("move", value)))


def book(world: World, lead: str, contact: str, day: str, at: str, zone: str = "") -> Tree:
    return panel_of(
        post(
            world,
            f"/leads/{lead}/call",
            ("contact_id", contact),
            ("date", day),
            ("time", at),
            ("timezone", zone),
        )
    )


def deal(world: World, lead: str, value: str, notes: str) -> Tree:
    return panel_of(post(world, f"/leads/{lead}/deal", ("deal_value", value), ("notes", notes)))


def notice(panel: Tree) -> str:
    return panel.find("section", id="lead-pipeline").find("p", class_="notice").text


def buttons(page: Tree) -> list[str]:
    section = page.find("section", id="lead-pipeline")
    return [
        form.find("button").text
        for form in section.find_all("form")
        if form.attrs.get("action", "").endswith("/stage")
    ]


def stored(world: World, lead: str) -> tuple[Any, ...]:
    [row] = world.sql(
        "SELECT pipeline_stage, deal_value, notes FROM leads WHERE id = ?", (lead,)
    )
    return tuple(row)


def counts(world: World) -> tuple[int, int]:
    [(events,)] = world.sql("SELECT COUNT(*) FROM events")
    [(calls,)] = world.sql("SELECT COUNT(*) FROM call_outcomes")
    return int(events), int(calls)


@pytest.fixture
def lead(world: World) -> str:
    return world.lead("Smith Law", "smith-law.co.uk")


@pytest.fixture
def pat(world: World, lead: str) -> str:
    contact = world.add(lead, ContactCandidate(name="Pat Lee", email="pat@smith-law.co.uk"))
    sync_markets(world)
    [(market,)] = world.sql("SELECT id FROM markets WHERE code = 'uk'")
    world.sql(
        "UPDATE contacts SET market_id = ?, tz = 'Europe/London' WHERE id = ?", (market, contact)
    )
    return contact


# The panel -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("at", "expected", "booking"),
    [
        (
            S.CALL_HELD,
            ["Audit proposed", "Audit won", "Build proposed", "Won", "Close lost", "Close disqualified"],
            None,
        ),
        (S.REPLIED, ["Close lost", "Close disqualified"], "Book call"),
        (S.CALL_BOOKED, ["Close lost", "Close disqualified"], "Rebook call"),
        (S.NEW, ["Close lost", "Close disqualified"], None),
        (S.LOST, ["Reopen"], None),
    ],
)
def test_the_panel_offers_only_the_operator_moves(
    world: World, lead: str, at: PipelineStage, expected: list[str], booking: str | None
) -> None:
    world.stage(lead, at)

    page = world.page(f"/leads/{lead}")

    assert buttons(page) == expected
    section = page.find("section", id="lead-pipeline")
    for form in section.find_all("form"):
        action = form.attrs["action"]
        assert form.attrs["method"] == "post" and form.attrs["hx-post"] == action
        assert form.attrs["hx-target"] == "#lead-pipeline"
        assert not any("hx-disable" in node.attrs for node in form.ancestors())
        assert [i.attrs["name"] for i in form.find_all("input") if i.attrs.get("name") == "csrf_token"]
    calls = section.find_all("form", class_="booking-form")
    assert [form.find("button").text for form in calls] == ([booking] if booking else [])
    assert section.find("p", class_="deal-line").text == "Deal value: —"


# The stage form --------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["new", "queued", "contacted", "replied", "interested", "not_now", "call_held"])
def test_a_forged_automatic_stage_changes_nothing(world: World, lead: str, target: str) -> None:
    world.stage(lead, S.CALL_HELD)
    before = counts(world)

    panel = move(world, lead, target)

    assert notice(panel) == "This stage is set automatically"
    assert (stored(world, lead)[0], counts(world)) == ("call_held", before)


def test_an_illegal_move_changes_nothing(world: World, lead: str) -> None:
    world.stage(lead, S.REPLIED)
    before = counts(world)

    panel = move(world, lead, "won")

    assert notice(panel) == "That move isn't available at this stage"
    assert (stored(world, lead)[0], counts(world)) == ("replied", before)


def test_move_close_and_reopen(world: World, lead: str) -> None:
    world.stage(lead, S.CALL_HELD)

    assert notice(move(world, lead, "audit_won")) == "Stage set to Audit won."
    assert stored(world, lead)[0] == "audit_won"
    panel = move(world, lead, "lost")
    assert notice(panel) == "Stage set to Lost."
    assert buttons(panel) == ["Reopen"]
    assert notice(move(world, lead, "disqualified")) == "This Lead is already closed"
    assert notice(move(world, lead, "reopen")) == "Lead reopened at Audit won."
    assert stored(world, lead)[0] == "audit_won"
    assert notice(move(world, lead, "reopen")) == "This Lead can't be reopened"
    actors = world.sql("SELECT DISTINCT actor FROM events WHERE cause IN "
                       "('operator_move', 'closed_lost', 'reopened')")
    assert actors == [("operator",)]


def test_the_opt_out_line_shows_on_a_reopened_lead(world: World, lead: str, pat: str) -> None:
    world.stage(lead, S.INTERESTED)
    world.transact(
        lambda: Compliance(world.clock).suppress(
            ContactSubject(pat), SuppressionScope.PERSON, SuppressionSource.ONE_CLICK, None
        )
    )
    page = world.page(f"/leads/{lead}")
    assert page.find("p", class_="opted-out").text == OPTED_OUT

    panel = move(world, lead, "reopen")

    assert stored(world, lead)[0] == "interested"
    assert panel.find("p", class_="opted-out").text == OPTED_OUT


# The call form ---------------------------------------------------------------------------------


def test_book_then_rebook_a_call(world: World, lead: str, pat: str) -> None:
    world.stage(lead, S.REPLIED)

    panel = book(world, lead, pat, "2026-11-20", "14:30", "America/New_York")

    assert notice(panel) == "Call booked."
    assert stored(world, lead)[0] == "call_booked"
    assert world.sql("SELECT scheduled_at, prospect_tz, booked_by FROM call_outcomes") == [
        ("2026-11-20T19:30:00.000000Z", "America/New_York", "operator")
    ]
    assert panel.find("p", class_="booking-line").text == (
        "Call booked for 2026-11-20 14:30 America/New_York (2026-11-20 19:30 UTC)"
    )
    assert panel.find("form", class_="booking-form").find("button").text == "Rebook call"
    # a blank timezone is the Contact's
    panel = book(world, lead, pat, "2026-11-21", "09:00")
    assert notice(panel) == "Call rebooked."
    assert world.sql(
        "SELECT scheduled_at, prospect_tz, superseded_at IS NULL FROM call_outcomes ORDER BY rowid"
    ) == [
        ("2026-11-20T19:30:00.000000Z", "America/New_York", 0),
        ("2026-11-21T09:00:00.000000Z", "Europe/London", 1),
    ]


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        (("", "2026-11-20", "14:30", "UTC"), StageRefusal.BOOKING_INCOMPLETE),
        (("{pat}", "", "14:30", "UTC"), StageRefusal.BOOKING_INCOMPLETE),
        (("{pat}", "2026-11-20", "", "UTC"), StageRefusal.BOOKING_INCOMPLETE),
        (("{pat}", "2026-11-20", "14:30", "Mars/Base"), StageRefusal.BAD_TIMEZONE),
        (("{pat}", "2026-03-29", "01:30", "Europe/London"), StageRefusal.BAD_DATE_TIME),
        (("{pat}", "2026-02-30", "10:00", "UTC"), StageRefusal.BAD_DATE_TIME),
        (("01JD3QZ8X5V9K2M7N4P6R8T0AC", "2026-11-20", "14:30", "UTC"), StageRefusal.BAD_CONTACT),
    ],
)
def test_a_bad_booking_changes_nothing(
    world: World, lead: str, pat: str, fields: tuple[str, ...], reason: StageRefusal
) -> None:
    world.stage(lead, S.REPLIED)
    before = counts(world)
    contact, day, at, zone = (value.format(pat=pat) for value in fields)

    panel = book(world, lead, contact, day, at, zone)

    assert notice(panel) == REFUSALS[reason]
    assert (stored(world, lead)[0], counts(world)) == ("replied", before)
    # the form keeps what was typed
    form = panel.find("form", class_="booking-form")
    assert form.find("input", id="call-date").attrs["value"] == day
    assert form.find("input", id="call-time").attrs["value"] == at
    assert form.find("input", id="call-timezone").attrs["value"] == zone
    chosen = [o.attrs["value"] for o in form.find_all("option") if "selected" in o.attrs]
    assert chosen == ([contact] if contact == pat else [""])


def test_the_call_forms_contact_options(world: World, lead: str, pat: str) -> None:
    world.stage(lead, S.REPLIED)
    hostile = world.add(lead, ContactCandidate(name=HOSTILE.splitlines()[0]))
    gone = world.add(lead, ContactCandidate(name="Erased Person"))
    world.transact(lambda: world.dashboard.erasures.erase_contact(gone, None, "retention"))

    page = world.page(f"/leads/{lead}")
    form = page.find("form", class_="booking-form")
    values = [o.attrs["value"] for o in form.find_all("option")]
    assert values == ["", pat, hostile]
    assert gone not in values
    assert page.disabled("alert(1)") and all(page.disabled("alert(1)"))
    for name in ("contact_id", "date", "time"):
        [field] = [f for f in (*form.find_all("select"), *form.find_all("input")) if f.attrs.get("name") == name]
        assert "required" in field.attrs
    # the booked Contact is the one selected
    panel = book(world, lead, hostile, "2026-11-20", "14:30", "UTC")
    form = panel.find("form", class_="booking-form")
    assert [o.attrs["value"] for o in form.find_all("option") if "selected" in o.attrs] == [hostile]


def test_the_booking_line_shows_only_at_call_booked(world: World, lead: str, pat: str) -> None:
    world.stage(lead, S.REPLIED)
    assert book(world, lead, pat, "2026-11-20", "14:30", "UTC").find_all("p", class_="booking-line")

    panel = move(world, lead, "lost")

    assert panel.find_all("p", class_="booking-line") == []
    world.stage(lead, S.CALL_HELD)  # a Call Outcome moved it on (Story 9.4)
    assert world.page(f"/leads/{lead}").find_all("p", class_="booking-line") == []


def test_won_and_the_closes_ask_for_confirmation(world: World, lead: str) -> None:
    world.stage(lead, S.CALL_HELD)

    section = world.page(f"/leads/{lead}").find("section", id="lead-pipeline")

    confirms = {
        form.find("button").text: form.attrs.get("hx-confirm")
        for form in section.find_all("form")
        if form.attrs.get("action", "").endswith("/stage")
    }
    assert confirms == {
        "Audit proposed": None,
        "Audit won": None,
        "Build proposed": None,
        "Won": "Mark this Lead as won?",
        "Close lost": "Close this Lead as lost?",
        "Close disqualified": "Close this Lead as disqualified?",
    }


def test_messages_of_the_matrix() -> None:
    assert REFUSALS[StageRefusal.BOOKING_INCOMPLETE] == "Enter the call's date, time and Contact"
    assert REFUSALS[StageRefusal.BAD_TIMEZONE] == "Enter a valid timezone, such as Europe/London"
    assert REFUSALS[StageRefusal.BAD_DEAL_VALUE] == "Enter the deal value in whole US dollars"
    assert REFUSALS[StageRefusal.NOTES_TOO_LONG] == "Notes can be at most 5,000 characters"
    assert set(REFUSALS) == set(StageRefusal)


def test_no_timezone_anywhere_is_refused(world: World, lead: str, pat: str) -> None:
    world.stage(lead, S.REPLIED)
    world.sql("UPDATE contacts SET market_id = NULL, tz = NULL WHERE id = ?", (pat,))

    panel = book(world, lead, pat, "2026-11-20", "14:30")

    assert notice(panel) == REFUSALS[StageRefusal.NO_TIMEZONE]
    assert world.sql("SELECT COUNT(*) FROM call_outcomes") == [(0,)]


def test_another_leads_contact_is_refused(world: World, lead: str) -> None:
    world.stage(lead, S.REPLIED)
    other = world.lead("Other Firm", "other.co.uk", minutes=1)
    stranger = world.add(other, ContactCandidate(name="Sam Roe"))

    panel = book(world, lead, stranger, "2026-11-20", "14:30", "UTC")

    assert notice(panel) == REFUSALS[StageRefusal.BAD_CONTACT]


# The deal form ---------------------------------------------------------------------------------


def test_save_the_deal_details_with_hostile_notes(world: World, lead: str) -> None:
    world.stage(lead, S.CALL_HELD)

    panel = deal(world, lead, " 12000 ", HOSTILE.replace("\n", "\r\n"))

    assert notice(panel) == "Deal details saved."
    assert stored(world, lead) == ("call_held", 12_000_000_000, HOSTILE)
    assert panel.find("p", class_="deal-line").text == "Deal value: $12,000"
    assert "<script>" not in panel_text(world, lead) and "&lt;script&gt;" in panel_text(world, lead)
    page = world.page(f"/leads/{lead}")
    assert page.disabled("alert(1)") and all(page.disabled("alert(1)"))
    assert page.find("input", id="deal-value").attrs["value"] == "12000"
    # the textarea holds the stored notes exactly (the parser keeps the newline HTML drops)
    typed = notes_area(panel_text(world, lead))
    assert typed == HOSTILE
    deal(world, lead, "15000", typed)
    assert stored(world, lead) == ("call_held", 15_000_000_000, HOSTILE)
    # blank clears both
    deal(world, lead, "", "  ")
    assert stored(world, lead) == ("call_held", None, None)


def notes_area(html: str) -> str:
    """The deal form's textarea text, unescaped, without the leading newline HTML drops."""
    found = re.search(r'<textarea id="deal-notes"[^>]*>(?:\r?\n)?(.*?)</textarea>', html, re.S)
    assert found is not None
    return unescape(found.group(1))


def panel_text(world: World, lead: str) -> str:
    return world.get(f"/leads/{lead}").text


@pytest.mark.parametrize(
    ("value", "notes", "reason"),
    [
        ("-5", "", StageRefusal.BAD_DEAL_VALUE),
        ("abc", "", StageRefusal.BAD_DEAL_VALUE),
        ("1.5", "", StageRefusal.BAD_DEAL_VALUE),
        ("1000000001", "", StageRefusal.BAD_DEAL_VALUE),
        ("5", "x" * 5_001, StageRefusal.NOTES_TOO_LONG),
    ],
)
def test_a_bad_deal_changes_nothing(
    world: World, lead: str, value: str, notes: str, reason: StageRefusal
) -> None:
    before = (stored(world, lead), counts(world))

    panel = deal(world, lead, value, notes)

    assert notice(panel) == REFUSALS[reason]
    assert (stored(world, lead), counts(world)) == before
    # the form keeps what was typed
    assert panel.find("input", id="deal-value").attrs["value"] == value
    assert panel.find("textarea", id="deal-notes").text.strip() == notes


def test_a_refused_deal_form_keeps_hostile_notes_escaped(world: World, lead: str) -> None:
    reply = post(world, f"/leads/{lead}/deal", ("deal_value", "abc"), ("notes", HOSTILE))

    assert "<script>" not in reply.text and "&lt;script&gt;" in reply.text
    panel = panel_of(reply)
    assert notice(panel) == REFUSALS[StageRefusal.BAD_DEAL_VALUE]
    area = panel.find("textarea", id="deal-notes")
    assert notes_area(reply.text) == HOSTILE
    assert any("hx-disable" in node.attrs for node in area.ancestors())


# Refused requests ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "pairs"),
    [
        ("stage", (("move", "won"), ("move", "lost"))),
        ("stage", (("move", "promoted"),)),
        ("stage", (("move", "won"), ("extra", "1"))),
        ("stage", ()),
        ("call", (("contact_id", "x"), ("date", "x"), ("time", "x"))),
        ("call", (("contact_id", "x"), ("date", "x"), ("time", "x"), ("timezone", ""), ("timezone", ""))),
        ("deal", (("deal_value", "5"),)),
        ("deal", (("deal_value", "5"), ("notes", ""), ("note", ""))),
    ],
)
def test_a_malformed_form_is_400(
    world: World, lead: str, path: str, pairs: tuple[tuple[str, str], ...]
) -> None:
    before = (stored(world, lead), counts(world))

    reply = post(world, f"/leads/{lead}/{path}", *pairs)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert (stored(world, lead), counts(world)) == before


@pytest.mark.parametrize("path", ["stage", "call", "deal"])
def test_an_unknown_or_merged_lead_is_404(world: World, lead: str, path: str) -> None:
    newer = world.lead("Newer", "newer.co.uk", minutes=1)
    world.merge(lead, newer)
    pairs = {
        "stage": (("move", "lost"),),
        "call": (
            ("contact_id", "01JD3QZ8X5V9K2M7N4P6R8T0AD"),
            ("date", "2026-11-20"),
            ("time", "14:30"),
            ("timezone", "UTC"),
        ),
        "deal": (("deal_value", "5"), ("notes", "")),
    }[path]

    for target in ("01JD3QZ8X5V9K2M7N4P6R8T0AC", "not-a-ulid", newer):
        reply = post(world, f"/leads/{target}/{path}", *pairs)
        assert (reply.status, reply.text) == (404, "That Lead doesn't exist.")
        assert target not in reply.text


def test_a_post_without_its_token_is_refused(world: World, lead: str) -> None:
    world.stage(lead, S.CALL_HELD)

    reply = post(world, f"/leads/{lead}/stage", ("move", "won"), token=False)

    assert reply.status == 403
    assert stored(world, lead)[0] == "call_held"


def test_the_routes_declare_personal_data(world: World) -> None:
    routes = {
        getattr(route, "path"): route
        for route in world.dashboard.app.routes
        if getattr(route, "path", "").startswith("/leads/{lead_id}/")
    }
    for path in ("/leads/{lead_id}/stage", "/leads/{lead_id}/call", "/leads/{lead_id}/deal"):
        assert getattr(routes[path], "methods") == {"POST"}
        assert getattr(getattr(routes[path], "endpoint"), "__nextron_personal_data__") is True


def test_the_view_module_reads_only_through_queries() -> None:
    tree = ast.parse((PACKAGE / "web" / "lead_pipeline.py").read_text(encoding="utf-8"))
    modules = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    ] + [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert app_modules and all(name.startswith("nextron.app.queries.") for name in app_modules)
    assert not [name for name in modules if name.startswith("nextron.db")]
    assert lead_pipeline.dollars(None) == "—"
    assert lead_pipeline.dollars(1_234_567_000_000) == "$1,234,567"
