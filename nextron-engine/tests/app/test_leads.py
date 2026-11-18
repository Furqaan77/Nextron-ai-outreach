"""``Discovery.upsert_lead`` and ``Discovery.merge`` with the ``pipeline`` subscriber (FR-9,
AD-3, AD-4) on a temporary database at head, with ``FrozenClock`` and the real subscriber
registry: Story 2.2's app rows (New, Match any stage, Attach, Multi-match, Merge, Merge
refused) with their ``events`` rows, refusals that roll back, the queries, and the ``leads``
checks, unique indexes and foreign keys on raw writes."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.discovery.api import MAX_MERGE_CHAIN, Discovery, UpsertedLead
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.repos.discovery.leads import StoredLead
from nextron.db.uow import UnitOfWork, UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.leads import InvalidLeadIdentity, LeadIdentity, LeadSource, MergeRefused
from nextron.domain.machines import IllegalTransition
from nextron.domain.notifications import LeadsMerged, Notification
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
STAMP = "2026-11-17T09:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
ACTOR = "system:pipeline"


@dataclass
class World:
    """Discovery and Pipeline on real units of work with the registry ``main`` builds."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    segment: str
    other_segment: str

    def transact[R](self, fn: Callable[[], R]) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as a service would."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def identity(self, name: str = "Smith Law LLP", **keys: Any) -> LeadIdentity:
        return LeadIdentity(name=name, segment_id=keys.pop("segment_id", self.segment), **keys)

    def upsert(
        self, identity: LeadIdentity, source: LeadSource = LeadSource.CSV
    ) -> UpsertedLead:
        return self.transact(lambda: self.discovery.upsert_lead(identity, source))

    def create(self, later: timedelta = timedelta(0), **keys: Any) -> str:
        """A new Lead at T0 + ``later``; returns its id."""
        self.clock.set(T0 + later)
        result = self.upsert(self.identity(**keys))
        assert result.created
        return result.lead_id

    def merge(self, survivor: str, loser: str) -> None:
        self.transact(lambda: self.discovery.merge(survivor, loser))

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def lead(self, lead_id: str) -> dict[str, Any]:
        [row] = self.sql(
            "SELECT id, name, web_domain, ch_number, place_id, segment_id, country_code, "
            "merged_into, created_at, pipeline_stage FROM leads WHERE id = ?",
            (lead_id,),
        )
        keys = (
            "id name web_domain ch_number place_id segment_id country_code merged_into "
            "created_at pipeline_stage"
        ).split()
        return dict(zip(keys, row, strict=True))

    def set_stage(self, lead_id: str, stage: str) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead_id))

    def events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events WHERE entity = 'leads' ORDER BY rowid"
        )

    def count(self) -> int:
        [(count,)] = self.sql("SELECT COUNT(*) FROM leads")
        return int(count)

    def dump(self) -> tuple[list[Any], list[Any]]:
        return self.sql("SELECT * FROM leads ORDER BY id"), self.sql(
            "SELECT * FROM events ORDER BY rowid"
        )


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
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
    found = World(units, db_engine, clock, discovery, "", "")
    [(first,), (second,)] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment, found.other_segment = first, second
    return found


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Notification]]:
    """Every notification published, in order, while the real subscribers still run."""
    seen: list[Notification] = []
    original = UnitOfWork.publish

    def recording(self: UnitOfWork, notification: Notification) -> None:
        seen.append(notification)
        original(self, notification)

    monkeypatch.setattr(UnitOfWork, "publish", recording)
    yield seen


def created_event(lead_id: str, source: str = "csv", at: str = STAMP) -> tuple[Any, ...]:
    return ("leads", lead_id, None, "new", f"created_from_{source}", None, ACTOR, at)


# New ------------------------------------------------------------------------------------------


def test_a_new_organization_becomes_one_lead_at_stage_new(
    world: World, published: list[Notification]
) -> None:
    result = world.upsert(
        world.identity(web_domain="smith-law.co.uk", ch_number="OC123456", country_code="GB")
    )

    assert result.created is True
    assert result.merged == ()
    assert world.count() == 1
    assert world.lead(result.lead_id) == {
        "id": result.lead_id,
        "name": "Smith Law LLP",
        "web_domain": "smith-law.co.uk",
        "ch_number": "OC123456",
        "place_id": None,
        "segment_id": world.segment,
        "country_code": "GB",
        "merged_into": None,
        "created_at": STAMP,
        "pipeline_stage": "new",
    }
    assert world.events() == [created_event(result.lead_id)]
    assert published == []


