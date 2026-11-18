"""The Leads list (``GET /leads``) and the Lead page (``GET /leads/{lead_id}``) (Story 2.4;
FR-51, FR-13, UX-DR17, UX-DR18, AD-3, AD-13) through ``Dashboard(units=...)`` on a temporary
database at head with the real subscriber registry and the shipped Segments, the HTML parsed
into a tree: every page row of the I/O matrix (the list, the filters, paging, bad parameters, a
CSV-imported Lead's page, merged and missing Leads, hostile text, the empty cases), each
Contact's live Eligibility row (Story 3.6: eligible and ineligible Contacts, every reason with
its ``data-reason``, recomputed on every render, nothing stored, inside ``hx-disable``),
``no-store``, the lock without a session, the route order, the nav link, the label tables and
the queries-only import rule. Story 2.9 adds the headquarters zone and the Signals and Evidence
sections (Tier 1 at 31 days live, Tier 2 at 91 days expired, "AI-extracted", escaped). Story
3.7's Provenance facts, links and bases are in ``tests/unit/test_lead_provenance_page.py``.
Story 2.12 adds a GB Lead's legal-form section ("Not checked yet", each status, the Companies House
link, the two Evidence quotes escaped inside ``hx-disable``, none outside GB)."""

import ast
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries.discovery import StoredLead
from nextron.app.queries.setup import SeededSegment
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import profile as profile_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import ContactSubject, SuppressionScope
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import Decision, Reason, ReasonKind
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import SuppressionSource
from nextron.web import leads_page
from nextron.web.forms import MALFORMED_FORM
from nextron.domain.extraction import FactKind
from nextron.domain.segments import EvidenceType, SignalClass
from nextron.web.leads_page import (
    CLASS_LABELS,
    EVIDENCE_TYPE_LABELS,
    FACT_LABELS,
    SOURCE_LABELS,
    STAGE_LABELS,
)
from tests.asgi import Reply
from tests.contracts.support import PACKAGE
from tests.dashboard import FORM, Dashboard, form, upload
from tests.fakes.clock import FrozenClock

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
ORIGIN = "Law Society directory, Manchester"
UNKNOWN = "01KA0000000000000000000000"  # a ULID no Lead has
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"})
LIST_HEADER = ["Name", "Website", "Segment", "Country", "Stage", "Created"]
CONTACT_HEADER = [
    "Name",
    "Role",
    "Email",
    "Address Class",
    "LinkedIn",
    "Primary",
    "Action",
    "Erasure",  # Story 3.13
]
ERASE = "Erase (data subject request)"  # Story 3.13
RECORD_HEADER = ["Record", "Source", "Detail", "Retrieved", "Address Class"]
SIGNAL_HEADER = ["Signal", "Class", "Tier", "Date", "Age", "Status", "Score only"]
EVIDENCE_HEADER = ["Type", "Evidence", "Supports", "Source", "Retrieved", "Extraction"]


# The parsed page --------------------------------------------------------------------------------


