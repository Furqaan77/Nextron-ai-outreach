"""The Pipeline board (Story 9.7; FR-46, FR-45, UX-DR18, AD-3, AD-13): ``queries.pipeline.board``
and ``GET /leads/pipeline`` through ``Dashboard(units=...)`` on a temporary database at head with
the shipped Segments and Markets. Leads, Contacts and ``events`` rows are written raw, so each
test sets exactly the stage, Market and entry time it needs. Covers the grouping in machine order
(empty groups included), merged Leads left out, each filter, paging per group, inclusive date
bounds, the I/O matrix's refusals, ``no-store``, the link from the Leads list, the queries-only
import rule and a 5,000-Lead board rendered in under a second."""

import ast
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id, utc_text
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contact_market import MarketBasis, TzBasis
from nextron.domain.leads import LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from nextron.web import pipeline_page
from nextron.web.forms import MALFORMED_FORM
from nextron.web.leads_page import STAGE_LABELS
from nextron.web.pipeline_page import RANGE_REFUSED
from tests.asgi import Reply
from tests.contracts.support import PACKAGE
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Node, Tree, table_rows

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
HEADER = ["Name", "Segment", "In stage since"]
NOT_NOW_HEADER = [*HEADER, "Re-contact"]


@dataclass
class World:
    """The dashboard on real units of work; Leads, Contacts and events are written raw."""

    dashboard: Dashboard
    units: UnitOfWorkFactory
    engine: Engine
    cookie: bytes
    law: str
    ops: str
    markets: dict[str, str]  # code -> id

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def lead(
        self,
        name: str,
        *,
        stage: PipelineStage = PipelineStage.NEW,
        minutes: int = 0,
        segment: str | None = None,
        merged_into: str | None = None,
    ) -> str:
        lead_id = new_id()
        self.sql(
            "INSERT INTO leads (id, name, place_id, segment_id, merged_into, created_at, "
            "pipeline_stage) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                lead_id,
                name,
                None if merged_into else f"place-{lead_id}",
                segment or self.law,
                merged_into,
                utc_text(T + timedelta(minutes=minutes)),
                str(stage),
            ),
        )
        return lead_id

    def entered(self, lead_id: str, stage: PipelineStage, at: datetime) -> None:
        """An ``events`` row moving the Lead to ``stage`` at ``at``."""
        self.sql(
            "INSERT INTO events (id, entity, entity_id, from_status, to_status, cause, "
            "cause_ref, actor, at) VALUES (?, 'leads', ?, NULL, ?, 'operator_move', NULL, "
            "'system:pipeline', ?)",
            (new_id(), lead_id, str(stage), utc_text(at)),
        )

    def contact(self, lead_id: str, market: str | None) -> None:
        market_id = None if market is None else self.markets[market]
        self.sql(
            "INSERT INTO contacts (id, lead_id, name, market_id, tz, market_basis, tz_basis, "
            "created_at, updated_at) VALUES (?, ?, 'Pat', ?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                lead_id,
                market_id,
                None if market is None else "Europe/London",
                str(MarketBasis.LEAD_COUNTRY),
                str(TzBasis.MARKET_DEFAULT),
                utc_text(T),
                utc_text(T),
            ),
        )

    def board(self, **filters: Any) -> pipeline_queries.PipelineBoard:
        with self.units.open(read_only=True):
            return pipeline_queries.board(**filters)

    def get(self, query: str = "") -> Reply:
        return self.dashboard.get("/leads/pipeline", cookie=self.cookie, query=query.encode())

    def page(self, query: str = "") -> Tree:
        reply = self.get(query)
        assert reply.status == 200, reply
        assert reply.header(b"cache-control") == b"no-store"
        return Tree(reply.text)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    markets = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).sync_markets(markets)
    dashboard = Dashboard(units=units, clock=FrozenClock(T))
    cookie = dashboard.open_session()
    found = World(dashboard, units, db_engine, cookie, "", "", {})
    [(law,), (ops,)] = found.sql("SELECT id FROM segments ORDER BY key")
    found.law, found.ops = law, ops
    found.markets = dict(found.sql("SELECT code, id FROM markets"))
    return found


