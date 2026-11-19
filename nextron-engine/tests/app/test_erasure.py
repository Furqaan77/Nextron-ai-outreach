"""Erasing a Contact (FR-52, FR-50, AD-3, AD-4, AD-16, AD-17, NFR-3, Story 3.13) on a temporary
database at head with ``FrozenClock`` and the real subscriber registry: every row of the spec's
I/O matrix on real units of work. A full Contact erased on request (every personal-data column
tombstoned by class, the hashed keys suppressed with source ``dsr``, the void reasons on the
person's keys redacted, the page text of the Lead and of a Lead merged into it nulled with the
hash kept, the request completed); an end-to-end planted-value scan over every table; no
``DELETE`` and no row lost; ``events`` rows and logs free of personal data; the re-add blocked;
a repeat; a hook that fails (rolled back, the request still ``open``); a retention erasure; the
gate and ``suppress`` on an erased Contact; bad references and arguments; ``open_dsr``; the fail
closed check; research's other paths skipping an erased Contact; and raw SQL that every new
trigger refuses."""

import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import ERASURE_INCOMPLETE, ErasureResult, Erasures
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import Playbook
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.app.research.api import ContactMarkets, Research
from nextron.app.setup.api import Setup
from nextron.db.models import Base
from nextron.db.pii_registry import PII, PiiClass
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import Subscribers, UnitOfWorkFactory, command, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import (
    Blocked,
    BlockReason,
    ContactSubject,
    SuppressionScope,
    key_hash,
)
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.crawl import text_hash
from nextron.domain.erasure import REDACTED
from nextron.domain.geo import Location
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.notifications import NOTIFICATIONS, ContactErased, LeadsMerged
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import SuppressionSource
from nextron.domain.widgets import ContactMethod, Detection, MatchSource
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
STAMP = "2026-11-18T10:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
WEB = "firm.co.uk"
REF = "DSR-2026-0042"
OTHER_REF = "DSR-2026-0043"
# Every planted personal value holds "plant", which no ULID, hash, code or time can hold (a ULID
# has no L, a hash no p): the scan below looks for it in every column of every table.
MARK = "plant"
NAME = "Jane Plantdoe"
ROLE = "Plantrole Partner"
EMAIL = "jane.plantdoe@firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-plantdoe"
DETAIL = "Plantdetail directory export"
NOTES = "Plantnote: office in Manchester"
QUOTE = "Plantquote: Jane leads our litigation team."
VALUE = "Plantvalue practice"
PAGE_TEXT = "Plantpage: meet Jane, our partner. Call 0161 496 0000."
MATCHED = "Plantmatch 0161 496 0000"
VOID_REASON = "Plantvoid: Jane wrote back to say yes"
OVERRIDE = "Plantreply: Jane asks what the audit costs."  # Story 4.6's override case
# Story 2.11: two postings on one careers page (the same URL), each naming Jane
POSTING_TITLE = "Plantposting: Assistant to Jane Plantdoe"
POSTING_DESCRIPTION = "Plantdescription: write to Jane about the role."
CAREERS_URL = "https://firm.co.uk/careers"
ADDRESS = key_hash("address", EMAIL)
PERSON = key_hash("person", (NAME, WEB))
PROFILE = key_hash("profile", LINKEDIN)
DOMAIN = key_hash("domain", WEB)
LOGGER = "nextron"
REQUEST_COLUMNS = (
    "id, contact_id, kind, status, request_ref, opened_at, completed_at, receipt_id"
)


