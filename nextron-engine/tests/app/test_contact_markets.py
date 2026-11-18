"""Each Contact's Market and timezone (FR-2, FR-51, AD-7, AD-11, AD-17; Story 3.2) on a temporary
database at head with the shipped Segments and Markets, ``FrozenClock`` and the real subscriber
registry: the app rows of the I/O matrix through ``Research.add_contact`` (a new Contact, an
office, a fill that re-resolves), ``reparent_lead`` (a merge), ``resolve_market`` and
``ContactMarkets`` (the backfill with and without Markets, ``queue_backfill`` once and
absorbed); the notes rewritten on a change and untouched when nothing changed; no write when
nothing changed; the new ``contacts`` checks on raw writes; and the 0018 to 0019 upgrade keeping
every row."""

import logging
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.research import api as research_api
from nextron.app.research.api import AddedContact, ContactMarkets, Research
from nextron.app.setup.api import Setup
from nextron.db.migrations import SCRIPT_LOCATION, ensure_current
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contact_market import MarketBasis, TzBasis
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.geo import Location
from nextron.domain.job import JobTypes, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from tests.fakes.clock import FrozenClock
from tests.migration_chains import chain, engine_on, run_sql

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP = "2026-11-18T09:00:00.000000Z"
ORIGIN = "Law Society directory, 2026"
SEGMENTS = main.SEED_DIR / main.SEGMENTS_FILE_NAME
MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
CONTACT_KEYS = (
    "lead_id",
    "email",
    "tz",
    "market_id",
    "market_basis",
    "tz_basis",
    "office_country",
    "office_region",
    "updated_at",
)
LEAD_COUNTRY_NOTE = "Market taken from the Lead's country; office location unknown"
UK_DEFAULT_NOTE = (
    "Timezone is the United Kingdom Market's default (Europe/London); neither the office "
    "location nor the Lead's headquarters gave one"
)
US_FALLBACK_NOTE = (
    "Timezone falls back to America/New_York: the US state is unknown, so the Send Window "
    "narrows to 11:00-17:00"
)
DE_NOTE = (
    "No Market: the email address is on the country-code domain .de, outside the enabled Markets"
)
UK_LEAD_NOTES = f"{LEAD_COUNTRY_NOTE}\n{UK_DEFAULT_NOTE}"
US_LEAD_NOTES = f"{LEAD_COUNTRY_NOTE}\n{US_FALLBACK_NOTE}"
RESEARCH_LOGGER = "nextron.app.research.api"


@dataclass
class World:
    """Research, ContactMarkets and a JobQueue on real units of work with the registry ``main``
    builds."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    job_types: JobTypes
    queue: JobQueue
    markets: ContactMarkets
    law: str

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def lead(
        self, name: str, domain: str, country: str | None, *, later: timedelta = timedelta(0)
    ) -> str:
        self.clock.set(T0 + later)
        identity = LeadIdentity(name, self.law, web_domain=domain, country_code=country)
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        self.sql("DELETE FROM jobs WHERE type = 'enrich_lead'")  # Story 2.5's crawl: not tested here
        self.clock.set(T0)
        return upserted.lead_id

    def add(
        self,
        lead_id: str,
        candidate: ContactCandidate,
        source: ProvenanceSource = ProvenanceSource.CSV,
        retrieved_at: datetime | None = None,
    ) -> AddedContact:
        at = self.clock.now() if retrieved_at is None else retrieved_at
        return self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail=ORIGIN, retrieved_at=at
            )
        )

    def resolve(self, contact_id: str) -> None:
        self.transact(lambda: self.research.resolve_market(contact_id))

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def contact(self, contact_id: str) -> dict[str, Any]:
        [row] = self.sql(
            f"SELECT {', '.join(CONTACT_KEYS)} FROM contacts WHERE id = ?", (contact_id,)
        )
        return dict(zip(CONTACT_KEYS, row, strict=True))

    def resolved(self, contact_id: str) -> tuple[Any, ...]:
        found = self.contact(contact_id)
        code = None
        if found["market_id"] is not None:
            [(code,)] = self.sql("SELECT code FROM markets WHERE id = ?", (found["market_id"],))
        return code, found["market_basis"], found["tz"], found["tz_basis"]

    def notes(self, contact_id: str) -> list[tuple[str, str | None]]:
        """Each record's subject and notes, by ``retrieved_at``, then ``id``."""
        return self.sql(
            "SELECT subject, notes FROM provenance_records WHERE contact_id = ? "
            "ORDER BY retrieved_at, id",
            (contact_id,),
        )

    def market_id(self, code: str) -> str:
        [(market_id,)] = self.sql("SELECT id FROM markets WHERE code = ?", (code,))
        return str(market_id)

    def jobs(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT type, lane, status, idempotency_key, payload FROM jobs ORDER BY created_at, id"
        )

    def unresolve(self) -> None:
        """Every Contact as it was before Story 3.2: no Market, timezone or bases, no notes."""
        self.sql(
            "UPDATE contacts SET market_id = NULL, tz = NULL, market_basis = NULL, "
            "tz_basis = NULL"
        )
        self.sql("UPDATE provenance_records SET notes = NULL")