def group(board: pipeline_queries.PipelineBoard, stage: PipelineStage) -> Any:
    [found] = [each for each in board.groups if each.stage is stage]
    return found


def names(board: pipeline_queries.PipelineBoard, stage: PipelineStage) -> list[str]:
    return [lead.name for lead in group(board, stage).leads]


def sections(page: Tree) -> list[Node]:
    return page.find_all("section", class_="stage-group")


def section(page: Tree, stage: PipelineStage) -> Node:
    return page.find("section", id=f"stage-{stage}")


def summary(node: Node) -> str:
    return node.find("p", class_="stage-summary").text


# The query --------------------------------------------------------------------------------------


def test_every_stage_has_a_group_in_machine_order_without_merged_leads(world: World) -> None:
    old = world.lead("Old", minutes=0)
    world.lead("New", minutes=5)
    world.lead("Merged", merged_into=old)
    booked = world.lead("Booked", stage=PipelineStage.CALL_BOOKED)
    world.entered(booked, PipelineStage.CALL_BOOKED, T + timedelta(days=1))
    world.entered(booked, PipelineStage.CALL_BOOKED, T + timedelta(days=2))  # the latest wins
    world.entered(booked, PipelineStage.REPLIED, T + timedelta(days=3))  # another stage

    board = world.board()

    assert [each.stage for each in board.groups] == list(PipelineStage)
    assert names(board, PipelineStage.NEW) == ["New", "Old"]  # newest first
    assert group(board, PipelineStage.NEW).total == 2
    [lead] = group(board, PipelineStage.CALL_BOOKED).leads
    assert lead.entered_at == T + timedelta(days=2)
    assert group(board, PipelineStage.NEW).leads[0].entered_at is None
    for empty in (PipelineStage.WON, PipelineStage.NOT_NOW, PipelineStage.LOST):
        found = group(board, empty)
        assert (found.total, found.page, found.pages, found.leads) == (0, 1, 1, ())
    assert all(each.deal_value is None for each in board.groups)


def test_a_tie_on_created_at_orders_by_id_descending(world: World) -> None:
    ids = sorted(world.lead(f"Tie {n}") for n in range(3))

    board = world.board()

    assert [lead.id for lead in group(board, PipelineStage.NEW).leads] == ids[::-1]


def test_a_group_sorts_by_lead_score_then_unscored_newest_first(world: World) -> None:
    low = world.lead("Low", minutes=9)
    high = world.lead("High", minutes=0)
    world.lead("Unscored old", minutes=1)
    world.lead("Unscored new", minutes=8)
    tie_old, tie_new = world.lead("Tie old", minutes=2), world.lead("Tie new", minutes=3)
    for lead_id, score in ((low, 10), (high, 90), (tie_old, 50), (tie_new, 50)):
        world.sql("UPDATE leads SET lead_score = ? WHERE id = ?", (score, lead_id))

    board = world.board()

    assert names(board, PipelineStage.NEW) == [
        "High",
        "Tie new",
        "Tie old",
        "Low",
        "Unscored new",
        "Unscored old",
    ]


def test_the_segment_and_market_filters(world: World) -> None:
    law_uk = world.lead("Law UK")
    world.contact(law_uk, "uk")
    world.contact(law_uk, "us")  # one Contact in the Market is enough
    law_us = world.lead("Law US")
    world.contact(law_us, "us")
    ops_uk = world.lead("Ops UK", segment=world.ops)
    world.contact(ops_uk, "uk")
    world.lead("No contact")
    no_market = world.lead("No market")
    world.contact(no_market, None)

    assert names(world.board(segment_id=world.ops), PipelineStage.NEW) == ["Ops UK"]
    assert names(world.board(market_id=world.markets["uk"]), PipelineStage.NEW) == [
        "Ops UK",
        "Law UK",
    ]
    both = world.board(segment_id=world.law, market_id=world.markets["uk"])
    assert names(both, PipelineStage.NEW) == ["Law UK"]
    assert group(both, PipelineStage.NEW).total == 1


