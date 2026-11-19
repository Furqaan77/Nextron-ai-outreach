"""A Lead's History tab (Story 9.8): ``GET /leads/{lead_id}/history`` through
``Dashboard(units=...)`` on a temporary database at head, with Leads, Contacts, bookings and
suppression entries written by the real services. Covers the tab bar on both pages, the
timeline's rows, the filters (links keep ``type``), paging, the merged redirect, an erased
Contact's "[erased]", ``no-store``, ``hx-disable``, an autoescaped ``<script>`` quote, the
malformed 400, an unknown Lead, no hash, ``domain_plain`` or email in the body, the route's
rules and the view module's imports."""

import ast
from datetime import timedelta

import pytest

from nextron.app.compliance.erasure import Erasures
from nextron.app.pipeline.api import OPERATOR
from nextron.db.models.base import new_id, utc_text
from nextron.domain.call_outcomes import CallResult, Outcome, PriceReaction
from nextron.domain.contactability import SuppressionScope
from nextron.web.forms import MALFORMED_FORM
from tests.app.test_call_outcomes import book
from tests.app.test_erasure import queue_on
from tests.app.test_lead_history import evidence, later
from tests.app.test_pipeline_manual import run
from tests.app.test_suppression import LATER, World, world  # noqa: F401 (the fixture)
from tests.asgi import Reply
from tests.contracts.support import PACKAGE
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Node, Tree, table_rows

HEADER = ["When", "Type", "Change", "Cause", "By", "Contact", "Detail"]
EMAIL = "pat.lee@firm.co.uk"
HELD = Outcome(
    outcome=CallResult.HELD,
    qualified=True,
    audit_proposed=False,
    price_reaction=PriceReaction.NOT_DISCUSSED,
    objections=(),
)


@pytest.fixture
def site(world: World) -> tuple[Dashboard, bytes]:
    dashboard = Dashboard(units=world.units, clock=FrozenClock(LATER + timedelta(days=1)))
    return dashboard, dashboard.open_session()


def get(site: tuple[Dashboard, bytes], path: str, query: str = "") -> Reply:
    dashboard, cookie = site
    return dashboard.get(path, cookie=cookie, query=query.encode())


def page(site: tuple[Dashboard, bytes], lead: str, query: str = "") -> Tree:
    reply = get(site, f"/leads/{lead}/history", query)
    assert reply.status == 200, reply
    assert reply.header(b"cache-control") == b"no-store"
    return Tree(reply.text)


def rows(tree: Tree) -> list[list[str]]:
    return table_rows(tree.find("table", class_="history-table"))


def links(node: Node) -> dict[str, str]:
    return {a.text: a.attrs["href"] or "" for a in node.find_all("a")}