@dataclass
class World:
    """Discovery, Research, Compliance and Erasures on real units of work."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    compliance: Compliance
    erasures: Erasures
    segment: str = ""
    statements: list[str] = field(default_factory=list)

    def transact[R](self, fn: Callable[[], R], units: UnitOfWorkFactory | None = None) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as a service would."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with (units or self.units).open():
            return caller()

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()

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

    def lead(self, web_domain: str | None = WEB, name: str = "Firm LLP") -> str:
        identity = LeadIdentity(name=name, segment_id=self.segment, web_domain=web_domain)
        return self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)).lead_id

    def contact(self, lead_id: str, detail: str = DETAIL, **fields: Any) -> str | None:
        candidate = ContactCandidate(**fields)
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id,
                candidate,
                source=ProvenanceSource.CSV,
                detail=detail,
                retrieved_at=T0,
            )
        )
        return added.contact_id

    def erase(self, contact_id: str, ref: str = REF) -> ErasureResult:
        with self.units.open():
            return self.erasures.erase_on_request(contact_id, ref)

    def erase_in(self, contact_id: str, ref: str | None, cause: str) -> ErasureResult:
        return self.transact(lambda: self.erasures.erase_contact(contact_id, ref, cause))

    def evidence(
        self,
        lead_id: str,
        person: str | None,
        *,
        quote: str | None = QUOTE,
        fact: str | None = None,
        value: str | None = None,
        sought: str | None = None,
    ) -> str:
        return self.transact(
            lambda: signals_repo.insert_evidence(
                lead_id=lead_id,
                evidence_type="quote" if quote is not None else "absence",
                quote=quote,
                sought=sought,
                number=None,
                fact=fact,
                value=value,
                pages_checked=["https://firm.co.uk/team"],
                source="website",
                source_url="https://firm.co.uk/team",
                retrieved_at=T0,
                extracted_by="rule",
                person_contact_id=person,
                at=T0,
            )
        )

    def page(self, lead_id: str, url: str, text: str = PAGE_TEXT, matched: str = MATCHED) -> str:
        def store() -> str:
            run_id = crawl_repo.insert_run(
                lead_id=lead_id,
                site_domain=WEB,
                final_domain=None,
                started_at=T0,
                ended_at=T0,
                outcome="ok",
                error_code=None,
                pages_fetched=1,
                pages_skipped_robots=0,
                renders=0,
                errors=0,
            )
            page_id = crawl_repo.insert_page(
                lead_id=lead_id,
                run_id=run_id,
                url=url,
                final_url=url,
                kind="team",
                fetched_at=T0,
                http_status=200,
                content_hash=text_hash(text),
                text=text,
                text_purge_after=T0 + timedelta(days=30),
                changed_at=T0,
                mailto=(EMAIL,),  # Story 2.13: a published address, nulled with the text
            )
            detection = Detection(ContactMethod.PHONE, "tel", MatchSource.TEXT, matched)
            crawl_repo.add_contact_methods(page_id, [detection], T0)
            return page_id

        return self.transact(store)

    def voided(
        self,
        entry_id: str,
        kind: str,
        value_hash: str,
        reason: str = VOID_REASON,
        domain_plain: str | None = None,
    ) -> str:
        """A ``reply_rule`` entry on the key, voided by raw SQL (Story 3.5 writes one)."""
        self.sql(
            "INSERT INTO suppression_entries (id, kind, value_hash, domain_plain, status, source, "
            "cause_ref, created_at, voided_at, void_reason) VALUES "
            "(?, ?, ?, ?, 'active', 'reply_rule', NULL, ?, NULL, NULL)",
            (entry_id, kind, value_hash, domain_plain, STAMP),
        )
        self.sql(
            "UPDATE suppression_entries SET status = 'voided', voided_at = ?, void_reason = ? "
            "WHERE id = ?",
            (STAMP, reason, entry_id),
        )
        return entry_id

    def counts(self) -> dict[str, int]:
        return {
            table: self.sql(f"SELECT COUNT(*) FROM {table}")[0][0]
            for table in sorted(Base.metadata.tables)
        }

    def requests(self) -> list[tuple[Any, ...]]:
        return self.sql(f"SELECT {REQUEST_COLUMNS} FROM data_subject_requests ORDER BY rowid")

    def entries(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT id, kind, value_hash, status, source, cause_ref, void_reason "
            "FROM suppression_entries ORDER BY rowid"
        )

    def events(self, *entities: str) -> list[tuple[Any, ...]]:
        marks = ",".join("?" for _ in entities)
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            f"FROM events WHERE entity IN ({marks}) ORDER BY rowid",
            entities,
        )

    def contact_row(self, contact_id: str) -> dict[str, Any]:
        names = [str(column.name) for column in Base.metadata.tables["contacts"].columns]
        [row] = self.sql(f"SELECT {', '.join(names)} FROM contacts WHERE id = ?", (contact_id,))
        return dict(zip(names, row, strict=True))

    def dump(self) -> str:
        """Every row of every table, as one lowercase text."""
        return repr(
            [self.sql(f"SELECT * FROM {table}") for table in sorted(Base.metadata.tables)]
        ).lower()


def no_jobs() -> Any:
    raise AssertionError("no job runs in these tests")


def queue_on(clock: FrozenClock) -> JobQueue:
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
        scores=no_jobs,
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> Iterator[World]:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    compliance = Compliance(clock)
    found = World(
        units,
        db_engine,
        clock,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        compliance,
        Erasures(clock, compliance, queue=queue_on(clock)),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        found.statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    yield found
    event.remove(db_engine, "before_cursor_execute", record)


@dataclass(frozen=True)
class Planted:
    lead_id: str
    contact_id: str
    merged_lead_id: str
    person_quote: str
    person_fact: str
    person_absence: str
    firm_quote: str
    pages: tuple[str, ...]


def plant(world: World) -> Planted:
    """Jane with every value set, her Provenance (notes too), her Evidence, the Lead's page and
    a merged Lead's page, a voided entry on each of her keys, an override case made from her
    Reply (Story 4.6), and Bob, another Contact."""
    lead_id = world.lead()
    contact_id = world.contact(
        lead_id,
        name=NAME,
        role=ROLE,
        email=EMAIL,
        linkedin_url=LINKEDIN,
        office=Location("GB"),
    )
    assert contact_id is not None
    world.sql("UPDATE provenance_records SET notes = ? WHERE contact_id = ?", (NOTES, contact_id))
    world.contact(lead_id, "Bob's own export", name="Bob Other", email="bob@firm.co.uk")
    merged_lead_id = world.lead("firm-old.co.uk", "Firm Old LLP")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead_id, merged_lead_id))
    pages = (
        world.page(lead_id, "https://firm.co.uk/team"),
        world.page(merged_lead_id, "https://firm.co.uk/team"),  # stayed on the merged Lead
    )
    person_quote = world.evidence(lead_id, contact_id)
    person_fact = world.evidence(lead_id, contact_id, fact="what_we_do", value=VALUE)
    person_absence = world.evidence(lead_id, contact_id, quote=None, sought="direct line")
    firm_quote = world.evidence(lead_id, None, quote="We are a firm of 40 lawyers.")
    for entry_id, kind, value_hash in (
        ("01JD3QZ8X5V9K2M7N4P6R8T0A1", "address", ADDRESS),
        ("01JD3QZ8X5V9K2M7N4P6R8T0A2", "person", PERSON),
        ("01JD3QZ8X5V9K2M7N4P6R8T0A3", "profile", PROFILE),
    ):
        world.voided(entry_id, kind, value_hash)
    # a "remove us" on the firm's domain, voided with a reason that names the person
    world.voided("01JD3QZ8X5V9K2M7N4P6R8T0A4", "domain", DOMAIN, domain_plain=WEB)
    for title in (POSTING_TITLE, POSTING_TITLE + " (part time)"):
        world.transact(
            lambda title=title: postings_repo.insert_posting(
                lead_id=lead_id,
                source="careers_page",
                board_token=None,
                url=CAREERS_URL,
                title=title,
                description=POSTING_DESCRIPTION,
                posted_date=None,
                checked_at=T0,
            )
        )
    world.transact(
        lambda: Playbook(world.clock).add_override_case(
            OVERRIDE, "question", contact_id=contact_id, names=(NAME,)
        )
    )
    return Planted(
        lead_id,
        contact_id,
        merged_lead_id,
        person_quote,
        person_fact,
        person_absence,
        firm_quote,
        pages,
    )


def assert_no_personal_data(values: object) -> None:
    # none of these can occur in a ULID (Crockford base32 has no I, L, O or U) or a hash
    text = repr(values).lower()
    for plain in (MARK, "doe", "linkedin", "@"):
        assert plain not in text, plain


# DSR, a full Contact ----------------------------------------------------------------------------


def test_a_dsr_erasure_tombstones_every_personal_column_and_keeps_the_keys(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    planted = plant(world)
    bob_before = world.sql("SELECT * FROM contacts WHERE name = 'Bob Other'")
    lead_before = world.sql("SELECT * FROM leads ORDER BY id")
    firm_before = world.sql("SELECT * FROM evidence WHERE id = ?", (planted.firm_quote,))
    counts = world.counts()
    hashes = world.sql("SELECT id, content_hash FROM crawled_pages ORDER BY id")
    world.statements.clear()

    result = world.erase(planted.contact_id)

    [(request_id, *_)] = world.requests()
    assert result == ErasureResult(
        planted.contact_id, request_id, erased=True, receipt_id=result.receipt_id
    )
    # the Contact: a tombstone
    row = world.contact_row(planted.contact_id)
    assert {name: row[name] for name in ("name", "role", "email", "linkedin_url")} == {
        "name": REDACTED,
        "role": REDACTED,
        "email": ADDRESS,
        "linkedin_url": None,
    }
    assert (row["office_country"], row["office_region"]) == (None, None)
    assert row["erased_at"] == row["updated_at"] == STAMP
    assert row["address_class"] == "guessed"  # none: codes stay
    # its Provenance: by class
    assert sorted(
        world.sql(
            "SELECT subject, source_detail, address, notes FROM provenance_records "
            "WHERE contact_id = ?",
            (planted.contact_id,),
        )
    ) == [("address", REDACTED, ADDRESS, REDACTED), ("contact", REDACTED, None, REDACTED)]
    # its Evidence: quote and value redacted, NULL kept; the firm's Evidence unchanged
    assert world.sql(
        "SELECT quote, value, sought FROM evidence WHERE person_contact_id = ? ORDER BY id",
        (planted.contact_id,),
    ) == [(REDACTED, None, None), (REDACTED, REDACTED, None), (None, None, "direct line")]
    assert world.sql("SELECT * FROM evidence WHERE id = ?", (planted.firm_quote,)) == firm_before
    # the pages of the Lead and of the merged Lead: text NULL, hash kept, matched redacted
    assert world.sql("SELECT id, content_hash FROM crawled_pages ORDER BY id") == hashes
    assert world.sql("SELECT text FROM crawled_pages") == [(None,), (None,)]
    assert world.sql("SELECT matched FROM page_contact_methods") == [(REDACTED,), (REDACTED,)]
    # the keys: three dsr entries for the request, and the voided ones redacted
    entries = world.entries()
    voided = [row for row in entries if row[3] == "voided"]
    assert {(kind, value_hash, reason) for _, kind, value_hash, *_, reason in voided} == {
        ("address", ADDRESS, REDACTED),
        ("person", PERSON, REDACTED),
        ("profile", PROFILE, REDACTED),
        ("domain", DOMAIN, REDACTED),  # the firm's domain key too
    }
    written = [row for row in entries if row[3] == "active"]
    assert [(kind, value_hash, source, cause) for _, kind, value_hash, _, source, cause, _ in
            written] == [
        ("address", ADDRESS, "dsr", request_id),
        ("person", PERSON, "dsr", request_id),
        ("profile", PROFILE, "dsr", request_id),
    ]
    # the request: completed
    assert world.requests() == [
        (request_id, planted.contact_id, "erase", "completed", REF, STAMP, STAMP, result.receipt_id)
    ]
    assert result.receipt_id is not None  # Story 3.14: the request names its receipt
    # the Lead and Bob unchanged
    assert world.sql("SELECT * FROM contacts WHERE name = 'Bob Other'") == bob_before
    assert world.sql("SELECT * FROM leads ORDER BY id") == lead_before
    # no row deleted, no DELETE run; only the request, its entries, events, its receipt (Story
    # 3.14) and the receipt's erase_external jobs were added
    after = world.counts()
    grown = {table for table in counts if after[table] != counts[table]}
    assert grown == {
        "data_subject_requests",
        "suppression_entries",
        "events",
        "erasure_receipts",
        "erasure_receipt_stores",
        "jobs",
    }
    assert all(after[table] >= counts[table] for table in counts)
    assert (after["data_subject_requests"], after["suppression_entries"]) == (1, 7)
    assert not [s for s in world.statements if s.lstrip().upper().startswith("DELETE")]
    # the audit: request opened and completed, three entries, the Contact erased
    contact_events = world.events("contacts")
    assert contact_events[-1] == (
        "contacts",
        planted.contact_id,
        None,
        "erased",
        "dsr",
        None,
        "system:research",
        STAMP,
    )
    assert world.events("data_subject_requests") == [
        ("data_subject_requests", request_id, None, "open", "opened", planted.contact_id,
         "operator", STAMP),
        ("data_subject_requests", request_id, "open", "completed", "completed",
         planted.contact_id, "operator", STAMP),
    ]
    assert [row[4:7] for row in world.events("suppression_entries")] == [
        ("dsr", request_id, "operator")
    ] * 3
    # nothing personal in events or the log; the log line after the commit
    assert_no_personal_data(world.sql("SELECT * FROM events"))
    [line] = [r for r in caplog.records if r.name.startswith(LOGGER) and r.getMessage() ==
              "contact_erased"]
    assert (line.contact_id, line.request_id, line.erased) == (  # type: ignore[attr-defined]
        planted.contact_id,
        request_id,
        True,
    )
    ours = [r for r in caplog.records if r.name.startswith(LOGGER)]
    assert_no_personal_data([(r.getMessage(), r.__dict__) for r in ours])


def test_no_planted_value_survives_in_any_table(world: World) -> None:
    planted = plant(world)
    before = world.dump()
    covered = {
        (table, column)
        for table, columns in PII.items()
        for column, pii_class in columns.items()
        if pii_class is not PiiClass.NONE
        and any(
            MARK in str(value).lower()
            for (value,) in world.sql(f"SELECT {column} FROM {table}")
        )
    }
    assert MARK in before

    world.erase(planted.contact_id)

    assert MARK not in world.dump()  # every column of every table
    # every personal-data column a Contact's data can reach was planted, then cleared
    assert covered == {
        ("contacts", "name"),
        ("contacts", "role"),
        ("contacts", "email"),
        ("contacts", "linkedin_url"),
        ("provenance_records", "source_detail"),
        ("provenance_records", "address"),
        ("provenance_records", "notes"),
        ("evidence", "quote"),
        ("evidence", "value"),
        ("crawled_pages", "text"),
        ("crawled_pages", "mailto"),  # Story 2.13: a published address, nulled with the text
        ("page_contact_methods", "matched"),
        ("suppression_entries", "void_reason"),
        ("ai_test_cases", "input"),  # Story 4.6: playbook's hook erases it
        ("job_postings", "title"),  # Story 2.11
        ("job_postings", "description"),
    }
    not_planted = {
        (table, column)
        for table, columns in PII.items()
        for column, pii_class in columns.items()
        if pii_class is not PiiClass.NONE and (table, column) not in covered
    }
    # office_country is a code ("GB") and the rest are never a Contact's (ERASURE: not_held),
    # or the request reference, which holds no address or name
    assert not_planted == {
        ("contacts", "office_country"),
        ("contacts", "office_region"),
        ("suppression_entries", "value_hash"),
        ("settings", "value"),
        ("footer_snapshots", "text"),
        ("jobs", "last_error"),
        ("backups", "error"),
        ("backups", "offpc_error"),
        ("restore_tests", "detail"),
        ("alerts", "text"),
        ("notifications", "text"),
        ("data_subject_requests", "request_ref"),
        ("erasure_receipts", "request_ref"),  # Story 3.14: the same reference
        # Story 4.12: no Draft text in this world; tests/app/test_draft_revisions.py erases it
        ("draft_revisions", "subject"),
        ("draft_revisions", "body"),
        ("draft_revisions", "instruction"),
        ("draft_revisions", "text_hash"),
        # Story 4.13: nor grounding results; tests/app/test_draft_grounding_rules.py erases them
        ("grounding_results", "flags"),
        # Story 5.2: nor OutboundMessages; tests/app/test_outbound_messages.py erases them
        ("outbound_messages", "to_address"),
        ("outbound_messages", "subject"),
        ("outbound_messages", "body"),
        ("outbound_messages", "unsub_token"),
    }
    assert world.sql(
        "SELECT office_country FROM contacts WHERE id = ?", (planted.contact_id,)
    ) == [(None,)]


# Re-add -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"email": EMAIL}, BlockReason.ADDRESS_SUPPRESSED),
        ({"name": "Other Name", "email": EMAIL}, BlockReason.ADDRESS_SUPPRESSED),
        ({"name": NAME}, BlockReason.PERSON_SUPPRESSED),
        ({"name": "JANE PLANTDOE", "role": "Partner"}, BlockReason.PERSON_SUPPRESSED),
        ({"name": "Someone Else", "linkedin_url": LINKEDIN}, BlockReason.PROFILE_SUPPRESSED),
    ],
)
def test_an_erased_person_found_again_is_blocked(
    world: World, fields: dict[str, str], reason: BlockReason
) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    contacts = world.sql("SELECT COUNT(*) FROM contacts")

    added = world.transact(
        lambda: world.research.add_contact(
            planted.lead_id,
            ContactCandidate(**fields),
            source=ProvenanceSource.WEBSITE,
            detail="https://firm.co.uk/team",
            retrieved_at=LATER,
        )
    )

    assert added.contact_id is None and added.blocked is not None
    assert reason in added.blocked.reasons
    assert world.sql("SELECT COUNT(*) FROM contacts") == contacts


# Repeat -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("ref", [REF, OTHER_REF, "Erased per letter 12"])
def test_erasing_an_erased_contact_changes_nothing_but_completes_its_request(
    world: World, ref: str
) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    contact = world.contact_row(planted.contact_id)
    entries = world.entries()
    pages = world.sql("SELECT * FROM crawled_pages ORDER BY id")
    world.clock.set(LATER + timedelta(days=1))

    result = world.erase(planted.contact_id, ref)

    first, second = world.requests()
    assert result == ErasureResult(
        planted.contact_id, second[0], erased=False, receipt_id=result.receipt_id
    )
    assert second[2:] == ("erase", "completed", ref, "2026-11-19T10:00:00.000000Z",
                          "2026-11-19T10:00:00.000000Z", result.receipt_id)
    assert first[3] == "completed"
    assert world.contact_row(planted.contact_id) == contact
    assert world.entries() == entries
    assert world.sql("SELECT * FROM crawled_pages ORDER BY id") == pages
    assert [row[3] for row in world.events("contacts")].count("erased") == 1


def test_an_erased_contact_completes_an_open_request_with_its_reference(world: World) -> None:
    planted = plant(world)
    world.erase_in(planted.contact_id, None, "retention")
    request_id = world.transact(
        lambda: world.erasures.open_dsr(planted.contact_id, "erase", REF)
    )

    result = world.erase(planted.contact_id, REF)

    assert result == ErasureResult(
        planted.contact_id, request_id, erased=False, receipt_id=result.receipt_id
    )
    assert [row[3] for row in world.requests()] == ["completed"]


# A hook fails -----------------------------------------------------------------------------------


def test_a_failing_hook_rolls_the_erasure_back_and_leaves_the_request_open(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    planted = plant(world)

    def broken(lead_ids: object) -> int:
        raise RuntimeError("the page store broke")

    # research's hook has already tombstoned the Provenance and the Evidence when this fails
    monkeypatch.setattr(crawl_repo, "tombstone_pages", broken)
    kept = ("data_subject_requests", "events", "suppression_entries")
    before = [world.sql(f"SELECT * FROM {table}") for table in sorted(Base.metadata.tables)
              if table not in kept]
    entries = world.entries()

    with pytest.raises(RuntimeError, match="the page store broke"):
        world.erase(planted.contact_id)

    after = [world.sql(f"SELECT * FROM {table}") for table in sorted(Base.metadata.tables)
             if table not in kept]
    assert after == before
    [(request_id, contact_id, kind, status, ref, *_)] = world.requests()
    assert (contact_id, kind, status, ref) == (planted.contact_id, "erase", "open", REF)
    assert [row[3] for row in world.events("data_subject_requests")] == ["open"]
    # the person was suppressed with the request: those entries survive; the redaction didn't
    assert world.entries()[: len(entries)] == entries
    assert [row[1:6] for row in world.entries()[len(entries):]] == [
        ("address", ADDRESS, "active", "dsr", request_id),
        ("person", PERSON, "active", "dsr", request_id),
        ("profile", PROFILE, "active", "dsr", request_id),
    ]
    gate = world.read(
        lambda: compliance_api.assert_contactable(
            ContactSubject(planted.contact_id), "email", "cold"
        )
    )
    assert gate == Blocked(
        (
            BlockReason.ADDRESS_SUPPRESSED,
            BlockReason.PERSON_SUPPRESSED,
            BlockReason.PROFILE_SUPPRESSED,
        )
    )
    assert not [r for r in caplog.records if r.getMessage() == "contact_erased"]

    # a retry with the same reference reuses the open request
    monkeypatch.undo()
    result = world.erase(planted.contact_id)

    assert result == ErasureResult(
        planted.contact_id, request_id, erased=True, receipt_id=result.receipt_id
    )
    assert [row[3] for row in world.requests()] == ["completed"]


def test_a_failing_later_subscriber_rolls_everything_back(db_engine: Engine) -> None:
    clock = FrozenClock(T0)
    compliance = Compliance(clock)
    erasures = Erasures(clock, compliance, queue=queue_on(clock))

    @command
    def failing(n: ContactErased) -> None:
        raise LookupError("a store is unknown")

    real = main.build_subscribers(clock=clock)
    subscribers = Subscribers()
    for kind in NOTIFICATIONS:  # the real registry, then a hook that fails after the others
        for subscriber in real.subscribers_of(kind):
            subscribers.register_subscriber(kind, subscriber)
    subscribers.register_subscriber(ContactErased, failing)
    subscribers.freeze()
    units = UnitOfWorkFactory(db_engine, subscribers)
    world = World(
        units,
        db_engine,
        clock,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        compliance,
        erasures,
    )
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    [(world.segment,), *_] = world.sql("SELECT id FROM segments ORDER BY key")
    planted = plant(world)
    contact = world.contact_row(planted.contact_id)

    with pytest.raises(LookupError, match="a store is unknown"):
        world.erase(planted.contact_id)

    assert world.contact_row(planted.contact_id) == contact
    # the dsr entries were committed with the request; the erasure's writes rolled back
    assert [(row[3], row[4]) for row in world.entries()] == [("voided", "reply_rule")] * 4 + [
        ("active", "dsr")
    ] * 3
    assert {row[6] for row in world.entries()[:4]} == {VOID_REASON}
    assert [row[3] for row in world.requests()] == ["open"]


def test_the_erasure_fails_closed_when_no_hook_tombstones_the_contact(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = plant(world)
    monkeypatch.setattr(contacts_repo, "tombstone", lambda contact_id, at: None)

    with pytest.raises(RuntimeError, match=ERASURE_INCOMPLETE):
        world.erase(planted.contact_id)

    assert world.contact_row(planted.contact_id)["name"] == NAME
    assert world.sql(
        "SELECT source_detail FROM provenance_records WHERE contact_id = ?", (planted.contact_id,)
    ) == [(DETAIL,), (DETAIL,)]
    assert [row[3] for row in world.requests()] == ["open"]


# Retention --------------------------------------------------------------------------------------


def test_a_retention_erasure_tombstones_with_no_entry_and_no_request(world: World) -> None:
    planted = plant(world)
    entries = world.entries()

    result = world.erase_in(planted.contact_id, None, "retention")

    assert result == ErasureResult(
        planted.contact_id, None, erased=True, receipt_id=result.receipt_id
    )
    row = world.contact_row(planted.contact_id)
    assert (row["name"], row["email"], row["erased_at"]) == (REDACTED, ADDRESS, STAMP)
    assert world.entries() == [  # the voided ones are redacted; nothing is written
        (entry[0], *entry[1:6], REDACTED) for entry in entries
    ]
    assert len(entries) == 4
    assert world.requests() == []
    assert world.events("contacts")[-1][3:7] == ("erased", "retention", None, "system:research")
    assert world.events("data_subject_requests", "suppression_entries") == []


OVERRIDE_COLUMNS = "contact_id, input, retired_at"


def override_rows(world: World) -> dict[str, tuple[Any, ...]]:
    """Each override case's ``input`` and ``retired_at``, by its Contact."""
    rows = world.sql(f"SELECT {OVERRIDE_COLUMNS} FROM ai_test_cases WHERE origin = 'override'")
    return {contact_id: (text, retired_at) for contact_id, text, retired_at in rows}


