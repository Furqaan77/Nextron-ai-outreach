"""The Leads list and Lead page reads (Story 2.4; FR-51, FR-13, AD-3) on a temporary database at
head with ``FrozenClock`` and the real subscriber registry: ``queries.discovery.lead_list`` (the
filters, the ``LIKE`` escape, the order, the clamp, the count, merged Leads left out, the
argument errors), ``queries.discovery.survivor_of`` (a merge chain, an unknown id, a text that
isn't a ULID, an over-long chain, a loop and a broken chain through raw rows) and
``queries.research.lead_provenance``."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries.discovery import LEADS_PER_PAGE, MAX_MERGE_HOPS, LeadListPage
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.engine import fk_off_connection
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contacts import ContactCandidate, ProvenanceSource, ProvenanceSubject
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
ORIGIN = "Law Society directory, Manchester, 2026"
UNKNOWN = "01KA0000000000000000000000"  # a ULID no Lead has


@dataclass
class World:
    """Discovery and Research on real units of work with the registry ``main`` builds."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
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

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()

    def lead(
        self,
        name: str,
        domain: str,
        *,
        minutes: int = 0,
        segment: str | None = None,
        country: str = "GB",
    ) -> str:
        self.clock.set(T0 + timedelta(minutes=minutes))
        identity = LeadIdentity(
            name, segment or self.law, web_domain=domain, country_code=country
        )
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        self.clock.set(T0)
        return upserted.lead_id

    def merge(self, survivor: str, loser: str) -> None:
        self.transact(lambda: self.discovery.merge(survivor, loser))

    def add(
        self,
        lead_id: str,
        candidate: ContactCandidate,
        *,
        at: datetime = T0,
        source: ProvenanceSource = ProvenanceSource.CSV,
    ) -> str:
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail=ORIGIN, retrieved_at=at
            )
        )
        return added.contact_id

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def stage(self, lead_id: str, stage: PipelineStage) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (str(stage), lead_id))

    def raw_lead(self, merged_into: str | None = None, *, name: str = "Raw") -> str:
        lead_id = new_id()
        domain = None if merged_into else f"{lead_id.lower()}.co.uk"
        self.sql(
            "INSERT INTO leads (id, name, web_domain, segment_id, country_code, merged_into, "
            "created_at) VALUES (?, ?, ?, ?, 'GB', ?, '2026-11-17T09:00:00.000000Z')",
            (lead_id, name, domain, self.law, merged_into),
        )
        return lead_id

    def listed(self, **filters: Any) -> LeadListPage:
        return self.read(lambda: discovery_queries.lead_list(**filters))

    def names(self, **filters: Any) -> list[str]:
        return [lead.name for lead in self.listed(**filters).leads]


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
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    discovery, research = Discovery(clock, Pipeline(clock), queue_on(clock)), Research(clock)
    found = World(units, db_engine, clock, discovery, research, "", "")
    [(law,), (ops,)] = found.sql("SELECT id FROM segments ORDER BY key")
    found.law, found.ops = law, ops
    return found


# lead_list --------------------------------------------------------------------------------------


def test_the_list_is_newest_first_and_leaves_merged_leads_out(world: World) -> None:
    a = world.lead("Smith Law", "smith-law.co.uk", minutes=0)
    b = world.lead("Smith Law LLP", "smithlaw.co.uk", minutes=1)
    c = world.lead("Jones Solicitors", "jones.co.uk", minutes=2, segment=world.ops, country="US")
    world.merge(a, b)

    found = world.listed()

    assert (found.total, found.page, found.pages) == (2, 1, 1)
    assert [lead.id for lead in found.leads] == [c, a]
    newest = found.leads[0]
    assert (newest.name, newest.web_domain, newest.segment_id, newest.country_code) == (
        "Jones Solicitors",
        "jones.co.uk",
        world.ops,
        "US",
    )
    assert newest.pipeline_stage == "new"
    assert newest.created_at == T0 + timedelta(minutes=2)


def test_leads_created_together_are_ordered_by_id_descending(world: World) -> None:
    ids = [world.lead(f"Firm {n}", f"firm{n}.co.uk") for n in range(3)]

    assert [lead.id for lead in world.listed().leads] == sorted(ids, reverse=True)


def test_the_stage_filter_and_every_stage_is_listed(world: World) -> None:
    world.lead("Alpha", "alpha.co.uk", minutes=0)
    b = world.lead("Bravo", "bravo.co.uk", minutes=1)
    c = world.lead("Charlie", "charlie.co.uk", minutes=2)
    world.stage(b, PipelineStage.CONTACTED)
    world.stage(c, PipelineStage.DISQUALIFIED)

    assert world.names() == ["Charlie", "Bravo", "Alpha"]
    assert world.names(stage=PipelineStage.CONTACTED) == ["Bravo"]
    assert world.names(stage=PipelineStage.DISQUALIFIED) == ["Charlie"]
    assert world.names(stage=PipelineStage.NEW) == ["Alpha"]
    assert world.listed(stage=PipelineStage.WON).total == 0