def test_the_date_bounds_are_inclusive_utc_days(world: World) -> None:
    for label, at in [
        ("before", datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=UTC)),
        ("first", datetime(2026, 11, 1, 0, 0, tzinfo=UTC)),
        ("last", datetime(2026, 11, 10, 23, 59, 59, 999999, tzinfo=UTC)),
        ("after", datetime(2026, 11, 11, 0, 0, tzinfo=UTC)),
    ]:
        lead = world.lead(label, stage=PipelineStage.CONTACTED)
        world.entered(lead, PipelineStage.CONTACTED, at)
    world.lead("never entered", stage=PipelineStage.CONTACTED)

    ranged = world.board(entered_from=date(2026, 11, 1), entered_to=date(2026, 11, 10))
    assert sorted(names(ranged, PipelineStage.CONTACTED)) == ["first", "last"]
    since = world.board(entered_from=date(2026, 11, 1))
    assert sorted(names(since, PipelineStage.CONTACTED)) == ["after", "first", "last"]
    until = world.board(entered_to=date(2026, 11, 10))
    assert sorted(names(until, PipelineStage.CONTACTED)) == ["before", "first", "last"]
    one_day = world.board(entered_from=date(2026, 11, 11), entered_to=date(2026, 11, 11))
    assert names(one_day, PipelineStage.CONTACTED) == ["after"]
    assert len(names(world.board(), PipelineStage.CONTACTED)) == 5


def test_each_group_pages_on_its_own_and_past_the_end_shows_the_last(world: World) -> None:
    for n in range(120):
        world.lead(f"New {n:03}", minutes=n)
    world.lead("Lost", stage=PipelineStage.LOST)

    board = world.board(pages={PipelineStage.NEW: 2})
    new = group(board, PipelineStage.NEW)
    assert (new.total, new.page, new.pages) == (120, 2, 3)
    assert names(board, PipelineStage.NEW) == [f"New {n:03}" for n in range(69, 19, -1)]
    assert names(board, PipelineStage.LOST) == ["Lost"]

    last = group(world.board(pages={PipelineStage.NEW: 99}), PipelineStage.NEW)
    assert (last.page, [lead.name for lead in last.leads][0], len(last.leads)) == (
        3,
        "New 019",
        20,
    )
    empty = group(world.board(pages={PipelineStage.WON: 5}), PipelineStage.WON)
    assert (empty.page, empty.pages) == (1, 1)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"segment_id": 1}, TypeError),
        ({"market_id": b"x"}, TypeError),
        ({"entered_from": "2026-11-01"}, TypeError),
        ({"entered_to": datetime(2026, 11, 1, tzinfo=UTC)}, TypeError),
        ({"pages": [1]}, TypeError),
        ({"pages": {"new": 1}}, TypeError),
        ({"pages": {PipelineStage.NEW: True}}, TypeError),
        ({"pages": {PipelineStage.NEW: "2"}}, TypeError),
        ({"pages": {PipelineStage.NEW: 0}}, ValueError),
        ({"entered_from": date(2026, 11, 2), "entered_to": date(2026, 11, 1)}, ValueError),
    ],
)
def test_bad_arguments_raise(world: World, arguments: dict[str, Any], error: type) -> None:
    with pytest.raises(error):
        world.board(**arguments)


def test_the_last_representable_day_has_no_upper_bound(world: World) -> None:
    lead = world.lead("Far", stage=PipelineStage.CONTACTED)
    world.entered(lead, PipelineStage.CONTACTED, T)

    assert names(world.board(entered_to=date.max), PipelineStage.CONTACTED) == ["Far"]
    both = world.board(entered_from=date.max, entered_to=date.max)
    assert names(both, PipelineStage.CONTACTED) == []