@pytest.mark.parametrize("source", list(LeadSource))
def test_the_creation_event_records_the_source(world: World, source: LeadSource) -> None:
    result = world.upsert(world.identity(place_id="ChIJ1"), source)

    assert world.events() == [created_event(result.lead_id, str(source))]


def test_distinct_organizations_get_distinct_leads(world: World) -> None:
    first = world.create(web_domain="a.com")
    second = world.create(web_domain="b.com", ch_number="00000001")
    third = world.create(place_id="P3")

    assert len({first, second, third}) == 3
    assert world.count() == 3
    assert len(world.events()) == 3


# Match any stage ------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", list(PipelineStage))
@pytest.mark.parametrize(
    "keys",
    [{"web_domain": "smith-law.co.uk"}, {"ch_number": "OC123456"}, {"place_id": "ChIJ1"}],
)
def test_a_key_matches_its_lead_at_any_stage(
    world: World, stage: PipelineStage, keys: dict[str, str]
) -> None:
    lead_id = world.create(web_domain="smith-law.co.uk", ch_number="OC123456", place_id="ChIJ1")
    world.set_stage(lead_id, str(stage))
    before = world.dump()

    world.clock.advance(timedelta(days=1))
    result = world.upsert(world.identity(name="Another Name", **keys), LeadSource.PLACES)

    assert result == UpsertedLead(lead_id, False, ())
    assert world.dump() == before  # no row and no event added


def test_a_key_held_by_a_merged_lead_returns_its_survivor(world: World) -> None:
    survivor = world.create(web_domain="a.com")
    merged = world.create(timedelta(seconds=1), web_domain="b.com")
    world.merge(survivor, merged)
    assert world.lead(merged)["web_domain"] == "b.com"  # the survivor holds another domain
    before = world.dump()

    result = world.upsert(world.identity(web_domain="b.com"))

    assert result == UpsertedLead(survivor, False, ())
    assert world.dump() == before


def test_merged_into_is_followed_to_the_end_of_the_chain(world: World) -> None:
    oldest = world.create(web_domain="c.com")
    middle = world.create(timedelta(seconds=1), web_domain="a.com")
    newest = world.create(timedelta(seconds=2), web_domain="b.com")
    world.merge(middle, newest)
    world.merge(oldest, middle)

    assert world.upsert(world.identity(web_domain="b.com")).lead_id == oldest
    assert world.upsert(world.identity(web_domain="a.com")).lead_id == oldest


def chain(world: World, links: int) -> str:
    """A survivor and ``links`` Leads each merged into the one before, written raw; the last
    holds ``deep.com``. Returns the survivor's id."""
    survivor = new_id()
    world.sql(
        "INSERT INTO leads (id, name, web_domain, segment_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (survivor, "S", "top.com", world.segment, STAMP),
    )
    previous = survivor
    for index in range(links):
        lead_id = new_id()
        domain = "deep.com" if index == links - 1 else None
        world.sql(
            "INSERT INTO leads (id, name, web_domain, segment_id, merged_into, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, "L", domain, world.segment, previous, STAMP),
        )
        previous = lead_id
    return survivor


def test_a_chain_of_64_links_is_followed(world: World) -> None:
    survivor = chain(world, MAX_MERGE_CHAIN)

    assert world.upsert(world.identity(web_domain="deep.com")).lead_id == survivor


def test_a_chain_longer_than_64_links_is_refused(world: World) -> None:
    chain(world, MAX_MERGE_CHAIN + 1)
    before = world.dump()

    with pytest.raises(RuntimeError, match="longer than 64 links"):
        world.upsert(world.identity(web_domain="deep.com", place_id="NEW"))

    assert world.dump() == before


# Attach ---------------------------------------------------------------------------------------