def test_the_tab_bar_links_overview_and_history_on_both_pages(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()

    overview = Tree(get(site, f"/leads/{lead}").text)
    history = page(site, lead, "type=calls")

    tabs = overview.find("nav", class_="lead-tabs")
    assert links(tabs) == {"Overview": f"/leads/{lead}", "History": f"/leads/{lead}/history"}
    assert tabs.find("a", href=f"/leads/{lead}").attrs["aria-current"] == "page"
    tabs = history.find("nav", class_="lead-tabs")
    assert links(tabs) == {
        "Overview": f"/leads/{lead}",
        "History": f"/leads/{lead}/history?type=calls",  # keeps the type
    }
    assert tabs.find("a", href=f"/leads/{lead}/history?type=calls").attrs["aria-current"] == "page"


def test_the_timeline_shows_each_entry_with_its_change_cause_actor_and_links(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()
    later(world, 1)
    contact = world.contact(lead, name="Pat Lee", email=EMAIL)
    evidence(world, lead, "We hired a new partner.", later(world, 2))
    later(world, 3)
    booking = book(world, lead, contact)
    later(world, 4)
    run(world, lambda p: p.record_call_outcome(booking, HELD, actor=OPERATOR))
    later(world, 5)
    world.suppress(contact, scope=SuppressionScope.FIRM)

    tree = page(site, lead)
    found = rows(tree)
    body = get(site, f"/leads/{lead}/history").text

    assert found[0] == HEADER
    created = ["2026-11-18 10:00 UTC", "Lead", "New", "Created from csv", "Engine (pipeline)"]
    assert [*created, "", ""] in found
    added = ["2026-11-18 10:01 UTC", "Contact added", "Added", "CSV import", "Operator"]
    assert [*added, "Pat Lee", "Contacts"] in found
    assert [
        "2026-11-18 10:03 UTC", "Lead", "Replied → Call booked", "Operator move", "Operator", "", ""
    ] in found
    evidence_row = [row for row in found if row[1] == "Evidence added"]
    assert evidence_row == [
        [
            "2026-11-18 10:02 UTC",
            "Evidence added",
            "Live",
            "Website",
            "Engine (rule)",
            "",
            "We hired a new partner. Source",
        ]
    ]
    assert tree.find("a", href="https://firm.co.uk/news").text == "Source"
    calls = [row for row in found if row[1] == "Call"]
    assert calls and all(row[6] == "Pipeline" for row in calls)
    assert tree.find_all("a", href=f"/leads/{lead}#lead-pipeline")
    assert tree.find_all("a", href=f"/leads/{lead}#lead-contacts")
    suppressed = [row for row in found if row[1] == "Suppression entry"]
    details = {row[6] for row in suppressed}
    assert details >= {"Address key, source: Reply rule", "Domain key, source: Reply rule"}
    stamps = [row[0] for row in found[1:]]
    assert stamps == sorted(stamps, reverse=True)
    assert tree.find("p", class_="history-count").text == f"{len(found) - 1} entries"
    # never a hash, a plain domain key value or an address
    [(value_hash, domain_plain)] = world.sql(
        "SELECT value_hash, domain_plain FROM suppression_entries WHERE kind = 'domain'"
    )
    assert value_hash not in body and EMAIL not in body
    assert domain_plain == "firm.co.uk"
    assert "firm.co.uk" not in body.replace("https://firm.co.uk/news", "")


def test_a_filter_keeps_its_category_and_marks_its_link(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email=EMAIL)
    booking = book(world, lead, contact)
    run(world, lambda p: p.record_call_outcome(booking, HELD, actor=OPERATOR))

    tree = page(site, lead, "type=calls")
    empty = page(site, lead, "type=drafts")

    assert {row[1] for row in rows(tree)[1:]} == {"Call"}
    filters = tree.find("nav", class_="history-filters")
    assert links(filters) == {
        "All": f"/leads/{lead}/history",
        "Messages": f"/leads/{lead}/history?type=messages",
        "Drafts": f"/leads/{lead}/history?type=drafts",
        "Stage changes": f"/leads/{lead}/history?type=stages",
        "Evidence and Signals": f"/leads/{lead}/history?type=evidence",
        "Calls": f"/leads/{lead}/history?type=calls",
    }
    [current] = [a for a in filters.find_all("a") if a.attrs.get("aria-current") == "true"]
    assert current.text == "Calls"
    assert empty.find("p", class_="history-empty").text == "No history entries of this type."
    assert not empty.find_all("table")


def test_paging_keeps_the_type_and_past_the_end_shows_the_last(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()
    for second in range(229):  # plus the creation row: 230 stage changes
        world.sql(
            "INSERT INTO events (id, entity, entity_id, from_status, to_status, cause, "
            "cause_ref, actor, at) VALUES (?, 'leads', ?, 'new', 'new', 'deal_details_saved', "
            "NULL, 'operator', ?)",
            (new_id(), lead, utc_text(LATER + timedelta(seconds=second + 1))),
        )

    third = page(site, lead, "type=stages&page=3")
    past = page(site, lead, "type=stages&page=9")

    assert len(rows(third)) == 1 + 30
    pager = third.find("nav", class_="pager")
    assert "Page 3 of 3" in pager.text
    assert links(pager) == {"Previous": f"/leads/{lead}/history?type=stages&page=2"}
    assert rows(past) == rows(third)
    first = page(site, lead, "type=stages")
    assert links(first.find("nav", class_="pager")) == {
        "Next": f"/leads/{lead}/history?type=stages&page=2"
    }


def test_a_merged_lead_redirects_to_its_survivors_history(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    survivor = world.lead()
    later(world, 1)
    loser = world.lead("other.co.uk")
    world.transact(lambda: world.discovery.merge(survivor, loser))

    reply = get(site, f"/leads/{loser}/history", "type=stages")
    tree = page(site, survivor)

    assert reply.status == 303
    assert reply.header(b"location") == f"/leads/{survivor}/history?type=stages".encode()
    assert reply.header(b"cache-control") == b"no-store"
    created = [row for row in rows(tree) if row[1:3] == ["Lead", "New"]]
    assert len(created) == 2  # the survivor's and the merged Lead's


def test_an_erased_contact_shows_erased(world: World, site: tuple[Dashboard, bytes]) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email=EMAIL)
    evidence(world, lead, "Pat Lee leads the team.", later(world, 1), person=contact)
    with world.units.open():
        Erasures(world.clock, world.compliance, queue=queue_on(world.clock)).erase_on_request(
            contact, "DSR-7"
        )

    tree = page(site, lead)
    body = get(site, f"/leads/{lead}/history").text

    [added] = [row for row in rows(tree) if row[1] == "Contact added"]
    assert added[5] == "[erased]" and added[0] == "2026-11-18 10:00 UTC"
    [quoted] = [row for row in rows(tree) if row[1] == "Evidence added"]
    assert quoted[5] == "[erased]" and quoted[6].startswith("[erased]")
    assert "Pat" not in body and EMAIL not in body


def test_free_text_is_autoescaped_inside_hx_disable(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()
    evidence(
        world,
        lead,
        '<script>alert(1)</script><a hx-post="/x">go</a>',
        later(world, 1),
        source_url="javascript:alert(1)",
    )

    reply = get(site, f"/leads/{lead}/history")
    tree = Tree(reply.text)

    assert "<script>alert(1)</script>" not in reply.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in reply.text
    assert tree.disabled("<script>alert(1)</script>") == [True]
    assert "javascript:" not in reply.text
    assert "|safe" not in (PACKAGE / "web" / "templates" / "lead_history.html").read_text("utf-8")


@pytest.mark.parametrize(
    "query",
    [
        "bogus=1",
        "type=calls&type=calls",
        "type=nope",
        "page=0",
        "page=-1",
        "page=x",
        "page=1&page=2",
        "page=12345678",
    ],
)
def test_a_bad_query_is_refused(
    world: World, site: tuple[Dashboard, bytes], query: str
) -> None:
    lead = world.lead()

    reply = get(site, f"/leads/{lead}/history", query)

    assert reply.status == 400
    assert reply.text == MALFORMED_FORM
    assert reply.header(b"cache-control") == b"no-store"


def test_blank_parameters_are_the_defaults(world: World, site: tuple[Dashboard, bytes]) -> None:
    lead = world.lead()

    tree = page(site, lead, "type=&page=")

    assert tree.find("p", class_="history-count").text == "1 entry"


def test_padded_parameters_are_read_stripped(world: World, site: tuple[Dashboard, bytes]) -> None:
    lead = world.lead()

    tree = page(site, lead, "type=stages%20&page=%201")

    filters = tree.find("nav", class_="history-filters")
    [current] = [a for a in filters.find_all("a") if a.attrs.get("aria-current") == "true"]
    assert current.text == "Stage changes"
    assert tree.find("p", class_="history-count").text == "1 entry"


def test_an_ai_actor_shows_as_ai_and_its_route(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()
    evidence(world, lead, "We hired a new partner.", later(world, 1), extracted_by="ai:extract@3")

    tree = page(site, lead)

    [added] = [row for row in rows(tree) if row[1] == "Evidence added"]
    assert added[4] == "AI (extract)"


@pytest.mark.parametrize("lead", ["nope", "01JD3QZ8X5V9K2M7N4P6R8T0AB"])
def test_an_unknown_lead_is_the_missing_page_never_echoing_the_id(
    world: World, site: tuple[Dashboard, bytes], lead: str
) -> None:
    reply = get(site, f"/leads/{lead}/history")

    assert reply.status == 404
    assert "Lead not found" in reply.text
    assert lead not in reply.text
    assert reply.header(b"cache-control") == b"no-store"


def test_a_render_writes_nothing(world: World, site: tuple[Dashboard, bytes]) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email=EMAIL)
    world.suppress(contact)
    before = world.sql("SELECT count(*) FROM events") + world.sql(
        "SELECT count(*) FROM suppression_entries"
    )

    page(site, lead)

    after = world.sql("SELECT count(*) FROM events") + world.sql(
        "SELECT count(*) FROM suppression_entries"
    )
    assert after == before


def test_the_route_is_a_get_with_personal_data_and_locked_without_a_session(
    world: World, site: tuple[Dashboard, bytes]
) -> None:
    lead = world.lead()
    dashboard, _ = site
    routes = {getattr(r, "name", None): r for r in dashboard.app.routes}
    route = routes["lead_history_page"]

    assert getattr(route, "path") == "/leads/{lead_id}/history"
    assert getattr(route, "methods") == {"GET"}
    assert getattr(getattr(route, "endpoint"), "__nextron_personal_data__") is True
    reply = dashboard.get(f"/leads/{lead}/history")
    assert reply.status == 401
    assert "Firm LLP" not in reply.text


def test_the_view_module_reads_only_through_queries_and_the_history_analytics() -> None:
    tree = ast.parse((PACKAGE / "web" / "history_page.py").read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    modules += [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    ]

    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert app_modules == ["nextron.app.analytics.history"]
    assert not [name for name in modules if name.startswith("nextron.db")]
