"""``compliance.assert_contactable`` and the gated Epic 2 paths (FR-50, FR-15, FR-63, AD-3, AD-16,
Story 3.4) on a temporary database at head with ``FrozenClock``, the real subscriber registry and
real Suppression List entries (stored through the repository, or through ``Compliance.suppress``
for a stored Contact): every row of the spec's I/O matrix. Each reason alone; every reason at
once; a voided ``reply_rule`` entry (voided by raw SQL) that no longer blocks; free webmail never
looked up; a CSV row on a suppressed domain (no Lead, no job, counted apart, and after a stop
only those of the committed batches); an existing Lead
on a suppressed domain (merged, its Contact blocked with the note); a blocked Contact (nothing
stored but one ``contact_blocked`` event); a blocked match (nothing filled); a closed Lead; bad
arguments and unknown subjects (nothing read). The gate runs in a read-only unit of work and
inside a transaction alike, writes nothing and logs nothing, and no ``events`` row holds a
personal value."""

import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import (
    Discovery,
    ImportRequest,
    ImportStopped,
    ImportSummary,
    LeadImports,
    NotedRow,
    UpsertedLead,
)
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.research.api import AddedContact, Research
from nextron.app.setup.api import Setup
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import UnitOfWorkFactory, command, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import (
    Allowed,
    Blocked,
    BlockReason,
    ContactSubject,
    NewContactSubject,
    NewLeadSubject,
    SuppressionScope,
    key_hash,
    suppression_key,
)
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.lead_import import (
    NOTE_CONTACT_LEAD_CLOSED,
    NOTE_CONTACT_SUPPRESSED,
    REASON_SUPPRESSED,
    SuppressedRow,
)
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import SuppressionSource
from tests.fakes.clock import FrozenClock
from tests.fakes.email_syntax import FakeEmailSyntax

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
LATER_STAMP = "2026-11-18T10:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
ORIGIN = "Law Society directory, Manchester, 2026"
UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
WEB = "firm.co.uk"
EMAIL = "jane.doe@firm.co.uk"
NAME = "Jane Doe"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
HEADER = "Organization name,Website,Country,Contact name,Email"
# Text that must never reach events or the log. None of it can occur in a ULID (Crockford base32
# has no I, L, O or U); "jane" could, so it isn't listed.
PLAIN = ("doe", "firm", "linkedin", "gmail", "@")
TABLES = ("leads", "contacts", "provenance_records", "events", "jobs", "suppression_entries")


