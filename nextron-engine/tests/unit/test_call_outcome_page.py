"""Story 9.4's dashboard side (FR-69, AD-13) through ``Dashboard(units=...)`` on a temporary
database at head: the notification area's "Call Outcome due" line (shown, not yet, cleared, a
hostile Lead name escaped in ``hx-disable``), the Pipeline panel's Call Outcome form for each I/O
matrix row (held, no-show, a correction, nothing changed, each refused field re-rendered with
what was typed and nothing stored), malformed forms (400), unknown Leads (404), CSRF,
``no-store`` and ``@personal_data``."""

from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import pytest

from nextron.domain.contacts import ContactCandidate
from nextron.domain.pipeline_stage import PipelineStage
from nextron.web.forms import MALFORMED_FORM
from nextron.web.lead_pipeline import MISSING_ERRORS, OBJECTION_LABELS, OUTCOME_ERRORS
from nextron.domain.call_outcomes import OutcomeRefusal
from nextron.domain.reply_classes import ObjectionType
from tests.asgi import Reply
from tests.dashboard import FORM
from tests.unit.test_lead_pipeline_page import HOSTILE, book, panel_of
from tests.unit.test_leads_page import (  # noqa: F401 (the fixture)
    T,
    Tree,
    World,
    sync_markets,
    world,
)

S = PipelineStage
HELD = (
    ("outcome", "held"),
    ("qualified", "yes"),
    ("reason", ""),
    ("audit_proposed", "yes"),
    ("audit_price", "2500"),
    ("price_reaction", "accepted"),
    ("objections", "no_budget"),
    ("next_step", "Send the scope"),
    ("next_step_on", "2026-11-25"),
)


def post(world: World, path: str, *pairs: tuple[str, str], token: bool = True) -> Reply:
    return world.dashboard.post(
        path,
        cookie=world.cookie,
        token=world.dashboard.csrf_token(world.cookie) if token else None,
        headers=[(b"content-type", FORM)],
        body=urlencode(pairs).encode("utf-8"),
    )


def record(world: World, lead: str, booking: str, *pairs: tuple[str, str]) -> Reply:
    return post(world, f"/leads/{lead}/outcome", ("booking", booking), *pairs)


def notice(panel: Tree) -> str:
    return panel.find("section", id="lead-pipeline").find("p", class_="notice").text


def area(world: World) -> Reply:
    return world.dashboard.get("/notifications", cookie=world.cookie)


def due_lines(world: World) -> list[tuple[str, str]]:
    reply = area(world)
    assert reply.status == 200
    tree = Tree(reply.text)
    return [
        (link.text, link.attrs["href"])
        for item in tree.find_all("ul", class_="outcome-due")
        for link in item.find_all("a")
    ]


def stored(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT outcome, qualified, reason, audit_price, objections, price_reaction, "
        "next_step, next_step_on, recorded_by FROM call_outcomes ORDER BY rowid"
    )


def counts(world: World) -> tuple[int, int]:
    [(events,)] = world.sql("SELECT COUNT(*) FROM events")
    [(recorded,)] = world.sql("SELECT COUNT(*) FROM call_outcomes WHERE recorded_at IS NOT NULL")
    return int(events), int(recorded)


@pytest.fixture
def lead(world: World) -> str:
    return world.lead("Acme Ltd", "acme.co.uk")


@pytest.fixture
def booking(world: World, lead: str) -> str:
    """A call booked 61 minutes before the dashboard's clock (T, 09:00 UTC)."""
    contact = world.add(lead, ContactCandidate(name="Pat Lee", email="pat@acme.co.uk"))
    sync_markets(world)
    world.stage(lead, S.REPLIED)
    at = T - timedelta(minutes=61)
    book(world, lead, contact, f"{at:%Y-%m-%d}", f"{at:%H:%M}", "UTC")
    [(found,)] = world.sql("SELECT id FROM call_outcomes")
    return str(found)


# The notification area -------------------------------------------------------------------------