@pytest.mark.parametrize("cause", ["dsr", "retention"])
def test_an_erasure_redacts_and_retires_the_contacts_override_cases(
    world: World, cause: str
) -> None:
    """Story 4.6's override cases (``ai_test_cases.input``, PII ``redact``) are reached through
    playbook's ``ContactErased`` hook, by a data subject request and a retention erasure alike;
    another Contact's case is left as it was."""
    planted = plant(world)
    [(bob,)] = world.sql("SELECT id FROM contacts WHERE name = 'Bob Other'")
    world.transact(
        lambda: Playbook(world.clock).add_override_case(
            "Bob here: not now, try us in spring.", "not_now", contact_id=bob
        )
    )
    before = override_rows(world)
    assert set(before) == {planted.contact_id, bob}
    assert MARK in before[planted.contact_id][0].lower() and before[bob][1] is None

    if cause == "dsr":
        world.erase(planted.contact_id)
    else:
        world.erase_in(planted.contact_id, None, "retention")

    # Jane's case: [erased] and retired now; Bob's: still live, its text kept
    assert override_rows(world) == {planted.contact_id: (REDACTED, STAMP), bob: before[bob]}
    # no row is deleted: both cases are still stored
    assert world.sql("SELECT COUNT(*) FROM ai_test_cases WHERE origin = 'override'") == [(2,)]