def csv_bytes(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


@dataclass
class World:
    """Discovery, Research, Compliance and the CSV import on real units of work."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    compliance: Compliance
    imports: LeadImports
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

    def gate(
        self, subject: object, channel: object = "email", purpose: object = "cold"
    ) -> Allowed | Blocked:
        """The gate in a read-only unit of work."""
        gate: Any = compliance_api.assert_contactable
        return self.read(lambda: gate(subject, channel, purpose))

    def upsert(self, web_domain: str, name: str = "Firm LLP") -> UpsertedLead:
        identity = LeadIdentity(name, self.segment, web_domain=web_domain, country_code="GB")
        return self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))

    def lead(self, web_domain: str = WEB) -> str:
        lead_id = self.upsert(web_domain).lead_id
        assert lead_id is not None
        return lead_id

    def add(self, lead_id: str, **fields: Any) -> AddedContact:
        candidate = ContactCandidate(**fields)
        return self.transact(
            lambda: self.research.add_contact(
                lead_id,
                candidate,
                source=ProvenanceSource.CSV,
                detail=ORIGIN,
                retrieved_at=T0,
            )
        )

    def contact(self, lead_id: str, **fields: Any) -> str:
        contact_id = self.add(lead_id, **fields).contact_id
        assert contact_id is not None
        return contact_id

    def entry(self, kind: str, value: str | tuple[str, str], source: str = "operator") -> str:
        """An ``active`` Suppression List entry for the key, stored through the repository."""
        key = suppression_key(kind, value)
        return self.transact(
            lambda: entries_repo.insert(
                kind=str(key.kind),
                value_hash=key.value_hash,
                domain_plain=key.domain_plain,
                source=source,
                cause_ref=None,
                created_at=self.clock.now(),
            )
        )

    def void(self, entry_id: str) -> None:
        """Void a ``reply_rule`` entry by raw SQL, as Story 3.5's reversal will."""
        self.sql(
            "UPDATE suppression_entries SET status = 'voided', voided_at = ?, void_reason = ? "
            "WHERE id = ?",
            (LATER_STAMP, "the reply was misread", entry_id),
        )

    def run(self, data: bytes) -> ImportSummary:
        with self.units.open():
            return self.imports.run(ImportRequest(ORIGIN, "law_private_ai", data))

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

    def set_stage(self, lead_id: str, stage: str) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead_id))

    def events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events ORDER BY rowid"
        )

    def contact_rows(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT id, lead_id, name, role, email, linkedin_url FROM contacts ORDER BY rowid"
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
    found = World(
        units,
        db_engine,
        clock,
        discovery,
        research,
        Compliance(clock),
        LeadImports(clock, discovery, research, FakeEmailSyntax()),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
    return found


@dataclass
class Reads:
    """Every call of the gate's readers."""

    calls: list[tuple[str, tuple[Any, ...]]]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def looked_up(self) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == "active_for"]


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> Iterator[Reads]:
    """Record each read the gate makes, while the real readers still run."""
    found = Reads([])
    for module, name in (
        (research_queries, "contact"),
        (discovery_queries, "lead"),
        (pipeline_queries, "stage"),
        (entries_repo, "active_for"),
    ):
        original = getattr(module, name)

        def recording(*args: Any, _name: str = name, _original: Any = original) -> Any:
            found.calls.append((_name, args))
            return _original(*args)

        monkeypatch.setattr(module, name, recording)
    yield found


def assert_no_personal_data(values: object) -> None:
    text = repr(values).lower()
    for plain in PLAIN:
        assert plain not in text, plain


def blocked(*reasons: BlockReason) -> Blocked:
    return Blocked(reasons)


# Each reason, on real entries -------------------------------------------------------------------


def _address(world: World, lead_id: str) -> object:
    world.entry("address", EMAIL)
    return NewContactSubject(lead_id, email=EMAIL)


def _address_domain(world: World, lead_id: str) -> object:
    world.entry("domain", "firm-group.com")
    return NewContactSubject(lead_id, email="jane@mail.firm-group.com")


def _lead_domain(world: World, lead_id: str) -> object:
    world.entry("domain", WEB)
    return NewContactSubject(lead_id, name=NAME)


def _new_lead_domain(world: World, lead_id: str) -> object:
    world.entry("domain", "www.other-firm.co.uk")
    return NewLeadSubject("other-firm.co.uk")


def _person(world: World, lead_id: str) -> object:
    world.entry("person", (NAME, WEB))
    return NewContactSubject(lead_id, name="  jane   DOE ")


def _profile(world: World, lead_id: str) -> object:
    world.entry("profile", LINKEDIN)
    return NewContactSubject(lead_id, name="J. Doe", linkedin_url=LINKEDIN)


def _stage(stage: str) -> Callable[[World, str], object]:
    def prepare(world: World, lead_id: str) -> object:
        world.set_stage(lead_id, stage)
        return NewContactSubject(lead_id, email=EMAIL, name=NAME, linkedin_url=LINKEDIN)

    return prepare


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (_address, BlockReason.ADDRESS_SUPPRESSED),
        (_address_domain, BlockReason.ADDRESS_DOMAIN_SUPPRESSED),
        (_lead_domain, BlockReason.LEAD_DOMAIN_SUPPRESSED),
        (_new_lead_domain, BlockReason.LEAD_DOMAIN_SUPPRESSED),
        (_person, BlockReason.PERSON_SUPPRESSED),
        (_profile, BlockReason.PROFILE_SUPPRESSED),
        (_stage("lost"), BlockReason.LEAD_LOST),
        (_stage("disqualified"), BlockReason.LEAD_DISQUALIFIED),
    ],
    ids=[
        "address",
        "address domain",
        "lead domain",
        "new lead domain",
        "person",
        "profile",
        "lost",
        "disqualified",
    ],
)
def test_each_reason_blocks_on_its_own(
    world: World, prepare: Callable[[World, str], object], reason: BlockReason
) -> None:
    lead_id = world.lead()
    subject = prepare(world, lead_id)

    assert world.gate(subject) == blocked(reason)