def test_a_match_gains_the_keys_it_lacks_that_no_lead_holds(world: World) -> None:
    lead_id = world.create(web_domain="smith-law.co.uk")

    result = world.upsert(
        world.identity(web_domain="smith-law.co.uk", ch_number="OC123456", place_id="ChIJ1")
    )

    assert result == UpsertedLead(lead_id, False, ())
    row = world.lead(lead_id)
    assert (row["ch_number"], row["place_id"]) == ("OC123456", "ChIJ1")
    assert world.count() == 1
    assert world.events() == [created_event(lead_id)]  # attaching is no stage change


def test_an_existing_leads_name_segment_and_country_never_change(world: World) -> None:
    lead_id = world.create(web_domain="a.com", ch_number="00000001", country_code="GB")

    world.upsert(
        world.identity(
            name="Renamed Ltd",
            segment_id=world.other_segment,
            web_domain="a.com",
            ch_number="00000002",  # conflicts with the Lead's own: dropped
            country_code="US",
        )
    )

    row = world.lead(lead_id)
    assert (row["name"], row["segment_id"], row["country_code"]) == (
        "Smith Law LLP",
        world.segment,
        "GB",
    )
    assert row["ch_number"] == "00000001"
    assert world.sql("SELECT COUNT(*) FROM leads WHERE ch_number = '00000002'") == [(0,)]


def test_a_key_moved_by_a_merge_beats_the_candidates_value(world: World) -> None:
    survivor = world.create(web_domain="a.com")
    newer = world.create(timedelta(hours=1), place_id="P1", ch_number="00000001")

    result = world.upsert(
        world.identity(web_domain="a.com", place_id="P1", ch_number="00000002")
    )

    assert result == UpsertedLead(survivor, False, (newer,))
    assert world.lead(survivor)["ch_number"] == "00000001"  # moved by the merge, then kept
    assert world.sql("SELECT COUNT(*) FROM leads WHERE ch_number = '00000002'") == [(0,)]
    later = world.upsert(world.identity(ch_number="00000001"))
    assert later == UpsertedLead(survivor, False, ())


def test_a_key_another_lead_holds_is_not_attached(world: World) -> None:
    first = world.create(web_domain="a.com", ch_number="00000001")
    other = world.create(timedelta(seconds=1), web_domain="b.com", ch_number="00000002")
    world.merge(first, other)  # b.com and ch 00000002 stay on the merged Lead

    result = world.upsert(world.identity(web_domain="b.com", ch_number="00000002"))

    assert result == UpsertedLead(first, False, ())
    assert world.lead(first)["web_domain"] == "a.com"
    assert world.lead(other)["ch_number"] == "00000002"


# Multi-match ----------------------------------------------------------------------------------


def test_a_candidate_matching_two_leads_merges_the_newer_into_the_older(
    world: World, published: list[Notification]
) -> None:
    older = world.create(web_domain="a.com")
    newer = world.create(timedelta(hours=1), place_id="P1")
    world.set_stage(newer, "contacted")
    world.clock.set(T0 + timedelta(hours=2))

    result = world.upsert(world.identity(web_domain="a.com", place_id="P1", ch_number="00000009"))

    assert result == UpsertedLead(older, False, (newer,))
    assert published == [LeadsMerged(survivor_lead_id=older, merged_lead_id=newer)]
    survivor, merged = world.lead(older), world.lead(newer)
    assert (survivor["web_domain"], survivor["place_id"], survivor["ch_number"]) == (
        "a.com",
        "P1",
        "00000009",
    )
    assert (merged["merged_into"], merged["place_id"], merged["pipeline_stage"]) == (
        older,
        None,
        "contacted",
    )
    assert survivor["pipeline_stage"] == "contacted"
    later = "2026-11-17T11:00:00.000000Z"
    assert world.events()[2:] == [
        ("leads", older, "new", "contacted", "leads_merged", newer, ACTOR, later)
    ]