def test_an_erasure_tombstones_each_posting_of_the_lead_uniquely(world: World) -> None:
    """Story 2.11's postings (title and description PII ``redact``): every posting of the
    Contact's Lead is tombstoned (fail-safe, like its pages), each title ``[erased] <id>``,
    so two postings of one careers page keep ``uq_job_postings_lead_id_url_title``; another
    Lead's posting is left as it was."""
    planted = plant(world)
    other_lead = world.lead("other.co.uk", "Other LLP")
    world.transact(
        lambda: postings_repo.insert_posting(
            lead_id=other_lead,
            source="careers_page",
            board_token=None,
            url=CAREERS_URL,
            title=POSTING_TITLE,
            description=None,
            posted_date=None,
            checked_at=T0,
        )
    )

    world.erase(planted.contact_id)

    rows = world.sql(
        "SELECT id, lead_id, url, title, description FROM job_postings ORDER BY lead_id, id"
    )
    ours = [row for row in rows if row[1] == planted.lead_id]
    assert [(url, title, text) for posting_id, _, url, title, text in ours] == [
        (CAREERS_URL, f"{REDACTED} {posting_id}", REDACTED) for posting_id, *_ in ours
    ]
    assert len(ours) == 2
    assert [row[3:] for row in rows if row[1] == other_lead] == [(POSTING_TITLE, None)]