def test_with_no_entry_and_an_open_lead_everything_is_allowed(world: World) -> None:
    lead_id = world.lead()

    for subject in (
        NewContactSubject(lead_id, email=EMAIL, name=NAME, linkedin_url=LINKEDIN),
        NewLeadSubject(WEB),
        NewLeadSubject(None),
    ):
        assert world.gate(subject) == Allowed()


def test_every_reason_at_once_in_enum_order(world: World) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN)
    world.entry("address", EMAIL)
    world.entry("domain", WEB)  # the address's domain and the Lead's web domain
    world.entry("person", (NAME, WEB))
    world.entry("profile", LINKEDIN)
    world.set_stage(lead_id, "lost")

    answer = world.gate(ContactSubject(contact_id), "linkedin", "task")

    assert answer == blocked(
        BlockReason.ADDRESS_SUPPRESSED,
        BlockReason.ADDRESS_DOMAIN_SUPPRESSED,
        BlockReason.LEAD_DOMAIN_SUPPRESSED,
        BlockReason.PERSON_SUPPRESSED,
        BlockReason.PROFILE_SUPPRESSED,
        BlockReason.LEAD_LOST,
    )


def test_a_stored_contact_suppressed_by_compliance_is_blocked_on_every_channel(
    world: World,
) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN)
    world.transact(
        lambda: world.compliance.suppress(
            ContactSubject(contact_id),
            SuppressionScope.PERSON,
            SuppressionSource.OPERATOR,
            None,
        )
    )
    keys = blocked(
        BlockReason.ADDRESS_SUPPRESSED,
        BlockReason.PERSON_SUPPRESSED,
        BlockReason.PROFILE_SUPPRESSED,
    )

    for channel in ("email", "linkedin", "whatsapp", "instagram", "phone"):
        assert world.gate(ContactSubject(contact_id), channel, "follow_up") == keys
    # an opt-out also closes the Lead lost
    world.transact(
        lambda: world.compliance.suppress(
            ContactSubject(contact_id),
            SuppressionScope.PERSON,
            SuppressionSource.REPLY_RULE,
            None,
        )
    )
    with_lost = Blocked((*keys.reasons, BlockReason.LEAD_LOST))
    assert world.gate(ContactSubject(contact_id)) == with_lost


def test_a_voided_reply_rule_entry_no_longer_blocks(world: World) -> None:
    lead_id = world.lead()
    entry_id = world.entry("address", EMAIL, source="reply_rule")
    subject = NewContactSubject(lead_id, email=EMAIL)
    assert world.gate(subject) == blocked(BlockReason.ADDRESS_SUPPRESSED)

    world.void(entry_id)

    assert world.gate(subject) == Allowed()
    address = key_hash("address", EMAIL)
    assert world.read(lambda: compliance_queries.active_entry("address", address)) is None


def test_free_webmail_is_never_looked_up(world: World, reads: Reads) -> None:
    lead_id = world.lead()
    world.entry("domain", "gmail.com")  # the writer never stores one; the gate ignores it anyway
    reads.calls.clear()

    answer = world.gate(NewContactSubject(lead_id, email="jane@gmail.com"))

    assert answer == Allowed()
    assert reads.looked_up() == [  # the address, then the Lead's web domain: no gmail.com
        ("address", key_hash("address", "jane@gmail.com")),
        ("domain", key_hash("domain", WEB)),
    ]


def test_a_new_leads_mail_domain_blocks_and_a_webmail_one_is_never_looked_up(
    world: World, reads: Reads
) -> None:
    world.entry("domain", "mail-firm.co.uk")
    world.entry("domain", "gmail.com")
    reads.calls.clear()

    mail = world.gate(NewLeadSubject(None, ("mail-firm.co.uk",)), "email", "discovery")
    looked_up = reads.looked_up()
    webmail = world.gate(NewLeadSubject(None, ("gmail.com",)), "email", "discovery")

    assert mail == blocked(BlockReason.LEAD_DOMAIN_SUPPRESSED)
    assert looked_up == [("domain", key_hash("domain", "mail-firm.co.uk"))]
    assert webmail == Allowed()
    assert reads.looked_up() == looked_up  # gmail.com was never looked up