def no_service() -> Any:
    """``build_job_types``'s other services: no other job runs in these tests."""
    raise AssertionError("no other job runs in these tests")


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))
    markets = validate_markets(seed_yaml.parse(MARKETS.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).sync_markets(markets)
    research = Research(clock)
    built: dict[str, ContactMarkets] = {}
    job_types = main.build_job_types(
        alerts=no_service,
        backups=no_service,
        restore_tests=no_service,
        recovery=no_service,
        contact_markets=lambda: built["markets"],
        crawls=no_service,
        discovery=no_service,
        research=no_service,
        extractions=no_service,
        primary=no_service,
        job_boards=no_service,
        scores=no_service,
    )
    queue = JobQueue(clock, job_types)
    built["markets"] = ContactMarkets(clock, research, queue)
    found = World(
        units,
        db_engine,
        clock,
        Discovery(clock, Pipeline(clock), queue),
        research,
        job_types,
        queue,
        built["markets"],
        "",
    )
    [(law,)] = found.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
    found.law = law
    return found


@pytest.fixture
def statements(world: World) -> Iterator[list[str]]:
    """Every SQL statement the engine runs while the test records (``recording.append``)."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(world.engine, "before_cursor_execute", record)
    yield seen
    event.remove(world.engine, "before_cursor_execute", record)


def writes(seen: list[str]) -> list[str]:
    return [s for s in seen if s.split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}]


# add_contact (the I/O matrix) --------------------------------------------------------------------


# Row: Office, generic TLD
def test_a_new_contact_with_an_office_takes_the_office_market_and_zone(world: World) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    office = Location("US", "NY")
    candidate = ContactCandidate(name="Jane Doe", email="jane@firm.com", office=office)

    added = world.add(lead_id, candidate)

    found = world.contact(added.contact_id)
    assert (found["office_country"], found["office_region"]) == ("US", "NY")
    assert world.resolved(added.contact_id) == (
        "us",
        "office_location",
        "America/New_York",
        "office_location",
    )
    assert world.notes(added.contact_id) == [("contact", None), ("address", None)]


# Row: Lead country
def test_a_new_contact_without_an_office_takes_the_leads_country_with_notes(world: World) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")

    added = world.add(lead_id, ContactCandidate(name="Jane Doe", email="jane@smith-law.co.uk"))

    assert world.resolved(added.contact_id) == (
        "uk",
        "lead_country",
        "Europe/London",
        "market_default",
    )
    assert world.contact(added.contact_id)["market_id"] == world.market_id("uk")
    assert world.notes(added.contact_id) == [
        ("contact", f"{LEAD_COUNTRY_NOTE}\n{UK_DEFAULT_NOTE}"),
        ("address", None),  # only the first contact record holds the notes
    ]


# Row: US unknown state
def test_a_us_contact_with_no_state_falls_back_to_new_york(world: World) -> None:
    lead_id = world.lead("Jones", "jones.com", "US")

    added = world.add(lead_id, ContactCandidate(name="Bo Lee"))

    assert world.resolved(added.contact_id) == (
        "us",
        "lead_country",
        "America/New_York",
        "us_unknown_fallback",
    )
    assert world.notes(added.contact_id) == [("contact", US_LEAD_NOTES)]


# Row: Conflict
def test_a_us_office_with_a_uk_address_is_uk_with_the_office_zone(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com", "US")
    candidate = ContactCandidate(name="Al", email="al@firm.co.uk", office=Location("US", "TX"))

    added = world.add(lead_id, candidate)

    assert world.resolved(added.contact_id) == (
        "uk",
        "conflict_stricter",
        "America/Chicago",
        "office_location",
    )
    [(_, notes), _] = world.notes(added.contact_id)
    assert notes is not None and notes.startswith("The office location and the email's")


# Row: Location blocks, Unknown
@pytest.mark.parametrize(
    ("country", "office", "basis"),
    [
        ("CA", None, "location_outside_markets"),
        ("GB", Location("CA"), "location_outside_markets"),
        (None, None, "location_unknown"),
    ],
)
def test_a_blocked_contact_has_no_market_or_timezone_and_one_note(
    world: World, country: str | None, office: Location | None, basis: str
) -> None:
    lead_id = world.lead("Maple", "maple.ca", country)

    added = world.add(lead_id, ContactCandidate(name="Ann", office=office))

    assert world.resolved(added.contact_id) == (None, basis, None, "no_market")
    [(subject, notes)] = world.notes(added.contact_id)
    assert subject == "contact" and notes is not None and notes.startswith("No Market: ")


# Row: Fill re-resolves
def test_a_filled_email_re_resolves_in_the_same_write(world: World) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    first = world.add(lead_id, ContactCandidate(name="Ann Lee"))
    assert world.resolved(first.contact_id)[1] == "lead_country"
    world.clock.advance(timedelta(minutes=5))

    second = world.add(lead_id, ContactCandidate(name="Ann Lee", email="a@firm.de"))

    assert second == AddedContact(first.contact_id, created=False, email_dropped=False)
    assert world.resolved(first.contact_id) == (None, "cctld_outside_markets", None, "no_market")
    assert world.contact(first.contact_id)["updated_at"] == "2026-11-18T09:05:00.000000Z"
    assert world.notes(first.contact_id) == [("contact", DE_NOTE), ("address", None)]


def test_a_filled_office_re_resolves_and_adds_a_contact_record(world: World) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    first = world.add(lead_id, ContactCandidate(name="Ann Lee", email="ann@firm.com"))
    world.clock.advance(timedelta(minutes=5))

    world.add(
        lead_id,
        ContactCandidate(name="Ann Lee", email="ann@firm.com", office=Location("US", "TX")),
        source=ProvenanceSource.WEBSITE,
    )

    found = world.contact(first.contact_id)
    assert (found["office_country"], found["office_region"]) == ("US", "TX")
    assert world.resolved(first.contact_id) == (
        "us",
        "office_location",
        "America/Chicago",
        "office_location",
    )
    # the notes of the first contact record now describe the office (none), the filled office
    # added one contact record
    assert world.notes(first.contact_id) == [
        ("contact", None),
        ("address", None),
        ("contact", None),
    ]


def test_an_office_is_never_overwritten_and_nothing_new_writes_nothing(
    world: World, statements: list[str]
) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    candidate = ContactCandidate(name="Ann Lee", office=Location("GB"))
    first = world.add(lead_id, candidate)
    before = world.contact(first.contact_id)
    statements.clear()

    world.add(lead_id, ContactCandidate(name="Ann Lee", office=Location("US", "NY")))

    assert world.contact(first.contact_id) == before
    assert writes(statements) == []


# Row: Merge
def test_a_merge_re_resolves_each_moved_contact_with_its_notes_replaced(world: World) -> None:
    older = world.lead("Jones US", "jones.com", "US")
    newer = world.lead("Jones UK", "jones.co.uk", "GB", later=timedelta(seconds=1))
    added = world.add(newer, ContactCandidate(name="Bo Lee"))
    kept = world.add(older, ContactCandidate(name="Al Kay"))
    assert world.resolved(added.contact_id)[0] == "uk"
    kept_before = world.contact(kept.contact_id)
    world.clock.advance(timedelta(minutes=1))

    world.transact(lambda: world.discovery.merge(older, newer))

    assert world.contact(added.contact_id)["lead_id"] == older
    assert world.resolved(added.contact_id) == (
        "us",
        "lead_country",
        "America/New_York",
        "us_unknown_fallback",
    )
    assert world.notes(added.contact_id) == [("contact", US_LEAD_NOTES)]
    assert world.contact(kept.contact_id) == kept_before  # the survivor's own Contact is unchanged


# resolve_market -------------------------------------------------------------------------------


def test_resolve_market_re_resolves_after_a_change_outside_add_contact(world: World) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    added = world.add(lead_id, ContactCandidate(name="Ann", email="ann@smith-law.co.uk"))
    world.sql("UPDATE contacts SET office_country = 'US', office_region = 'HI'")

    world.resolve(added.contact_id)

    assert world.resolved(added.contact_id) == (
        "uk",  # the .uk address and a US office: the stricter UK
        "conflict_stricter",
        "Pacific/Honolulu",
        "office_location",
    )


def test_resolve_market_with_nothing_changed_writes_nothing(
    world: World, statements: list[str]
) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    added = world.add(lead_id, ContactCandidate(name="Ann", email="ann@smith-law.co.uk"))
    before = (world.contact(added.contact_id), world.notes(added.contact_id))
    world.clock.advance(timedelta(hours=1))
    statements.clear()

    world.resolve(added.contact_id)

    assert writes(statements) == []
    assert (world.contact(added.contact_id), world.notes(added.contact_id)) == before


def test_resolve_market_rewrites_only_what_changed(world: World, statements: list[str]) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    added = world.add(lead_id, ContactCandidate(name="Ann", email="ann@smith-law.co.uk"))
    world.sql("UPDATE provenance_records SET notes = 'stale' WHERE subject = 'contact'")
    statements.clear()

    world.resolve(added.contact_id)

    # the first record's notes, then the clearing of any other contact record's: no contacts row
    assert {s.split()[1] for s in writes(statements)} == {"provenance_records"}
    assert world.notes(added.contact_id)[0] == ("contact", UK_LEAD_NOTES)


# Row: notes stay on the first contact record (review pass 1, P1)
def test_an_earlier_record_from_a_later_source_takes_the_notes_from_the_old_first(
    world: World,
) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    t1 = T0 + timedelta(hours=1)
    added = world.add(lead_id, ContactCandidate(name="Ann Lee"), retrieved_at=t1)
    assert world.notes(added.contact_id) == [("contact", UK_LEAD_NOTES)]  # record A, at T1

    world.add(
        lead_id,
        ContactCandidate(name="Ann Lee", role="Partner"),
        source=ProvenanceSource.WEBSITE,
        retrieved_at=T0,  # retrieved before A, so the new record B sorts first
    )

    [(b_at, b_notes), (a_at, a_notes)] = world.sql(
        "SELECT retrieved_at, notes FROM provenance_records WHERE contact_id = ? "
        "ORDER BY retrieved_at, id",
        (added.contact_id,),
    )
    assert (b_at, b_notes) == (STAMP, UK_LEAD_NOTES)
    assert (a_at, a_notes) == ("2026-11-18T10:00:00.000000Z", None)


# Row: a name-only fill resolves (review pass 1, P2)
def test_a_name_only_fill_resolves_a_contact_stored_before_story_3_2(world: World) -> None:
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    added = world.add(lead_id, ContactCandidate(email="ann@smith-law.co.uk"))
    world.unresolve()
    assert world.resolved(added.contact_id) == (None, None, None, None)
    world.clock.advance(timedelta(minutes=5))

    world.add(lead_id, ContactCandidate(name="Ann Lee", email="ann@smith-law.co.uk"))

    assert world.resolved(added.contact_id) == (
        "uk",
        "lead_country",
        "Europe/London",
        "market_default",
    )
    assert world.notes(added.contact_id) == [
        ("contact", UK_LEAD_NOTES),  # the first contact record
        ("address", None),
        ("contact", None),  # the record of the filled name
    ]


def test_resolve_market_refuses_an_unknown_contact(world: World) -> None:
    for contact_id in ("01JD3QZ8X5V9K2M7N4P6R8T0WY", "not a ulid"):
        with pytest.raises(LookupError, match="the Contact is unknown"):
            world.resolve(contact_id)


def test_resolution_appends_no_event_and_logs_nothing_personal(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lead_id = world.lead("Smith Law", "smith-law.co.uk", "GB")
    added = world.add(lead_id, ContactCandidate(name="Plantedname", email="p@plantedfirm.de"))
    before = world.sql("SELECT COUNT(*) FROM events")

    world.resolve(added.contact_id)
    world.unresolve()
    with world.units.open():
        world.markets.backfill()

    assert world.sql("SELECT COUNT(*) FROM events") == before
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for planted in ("Plantedname", "plantedfirm", "No Market", ".de"):
        assert planted not in text


# ContactMarkets (Row: Backfill) -----------------------------------------------------------------


def three_old_contacts(world: World) -> list[str]:
    us = world.lead("Jones", "jones.com", "US")
    gb = world.lead("Smith", "smith.co.uk", "GB", later=timedelta(seconds=1))
    ids = [
        world.add(us, ContactCandidate(name="Bo Lee")).contact_id,
        world.add(gb, ContactCandidate(name="Ann", email="ann@smith.co.uk")).contact_id,
        world.add(gb, ContactCandidate(name="Cy", email="cy@smith.de")).contact_id,
    ]
    world.unresolve()
    return ids


def test_queue_backfill_queues_one_bulk_job_and_a_repeat_is_absorbed(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=RESEARCH_LOGGER)
    three_old_contacts(world)

    with world.units.open():
        first = world.markets.queue_backfill()
    with world.units.open():
        again = world.markets.queue_backfill()

    assert (first, again) == (True, True)
    assert world.jobs() == [
        (
            "resolve_contact_markets",
            "bulk",
            "queued",
            "resolve_contact_markets:global:1",
            '{"v":1}',
        )
    ]
    queued = [r for r in caplog.records if r.getMessage() == "contact_markets_queued"]
    assert [getattr(record, "new_job") for record in queued] == [True, False]


def test_the_backfill_resolves_every_old_contact_and_the_next_start_queues_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=RESEARCH_LOGGER)
    ids = three_old_contacts(world)
    main.queue_contact_markets(world.units, world.markets)  # the engine's startup step
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None and claim.job.payload == {"v": 1}
    handler = world.job_types.get("resolve_contact_markets").handler

    with world.units.open():
        assert handler(claim.job) is None
    with world.units.open():
        assert world.queue.complete(claim)

    assert [world.resolved(contact_id) for contact_id in ids] == [
        ("us", "lead_country", "America/New_York", "us_unknown_fallback"),
        ("uk", "lead_country", "Europe/London", "market_default"),
        (None, "cctld_outside_markets", None, "no_market"),
    ]
    assert world.notes(ids[2]) == [("contact", DE_NOTE), ("address", None)]
    [resolved] = [r for r in caplog.records if r.getMessage() == "contact_markets_resolved"]
    assert resolved.count == 3  # type: ignore[attr-defined]
    with world.units.open():
        assert world.markets.queue_backfill() is False
    assert [row[2] for row in world.jobs()] == ["done"]


def test_the_backfill_walks_in_batches_by_id(
    world: World, monkeypatch: pytest.MonkeyPatch, statements: list[str]
) -> None:
    ids = three_old_contacts(world)
    monkeypatch.setattr(research_api, "BACKFILL_BATCH", 2)
    statements.clear()

    with world.units.open():
        count = world.markets.backfill()
    begins = [s for s in statements if s.upper().startswith("BEGIN")]

    assert count == 3
    assert len(begins) == 3  # the Markets check, then two batches (2, then 1)
    assert all(world.contact(contact_id)["market_basis"] is not None for contact_id in ids)


def test_with_no_market_stored_the_backfill_writes_nothing_and_the_next_start_requeues(
    world: World, statements: list[str]
) -> None:
    three_old_contacts(world)
    world.sql("DELETE FROM markets")  # no Contact points to one any more
    with world.units.open():
        assert world.markets.queue_backfill() is True
    with world.units.open():
        claim = world.queue.claim(Lane.BULK)
    assert claim is not None
    before = world.sql("SELECT * FROM contacts ORDER BY id")
    statements.clear()

    with world.units.open():
        assert world.markets.backfill() == 0

    assert writes(statements) == []
    assert world.sql("SELECT * FROM contacts ORDER BY id") == before
    with world.units.open():
        world.queue.complete(claim)
    with world.units.open():
        assert world.markets.queue_backfill() is True  # the next start queues it again
    assert [row[2] for row in world.jobs()] == ["done", "queued"]


def test_queue_backfill_with_every_contact_resolved_queues_nothing(world: World) -> None:
    lead_id = world.lead("Smith", "smith.co.uk", "GB")
    world.add(lead_id, ContactCandidate(name="Ann"))

    with world.units.open():
        assert world.markets.queue_backfill() is False

    assert world.jobs() == []


# The new contacts checks on raw writes ----------------------------------------------------------


def raw_contact(world: World) -> str:
    lead_id = world.lead("Smith", "smith.co.uk", "GB")
    return world.add(lead_id, ContactCandidate(name="Ann")).contact_id


@pytest.mark.parametrize("basis", list(MarketBasis))
def test_the_migrated_check_accepts_every_market_basis(world: World, basis: MarketBasis) -> None:
    contact_id = raw_contact(world)

    world.sql("UPDATE contacts SET market_basis = ? WHERE id = ?", (str(basis), contact_id))

    assert world.contact(contact_id)["market_basis"] == str(basis)


@pytest.mark.parametrize("basis", list(TzBasis))
def test_the_migrated_check_accepts_every_tz_basis(world: World, basis: TzBasis) -> None:
    contact_id = raw_contact(world)

    world.sql("UPDATE contacts SET tz_basis = ? WHERE id = ?", (str(basis), contact_id))

    assert world.contact(contact_id)["tz_basis"] == str(basis)


@pytest.mark.parametrize(
    ("assignments", "check"),
    [
        ("market_basis = 'somewhere'", "ck_contacts_market_basis"),
        ("tz_basis = 'lead_office'", "ck_contacts_tz_basis"),
        ("market_basis = NULL", "ck_contacts_resolved"),
        ("tz_basis = NULL", "ck_contacts_resolved"),
        ("tz = NULL", "ck_contacts_market_tz"),
        ("market_id = NULL", "ck_contacts_market_tz"),
        ("office_country = 'us'", "ck_contacts_office_country"),
        ("office_country = 'USA'", "ck_contacts_office_country"),
        ("office_region = 'NY'", "ck_contacts_office_region"),
        ("office_country = 'US', office_region = 'ny'", "ck_contacts_office_region"),
    ],
)
def test_a_raw_contact_that_breaks_a_new_check_raises_integrity_error(
    world: World, assignments: str, check: str
) -> None:
    contact_id = raw_contact(world)  # a UK Contact: Market, timezone and both bases set

    with pytest.raises(IntegrityError, match=check):
        world.sql(f"UPDATE contacts SET {assignments} WHERE id = ?", (contact_id,))


def test_a_raw_office_with_a_country_and_region_is_accepted(world: World) -> None:
    contact_id = raw_contact(world)

    world.sql("UPDATE contacts SET office_country = 'US', office_region = 'NY'")

    found = world.contact(contact_id)
    assert (found["office_country"], found["office_region"]) == ("US", "NY")


# The 0018 to 0019 upgrade -----------------------------------------------------------------------

SEGMENT, LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0A1", "01JD3QZ8X5V9K2M7N4P6R8T0A2"
ANN, BO = "01JD3QZ8X5V9K2M7N4P6R8T0A3", "01JD3QZ8X5V9K2M7N4P6R8T0A4"
RECORDS = ("01JD3QZ8X5V9K2M7N4P6R8T0A5", "01JD3QZ8X5V9K2M7N4P6R8T0A6", "01JD3QZ8X5V9K2M7N4P6R8T0A7")
OLD_COLUMNS = (
    "id, lead_id, name, role, email, address_class, linkedin_url, is_primary, tz, market_id, "
    "created_at, updated_at"
)


def rows(path: Path, statement: str) -> list[tuple[Any, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(statement).fetchall()
    finally:
        reader.close()


def test_0019_keeps_every_0018_contact_and_record_with_clean_foreign_keys(tmp_path: Path) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0018_4_7_sending_inboxes":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0018_4_7_sending_inboxes",
        )
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{SEGMENT}', 'law', '{{}}', 1, '{STAMP}', '{STAMP}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, country_code, created_at) "
        f"VALUES ('{LEAD}', 'Smith Law', 'smith.co.uk', '{SEGMENT}', 'GB', '{STAMP}')",
        f"INSERT INTO contacts ({OLD_COLUMNS}) VALUES ('{ANN}', '{LEAD}', 'Ann', 'Partner', "
        f"'ann@smith.co.uk', 'guessed', 'https://www.linkedin.com/in/ann', 1, NULL, NULL, "
        f"'{STAMP}', '{STAMP}')",
        f"INSERT INTO contacts ({OLD_COLUMNS}) VALUES ('{BO}', '{LEAD}', 'Bo', NULL, NULL, NULL, "
        f"NULL, 0, NULL, NULL, '{STAMP}', '{STAMP}')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        f"retrieved_at) VALUES ('{RECORDS[0]}', '{ANN}', 'contact', 'csv', 'a list', '{STAMP}')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        f"address, retrieved_at, address_class) VALUES ('{RECORDS[1]}', '{ANN}', 'address', "
        f"'csv', 'a list', 'ann@smith.co.uk', '{STAMP}', 'guessed')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        f"retrieved_at) VALUES ('{RECORDS[2]}', '{BO}', 'contact', 'csv', 'a list', '{STAMP}')",
    )
    contacts_before = rows(path, f"SELECT {OLD_COLUMNS} FROM contacts ORDER BY id")
    records_before = rows(path, "SELECT * FROM provenance_records ORDER BY id")

    migration = SCRIPT_LOCATION / "versions" / "0019_3_2_contact_markets.py"
    shutil.copy2(migration, location / "versions" / migration.name)  # 0019 alone, not later ones
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            "0018_4_7_sending_inboxes",
            "0019_3_2_contact_markets",
        )

    assert rows(path, f"SELECT {OLD_COLUMNS} FROM contacts ORDER BY id") == contacts_before
    new = "market_basis, tz_basis, office_country, office_region"
    assert rows(path, f"SELECT {new} FROM contacts") == [(None,) * 4] * 2
    assert rows(path, "SELECT * FROM provenance_records ORDER BY id") == records_before
    assert rows(path, "PRAGMA foreign_key_check") == []
    columns = [row[1] for row in rows(path, "PRAGMA table_info(contacts)")]
    assert columns[columns.index("market_id") + 1 : columns.index("created_at")] == [
        "market_basis",
        "tz_basis",
        "office_country",
        "office_region",
    ]
    assert rows(path, "PRAGMA index_list(contacts)")[0][1] == "ix_contacts_lead_id"
    referenced = sorted(row[2] for row in rows(path, "PRAGMA foreign_key_list(contacts)"))
    assert referenced == ["leads", "markets"]
    [(table_sql,)] = rows(path, "SELECT sql FROM sqlite_master WHERE name = 'contacts'")
    for check in (
        "ck_contacts_address_class",
        "ck_contacts_email_class",
        "ck_contacts_identity",
        "ck_contacts_market_basis",
        "ck_contacts_tz_basis",
        "ck_contacts_resolved",
        "ck_contacts_market_tz",
        "ck_contacts_office_country",
        "ck_contacts_office_region",
    ):
        assert f"CONSTRAINT {check} CHECK" in table_sql
    assert "_alembic_tmp" not in " ".join(
        str(row[0]) for row in rows(path, "SELECT name FROM sqlite_master")
    )