def test_the_entry_time_comes_from_the_real_pipeline_service(world: World) -> None:
    clock = FrozenClock(datetime(2026, 11, 20, 14, 30, tzinfo=UTC))
    pipeline = Pipeline(clock)
    lead = world.lead("Real")  # stage new, no events row yet

    def run(fn: Any) -> None:
        @use_case
        def caller() -> None:
            with current().transaction():
                fn()

        with world.units.open():
            caller()

    run(lambda: pipeline.record_created(lead, LeadSource.CSV))
    [created] = group(world.board(), PipelineStage.NEW).leads
    assert created.entered_at == datetime(2026, 11, 20, 14, 30, tzinfo=UTC)

    clock.set(datetime(2026, 11, 22, 8, 0, tzinfo=UTC))
    run(lambda: pipeline.close(lead, PipelineStage.LOST, None))
    [closed] = group(world.board(), PipelineStage.LOST).leads
    assert closed.entered_at == datetime(2026, 11, 22, 8, 0, tzinfo=UTC)
    day = date(2026, 11, 22)
    kept = world.board(entered_from=day, entered_to=day)
    assert names(kept, PipelineStage.LOST) == ["Real"]
    after = day + timedelta(days=1)
    assert names(world.board(entered_from=after, entered_to=after), PipelineStage.LOST) == []
    before = date(2026, 11, 20)  # its creation day: it has left that stage
    assert all(not each.leads for each in world.board(entered_from=before, entered_to=before).groups)


# The page ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["to=9999-12-31", "from=9999-12-31&to=9999-12-31"])
def test_the_last_representable_day_is_accepted(world: World, query: str) -> None:
    world.lead("Smith Law")

    assert len(sections(world.page(query))) == len(PipelineStage)


def test_only_the_not_now_group_has_the_recontact_column(world: World) -> None:
    lead = world.lead("Parked", stage=PipelineStage.NOT_NOW)
    world.lead("Lost", stage=PipelineStage.LOST)
    with world.units.open(read_only=True):
        board = pipeline_queries.board()
    views = [pipeline_page.group_view(each, pipeline_page.BoardFilters(), board, {}) for each in board.groups]

    assert [view.stage for view in views if view.recontact] == ["not_now"]
    page = world.page()
    assert table_rows(section(page, PipelineStage.LOST).find("table"))[0] == HEADER
    assert table_rows(section(page, PipelineStage.NOT_NOW).find("table"))[0] == NOT_NOW_HEADER
    assert lead


def test_the_unfiltered_board_shows_every_group_with_its_count_and_links(world: World) -> None:
    a = world.lead("Smith <b>Law</b>", minutes=1)
    b = world.lead("Jones Ops", segment=world.ops)
    world.entered(b, PipelineStage.NEW, datetime(2026, 11, 3, 8, 0, tzinfo=UTC))
    parked = world.lead("Parked", stage=PipelineStage.NOT_NOW)
    world.lead("Gone", merged_into=a)
    world.lead("Closed", stage=PipelineStage.DISQUALIFIED)

    page = world.page()

    assert page.find("title").text == "Nextron: Pipeline"
    assert page.find("h1").text == "Pipeline"
    found = sections(page)
    assert [node.attrs["data-stage"] for node in found] == [str(s) for s in PipelineStage]
    assert [node.find("h2").text for node in found] == [STAGE_LABELS[s] for s in PipelineStage]
    new = section(page, PipelineStage.NEW)
    assert summary(new) == "2 Leads Deal value: —"
    table = new.find("table")
    assert "hx-disable" in table.attrs
    assert table_rows(table) == [
        HEADER,
        ["Smith <b>Law</b>", "Law-PrivateAI", "—"],
        ["Jones Ops", "Ops-Automation", "2026-11-03"],
    ]
    links = [row.find("a").attrs["href"] for row in table.find_all("tr")[1:]]
    assert links == [f"/leads/{a}", f"/leads/{b}"]
    not_now = section(page, PipelineStage.NOT_NOW)
    assert summary(not_now) == "1 Lead Deal value: —"
    assert table_rows(not_now.find("table")) == [
        NOT_NOW_HEADER,
        ["Parked", "Law-PrivateAI", "—", "Re-contact: —"],
    ]
    assert not_now.find("a").attrs["href"] == f"/leads/{parked}"
    assert summary(section(page, PipelineStage.DISQUALIFIED)) == "1 Lead Deal value: —"
    won = section(page, PipelineStage.WON)
    assert summary(won) == "0 Leads Deal value: —"
    assert won.find("p", class_="stage-empty").text == "No Leads at this stage."
    assert not won.find_all("table")
    assert "Gone" not in page.text
    assert not page.find_all("nav", class_="pager")
    assert not page.find_all("p", class_="notice-refused")
    assert not page.find("main").find_all("a", href="/leads/pipeline")  # no "Clear filters"
    reply = world.get()
    assert "<b>Law</b>" not in reply.text