def test_the_queries_read_only_active_keys(world: World) -> None:
    active = world.entry("address", EMAIL)
    world.void(world.entry("address", "old@firm.co.uk", source="reply_rule"))
    keys = (
        suppression_key("address", EMAIL),
        suppression_key("address", "old@firm.co.uk"),
        suppression_key("address", EMAIL),
        suppression_key("domain", WEB),
    )

    found = world.read(lambda: compliance_queries.active_keys(keys))

    assert found == frozenset({("address", key_hash("address", EMAIL))})
    address = key_hash("address", EMAIL)
    stored = world.read(lambda: compliance_queries.active_entry("address", address))
    assert stored is not None and stored.id == active
    pairs: Any = [("address", "x")]
    with pytest.raises(TypeError, match="SuppressionKey"):
        world.read(lambda: compliance_queries.active_keys(pairs))


# A new Lead on a suppressed domain --------------------------------------------------------------


def test_upsert_lead_on_a_suppressed_domain_creates_nothing(world: World) -> None:
    world.entry("domain", "www.suppressed-firm.co.uk")
    before = world.counts()

    upserted = world.upsert("suppressed-firm.co.uk")

    assert upserted == UpsertedLead(
        lead_id=None,
        created=False,
        merged=(),
        blocked=blocked(BlockReason.LEAD_DOMAIN_SUPPRESSED),
    )
    assert world.counts() == before  # no Lead, no events row, no crawl job


def test_a_csv_row_on_a_suppressed_domain_is_counted_apart(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.entry("domain", "suppressed-firm.co.uk")

    summary = world.run(
        csv_bytes(
            HEADER,
            "Suppressed Firm,https://www.suppressed-firm.co.uk/,GB,Jane Doe,"
            "jane@suppressed-firm.co.uk",
            "Open Firm,open-firm.co.uk,GB,Ann Lee,ann@open-firm.co.uk",
            "Suppressed Again,suppressed-firm.co.uk,GB,,",
        )
    )

    assert summary == ImportSummary(
        imported=1,
        merged=0,
        rejected=(),
        noted=(),
        blocked=(),
        suppressed=(SuppressedRow(2, "Suppressed Firm"), SuppressedRow(4, "Suppressed Again")),
    )
    assert summary.suppressed[0].reason == REASON_SUPPRESSED
    assert REASON_SUPPRESSED == "Organization is on the Suppression List"
    assert world.sql("SELECT web_domain FROM leads") == [("open-firm.co.uk",)]
    assert [row[2] for row in world.contact_rows()] == ["Ann Lee"]
    assert world.sql("SELECT COUNT(*) FROM jobs") == [(1,)]  # the open Lead's crawl only
    [record] = [r for r in caplog.records if r.getMessage() == "lead_import_finished"]
    logged = [getattr(record, key) for key in ("imported", "merged", "noted", "suppressed")]
    assert logged == [1, 0, 0, 2]
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records).lower()
    for planted in ("suppressed firm", "suppressed-firm", "jane", "ann lee"):
        assert planted not in text