@dataclass(eq=False)
class Node:
    """One element: its tag, attributes, parent and children (elements and text)."""

    tag: str
    attrs: dict[str, str | None]
    parent: "Node | None"
    children: list["Node | str"] = field(default_factory=list)

    def walk(self) -> Iterator["Node"]:
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, tag: str, **attrs: str) -> list["Node"]:
        """Each descendant ``tag`` whose attributes include ``attrs`` (``class_`` matches one
        class)."""
        wanted = {key.rstrip("_"): value for key, value in attrs.items()}
        found = []
        for node in self.walk():
            if node.tag != tag:
                continue
            if all(
                value in (node.attrs.get("class") or "").split()
                if key == "class"
                else node.attrs.get(key) == value
                for key, value in wanted.items()
            ):
                found.append(node)
        return found

    def find(self, tag: str, **attrs: str) -> "Node":
        [found] = self.find_all(tag, **attrs)
        return found

    @property
    def text(self) -> str:
        parts: list[str] = []

        def collect(node: Node) -> None:
            for child in node.children:
                if isinstance(child, Node):
                    collect(child)
                else:
                    parts.append(child)

        collect(self)
        return " ".join("".join(parts).split())

    def ancestors(self) -> Iterator["Node"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class Tree(HTMLParser):
    """The page as a tree of ``Node``; ``texts`` holds each text with its element."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.texts: list[tuple[str, Node]] = []
        self._open = self.root
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs), self._open)
        self._open.children.append(node)
        if tag not in VOID:
            self._open = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open.children.append(Node(tag, dict(attrs), self._open))

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self._open
        while node is not None and node is not self.root and node.tag != tag:
            node = node.parent
        if node is not None and node is not self.root and node.parent is not None:
            self._open = node.parent

    def handle_data(self, data: str) -> None:
        self._open.children.append(data)
        self.texts.append((data, self._open))

    def find_all(self, tag: str, **attrs: str) -> list[Node]:
        return self.root.find_all(tag, **attrs)

    def find(self, tag: str, **attrs: str) -> Node:
        return self.root.find(tag, **attrs)

    @property
    def text(self) -> str:
        return self.root.text

    def disabled(self, needle: str) -> list[bool]:
        """For each text holding ``needle``: whether an ancestor carries ``hx-disable``."""
        return [
            any("hx-disable" in node.attrs for node in (element, *element.ancestors()))
            for text, element in self.texts
            if needle in text
        ]


def cells(row: Node) -> list[str]:
    return [
        cell.text for cell in row.children if isinstance(cell, Node) and cell.tag in ("td", "th")
    ]


def options_of(select: Node) -> list[Node]:
    return select.find_all("option")


def table_rows(table: Node) -> list[list[str]]:
    """The rows of ``table`` itself (not of a table inside it), header first."""
    found = []
    for row in table.find_all("tr"):
        nearest = next(node for node in row.ancestors() if node.tag == "table")
        if nearest is table:
            found.append(cells(row))
    return found


# The world --------------------------------------------------------------------------------------


@dataclass
class World:
    """The dashboard on real units of work, with Discovery and Research to add Leads."""

    dashboard: Dashboard
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    cookie: bytes
    discovery: Discovery
    research: Research
    law: str
    ops: str

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def lead(
        self,
        name: str,
        domain: str | None,
        *,
        minutes: int = 0,
        segment: str | None = None,
        country: str | None = "GB",
        ch_number: str | None = None,
    ) -> str:
        self.clock.set(T + timedelta(minutes=minutes))
        identity = LeadIdentity(
            name,
            segment or self.law,
            web_domain=domain,
            ch_number=ch_number,
            country_code=country,
        )
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        self.clock.set(T)
        return upserted.lead_id

    def merge(self, survivor: str, loser: str) -> None:
        self.transact(lambda: self.discovery.merge(survivor, loser))

    def add(
        self,
        lead_id: str,
        candidate: ContactCandidate,
        *,
        detail: str = ORIGIN,
        source: ProvenanceSource = ProvenanceSource.CSV,
        seconds: int = 0,
    ) -> str:
        self.clock.set(T + timedelta(seconds=seconds))
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail=detail, retrieved_at=T
            )
        )
        self.clock.set(T)
        return added.contact_id

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def stage(self, lead_id: str, stage: PipelineStage) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (str(stage), lead_id))

    def get(self, path: str, query: str = "") -> Reply:
        return self.dashboard.get(path, cookie=self.cookie, query=query.encode())

    def page(self, path: str, query: str = "") -> Tree:
        reply = self.get(path, query)
        assert reply.status == 200, reply
        assert reply.header(b"cache-control") == b"no-store"
        return Tree(reply.text)


def no_jobs() -> Any:
    """``build_job_types``'s services: no job runs in these tests."""
    raise AssertionError("no job runs in these tests")


def queue_on(clock: FrozenClock) -> JobQueue:
    """A job queue on the real job types; ``upsert_lead`` enqueues a CSV Lead's crawl there."""
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        test_runs=no_jobs,
        job_boards=no_jobs,
        legal_forms=no_jobs,
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    dashboard = Dashboard(units=units, clock=FrozenClock(T))
    cookie = dashboard.open_session()
    discovery, research = Discovery(clock, Pipeline(clock), queue_on(clock)), Research(clock)
    found = World(dashboard, units, db_engine, clock, cookie, discovery, research, "", "")
    [(law,), (ops,)] = found.sql("SELECT id FROM segments ORDER BY key")
    found.law, found.ops = law, ops
    return found


# The Leads list ---------------------------------------------------------------------------------


def test_the_list_shows_each_lead_newest_first_without_merged_leads(world: World) -> None:
    a = world.lead("Smith Law", "smith-law.co.uk", minutes=0)
    b = world.lead("Smith Law LLP", "smithlaw.co.uk", minutes=1)
    c = world.lead(
        "Jones Ops", None, minutes=60 * 24, segment=world.ops, country=None, ch_number="12345678"
    )
    world.merge(a, b)
    world.stage(a, PipelineStage.CALL_BOOKED)

    page = world.page("/leads")

    assert page.find("title").text == "Nextron: Leads"
    assert page.find("h1").text == "Leads"
    assert page.find("a", href="/leads/import").text == "Import Leads from a CSV file"
    table = page.find("table", class_="lead-table")
    assert "hx-disable" in table.attrs
    assert table_rows(table) == [
        LIST_HEADER,
        ["Jones Ops", "—", "Ops-Automation", "—", "New", "2026-11-18"],
        ["Smith Law", "smith-law.co.uk", "Law-PrivateAI", "GB", "Call booked", "2026-11-17"],
    ]
    links = [row.find("a").attrs["href"] for row in table.find_all("tr")[1:]]
    assert links == [f"/leads/{c}", f"/leads/{a}"]
    assert page.find("p", class_="lead-count").text == "Showing 1–2 of 2 Leads"
    assert not page.find_all("nav", class_="pager")
    assert not page.find("main").find_all("a", href="/leads")  # no filter: no "Clear filters"


def test_the_filter_form_is_a_get_form_without_a_token(world: World) -> None:
    page = world.page("/leads")

    [form] = page.find_all("form")
    assert form.attrs["method"] == "get"
    assert form.attrs["action"] == "/leads"
    assert "hx-post" not in form.attrs
    assert not form.find_all("input", name="csrf_token")
    names = [node.attrs["name"] for node in form.walk() if "name" in node.attrs]
    assert names == ["stage", "segment", "q"]  # a named button would 400 every submission
    reply = world.get("/leads")
    form_html = reply.text[reply.text.index("<form") : reply.text.index("</form>")]
    token = world.dashboard.csrf_token(world.cookie)
    assert token in reply.text  # in <body hx-headers>, for HTMX
    assert token not in form_html and "csrf" not in form_html.lower()
    stage = form.find("select", name="stage")
    options = [(o.attrs["value"], o.text, "selected" in o.attrs) for o in options_of(stage)]
    assert options == [("", "All stages", True)] + [
        (str(s), STAGE_LABELS[s], False) for s in PipelineStage
    ]
    segment = form.find("select", name="segment")
    options = [(o.attrs["value"], o.text, "selected" in o.attrs) for o in options_of(segment)]
    assert options == [
        ("", "All Segments", True),
        ("law_private_ai", "Law-PrivateAI", False),
        ("ops_automation", "Ops-Automation", False),
    ]
    search = form.find("input", name="q")
    assert (search.attrs["type"], search.attrs["maxlength"], search.attrs["value"]) == (
        "text",
        "200",
        "",
    )
    assert form.find("label", **{"for": "filter-q"}).text == "Name or website"
    assert form.find("button").text == "Filter"
    assert page.find("p", class_="lead-count").text == "No Leads yet."


def test_the_filters_keep_their_values(world: World) -> None:
    a = world.lead("Smith_Law", "a.co.uk", minutes=0)
    b = world.lead("SmithXLaw", "b.co.uk", minutes=1)
    c = world.lead("Smith_Ops", "c.co.uk", minutes=2, segment=world.ops)
    world.lead("Smith_Partners", "d.co.uk", minutes=3)  # still at New
    for lead_id in (a, b, c):
        world.stage(lead_id, PipelineStage.CONTACTED)

    page = world.page("/leads", "stage=contacted&segment=law_private_ai&q=Smith_")

    rows = table_rows(page.find("table", class_="lead-table"))
    assert [row[0] for row in rows[1:]] == ["Smith_Law"]
    assert page.find("p", class_="lead-count").text == "Showing 1–1 of 1 Lead"
    selected = [o.attrs["value"] for o in page.find_all("option") if "selected" in o.attrs]
    assert selected == ["contacted", "law_private_ai"]
    assert page.find("input", name="q").attrs["value"] == "Smith_"
    assert page.find("main").find("a", href="/leads").text == "Clear filters"


def test_no_match_says_so(world: World) -> None:
    world.lead("Smith Law", "smith-law.co.uk")

    page = world.page("/leads", "q=nobody")

    assert page.find("p", class_="lead-count").text == "No Leads match these filters."
    assert not page.find_all("table")
    assert page.find("main").find("a", href="/leads").text == "Clear filters"


def test_blank_parameters_are_unset(world: World) -> None:
    world.lead("Smith Law", "smith-law.co.uk")

    page = world.page("/leads", "stage=&segment=+&q=+++&page=%09")

    assert page.find("p", class_="lead-count").text == "Showing 1–1 of 1 Lead"
    assert not page.find("main").find_all("a", href="/leads")


def test_the_search_text_is_collapsed_and_escaped_in_its_value(world: World) -> None:
    reply = world.get("/leads", "q=%22%3E%3Cscript%3Ex%3C%2Fscript%3E++a")

    assert reply.status == 200
    assert "<script>x" not in reply.text
    assert Tree(reply.text).find("input", name="q").attrs["value"] == '"><script>x</script> a'


def many(
    world: World, count: int, *, name: str = "Firm {n:03}", domain: str = "firm{n}.co.uk"
) -> list[str]:
    """``count`` Leads, a minute apart, oldest first, in one transaction; ``name`` and
    ``domain`` are formatted with ``n``."""

    def add() -> list[str]:
        ids = []
        for n in range(count):
            world.clock.set(T + timedelta(minutes=n))
            identity = LeadIdentity(
                name.format(n=n), world.law, web_domain=domain.format(n=n)
            )
            ids.append(world.discovery.upsert_lead(identity, LeadSource.CSV).lead_id)
        world.clock.set(T)
        return ids

    return world.transact(add)


def test_paging_keeps_the_filters(world: World) -> None:
    ids = many(world, 51)
    world.lead("Other", "other.co.uk", minutes=999, segment=world.ops)
    query = "segment=law_private_ai&q=Firm+0"  # matches Firm 000 to Firm 050

    first = world.page("/leads", query)
    second = world.page("/leads", query + "&page=2")
    past = world.page("/leads", query + "&page=9")

    first_rows = table_rows(first.find("table", class_="lead-table"))
    assert [row[0] for row in first_rows[1:]] == [f"Firm {n:03}" for n in range(50, 0, -1)]
    hrefs = [a.attrs["href"] for a in first.find("table", class_="lead-table").find_all("a")]
    assert hrefs == [f"/leads/{lead_id}" for lead_id in ids[:0:-1]]
    assert first.find("p", class_="lead-count").text == "Showing 1–50 of 51 Leads"
    pager = first.find("nav", class_="pager")
    assert pager.text == "Page 1 of 2 Next"
    [next_link] = pager.find_all("a")
    href = next_link.attrs["href"] or ""
    assert next_link.attrs["rel"] == "next"
    assert urlsplit(href).path == "/leads"
    assert parse_qsl(urlsplit(href).query) == [
        ("segment", "law_private_ai"),
        ("q", "Firm 0"),
        ("page", "2"),
    ]
    rows = table_rows(second.find("table", class_="lead-table"))
    assert [row[0] for row in rows[1:]] == ["Firm 000"]
    assert second.find("p", class_="lead-count").text == "Showing 51–51 of 51 Leads"
    pager = second.find("nav", class_="pager")
    assert pager.text == "Previous Page 2 of 2"
    assert pager.find("a").attrs["rel"] == "prev"
    assert parse_qsl(urlsplit(pager.find("a").attrs["href"] or "").query)[-1] == ("page", "1")
    assert table_rows(past.find("table", class_="lead-table")) == rows
    assert past.find("p", class_="lead-count").text == "Showing 51–51 of 51 Leads"
    pager = past.find("nav", class_="pager")
    assert pager.text == "Previous Page 2 of 2"
    assert parse_qsl(urlsplit(pager.find("a").attrs["href"] or "").query) == [
        ("segment", "law_private_ai"),
        ("q", "Firm 0"),
        ("page", "1"),
    ]


def follow(world: World, href: str | None) -> Tree:
    """The page at ``href``, a link the dashboard rendered."""
    parts = urlsplit(href or "")
    assert parts.path == "/leads" and not parts.scheme and not parts.netloc
    return world.page(parts.path, parts.query)


def test_the_pager_links_encode_the_search_and_round_trip(world: World) -> None:
    text = "A&B+C 50% Firm"
    many(world, 51, name=text + " {n:03}", domain="ab{n}.co.uk")
    many(world, 3, name="Other {n}", domain="other{n}.co.uk")  # not matched

    first = world.page("/leads", urlencode({"q": text}))
    next_href = first.find("nav", class_="pager").find("a").attrs["href"]
    second = follow(world, next_href)
    previous_href = second.find("nav", class_="pager").find("a").attrs["href"]
    back = follow(world, previous_href)

    assert parse_qsl(urlsplit(next_href or "").query) == [("q", text), ("page", "2")]
    assert first.find("p", class_="lead-count").text == "Showing 1–50 of 51 Leads"
    rows = table_rows(second.find("table", class_="lead-table"))
    assert [row[0] for row in rows[1:]] == [f"{text} 000"]
    assert second.find("p", class_="lead-count").text == "Showing 51–51 of 51 Leads"
    assert second.find("input", name="q").attrs["value"] == text
    assert parse_qsl(urlsplit(previous_href or "").query) == [("q", text), ("page", "1")]
    assert table_rows(back.find("table", class_="lead-table")) == table_rows(
        first.find("table", class_="lead-table")
    )
    assert back.find("p", class_="lead-count").text == "Showing 1–50 of 51 Leads"


@pytest.mark.parametrize(
    "query",
    [
        "stage=bogus",
        "stage=NEW",
        "segment=nope",
        "page=0",
        "page=abc",
        "page=-1",
        "page=1.5",
        "page=12345678",
        "page=%EF%BC%91",  # a fullwidth digit
        "q=a&q=b",
        "stage=new&stage=new",
        "foo=1",
        "csrf_token=x",
        "q=" + "a" * 201,
        "q=a%00b",
    ],
)
def test_a_bad_query_parameter_is_refused(world: World, query: str) -> None:
    reply = world.get("/leads", query)

    assert reply.status == 400
    assert reply.text == MALFORMED_FORM
    assert reply.header(b"cache-control") == b"no-store"


def test_the_longest_search_and_page_are_accepted(world: World) -> None:
    world.lead("Smith Law", "smith-law.co.uk")

    assert world.get("/leads", "q=" + "a" * 200 + "&page=9999999").status == 200
    assert world.get("/leads", "q=+" + "a" * 199 + "++").status == 200
    assert world.get("/leads", "page=0000001").status == 200


# The Lead page ----------------------------------------------------------------------------------


def import_csv(world: World, *lines: str) -> None:
    data, headers = upload(
        ("\r\n".join(lines) + "\r\n").encode("utf-8"),
        csrf_token=world.dashboard.csrf_token(world.cookie),
        origin=ORIGIN,
        default_segment="law_private_ai",
    )
    reply = world.dashboard.post(
        "/leads/import",
        cookie=world.cookie,
        token=world.dashboard.csrf_token(world.cookie),
        headers=headers,
        body=data,
    )
    assert reply.status == 200 and "Imported: 1." in reply.text, reply.text


def test_a_csv_imported_lead_shows_its_contact_and_provenance(world: World) -> None:
    markets = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
    with world.units.open():  # Story 3.2's import blocks a row outside the enabled Markets
        Setup(world.clock).sync_markets(markets)
    import_csv(
        world,
        "Organization name,Website,Country,Contact name,Role,Email,LinkedIn URL",
        "Smith Law,https://www.smith-law.co.uk/team,UK,Jane Doe,Partner,Jane@Smith-Law.co.uk,"
        "linkedin.com/in/Jane-Doe",
    )
    listed = world.page("/leads")
    href = listed.find("table", class_="lead-table").find("a").attrs["href"] or ""

    page = world.page(href)

    assert page.find("title").text == "Nextron: Lead"
    header = page.find("header", class_="lead-header")
    assert "hx-disable" in header.attrs
    assert header.find("h1").text == "Smith Law"
    facts = [node.text for node in header.find("dl").walk() if node.tag in ("dt", "dd")]
    assert facts == [
        "Website",
        "smith-law.co.uk",
        "Segment",
        "Law-PrivateAI",
        "Country",
        "GB",
        "HQ timezone",
        "—",
        "Pipeline Stage",
        "New",
        "Created",
        "2026-11-17",
    ]
    website = header.find("a")
    assert website.attrs == {"href": "https://smith-law.co.uk/", "rel": "noopener noreferrer"}
    assert page.find("main").find("a", href="/leads").text == "All Leads"
    assert [h2.text for h2 in page.find_all("h2")] == [
        "UK legal form",  # a GB Lead (Story 2.12)
        "Signals",
        "Evidence",
        "Contacts",
        "Job postings",
        "Data subject requests",  # Story 3.15's export forms
    ]
    table = page.find("table", class_="contact-table")
    assert table_rows(table)[0] == CONTACT_HEADER
    [contact] = table.find_all("tr", class_="contact-row")
    assert cells(contact) == [
        "Jane Doe",
        "Partner",
        "jane@smith-law.co.uk",
        "guessed",
        "https://www.linkedin.com/in/jane-doe",
        "No",
        "Make primary Channel permissions",  # Story 10.7's link
        ERASE,
    ]
    # Story 2.10: each untrusted cell carries hx-disable, the "Make primary" cell doesn't
    untrusted = [n for n in contact.children if isinstance(n, Node) and n.tag == "td"][:5]
    assert all("hx-disable" in cell.attrs for cell in untrusted)
    linkedin, permissions, erase = contact.find_all("a")  # Story 10.7's link between
    assert permissions.attrs["class"] == "permissions-link"
    [(contact_id,)] = world.sql("SELECT id FROM contacts")
    assert erase.attrs == {
        "class": "erase-link",
        "href": f"{href}/contacts/{contact_id}/erase",
    }
    assert linkedin.attrs["href"] == "https://www.linkedin.com/in/jane-doe"
    assert linkedin.attrs["rel"] == "noopener noreferrer"
    records = table_rows(table.find("table", class_="provenance-table"))
    assert records[0] == RECORD_HEADER
    assert sorted(records[1:]) == [
        ["Address jane@smith-law.co.uk", "CSV import", ORIGIN, "2026-11-17", "guessed"],
        ["Contact", "CSV import", ORIGIN, "2026-11-17", "—"],
    ]


def test_a_lead_without_a_website_or_country_says_so(world: World) -> None:
    lead = world.lead("Jones Ops", None, country=None, ch_number="12345678")

    header = world.page(f"/leads/{lead}").find("header", class_="lead-header")

    assert "No website on record" in header.text
    assert not header.find_all("a")
    assert header.find("dl").find_all("dd")[2].text == "—"


def test_the_empty_cases(world: World) -> None:
    empty = world.lead("Smith Law", "smith-law.co.uk")
    lead = world.lead("Jones Law", "jones.co.uk")
    jane = world.add(lead, ContactCandidate(name="Jane Doe"))
    world.add(lead, ContactCandidate(email="info@jones.co.uk"), seconds=1)
    world.sql("DELETE FROM provenance_records WHERE contact_id = ?", (jane,))

    nothing = world.page(f"/leads/{empty}")
    page = world.page(f"/leads/{lead}")

    assert nothing.find("section", class_="lead-contacts").text == "Contacts No Contacts yet."
    assert nothing.find("section", class_="lead-signals").text == "Signals No Signals yet."
    assert nothing.find("section", class_="lead-evidence").text == "Evidence No Evidence yet."
    assert not nothing.find_all("table")
    first, second = page.find_all("tr", class_="contact-row")
    assert cells(first) == [
        "Jane Doe",
        "—",
        "—",
        "—",
        "—",
        "No",
        "Make primary Channel permissions",  # Story 10.7's link
        ERASE,
    ]
    assert cells(second) == [
        "—",
        "—",
        "info@jones.co.uk",
        "guessed",
        "—",
        "No",
        "Make primary Channel permissions",
        ERASE,
    ]
    rows = page.find_all("tr", class_="provenance-row")
    # Story 3.7: a Contact with no source record shows "Provenance missing" in its place
    assert rows[0].find("p", class_="provenance-missing").text == "Provenance missing"
    assert not rows[0].find_all("table", class_="provenance-table")
    assert not rows[1].find_all("p", class_="provenance-missing")
    assert rows[1].find_all("table", class_="provenance-table")


def test_the_primary_flag_and_every_source_label(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    # add_contact refuses eligibility_basis (Story 3.7); test_lead_provenance_page shows its label
    sources = [s for s in ProvenanceSource if s is not ProvenanceSource.ELIGIBILITY_BASIS]
    for n, source in enumerate(sources):
        candidate = ContactCandidate(name=f"Person {n}")
        world.add(lead, candidate, source=source, detail=f"from {n}", seconds=n)
    world.sql("UPDATE contacts SET is_primary = 1 WHERE name = 'Person 0'")

    page = world.page(f"/leads/{lead}")

    primaries = [cells(row)[5] for row in page.find_all("tr", class_="contact-row")]
    assert primaries == ["Yes"] + ["No"] * (len(sources) - 1)
    shown = [
        table_rows(table)[1][1] for table in page.find_all("table", class_="provenance-table")
    ]
    assert shown == [SOURCE_LABELS[source] for source in sources]


def test_a_merged_lead_redirects_to_the_end_of_its_chain(world: World) -> None:
    a = world.lead("Alpha", "alpha.co.uk", minutes=0)
    b = world.lead("Alpha Two", "alpha2.co.uk", minutes=1)
    c = world.lead("Alpha Three", "alpha3.co.uk", minutes=2)
    world.merge(b, c)  # C -> B
    world.merge(a, b)  # B -> A

    for loser in (b, c):
        reply = world.get(f"/leads/{loser}")
        assert reply.status == 303
        assert reply.header(b"location") == f"/leads/{a}".encode()
        assert reply.header(b"cache-control") == b"no-store"
    assert world.get(f"/leads/{a}").status == 200


@pytest.mark.parametrize(
    "path",
    [
        f"/leads/{UNKNOWN}",
        f"/leads/{UNKNOWN.lower()}",
        "/leads/abc",
        "/leads/%3Cscript%3Ealert(1)%3C%2Fscript%3E",
        "/leads/NOT-A-LEAD-ZZZZ",
    ],
)
def test_a_missing_lead_is_404_without_echoing_the_id(world: World, path: str) -> None:
    world.lead("Smith Law", "smith-law.co.uk")

    reply = world.get(path)

    assert reply.status == 404
    assert reply.header(b"cache-control") == b"no-store"
    page = Tree(reply.text)
    assert page.find("h1").text == "Lead not found"
    assert page.find("main").find("a", href="/leads").text == "All Leads"
    assert page.find("title").text == "Nextron: Lead not found"
    for echoed in (UNKNOWN, UNKNOWN.lower(), "alert(1)", "NOT-A-LEAD"):
        assert echoed not in reply.text
    assert page.find("main").text == (
        "Lead not found No Lead has this address. Open the Lead from the Leads list. All Leads"
    )


def test_a_merge_loop_is_404(world: World) -> None:
    a = world.lead("Alpha", "alpha.co.uk")
    b = world.lead("Bravo", "bravo.co.uk")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (b, a))
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (a, b))  # a loop

    assert world.get(f"/leads/{a}").status == 404