def test_the_due_line_shows_then_clears(world: World, lead: str, booking: str) -> None:
    assert due_lines(world) == [("Call Outcome due: Acme Ltd", f"/leads/{lead}#call-outcome")]
    assert area(world).header(b"cache-control") == b"no-store"

    saved = record(world, lead, booking, *HELD)

    # the layout's notification area re-fetches on this event, so the line clears at once
    assert saved.header(b"hx-trigger") == b"call-outcome-saved"
    assert due_lines(world) == []
    assert area(world).text.strip() == ""  # nothing else shows
    layout = world.page(f"/leads/{lead}").find("div", id="notification-list")
    assert layout.attrs["hx-trigger"] == "load, call-outcome-saved from:body"


def test_not_yet_due_or_a_closed_lead_shows_nothing(world: World, lead: str, booking: str) -> None:
    world.sql("UPDATE call_outcomes SET scheduled_at = '2026-11-17T08:01:00.000000Z'")
    assert due_lines(world) == []  # 59 minutes ago

    world.sql("UPDATE call_outcomes SET scheduled_at = '2026-11-17T07:00:00.000000Z'")
    world.stage(lead, S.LOST)
    assert due_lines(world) == []


def test_a_hostile_lead_name_is_escaped_inside_hx_disable(world: World, booking: str) -> None:
    world.sql("UPDATE leads SET name = ?", (HOSTILE,))

    reply = area(world)

    assert "<script>" not in reply.text and "&lt;script&gt;" in reply.text
    assert 'hx-get="/x"' not in reply.text
    assert '<ul class="outcome-due" hx-disable>' in reply.text


# The form --------------------------------------------------------------------------------------


def test_the_form_shows_for_a_past_booking(world: World, lead: str, booking: str) -> None:
    page = world.page(f"/leads/{lead}")

    form = page.find("div", id="call-outcome")
    assert form.find("h3").text == "Call Outcome"
    assert form.find("p", class_="call-line").text == (
        "Call on 2026-11-17 07:59 UTC (2026-11-17 07:59 UTC)"
    )
    posted = form.find("form")
    assert posted.attrs["action"] == posted.attrs["hx-post"] == f"/leads/{lead}/outcome"
    assert posted.attrs["method"] == "post"
    assert posted.find("input", name="booking").attrs["value"] == booking
    assert posted.find("input", name="csrf_token") is not None


def test_every_objection_type_has_a_checkbox_label() -> None:
    """A new ``ObjectionType`` must get a label, or a correction would silently drop it."""
    assert set(OBJECTION_LABELS) == set(ObjectionType)


def test_no_form_before_the_call(world: World, lead: str, booking: str) -> None:
    world.sql("UPDATE call_outcomes SET scheduled_at = '2026-11-17T09:30:00.000000Z'")

    assert 'id="call-outcome"' not in world.get(f"/leads/{lead}").text


def test_held_then_correct_then_nothing_changed(world: World, lead: str, booking: str) -> None:
    panel = panel_of(record(world, lead, booking, *HELD))

    assert notice(panel) == "Call Outcome recorded."
    assert world.sql("SELECT pipeline_stage FROM leads") == [("call_held",)]
    assert stored(world) == [
        (
            "held",
            1,
            None,
            2_500_000_000,
            "no_budget",
            "accepted",
            "Send the scope",
            "2026-11-25",
            "operator",
        )
    ]
    form = panel.find("div", id="call-outcome")
    assert form.find("h3").text == "Correct Call Outcome"
    assert form.find("input", id="outcome-audit_price").attrs["value"] == "2500"
    assert "checked" in form.find("input", value="no_budget").attrs
    assert "checked" not in form.find("input", value="uses_other_tool").attrs

    corrected = dict(HELD) | {"qualified": "no", "reason": "No budget"}
    panel = panel_of(record(world, lead, booking, *corrected.items()))
    assert notice(panel) == (
        "Call Outcome corrected. The stage wasn't changed; move it by hand if needed."
    )
    assert stored(world)[0][1:3] == (0, "No budget")
    assert world.sql("SELECT pipeline_stage FROM leads") == [("call_held",)]

    before = counts(world)
    panel = panel_of(record(world, lead, booking, *corrected.items()))
    assert notice(panel) == "Nothing changed."
    assert counts(world) == before