def test_the_segment_filter(world: World) -> None:
    world.lead("Alpha", "alpha.co.uk", minutes=0)
    world.lead("Bravo", "bravo.co.uk", minutes=1, segment=world.ops)

    assert world.names(segment_id=world.ops) == ["Bravo"]
    assert world.names(segment_id=world.law) == ["Alpha"]
    assert world.names(segment_id=new_id()) == []


def test_search_matches_the_name_or_the_domain_ignoring_ascii_case(world: World) -> None:
    world.lead("Smith Law", "smith-law.co.uk", minutes=0)
    world.lead("Jones & Co", "jonesco.com", minutes=1)
    world.lead("Ärzte Partner", "aerzte.de", minutes=2)

    assert world.names(search="SMITH") == ["Smith Law"]
    assert world.names(search="law") == ["Smith Law"]
    assert world.names(search="nesco.c") == ["Jones & Co"]
    assert world.names(search=".co") == ["Jones & Co", "Smith Law"]
    assert world.names(search="Ärzte") == ["Ärzte Partner"]
    assert world.names(search="ärzte") == []  # SQLite LIKE folds ASCII letters only
    assert world.names(search="https://smith-law.co.uk") == []  # the URL isn't stored
    assert world.names(search="nobody") == []


def test_search_treats_percent_underscore_and_the_escape_character_literally(
    world: World,
) -> None:
    world.lead("Smith_Law", "a.co.uk", minutes=0)
    world.lead("SmithXLaw", "b.co.uk", minutes=1)
    world.lead("100% Legal", "c.co.uk", minutes=2)
    world.lead("A/B Partners", "d.co.uk", minutes=3)

    assert world.names(search="Smith_") == ["Smith_Law"]
    assert world.names(search="_") == ["Smith_Law"]
    assert world.names(search="%") == ["100% Legal"]
    assert world.names(search="0% L") == ["100% Legal"]
    assert world.names(search="/") == ["A/B Partners"]
    assert world.names(search="A/B") == ["A/B Partners"]


@pytest.mark.parametrize("search", ["", " ", "\t\n "])
def test_a_blank_search_is_none(world: World, search: str) -> None:
    world.lead("Alpha", "alpha.co.uk")

    assert world.names(search=search) == ["Alpha"]


def test_the_filters_combine(world: World) -> None:
    a = world.lead("Smith Law", "smith-law.co.uk", minutes=0)
    b = world.lead("Smith Ops", "smith-ops.co.uk", minutes=1, segment=world.ops)
    world.lead("Jones Law", "jones.co.uk", minutes=2)
    world.stage(a, PipelineStage.CONTACTED)
    world.stage(b, PipelineStage.CONTACTED)

    found = world.listed(stage=PipelineStage.CONTACTED, segment_id=world.law, search="smith")

    assert [lead.id for lead in found.leads] == [a]
    assert found.total == 1


def test_fifty_per_page_and_a_page_past_the_end_shows_the_last(world: World) -> None:
    ids = world.transact(lambda: many(world, LEADS_PER_PAGE + 1))

    first, second, past = world.listed(), world.listed(page=2), world.listed(page=9)

    assert LEADS_PER_PAGE == 50
    assert (first.total, first.page, first.pages, len(first.leads)) == (51, 1, 2, 50)
    assert [lead.id for lead in first.leads] == ids[::-1][:50]
    assert [lead.id for lead in second.leads] == [ids[0]]
    assert (second.page, second.pages) == (2, 2)
    assert past == second


def many(world: World, count: int) -> list[str]:
    """``count`` Leads, a minute apart, oldest first."""
    ids = []
    for n in range(count):
        world.clock.set(T0 + timedelta(minutes=n))
        identity = LeadIdentity(f"Firm {n:03}", world.law, web_domain=f"firm{n}.co.uk")
        ids.append(world.discovery.upsert_lead(identity, LeadSource.CSV).lead_id)
    world.clock.set(T0)
    return ids


def test_exactly_fifty_leads_is_one_page(world: World) -> None:
    world.transact(lambda: many(world, LEADS_PER_PAGE))

    found = world.listed(page=2)

    assert (found.total, found.page, found.pages, len(found.leads)) == (50, 1, 1, 50)