# Hostile text -----------------------------------------------------------------------------------


NAME = "<script>x</script>"
ROLE = "<img src=x onerror=y>"
DETAIL = '<b hx-get="/x">'


def test_hostile_text_is_escaped_inside_hx_disable(world: World) -> None:
    lead = world.lead(NAME, "smith-law.co.uk")
    world.add(lead, ContactCandidate(name="Jane <i>Doe</i>", role=ROLE), detail=DETAIL)

    listed = world.get("/leads")
    page = world.get(f"/leads/{lead}")

    for reply in (listed, page):
        assert reply.status == 200
        for raw in ("<script>x", "<img src", "<b hx-get", "<i>Doe"):
            assert raw not in reply.text
    listed_tree, page_tree = Tree(listed.text), Tree(page.text)
    assert listed_tree.disabled(NAME) == [True]
    assert page_tree.disabled(NAME) == [True]
    assert page_tree.disabled(ROLE) == [True]
    assert page_tree.disabled(DETAIL) == [True]
    assert page_tree.disabled("Jane <i>Doe</i>") == [True, True]  # the row, its export form
    for tree in (listed_tree, page_tree):
        assert [node.attrs.get("src") for node in tree.find_all("script")] == [
            "/static/htmx-2.0.11.min.js"
        ]
        assert not tree.find_all("img") and not tree.find_all("b") and not tree.find_all("i")


