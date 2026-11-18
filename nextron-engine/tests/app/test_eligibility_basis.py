"""``Research.record_eligibility_basis`` and Provenance in Eligibility (FR-51, AD-7, AD-16, NFR-3;
Story 3.7) on a temporary database at head with ``FrozenClock``, the real subscriber registry
and the shipped seeds (Segments, Markets, Sequences, Offers and the four draft Campaigns),
through real units of work.

- **The recorder.** A US basis from a live Decision (the 4.8 path, in one unit); a UK basis with
  the Campaign's LIA; the LIA changed later with ``Campaigns.record_lia`` (the first record
  keeps R1, both kept in order); every refusal of the matrix (an ineligible Decision, an
  unknown Contact or Campaign, another Market, a US basis in a UK Campaign, a missing or
  different LIA, an LIA on a US Campaign, a naive ``at``, a ``datetime`` as ``lia_date`` and
  the other type errors) writing nothing; the width (108 and 113 characters stored); nothing
  logged.
- **``add_contact``** refuses ``eligibility_basis`` before the gate and before any write.
- **``first_contact_record``** skips a basis record, so the Market notes never land on one, and
  a basis record is never updated.
- **``provenance_missing``** through ``evaluate_eligibility``: records removed by raw SQL, an
  email without its ``address`` record, only a basis record left; a basis record doesn't count
  as a source.
- **The 0023 to 0024 upgrade** keeps every row, check, the foreign key and the index, and widens
  ``basis_rule`` to 128.
"""

import logging
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Campaigns, Setup, check_seed
from nextron.db.migrations import SCRIPT_LOCATION, ensure_current
from nextron.db.repos.research import provenance as provenance_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.campaign import validate_campaigns
from nextron.domain.contactability import Allowed
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import (
    LEGAL_FORM_LABELS,
    PROVENANCE_MISSING_TEXT,
    UK_BASIS,
    US_BASIS,
    Decision,
    Reason,
    ReasonKind,
)
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import InvalidMarkets, validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from tests.fakes.clock import FrozenClock
from tests.migration_chains import chain, engine_on, run_sql

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
ORIGIN = "Law Society directory, Manchester, 2026"
UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
US_WEB, UK_WEB = "acmelaw.com", "acmelaw.co.uk"
US_EMAIL, UK_EMAIL = "zelda.quill@acmelaw.com", "zelda.quill@acmelaw.co.uk"
NAME = "Zelda Quill"
LTD_BASIS = UK_BASIS.format(label=LEGAL_FORM_LABELS["ltd"])
UK_DECISION = Decision(True, LTD_BASIS, ())
US_DECISION = Decision(True, US_BASIS, ())
R1, R1_DATE = "LIA-R1", date(2026, 10, 1)
R2, R2_DATE = "LIA-R2", date(2026, 11, 2)
TABLES = ("leads", "contacts", "provenance_records", "events", "jobs", "campaigns")
RECORD_KEYS = (
    "id contact_id subject source_kind source_detail address retrieved_at address_class notes "
    "basis_rule basis_campaign_id lia_ref lia_date basis_recorded_at"
).split()
# Text that must never reach a log line or an error message.
PLAIN = ("zelda", "quill", "acmelaw", "lia-r", "corporate subscriber")


