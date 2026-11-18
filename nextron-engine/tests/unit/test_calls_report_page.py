"""Story 9.6's Calls report page (FR-69, NFR-49, NFR-50, AD-13) through ``Dashboard(units=...)``
on a temporary database at head: early (the "n of 10" line, no figures), the matrix fixture's
exact figures and checkpoint, the checkpoint gone at 2 accepted audits, the "engine-sourced"
label, ``no-store``, ``@personal_data(False)``, the Pipeline board's link, no navigation
section, no free text or names (an erased Contact's ``[erased]`` texts included), no write, and
the money format."""

from datetime import timedelta
from typing import Any

import pytest

from nextron.app.pipeline.api import OPERATOR, Pipeline
from nextron.domain.call_outcomes import CallResult, Outcome, PriceReaction
from nextron.domain.contacts import ContactCandidate
from nextron.domain.offer_validation import CHECKPOINT
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.reply_classes import ObjectionType
from nextron.web.calls_report_page import money, price_range, share
from tests.unit.test_leads_page import (  # noqa: F401 (the fixture)
    T,
    Tree,
    World,
    table_rows,
    world,
)

S = PipelineStage
O = ObjectionType
R = PriceReaction
DOLLAR = 1_000_000
PATH = "/reports/calls"
NO_SHOW = Outcome(outcome=CallResult.NO_SHOW)
SECRET_REASON = "Partner Zed said too small"
SECRET_STEP = "Phone Quentin Xavier"


def held(
    *,
    qualified: bool = True,
    price: int | None = None,
    objections: tuple[ObjectionType, ...] = (),
    reaction: PriceReaction = R.NOT_DISCUSSED,
) -> Outcome:
    return Outcome(
        outcome=CallResult.HELD,
        qualified=qualified,
        reason=None if qualified else SECRET_REASON,
        audit_proposed=price is not None,
        audit_price=None if price is None else price * DOLLAR,
        price_reaction=reaction,
        objections=tuple(sorted(objections)),
        next_step=SECRET_STEP,
    )


class Calls:
    def __init__(self, world: World) -> None:
        self.world = world
        self.pipeline = Pipeline(world.clock)
        self.count = 0

    def booked(self, outcome: Outcome | None = None) -> str:
        self.count += 1
        domain = f"acme{self.count}.co.uk"
        lead = self.world.lead(f"Acme Number {self.count} Ltd", domain)
        contact = self.world.add(lead, ContactCandidate(name="Pat Lee", email=f"pat@{domain}"))
        self.world.stage(lead, S.REPLIED)
        at = T - timedelta(minutes=61)
        booking = self.world.transact(
            lambda: self.pipeline.book_call(lead, contact, at, "UTC", actor=OPERATOR)
        )
        if outcome is not None:
            self.world.transact(
                lambda: self.pipeline.record_call_outcome(booking, outcome, actor=OPERATOR)
            )
        return lead

    def win_audit(self, lead: str) -> None:
        self.world.transact(lambda: self.pipeline.move(lead, S.AUDIT_WON, actor=OPERATOR))


def report(world: World) -> Tree:
    reply = world.get(PATH)
    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    return Tree(reply.text)


def definitions(page: Tree, css: str) -> list[tuple[str, str]]:
    found = page.find("dl", class_=css)
    return list(zip(
        [each.text for each in found.find_all("dt")],
        [each.text for each in found.find_all("dd")],
        strict=True,
    ))


def fixture(world: World) -> list[str]:
    calls = Calls(world)
    objections = [
        (O.NO_BUDGET, O.USES_OTHER_TOOL),
        (O.NO_BUDGET, O.USES_OTHER_TOOL),
        (O.NO_BUDGET, O.SEND_INFORMATION),
        (O.NO_BUDGET,),
        (O.USES_OTHER_TOOL, O.SEND_INFORMATION),
    ] + [()] * 7
    prices = [2_000, 2_500, 2_500, 3_000, 2_500] + [None] * 7
    reactions = [R.ACCEPTED, R.PUSHED_BACK, R.PUSHED_BACK] + [R.NOT_DISCUSSED] * 9
    leads = [
        calls.booked(
            held(
                qualified=index < 8,
                price=prices[index],
                objections=objections[index],
                reaction=reactions[index],
            )
        )
        for index in range(12)
    ]
    calls.win_audit(leads[0])
    return leads


def test_early(world: World) -> None:
    calls = Calls(world)
    for _ in range(6):
        calls.booked(held())
    calls.booked(NO_SHOW)
    calls.booked()

    page = report(world)

    assert page.find("title").text == "Nextron: Calls report"
    assert page.find("h1").text == "Calls report"
    assert page.find("p", class_="report-scope").text == "Counts engine-sourced Leads only."
    assert definitions(page, "call-counts") == [("Booked", "8"), ("Held", "6"), ("No-shows", "1")]
    assert page.find("p", class_="offer-progress").text == (
        "Offer validation starts at 10 held calls: 6 of 10 recorded"
    )
    assert not page.find_all("dl", class_="offer-figures")
    assert CHECKPOINT not in page.text