# Signals and Evidence (Story 2.9) --------------------------------------------------------------

HOSTILE_QUOTE = '<script>alert(1)</script> We are hiring <b hx-get="/x">now</b>'


def evidence_row(world: World, lead: str, created: int, **values: Any) -> str:
    """An Evidence row of ``lead`` created ``created`` minutes after ``T``."""
    row: dict[str, Any] = {
        "lead_id": lead,
        "evidence_type": "quote",
        "quote": None,
        "sought": None,
        "number": None,
        "fact": None,
        "value": None,
        "pages_checked": ("https://smith-law.co.uk/team",),
        "source": "website",
        "source_url": "https://smith-law.co.uk/team",
        "retrieved_at": T,
        "extracted_by": "ai:extract_signals@v1",
        "person_contact_id": None,
        "at": T + timedelta(minutes=created),
    }
    row.update(values)
    return world.transact(lambda: signals_repo.insert_evidence(**row))


def stored_signal(world: World, lead: str, evidence_id: str, **values: Any) -> str:
    row: dict[str, Any] = {
        "lead_id": lead,
        "signal_class": "event",
        "score_only": False,
        "event_date": None,
        "evidence_ids": [evidence_id],
    }
    row.update(values)
    return world.transact(lambda: signals_repo.insert_signal(**row))


def signalled_lead(world: World) -> str:
    """A Lead with a Tier 1 Event first seen 31 days ago, a Tier 2 Event dated 91 days ago, a
    score-only Attribute with Absence Evidence, a location fact, a Signal type its Segment no
    longer lists, and its headquarters zone."""
    lead = world.lead("Smith Law", "smith-law.co.uk")
    hiring = evidence_row(world, lead, 1, quote=HOSTILE_QUOTE, retrieved_at=T - timedelta(days=31))
    opened = evidence_row(world, lead, 2, quote="We opened in Manchester on 18 August 2026.")
    absent = evidence_row(
        world,
        lead,
        3,
        evidence_type="absence",
        sought="chat widget, booking link or after-hours line",
        pages_checked=("https://smith-law.co.uk/", "https://smith-law.co.uk/contact"),
        source_url=None,
        extracted_by="rule",
    )
    location = evidence_row(
        world, lead, 4, quote="Our head office is in Chicago, IL.", fact="location", value="Chicago"
    )
    evidence_row(world, lead, 5, quote="We employ 1,200 staff.", fact="staff", number=1200)
    stored_signal(
        world,
        lead,
        opened,
        signal_type="law_expansion",
        tier=2,
        event_date=(T - timedelta(days=91)).date(),
        first_seen_at=T - timedelta(days=100),
    )
    stored_signal(
        world,
        lead,
        hiring,
        signal_type="law_hiring_legal_ops",
        tier=1,
        first_seen_at=T - timedelta(days=31),
    )
    stored_signal(
        world,
        lead,
        absent,
        signal_type="law_contact_form_only",
        signal_class="attribute",
        tier=3,
        score_only=True,
        first_seen_at=T - timedelta(days=5),
    )
    retired = evidence_row(world, lead, 6, quote="We are hiring.")
    stored_signal(
        world, lead, retired, signal_type="law_retired_type", tier=3, first_seen_at=T
    )
    evidence_row(
        world,
        lead,
        7,
        evidence_type="count",
        sought="matters listed on the practice pages",
        number=1250,
        pages_checked=("https://smith-law.co.uk/practice", "https://smith-law.co.uk/news"),
        source_url=None,
        extracted_by="rule",
    )
    world.transact(lambda: profile_repo.set_headquarters(lead, "America/Chicago", location))
    return lead


def test_the_signals_section_shows_each_current_signal(world: World) -> None:
    lead = signalled_lead(world)

    page = world.page(f"/leads/{lead}")

    header = page.find("header", class_="lead-header")
    facts = [node.text for node in header.find("dl").walk() if node.tag in ("dt", "dd")]
    assert facts[6:8] == ["HQ timezone", "America/Chicago"]
    section = page.find("section", class_="lead-signals")
    assert "hx-disable" in section.attrs
    assert table_rows(section.find("table", class_="signal-table")) == [
        SIGNAL_HEADER,
        [
            "New office, merger, or practice-area expansion",
            "Event",
            "Tier 2",
            "2026-08-18",
            "91 days",
            "Expired",
            "",
        ],
        [
            "Hiring intake, legal-ops, knowledge-management, or innovation staff",
            "Event",
            "Tier 1",
            "first seen 2026-10-17",
            "31 days",
            "Live",
            "",
        ],
        [
            "Contact form only (no chat, booking, or after-hours line)",
            "Attribute",
            "Tier 3",
            "first seen 2026-11-12",
            "5 days",
            "Live",
            "Score only",
        ],
        ["law_retired_type", "Event", "Tier 3", "first seen 2026-11-17", "0 days", "Live", ""],
    ]


def test_the_evidence_section_shows_each_live_row(world: World) -> None:
    lead = signalled_lead(world)

    page = world.page(f"/leads/{lead}")

    section = page.find("section", class_="lead-evidence")
    assert "hx-disable" in section.attrs
    table = section.find("table", class_="evidence-table")
    rows = table_rows(table)
    hiring = "Hiring intake, legal-ops, knowledge-management, or innovation staff"
    assert rows[0] == EVIDENCE_HEADER
    assert rows[1] == [
        "Quote",
        HOSTILE_QUOTE,
        hiring,
        "https://smith-law.co.uk/team",
        "2026-10-17 09:00 UTC",
        "AI-extracted",
    ]
    assert rows[2][2:] == [
        "New office, merger, or practice-area expansion",
        "https://smith-law.co.uk/team",
        "2026-11-17 09:00 UTC",
        "AI-extracted",
    ]
    assert rows[3][0] == "Absence"
    assert rows[3][2:] == [
        "Contact form only (no chat, booking, or after-hours line)",
        "—",
        "2026-11-17 09:00 UTC",
        "—",
    ]
    assert rows[4][1:3] == ["Our head office is in Chicago, IL.", "Location: Chicago"]
    assert rows[5][1:3] == ["We employ 1,200 staff.", "Staff: 1,200"]
    assert rows[6][2] == "law_retired_type"
    count_row = table.find_all("tr", class_="evidence-row")[6]
    assert cells(count_row)[0] == "Count"
    counted = "Counted: matters listed on the practice pages: 1,250"
    assert cells(count_row)[1].startswith(counted)  # then the pages checked, as a list
    assert cells(count_row)[2:] == ["—", "—", "2026-11-17 09:00 UTC", "—"]
    assert not count_row.find_all("q")
    count_links = count_row.find("ul", class_="pages-checked").find_all("a")
    assert [(a.attrs["href"], a.text) for a in count_links] == [
        ("https://smith-law.co.uk/practice", "https://smith-law.co.uk/practice"),
        ("https://smith-law.co.uk/news", "https://smith-law.co.uk/news"),
    ]
    absence = table.find_all("tr", class_="evidence-row")[2]
    sought = "Sought: chat widget, booking link or after-hours line"
    assert cells(absence)[1].startswith(sought)  # then the pages checked, as a list
    links = absence.find("ul", class_="pages-checked").find_all("a")
    assert [(a.attrs["href"], a.attrs["rel"]) for a in links] == [
        ("https://smith-law.co.uk/", "noopener noreferrer"),
        ("https://smith-law.co.uk/contact", "noopener noreferrer"),
    ]
    first = table.find_all("tr", class_="evidence-row")[0]
    assert first.find("a").attrs["href"] == "https://smith-law.co.uk/team"