def test_three_matches_merge_into_the_oldest_in_age_order(
    world: World, published: list[Notification]
) -> None:
    oldest = world.create(timedelta(seconds=5), ch_number="00000001")
    youngest = world.create(timedelta(seconds=9), place_id="P1")
    middle = world.create(timedelta(seconds=7), web_domain="a.com")

    result = world.upsert(
        world.identity(web_domain="a.com", ch_number="00000001", place_id="P1")
    )

    assert result == UpsertedLead(oldest, False, (middle, youngest))
    assert [n.merged_lead_id for n in published if isinstance(n, LeadsMerged)] == [
        middle,
        youngest,
    ]
    row = world.lead(oldest)
    assert (row["web_domain"], row["ch_number"], row["place_id"]) == ("a.com", "00000001", "P1")


def test_a_tie_on_created_at_keeps_the_smaller_id(world: World) -> None:
    first = world.create(place_id="X1")
    second = world.create(place_id="X2")  # the same frozen time
    smaller, larger = sorted((first, second))
    # The larger id holds the key looked up first, so only the id tie-break picks the smaller.
    world.sql("UPDATE leads SET place_id = 'P1' WHERE id = ?", (smaller,))
    world.sql("UPDATE leads SET web_domain = 'a.com', place_id = NULL WHERE id = ?", (larger,))
    assert smaller < larger
    assert world.lead(larger)["created_at"] == world.lead(smaller)["created_at"]
    assert world.lead(larger)["web_domain"] == "a.com"

    result = world.upsert(world.identity(web_domain="a.com", place_id="P1"))

    assert result == UpsertedLead(smaller, False, (larger,))
    assert world.lead(larger)["merged_into"] == smaller


# Merge ----------------------------------------------------------------------------------------


def test_merge_moves_the_keys_the_survivor_lacks_and_advances_its_stage(
    world: World, published: list[Notification]
) -> None:
    survivor = world.create(web_domain="a.com")
    loser = world.create(timedelta(minutes=1), web_domain="b.com", place_id="P1")
    world.set_stage(loser, "contacted")
    world.clock.set(T0 + timedelta(minutes=5))

    world.merge(survivor, loser)

    kept, merged = world.lead(survivor), world.lead(loser)
    assert (merged["merged_into"], merged["place_id"], merged["web_domain"]) == (
        survivor,
        None,
        "b.com",  # the survivor holds another domain: it stays on the merged Lead
    )
    assert (kept["place_id"], kept["web_domain"], kept["merged_into"]) == ("P1", "a.com", None)
    assert (kept["pipeline_stage"], merged["pipeline_stage"]) == ("contacted", "contacted")
    assert published == [LeadsMerged(survivor_lead_id=survivor, merged_lead_id=loser)]
    assert world.events() == [
        created_event(survivor),
        created_event(loser, at="2026-11-17T09:01:00.000000Z"),
        (
            "leads",
            survivor,
            "new",
            "contacted",
            "leads_merged",
            loser,
            ACTOR,
            "2026-11-17T09:05:00.000000Z",
        ),
    ]


@pytest.mark.parametrize(
    ("survivor_stage", "loser_stage", "expected"),
    [
        ("contacted", "new", None),
        ("won", "replied", None),
        ("queued", "queued", None),
        ("lost", "won", None),
        ("disqualified", "lost", None),
        ("won", "disqualified", "disqualified"),
        ("replied", "not_now", "not_now"),
        ("not_now", "interested", "interested"),
    ],
)
def test_the_survivor_takes_the_more_advanced_stage(
    world: World, survivor_stage: str, loser_stage: str, expected: str | None
) -> None:
    survivor = world.create(web_domain="a.com")
    loser = world.create(timedelta(minutes=1), web_domain="b.com")
    world.set_stage(survivor, survivor_stage)
    world.set_stage(loser, loser_stage)

    world.merge(survivor, loser)

    assert world.lead(survivor)["pipeline_stage"] == (expected or survivor_stage)
    assert world.lead(loser)["pipeline_stage"] == loser_stage
    stage_rows = world.events()[2:]
    if expected is None:
        assert stage_rows == []
    else:
        assert [row[2:6] for row in stage_rows] == [
            (survivor_stage, expected, "leads_merged", loser)
        ]