def test_a_retention_erasure_takes_no_reference(world: World) -> None:
    planted = plant(world)

    with pytest.raises(ValueError, match="retention erasure takes no request reference"):
        world.erase_in(planted.contact_id, REF, "retention")

    assert world.contact_row(planted.contact_id)["erased_at"] is None


# The gate and suppress on an erased Contact -----------------------------------------------------


def test_the_gate_blocks_an_erased_contact_even_without_an_entry(world: World) -> None:
    planted = plant(world)
    world.erase_in(planted.contact_id, None, "retention")

    answer = world.read(
        lambda: compliance_api.assert_contactable(
            ContactSubject(planted.contact_id), "email", "cold"
        )
    )

    assert answer == Blocked((BlockReason.CONTACT_ERASED,))


def test_the_gate_blocks_a_dsr_erased_contact_with_its_reason(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)

    answer = world.read(
        lambda: compliance_api.assert_contactable(
            ContactSubject(planted.contact_id), "linkedin", "task"
        )
    )

    assert answer == Blocked((BlockReason.CONTACT_ERASED,))


@pytest.mark.parametrize(
    ("scope", "kinds"),
    [(SuppressionScope.PERSON, ["address"]), (SuppressionScope.FIRM, ["address", "domain"])],
)
def test_suppress_on_an_erased_contact_writes_only_the_address_hash(
    world: World, scope: SuppressionScope, kinds: list[str]
) -> None:
    planted = plant(world)
    world.erase_in(planted.contact_id, None, "retention")
    before = len(world.entries())

    result = world.transact(
        lambda: world.compliance.suppress(
            ContactSubject(planted.contact_id), scope, SuppressionSource.ONE_CLICK, None
        )
    )

    written = world.entries()[before:]
    assert [row[0] for row in written] == list(result.written)
    assert [row[1] for row in written] == kinds
    assert written[0][2] == ADDRESS
    if scope is SuppressionScope.FIRM:
        assert written[1][2] == key_hash("domain", WEB)
    assert world.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (planted.lead_id,)) == [
        ("lost",)
    ]


def test_suppress_on_an_erased_contact_without_an_address_writes_nothing(world: World) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name=NAME)
    assert contact_id is not None
    world.erase_in(contact_id, None, "retention")

    result = world.transact(
        lambda: world.compliance.suppress(
            ContactSubject(contact_id), SuppressionScope.PERSON, SuppressionSource.MAILTO, None
        )
    )

    assert (result.written, result.existing) == ((), ())
    assert world.entries() == []


# Bad references and arguments -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("jane@firm.co.uk", "no @"),
        (NAME, "the person's name"),
        ("Plantdoe, Jane (letter)", "the person's name"),
        ("", "1 to 200 characters"),
        ("r" * 201, "1 to 200 characters"),
        (" DSR-1", "1 to 200 characters"),
    ],
)
def test_a_bad_reference_is_refused_and_nothing_is_written(
    world: World, ref: str, message: str
) -> None:
    planted = plant(world)
    counts = world.counts()

    with pytest.raises(ValueError, match=message):
        world.erase(planted.contact_id, ref)
    with pytest.raises(ValueError, match=message):
        world.erase_in(planted.contact_id, ref, "dsr")

    assert world.counts() == counts