def test_superseded_and_retracted_evidence_and_retracted_signals_are_not_shown(
    world: World,
) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    old = evidence_row(world, lead, 1, quote="Old quote.")
    new = evidence_row(world, lead, 2, quote="New quote.")
    gone = evidence_row(world, lead, 3, quote="Gone quote.")
    world.transact(lambda: signals_repo.supersede_evidence(old, new))
    world.transact(lambda: signals_repo.retract_evidence(gone, T))
    stored_signal(world, lead, gone, signal_type="law_expansion", tier=2, first_seen_at=T)
    world.transact(lambda: signals_repo.retract_unsupported(lead, T))

    page = world.page(f"/leads/{lead}")

    assert page.find("section", class_="lead-signals").text == "Signals No Signals yet."
    rows = table_rows(page.find("table", class_="evidence-table"))
    assert [row[1] for row in rows[1:]] == ["New quote."]


def test_the_signal_and_evidence_text_is_escaped_inside_hx_disable(world: World) -> None:
    lead = signalled_lead(world)

    reply = world.get(f"/leads/{lead}")

    assert reply.status == 200
    for raw in ("<script>alert", '<b hx-get="/x">'):
        assert raw not in reply.text
    tree = Tree(reply.text)
    assert tree.disabled(HOSTILE_QUOTE) == [True]
    assert [node.attrs.get("src") for node in tree.find_all("script")] == [
        "/static/htmx-2.0.11.min.js"
    ]
    assert not tree.find_all("b")
    for name in ("lead-signals", "lead-evidence"):  # neither section holds an HTMX action
        section = tree.find("section", class_=name)
        for node in (section, *section.walk()):
            assert not [key for key in node.attrs if key.startswith("hx-") and key != "hx-disable"]
        assert not section.find_all("form") and not section.find_all("button")


def test_every_signal_class_evidence_type_and_fact_kind_has_a_label() -> None:
    assert list(CLASS_LABELS) == list(SignalClass)
    assert list(EVIDENCE_TYPE_LABELS) == list(EvidenceType)
    assert list(FACT_LABELS) == list(FactKind)
    assert leads_page.age_text(1) == "1 day"
    assert leads_page.age_text(0) == "0 days"
    assert leads_page.age_text(1200) == "1,200 days"


# Live Eligibility (Story 3.6) ------------------------------------------------------------------


def sync_markets(world: World, *changes: Callable[[MarketDef], MarketDef]) -> None:
    """Store the shipped Markets, each changed by ``changes`` in turn."""
    markets = list(validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8"))))
    for change in changes:
        markets = [change(market) for market in markets]
    with world.units.open():
        Setup(world.clock).sync_markets(markets)


def eligibility(row: Node) -> tuple[str, list[tuple[str | None, str]]]:
    """An ``eligibility-row``'s summary and its reasons as (``data-reason``, text)."""
    summary = row.find("p", class_="eligibility-summary").text
    items = [(item.attrs.get("data-reason"), item.text) for item in row.find_all("li")]
    return summary, items


def contact_table_rows(page: Tree) -> list[str]:
    """The class of each body row of the Contacts table itself, in order."""
    table = page.find("table", class_="contact-table")
    return [
        row.attrs.get("class") or ""
        for row in table.find_all("tr")
        if next(node for node in row.ancestors() if node.tag == "table") is table
    ][1:]


def test_each_contact_shows_its_live_eligibility_under_its_row(world: World) -> None:
    sync_markets(world)
    lead = world.lead("Acme Law", "acmelaw.co.uk")
    world.add(
        lead,
        ContactCandidate(name="Zelda Quill", email="zelda@acmelaw.co.uk"),
        source=ProvenanceSource.CSV,
    )
    us_lead = world.lead("Acme US", "acmelaw.com", minutes=1, country="US")
    world.add(us_lead, ContactCandidate(email="info@acmelaw.com"), source=ProvenanceSource.WEBSITE)

    uk_page = world.page(f"/leads/{lead}")
    us_page = world.page(f"/leads/{us_lead}")

    assert contact_table_rows(uk_page) == ["contact-row", "eligibility-row", "provenance-row"]
    [uk_row] = uk_page.find_all("tr", class_="eligibility-row")
    assert eligibility(uk_row) == (
        "Not eligible",
        [
            (
                "uk_legal_form_unconfirmed",
                "The Lead's legal form isn't confirmed: no Evidence links the Lead to its "
                "Companies House record",
            ),
            (
                "address_not_sendable",
                "The address is Guessed; only Published or Verified addresses can be emailed",
            ),
        ],
    )
    [us_row] = us_page.find_all("tr", class_="eligibility-row")
    assert eligibility(us_row) == ("Eligible: US: cold email allowed (CAN-SPAM)", [])
    assert not us_row.find_all("ul")
    assert summary_class(uk_row) == "eligibility-summary not-eligible"
    assert summary_class(us_row) == "eligibility-summary eligible"
    for row in (uk_row, us_row):
        cell = row.find("td")
        assert cell.attrs["colspan"] == "8"  # the Contacts panel's eight columns (2.10, 3.13)
        assert "hx-disable" in cell.attrs  # each untrusted cell carries it (Story 2.10)
        table = next(node for node in row.ancestors() if node.tag == "table")
        assert "contact-table" in (table.attrs.get("class") or "")


def summary_class(row: Node) -> str:
    return row.find("p", class_="eligibility-summary").attrs.get("class") or ""


def test_each_contact_on_one_lead_shows_its_own_decision(world: World) -> None:
    sync_markets(world)
    lead = world.lead("Acme US", "acmelaw.com", country="US")
    world.add(lead, ContactCandidate(email="info@acmelaw.com"), source=ProvenanceSource.WEBSITE)
    world.add(
        lead,
        ContactCandidate(name="Ann Jones", email="ann@acmelaw.com"),
        source=ProvenanceSource.CSV,
        seconds=1,
    )
    world.add(lead, ContactCandidate(name="Bo Smith"), seconds=2)

    page = world.page(f"/leads/{lead}")

    assert contact_table_rows(page) == ["contact-row", "eligibility-row", "provenance-row"] * 3
    contacts = page.find_all("tr", class_="contact-row")
    assert [cells(row)[:3] for row in contacts] == [
        ["—", "—", "info@acmelaw.com"],
        ["Ann Jones", "—", "ann@acmelaw.com"],
        ["Bo Smith", "—", "—"],
    ]
    rows = page.find_all("tr", class_="eligibility-row")
    assert [eligibility(row) for row in rows] == [
        ("Eligible: US: cold email allowed (CAN-SPAM)", []),
        (
            "Not eligible",
            [
                (
                    "address_not_sendable",
                    "The address is Guessed; only Published or Verified addresses can be emailed",
                )
            ],
        ),
        ("Not eligible", [("address_not_sendable", "The Contact has no email address")]),
    ]
    assert [summary_class(row) for row in rows] == [
        "eligibility-summary eligible",
        "eligibility-summary not-eligible",
        "eligibility-summary not-eligible",
    ]


def test_a_contact_without_a_market_shows_why(world: World) -> None:
    lead = world.lead("Jones Law", "jones.co.uk")
    world.add(lead, ContactCandidate(name="Ann Jones"))

    [row] = world.page(f"/leads/{lead}").find_all("tr", class_="eligibility-row")

    summary, items = eligibility(row)
    assert summary == "Not eligible"
    assert [code for code, _ in items] == ["no_enabled_market", "address_not_sendable"]
    assert items[1][1] == "The Contact has no email address"


def test_the_eligibility_is_recomputed_on_every_render_and_stored_nowhere(world: World) -> None:
    sync_markets(world)
    lead = world.lead("Acme US", "acmelaw.com", country="US")
    contact = world.add(
        lead,
        ContactCandidate(name="Zelda Quill", email="zelda@acmelaw.com"),
        source=ProvenanceSource.WEBSITE,
    )
    tables = [
        name
        for (name,) in world.sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]

    def counts() -> dict[str, int]:
        return {name: world.sql(f'SELECT COUNT(*) FROM "{name}"')[0][0] for name in tables}

    before = counts()
    first = world.page(f"/leads/{lead}").find("tr", class_="eligibility-row")
    again = world.page(f"/leads/{lead}").find("tr", class_="eligibility-row")
    assert counts() == before
    world.stage(lead, PipelineStage.NOT_NOW)
    second = world.page(f"/leads/{lead}").find("tr", class_="eligibility-row")
    world.stage(lead, PipelineStage.NEW)
    world.transact(
        lambda: Compliance(world.clock).suppress(
            ContactSubject(contact), SuppressionScope.PERSON, SuppressionSource.OPERATOR, None
        )
    )
    third = world.page(f"/leads/{lead}").find("tr", class_="eligibility-row")
    world.stage(lead, PipelineStage.LOST)
    fourth = world.page(f"/leads/{lead}").find("tr", class_="eligibility-row")

    assert eligibility(first) == eligibility(again)
    assert eligibility(first) == ("Eligible: US: cold email allowed (CAN-SPAM)", [])
    assert eligibility(second) == (
        "Not eligible",
        [("not_now_undated", "Parked Not Now with no re-contact date recorded")],
    )
    assert [code for code, _ in eligibility(third)[1]] == [
        "address_or_domain_suppressed",
        "asked_not_to_be_contacted",
    ]
    assert [code for code, _ in eligibility(fourth)[1]] == [
        "address_or_domain_suppressed",
        "asked_not_to_be_contacted",
        "lead_closed",
    ]


def test_a_market_name_in_a_reason_is_escaped_inside_hx_disable(world: World) -> None:
    hostile = "<i>Britain</i>"

    def rename(market: MarketDef) -> MarketDef:
        return replace(market, name=hostile) if market.code == "uk" else market

    sync_markets(world, rename)
    lead = world.lead("Acme Law", "acmelaw.co.uk")
    world.add(lead, ContactCandidate(email="zelda@gmail.com"), source=ProvenanceSource.WEBSITE)

    reply = world.get(f"/leads/{lead}")

    assert reply.status == 200
    assert hostile not in reply.text
    assert "&lt;i&gt;Britain&lt;/i&gt;" in reply.text
    page = Tree(reply.text)
    assert not page.find_all("i")
    # the reason, then (Story 3.7) the Market fact and the timezone note
    assert page.disabled("Britain") == [True, True, True]
    [row] = page.find_all("tr", class_="eligibility-row")
    assert [code for code, _ in eligibility(row)[1]] == [
        "uk_legal_form_unconfirmed",
        "uk_free_webmail",
    ]


def test_an_eligibility_row_carries_each_reasons_code() -> None:
    until = Reason(ReasonKind.NOT_NOW_UNTIL, "Parked Not Now until 2027-01-15", date(2027, 1, 15))
    closed = Reason(ReasonKind.LEAD_CLOSED, "The Lead is closed (Lost)")

    row = leads_page.eligibility_row(Decision(False, None, (until, closed)))
    passing = leads_page.eligibility_row(Decision(True, "US: cold email allowed (CAN-SPAM)", ()))

    assert row.eligible is False
    assert row.summary == "Not eligible"
    assert row.reasons == (
        leads_page.ReasonRow("not_now_until_2027-01-15", "Parked Not Now until 2027-01-15"),
        leads_page.ReasonRow("lead_closed", "The Lead is closed (Lost)"),
    )
    assert passing == leads_page.EligibilityRow(
        True, "Eligible: US: cold email allowed (CAN-SPAM)", ()
    )
    with pytest.raises(TypeError):
        leads_page.eligibility_row(None)  # type: ignore[arg-type]


# Route rules, nav and labels --------------------------------------------------------------------


def test_without_a_session_both_pages_are_locked(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")

    for path in ("/leads", f"/leads/{lead}"):
        reply = world.dashboard.get(path)
        assert reply.status == 401
        assert "Smith Law" not in reply.text


def test_both_routes_are_gets_declaring_personal_data(world: World) -> None:
    routes = {
        getattr(route, "name", None): route
        for route in world.dashboard.app.routes
        if getattr(route, "name", None) in ("pipeline_page", "lead_page", "lead_import_page")
    }

    assert getattr(routes["pipeline_page"], "path") == "/leads"
    assert getattr(routes["lead_page"], "path") == "/leads/{lead_id}"
    for name in ("pipeline_page", "lead_page"):
        assert getattr(routes[name], "methods") == {"GET"}
        endpoint = getattr(routes[name], "endpoint")
        assert getattr(endpoint, "__nextron_personal_data__") is True
    paths = [getattr(route, "path", None) for route in world.dashboard.app.routes]
    assert paths.index("/leads/import") < paths.index("/leads/{lead_id}")


def test_the_import_page_is_still_the_import_page(world: World) -> None:
    page = world.page("/leads/import")

    assert page.find("h1").text == "Import Leads from a CSV file"
    assert world.dashboard.get("/leads/import", cookie=world.cookie).status == 200


def test_the_nav_links_the_leads_list(world: World) -> None:
    page = world.page("/leads")

    nav = page.find("nav", **{"aria-label": "Sections"})
    links = [(a.attrs["href"], a.text) for a in nav.find_all("a")]
    assert links == [
        ("/leads", "Leads/Pipeline"),
        ("/settings", "Settings"),
        ("/health/panel", "Health"),
    ]


def test_segment_options_are_by_name_whatever_the_key_order() -> None:
    shipped = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))[0]
    segments = [
        SeededSegment(f"0{n}" + "0" * 24, key, 1, replace(shipped, key=key, name=name))
        for n, (key, name) in enumerate([("alpha", "Zeta"), ("mid", "alpha"), ("zulu", "Beta")])
    ]

    options = leads_page.segment_options(segments, "zulu")

    assert [(o.value, o.label, o.selected) for o in options] == [
        ("mid", "alpha", False),
        ("zulu", "Beta", True),
        ("alpha", "Zeta", False),
    ]
    assert not any(o.selected for o in leads_page.segment_options(segments, None))