def shipped[T](name: str, validate: Callable[[object], T]) -> T:
    return validate(seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8")))


# The world ----------------------------------------------------------------------------------------


@dataclass
class World:
    """Discovery, Research, Compliance and the Campaigns on real units of work."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    compliance: Compliance
    segment: str = ""

    def transact[R](self, fn: Callable[[], R]) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as a service would."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()

    def lead(self, web_domain: str, country: str | None, name: str = "Acme Law") -> str:
        identity = LeadIdentity(name, self.segment, web_domain=web_domain, country_code=country)
        lead_id = self.transact(
            lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)
        ).lead_id
        assert lead_id is not None
        return lead_id

    def contact(
        self,
        lead_id: str,
        source: ProvenanceSource = ProvenanceSource.WEBSITE,
        retrieved_at: datetime = T0,
        **fields: Any,
    ) -> str:
        """A stored Contact; the default source, the website, makes its address Published."""
        candidate = ContactCandidate(**fields)
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=source, detail=ORIGIN, retrieved_at=retrieved_at
            )
        )
        assert added.contact_id is not None
        return added.contact_id

    def us_contact(self) -> str:
        return self.contact(self.lead(US_WEB, "US"), email=US_EMAIL, name=NAME)

    def uk_contact(self) -> str:
        return self.contact(self.lead(UK_WEB, "GB"), email=UK_EMAIL, name=NAME)

    def campaign(self, key: str) -> str:
        [found] = [c.id for c in self.read(setup_queries.campaigns) if c.key == key]
        return found

    def record_lia(self, campaign_id: str, ref: str, day: date) -> None:
        with self.units.open():
            Campaigns(self.clock).record_lia(campaign_id, ref, day.isoformat())

    def record(
        self,
        contact_id: str,
        decision: Decision,
        campaign_id: str,
        lia_ref: str | None = None,
        lia_date: date | None = None,
        at: datetime = LATER,
    ) -> str:
        return self.transact(
            lambda: self.research.record_eligibility_basis(
                contact_id, decision, campaign_id, lia_ref, lia_date, at
            )
        )

    def evaluate(self, contact_id: str) -> Decision:
        with self.units.open(read_only=True):
            return self.compliance.evaluate_eligibility(contact_id)

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        return {table: self.sql(f"SELECT COUNT(*) FROM {table}")[0][0] for table in TABLES}

    def records(self, contact_id: str) -> list[dict[str, Any]]:
        rows = self.sql(
            f"SELECT {', '.join(RECORD_KEYS)} FROM provenance_records WHERE contact_id = ? "
            "ORDER BY retrieved_at, id",
            (contact_id,),
        )
        return [dict(zip(RECORD_KEYS, row, strict=True)) for row in rows]

    def bases(self, contact_id: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self.records(contact_id)
            if record["source_kind"] == "eligibility_basis"
        ]


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
    )
    return JobQueue(clock, job_types)


def seed_all(world: World) -> None:
    """The shipped Segments, Markets, Sequences, Offers and Campaigns, as ``nextron seed sync``
    stores them."""
    checked = check_seed(
        shipped(main.SEGMENTS_FILE_NAME, validate_segments),
        shipped(main.MARKETS_FILE_NAME, validate_markets),
        shipped(main.SEQUENCES_FILE_NAME, validate_sequences),
        shipped(main.OFFERS_FILE_NAME, validate_offers),
        shipped(main.CAMPAIGNS_FILE_NAME, validate_campaigns),
    )
    world.transact(lambda: Setup(world.clock).seed_sync(checked, world.clock.now()))


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    found = World(
        units,
        db_engine,
        clock,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        Compliance(clock),
    )
    seed_all(found)
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
    return found


@pytest.fixture
def statements(world: World) -> Iterator[list[str]]:
    """Every SQL statement the engine runs while the test records."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(" ".join(statement.split()))

    event.listen(world.engine, "before_cursor_execute", record)
    yield seen
    event.remove(world.engine, "before_cursor_execute", record)


def writes(seen: list[str]) -> list[str]:
    return [s for s in seen if s.split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}]


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# The recorder: US and UK --------------------------------------------------------------------------