def test_a_merge_with_no_keys_to_move_still_publishes(
    world: World, published: list[Notification]
) -> None:
    survivor = world.create(web_domain="a.com", ch_number="00000001", place_id="P1")
    loser = world.create(timedelta(minutes=1), web_domain="b.com")

    world.merge(survivor, loser)

    assert world.lead(loser)["merged_into"] == survivor
    assert world.lead(loser)["web_domain"] == "b.com"
    assert len(published) == 1


# Merge refused --------------------------------------------------------------------------------


def test_merge_refusals_roll_the_transaction_back(world: World) -> None:
    older = world.create(web_domain="a.com")
    newer = world.create(timedelta(minutes=1), web_domain="b.com")
    merged = world.create(timedelta(minutes=2), web_domain="c.com")
    world.merge(older, merged)
    unknown = new_id()
    cases = [
        (older, older, "itself"),
        (unknown, newer, "survivor is not a known Lead"),
        (older, unknown, "Lead to merge is not a known Lead"),
        ("not-a-ulid", newer, "survivor is not a known Lead"),
        (older.lower(), newer, "survivor is not a known Lead"),
        (older, merged, "Lead to merge is already merged"),
        (merged, newer, "survivor is already merged"),
        (newer, older, "survivor is newer"),
    ]
    for survivor_id, loser_id, reason in cases:
        before = world.dump()

        def attempt(survivor_id: str = survivor_id, loser_id: str = loser_id) -> None:
            world.discovery.upsert_lead(world.identity(place_id="WRITTEN-FIRST"), LeadSource.CSV)
            world.discovery.merge(survivor_id, loser_id)

        with pytest.raises(MergeRefused, match=reason) as caught:
            world.transact(attempt)

        assert caught.value.code == "merge_refused"
        assert world.dump() == before  # the Lead created first was rolled back too


def test_a_tie_on_created_at_refuses_the_larger_id_as_survivor(world: World) -> None:
    first = world.create(web_domain="a.com")
    second = world.create(web_domain="b.com")
    smaller, larger = sorted((first, second))

    with pytest.raises(MergeRefused, match="newer"):
        world.merge(larger, smaller)
    world.merge(smaller, larger)

    assert world.lead(larger)["merged_into"] == smaller


def test_merge_takes_str_ids(world: World) -> None:
    with pytest.raises(TypeError):
        world.transact(lambda: world.discovery.merge(None, new_id()))  # type: ignore[arg-type]


# upsert_lead refusals -------------------------------------------------------------------------


def test_an_unknown_segment_is_refused_and_nothing_is_written(world: World) -> None:
    world.create(web_domain="a.com")
    before = world.dump()

    with pytest.raises(InvalidLeadIdentity) as caught:
        world.upsert(world.identity(segment_id=new_id(), web_domain="a.com"))

    assert caught.value.field == "segment_id"
    assert world.dump() == before


@pytest.mark.parametrize(
    ("identity", "source"),
    [
        ({"name": "X", "web_domain": "a.com"}, LeadSource.CSV),
        (None, LeadSource.CSV),
        ("VALID", "csv"),
        ("VALID", None),
    ],
)
def test_wrong_types_raise_type_error_and_nothing_is_written(
    world: World, identity: object, source: object
) -> None:
    chosen = world.identity(web_domain="a.com") if identity == "VALID" else identity
    before = world.dump()

    def attempt() -> None:
        world.discovery.upsert_lead(world.identity(place_id="WRITTEN-FIRST"), LeadSource.CSV)
        world.discovery.upsert_lead(chosen, source)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        world.transact(attempt)

    assert world.dump() == before


def test_record_created_refuses_a_lead_that_is_not_new(world: World) -> None:
    lead_id = world.create(web_domain="a.com")
    world.set_stage(lead_id, "contacted")
    pipeline = Pipeline(world.clock)

    with pytest.raises(IllegalTransition):
        world.transact(lambda: pipeline.record_created(lead_id, LeadSource.CSV))
    with pytest.raises(IllegalTransition):
        world.transact(lambda: pipeline.record_created(new_id(), LeadSource.CSV))
    with pytest.raises(TypeError):
        world.transact(lambda: pipeline.record_created(lead_id, "csv"))  # type: ignore[arg-type]
    assert len(world.events()) == 1


# Queries --------------------------------------------------------------------------------------