def test_the_filter_form_is_a_get_form_without_a_token(world: World) -> None:
    page = world.page()

    [form] = page.find_all("form")
    assert (form.attrs["method"], form.attrs["action"]) == ("get", "/leads/pipeline")
    assert "hx-post" not in form.attrs
    names_ = [node.attrs["name"] for node in form.walk() if "name" in node.attrs]
    assert names_ == ["segment", "market", "from", "to"]
    reply = world.get()
    form_html = reply.text[reply.text.index("<form") : reply.text.index("</form>")]
    assert "csrf" not in form_html.lower()
    segment = [(o.attrs["value"], o.text) for o in form.find("select", name="segment").find_all("option")]
    assert segment == [
        ("", "All Segments"),
        ("law_private_ai", "Law-PrivateAI"),
        ("ops_automation", "Ops-Automation"),
    ]
    market = [(o.attrs["value"], o.text) for o in form.find("select", name="market").find_all("option")]
    assert market == [("", "All Markets"), ("uk", "United Kingdom"), ("us", "United States")]
    assert [form.find("input", name=n).attrs["type"] for n in ("from", "to")] == ["date", "date"]


def test_the_filters_apply_to_every_group_and_stay_selected(world: World) -> None:
    hit = world.lead("Hit", stage=PipelineStage.REPLIED)
    world.contact(hit, "uk")
    world.entered(hit, PipelineStage.REPLIED, datetime(2026, 11, 5, tzinfo=UTC))
    late = world.lead("Late", stage=PipelineStage.REPLIED)
    world.contact(late, "uk")
    world.entered(late, PipelineStage.REPLIED, datetime(2026, 11, 20, tzinfo=UTC))
    us = world.lead("US", stage=PipelineStage.LOST)
    world.contact(us, "us")
    world.entered(us, PipelineStage.LOST, datetime(2026, 11, 5, tzinfo=UTC))
    ops = world.lead("Ops", segment=world.ops)
    world.contact(ops, "uk")
    world.entered(ops, PipelineStage.NEW, datetime(2026, 11, 5, tzinfo=UTC))

    page = world.page("segment=law_private_ai&market=uk&from=2026-11-01&to=2026-11-10")

    counts = {node.attrs["data-stage"]: summary(node) for node in sections(page)}
    assert counts["replied"] == "1 Lead Deal value: —"
    assert all(text.startswith("0 Leads") for stage, text in counts.items() if stage != "replied")
    assert "Hit" in page.text and "Late" not in page.text
    selected = [o.attrs["value"] for o in page.find_all("option") if "selected" in o.attrs]
    assert selected == ["law_private_ai", "uk"]
    assert page.find("input", name="from").attrs["value"] == "2026-11-01"
    assert page.find("input", name="to").attrs["value"] == "2026-11-10"
    assert page.find("main").find("a", href="/leads/pipeline").text == "Clear filters"


def test_blank_parameters_are_unset(world: World) -> None:
    world.lead("Smith Law")

    page = world.page("segment=&market=+&from=&to=%09&page_new=")

    assert summary(section(page, PipelineStage.NEW)) == "1 Lead Deal value: —"
    assert not page.find("main").find_all("a", href="/leads/pipeline")