def test_every_stage_and_source_has_a_label() -> None:
    assert list(STAGE_LABELS) == list(PipelineStage)
    assert list(SOURCE_LABELS) == list(ProvenanceSource)
    assert STAGE_LABELS[PipelineStage.CALL_BOOKED] == "Call booked"
    assert SOURCE_LABELS[ProvenanceSource.OPERATOR] == "Manual (entered by Mac)"  # Story 3.7
    assert leads_page.stage_label("retired") == "retired"  # an unknown stored value
    assert leads_page.source_label("carrier_pigeon") == "carrier_pigeon"


def test_the_view_module_reads_only_through_queries() -> None:
    tree = ast.parse((PACKAGE / "web" / "leads_page.py").read_text(encoding="utf-8"))
    modules = [
        name
        for node in ast.walk(tree)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    ]

    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert app_modules  # not vacuous
    assert all(name.startswith("nextron.app.queries.") for name in app_modules), app_modules
    assert not [name for name in modules if name.startswith("nextron.db")]


# The Primary Contact (Story 2.10) ---------------------------------------------------------------

HOSTILE_NAME = '<script>alert(1)</script> Cy <b hx-get="/x">Ceo</b>'


def set_primary(world: World, contact: str, set_by: str) -> None:
    world.sql(
        "UPDATE contacts SET is_primary = 1, primary_set_by = ? WHERE id = ?", (set_by, contact)
    )


def primary_paragraphs(page: Tree) -> list[Node]:
    return page.find_all("p", **{"class": "primary-contact"})


def make_primary_forms(page: Tree) -> list[Node]:
    panel = page.find("section", id="lead-contacts")
    return panel.find_all("form")


def post_primary(world: World, lead: str, contact: str, *, token: bool = True) -> Reply:
    return world.dashboard.post(
        f"/leads/{lead}/primary",
        cookie=world.cookie,
        token=world.dashboard.csrf_token(world.cookie) if token else None,
        headers=[(b"content-type", FORM)],
        body=form(contact_id=contact),
    )


def flags(world: World, lead: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT name, is_primary, primary_set_by FROM contacts WHERE lead_id = ? "
        "ORDER BY created_at, id",
        (lead,),
    )