def test_no_leads_is_one_empty_page(world: World) -> None:
    assert world.listed(page=5) == LeadListPage(leads=(), total=0, page=1, pages=1)


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"page": "1"}, TypeError),
        ({"page": True}, TypeError),
        ({"page": False}, TypeError),
        ({"page": 1.0}, TypeError),
        ({"page": None}, TypeError),
        ({"page": 0}, ValueError),
        ({"page": -1}, ValueError),
        ({"stage": "new"}, TypeError),
        ({"segment_id": 1}, TypeError),
        ({"search": b"smith"}, TypeError),
    ],
)
def test_lead_list_refuses_bad_arguments(
    world: World, arguments: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        world.listed(**arguments)


# survivor_of ------------------------------------------------------------------------------------


def test_survivor_of_follows_the_merge_chain(world: World) -> None:
    a = world.lead("Alpha", "alpha.co.uk", minutes=0)
    b = world.lead("Alpha Two", "alpha2.co.uk", minutes=1)
    c = world.lead("Alpha Three", "alpha3.co.uk", minutes=2)
    world.merge(b, c)  # C -> B
    world.merge(a, b)  # B -> A

    survivors = world.read(lambda: [discovery_queries.survivor_of(x) for x in (a, b, c)])

    assert [found.id if found else None for found in survivors] == [a, a, a]
    assert survivors[0] == world.read(lambda: discovery_queries.lead(a))


@pytest.mark.parametrize(
    "lead_id", ["", "abc", "<script>", "01kaaaaaaaaaaaaaaaaaaaaaaa", None, 7, UNKNOWN]
)
def test_survivor_of_an_unknown_or_malformed_id_is_none(world: World, lead_id: Any) -> None:
    world.lead("Alpha", "alpha.co.uk")

    assert world.read(lambda: discovery_queries.survivor_of(lead_id)) is None


def chain(world: World, hops: int) -> tuple[str, str]:
    """A raw merge chain of ``hops`` links: its first Lead and its end."""
    end = world.raw_lead(name="End")
    first = end
    for _ in range(hops):
        first = world.raw_lead(first)
    return first, end


def test_survivor_of_follows_at_most_max_merge_hops(world: World) -> None:
    longest, end = chain(world, MAX_MERGE_HOPS)
    too_long, _ = chain(world, MAX_MERGE_HOPS + 1)

    found = world.read(lambda: discovery_queries.survivor_of(longest))

    assert MAX_MERGE_HOPS == 32
    assert found is not None and found.id == end
    assert world.read(lambda: discovery_queries.survivor_of(too_long)) is None


def test_survivor_of_a_merge_loop_is_none(world: World) -> None:
    a = world.raw_lead()
    b = world.raw_lead()
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (b, a))
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (a, b))

    assert world.read(lambda: discovery_queries.survivor_of(a)) is None


def test_survivor_of_a_broken_chain_is_none(world: World) -> None:
    a = world.raw_lead()
    with fk_off_connection(world.engine) as connection:
        connection.exec_driver_sql(
            "UPDATE leads SET merged_into = ? WHERE id = ?", (new_id(), a)
        )
        connection.commit()

    assert world.read(lambda: discovery_queries.survivor_of(a)) is None


# lead_provenance --------------------------------------------------------------------------------


def test_lead_provenance_maps_each_contact_to_its_records_oldest_first(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    other = world.lead("Jones Law", "jones.co.uk")
    jane = world.add(lead, ContactCandidate(name="Jane Doe", email="jane@smith-law.co.uk"))
    later = T0 + timedelta(hours=1)
    world.add(lead, ContactCandidate(name="Jane Doe", role="Partner"), at=later)
    john = world.add(lead, ContactCandidate(name="John Roe"), at=T0 - timedelta(days=1))
    world.add(other, ContactCandidate(name="Ann Other"))

    found = world.read(lambda: research_queries.lead_provenance(lead))

    assert list(found) == [john, jane]  # in record order
    jane_records = found[jane]
    assert [(r.retrieved_at, r.id) for r in jane_records] == sorted(
        (r.retrieved_at, r.id) for r in jane_records
    )
    assert [r.retrieved_at for r in jane_records] == [T0, T0, later]
    assert sorted(r.subject for r in jane_records[:2]) == ["address", "contact"]
    assert jane_records[2].subject == ProvenanceSubject.CONTACT
    assert {r.contact_id for r in jane_records} == {jane}
    [address] = [r for r in jane_records if r.subject == ProvenanceSubject.ADDRESS]
    assert (address.address, address.address_class, address.source_detail) == (
        "jane@smith-law.co.uk",
        "guessed",
        ORIGIN,
    )
    assert [r.subject for r in found[john]] == [ProvenanceSubject.CONTACT]
    for contact_id, records in found.items():
        assert records == world.read(lambda c=contact_id: research_queries.provenance(c))


def test_lead_provenance_leaves_out_contacts_without_records(world: World) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    jane = world.add(lead, ContactCandidate(name="Jane Doe"))
    world.sql("DELETE FROM provenance_records WHERE contact_id = ?", (jane,))

    assert world.read(lambda: research_queries.lead_provenance(lead)) == {}
    assert world.read(lambda: research_queries.contacts(lead))[0].id == jane


@pytest.mark.parametrize("lead_id", ["", "abc", None, "01kaaaaaaaaaaaaaaaaaaaaaaa", UNKNOWN])
def test_lead_provenance_of_an_unknown_or_malformed_lead_is_empty(
    world: World, lead_id: Any
) -> None:
    lead = world.lead("Smith Law", "smith-law.co.uk")
    world.add(lead, ContactCandidate(name="Jane Doe"))

    assert world.read(lambda: research_queries.lead_provenance(lead_id)) == {}