@pytest.mark.parametrize(
    ("contact_id", "ref", "cause", "error"),
    [
        (None, REF, "dsr", TypeError),
        ("01JD3QZ8X5V9K2M7N4P6R8T0AB", REF, "dsr", LookupError),
        ("not-a-ulid", REF, "dsr", LookupError),
        ("{contact}", None, "dsr", TypeError),
        ("{contact}", 42, "dsr", TypeError),
        ("{contact}", REF, "purge", ValueError),
        ("{contact}", REF, None, TypeError),
    ],
)
def test_erase_contact_checks_its_arguments(
    world: World, contact_id: str | None, ref: object, cause: object, error: type[Exception]
) -> None:
    planted = plant(world)
    target = planted.contact_id if contact_id == "{contact}" else contact_id
    counts = world.counts()

    with pytest.raises(error):
        world.erase_in(target, ref, cause)  # type: ignore[arg-type]

    assert world.counts() == counts


# open_dsr ---------------------------------------------------------------------------------------


def test_open_dsr_opens_one_request_and_reuses_it(world: World) -> None:
    planted = plant(world)

    first = world.transact(lambda: world.erasures.open_dsr(planted.contact_id, "erase", REF))
    again = world.transact(lambda: world.erasures.open_dsr(planted.contact_id, "erase", REF))
    export = world.transact(lambda: world.erasures.open_dsr(planted.contact_id, "export", REF))
    other = world.transact(
        lambda: world.erasures.open_dsr(planted.contact_id, "erase", OTHER_REF)
    )

    assert first == again and len({first, export, other}) == 3
    assert [(row[0], row[2], row[3], row[4]) for row in world.requests()] == [
        (first, "erase", "open", REF),
        (export, "export", "open", REF),
        (other, "erase", "open", OTHER_REF),
    ]
    assert [row[1:] for row in world.events("data_subject_requests")] == [
        (request_id, None, "open", "opened", planted.contact_id, "operator", STAMP)
        for request_id in (first, export, other)
    ]
    found = world.read(lambda: compliance_queries.open_request(planted.contact_id, "erase", REF))
    assert found is not None and found.id == first
    assert REF not in repr(found)


@pytest.mark.parametrize(
    ("contact_id", "kind", "ref", "error"),
    [
        (None, "erase", REF, TypeError),
        ("{contact}", None, REF, TypeError),
        ("{contact}", "erase", None, TypeError),
        ("{contact}", "delete", REF, ValueError),
        ("not-a-ulid", "erase", REF, ValueError),
        ("{contact}", "erase", "a@b", ValueError),
        ("{contact}", "erase", "Jane Plantdoe DSR", ValueError),
        ("01JD3QZ8X5V9K2M7N4P6R8T0AB", "erase", REF, LookupError),
    ],
)
def test_open_dsr_checks_its_arguments(
    world: World, contact_id: str | None, kind: object, ref: object, error: type[Exception]
) -> None:
    planted = plant(world)
    target = planted.contact_id if contact_id == "{contact}" else contact_id

    with pytest.raises(error) as raised:
        world.transact(lambda: world.erasures.open_dsr(target, kind, ref))  # type: ignore[arg-type]

    assert "plant" not in str(raised.value).lower()
    assert world.requests() == []


# Research's other paths skip an erased Contact ---------------------------------------------------


def test_resolving_an_erased_contact_is_a_no_op(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    row = world.contact_row(planted.contact_id)

    world.transact(lambda: world.research.resolve_market(planted.contact_id))

    assert world.contact_row(planted.contact_id) == row


def test_an_erased_unresolved_contact_is_not_backfilled(world: World) -> None:
    planted = plant(world)
    world.sql(
        "UPDATE contacts SET market_id = NULL, tz = NULL, market_basis = NULL, tz_basis = NULL "
        "WHERE id = ?",
        (planted.contact_id,),
    )
    assert world.read(contacts_repo.has_unresolved)

    world.erase(planted.contact_id)

    assert not world.read(contacts_repo.has_unresolved)
    assert world.read(lambda: contacts_repo.unresolved_ids(None, 10)) == ()
    markets = ContactMarkets(world.clock, world.research, queue_on(world.clock))
    with world.units.open():
        assert markets.queue_backfill() is False


def test_a_merge_moves_an_erased_contact_without_touching_its_tombstone(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    survivor = world.lead("firm-new.co.uk", "Firm New LLP")
    row = world.contact_row(planted.contact_id)

    world.transact(lambda: current().publish(LeadsMerged(survivor, planted.lead_id)))

    moved = world.contact_row(planted.contact_id)
    assert moved["lead_id"] == survivor
    assert {k: v for k, v in moved.items() if k not in ("lead_id", "is_primary", "updated_at")} == {
        k: v for k, v in row.items() if k not in ("lead_id", "is_primary", "updated_at")
    }


def test_a_new_candidate_never_matches_an_erased_contact(world: World) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name="Ann Lee")
    assert contact_id is not None
    world.erase_in(contact_id, None, "retention")

    # retention writes no entry: the same name may come back, as a new Contact
    added = world.contact(lead_id, name="Ann Lee", role="Partner")
    # "Erased" has the tombstone's name key: it is still never a match (nor blocked by it)
    erased_named = world.contact(lead_id, name="Erased")

    assert added is not None and added != contact_id
    assert erased_named is not None and erased_named not in (contact_id, added)
    assert world.contact_row(contact_id)["role"] is None
    assert world.contact_row(erased_named)["name"] == "Erased"


def test_a_dsr_after_a_retention_erasure_suppresses_the_stored_address(world: World) -> None:
    planted = plant(world)
    world.erase_in(planted.contact_id, None, "retention")

    result = world.erase(planted.contact_id)

    [(request_id, *_)] = world.requests()
    assert result == ErasureResult(
        planted.contact_id, request_id, erased=False, receipt_id=result.receipt_id
    )
    active = [row for row in world.entries() if row[3] == "active"]
    assert [row[1:6] for row in active] == [("address", ADDRESS, "active", "dsr", request_id)]
    added = world.transact(
        lambda: world.research.add_contact(
            planted.lead_id,
            ContactCandidate(email=EMAIL),
            source=ProvenanceSource.WEBSITE,
            detail="https://firm.co.uk/team",
            retrieved_at=LATER,
        )
    )
    assert added.contact_id is None and added.blocked is not None
    assert BlockReason.ADDRESS_SUPPRESSED in added.blocked.reasons


def test_an_erased_primary_is_nobodys_primary_and_a_merge_keeps_the_live_one(
    world: World,
) -> None:
    survivor = world.lead()
    jane = world.contact(survivor, name=NAME, email=EMAIL)
    merged = world.lead("firm-old.co.uk", "Firm Old LLP")
    # Story 2.10 (at the merge): a merge selects the survivor's Primary again, so Ann is a
    # decision-maker, and both Primaries are the engine's, with primary_set_by set with is_primary
    ann = world.contact(merged, name="Ann Lee", role="Managing Partner")
    world.sql(
        "UPDATE contacts SET is_primary = 1, primary_set_by = 'system' WHERE id IN (?, ?)",
        (jane, ann),
    )
    assert jane is not None and ann is not None

    world.erase(jane)

    assert (world.contact_row(jane)["is_primary"], world.contact_row(jane)["primary_set_by"]) == (
        0,
        None,
    )
    assert not world.read(lambda: contacts_repo.has_primary(survivor))
    world.transact(lambda: current().publish(LeadsMerged(survivor, merged)))
    assert (world.contact_row(ann)["lead_id"], world.contact_row(ann)["is_primary"]) == (
        survivor,
        1,
    )
    assert world.contact_row(jane)["is_primary"] == 0  # the selection never picks the tombstone
    # the tombstone counted as no Primary: the merge cleared neither Contact
    cleared = "SELECT COUNT(*) FROM events WHERE to_status = 'primary_contact_cleared'"
    assert world.sql(cleared) == [(0,)]


def test_the_queries_read_the_erasure(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    [(request_id, *_)] = world.requests()

    found = world.read(lambda: research_queries.contact(planted.contact_id))
    erasures = world.read(
        lambda: compliance_queries.completed_erasures([planted.contact_id, planted.lead_id])
    )
    merged = world.read(lambda: discovery_queries.merged_lead_ids(planted.lead_id))

    assert found is not None and found.erased_at == LATER
    assert list(erasures) == [planted.contact_id]
    assert erasures[planted.contact_id].id == request_id
    assert merged == (planted.merged_lead_id,)
    assert world.read(lambda: discovery_queries.merged_lead_ids("nope")) == ()
    assert world.read(lambda: compliance_queries.completed_erasures([])) == {}


def test_merged_lead_ids_follow_the_whole_chain_and_a_loop_once(world: World) -> None:
    a, b, c, d = (world.lead(f"chain{n}.co.uk", f"Chain {n}") for n in range(4))
    world.sql("UPDATE leads SET merged_into = ? WHERE id IN (?, ?)", (a, b, c))
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (b, d))

    assert world.read(lambda: discovery_queries.merged_lead_ids(a)) == tuple(sorted((b, c, d)))
    assert world.read(lambda: discovery_queries.merged_lead_ids(d)) == ()
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (d, a))  # a loop
    assert world.read(lambda: discovery_queries.merged_lead_ids(a)) == tuple(sorted((b, c, d)))