def test_a_us_basis_from_the_live_decision_in_one_unit(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    contact_id = world.us_contact()
    campaign_id = world.campaign("law_private_ai_us")
    caplog.set_level(logging.DEBUG)

    def enroll() -> str:  # the 4.8 path: evaluate, then record, in one unit
        decision = world.compliance.evaluate_eligibility(contact_id)
        assert decision.eligible
        return world.research.record_eligibility_basis(
            contact_id, decision, campaign_id, None, None, world.clock.now()
        )

    record_id = world.transact(enroll)

    [basis] = world.bases(contact_id)
    assert basis == {
        "id": record_id,
        "contact_id": contact_id,
        "subject": "contact",
        "source_kind": "eligibility_basis",
        "source_detail": US_BASIS,
        "address": None,
        "retrieved_at": stamp(LATER),
        "address_class": None,
        "notes": None,
        "basis_rule": US_BASIS,
        "basis_campaign_id": campaign_id,
        "lia_ref": None,
        "lia_date": None,
        "basis_recorded_at": stamp(LATER),
    }
    assert caplog.records == []  # nothing logged, so no value either


def test_a_uk_basis_holds_the_rule_the_campaign_the_lia_and_the_time(world: World) -> None:
    contact_id = world.uk_contact()
    campaign_id = world.campaign("law_private_ai_uk")
    world.record_lia(campaign_id, R1, R1_DATE)
    at = datetime(2026, 11, 18, 10, 30, tzinfo=UTC)

    record_id = world.record(contact_id, UK_DECISION, campaign_id, R1, R1_DATE, at)

    [basis] = world.bases(contact_id)
    assert basis["id"] == record_id
    assert (basis["subject"], basis["address"], basis["address_class"], basis["notes"]) == (
        "contact",
        None,
        None,
        None,
    )
    assert (basis["source_detail"], basis["basis_rule"]) == (LTD_BASIS, LTD_BASIS)
    assert (basis["basis_campaign_id"], basis["lia_ref"], basis["lia_date"]) == (
        campaign_id,
        R1,
        "2026-10-01",
    )
    assert basis["retrieved_at"] == basis["basis_recorded_at"] == stamp(at)
    with world.units.open(read_only=True):
        [stored] = [
            record
            for record in research_queries.provenance(contact_id)
            if record.source_kind == "eligibility_basis"
        ]
    assert (stored.lia_date, stored.basis_recorded_at) == (R1_DATE, at)


def test_a_later_lia_leaves_the_earlier_basis_as_recorded(world: World) -> None:
    contact_id = world.uk_contact()
    campaign_id = world.campaign("law_private_ai_uk")
    world.record_lia(campaign_id, R1, R1_DATE)
    first = world.record(contact_id, UK_DECISION, campaign_id, R1, R1_DATE, LATER)
    before = world.bases(contact_id)

    world.record_lia(campaign_id, R2, R2_DATE)
    with pytest.raises(ValueError, match="the LIA isn't the Campaign's recorded one"):
        world.record(contact_id, UK_DECISION, campaign_id, R1, R1_DATE, LATER)  # R1 is stale
    second = world.record(
        contact_id, UK_DECISION, campaign_id, R2, R2_DATE, LATER + timedelta(minutes=5)
    )

    bases = world.bases(contact_id)
    assert [basis["id"] for basis in bases] == [first, second]  # oldest first
    assert bases[0] == before[0]  # never updated
    assert [(basis["lia_ref"], basis["lia_date"]) for basis in bases] == [
        (R1, "2026-10-01"),
        (R2, "2026-11-02"),
    ]


def test_each_call_adds_a_record_even_for_the_same_basis(world: World) -> None:
    contact_id = world.us_contact()
    campaign_id = world.campaign("ops_automation_us")

    first = world.record(contact_id, US_DECISION, campaign_id)
    second = world.record(contact_id, US_DECISION, campaign_id)

    assert first != second
    assert [basis["id"] for basis in world.bases(contact_id)] == sorted([first, second])


@pytest.mark.parametrize(
    ("label", "length"),
    [
        (LEGAL_FORM_LABELS["private-limited-guarant-nsc-limited-exemption"], 108),
        ("a" + "b" * 63, 113),  # a 64-character legal-form code with no label
    ],
)
def test_the_longest_bases_round_trip_through_the_recorder(
    world: World, label: str, length: int
) -> None:
    # SQLite doesn't enforce VARCHAR(n): the 0024 PRAGMA table_info test and the drift contract
    # pin the 128-character width itself.
    contact_id = world.uk_contact()
    campaign_id = world.campaign("law_private_ai_uk")
    world.record_lia(campaign_id, R1, R1_DATE)
    rule = UK_BASIS.format(label=label)
    assert len(rule) == length

    world.record(contact_id, Decision(True, rule, ()), campaign_id, R1, R1_DATE)

    [basis] = world.bases(contact_id)
    assert basis["basis_rule"] == basis["source_detail"] == rule


# The recorder: refusals ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One refusal: the Contact (``us``, ``uk``, ``unresolved``, ``unknown`` or a raw id), the
    Campaign's key (or ``unknown``), the arguments and the error."""

    contact: str
    campaign: str
    decision: object
    lia_ref: object = None
    lia_date: object = None
    at: object = LATER
    error: type[Exception] = ValueError
    match: str | None = None


INELIGIBLE = Decision(
    False, None, (Reason(ReasonKind.PROVENANCE_MISSING, PROVENANCE_MISSING_TEXT),)
)
UK_CAMPAIGN, US_CAMPAIGN = "law_private_ai_uk", "law_private_ai_us"
SAME_MARKET = "Market isn't the Campaign's"
WRONG_COUNTRY = "basis isn't for the Campaign's Market"
NEEDS_LIA = "needs the Campaign's recorded LIA"
OTHER_LIA = "isn't the Campaign's recorded one"
NO_LIA = "only a UK basis records an LIA"
NAIVE = datetime(2026, 11, 18, 10)  # a naive datetime, refused
AWARE_LIA_DATE = datetime(2026, 10, 1, tzinfo=UTC)

REFUSALS = [
    pytest.param(Case("us", US_CAMPAIGN, INELIGIBLE, match="eligible"), id="ineligible"),
    pytest.param(
        Case("unknown", US_CAMPAIGN, US_DECISION, error=LookupError, match="Contact is unknown"),
        id="unknown_contact",
    ),
    pytest.param(
        Case("not-a-ulid", US_CAMPAIGN, US_DECISION, error=LookupError, match="Contact is"),
        id="non_ulid_contact",
    ),
    pytest.param(
        Case("us", "unknown", US_DECISION, error=LookupError, match="Campaign is unknown"),
        id="unknown_campaign",
    ),
    pytest.param(
        Case("us", UK_CAMPAIGN, UK_DECISION, R1, R1_DATE, match=SAME_MARKET), id="other_market"
    ),
    pytest.param(
        Case("unresolved", US_CAMPAIGN, US_DECISION, match=SAME_MARKET), id="unresolved_contact"
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, US_DECISION, R1, R1_DATE, match=WRONG_COUNTRY),
        id="us_basis_in_a_uk_campaign",
    ),
    pytest.param(
        Case("us", US_CAMPAIGN, UK_DECISION, match=WRONG_COUNTRY), id="uk_basis_in_a_us_campaign"
    ),
    pytest.param(Case("uk", UK_CAMPAIGN, UK_DECISION, match=NEEDS_LIA), id="missing_lia"),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, R1, None, match=NEEDS_LIA), id="missing_lia_date"
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, None, R1_DATE, match=NEEDS_LIA),
        id="missing_lia_ref",
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, R2, R1_DATE, match=OTHER_LIA),
        id="different_lia_ref",
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, R1, R2_DATE, match=OTHER_LIA),
        id="different_lia_date",
    ),
    pytest.param(
        Case("us", US_CAMPAIGN, US_DECISION, R1, R1_DATE, match=NO_LIA), id="lia_on_a_us_campaign"
    ),
    pytest.param(
        Case("us", US_CAMPAIGN, US_DECISION, None, R1_DATE, match=NO_LIA),
        id="lia_date_on_a_us_campaign",
    ),
    pytest.param(
        Case("us", US_CAMPAIGN, US_DECISION, at=NAIVE, error=TypeError, match="aware datetime"),
        id="naive_at",
    ),
    pytest.param(
        Case("us", US_CAMPAIGN, US_DECISION, at=date(2026, 11, 18), error=TypeError),
        id="date_as_at",
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, R1, AWARE_LIA_DATE, error=TypeError, match="lia_d"),
        id="datetime_as_lia_date",
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, R1, "2026-10-01", error=TypeError, match="lia_d"),
        id="text_as_lia_date",
    ),
    pytest.param(
        Case("uk", UK_CAMPAIGN, UK_DECISION, 7, R1_DATE, error=TypeError, match="lia_ref"),
        id="number_as_lia_ref",
    ),
    pytest.param(
        Case("us", US_CAMPAIGN, {"eligible": True}, error=TypeError, match="Decision"),
        id="not_a_decision",
    ),
]