def test_a_group_pager_keeps_every_other_parameter(world: World) -> None:
    for n in range(120):
        world.lead(f"New {n:03}", minutes=n)
    for n in range(60):
        world.lead(f"Lost {n:03}", stage=PipelineStage.LOST, minutes=n)

    page = world.page("segment=law_private_ai&page_new=2&page_lost=2")

    new = section(page, PipelineStage.NEW)
    rows = table_rows(new.find("table"))[1:]
    assert (rows[0][0], rows[-1][0], len(rows)) == ("New 069", "New 020", 50)
    pager = new.find("nav", class_="pager")
    assert pager.find("span").text == "Page 2 of 3"
    previous = urlsplit(pager.find("a", rel="prev").attrs["href"] or "")
    following = urlsplit(pager.find("a", rel="next").attrs["href"] or "")
    assert previous.path == following.path == "/leads/pipeline"
    assert previous.fragment == following.fragment == "stage-new"
    assert parse_qsl(previous.query) == [("segment", "law_private_ai"), ("page_lost", "2")]
    assert parse_qsl(following.query) == [
        ("segment", "law_private_ai"),
        ("page_new", "3"),
        ("page_lost", "2"),
    ]
    lost = section(page, PipelineStage.LOST)
    assert lost.find("nav", class_="pager").find("span").text == "Page 2 of 2"
    assert [r[0] for r in table_rows(lost.find("table"))[1:]] == [
        f"Lost {n:03}" for n in range(9, -1, -1)
    ]


def test_a_page_past_the_end_shows_the_last(world: World) -> None:
    for n in range(51):
        world.lead(f"New {n:03}", minutes=n)

    page = world.page("page_new=9999999")

    new = section(page, PipelineStage.NEW)
    assert table_rows(new.find("table"))[1:] == [["New 000", "Law-PrivateAI", "—"]]
    pager = new.find("nav", class_="pager")
    assert pager.find("span").text == "Page 2 of 2"
    assert not pager.find_all("a", rel="next")


def test_a_start_after_the_end_is_refused_with_an_alert(world: World) -> None:
    world.lead("Smith Law")

    reply = world.get("from=2026-11-10&to=2026-11-01&segment=ops_automation")

    assert reply.status == 400
    assert reply.header(b"cache-control") == b"no-store"
    page = Tree(reply.text)
    alert = page.find("p", class_="notice-refused")
    assert (alert.attrs["role"], alert.text) == ("alert", RANGE_REFUSED)
    assert RANGE_REFUSED == "End date must be on or after start date"
    assert not sections(page)
    assert page.find("input", name="from").attrs["value"] == "2026-11-10"
    assert page.find("input", name="to").attrs["value"] == "2026-11-01"
    selected = [o.attrs["value"] for o in page.find_all("option") if "selected" in o.attrs]
    assert selected == ["ops_automation", ""]  # the Segment kept, "All Markets"


@pytest.mark.parametrize(
    "query",
    [
        "foo=1",
        "page=2",
        "stage=new",
        "csrf_token=x",
        "segment=nope",
        "market=xx",
        "market=UK",
        "segment=law_private_ai&segment=law_private_ai",
        "from=2026-11-01&from=2026-11-02",
        "from=2026-1-01",
        "from=20261101",
        "to=2026-02-30",
        "to=2026-11-01T00:00",
        "from=%EF%BC%92026-11-01",  # a fullwidth digit
        "page_new=0",
        "page_new=abc",
        "page_new=-1",
        "page_new=12345678",
        "page_bogus=1",
        "page_new=1&page_new=2",
    ],
)
def test_a_bad_query_parameter_is_refused(world: World, query: str) -> None:
    reply = world.get(query)

    assert reply.status == 400
    assert reply.text == MALFORMED_FORM
    assert reply.header(b"cache-control") == b"no-store"


def test_the_leads_list_links_the_board(world: World) -> None:
    page = Tree(world.dashboard.get("/leads", cookie=world.cookie).text)

    assert page.find("a", href="/leads/pipeline").text == "View by Pipeline Stage"


