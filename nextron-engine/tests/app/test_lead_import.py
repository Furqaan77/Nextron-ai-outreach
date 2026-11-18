"""The CSV Lead import, ``Research.add_contact`` and ``Research.reparent_lead`` (FR-59, FR-9,
FR-15, FR-51; AD-3, AD-4, AD-7) on a temporary database at head, with ``FrozenClock``, the real
subscriber registry and ``FakeEmailSyntax``: Story 2.3's app rows of the I/O matrix (happy
path, refused imports, rejected rows, a failing email, Contact dedup, merge, batch stop), the
queries, and the ``contacts`` and ``provenance_records`` checks, foreign keys and indexes on raw
writes. With the shipped Markets seeded: Story 3.2's rows blocked by Market (a valid country
with no enabled Market imports nothing), and each imported Contact resolved in its batch."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.discovery.api import (
    BATCH_ROWS,
    Discovery,
    ImportRequest,
    ImportStopped,
    ImportSummary,
    LeadImports,
    NotedRow,
)
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import AddedContact, Research
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.address_class import AddressClass
from nextron.domain.contacts import ContactCandidate, ProvenanceSource, ProvenanceSubject
from nextron.domain.lead_import import (
    FILE_NOT_UTF8,
    FILE_TOO_LARGE,
    FILE_TOO_MANY_ROWS,
    MAX_FILE_BYTES,
    MAX_ROWS,
    NOTE_BAD_EMAIL,
    NOTE_EMAIL_DROPPED,
    ORIGIN_LENGTH,
    REASON_BAD_COUNTRY,
    REASON_BAD_WEBSITE,
    REASON_DENIED,
    REASON_EXTRA_CELLS,
    REASON_NO_SEGMENT,
    REASON_SHARED_PLATFORM,
    REASON_UNKNOWN_SEGMENT,
    BlockedRow,
    ImportRefused,
    RejectedRow,
)
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.machines import IllegalTransition
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from tests.fakes.clock import FrozenClock
from tests.fakes.email_syntax import FakeEmailSyntax

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
STAMP = "2026-11-17T09:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
ORIGIN = "Law Society directory, Manchester, 2026"
HEADER = "Organization name,Website,Country"
TABLES = ("leads", "contacts", "provenance_records", "events")


def csv_bytes(*lines: str, bom: bool = False) -> bytes:
    data = "\r\n".join(lines).encode("utf-8") + b"\r\n"
    return b"\xef\xbb\xbf" + data if bom else data


@dataclass
class World:
    """The import, Discovery and Research on real units of work with the registry ``main``
    builds."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    imports: LeadImports
    law: str
    ops: str

    def run(
        self, data: bytes, origin: str = ORIGIN, default: str | None = "law_private_ai"
    ) -> ImportSummary:
        with self.units.open():
            return self.imports.run(ImportRequest(origin, default, data))

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def lead(self, name: str, domain: str, *, later: timedelta = timedelta(0)) -> str:
        self.clock.set(T0 + later)
        identity = LeadIdentity(name, self.law, web_domain=domain, country_code="GB")
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        self.clock.set(T0)
        return upserted.lead_id

    def add(
        self,
        lead_id: str,
        candidate: ContactCandidate,
        source: ProvenanceSource = ProvenanceSource.CSV,
        detail: str = ORIGIN,
    ) -> AddedContact:
        return self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail=detail, retrieved_at=T0
            )
        )

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def count(self, table: str) -> int:
        [(count,)] = self.sql(f"SELECT COUNT(*) FROM {table}")
        return int(count)

    def counts(self) -> dict[str, int]:
        return {table: self.count(table) for table in TABLES}

    def contacts(self) -> list[dict[str, Any]]:
        keys = (
            "id lead_id name role email address_class linkedin_url is_primary tz market_id "
            "market_basis tz_basis office_country office_region"
        )
        rows = self.sql(f"SELECT {keys.replace(' ', ', ')} FROM contacts ORDER BY rowid")
        return [dict(zip(keys.split(), row, strict=True)) for row in rows]

    def records(self, contact_id: str | None = None) -> list[dict[str, Any]]:
        keys = (
            "contact_id subject source_kind source_detail address retrieved_at address_class "
            "notes basis_rule basis_campaign_id lia_ref lia_date basis_recorded_at"
        )
        where, params = ("WHERE contact_id = ?", (contact_id,)) if contact_id else ("", ())
        rows = self.sql(
            f"SELECT {keys.replace(' ', ', ')} FROM provenance_records {where} ORDER BY rowid",
            params,
        )
        return [dict(zip(keys.split(), row, strict=True)) for row in rows]

    def contact_events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events WHERE entity = 'contacts' ORDER BY rowid"
        )

    def market_id(self, code: str) -> str:
        [(market_id,)] = self.sql("SELECT id FROM markets WHERE code = ?", (code,))
        return str(market_id)

    def leads(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT name, web_domain, country_code, segment_id, merged_into FROM leads "
            "ORDER BY rowid"
        )

    def dump(self) -> list[list[Any]]:
        return [self.sql(f"SELECT * FROM {table} ORDER BY rowid") for table in TABLES]


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
        job_boards=no_jobs,
        scores=no_jobs,
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    markets = validate_markets(seed_yaml.parse(SHIPPED_MARKETS.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).sync_markets(markets)
    discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
    research = Research(clock)
    imports = LeadImports(clock, discovery, research, FakeEmailSyntax())
    found = World(units, db_engine, clock, discovery, research, imports, "", "")
    [(law,), (ops,)] = found.sql("SELECT id FROM segments ORDER BY key")
    found.law, found.ops = law, ops
    return found


# The happy path -------------------------------------------------------------------------------


def test_the_happy_path(world: World) -> None:
    data = csv_bytes(
        "Organisation Name,WEBSITE,Country,Contact Name,Email",
        "Smith Law,https://www.smith-law.co.uk/team,UK,Jane Doe,Jane@Smith-Law.co.uk",
        "Smith Law LLP,smith-law.co.uk,GB,,",
        bom=True,
    )

    summary = world.run(data)

    assert summary == ImportSummary(imported=1, merged=1, rejected=(), noted=(), blocked=())
    assert world.leads() == [("Smith Law", "smith-law.co.uk", "GB", world.law, None)]
    [contact] = world.contacts()
    [lead_id] = [row[0] for row in world.sql("SELECT id FROM leads")]
    assert contact | {"id": "-"} == {
        "id": "-",
        "lead_id": lead_id,
        "name": "Jane Doe",
        "role": None,
        "email": "jane@smith-law.co.uk",
        "address_class": "guessed",
        "linkedin_url": None,
        "is_primary": 0,
        "tz": "Europe/London",  # Story 3.2: resolved in the batch's transaction
        "market_id": world.market_id("uk"),
        "market_basis": "lead_country",
        "tz_basis": "market_default",
        "office_country": None,
        "office_region": None,
    }
    blank = dict.fromkeys(
        ("notes", "basis_rule", "basis_campaign_id", "lia_ref", "lia_date", "basis_recorded_at")
    )
    assert world.records() == [
        {
            "contact_id": contact["id"],
            "subject": "contact",
            "source_kind": "csv",
            "source_detail": ORIGIN,
            "address": None,
            "retrieved_at": STAMP,
            "address_class": None,
            **blank,
            "notes": (
                "Market taken from the Lead's country; office location unknown\n"
                "Timezone is the United Kingdom Market's default (Europe/London); neither the "
                "office location nor the Lead's headquarters gave one"
            ),
        },
        {
            "contact_id": contact["id"],
            "subject": "address",
            "source_kind": "csv",
            "source_detail": ORIGIN,
            "address": "jane@smith-law.co.uk",
            "retrieved_at": STAMP,
            "address_class": "guessed",
            **blank,
        },
    ]
    assert world.contact_events() == [
        ("contacts", contact["id"], None, "guessed", "assigned_from_csv", None,
         "system:research", STAMP)
    ]
    lead_events = world.sql("SELECT cause FROM events WHERE entity = 'leads'")
    assert lead_events == [("created_from_csv",)]  # the Lead's origin, with no text


def test_reimporting_the_same_file_writes_nothing_new(world: World) -> None:
    data = csv_bytes(
        HEADER + ",Contact name,Role,Email,LinkedIn URL",
        "Smith Law,smith-law.co.uk,GB,Jane Doe,Partner,jane@smith-law.co.uk,linkedin.com/in/jane",
        "Jones,jones.com,US,Bo Lee,,,",
    )
    world.run(data)
    before = world.dump()
    world.clock.advance(timedelta(days=1))

    summary = world.run(data)

    assert summary == ImportSummary(imported=0, merged=2, rejected=(), noted=(), blocked=())
    assert world.dump() == before


def test_rows_sharing_a_domain_or_matching_a_stored_lead_count_as_merged(world: World) -> None:
    world.lead("Stored Firm", "stored.com")

    summary = world.run(
        csv_bytes(HEADER, "A,a.com,GB", "A again,https://www.a.com/x,GB", "Stored,stored.com,US")
    )

    assert (summary.imported, summary.merged) == (1, 2)
    assert world.leads() == [
        ("Stored Firm", "stored.com", "GB", world.law, None),  # an existing Lead never changes
        ("A", "a.com", "GB", world.law, None),
    ]


def test_a_row_segment_overrides_the_default(world: World) -> None:
    world.run(csv_bytes(HEADER + ",Segment", "A,a.com,GB,Ops-Automation", "B,b.com,GB,"))

    assert [row[3] for row in world.leads()] == [world.ops, world.law]


def test_a_segment_cell_matches_a_stored_key_in_any_case(world: World) -> None:
    world.run(csv_bytes(HEADER + ",Segment", "A,a.com,GB,LAW_PRIVATE_AI"), default="ops_automation")

    assert [row[3] for row in world.leads()] == [world.law]


# Refusals ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "default", "data", "field", "reason"),
    [
        ("ab", "law_private_ai", csv_bytes(HEADER, "A,a.com,GB"), "origin", ORIGIN_LENGTH),
        ("x" * 201, "law_private_ai", csv_bytes(HEADER, "A,a.com,GB"), "origin", ORIGIN_LENGTH),
        (ORIGIN, "law_private_ai", b"x" * (MAX_FILE_BYTES + 1), "file", FILE_TOO_LARGE),
        (
            ORIGIN,
            "law_private_ai",
            csv_bytes(HEADER, *[f"F{n},f{n}.com,GB" for n in range(MAX_ROWS + 1)]),
            "file",
            FILE_TOO_MANY_ROWS,
        ),
        (ORIGIN, "law_private_ai", "Café,cafe.fr,FR".encode("latin-1"), "file", FILE_NOT_UTF8),
        (
            ORIGIN,
            "law_private_ai",
            csv_bytes("Organization name,Country", "A,GB"),
            "file",
            "The header has no website column.",
        ),
        (
            ORIGIN,
            "nope",
            csv_bytes(HEADER, "A,a.com,GB"),
            "default_segment",
            "That Segment isn't stored. Choose one from the list.",
        ),
    ],
)
def test_a_refused_import_writes_nothing(
    world: World,
    origin: str,
    default: str,
    data: bytes,
    field: str,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world.lead("Stored", "stored.com")
    before = world.counts()
    caplog.set_level(logging.INFO, logger="nextron.app.discovery.api")

    with pytest.raises(ImportRefused) as caught:
        world.run(data, origin=origin, default=default)

    assert (caught.value.field, caught.value.reason) == (field, reason)
    assert world.counts() == before
    [record] = [r for r in caplog.records if r.getMessage() == "lead_import_refused"]
    assert getattr(record, "field") == field


def test_the_origin_is_whitespace_collapsed_before_it_is_stored(world: World) -> None:
    world.run(csv_bytes(HEADER + ",Contact", "A,a.com,GB,Ann"), origin="  My \t list  ")

    assert {row["source_detail"] for row in world.records()} == {"My list"}


# Rejected rows ----------------------------------------------------------------------------------


def test_rejected_rows_write_nothing(world: World) -> None:
    data = csv_bytes(
        "Organization name,Website,Country,Segment,Contact name,Email",
        ",blank-name.com,GB,,Ann,ann@blank-name.com",
        "Not a URL,not a url,GB,,Ann,ann@x.com",
        "LinkedIn,https://www.linkedin.com/company/x,GB,,Ann,ann@x.com",
        "Maps,https://www.google.com/maps/place/x,GB,,Ann,ann@x.com",
        "Indeed,uk.indeed.com/jobs,GB,,Ann,ann@x.com",
        "Sites,sites.google.com/view/x,GB,,Ann,ann@x.com",
        "Zed,zed.com,ZZ,,Ann,ann@zed.com",
        "Nope,nope.com,GB,Nope,Ann,ann@nope.com",
        "Extra,extra.com,GB,,Ann,ann@extra.com,surplus",
    )

    before = world.counts()

    summary = world.run(data)

    assert summary == ImportSummary(
        imported=0,
        merged=0,
        rejected=(
            RejectedRow(2, "", "missing organization name"),
            RejectedRow(3, "Not a URL", REASON_BAD_WEBSITE),
            RejectedRow(4, "LinkedIn", REASON_DENIED),
            RejectedRow(5, "Maps", REASON_DENIED),
            RejectedRow(6, "Indeed", REASON_DENIED),
            RejectedRow(7, "Sites", REASON_SHARED_PLATFORM),
            RejectedRow(8, "Zed", REASON_BAD_COUNTRY),
            RejectedRow(9, "Nope", REASON_UNKNOWN_SEGMENT),
            RejectedRow(10, "Extra", REASON_EXTRA_CELLS),
        ),
        noted=(),
        blocked=(),
    )
    assert world.counts() == before


# Row: CSV blocked (Story 3.2)
def test_rows_blocked_by_market_write_nothing_and_the_rest_import_resolved(world: World) -> None:
    data = csv_bytes(
        HEADER + ",Contact name,Email",
        "Maple Law,maple.ca,CA,Ann Lee,ann@maple.ca",
        "Berg Recht,berg.de,de,Bo Berg,",
        "Smith Law,smith.co.uk,GB,Jo Bell,jo@smith.co.uk",
    )

    summary = world.run(data)

    assert summary == ImportSummary(
        imported=1,
        merged=0,
        rejected=(),
        noted=(),
        blocked=(BlockedRow(2, "Maple Law", "CA"), BlockedRow(3, "Berg Recht", "DE")),
    )
    assert [row.reason for row in summary.blocked] == [
        "Country CA isn't an enabled Market",
        "Country DE isn't an enabled Market",
    ]
    assert world.leads() == [("Smith Law", "smith.co.uk", "GB", world.law, None)]
    [contact] = world.contacts()
    assert (contact["name"], contact["market_id"], contact["market_basis"]) == (
        "Jo Bell",
        world.market_id("uk"),
        "lead_country",
    )
    assert (contact["tz"], contact["tz_basis"]) == ("Europe/London", "market_default")
    assert world.count("provenance_records") == 2  # the GB row's contact and address records


def test_with_no_market_stored_every_row_is_blocked(world: World) -> None:
    world.sql("DELETE FROM markets")  # no Contact points to one yet
    before = world.counts()

    summary = world.run(csv_bytes(HEADER, "A,a.com,GB", "B,b.com,US"))

    assert (summary.imported, summary.merged, summary.rejected) == (0, 0, ())
    assert [(row.number, row.country) for row in summary.blocked] == [(2, "GB"), (3, "US")]
    assert world.counts() == before


def test_a_disabled_market_blocks_its_country(world: World) -> None:
    world.sql("UPDATE markets SET enabled = 0 WHERE code = 'us'")

    summary = world.run(csv_bytes(HEADER, "A,a.com,US", "B,b.co.uk,GB"))

    assert summary.blocked == (BlockedRow(2, "A", "US"),)
    assert (summary.imported, summary.rejected) == (1, ())


def test_a_blank_segment_without_a_default_is_rejected(world: World) -> None:
    summary = world.run(csv_bytes(HEADER, "A,a.com,GB"), default=None)

    assert summary.rejected == (RejectedRow(2, "A", REASON_NO_SEGMENT),)
    assert world.count("leads") == 0


def test_an_email_that_fails_the_syntax_check(world: World) -> None:
    summary = world.run(
        csv_bytes(HEADER + ",Contact name,Email", 'Firm,firm.com,GB,Ann Lee,"a,b@firm.com"')
    )

    assert summary == ImportSummary(1, 0, (), (NotedRow(2, "Firm", (NOTE_BAD_EMAIL,)),), ())
    [contact] = world.contacts()
    assert (contact["name"], contact["email"], contact["address_class"]) == ("Ann Lee", None, None)
    assert [row["subject"] for row in world.records()] == ["contact"]
    assert world.contact_events() == []


# Contacts ---------------------------------------------------------------------------------------


def test_contacts_are_deduplicated_within_their_lead(world: World) -> None:
    data = csv_bytes(
        HEADER + ",Contact name,Email",
        "Firm,firm.com,GB,Ann Lee,ann@firm.com",
        "Firm,firm.com,GB,,ann@firm.com",  # the same email
        "Firm,firm.com,GB,Ann Lee,",  # the same name, no email
        "Firm,firm.com,GB,ANN  lee,other@firm.com",  # the same name, another email
    )

    summary = world.run(data)

    assert summary == ImportSummary(1, 3, (), (NotedRow(5, "Firm", (NOTE_EMAIL_DROPPED,)),), ())
    [contact] = world.contacts()
    assert (contact["name"], contact["email"]) == ("Ann Lee", "ann@firm.com")
    assert len(world.contact_events()) == 1  # the email was assigned once
    assert [r["subject"] for r in world.records()] == ["contact", "address"]


def test_a_name_then_an_email_fills_the_email_once(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")
    first = world.add(lead_id, ContactCandidate(name="Ann Lee"))

    second = world.add(lead_id, ContactCandidate(name="ann lee", email="ann@firm.com"))
    third = world.add(lead_id, ContactCandidate(name="Ann Lee", email="ann@firm.com"))
    fourth = world.add(lead_id, ContactCandidate(name="Ann Lee", email="new@firm.com"))

    assert first == AddedContact(first.contact_id, created=True, email_dropped=False)
    assert second == AddedContact(first.contact_id, created=False, email_dropped=False)
    assert third == AddedContact(first.contact_id, created=False, email_dropped=False)
    assert fourth == AddedContact(first.contact_id, created=False, email_dropped=True)
    [contact] = world.contacts()
    assert (contact["name"], contact["email"], contact["address_class"]) == (
        "Ann Lee",
        "ann@firm.com",
        "guessed",
    )
    assert [(r["subject"], r["address"]) for r in world.records()] == [
        ("contact", None),
        ("address", "ann@firm.com"),
    ]
    assert len(world.contact_events()) == 1


def test_a_match_fills_what_it_lacks_with_one_contact_record(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")
    added = world.add(lead_id, ContactCandidate(email="ann@firm.com"))
    world.clock.advance(timedelta(hours=1))

    world.add(
        lead_id,
        ContactCandidate(
            name="Ann",
            role="CEO",
            email="ann@firm.com",
            linkedin_url="https://www.linkedin.com/in/ann",
        ),
        detail="second list",
    )

    [contact] = world.contacts()
    assert (contact["name"], contact["role"], contact["linkedin_url"]) == (
        "Ann",
        "CEO",
        "https://www.linkedin.com/in/ann",
    )
    assert [(r["subject"], r["source_detail"]) for r in world.records(added.contact_id)] == [
        ("contact", ORIGIN),
        ("address", ORIGIN),
        ("contact", "second list"),
    ]
    [(updated,)] = world.sql("SELECT updated_at FROM contacts")
    assert updated == "2026-11-17T10:00:00.000000Z"


def test_a_match_never_overwrites_and_writes_nothing_when_nothing_is_new(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")
    world.add(lead_id, ContactCandidate(name="Ann", role="CEO", email="ann@firm.com"))
    before = world.dump()
    world.clock.advance(timedelta(hours=1))

    added = world.add(lead_id, ContactCandidate(name="Ann", role="Partner", email="ann@firm.com"))

    assert not added.created
    assert world.dump() == before


def test_contacts_on_different_leads_never_match(world: World) -> None:
    one = world.lead("One", "one.com")
    two = world.lead("Two", "two.com")

    world.add(one, ContactCandidate(name="Ann", email="ann@shared.com"))
    world.add(two, ContactCandidate(name="Ann", email="ann@shared.com"))

    assert [c["lead_id"] for c in world.contacts()] == [one, two]


def test_a_website_source_assigns_published(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")

    added = world.add(
        lead_id, ContactCandidate(email="ann@firm.com"), source=ProvenanceSource.WEBSITE
    )

    [contact] = world.contacts()
    assert contact["address_class"] == "published"
    assert world.contact_events()[0][2:5] == (None, "published", "assigned_from_website")
    assert {r["source_kind"] for r in world.records(added.contact_id)} == {"website"}


# add_contact refusals ---------------------------------------------------------------------------


def test_add_contact_refusals_write_nothing(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")
    other = world.lead("Other", "other.com", later=timedelta(seconds=1))
    world.transact(lambda: world.discovery.merge(lead_id, other))
    before = world.dump()
    candidate = ContactCandidate(name="Ann", email="ann@firm.com")

    def add(**changes: Any) -> None:
        arguments: dict[str, Any] = {
            "lead_id": lead_id,
            "candidate": candidate,
            "source": ProvenanceSource.CSV,
            "detail": ORIGIN,
            "retrieved_at": T0,
            **changes,
        }
        lead = arguments.pop("lead_id")
        found = arguments.pop("candidate")
        world.transact(lambda: world.research.add_contact(lead, found, **arguments))

    for changes, error in [
        ({"candidate": {"name": "Ann"}}, TypeError),
        ({"source": "csv"}, TypeError),
        ({"detail": None}, ValueError),
        ({"retrieved_at": datetime(2026, 11, 17)}, TypeError),
        ({"retrieved_at": "2026-11-17"}, TypeError),
        ({"detail": ""}, ValueError),
        ({"detail": " padded "}, ValueError),
        ({"detail": "x" * 2049}, ValueError),
        ({"lead_id": new_id()}, LookupError),
        ({"lead_id": "not a ulid"}, LookupError),
        ({"lead_id": other}, ValueError),  # merged
        ({"source": ProvenanceSource.REFERRAL}, IllegalTransition),  # never yields an address
    ]:
        with pytest.raises(error):
            add(**changes)
    assert world.dump() == before


def test_a_source_that_yields_no_address_may_add_a_contact_without_one(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")

    world.add(lead_id, ContactCandidate(name="Ann"), source=ProvenanceSource.REFERRAL)

    assert [r["source_kind"] for r in world.records()] == ["referral"]


# Merge ------------------------------------------------------------------------------------------


def test_a_merge_moves_the_merged_leads_contacts_to_the_survivor(world: World) -> None:
    a = world.lead("A", "a.com")
    b = world.lead("B", "b.com", later=timedelta(minutes=1))
    world.add(a, ContactCandidate(name="Ann", email="ann@a.com"))
    moved = world.add(b, ContactCandidate(name="Bo", email="bo@b.com")).contact_id
    duplicate = world.add(b, ContactCandidate(name="Ann", email="ann@a.com")).contact_id
    records_before = world.records()
    world.clock.advance(timedelta(hours=2))

    world.transact(lambda: world.discovery.merge(a, b))

    assert [c["lead_id"] for c in world.contacts()] == [a, a, a]  # duplicates kept for 2.10
    assert world.records() == records_before
    updated = dict(world.sql("SELECT id, updated_at FROM contacts"))
    assert updated[moved] == updated[duplicate] == "2026-11-17T11:00:00.000000Z"
    with world.units.open(read_only=True):
        assert [c.id for c in research_queries.contacts(b)] == []
        assert len(research_queries.contacts(a)) == 3


def test_a_merge_clears_moved_primaries_when_the_survivor_has_one(world: World) -> None:
    a = world.lead("A", "a.com")
    b = world.lead("B", "b.com", later=timedelta(minutes=1))
    c = world.lead("C", "c.com", later=timedelta(minutes=2))
    kept = world.add(a, ContactCandidate(name="Ann")).contact_id
    cleared = world.add(b, ContactCandidate(name="Bo")).contact_id
    carried = world.add(c, ContactCandidate(name="Cy")).contact_id
    # the operator's choices (Story 2.10): the selection a merge re-runs keeps them, though
    # none of these Contacts has a decision-maker role
    world.sql("UPDATE contacts SET is_primary = 1, primary_set_by = 'operator'")

    world.transact(lambda: world.discovery.merge(a, b))

    primary = {
        row[0]: row[1:] for row in world.sql("SELECT id, is_primary, primary_set_by FROM contacts")
    }
    assert primary == {kept: (1, "operator"), cleared: (0, None), carried: (1, "operator")}
    world.sql("UPDATE contacts SET is_primary = 0, primary_set_by = NULL WHERE id = ?", (kept,))

    world.transact(lambda: world.discovery.merge(a, c))

    primary = {
        row[0]: row[1:] for row in world.sql("SELECT id, is_primary, primary_set_by FROM contacts")
    }
    # A had no Primary: C's carries over
    assert primary == {kept: (0, None), cleared: (0, None), carried: (1, "operator")}


def test_an_import_that_merges_two_leads_moves_their_contacts(world: World) -> None:
    by_domain = world.lead("Domain", "firm.com")
    world.add(by_domain, ContactCandidate(name="Ann"))
    later = LeadIdentity("CH", world.law, ch_number="12345678")
    world.clock.set(T0 + timedelta(minutes=1))
    by_number = world.transact(lambda: world.discovery.upsert_lead(later, LeadSource.CSV))
    world.add(by_number.lead_id, ContactCandidate(name="Bo"))
    world.clock.set(T0)

    world.transact(
        lambda: world.discovery.upsert_lead(
            LeadIdentity("Both", world.law, web_domain="firm.com", ch_number="12345678"),
            LeadSource.CSV,
        )
    )

    assert {c["lead_id"] for c in world.contacts()} == {by_domain}


# Batch stop -------------------------------------------------------------------------------------


def test_a_failing_batch_stops_the_import_and_keeps_earlier_batches(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bad = '"a,b@firm.com"'  # fails the syntax check, so the row is noted
    rows = [  # rows 2 to 301; row 3 (a committed batch) and row 205 (the failed one) are noted
        f"Firm {n},firm{n}.com,GB,Person {n},{bad if n in (3, 205) else ''}"
        for n in range(2, 302)
    ]
    original = Research.add_contact.__wrapped__  # type: ignore[attr-defined]
    calls: list[int] = []

    def failing(self: Research, lead_id: str, candidate: ContactCandidate, **kwargs: Any) -> Any:
        calls.append(1)
        if candidate.name == "Person 250":
            raise RuntimeError("the disk is full")
        return original(self, lead_id, candidate, **kwargs)

    from nextron.db.uow import command  # noqa: PLC0415

    monkeypatch.setattr(Research, "add_contact", command(failing))
    caplog.set_level(logging.INFO, logger="nextron.app.discovery.api")

    blocked = "Maple Law,maple.ca,CA,Ann Lee,"  # row 302: blocked by Market, in no batch

    with pytest.raises(ImportStopped) as caught:
        world.run(csv_bytes(HEADER + ",Contact,Email", *rows, blocked))

    stopped = caught.value
    assert isinstance(stopped, RuntimeError)
    assert stopped.at_row == 202 == 2 + BATCH_ROWS
    assert stopped.summary == ImportSummary(
        imported=200,
        merged=0,
        rejected=(),
        noted=(NotedRow(3, "Firm 3", (NOTE_BAD_EMAIL,)),),  # row 205's note was rolled back
        blocked=(BlockedRow(302, "Maple Law", "CA"),),  # every blocked row, even after a stop
    )
    assert isinstance(stopped.__cause__, RuntimeError)
    names = [row[0] for row in world.leads()]
    assert names == [f"Firm {n}" for n in range(2, 202)]
    assert world.count("contacts") == 200
    [record] = [r for r in caplog.records if r.getMessage() == "lead_import_stopped"]
    assert (getattr(record, "at_row"), getattr(record, "error")) == (202, "RuntimeError")
    assert not [r for r in caplog.records if r.getMessage() == "lead_import_finished"]


def test_the_finished_log_holds_counts_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.run(
        csv_bytes(
            HEADER + ",Contact,Email",
            "Secretfirm,secretfirm.com,GB,Plantedname,p@secretfirm.com",
            "Plantedblocked,plantedblocked.ca,CA,Otherplanted,o@plantedblocked.ca",
        ),
        origin="Plantedorigin list",
    )

    [record] = [r for r in caplog.records if r.getMessage() == "lead_import_finished"]
    keys = ("imported", "merged", "rejected", "noted", "blocked")
    counts = [getattr(record, key) for key in keys]
    assert counts == [1, 0, 0, 0, 1]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    planted_texts = (
        "Secretfirm",
        "secretfirm",
        "Plantedname",
        "Plantedorigin",
        "Plantedblocked",
        "plantedblocked",
        "Otherplanted",
    )
    for planted in planted_texts:
        assert planted not in text


# Queries ----------------------------------------------------------------------------------------


def test_the_queries(world: World) -> None:
    lead_id = world.lead("Firm", "firm.com")
    added = world.add(lead_id, ContactCandidate(name="Ann", email="ann@firm.com"))

    with world.units.open(read_only=True):
        [contact] = research_queries.contacts(lead_id)
        records = research_queries.provenance(added.contact_id)
        ids = setup_queries.segment_ids()
        assert research_queries.contacts("not a ulid") == ()
        assert research_queries.provenance(new_id()) == ()
        assert research_queries.provenance("x") == ()

    assert (contact.id, contact.email, contact.address_class, contact.is_primary) == (
        added.contact_id,
        "ann@firm.com",
        "guessed",
        False,
    )
    assert contact.created_at == contact.updated_at == T0
    assert sorted(record.subject for record in records) == ["address", "contact"]
    assert all(record.retrieved_at == T0 and record.lia_date is None for record in records)
    assert ids == {world.law, world.ops}


def test_upsert_lead_checks_the_segment_ids(world: World) -> None:
    identity = LeadIdentity("Firm", new_id(), web_domain="firm.com")

    with pytest.raises(Exception, match="segment_id: is not a stored Segment"):
        world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.CSV))


# Raw writes -------------------------------------------------------------------------------------


def raw_contact(world: World, **changes: object) -> str:
    lead_id = changes.pop("lead_id", None) or world.lead("Firm", "firm.com")
    values: dict[str, object] = {
        "id": new_id(),
        "lead_id": lead_id,
        "name": "Ann",
        "email": None,
        "address_class": None,
        "created_at": STAMP,
        "updated_at": STAMP,
        **changes,
    }
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    world.sql(f"INSERT INTO contacts ({columns}) VALUES ({marks})", tuple(values.values()))
    return str(values["id"])


def raw_record(world: World, owner: str, **changes: object) -> None:
    values: dict[str, object] = {
        "id": new_id(),
        "contact_id": owner,
        "subject": "contact",
        "source_kind": "csv",
        "source_detail": ORIGIN,
        "address": None,
        "retrieved_at": STAMP,
        **changes,
    }
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    world.sql(
        f"INSERT INTO provenance_records ({columns}) VALUES ({marks})", tuple(values.values())
    )


@pytest.mark.parametrize("address_class", list(AddressClass))
def test_the_migrated_checks_accept_every_address_class(
    world: World, address_class: AddressClass
) -> None:
    contact_id = raw_contact(world, email="a@b.com", address_class=str(address_class))
    raw_record(
        world, contact_id, subject="address", address="a@b.com", address_class=str(address_class)
    )

    assert world.sql("SELECT address_class FROM contacts") == [(str(address_class),)]
    assert world.sql("SELECT address_class FROM provenance_records") == [(str(address_class),)]


@pytest.mark.parametrize("source", list(ProvenanceSource))
def test_the_migrated_checks_accept_every_provenance_source(
    world: World, source: ProvenanceSource
) -> None:
    contact_id = raw_contact(world)
    raw_record(world, contact_id, source_kind=str(source))
    raw_record(world, contact_id, subject="address", address="a@b.com", source_kind=str(source))

    assert world.sql("SELECT DISTINCT source_kind FROM provenance_records") == [(str(source),)]


@pytest.mark.parametrize("subject", list(ProvenanceSubject))
def test_the_migrated_checks_accept_every_subject(
    world: World, subject: ProvenanceSubject
) -> None:
    contact_id = raw_contact(world)
    address = "a@b.com" if subject is ProvenanceSubject.ADDRESS else None
    raw_record(world, contact_id, subject=str(subject), address=address)

    assert world.sql("SELECT subject FROM provenance_records") == [(str(subject),)]


def test_a_raw_contact_defaults_and_valid_rows(world: World) -> None:
    contact_id = raw_contact(world, email="a@b.com", address_class="verified")
    lead_id = world.contacts()[0]["lead_id"]
    raw_contact(
        world, lead_id=lead_id, name=None, email="c@b.com", address_class="invalid"
    )
    raw_record(world, contact_id, subject="address", address="a@b.com", address_class="verified")
    raw_record(world, contact_id, lia_date="2026-11-17", basis_rule="r")

    assert world.contacts()[0]["is_primary"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"address_class": "guessed"},  # a class without an address
        {"email": "a@b.com"},  # an address without a class
        {"email": "a@b.com", "address_class": "GUESSED"},
        {"email": "a@b.com", "address_class": "bounced"},
        {"name": None},  # neither a name nor an email
        {"lead_id": "01JD3QZ8X5V9K2M7N4P6R8T0WY"},  # fk_contacts_lead_id_leads
        {"market_id": "01JD3QZ8X5V9K2M7N4P6R8T0WY"},  # fk_contacts_market_id_markets
        {"created_at": None},
        {"updated_at": None},
        {"is_primary": None},
    ],
)
def test_a_raw_contact_that_breaks_a_rule_raises_integrity_error(
    world: World, changes: dict[str, object]
) -> None:
    world.lead("Firm", "firm.com")
    [(lead_id,)] = world.sql("SELECT id FROM leads")

    with pytest.raises(IntegrityError):
        raw_contact(world, **{"lead_id": lead_id, **changes})
    assert world.count("contacts") == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "lead"},
        {"source_kind": "places"},
        {"source_kind": "CSV"},
        {"subject": "address"},  # an address record without its address
        {"address": "a@b.com"},  # a contact record with an address
        {"address_class": "bounced"},
        {"contact_id": "01JD3QZ8X5V9K2M7N4P6R8T0WY"},  # fk_provenance_records_contact_id_contacts
        {"source_detail": None},
        {"retrieved_at": None},
        {"subject": None},
    ],
)
def test_a_raw_record_that_breaks_a_rule_raises_integrity_error(
    world: World, changes: dict[str, object]
) -> None:
    contact_id = raw_contact(world)

    with pytest.raises(IntegrityError):
        raw_record(world, contact_id, **changes)
    assert world.count("provenance_records") == 0


def test_a_lead_with_contacts_and_a_contact_with_records_cannot_be_deleted(world: World) -> None:
    contact_id = raw_contact(world)
    raw_record(world, contact_id)

    with pytest.raises(IntegrityError):
        world.sql("DELETE FROM contacts WHERE id = ?", (contact_id,))
    with pytest.raises(IntegrityError):
        world.sql("DELETE FROM leads")


def test_the_indexes_exist(world: World) -> None:
    rows = world.sql("SELECT type, name FROM sqlite_master WHERE type = 'index'")
    indexes = {row[1] for row in rows}

    assert {"ix_contacts_lead_id", "ix_provenance_records_contact_id"} <= indexes