def test_empty(world: World) -> None:
    page = report(world)

    assert definitions(page, "call-counts") == [("Booked", "0"), ("Held", "0"), ("No-shows", "0")]
    assert page.find("p", class_="offer-progress").text.endswith("0 of 10 recorded")


def test_the_fixture(world: World) -> None:
    fixture(world)

    page = report(world)

    assert definitions(page, "call-counts") == [("Booked", "12"), ("Held", "12"), ("No-shows", "0")]
    assert not page.find_all("p", class_="offer-progress")
    assert definitions(page, "offer-figures") == [
        ("Audits proposed", "5 of 12 (42%)"),
        ("Audits accepted", "1"),
        ("Audit price range", "$2,000–$3,000"),
        ("Median audit price", "$2,500"),
        ("Qualified", "8 of 12 (67%)"),
    ]
    assert table_rows(page.find("table", class_="price-reactions")) == [
        ["Reaction", "Held calls"],
        ["Accepted", "1"],
        ["Pushed back", "2"],
        ["Not discussed", "9"],
    ]
    assert table_rows(page.find("table", class_="top-objections")) == [
        ["Objection", "Held calls"],
        ["No budget", "4"],
        ["We already use another tool", "3"],
        ["Send me some information", "2"],
    ]
    checkpoint = page.find("p", class_="offer-checkpoint")
    assert checkpoint.text == "Review the Offer's price and scope before the next Variant"
    assert checkpoint.attrs["role"] == "status"
    assert "notice-refused" not in checkpoint.attrs["class"]


def test_two_accepted_audits_hide_the_checkpoint(world: World) -> None:
    leads = fixture(world)
    Calls(world).win_audit(leads[1])

    page = report(world)

    assert ("Audits accepted", "2") in definitions(page, "offer-figures")
    assert not page.find_all("p", class_="offer-checkpoint")
    assert CHECKPOINT not in page.text


def test_no_proposals_and_no_objections(world: World) -> None:
    calls = Calls(world)
    for _ in range(10):
        calls.booked(held())

    page = report(world)

    figures = dict(definitions(page, "offer-figures"))
    assert figures["Audit price range"] == figures["Median audit price"] == "—"
    assert figures["Audits proposed"] == "0 of 10 (0%)"
    assert page.find("p", class_="no-objections").text == "No objections recorded"


def every_table(world: World) -> dict[str, list[Any]]:
    names = [name for (name,) in world.sql("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {name: world.sql(f'SELECT * FROM "{name}" ORDER BY rowid') for name in names}


def test_no_free_text_or_names_and_no_write(world: World) -> None:
    calls = Calls(world)
    for _ in range(10):
        calls.booked(held(qualified=False))
    # as erasure leaves them (Story 9.4)
    world.sql("UPDATE call_outcomes SET reason = '[erased]' WHERE rowid % 2 = 0")
    before = every_table(world)

    reply = world.get(PATH)

    assert reply.status == 200
    for text in ("[erased]", SECRET_REASON, SECRET_STEP, "Acme Number", "Pat Lee", "acme1"):
        assert text not in reply.text
    assert every_table(world) == before


def test_the_route_declares_no_personal_data_and_is_no_navigation_section(world: World) -> None:
    [route] = [
        route for route in world.dashboard.app.routes if getattr(route, "path", "") == PATH
    ]
    assert getattr(route, "methods") == {"GET"}
    assert getattr(getattr(route, "endpoint"), "__nextron_personal_data__") is False
    page = report(world)
    assert not page.find("nav").find_all("a", href=PATH)


def test_the_pipeline_board_links_to_it(world: World) -> None:
    page = Tree(world.get("/leads/pipeline").text)

    assert page.find("main").find("a", href=PATH).text == "Calls report"


@pytest.mark.parametrize(
    ("micros", "expected"),
    [
        (None, "—"),
        (2_500 * DOLLAR, "$2,500"),
        (2_500_500_000, "$2,500.50"),
        (1_234_567 * DOLLAR + 500_000, "$1,234,567.50"),
        (0, "$0"),
    ],
)
def test_money(micros: int | None, expected: str) -> None:
    assert money(micros) == expected


def test_share_and_range() -> None:
    assert share(5, 12, 42) == "5 of 12 (42%)"
    assert price_range(None, None) == "—"
    assert price_range(2_000 * DOLLAR, 2_000 * DOLLAR) == "$2,000–$2,000"