# Raw SQL: the triggers --------------------------------------------------------------------------

TOMBSTONE = "an erased Contact is a tombstone"
ERASED_PROVENANCE = "the Provenance of an erased Contact is a tombstone"
EVIDENCE_IMMUTABLE = "Evidence is immutable"
REQUESTS_PERMANENT = "data subject requests are permanent"
OTHER_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AZ"
MERGE_COLUMNS = ("lead_id", "is_primary", "updated_at")


def refused(world: World, message: str, statement: str, params: tuple[object, ...] = ()) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=message):
        world.sql(statement, params)


def contact_columns(world: World) -> list[str]:
    return [str(row[1]) for row in world.sql("PRAGMA table_info(contacts)")]


def test_every_column_of_an_erased_contact_but_a_merges_is_fixed(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    row = world.contact_row(planted.contact_id)
    fixed = [column for column in contact_columns(world) if column not in MERGE_COLUMNS]
    # a new contacts column must join trg_contacts_tombstone: 15 since the merge with Story
    # 2.10's primary_set_by (fixed: a tombstone keeps it NULL and is never Primary again), 18
    # since Story 2.13's email_search_tried, email_search_outcome and email_search_at (0040)
    assert len(fixed) == 18
    assert "primary_set_by" in fixed
    assert {"email_search_tried", "email_search_outcome", "email_search_at"} <= set(fixed)

    for column in fixed:
        # the trigger runs before any check, so any different value shows the refusal
        value = OTHER_ID if column == "id" else "x" if row[column] is None else None
        refused(
            world,
            TOMBSTONE,
            f"UPDATE contacts SET {column} = ? WHERE id = ?",
            (value, planted.contact_id),
        )
        if row[column] is not None and column != "id":
            refused(
                world,
                TOMBSTONE,
                f"UPDATE contacts SET {column} = {column} || 'x' WHERE id = ?",
                (planted.contact_id,),
            )
    other = world.lead("other.co.uk", "Other LLP")
    world.sql(
        "UPDATE contacts SET lead_id = ?, is_primary = 1, updated_at = ? WHERE id = ?",
        (other, "2026-11-20T00:00:00.000000Z", planted.contact_id),
    )
    assert world.contact_row(planted.contact_id)["lead_id"] == other


TOMBSTONE_SET = {
    "name": "'[erased]'",
    "role": "'[erased]'",
    "email": f"'{ADDRESS}'",
    "linkedin_url": "NULL",
    "office_country": "NULL",
    "office_region": "NULL",
}


@pytest.mark.parametrize(
    "changes",
    [
        {"name": None, "role": None, "email": None, "linkedin_url": None,
         "office_country": None},  # erased_at alone
        {"office_country": None},  # the office stays
        {"linkedin_url": None},  # the LinkedIn URL stays
        {"name": None},  # the name stays
        {"role": "'Partner'"},
        {"name": "'Jane'"},
        {"email": "'not-a-hash'"},
        {"email": f"'{ADDRESS.upper()}'"},
        {"email": f"'{ADDRESS[:63]}'"},
    ],
)
def test_setting_erased_at_needs_a_tombstone(world: World, changes: dict[str, str | None]) -> None:
    planted = plant(world)
    values = {**TOMBSTONE_SET, **changes}
    assignments = ", ".join(f"{k} = {v}" for k, v in values.items() if v is not None)
    statement = f"UPDATE contacts SET {assignments}, erased_at = ? WHERE id = ?"

    refused(world, TOMBSTONE, statement, (STAMP, planted.contact_id))

    assert world.contact_row(planted.contact_id)["erased_at"] is None


def test_a_tombstone_by_raw_sql_is_accepted(world: World) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name="Ann Lee", email="ann@firm.co.uk")

    world.sql(
        "UPDATE contacts SET name = '[erased]', role = NULL, email = ?, linkedin_url = NULL, "
        "office_country = NULL, office_region = NULL, erased_at = ? WHERE id = ?",
        (key_hash("address", "ann@firm.co.uk"), STAMP, contact_id),
    )

    assert world.contact_row(str(contact_id))["erased_at"] == STAMP