def test_the_operators_primary_line_and_the_make_primary_forms(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    ceo = world.add(lead, ContactCandidate(name="Cy Ceo", role="Managing Partner"))
    other = world.add(lead, ContactCandidate(name="Ann Fox", role="Owner"), seconds=1)
    set_primary(world, ceo, "operator")

    page = world.page(f"/leads/{lead}")

    [line] = primary_paragraphs(page)
    assert line.text == "Primary Contact: Cy Ceo (chosen by you)"
    assert "hx-disable" in line.attrs
    panel = page.find("section", id="lead-contacts")
    assert panel.attrs["class"] == "lead-contacts" and "hx-disable" not in panel.attrs
    rows = page.find_all("tr", class_="contact-row")
    assert [cells(row)[5] for row in rows] == ["Yes", "No"]
    # the operator's Primary has no form; every other row has one
    [form_node] = make_primary_forms(page)
    assert form_node.attrs == {
        "method": "post",
        "action": f"/leads/{lead}/primary",
        "hx-post": f"/leads/{lead}/primary",
        "hx-target": "#lead-contacts",
        "hx-swap": "outerHTML",
        "hx-sync": "this:drop",
    }
    inputs = {node.attrs["name"]: node.attrs for node in form_node.find_all("input")}
    assert inputs["contact_id"] == {"type": "hidden", "name": "contact_id", "value": other}
    assert inputs["csrf_token"]["type"] == "hidden"
    assert form_node.find("button").text == "Make primary"
    # no form sits inside an element that turns HTMX off, and every Contact cell does
    assert not any("hx-disable" in node.attrs for node in form_node.ancestors())
    for row in rows:
        data = [cell for cell in row.children if isinstance(cell, Node) and cell.tag == "td"]
        # Primary, the form and (Story 3.13) the Erasure cell, which holds only the erase link
        assert ["hx-disable" in cell.attrs for cell in data] == [True] * 5 + [False] * 3


def test_the_engines_primary_line_keeps_its_form(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    jane = world.add(lead, ContactCandidate(name="Jane Roe", role="Managing Partner"))
    set_primary(world, jane, "system")

    page = world.page(f"/leads/{lead}")

    [line] = primary_paragraphs(page)
    assert line.text == "Primary Contact: Jane Roe (chosen by the engine)"
    [form_node] = make_primary_forms(page)
    assert {n.attrs["name"]: n.attrs.get("value") for n in form_node.find_all("input")}[
        "contact_id"
    ] == jane


def test_a_contact_with_an_invalid_address_has_no_make_primary_form(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    world.add(lead, ContactCandidate(name="Cy Ceo", email="cy@smith-law.co.uk"))
    jane = world.add(lead, ContactCandidate(name="Jane Roe", role="Owner"), seconds=1)
    world.sql("UPDATE contacts SET address_class = 'invalid' WHERE name = 'Cy Ceo'")

    page = world.page(f"/leads/{lead}")

    [form_node] = make_primary_forms(page)  # the Invalid address's row has none: always refused
    values = {n.attrs["name"]: n.attrs.get("value") for n in form_node.find_all("input")}
    assert values["contact_id"] == jane
    first, _ = page.find_all("tr", class_="contact-row")
    assert cells(first)[6] == "Channel permissions"  # Story 10.7's link only


def test_no_decision_maker_found_is_shown_only_after_a_selection_found_none(
    world: World,
) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    world.add(lead, ContactCandidate(name="Pat Moe", role="Paralegal"))

    assert primary_paragraphs(world.page(f"/leads/{lead}")) == []  # never selected

    world.transact(lambda: profile_repo.set_no_decision_maker(lead, True))
    [line] = primary_paragraphs(world.page(f"/leads/{lead}"))
    assert line.text == "Primary Contact: no decision-maker found"

    world.transact(lambda: profile_repo.set_no_decision_maker(lead, False))
    assert primary_paragraphs(world.page(f"/leads/{lead}")) == []


def test_make_primary_answers_the_panel_with_a_notice(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    world.add(lead, ContactCandidate(name="Jane Roe", role="Managing Partner"))
    owner = world.add(lead, ContactCandidate(name="Ann Fox", role="Owner"), seconds=1)

    reply = post_primary(world, lead, owner)

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    panel = Tree(reply.text)
    [section] = panel.find_all("section")
    assert section.attrs["id"] == "lead-contacts"
    assert panel.find("p", role="status").text == "Primary Contact set."
    [line] = primary_paragraphs(panel)
    assert line.text == "Primary Contact: Ann Fox (chosen by you)"
    assert flags(world, lead) == [("Jane Roe", 0, None), ("Ann Fox", 1, "operator")]
    assert not panel.find_all("nav")  # the panel alone, not the page
    # Story 3.6: the panel answered shows each Contact's live Eligibility too (no email here)
    rows = panel.find_all("tr", class_="eligibility-row")
    assert [eligibility(row)[0] for row in rows] == ["Not eligible", "Not eligible"]
    assert all(
        "address_not_sendable" in [code for code, _ in eligibility(row)[1]] for row in rows
    )
    assert world.sql(
        "SELECT to_status, cause, cause_ref, actor FROM events WHERE entity = 'leads' "
        "AND entity_id = ? AND to_status LIKE 'primary_%'",
        (lead,),
    ) == [("primary_contact_set", "operator", owner, "operator")]


def test_each_refusal_answers_the_panel_with_its_notice_and_writes_nothing(
    world: World,
) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    jane = world.add(lead, ContactCandidate(name="Jane Roe", role="Managing Partner"))
    invalid = world.add(
        lead, ContactCandidate(name="Cy Ceo", email="cy@smith-law.co.uk"), seconds=1
    )
    world.sql("UPDATE contacts SET address_class = 'invalid' WHERE id = ?", (invalid,))
    other = world.lead("Jones Law", "jones-law.co.uk", minutes=1)
    elsewhere = world.add(other, ContactCandidate(name="Bo Day", role="Owner"))
    merged = world.lead("Smith Law Two", "smith-law-two.co.uk", minutes=2)
    world.merge(lead, merged)
    before = (flags(world, lead), world.sql("SELECT COUNT(*) FROM events"))

    for target, contact, notice in [
        (lead, elsewhere, "That Contact isn't on this Lead."),
        (lead, "not-a-ulid", "That Contact isn't on this Lead."),
        (lead, invalid, "That Contact's address is Invalid."),
        (merged, jane, "This Lead was merged into another Lead."),
    ]:
        reply = post_primary(world, target, contact)
        assert reply.status == 200, reply
        alert = Tree(reply.text).find("p", role="alert")
        assert alert.text == notice
        assert "notice-refused" in alert.attrs["class"]

    assert (flags(world, lead), world.sql("SELECT COUNT(*) FROM events")) == before


@pytest.mark.parametrize("lead", [UNKNOWN, "not-a-ulid"])
def test_make_primary_on_an_unknown_lead_is_404_without_echoing_the_id(
    world: World, lead: str
) -> None:
    known = world.lead("Smith Law", "smith-law.co.uk")
    jane = world.add(known, ContactCandidate(name="Jane Roe", role="Managing Partner"))

    reply = post_primary(world, lead, jane)

    assert reply.status == 404
    assert reply.text == "That Lead doesn't exist."
    assert lead not in reply.text and jane not in reply.text
    assert flags(world, known) == [("Jane Roe", 0, None)]


def test_make_primary_needs_the_csrf_token_and_exactly_its_field(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    jane = world.add(lead, ContactCandidate(name="Jane Roe", role="Managing Partner"))
    token = world.dashboard.csrf_token(world.cookie)

    assert post_primary(world, lead, jane, token=False).status == 403
    for body in (
        form(contact_id=jane, extra="x"),
        b"contact_id=" + jane.encode() + b"&contact_id=" + jane.encode(),
        b"",
    ):
        reply = world.dashboard.post(
            f"/leads/{lead}/primary",
            cookie=world.cookie,
            token=token,
            headers=[(b"content-type", FORM)],
            body=body,
        )
        assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert flags(world, lead) == [("Jane Roe", 0, None)]


def test_the_make_primary_route_is_a_post_declaring_personal_data(world: World) -> None:
    [route] = [
        route
        for route in world.dashboard.app.routes
        if getattr(route, "name", None) == "lead_make_primary"
    ]

    assert getattr(route, "path") == "/leads/{lead_id}/primary"
    assert getattr(route, "methods") == {"POST"}
    assert getattr(getattr(route, "endpoint"), "__nextron_personal_data__") is True


def test_a_hostile_primary_name_is_escaped_inside_hx_disable(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    ceo = world.add(lead, ContactCandidate(name=HOSTILE_NAME, role="Managing Partner"))
    set_primary(world, ceo, "operator")

    reply = world.get(f"/leads/{lead}")

    assert "<script>alert" not in reply.text and '<b hx-get="/x">' not in reply.text
    page = Tree(reply.text)
    # the Primary line, the Contact row and (Story 3.15) its export form
    assert page.disabled("alert(1)") == [True, True, True]


def test_a_person_evidence_row_supports_its_contact(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    jane = world.add(lead, ContactCandidate(name=HOSTILE_NAME, role="Managing Partner"))
    evidence_row(
        world,
        lead,
        1,
        quote="Jane Roe, Managing Partner",
        extracted_by="ai:extract_people@v1",
        person_contact_id=jane,
    )

    page = world.page(f"/leads/{lead}")

    [row] = page.find_all("tr", class_="evidence-row")
    assert cells(row)[2] == f"Contact: {HOSTILE_NAME}"
    assert page.disabled("Contact: <script>") == [True]


# Job postings (Story 2.11) -----------------------------------------------------------------------

POSTING_HEADER = ["Title", "Source", "Posted", "First seen", "Status"]
HOSTILE_TITLE = '<b hx-get="/x">Legal Operations</b> Manager <script>alert(1)</script>'


def stored_posting(
    world: World,
    lead: str,
    title: str,
    *,
    source: str = "greenhouse",
    url: str = "https://boards.greenhouse.io/smith/jobs/1",
    posted: date | None = None,
    days_ago: int = 0,
    closed: bool = False,
) -> str:
    """A ``job_postings`` row written through the repository, first seen ``days_ago``."""

    def write() -> str:
        seen = T - timedelta(days=days_ago)
        posting_id = postings_repo.insert_posting(
            lead_id=lead,
            source=source,
            board_token=None if source == "careers_page" else "smith",
            url=url,
            title=title,
            description=None,
            posted_date=posted,
            checked_at=seen,
        )
        if closed:
            postings_repo.close(posting_id, T)
        return posting_id

    return world.transact(write)


def test_the_job_postings_section_shows_live_postings_first_then_the_newest(
    world: World,
) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    stored_posting(world, lead, "Intake Coordinator", days_ago=20, closed=True)
    stored_posting(
        world,
        lead,
        "Legal Operations Manager",
        source="careers_page",
        url="https://smith-law.co.uk/careers",
        posted=date(2026, 11, 10),
        days_ago=8,
    )
    stored_posting(world, lead, "Paralegal", source="lever", days_ago=2, closed=True)
    stored_posting(world, lead, "Knowledge Lawyer", source="ashby", days_ago=30)

    page = world.page(f"/leads/{lead}")

    section = page.find("section", class_="lead-postings")
    assert "hx-disable" in section.attrs
    assert table_rows(section.find("table", class_="posting-table")) == [
        POSTING_HEADER,
        ["Legal Operations Manager", "Careers page", "2026-11-10", "2026-11-09", "Live"],
        ["Knowledge Lawyer", "Ashby", "—", "2026-10-18", "Live"],
        ["Paralegal", "Lever", "—", "2026-11-15", "Closed"],
        ["Intake Coordinator", "Greenhouse", "—", "2026-10-28", "Closed"],
    ]
    for node in (section, *section.walk()):  # the section holds no HTMX action
        assert not [key for key in node.attrs if key.startswith("hx-") and key != "hx-disable"]


def test_a_lead_with_no_job_postings_says_so(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")

    page = world.page(f"/leads/{lead}")

    section = page.find("section", class_="lead-postings")
    assert section.text == "Job postings No job postings yet."


def test_a_hostile_posting_title_is_escaped_inside_hx_disable(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    stored_posting(world, lead, HOSTILE_TITLE)
    stored_posting(world, lead, f"Old {HOSTILE_TITLE}", days_ago=3, closed=True)

    reply = world.get(f"/leads/{lead}")

    assert reply.status == 200
    for raw in ("<script>alert", '<b hx-get="/x">'):
        assert raw not in reply.text
    tree = Tree(reply.text)
    assert tree.disabled("alert(1)") == [True, True]
    assert not tree.find_all("b")
    rows = table_rows(tree.find("table", class_="posting-table"))
    assert [row[0] for row in rows[1:]] == [HOSTILE_TITLE, f"Old {HOSTILE_TITLE}"]


# The UK legal form (Story 2.12) -----------------------------------------------------------------

CH_PAGE = "https://find-and-update.company-information.service.gov.uk/company/"
SITE_QUOTE = '<script>alert(1)</script> registered in England, company number <b hx-get="/x">'
REGISTRY_QUOTE = (
    "Companies House record 01234567: company type ltd, company status active, registered office "
    "postcode EC1A 1BB, registered name <i>SMITH LAW LIMITED</i>"
)
NOTE = "This Lead stays researchable; UK cold email waits for a confirmed corporate legal form."


def legal_form_lead(
    world: World,
    legal_form: str | None,
    company_status: str | None,
    status: str | None,
    *,
    ch_number: str | None = "01234567",
) -> tuple[str, str, str]:
    """A GB Lead with its site and registry Evidence and the legal form pointing to them."""
    lead = world.lead("Smith Law", "smith-law.co.uk", ch_number=ch_number)
    site = evidence_row(world, lead, 1, quote=SITE_QUOTE, extracted_by="rule")
    registry = evidence_row(
        world,
        lead,
        2,
        quote=REGISTRY_QUOTE,
        source="registry",
        source_url=CH_PAGE + "01234567",
        pages_checked=(CH_PAGE + "01234567",),
        extracted_by="rule",
    )
    stored = profile_repo.StoredLegalForm(legal_form, company_status, status, site, registry)
    world.transact(lambda: profile_repo.set_legal_form(lead, stored))
    return lead, site, registry


def legal_form_section(page: Tree) -> Node:
    return page.find("section", class_="lead-legal-form")


def legal_form_facts(section: Node) -> list[str]:
    return [node.text for node in section.find("dl").walk() if node.tag in ("dt", "dd")]


def test_a_gb_lead_never_checked_says_not_checked_yet(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")

    page = world.page(f"/leads/{lead}")

    section = legal_form_section(page)
    assert "hx-disable" in section.attrs
    assert section.find("p", class_="legal-form-status").text == "Not checked yet"
    assert section.find("p", class_="legal-form-note").text == NOTE
    assert legal_form_facts(section) == [
        "Legal form",
        "—",
        "Company status",
        "—",
        "Company number",
        "—",
    ]
    assert not section.find_all("ul") and not section.find_all("a")
    order = [
        node
        for node in page.root.walk()
        if node.tag == "header" and "lead-header" in (node.attrs.get("class") or "")
        or node is section
        or node.tag == "section" and "lead-signals" in (node.attrs.get("class") or "")
    ]
    assert [node.tag for node in order] == ["header", "section", "section"]
    assert order[1] is section  # right after the header, before the Signals


@pytest.mark.parametrize("country", ["US", None])
def test_a_lead_outside_gb_has_no_legal_form_section(world: World, country: str | None) -> None:
    lead = world.lead("Smith Law", "smith-law.com", country=country)

    page = world.page(f"/leads/{lead}")

    assert page.find_all("section", class_="lead-legal-form") == []
    assert NOTE not in page.text


def test_a_confirmed_lead_shows_its_legal_form_its_number_linked_and_its_evidence(
    world: World,
) -> None:
    lead, _, _ = legal_form_lead(world, "ltd", "active", "confirmed")

    reply = world.get(f"/leads/{lead}")

    assert reply.status == 200
    for raw in ("<script>alert", '<b hx-get="/x">', "<i>SMITH"):
        assert raw not in reply.text
    page = Tree(reply.text)
    section = legal_form_section(page)
    assert section.find("p", class_="legal-form-status").text == "Confirmed: corporate subscriber"
    assert not section.find_all("p", class_="legal-form-note")
    assert legal_form_facts(section) == [
        "Legal form",
        "private limited company (ltd)",
        "Company status",
        "active",
        "Company number",
        "01234567",
    ]
    number = section.find("dl").find("a")
    assert (number.attrs["href"], number.text, number.attrs["rel"]) == (
        CH_PAGE + "01234567",
        "01234567",
        "noopener noreferrer",
    )
    items = section.find("ul", class_="legal-form-evidence").find_all("li")
    assert [item.find("span", class_="legal-form-source").text for item in items] == [
        "Website",
        "Companies House",
    ]
    assert [item.find("q").text for item in items] == [SITE_QUOTE, REGISTRY_QUOTE]
    assert [item.find("a").attrs["href"] for item in items] == [
        "https://smith-law.co.uk/team",
        CH_PAGE + "01234567",
    ]
    assert page.disabled(SITE_QUOTE) == [True, True]  # the section and the Evidence table
    assert page.disabled("SMITH LAW LIMITED") == [True, True]
    for node in (section, *section.walk()):  # no HTMX action
        assert not [key for key in node.attrs if key.startswith("hx-") and key != "hx-disable"]
    assert not section.find_all("form") and not section.find_all("button")
    rows = table_rows(page.find("table", class_="evidence-table"))
    assert [(row[1], row[2]) for row in rows[1:]] == [
        (SITE_QUOTE, "Legal form"),
        (REGISTRY_QUOTE, "Legal form"),
    ]


@pytest.mark.parametrize(
    ("legal_form", "company_status", "status", "text", "shown"),
    [
        (
            "limited-partnership",
            "active",
            "not_corporate",
            "Not a corporate subscriber",
            "limited-partnership",
        ),
        ("ltd", "dissolved", "unconfirmed", "Unconfirmed", "private limited company (ltd)"),
        (None, None, "unconfirmed", "Unconfirmed", "—"),
        ("ltd", "active", "something-else", "Unconfirmed", "private limited company (ltd)"),
    ],
)
def test_a_lead_that_isnt_confirmed_says_so_and_stays_researchable(
    world: World,
    legal_form: str | None,
    company_status: str | None,
    status: str,
    text: str,
    shown: str,
) -> None:
    lead, _, _ = legal_form_lead(world, legal_form, company_status, status)

    page = world.page(f"/leads/{lead}")

    section = legal_form_section(page)
    assert section.find("p", class_="legal-form-status").text == text
    assert section.find("p", class_="legal-form-note").text == NOTE
    assert legal_form_facts(section)[1] == shown
    assert legal_form_facts(section)[3] == (company_status or "—")
    listed = world.page("/leads")
    assert "Smith Law" in listed.text  # still on the Leads list


def test_a_pointer_to_a_row_that_isnt_live_shows_no_quote(world: World) -> None:
    lead, site, _ = legal_form_lead(world, "ltd", "active", "confirmed")
    world.transact(lambda: signals_repo.retract_evidence(site, T))

    page = world.page(f"/leads/{lead}")

    items = legal_form_section(page).find("ul", class_="legal-form-evidence").find_all("li")
    assert [item.find("q").text for item in items] == [REGISTRY_QUOTE]


def test_a_lead_without_a_number_shows_a_dash(world: World) -> None:
    lead, _, _ = legal_form_lead(world, None, None, "unconfirmed", ch_number=None)

    section = legal_form_section(world.page(f"/leads/{lead}"))

    assert legal_form_facts(section)[4:] == ["Company number", "—"]
    assert not section.find("dl").find_all("a")


def test_legal_form_view_is_none_outside_gb() -> None:
    lead = StoredLead(
        "01JD3QZ8X5V9K2M7N4P6R8T0AA",
        "Smith Law",
        "smith-law.co.uk",
        "01234567",
        None,
        "01JD3QZ8X5V9K2M7N4P6R8T0AB",
        "US",
        None,
        T,
    )

    assert leads_page.legal_form_view(lead, None, ()) is None
    view = leads_page.legal_form_view(replace(lead, country_code="GB"), None, ())
    assert view is not None
    assert (view.status, view.note, view.legal_form, view.number_url) == (
        "Not checked yet",
        NOTE,
        "—",
        CH_PAGE + "01234567",
    )