def test_no_show_keeps_call_booked(world: World, lead: str, booking: str) -> None:
    panel = panel_of(record(world, lead, booking, ("outcome", "no_show")))

    assert notice(panel) == "Call Outcome recorded."
    assert world.sql("SELECT pipeline_stage FROM leads") == [("call_booked",)]
    assert stored(world)[0][0] == "no_show"


def test_a_no_show_stores_the_held_fields_null(world: World, lead: str, booking: str) -> None:
    typed = dict(HELD) | {"outcome": "no_show", "qualified": "no", "reason": "Too small"}

    panel = panel_of(record(world, lead, booking, *typed.items()))

    assert notice(panel) == "Call Outcome recorded."
    assert stored(world) == [
        ("no_show", None, None, None, None, None, "Send the scope", "2026-11-25", "operator")
    ]


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"outcome": ""}, "outcome", MISSING_ERRORS["outcome"]),
        ({"qualified": ""}, "qualified", MISSING_ERRORS["qualified"]),
        ({"audit_proposed": ""}, "audit_proposed", MISSING_ERRORS["audit_proposed"]),
        (
            {"qualified": "no", "reason": "x" * 501},
            "reason",
            OUTCOME_ERRORS[OutcomeRefusal.TOO_LONG],
        ),
        ({"qualified": "no"}, "reason", MISSING_ERRORS["reason"]),
        ({"audit_price": ""}, "audit_price", MISSING_ERRORS["audit_price"]),
        ({"audit_price": "1.5"}, "audit_price", OUTCOME_ERRORS[OutcomeRefusal.BAD_PRICE]),
        ({"next_step_on": "2026-02-30"}, "next_step_on", OUTCOME_ERRORS[OutcomeRefusal.BAD_DATE]),
        ({"next_step": "x" * 501}, "next_step", OUTCOME_ERRORS[OutcomeRefusal.TOO_LONG]),
        ({"price_reaction": ""}, "price_reaction", MISSING_ERRORS["price_reaction"]),
    ],
)
def test_a_refused_field_stores_nothing_and_shows_its_error(
    world: World, lead: str, booking: str, changes: dict[str, str], field: str, message: str
) -> None:
    before = counts(world), stored(world)

    panel = panel_of(record(world, lead, booking, *(dict(HELD) | changes).items()))

    assert notice(panel) == "The Call Outcome wasn't saved."
    error = panel.find("p", id=f"outcome-{field}-error")
    assert error.text == message
    assert (counts(world), stored(world)) == before
    assert panel.find("input", id="outcome-audit_price").attrs["value"] == (
        (dict(HELD) | changes)["audit_price"]
    )
    assert panel.find("input", name="booking").attrs["value"] == booking
    assert record(world, lead, booking, *(dict(HELD) | changes).items()).header(
        b"hx-trigger"
    ) is None


def test_a_refused_correction_keeps_its_heading_and_typed_values(
    world: World, lead: str, booking: str
) -> None:
    record(world, lead, booking, *HELD)
    before = counts(world), stored(world)
    typed = dict(HELD) | {"audit_price": "1.5", "next_step": "Call back"}

    panel = panel_of(record(world, lead, booking, *typed.items()))

    assert notice(panel) == "The Call Outcome wasn't saved."
    form = panel.find("div", id="call-outcome")
    assert form.find("h3").text == "Correct Call Outcome"
    assert form.find("input", id="outcome-audit_price").attrs["value"] == "1.5"
    assert form.find("textarea", id="outcome-next_step").text.strip() == "Call back"
    assert form.find("input", name="booking").attrs["value"] == booking
    assert (counts(world), stored(world)) == before