def test_a_stopped_import_keeps_the_suppressed_rows_of_its_committed_batches(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.entry("domain", "gone4.com")
    world.entry("domain", "gone210.com")
    rows = [  # rows 2 to 301: row 4 (a committed batch) and row 210 (the failed one) suppressed
        f"Firm {n},{'gone' if n in (4, 210) else 'firm'}{n}.com,GB,Person {n},"
        for n in range(2, 302)
    ]
    original = Research.add_contact.__wrapped__  # type: ignore[attr-defined]

    def failing(self: Research, lead_id: str, candidate: ContactCandidate, **kwargs: Any) -> Any:
        if candidate.name == "Person 250":
            raise RuntimeError("the disk is full")
        return original(self, lead_id, candidate, **kwargs)

    monkeypatch.setattr(Research, "add_contact", command(failing))

    with pytest.raises(ImportStopped) as caught:
        world.run(csv_bytes(HEADER, *rows))

    assert caught.value.at_row == 202
    assert caught.value.summary == ImportSummary(
        imported=199,
        merged=0,
        rejected=(),
        noted=(),
        blocked=(),
        suppressed=(SuppressedRow(4, "Firm 4"),),  # row 210's batch was rolled back
    )


def test_an_existing_lead_on_a_suppressed_domain_is_merged_and_its_contact_blocked(
    world: World,
) -> None:
    lead_id = world.lead()
    world.entry("domain", WEB)
    before = world.counts()

    summary = world.run(csv_bytes(HEADER, f"Firm Again,{WEB},GB,Jane Doe,{EMAIL}"))

    assert summary == ImportSummary(
        imported=0,
        merged=1,
        rejected=(),
        noted=(NotedRow(2, "Firm Again", (NOTE_CONTACT_SUPPRESSED,)),),
        blocked=(),
        suppressed=(),
    )
    assert summary.noted[0].notes == ("Contact is on the Suppression List; not imported",)
    assert world.sql("SELECT id FROM leads") == [(lead_id,)]  # returned, never re-created
    assert world.counts() == before | {"events": before["events"] + 1}
    assert world.events()[-1] == (  # the cause is the first reason: the address is on firm.co.uk
        "leads",
        lead_id,
        None,
        "contact_blocked",
        "address_domain_suppressed",
        None,
        "system:research",
        LATER_STAMP,
    )


# A blocked Contact ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "value", "reason"),
    [
        ("address", EMAIL, BlockReason.ADDRESS_SUPPRESSED),
        ("person", (NAME, WEB), BlockReason.PERSON_SUPPRESSED),
        ("profile", LINKEDIN, BlockReason.PROFILE_SUPPRESSED),
    ],
)
def test_a_suppressed_contact_stores_nothing_but_one_event(
    world: World, kind: str, value: str | tuple[str, str], reason: BlockReason
) -> None:
    lead_id = world.lead()
    world.entry(kind, value)
    before = world.counts()

    added = world.add(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN, role="Partner")

    assert added == AddedContact(
        contact_id=None, created=False, email_dropped=False, blocked=blocked(reason)
    )
    assert world.counts() == before | {"events": before["events"] + 1}
    assert world.contact_rows() == []
    assert world.events()[-1] == (
        "leads",
        lead_id,
        None,
        "contact_blocked",
        str(reason),
        None,
        "system:research",
        LATER_STAMP,
    )
    assert_no_personal_data(world.events())


def test_a_csv_contact_whose_person_is_suppressed_gets_the_note(world: World) -> None:
    world.entry("person", (NAME, "new-firm.co.uk"))

    summary = world.run(csv_bytes(HEADER, "New Firm,new-firm.co.uk,GB,Jane Doe,"))

    assert (summary.imported, summary.merged, summary.suppressed) == (1, 0, ())
    assert summary.noted == (NotedRow(2, "New Firm", (NOTE_CONTACT_SUPPRESSED,)),)
    assert world.contact_rows() == []
    assert world.sql("SELECT COUNT(*) FROM leads") == [(1,)]


def test_a_match_whose_merged_values_are_suppressed_is_not_filled(world: World) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name=NAME, email=EMAIL)
    world.entry("address", EMAIL)
    rows = world.contact_rows()
    before = world.counts()

    # the candidate lacks an email: its match's is checked, and it is suppressed
    added = world.add(lead_id, name=NAME, linkedin_url=LINKEDIN, role="Partner")

    assert added == AddedContact(None, False, False, blocked(BlockReason.ADDRESS_SUPPRESSED))
    assert world.contact_rows() == rows == [(contact_id, lead_id, NAME, None, EMAIL, None)]
    assert world.counts() == before | {"events": before["events"] + 1}


OTHER_EMAIL = "jane@firm-mail.co.uk"
OTHER_LINKEDIN = "https://www.linkedin.com/in/jane-doe-2"