def test_an_erased_contacts_provenance_is_fixed(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    [(record_id,), *_] = world.sql(
        "SELECT id FROM provenance_records WHERE contact_id = ?", (planted.contact_id,)
    )
    [(bob,)] = world.sql("SELECT id FROM contacts WHERE name = 'Bob Other'")
    [(bob_record,), *_] = world.sql(
        "SELECT id FROM provenance_records WHERE contact_id = ?", (bob,)
    )

    refused(
        world,
        ERASED_PROVENANCE,
        "UPDATE provenance_records SET notes = 'Jane again' WHERE id = ?",
        (record_id,),
    )
    refused(
        world,
        ERASED_PROVENANCE,
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        "address, retrieved_at, address_class) VALUES (?, ?, 'contact', 'csv', 'x', NULL, ?, "
        "NULL)",
        (OTHER_ID, planted.contact_id, STAMP),
    )
    refused(
        world,
        ERASED_PROVENANCE,
        "UPDATE provenance_records SET contact_id = ? WHERE id = ?",
        (planted.contact_id, bob_record),
    )
    world.sql("UPDATE provenance_records SET notes = 'fine' WHERE id = ?", (bob_record,))


def test_only_the_redaction_of_a_persons_evidence_is_allowed(world: World) -> None:
    planted = plant(world)
    person, firm = planted.person_quote, planted.firm_quote

    def evidence_refused(statement: str, params: tuple[object, ...]) -> None:
        refused(world, EVIDENCE_IMMUTABLE, statement, params)

    evidence_refused("UPDATE evidence SET quote = 'Other text.' WHERE id = ?", (person,))
    evidence_refused("UPDATE evidence SET quote = '[erased]' WHERE id = ?", (firm,))
    evidence_refused("UPDATE evidence SET quote = NULL WHERE id = ?", (person,))
    evidence_refused(
        "UPDATE evidence SET quote = '[erased]', person_contact_id = NULL WHERE id = ?", (person,)
    )
    evidence_refused(
        "UPDATE evidence SET person_contact_id = ? WHERE id = ?", (planted.contact_id, firm)
    )
    evidence_refused(
        "UPDATE evidence SET value = '[erased]' WHERE id = ?", (planted.person_quote,)
    )  # NULL value: nothing to redact
    evidence_refused(
        "UPDATE evidence SET quote = '[erased]' WHERE id = ?", (planted.person_absence,)
    )
    for column, value in (
        ("type", "absence"),
        ("sought", "x"),
        ("number", 3),
        ("fact", "staff"),
        ("pages_checked", '["https://other.com/"]'),
        ("source", "operator"),
        ("source_url", "https://other.com/"),
        ("retrieved_at", "2026-01-01T00:00:00.000000Z"),
        ("extracted_by", "operator"),
        ("created_at", "2026-01-01T00:00:00.000000Z"),
        ("id", OTHER_ID),
    ):
        evidence_refused(f"UPDATE evidence SET {column} = ? WHERE id = ?", (value, person))

    world.sql("UPDATE evidence SET quote = '[erased]' WHERE id = ?", (person,))
    world.sql(
        "UPDATE evidence SET quote = '[erased]', value = '[erased]' WHERE id = ?",
        (planted.person_fact,),
    )
    evidence_refused("UPDATE evidence SET quote = ? WHERE id = ?", (QUOTE, person))  # back
    evidence_refused("UPDATE evidence SET quote = NULL WHERE id = ?", (person,))
    evidence_refused("DELETE FROM evidence WHERE id = ?", (person,))


def test_a_request_is_permanent_and_moves_only_to_completed(world: World) -> None:
    planted = plant(world)
    world.erase(planted.contact_id)
    [(request_id, *_)] = world.requests()
    open_id = world.transact(
        lambda: world.erasures.open_dsr(planted.contact_id, "export", REF)
    )

    for statement, params in (
        ("DELETE FROM data_subject_requests WHERE id = ?", (request_id,)),
        ("DELETE FROM data_subject_requests", ()),
        ("UPDATE data_subject_requests SET status = 'open', completed_at = NULL WHERE id = ?",
         (request_id,)),
        ("UPDATE data_subject_requests SET request_ref = 'other' WHERE id = ?", (request_id,)),
        ("UPDATE data_subject_requests SET kind = 'export' WHERE id = ?", (request_id,)),
        ("UPDATE data_subject_requests SET contact_id = ? WHERE id = ?", (OTHER_ID, request_id)),
        ("UPDATE data_subject_requests SET opened_at = ? WHERE id = ?",
         ("2026-01-01T00:00:00.000000Z", request_id)),
        ("UPDATE data_subject_requests SET id = ? WHERE id = ?", (OTHER_ID, request_id)),
        ("UPDATE data_subject_requests SET completed_at = ? WHERE id = ?",
         ("2027-01-01T00:00:00.000000Z", request_id)),
        (f"INSERT INTO data_subject_requests ({REQUEST_COLUMNS}) VALUES "
         "(?, ?, 'erase', 'completed', 'x', ?, ?, NULL)",
         (OTHER_ID, planted.contact_id, STAMP, STAMP)),
        (f"INSERT OR REPLACE INTO data_subject_requests ({REQUEST_COLUMNS}) VALUES "
         "(?, ?, 'erase', 'open', 'x', ?, NULL, NULL)",
         (request_id, planted.contact_id, STAMP)),
        (f"REPLACE INTO data_subject_requests ({REQUEST_COLUMNS}) VALUES "
         "(?, ?, 'erase', 'open', 'x', ?, NULL, NULL)",
         (open_id, planted.contact_id, STAMP)),
    ):
        refused(world, REQUESTS_PERMANENT, statement, params)

    # Story 3.14: the erasure set its request's receipt once; another request may name a
    # receipt of the same Contact once (a receipt that doesn't exist is refused, 3.14's tests)
    [(receipt_id,)] = world.sql(
        "SELECT receipt_id FROM data_subject_requests WHERE id = ?", (request_id,)
    )
    assert receipt_id is not None
    world.sql(
        "UPDATE data_subject_requests SET receipt_id = ? WHERE id = ?", (receipt_id, open_id)
    )
    second = world.erase(planted.contact_id, OTHER_REF)  # another receipt of the same Contact
    refused(
        world,
        REQUESTS_PERMANENT,
        "UPDATE data_subject_requests SET receipt_id = ? WHERE id = ?",
        (second.receipt_id, request_id),
    )
    refused(
        world,
        REQUESTS_PERMANENT,
        "UPDATE data_subject_requests SET receipt_id = NULL WHERE id = ?",
        (request_id,),
    )
    assert len(world.requests()) == 3


@pytest.mark.parametrize(
    ("values", "check"),
    [
        ("'erase', 'open', '', ?, NULL", "ck_data_subject_requests_request_ref"),
        ("'delete', 'open', 'x', ?, NULL", "ck_data_subject_requests_kind"),
    ],
)
def test_a_request_keeps_its_checks(world: World, values: str, check: str) -> None:
    planted = plant(world)

    with pytest.raises(sqlite3.IntegrityError, match=check):
        world.sql(
            f"INSERT INTO data_subject_requests ({REQUEST_COLUMNS}) VALUES "
            f"(?, ?, {values}, NULL)",
            (OTHER_ID, planted.contact_id, STAMP),
        )