def test_the_queries_read_a_lead_and_its_stage(world: World) -> None:
    survivor = world.create(web_domain="a.com", country_code="GB")
    loser = world.create(timedelta(minutes=1), web_domain="b.com")
    world.set_stage(loser, "replied")
    world.merge(survivor, loser)

    with world.units.open(read_only=True):
        lead = discovery_queries.lead(survivor)
        merged = discovery_queries.lead(loser)
        stages = (pipeline_queries.stage(survivor), pipeline_queries.stage(loser))
        missing = (
            discovery_queries.lead(new_id()),
            discovery_queries.lead("not-a-ulid"),
            pipeline_queries.stage(new_id()),
            pipeline_queries.stage("not-a-ulid"),
        )

    assert lead == StoredLead(
        id=survivor,
        name="Smith Law LLP",
        web_domain="a.com",
        ch_number=None,
        place_id=None,
        segment_id=world.segment,
        country_code="GB",
        merged_into=None,
        created_at=T0,
    )
    assert merged is not None and merged.merged_into == survivor
    assert stages == (PipelineStage.REPLIED, PipelineStage.REPLIED)
    assert isinstance(stages[0], PipelineStage)
    assert missing == (None, None, None, None)


# The table ------------------------------------------------------------------------------------


def raw_insert(world: World, **changes: object) -> str:
    values: dict[str, object] = {
        "id": new_id(),
        "name": "Raw",
        "web_domain": None,
        "ch_number": None,
        "place_id": "RAW",
        "segment_id": world.segment,
        "country_code": None,
        "merged_into": None,
        "created_at": STAMP,
    }
    values.update(changes)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    world.sql(f"INSERT INTO leads ({columns}) VALUES ({marks})", tuple(values.values()))
    return str(values["id"])


def test_a_raw_row_defaults_to_stage_new(world: World) -> None:
    lead_id = raw_insert(world)

    assert world.lead(lead_id)["pipeline_stage"] == "new"


@pytest.mark.parametrize(
    "changes",
    [
        {"pipeline_stage": "merged"},
        {"pipeline_stage": "NEW"},
        {"country_code": "gb"},
        {"country_code": "GBR"},
        {"country_code": "G1"},
        {"place_id": None},  # ck_leads_identity_key
        {"segment_id": "01JD3QZ8X5V9K2M7N4P6R8T0WY"},  # fk_leads_segment_id_segments
        {"merged_into": "01JD3QZ8X5V9K2M7N4P6R8T0WY"},  # fk_leads_merged_into_leads
        {"name": None},
        {"created_at": None},
        {"pipeline_stage": None},
    ],
)
def test_a_raw_write_that_breaks_a_rule_raises_integrity_error(
    world: World, changes: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        raw_insert(world, **changes)
    assert world.count() == 0


def test_a_lead_cannot_be_merged_into_itself_by_a_raw_write(world: World) -> None:
    lead_id = raw_insert(world)

    with pytest.raises(IntegrityError):
        world.sql("UPDATE leads SET merged_into = id WHERE id = ?", (lead_id,))


def test_a_merged_lead_may_hold_no_key(world: World) -> None:
    survivor = raw_insert(world)
    merged = raw_insert(world, place_id=None, merged_into=survivor)

    assert world.lead(merged)["merged_into"] == survivor


@pytest.mark.parametrize(
    ("column", "value"),
    [("web_domain", "a.com"), ("ch_number", "00000001"), ("place_id", "P1")],
)
def test_each_key_is_unique_and_null_is_not(world: World, column: str, value: str) -> None:
    raw_insert(world, **{column: value, "place_id": "first" if column != "place_id" else value})

    with pytest.raises(IntegrityError):
        raw_insert(world, **{column: value, "place_id": "second" if column != "place_id" else value})
    raw_insert(world, place_id="third")  # NULLs in the other key columns don't collide
    raw_insert(world, place_id="fourth")
    assert world.count() == 3


def test_a_segment_with_leads_cannot_be_deleted(world: World) -> None:
    raw_insert(world)

    with pytest.raises(IntegrityError):
        world.sql("DELETE FROM segments WHERE id = ?", (world.segment,))