@pytest.mark.parametrize(
    ("stored", "candidate", "suppressed", "reason"),
    [
        (  # the stored email is suppressed; the candidate brings another, clean one
            {"email": EMAIL},
            {"email": OTHER_EMAIL, "linkedin_url": LINKEDIN},
            ("address", EMAIL),
            BlockReason.ADDRESS_SUPPRESSED,
        ),
        (  # the stored email is clean; the candidate's other email is suppressed
            {"email": EMAIL},
            {"email": OTHER_EMAIL, "linkedin_url": LINKEDIN},
            ("address", OTHER_EMAIL),
            BlockReason.ADDRESS_SUPPRESSED,
        ),
        (  # the stored LinkedIn URL is suppressed; the candidate brings another
            {"linkedin_url": LINKEDIN},
            {"linkedin_url": OTHER_LINKEDIN, "role": "Partner"},
            ("profile", LINKEDIN),
            BlockReason.PROFILE_SUPPRESSED,
        ),
        (  # the stored LinkedIn URL is clean; the candidate's other one is suppressed
            {"linkedin_url": LINKEDIN},
            {"linkedin_url": OTHER_LINKEDIN, "role": "Partner"},
            ("profile", OTHER_LINKEDIN),
            BlockReason.PROFILE_SUPPRESSED,
        ),
    ],
    ids=["stored email", "candidate email", "stored profile", "candidate profile"],
)
def test_a_match_is_gated_on_its_stored_values_and_the_candidates(
    world: World,
    stored: dict[str, str],
    candidate: dict[str, str],
    suppressed: tuple[str, str],
    reason: BlockReason,
) -> None:
    lead_id = world.lead()
    world.contact(lead_id, name=NAME, **stored)
    world.entry(*suppressed)
    rows = world.contact_rows()
    before = world.counts()

    added = world.add(lead_id, name=NAME, **candidate)  # matched by name

    assert added == AddedContact(None, False, False, blocked(reason))
    assert world.contact_rows() == rows  # nothing filled
    assert world.counts() == before | {"events": before["events"] + 1}
    assert world.events()[-1][3:5] == ("contact_blocked", str(reason))


def test_both_answers_reasons_are_combined_in_enum_order(world: World) -> None:
    lead_id = world.lead()
    world.contact(lead_id, name=NAME, linkedin_url=LINKEDIN)
    world.entry("profile", LINKEDIN)  # the match's
    world.entry("address", OTHER_EMAIL)  # the candidate's

    added = world.add(lead_id, name=NAME, email=OTHER_EMAIL, linkedin_url=OTHER_LINKEDIN)

    assert added.blocked == blocked(
        BlockReason.ADDRESS_SUPPRESSED, BlockReason.PROFILE_SUPPRESSED
    )
    assert world.events()[-1][3:5] == ("contact_blocked", "address_suppressed")


# An answer that is neither Allowed nor Blocked --------------------------------------------------