def test_a_due_rebooking_takes_the_form_and_a_stale_refusal_keeps_its_booking(
    world: World, lead: str, booking: str
) -> None:
    record(world, lead, booking, ("outcome", "no_show"))  # the Lead stays at call_booked
    [(contact,)] = world.sql("SELECT id FROM contacts")
    at = T - timedelta(minutes=10)
    book(world, lead, str(contact), f"{at:%Y-%m-%d}", f"{at:%H:%M}", "UTC")
    [(rebooked,)] = world.sql("SELECT id FROM call_outcomes WHERE recorded_at IS NULL")

    form = world.page(f"/leads/{lead}").find("div", id="call-outcome")
    assert form.find("h3").text == "Call Outcome"
    assert form.find("input", name="booking").attrs["value"] == rebooked

    # a stale tab still correcting the first booking: refused, it keeps the posted booking
    panel = panel_of(record(world, lead, booking, ("outcome", "held")))
    assert panel.find("input", name="booking").attrs["value"] == booking


def test_hostile_text_is_kept_escaped_inside_hx_disable(
    world: World, lead: str, booking: str
) -> None:
    typed = dict(HELD) | {"next_step_on": "bad", "next_step": HOSTILE}
    reply = record(world, lead, booking, *typed.items())

    assert "<script>" not in reply.text and "&lt;script&gt;" in reply.text
    area_ = panel_of(reply).find("textarea", id="outcome-next_step")
    assert any("hx-disable" in node.attrs for node in area_.ancestors())

    record(world, lead, booking, *(dict(HELD) | {"next_step": HOSTILE}).items())
    page = world.get(f"/leads/{lead}")
    assert "<script>" not in page.text and "&lt;script&gt;" in page.text


# Refused requests ------------------------------------------------------------------------------


def test_a_form_without_a_ulid_booking_is_400(world: World, lead: str, booking: str) -> None:
    for pairs in ((("outcome", "held"),), (("booking", "x"), ("outcome", "held"))):
        reply = post(world, f"/leads/{lead}/outcome", *pairs)
        assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert stored(world)[0][0] is None


@pytest.mark.parametrize(
    "pairs",
    [
        (("outcome", "maybe"),),
        (("qualified", "perhaps"),),
        (("price_reaction", "loved_it"),),
        (("objections", "too_busy"),),
        (("objections", "no_budget"), ("objections", "no_budget")),
        (("outcome", "held"), ("outcome", "no_show")),
        (("note", "x"),),
    ],
)
def test_a_malformed_form_is_400(
    world: World, lead: str, booking: str, pairs: tuple[tuple[str, str], ...]
) -> None:
    before = counts(world), stored(world)
    reply = record(world, lead, booking, *pairs)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert (counts(world), stored(world)) == before


def test_another_leads_or_a_superseded_booking_is_400(
    world: World, lead: str, booking: str
) -> None:
    other = world.lead("Other Ltd", "other.co.uk", minutes=1)
    before = counts(world), stored(world)

    reply = record(world, other, booking, *HELD)
    assert (reply.status, reply.text) == (400, MALFORMED_FORM)

    reply = record(world, other, booking, ("outcome", "held"))  # incomplete: still 400
    assert (reply.status, reply.text) == (400, MALFORMED_FORM)

    world.sql("UPDATE call_outcomes SET superseded_at = '2026-11-17T08:30:00.000000Z'")
    for pairs in (HELD, (("outcome", "held"),)):
        reply = record(world, lead, booking, *pairs)
        assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert (counts(world), stored(world)) == before


def test_an_unknown_or_merged_lead_is_404(world: World, lead: str, booking: str) -> None:
    newer = world.lead("Newer", "newer.co.uk", minutes=1)
    world.merge(lead, newer)

    for target in ("01JD3QZ8X5V9K2M7N4P6R8T0AC", "not-a-ulid", newer):
        reply = record(world, target, booking, *HELD)
        assert (reply.status, reply.text) == (404, "That Lead doesn't exist.")


def test_a_post_without_its_token_is_refused(world: World, lead: str, booking: str) -> None:
    reply = post(world, f"/leads/{lead}/outcome", ("booking", booking), *HELD, token=False)

    assert reply.status == 403
    assert stored(world)[0][0] is None


def test_the_route_declares_personal_data(world: World) -> None:
    [route] = [
        route
        for route in world.dashboard.app.routes
        if getattr(route, "path", "") == "/leads/{lead_id}/outcome"
    ]
    assert getattr(route, "methods") == {"POST"}
    assert getattr(getattr(route, "endpoint"), "__nextron_personal_data__") is True