def contact_for(world: World, which: str) -> object:
    if which == "us":
        return world.us_contact()
    if which == "uk":
        return world.uk_contact()
    if which == "unresolved":  # a Contact whose Market was never resolved
        contact_id = world.us_contact()
        world.sql(
            "UPDATE contacts SET market_id = NULL, tz = NULL, market_basis = NULL, "
            "tz_basis = NULL WHERE id = ?",
            (contact_id,),
        )
        return contact_id
    return UNKNOWN if which == "unknown" else which


@pytest.mark.parametrize("case", REFUSALS)
def test_a_refusal_writes_nothing(
    world: World, case: Case, statements: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    contact_id = contact_for(world, case.contact)
    world.record_lia(world.campaign(UK_CAMPAIGN), R1, R1_DATE)
    campaign_id = UNKNOWN if case.campaign == "unknown" else world.campaign(case.campaign)
    before = world.counts()
    every_record = world.sql("SELECT * FROM provenance_records ORDER BY id")
    statements.clear()
    caplog.set_level(logging.DEBUG)

    with pytest.raises(case.error, match=case.match) as raised:
        world.transact(
            lambda: world.research.record_eligibility_basis(
                contact_id,  # type: ignore[arg-type]
                case.decision,  # type: ignore[arg-type]
                campaign_id,
                case.lia_ref,  # type: ignore[arg-type]
                case.lia_date,  # type: ignore[arg-type]
                case.at,  # type: ignore[arg-type]
            )
        )

    assert world.counts() == before
    assert world.sql("SELECT * FROM provenance_records ORDER BY id") == every_record
    assert writes(statements) == []
    message = str(raised.value).lower()
    for plain in (*PLAIN, str(contact_id).lower(), campaign_id.lower()):
        assert plain not in message, plain
    assert [r for r in caplog.records if r.name.startswith("nextron.app.research")] == []


@pytest.mark.parametrize(
    ("contact_id", "campaign_id"),
    [(7, "x"), (None, "x"), ("x", 7), ("x", None)],
)
def test_ids_that_arent_text_are_type_errors(
    world: World, contact_id: object, campaign_id: object
) -> None:
    before = world.counts()

    with pytest.raises(TypeError):
        world.transact(
            lambda: world.research.record_eligibility_basis(
                contact_id,  # type: ignore[arg-type]
                US_DECISION,
                campaign_id,  # type: ignore[arg-type]
                None,
                None,
                LATER,
            )
        )

    assert world.counts() == before


def test_a_uk_campaign_without_a_recorded_lia_refuses_any_lia(world: World) -> None:
    contact_id = world.uk_contact()
    campaign_id = world.campaign(UK_CAMPAIGN)  # no LIA recorded yet

    for lia_ref, lia_date in ((R1, R1_DATE), (None, None)):
        with pytest.raises(ValueError, match="needs the Campaign's recorded LIA"):
            world.record(contact_id, UK_DECISION, campaign_id, lia_ref, lia_date)

    assert world.bases(contact_id) == []


def test_unreadable_markets_raise_invalid_markets_and_write_nothing(world: World) -> None:
    contact_id = world.us_contact()
    campaign_id = world.campaign(US_CAMPAIGN)
    world.sql("UPDATE markets SET cctlds = 'not json' WHERE code = 'uk'")
    before = world.counts()

    with pytest.raises(InvalidMarkets):
        world.record(contact_id, US_DECISION, campaign_id)

    assert world.counts() == before
    assert world.bases(contact_id) == []


# add_contact refuses the basis source -------------------------------------------------------------


def test_add_contact_refuses_the_eligibility_basis_source_before_the_gate(
    world: World, monkeypatch: pytest.MonkeyPatch, statements: list[str]
) -> None:
    lead_id = world.lead(US_WEB, "US")
    calls: list[object] = []

    def gate(*arguments: object) -> Allowed:
        calls.append(arguments)
        return Allowed()

    monkeypatch.setattr(compliance_api, "assert_contactable", gate)
    before = world.counts()
    statements.clear()

    with pytest.raises(ValueError, match="eligibility_basis"):
        world.transact(
            lambda: world.research.add_contact(
                lead_id,
                ContactCandidate(name=NAME, email=US_EMAIL),
                source=ProvenanceSource.ELIGIBILITY_BASIS,
                detail=ORIGIN,
                retrieved_at=T0,
            )
        )

    assert calls == []
    assert world.counts() == before
    assert writes(statements) == []


# The Market notes never land on a basis record ---------------------------------------------------


def test_first_contact_record_skips_a_basis_record(world: World) -> None:
    lead_id = world.lead(US_WEB, "US")
    # a Contact whose source record is retrieved after the basis is recorded
    contact_id = world.contact(lead_id, email=US_EMAIL, name=NAME, retrieved_at=LATER)
    campaign_id = world.campaign(US_CAMPAIGN)
    basis_id = world.record(contact_id, US_DECISION, campaign_id, at=T0)  # earlier than LATER
    [source] = [
        record
        for record in world.records(contact_id)
        if (record["subject"], record["source_kind"]) == ("contact", "website")
    ]
    assert world.records(contact_id)[0]["id"] == basis_id  # the basis sorts first

    first = world.read(lambda: provenance_repo.first_contact_record(contact_id))
    assert first is not None
    assert first.id == source["id"]

    world.sql("UPDATE provenance_records SET notes = NULL")  # force the notes to be written again
    world.transact(lambda: world.research.resolve_market(contact_id))

    records = {r["id"]: r for r in world.records(contact_id)}
    assert records[source["id"]]["notes"]  # the Market notes landed on the source record
    assert records[basis_id]["notes"] is None
    assert records[basis_id]["basis_rule"] == US_BASIS


def test_a_basis_only_contact_has_no_record_for_its_notes(world: World) -> None:
    contact_id = world.us_contact()
    world.record(contact_id, US_DECISION, world.campaign(US_CAMPAIGN))
    world.sql(
        "DELETE FROM provenance_records WHERE contact_id = ? AND source_kind != "
        "'eligibility_basis'",
        (contact_id,),
    )
    before = world.records(contact_id)

    assert world.read(lambda: provenance_repo.first_contact_record(contact_id)) is None
    world.transact(lambda: world.research.resolve_market(contact_id))

    assert world.records(contact_id) == before


def test_a_basis_record_survives_a_later_source_unchanged(world: World) -> None:
    contact_id = world.us_contact()
    world.record(contact_id, US_DECISION, world.campaign(US_CAMPAIGN))
    before = world.bases(contact_id)

    world.contact(
        world.sql("SELECT lead_id FROM contacts WHERE id = ?", (contact_id,))[0][0],
        source=ProvenanceSource.CSV,
        name=NAME,
        role="Partner",
    )  # fills the role: a new contact record and a re-resolution

    assert world.bases(contact_id) == before


# provenance_missing through evaluate_eligibility --------------------------------------------------


def test_a_contact_with_its_sources_is_eligible_and_a_basis_changes_nothing(world: World) -> None:
    contact_id = world.us_contact()
    before = world.evaluate(contact_id)
    world.record(contact_id, US_DECISION, world.campaign(US_CAMPAIGN))

    assert before == Decision(True, US_BASIS, ())
    assert world.evaluate(contact_id) == before


@pytest.mark.parametrize(
    "delete",
    [
        "DELETE FROM provenance_records WHERE contact_id = ?",
        "DELETE FROM provenance_records WHERE contact_id = ? AND subject = 'address'",
        "DELETE FROM provenance_records WHERE contact_id = ? AND subject = 'contact'",
    ],
    ids=["no_record", "no_address_record", "no_contact_record"],
)
def test_missing_provenance_is_a_reason(world: World, delete: str) -> None:
    contact_id = world.us_contact()
    world.sql(delete, (contact_id,))

    decision = world.evaluate(contact_id)

    assert decision == Decision(
        False, None, (Reason(ReasonKind.PROVENANCE_MISSING, PROVENANCE_MISSING_TEXT),)
    )


def test_an_address_record_for_another_address_doesnt_count(world: World) -> None:
    contact_id = world.us_contact()
    world.sql(
        "UPDATE provenance_records SET address = 'other@acmelaw.com' WHERE contact_id = ? "
        "AND subject = 'address'",
        (contact_id,),
    )

    assert world.evaluate(contact_id).kinds == (ReasonKind.PROVENANCE_MISSING,)


def test_only_a_basis_record_left_is_provenance_missing(world: World) -> None:
    contact_id = world.us_contact()
    world.record(contact_id, US_DECISION, world.campaign(US_CAMPAIGN))
    world.sql(
        "DELETE FROM provenance_records WHERE contact_id = ? AND source_kind != "
        "'eligibility_basis'",
        (contact_id,),
    )

    assert world.evaluate(contact_id).kinds == (ReasonKind.PROVENANCE_MISSING,)


def test_a_contact_without_an_email_needs_only_its_contact_record(world: World) -> None:
    contact_id = world.contact(world.lead(US_WEB, "US"), name=NAME)

    kinds = world.evaluate(contact_id).kinds
    world.sql("DELETE FROM provenance_records WHERE contact_id = ?", (contact_id,))

    assert kinds == (ReasonKind.ADDRESS_NOT_SENDABLE,)
    assert world.evaluate(contact_id).kinds == (
        ReasonKind.ADDRESS_NOT_SENDABLE,
        ReasonKind.PROVENANCE_MISSING,
    )


# The 0023 to 0024 upgrade -------------------------------------------------------------------------

SEGMENT, LEAD = "01JD3QZ8X5V9K2M7N4P6R8T0A1", "01JD3QZ8X5V9K2M7N4P6R8T0A2"
ANN = "01JD3QZ8X5V9K2M7N4P6R8T0A3"
RECORDS = ("01JD3QZ8X5V9K2M7N4P6R8T0A5", "01JD3QZ8X5V9K2M7N4P6R8T0A6", "01JD3QZ8X5V9K2M7N4P6R8T0A7")
CAMPAIGN = "01JD3QZ8X5V9K2M7N4P6R8T0A8"
STAMP = "2026-11-18T09:00:00.000000Z"
CHECKS = (
    "ck_provenance_records_subject",
    "ck_provenance_records_source_kind",
    "ck_provenance_records_address",
    "ck_provenance_records_address_class",
)


def rows(path: Path, statement: str) -> list[tuple[Any, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(statement).fetchall()
    finally:
        reader.close()


def test_0024_keeps_every_record_check_key_and_index_and_widens_basis_rule(
    tmp_path: Path,
) -> None:
    location = chain(tmp_path)
    for revision in sorted((SCRIPT_LOCATION / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")):
        if revision.stem <= "0023_4_11_footer_snapshots":
            shutil.copy2(revision, location / "versions" / revision.name)
    path = tmp_path / "nextron.db"
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            None,
            "0023_4_11_footer_snapshots",
        )
    columns_before = rows(path, "PRAGMA table_info(provenance_records)")
    indexes_before = sorted(row[1:] for row in rows(path, "PRAGMA index_list(provenance_records)"))
    run_sql(
        path,
        "INSERT INTO segments (id, key, definition, version, created_at, updated_at) "
        f"VALUES ('{SEGMENT}', 'law', '{{}}', 1, '{STAMP}', '{STAMP}')",
        "INSERT INTO leads (id, name, web_domain, segment_id, country_code, created_at) "
        f"VALUES ('{LEAD}', 'Smith Law', 'smith.co.uk', '{SEGMENT}', 'GB', '{STAMP}')",
        "INSERT INTO contacts (id, lead_id, name, email, address_class, created_at, updated_at) "
        f"VALUES ('{ANN}', '{LEAD}', 'Ann', 'ann@smith.co.uk', 'guessed', '{STAMP}', '{STAMP}')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        f"retrieved_at, notes) VALUES ('{RECORDS[0]}', '{ANN}', 'contact', 'csv', 'a list', "
        f"'{STAMP}', 'a note')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        f"address, retrieved_at, address_class) VALUES ('{RECORDS[1]}', '{ANN}', 'address', "
        f"'csv', 'a list', 'ann@smith.co.uk', '{STAMP}', 'guessed')",
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        "retrieved_at, basis_rule, basis_campaign_id, lia_ref, lia_date, basis_recorded_at) "
        f"VALUES ('{RECORDS[2]}', '{ANN}', 'contact', 'eligibility_basis', 'a rule', '{STAMP}', "
        f"'{'r' * 64}', '{CAMPAIGN}', 'R1', '2026-10-01', '{STAMP}')",
    )
    before = rows(path, "SELECT * FROM provenance_records ORDER BY id")

    migration = SCRIPT_LOCATION / "versions" / "0024_3_7_basis_rule_width.py"
    shutil.copy2(migration, location / "versions" / migration.name)  # 0024 alone
    with engine_on(path) as engine:
        assert ensure_current(engine, script_location=location) == (
            "0023_4_11_footer_snapshots",
            "0024_3_7_basis_rule_width",
        )

    assert rows(path, "SELECT * FROM provenance_records ORDER BY id") == before
    assert rows(path, "PRAGMA foreign_key_check") == []
    columns = rows(path, "PRAGMA table_info(provenance_records)")
    assert [column[1:] for column in columns] == [
        (*column[1:2], "VARCHAR(128)", *column[3:]) if column[1] == "basis_rule" else column[1:]
        for column in columns_before
    ]
    indexes = sorted(row[1:] for row in rows(path, "PRAGMA index_list(provenance_records)"))
    assert indexes == indexes_before  # the primary key's and ix_provenance_records_contact_id
    assert "ix_provenance_records_contact_id" in [index[0] for index in indexes]
    [(indexed,)] = [
        row[2:] for row in rows(path, "PRAGMA index_info(ix_provenance_records_contact_id)")
    ]
    assert indexed == "contact_id"
    [key] = rows(path, "PRAGMA foreign_key_list(provenance_records)")
    assert (key[2], key[3], key[4]) == ("contacts", "contact_id", "id")
    [(table_sql,)] = rows(path, "SELECT sql FROM sqlite_master WHERE name = 'provenance_records'")
    for check in CHECKS:
        assert f"CONSTRAINT {check} CHECK" in table_sql
    assert "fk_provenance_records_contact_id_contacts" in table_sql
    assert "_alembic_tmp" not in " ".join(
        str(row[0]) for row in rows(path, "SELECT name FROM sqlite_master")
    )
    long_rule = UK_BASIS.format(label="a" + "b" * 63)
    where = f"WHERE id = '{RECORDS[2]}'"
    run_sql(path, f"UPDATE provenance_records SET basis_rule = '{long_rule}' {where}")
    assert rows(path, f"SELECT basis_rule FROM provenance_records {where}") == [(long_rule,)]


def checked_insert(world: World, statement: str, params: tuple[object, ...]) -> None:
    """Run ``statement`` with foreign keys on, as the engine's connections run."""
    connection = sqlite3.connect(world.db_file, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement, params)
    finally:
        connection.close()


BAD_RECORDS = [
    pytest.param("'person', 'csv', NULL, NULL", id="subject"),
    pytest.param("'contact', 'rumour', NULL, NULL", id="source_kind"),
    pytest.param("'address', 'csv', NULL, NULL", id="address"),
    pytest.param("'contact', 'csv', 'a@b.com', NULL", id="address_on_a_contact_record"),
    pytest.param("'address', 'csv', 'a@b.com', 'bounced'", id="address_class"),
]


@pytest.mark.parametrize("values", BAD_RECORDS)
def test_the_checks_still_refuse_a_bad_record_after_the_rebuild(
    world: World, values: str
) -> None:
    contact_id = world.us_contact()

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        checked_insert(
            world,
            "INSERT INTO provenance_records (id, contact_id, subject, source_kind, address, "
            f"address_class, source_detail, retrieved_at) VALUES (?, ?, {values}, 'x', ?)",
            ("01JD3QZ8X5V9K2M7N4P6R8T0B1", contact_id, STAMP),
        )


def test_the_foreign_key_still_refuses_an_unknown_contact(world: World) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        checked_insert(
            world,
            "INSERT INTO provenance_records (id, contact_id, subject, source_kind, "
            "source_detail, retrieved_at) VALUES (?, ?, 'contact', 'csv', 'x', ?)",
            ("01JD3QZ8X5V9K2M7N4P6R8T0B5", UNKNOWN, STAMP),
        )