@pytest.mark.parametrize("answer", [None, True, "allowed"])
def test_upsert_lead_never_proceeds_on_a_foreign_answer(
    world: World, monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    before = world.counts()
    monkeypatch.setattr(compliance_api, "assert_contactable", lambda *args: answer)

    with pytest.raises(TypeError, match=f"assert_contactable answered a {type(answer).__name__}"):
        world.upsert("new-firm.co.uk")

    assert world.counts() == before


@pytest.mark.parametrize("matched", [False, True])
def test_add_contact_never_proceeds_on_a_foreign_answer(
    world: World, monkeypatch: pytest.MonkeyPatch, matched: bool
) -> None:
    lead_id = world.lead()
    if matched:
        world.contact(lead_id, name=NAME)
    answers = iter([Allowed(), None])  # a match's second answer is the foreign one
    monkeypatch.setattr(
        compliance_api,
        "assert_contactable",
        lambda *args: next(answers) if matched else None,
    )
    before = world.counts()
    rows = world.contact_rows()

    with pytest.raises(TypeError, match="assert_contactable answered a NoneType"):
        world.add(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN)

    assert world.counts() == before
    assert world.contact_rows() == rows


def test_a_closed_lead_blocks_its_contacts_with_the_closed_note(world: World) -> None:
    lead_id = world.lead()
    world.set_stage(lead_id, "disqualified")

    summary = world.run(csv_bytes(HEADER, f"Firm Again,{WEB},GB,Jane Doe,{EMAIL}"))

    assert (summary.imported, summary.merged) == (0, 1)
    assert summary.noted == (NotedRow(2, "Firm Again", (NOTE_CONTACT_LEAD_CLOSED,)),)
    assert NOTE_CONTACT_LEAD_CLOSED == (
        "the Lead is Lost or Disqualified, so the Contact was not imported"
    )
    assert world.contact_rows() == []
    world.set_stage(lead_id, "lost")
    assert world.add(lead_id, name=NAME).blocked == blocked(BlockReason.LEAD_LOST)


def test_a_closed_lead_with_a_suppressed_contact_gets_the_suppressed_note(world: World) -> None:
    lead_id = world.lead()
    world.set_stage(lead_id, "lost")
    world.entry("address", EMAIL)

    summary = world.run(csv_bytes(HEADER, f"Firm Again,{WEB},GB,Jane Doe,{EMAIL}"))

    assert summary.noted == (NotedRow(2, "Firm Again", (NOTE_CONTACT_SUPPRESSED,)),)


# Bad arguments and unknown subjects -------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "purpose", "subject", "error"),
    [
        ("fax", "cold", None, ValueError),
        ("email", "spam", None, ValueError),
        (None, "cold", None, TypeError),
        ("email", 3, None, TypeError),
        ("email", "cold", "not a subject", TypeError),
        ("email", "cold", ("lead", UNKNOWN), TypeError),
    ],
)
def test_bad_arguments_raise_before_any_read(
    world: World,
    reads: Reads,
    channel: object,
    purpose: object,
    subject: object,
    error: type[Exception],
) -> None:
    lead_id = world.lead()
    reads.calls.clear()
    chosen = NewContactSubject(lead_id, email=EMAIL) if subject is None else subject

    with pytest.raises(error):
        world.gate(chosen, channel, purpose)

    assert reads.calls == []


def test_an_unknown_contact_or_lead_raises_lookup_error_without_the_id(
    world: World, reads: Reads
) -> None:
    for subject, read in (
        (ContactSubject(UNKNOWN), ["contact"]),
        (NewContactSubject(UNKNOWN, email=EMAIL), ["lead"]),
    ):
        reads.calls.clear()
        with pytest.raises(LookupError) as raised:
            world.gate(subject)
        assert UNKNOWN not in str(raised.value)
        assert reads.names() == read  # nothing read beyond it


def test_a_merged_lead_raises_value_error(world: World, reads: Reads) -> None:
    older = world.lead("older-firm.co.uk")
    world.clock.set(LATER + timedelta(minutes=1))
    newer = world.lead("newer-firm.co.uk")
    world.transact(lambda: world.discovery.merge(older, newer))
    reads.calls.clear()

    with pytest.raises(ValueError, match="merged") as raised:
        world.gate(NewContactSubject(newer, name=NAME))

    assert newer not in str(raised.value)
    assert reads.names() == ["lead"]


# Read-only, nothing written, nothing logged -----------------------------------------------------


def test_the_gate_writes_and_logs_nothing_inside_a_transaction(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN)
    world.entry("person", (NAME, WEB))
    before = world.counts()
    caplog.set_level(logging.DEBUG)

    answers = world.transact(
        lambda: [
            compliance_api.assert_contactable(ContactSubject(contact_id), "email", "cold"),
            compliance_api.assert_contactable(NewLeadSubject(WEB), "phone", "call"),
        ]
    )

    assert answers == [blocked(BlockReason.PERSON_SUPPRESSED), Allowed()]
    assert world.counts() == before
    assert caplog.records == []


def test_no_events_row_holds_a_personal_value(world: World) -> None:
    lead_id = world.lead()
    world.entry("address", EMAIL)
    world.entry("domain", "suppressed-firm.co.uk")
    world.add(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN)
    world.run(
        csv_bytes(
            HEADER,
            f"Firm Again,{WEB},GB,Jane Doe,{EMAIL}",
            "Suppressed Firm,suppressed-firm.co.uk,GB,Jane Doe,jane@suppressed-firm.co.uk",
        )
    )

    events = world.events()
    assert [event[3] for event in events].count("contact_blocked") == 2
    assert_no_personal_data(events)