def test_the_route_is_a_get_before_the_lead_page_and_locked_without_a_session(
    world: World,
) -> None:
    world.lead("Smith Law")
    routes = {getattr(r, "name", None): r for r in world.dashboard.app.routes}
    board = routes["pipeline_board"]

    assert getattr(board, "path") == "/leads/pipeline"
    assert getattr(board, "methods") == {"GET"}
    assert getattr(getattr(board, "endpoint"), "__nextron_personal_data__") is True
    paths = [getattr(r, "path", None) for r in world.dashboard.app.routes]
    assert paths.index("/leads/pipeline") < paths.index("/leads/{lead_id}")
    reply = world.dashboard.get("/leads/pipeline")
    assert reply.status == 401
    assert "Smith Law" not in reply.text


def test_the_view_module_reads_only_through_queries() -> None:
    tree = ast.parse((PACKAGE / "web" / "pipeline_page.py").read_text(encoding="utf-8"))
    modules = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ] + [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert app_modules  # not vacuous
    assert all(name.startswith("nextron.app.queries.") for name in app_modules), app_modules
    assert not [name for name in modules if name.startswith("nextron.db")]


# 5,000 Leads ------------------------------------------------------------------------------------


def test_five_thousand_leads_render_in_under_a_second(world: World) -> None:
    stages = list(PipelineStage)
    leads = []
    events = []
    contacts = []
    for n in range(5_000):
        lead_id = new_id()
        stage = stages[n % len(stages)]
        leads.append(
            (
                lead_id,
                f"Firm {n:04}",
                f"place-{n}",
                world.law if n % 2 else world.ops,
                utc_text(T + timedelta(seconds=n)),
                str(stage),
            )
        )
        events.append((new_id(), lead_id, str(stage), utc_text(T + timedelta(seconds=n))))
        contacts.append(
            (
                new_id(),
                lead_id,
                world.markets["uk" if n % 3 else "us"],
                "Europe/London",
                str(MarketBasis.LEAD_COUNTRY),
                str(TzBasis.MARKET_DEFAULT),
                utc_text(T),
                utc_text(T),
            )
        )
    with world.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO leads (id, name, place_id, segment_id, created_at, pipeline_stage) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            leads,
        )
        connection.exec_driver_sql(
            "INSERT INTO events (id, entity, entity_id, from_status, to_status, cause, "
            "cause_ref, actor, at) VALUES (?, 'leads', ?, NULL, ?, 'operator_move', NULL, "
            "'system:pipeline', ?)",
            events,
        )
        connection.exec_driver_sql(
            "INSERT INTO contacts (id, lead_id, name, market_id, tz, market_basis, tz_basis, "
            "created_at, updated_at) VALUES (?, ?, 'Pat', ?, ?, ?, ?, ?, ?)",
            contacts,
        )
    world.get()  # warm the templates

    def best_of_three(query: str) -> tuple[Reply, float]:
        """The reply and the fastest of three timed requests (one sample can flake under -n)."""
        timings = []
        for _ in range(3):
            started = time.perf_counter()
            reply = world.get(query)
            timings.append(time.perf_counter() - started)
        return reply, min(timings)

    def total(page: Tree) -> int:
        return sum(int(summary(node).split()[0].replace(",", "")) for node in sections(page))

    reply, elapsed = best_of_three("")
    assert reply.status == 200
    assert elapsed < 1.0, elapsed
    page = Tree(reply.text)
    assert total(page) == 5_000
    assert all(len(node.find_all("tr")) == 51 for node in sections(page))  # 50 rows + header

    reply, elapsed = best_of_three("market=uk&from=2026-11-17&to=2026-11-17")
    assert reply.status == 200
    assert elapsed < 1.0, elapsed
    filtered = Tree(reply.text)
    assert total(filtered) == 5_000 - 1_667  # every Lead but each third one (a US Contact)
    new = section(filtered, PipelineStage.NEW)  # n % 14 == 0 and n % 3 != 0
    assert summary(new).startswith(f"{sum(1 for n in range(0, 5_000, 14) if n % 3)} Leads")
