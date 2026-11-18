"""Erasure receipts and the stores outside the database (FR-52, AD-10, AD-15, AD-17, NFR-3,
NFR-17, Story 3.14) on a temporary database at head with ``FrozenClock``, the real subscriber
registry, the real job type registry and the fakes of the Telegram and sink ports: every row of
the spec's I/O matrix on real units of work. A data subject request's receipt (17 rows: the
in-database items worked out before the hooks, the twelve external stores ``pending``), its
``events`` row, the request's ``receipt_id`` and the twelve ``erase_external`` jobs (keys and
payloads); the jobs run on fakes (every store settled, "Complete"); Telegram (deleted, older,
refused, retried, failed); the sink (matched, prod, retried, failed); the residuals (Evidence
and page addresses naming the person, other Contacts sharing a key, no person key without a web
domain); retention; a repeat request; a failing handler and the health issues; an open request
on the health panel; the fail-closed checks; raw SQL that every new trigger refuses; and no
personal data in a receipt, ``events``, a payload or a log."""

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.compliance.erasure_stores import (
    ERASURE_STORE_FAILED,
    MAILBOX_PLACEHOLDER,
    ErasureStores,
)
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import Playbook
from nextron.app.queries import compliance as compliance_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.compliance import erasure_receipts as receipts_repo
from nextron.db.repos.ops import backups as backups_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.alert import Channel
from nextron.domain.contactability import key_hash
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.crawl import text_hash
from nextron.domain.erasure_receipt import (
    EXTERNAL_STORES,
    STORE_NUMBERS,
    ErasureStore,
    receipt_state,
)
from nextron.domain.health import HealthIssue
from nextron.domain.job import Job, JobTypes, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.segments import validate_segments
from nextron.domain.settings_schema import Key
from nextron.ports.errors import AdapterError
from nextron.ports.sink import SINK_ERASE_FAILED, SinkErasure
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.sink import InMemorySinkFiles

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
STAMP = "2026-11-18T10:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
WEB = "firm.co.uk"
REF = "DSR-2026-0042"
OTHER_REF = "DSR-2026-0043"
NAME = "Jane Doe"
EMAIL = "jane.doe@firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
ADDRESS = key_hash("address", EMAIL)
CHAT = "-1001234567890"
LOGGER = "nextron"
STORE_ROWS = "SELECT store, status, detail FROM erasure_receipt_stores WHERE receipt_id = ?"
JOB_ROWS = (
    "SELECT idempotency_key, payload, lane, priority FROM jobs WHERE type = 'erase_external' "
    "ORDER BY created_at, id"
)


def no_jobs() -> Any:
    raise AssertionError("no other job runs in these tests")


@dataclass
class World:
    """Discovery, Research, Compliance, Erasures and ErasureStores on real units of work."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    job_types: JobTypes
    queue: JobQueue
    discovery: Discovery
    research: Research
    erasures: Erasures
    stores: ErasureStores
    alert: InMemoryAlert
    sink: InMemorySinkFiles
    segment: str = ""

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

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        assert self.engine.url.database
        connection = sqlite3.connect(Path(self.engine.url.database), isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    def lead(self, web_domain: str | None = WEB, name: str = "Firm LLP") -> str:
        number = None if web_domain is not None else "OC123456"  # a Lead needs one key
        identity = LeadIdentity(
            name=name, segment_id=self.segment, web_domain=web_domain, ch_number=number
        )
        return self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)).lead_id

    def contact(self, lead_id: str, **fields: Any) -> str:
        candidate = ContactCandidate(**fields)
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id, candidate, source=ProvenanceSource.CSV, detail="export", retrieved_at=T0
            )
        )
        assert added.contact_id is not None
        return added.contact_id

    def jane(self, web_domain: str | None = WEB) -> tuple[str, str]:
        lead_id = self.lead(web_domain)
        return lead_id, self.contact(lead_id, name=NAME, email=EMAIL, linkedin_url=LINKEDIN)

    def erase(self, contact_id: str, ref: str = REF) -> Any:
        with self.units.open():
            return self.erasures.erase_on_request(contact_id, ref)

    def erase_in(self, contact_id: str, ref: str | None, cause: str) -> Any:
        return self.transact(lambda: self.erasures.erase_contact(contact_id, ref, cause))

    def run(self, contact_id: str, store: str, *, last_attempt: bool = False) -> Any:
        with self.units.open():
            return self.stores.run(contact_id, store, last_attempt=last_attempt)

    def run_jobs(self) -> list[str]:
        """Claim and run every queued ``erase_external`` job through the registry's handler,
        completing each; the keys run, in order."""
        ran: list[str] = []
        while True:
            with self.units.open():
                claim = self.queue.claim(Lane.BULK)
            if claim is None:
                return ran
            if claim.job.type != "erase_external":
                with self.units.open():
                    self.queue.release([claim])
                return ran
            with self.units.open():
                assert self.job_types.get(claim.job.type).handler(claim.job) is None
            with self.units.open():
                self.queue.complete(claim)
            [(key,)] = self.sql("SELECT idempotency_key FROM jobs WHERE id = ?", (claim.job.id,))
            ran.append(key)

    def receipts(self, contact_id: str) -> tuple[Any, ...]:
        return self.read(lambda: compliance_queries.receipts([contact_id])).get(contact_id, ())

    def rows(self, receipt_id: str) -> dict[str, tuple[str, str]]:
        return {
            store: (status, detail) for store, status, detail in self.sql(STORE_ROWS, (receipt_id,))
        }

    def issues(self) -> tuple[HealthIssue, ...]:
        return self.read(compliance_queries.health_issues)

    def page(self, lead_id: str, url: str, final_url: str | None = None) -> str:
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
            return crawl_repo.insert_page(
                lead_id=lead_id,
                run_id=run_id,
                url=url,
                final_url=final_url or url,
                kind="team",
                fetched_at=T0,
                http_status=200,
                content_hash=text_hash("page text"),
                text="page text",
                text_purge_after=T0 + timedelta(days=30),
                changed_at=T0,
                mailto=(),
            )

        return self.transact(store)

    def evidence(
        self,
        lead_id: str,
        quote: str,
        *,
        person: str | None = None,
        source_url: str | None = None,
        value: str | None = None,
    ) -> str:
        return self.transact(
            lambda: signals_repo.insert_evidence(
                lead_id=lead_id,
                evidence_type="quote",
                quote=quote,
                sought=None,
                number=None,
                fact=None if value is None else "what_we_do",
                value=value,
                pages_checked=["https://firm.co.uk/team"],
                source="website",
                source_url=source_url or "https://firm.co.uk/team",
                retrieved_at=T0,
                extracted_by="rule",
                person_contact_id=person,
                at=T0,
            )
        )

    def alert_row(
        self, subject: str, status: str, *, message_id: str | None, at: datetime, kind: str
    ) -> None:
        stamp = f"{at.astimezone(UTC):%Y-%m-%dT%H:%M:%S.%fZ}"
        self.sql(
            "INSERT INTO alerts (id, kind, subject, channel, text, status, provider_message_id, "
            "error_code, resent_at, created_at, updated_at) VALUES "
            "(?, ?, ?, 'telegram', 'Ids only', ?, ?, NULL, NULL, ?, ?)",
            (f"01JD3QZ8X5V9K2M7N4P6R8T{len(kind):03d}", kind, subject, status, message_id)
            + (stamp, stamp),
        )

    def backup(self, started_at: datetime, *, offpc: bool = False, local: bool = True) -> str:
        def store() -> str:
            backup_id = backups_repo.insert(
                kind="daily",
                status="verified",
                started_at=started_at,
                finished_at=started_at,
                path="C:/backups/local/copy.db",
                size_bytes=1,
                sha256="0" * 64,
                db_revision="0029_3_14_erasure_receipts",
                suppression_count=0,
                provenance_count=0,
                error=None,
            )
            if offpc:
                backups_repo.set_offpc(
                    backup_id,
                    status="copied",
                    name="copy.db.enc",
                    ledger="absent",
                    at=started_at,
                    error=None,
                )
            if not local:
                backups_repo.mark_deleted([backup_id], started_at)
            return backup_id

        return self.transact(store)


@pytest.fixture
def world(db_engine: Engine) -> Iterator[World]:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).set(Key.ALERT_CHAT_ID, CHAT)
    built: dict[str, ErasureStores] = {}
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
        erasure_stores=lambda: built["stores"],
    )
    queue = JobQueue(clock, job_types)
    alert = InMemoryAlert()
    sink = InMemorySinkFiles()
    notify = Notify(clock, queue, alert_ports={Channel.TELEGRAM: lambda: alert})
    built["stores"] = ErasureStores(clock, notify, sink)
    found = World(
        units,
        db_engine,
        clock,
        job_types,
        queue,
        Discovery(clock, Pipeline(clock), queue),
        Research(clock),
        Erasures(clock, Compliance(clock), queue=queue),
        built["stores"],
        alert,
        sink,
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
    yield found


def assert_no_personal_data(values: object) -> None:
    text = repr(values).lower()
    for plain in ("jane", "doe", "linkedin.com", "@", REF.lower()):  # "LinkedIn URL" is fixed
        assert plain not in text, plain


# DSR erasure ----------------------------------------------------------------------------------


def test_a_dsr_erasure_writes_one_receipt_with_seventeen_rows_and_twelve_jobs(
    world: World,
) -> None:
    lead_id, contact_id = world.jane()

    result = world.erase(contact_id)

    [(receipt_id, contact, ref, cause, outcome, created)] = world.sql(
        "SELECT id, contact_id, request_ref, cause, outcome, created_at FROM erasure_receipts"
    )
    assert (receipt_id, contact, ref, cause, outcome, created) == (
        result.receipt_id,
        contact_id,
        REF,
        "dsr",
        "erased",
        STAMP,
    )
    rows = world.rows(receipt_id)
    assert list(rows) == [str(store) for store in ErasureStore]
    assert rows["database"][0] == "done"
    assert rows["suppression"] == ("done", "3 keys (address, person, profile) stay suppressed")
    assert rows["evidence_quotes"] == ("none_held", "None contains the person's full name")
    assert rows["page_addresses"] == ("none_held", "None contains the person's full name")
    assert rows["other_contacts"] == (
        "none_held",
        "No other Contact shares this address or LinkedIn URL",
    )
    assert {rows[str(store)][0] for store in EXTERNAL_STORES} == {"pending"}
    # the actions are the stores' fixed texts
    actions = dict(
        world.sql("SELECT store, action FROM erasure_receipt_stores WHERE receipt_id = ?",
                  (receipt_id,))
    )
    assert actions == {str(store): store.action for store in ErasureStore}
    # the request names its receipt
    [(request_receipt,)] = world.sql("SELECT receipt_id FROM data_subject_requests")
    assert request_receipt == receipt_id == result.receipt_id
    # one events row for the receipt
    assert world.sql(
        "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'erasure_receipts'"
    ) == [("erasure_receipts", receipt_id, None, "written", "erased", contact_id, "operator")]
    # twelve jobs, one per external store, keyed by the store's fixed number
    jobs = world.sql(JOB_ROWS)
    assert [key for key, *_ in jobs] == [
        f"erase_external:{contact_id}:{STORE_NUMBERS[store]}" for store in EXTERNAL_STORES
    ]
    assert [json.loads(payload) for _, payload, *_ in jobs] == [
        {"v": 1, "contact_id": contact_id, "store": str(store)} for store in EXTERNAL_STORES
    ]
    assert {(lane, priority) for _, _, lane, priority in jobs} == {("bulk", 60)}
    assert lead_id  # the Lead itself is untouched (Story 3.13's tests)


def test_the_receipt_reads_in_progress_until_its_jobs_run(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)

    [receipt] = world.receipts(contact_id)

    assert [row.store for row in receipt.stores] == [str(store) for store in ErasureStore]
    assert receipt.request_ref == REF and REF not in repr(receipt)


# Jobs run -------------------------------------------------------------------------------------


def test_the_jobs_settle_every_store_and_the_receipt_reads_complete(world: World) -> None:
    _, contact_id = world.jane()  # added at LATER
    world.backup(LATER + timedelta(minutes=5))  # after the Contact was added: it holds her
    world.backup(T0 - timedelta(days=1))  # before: it can't
    world.clock.set(LATER + timedelta(minutes=10))
    result = world.erase(contact_id)

    ran = world.run_jobs()

    assert len(ran) == 12
    rows = world.rows(result.receipt_id)
    assert {store: status for store, (status, _) in rows.items()} == {
        "database": "done",
        "suppression": "done",
        "evidence_quotes": "none_held",
        "page_addresses": "none_held",
        "other_contacts": "none_held",
        "mailboxes": "none_held",
        "crawled_pages": "none_held",
        "telegram": "none_held",
        "anthropic": "residual",
        "worker_kv": "residual",
        "backups": "residual",
        "ai_test_sets": "none_held",  # no override case made from her Replies (Story 4.6)
        "nextronai_inbox": "residual",
        "logs": "none_held",
        "sink_files": "none_held",
        "restore_work": "none_held",
        "erasure_ledger": "none_held",
    }
    assert rows["backups"][1].startswith(
        "1 local and 0 off-PC copy taken before the erasure hold the Contact; they age out "
        "between 2026-12-23 and 2026-12-23, and every copy taken before the erasure ages out by "
        "2026-12-23, while daily backups keep running. Copies saved by `nextron restore`"
    )
    assert rows["worker_kv"][1] == "Worker KV: token and time only; expires within 30 days"
    assert rows["mailboxes"][1] == "The Contact has no Campaign Threads"
    assert world.sink.erased == [ADDRESS]  # the erased Contact's stored address hash
    assert world.alert.deleted == []
    [receipt] = world.receipts(contact_id)
    assert receipt_state(row.status for row in receipt.stores).label == "Complete"
    # one events row per settled row
    settled = world.sql(
        "SELECT entity_id, from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'erasure_receipt_stores'"
    )
    assert len(settled) == 12
    assert {row[1:4] for row in settled} <= {
        ("pending", status, "settled") for status in ("done", "residual", "none_held")
    }
    assert {(row[4], row[5]) for row in settled} == {(result.receipt_id, "system:compliance")}
    # a run with nothing pending is a no-op
    assert world.run(contact_id, "logs") is None


def test_the_ai_test_sets_store_counts_the_override_cases_playbooks_hook_erased(
    world: World,
) -> None:
    # the merge of Stories 3.14 and 4.6: playbook's hook erases the Contact's Reply override
    # cases in the erasure's transaction, so the receipt says done, not a fixed residual
    _, contact_id = world.jane()
    other_lead = world.lead("other-firm.co.uk", "Other LLP")
    other_id = world.contact(other_lead, name="Ann Lee", email="ann.lee@other-firm.co.uk")
    playbook = Playbook(world.clock)
    for reply in ("Hi, Jane Doe here: what would it cost?", "Jane Doe again: any update?"):
        world.transact(
            lambda reply=reply: playbook.add_override_case(
                reply, "question", contact_id=contact_id, names=(NAME,)
            )
        )
    world.erase(contact_id)
    world.erase(other_id)

    world.run(contact_id, "ai_test_sets")
    world.run(other_id, "ai_test_sets")

    [receipt] = world.receipts(contact_id)
    [row] = [row for row in receipt.stores if row.store == "ai_test_sets"]
    assert (row.status, row.detail) == (
        "done",
        "AI Test Sets: 2 override cases made from the Contact's Replies erased and retired; "
        "the other cases are synthetic or pseudonymised",
    )
    assert world.sql(
        "SELECT input, retired_at IS NOT NULL FROM ai_test_cases WHERE contact_id = ?",
        (contact_id,),
    ) == [("[erased]", 1), ("[erased]", 1)]
    [other] = world.receipts(other_id)
    [row] = [row for row in other.stores if row.store == "ai_test_sets"]
    assert (row.status, row.detail) == (
        "none_held",
        "AI Test Sets: no case was made from the Contact's Replies; the other cases are "
        "synthetic or pseudonymised",
    )


def test_the_crawled_pages_store_counts_the_pages_of_the_lead_and_its_merged_leads(
    world: World,
) -> None:
    lead_id, contact_id = world.jane()
    old = world.lead("firm-old.co.uk", "Firm Old LLP")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead_id, old))
    world.page(lead_id, "https://firm.co.uk/team")
    world.page(old, "https://firm-old.co.uk/team")
    world.erase(contact_id)

    world.run(contact_id, "crawled_pages")

    [receipt] = world.receipts(contact_id)
    [row] = [row for row in receipt.stores if row.store == "crawled_pages"]
    assert (row.status, row.detail) == (
        "done",
        "Text of 2 pages nulled, content hash kept; a later crawl stores public page text "
        "again for at most 30 days",
    )


def test_a_page_crawled_since_the_erasure_leaves_the_crawled_pages_residual(
    world: World,
) -> None:
    lead_id, contact_id = world.jane()
    world.page(lead_id, "https://firm.co.uk/team")
    again = world.page(lead_id, "https://firm.co.uk/about")
    world.erase(contact_id)
    world.sql("UPDATE crawled_pages SET text = 'crawled again' WHERE id = ?", (again,))

    assert world.run(contact_id, "crawled_pages") == "residual"

    [receipt] = world.receipts(contact_id)
    [row] = [row for row in receipt.stores if row.store == "crawled_pages"]
    assert row.detail == (
        "1 page nulled, content hash kept; 1 page crawled since holds public page text again, "
        "kept at most 30 days"
    )


def test_the_backups_store_counts_local_and_offpc_copies_and_their_age_out(world: World) -> None:
    _, contact_id = world.jane()  # added at LATER
    world.backup(LATER + timedelta(minutes=1), offpc=True)  # both copies
    world.backup(LATER + timedelta(minutes=2), offpc=True, local=False)  # the off-PC one only
    world.backup(LATER + timedelta(minutes=3), local=False)  # no copy left
    with world.units.open():
        Setup(world.clock).set(Key.BACKUP_ROTATION_DAYS, 7)
    world.clock.set(LATER + timedelta(minutes=10))
    world.erase(contact_id)
    world.clock.set(LATER + timedelta(days=1))
    world.backup(LATER + timedelta(hours=2))  # after the erasure: it can't hold her

    world.run(contact_id, "backups")

    detail = dict((row.store, row.detail) for row in world.receipts(contact_id)[0].stores)
    assert detail["backups"].startswith(
        "1 local and 2 off-PC copies taken before the erasure hold the Contact; they age out "
        "between 2026-11-25 and 2026-12-23, and every copy taken before the erasure ages out by "
        "2026-12-23,"
    )


def test_no_recorded_backup_is_still_residual_with_its_bound(world: World) -> None:
    world.backup(T0 - timedelta(days=1))  # before the Contact: it can't hold her
    _, contact_id = world.jane()
    world.erase(contact_id)  # at LATER: 2026-11-18

    assert world.run(contact_id, "backups") == "residual"  # a running backup isn't recorded yet
    detail = dict((row.store, row.detail) for row in world.receipts(contact_id)[0].stores)
    assert detail["backups"] == (
        "No recorded backup copy holds the Contact; every copy taken before the erasure ages out "
        "by 2026-12-23, while daily backups keep running. Copies saved by `nextron restore` or "
        "left by a failed migration are kept until deleted by hand. Erasure re-applied after any "
        "restore."
    )


# Telegram -------------------------------------------------------------------------------------


def telegram_row(world: World, contact_id: str) -> tuple[str, str]:
    [receipt] = world.receipts(contact_id)
    [row] = [row for row in receipt.stores if row.store == "telegram"]
    return row.status, row.detail


def test_telegram_deletes_the_recent_alert_and_lists_the_rest(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    now = world.clock.now()
    world.alert_row(contact_id, "sent", message_id="101", at=now - timedelta(hours=1), kind="a")
    world.alert_row(contact_id, "sent", message_id="102", at=now - timedelta(days=3), kind="bb")
    world.alert_row(contact_id, "failed", message_id=None, at=now, kind="ccc")

    assert world.run(contact_id, "telegram") == "residual"

    assert world.alert.deleted == [(CHAT, "101")]
    assert telegram_row(world, contact_id) == (
        "residual",
        "1 deleted; 1 older than 48 hours remains (Telegram message older than 48 hours)",
    )


def test_telegram_is_done_when_every_alert_is_deleted(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    now = world.clock.now()
    world.alert_row(contact_id, "sent", message_id="101", at=now - timedelta(hours=1), kind="a")
    world.alert_row(contact_id, "sent", message_id="102", at=now - timedelta(hours=47), kind="b2")

    assert world.run(contact_id, "telegram") == "done"
    assert telegram_row(world, contact_id) == ("done", "2 deleted")


def test_a_refused_delete_and_an_unconfirmed_alert_are_residual(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    now = world.clock.now()
    world.alert_row(contact_id, "sent", message_id="101", at=now - timedelta(hours=1), kind="a")
    world.alert_row(contact_id, "unknown", message_id=None, at=now, kind="bb")
    world.alert.delete_outcomes.append(False)

    assert world.run(contact_id, "telegram") == "residual"
    assert telegram_row(world, contact_id) == (
        "residual",
        "0 deleted; 1 that Telegram would not delete remains; 1 that may have been delivered "
        "without an id remains",
    )


def test_a_retryable_telegram_error_retries_and_the_last_attempt_fails(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _, contact_id = world.jane()
    world.erase(contact_id)
    world.alert_row(contact_id, "sent", message_id="101", at=world.clock.now(), kind="a")
    unavailable = AdapterError(True, "telegram_unavailable", "Telegram answered with HTTP 503")
    world.alert.delete_outcomes.extend([unavailable, unavailable])

    with pytest.raises(AdapterError):
        world.run(contact_id, "telegram")
    assert telegram_row(world, contact_id)[0] == "pending"  # the job retries

    assert world.run(contact_id, "telegram", last_attempt=True) == "failed"
    assert telegram_row(world, contact_id) == ("failed", "Failed: telegram_unavailable")
    failed = [record for record in caplog.records if record.getMessage() == ERASURE_STORE_FAILED]
    assert [(r.contact_id, r.store, r.code) for r in failed] == [  # type: ignore[attr-defined]
        (contact_id, "telegram", "telegram_unavailable")
    ]


def test_an_unusable_chat_id_fails_the_telegram_store(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    world.alert_row(contact_id, "sent", message_id="101", at=world.clock.now(), kind="a")
    world.sql("DELETE FROM settings WHERE key = 'alert_chat_id'")

    with pytest.raises(AdapterError) as retried:  # retried first: it may be set meanwhile
        world.run(contact_id, "telegram")
    assert (retried.value.retryable, retried.value.code) == (True, "erasure_store_retry")
    assert telegram_row(world, contact_id)[0] == "pending"

    assert world.run(contact_id, "telegram", last_attempt=True) == "failed"
    assert telegram_row(world, contact_id) == ("failed", "Failed: setting_not_set")
    assert world.alert.deleted == []


def test_a_non_retryable_telegram_error_fails_the_store_at_once(world: World) -> None:
    _, contact_id = world.jane()
    result = world.erase(contact_id)
    world.alert_row(contact_id, "sent", message_id="101", at=world.clock.now(), kind="a")
    world.alert.delete_outcomes.append(
        AdapterError(False, "telegram_refused", "Telegram refused the deletion with HTTP 401")
    )

    assert world.run(contact_id, "telegram", last_attempt=False) == "failed"

    assert telegram_row(world, contact_id) == ("failed", "Failed: telegram_refused")
    assert world.issues() == (
        HealthIssue(
            "erasure_failed",
            result.receipt_id,
            f"Erasure failed: Telegram, Contact {contact_id}, receipt {result.receipt_id}",
            detail="Failed: telegram_refused",
        ),
    )


# Sink ------------------------------------------------------------------------------------------


def sink_row(world: World, contact_id: str) -> tuple[str, str]:
    [receipt] = world.receipts(contact_id)
    [row] = [row for row in receipt.stores if row.store == "sink_files"]
    return row.status, row.detail


def test_the_sink_store_erases_the_recipients_lines(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    world.sink.result = SinkErasure(files=1, lines=2)

    assert world.run(contact_id, "sink_files") == "done"
    assert world.sink.erased == [ADDRESS]
    assert sink_row(world, contact_id) == ("done", "2 messages in 1 file")


def test_a_locked_sink_file_retries_then_fails(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    locked = AdapterError(True, SINK_ERASE_FAILED, "the sink could not erase")
    world.sink.outcomes.extend([locked, locked])

    with pytest.raises(AdapterError):
        world.run(contact_id, "sink_files")
    assert world.run(contact_id, "sink_files", last_attempt=True) == "failed"
    assert sink_row(world, contact_id) == ("failed", "Failed: sink_erase_failed")


def test_prod_has_no_sink_files(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    prod = ErasureStores(world.clock, None, None)  # type: ignore[arg-type]

    with world.units.open():
        assert prod.run(contact_id, "sink_files", last_attempt=False) == "none_held"
    assert sink_row(world, contact_id) == (
        "none_held",
        "This environment (prod) records no sink files",
    )
    assert world.sink.erased == []


# Residuals ------------------------------------------------------------------------------------


def test_the_residuals_name_what_the_erasure_kept(world: World) -> None:
    lead_id, contact_id = world.jane(web_domain=None)
    world.evidence(lead_id, "Jane Doe leads our litigation team.")  # not linked to her
    world.evidence(lead_id, "We are a firm of 40 lawyers.")
    world.evidence(lead_id, "Doe and Jane, partners.", source_url="https://firm.co.uk/jane-doe")
    world.page(lead_id, "https://firm.co.uk/people/jane-doe", "https://firm.co.uk/people/janedoe")
    world.page(lead_id, "https://firm.co.uk/about")
    other_lead = world.lead("other.co.uk", "Other LLP")
    same_address = world.contact(other_lead, name="J. Doe", email=EMAIL)
    same_profile = world.contact(other_lead, name="Jane D", linkedin_url=LINKEDIN)
    world.contact(other_lead, name="Bob Other", email="bob@other.co.uk")

    result = world.erase(contact_id)

    rows = world.rows(result.receipt_id)
    assert rows["evidence_quotes"] == (
        "residual",
        "2 Evidence rows name the person and were kept (the firm's public pages)",
    )
    assert rows["page_addresses"] == (
        "residual",
        "3 page addresses name the person and were kept (the firm's public pages)",
    )
    ids = ", ".join(sorted([same_address, same_profile]))
    assert rows["other_contacts"] == (
        "residual",
        f"2 other Contacts share this address or LinkedIn URL and were not erased: {ids}",
    )
    assert rows["suppression"] == (
        "residual",
        "2 keys (address, profile) stay suppressed; no person key, because the Lead has no web "
        "domain: a later import of this person by name alone on this Lead isn't blocked",
    )


def test_the_quotes_and_addresses_count_every_lead_and_every_evidence_row(world: World) -> None:
    lead_id, contact_id = world.jane()
    world.evidence(lead_id, "Jane Doe joined us.", person=contact_id)  # hers: tombstoned
    retracted = world.evidence(lead_id, "Jane Doe leads litigation.")
    world.transact(lambda: signals_repo.retract_evidence(retracted, T0))
    world.evidence(lead_id, "Our litigation lead.", value="Jane Doe's litigation practice")
    old = world.lead("firm-old.co.uk", "Firm Old LLP")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead_id, old))
    world.evidence(old, "Doe, Jane: partner.")
    world.page(old, "https://firm-old.co.uk/people/jane-doe")

    result = world.erase(contact_id)

    rows = world.rows(result.receipt_id)
    assert rows["evidence_quotes"] == (
        "residual",
        "3 Evidence rows name the person and were kept (the firm's public pages)",
    )
    assert rows["page_addresses"] == (
        "residual",
        "1 page address names the person and was kept (the firm's public pages)",
    )


def test_a_name_that_forms_no_person_key_is_a_residual_even_with_a_web_domain(
    world: World,
) -> None:
    _, contact_id = world.jane()
    world.sql("UPDATE contacts SET name = '--' WHERE id = ?", (contact_id,))  # no letters to key

    result = world.erase(contact_id)

    assert world.rows(result.receipt_id)["suppression"] == (
        "residual",
        "2 keys (address, profile) stay suppressed; no person key, because the Contact's name "
        "and the Lead's web domain don't form one: a later import of this person by name alone "
        "on this Lead isn't blocked",
    )


def test_an_erased_other_contact_isnt_listed(world: World) -> None:
    _, contact_id = world.jane()
    other_lead = world.lead("other.co.uk", "Other LLP")
    other = world.contact(other_lead, name="J. Doe", email=EMAIL)
    world.erase(other, OTHER_REF)

    result = world.erase(contact_id)

    assert world.rows(result.receipt_id)["other_contacts"][0] == "none_held"


def test_a_contact_with_no_key_is_residual_on_the_suppression_list(world: World) -> None:
    lead_id = world.lead(None)  # no web domain, so a name alone is no key
    contact_id = world.contact(lead_id, name=NAME)

    result = world.erase(contact_id)

    rows = world.rows(result.receipt_id)
    assert rows["suppression"][0] == "residual"
    assert rows["suppression"][1].startswith("No address, person or profile key is known")


def test_a_contact_without_a_name_has_no_name_to_look_for(world: World) -> None:
    lead_id = world.lead()
    world.evidence(lead_id, "Jane Doe leads our team.")
    contact_id = world.contact(lead_id, email=EMAIL)

    result = world.erase(contact_id)

    rows = world.rows(result.receipt_id)
    assert rows["evidence_quotes"] == ("none_held", "The Contact had no name to look for")
    assert rows["page_addresses"] == ("none_held", "The Contact had no name to look for")
    assert rows["suppression"] == ("done", "1 key (address) stays suppressed")


# Retention ------------------------------------------------------------------------------------


def test_a_retention_receipt_has_no_reference_and_writes_no_suppression(world: World) -> None:
    _, contact_id = world.jane()

    result = world.erase_in(contact_id, None, "retention")

    [(ref, cause, outcome)] = world.sql(
        "SELECT request_ref, cause, outcome FROM erasure_receipts"
    )
    assert (ref, cause, outcome, result.request_id) == (None, "retention", "erased", None)
    rows = world.rows(result.receipt_id)
    assert rows["suppression"] == ("none_held", "Retention writes no suppression entry")
    assert len(world.sql(JOB_ROWS)) == 12
    assert world.sql(
        "SELECT actor FROM events WHERE entity = 'erasure_receipts'"
    ) == [("system:compliance",)]


def test_a_retention_call_on_an_erased_contact_writes_nothing(world: World) -> None:
    _, contact_id = world.jane()
    world.erase_in(contact_id, None, "retention")
    receipts = world.sql("SELECT COUNT(*) FROM erasure_receipts")

    result = world.erase_in(contact_id, None, "retention")

    assert (result.erased, result.receipt_id) == (False, None)
    assert world.sql("SELECT COUNT(*) FROM erasure_receipts") == receipts


# Repeat ---------------------------------------------------------------------------------------


def test_a_repeat_request_gets_its_own_receipt_and_its_jobs_settle_both(world: World) -> None:
    _, contact_id = world.jane()
    first = world.erase(contact_id)
    world.clock.set(LATER + timedelta(days=1))

    second = world.erase(contact_id, OTHER_REF)

    assert (second.erased, second.receipt_id != first.receipt_id) == (False, True)
    [(outcome, ref)] = world.sql(
        "SELECT outcome, request_ref FROM erasure_receipts WHERE id = ?", (second.receipt_id,)
    )
    assert (outcome, ref) == ("already_erased", OTHER_REF)
    rows = world.rows(second.receipt_id)
    assert rows["database"] == (
        "none_held",
        "Already erased on 2026-11-18; see that erasure's receipt",
    )
    as_at = "As at the erasure on 2026-11-18: "
    assert rows["evidence_quotes"] == ("none_held", as_at + "None contains the person's full name")
    assert rows["page_addresses"] == ("none_held", as_at + "None contains the person's full name")
    assert rows["other_contacts"] == (
        "none_held",
        as_at + "No other Contact shares this address or LinkedIn URL",
    )
    assert rows["suppression"] == ("done", "1 key (address) stays suppressed")
    # the first receipt's jobs are still queued: they absorb the repeat's, then settle both
    assert len(world.sql(JOB_ROWS)) == 12
    world.run_jobs()
    for receipt_id in (first.receipt_id, second.receipt_id):
        assert "pending" not in {status for status, _ in world.rows(receipt_id).values()}


def test_a_repeat_request_keeps_the_residuals_the_erasure_found(world: World) -> None:
    lead_id, contact_id = world.jane()
    world.evidence(lead_id, "Jane Doe leads our litigation team.")
    world.erase(contact_id)
    world.clock.set(LATER + timedelta(days=1))

    second = world.erase(contact_id, OTHER_REF)

    assert world.rows(second.receipt_id)["evidence_quotes"] == (
        "residual",
        "As at the erasure on 2026-11-18: 1 Evidence row names the person and was kept (the "
        "firm's public pages)",
    )


def test_a_repeat_on_a_contact_erased_before_receipts_says_so(world: World) -> None:
    _, contact_id = world.jane()
    world.sql(  # a Story 3.13 tombstone, written before receipts were kept
        "UPDATE contacts SET name = '[erased]', role = NULL, email = ?, linkedin_url = NULL, "
        "office_country = NULL, office_region = NULL, erased_at = ? WHERE id = ?",
        (ADDRESS, STAMP, contact_id),
    )

    result = world.erase(contact_id)

    rows = world.rows(result.receipt_id)
    unrecorded = (
        "residual",
        "Erased on 2026-11-18, before receipts were kept: what remains here wasn't recorded",
    )
    for store in ("evidence_quotes", "page_addresses", "other_contacts"):
        assert rows[store] == unrecorded
    assert rows["database"][0] == "none_held"


def test_a_repeat_after_the_jobs_ran_runs_them_again(world: World) -> None:
    _, contact_id = world.jane()
    world.erase(contact_id)
    world.run_jobs()
    world.clock.set(LATER + timedelta(days=1))

    second = world.erase(contact_id, OTHER_REF)

    assert len(world.sql(JOB_ROWS)) == 24
    assert len(world.run_jobs()) == 12
    assert "pending" not in {status for status, _ in world.rows(second.receipt_id).values()}


# Failure and the health panel -------------------------------------------------------------------


class Exploding:
    def prepare(self, contact: object, now: datetime) -> object:
        return None

    def act(self, plan: object) -> Any:
        raise RuntimeError("the store broke")


def test_a_failing_handler_is_failed_and_shows_on_the_health_panel(
    world: World, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    _, contact_id = world.jane()
    result = world.erase(contact_id)
    world.run_jobs()  # the first receipt: every store settled
    second = world.erase(contact_id, OTHER_REF)
    exploding = MappingProxyType({**world.stores.handlers, ErasureStore.ANTHROPIC: Exploding()})
    monkeypatch.setattr(world.stores, "_handlers", exploding)

    assert world.run(contact_id, "anthropic", last_attempt=True) == "failed"

    rows = world.rows(second.receipt_id)
    assert rows["anthropic"] == ("failed", "Failed: RuntimeError")
    [receipt] = [r for r in world.receipts(contact_id) if r.id == second.receipt_id]
    assert receipt_state(row.status for row in receipt.stores).label == "Incomplete: 1 failed"
    assert world.issues() == (
        HealthIssue(
            "erasure_failed",
            second.receipt_id,
            f"Erasure failed: Anthropic, Contact {contact_id}, receipt {second.receipt_id}",
            detail="Failed: RuntimeError",
        ),
    )
    assert any(record.getMessage() == ERASURE_STORE_FAILED for record in caplog.records)
    assert result.receipt_id != second.receipt_id


def test_an_error_before_the_last_attempt_is_retried_with_the_rows_pending(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, contact_id = world.jane()
    result = world.erase(contact_id)
    exploding = MappingProxyType({**world.stores.handlers, ErasureStore.ANTHROPIC: Exploding()})
    monkeypatch.setattr(world.stores, "_handlers", exploding)

    with pytest.raises(AdapterError) as retried:
        world.run(contact_id, "anthropic")

    assert (retried.value.retryable, retried.value.code) == (True, "erasure_store_retry")
    assert isinstance(retried.value.__cause__, RuntimeError)
    assert "broke" not in str(retried.value)
    assert world.rows(result.receipt_id)["anthropic"][0] == "pending"
    assert world.issues() == ()


def test_a_settle_that_fails_before_the_last_attempt_is_retried(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, contact_id = world.jane()
    result = world.erase(contact_id)

    def locked(*args: object) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(receipts_repo, "settle", locked)

    with pytest.raises(AdapterError) as retried:
        world.run(contact_id, "logs")
    assert (retried.value.retryable, retried.value.code) == (True, "erasure_store_retry")
    with pytest.raises(RuntimeError, match="locked"):  # the last attempt: the job dies
        world.run(contact_id, "logs", last_attempt=True)
    assert world.rows(result.receipt_id)["logs"][0] == "pending"


def test_an_erasure_rolled_back_leaves_an_open_request_on_the_health_panel(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, contact_id = world.jane()
    monkeypatch.setattr(receipts_repo, "insert_receipt", _explode)

    with pytest.raises(RuntimeError):
        world.erase(contact_id)

    [(request_id, status)] = world.sql("SELECT id, status FROM data_subject_requests")
    assert status == "open"
    assert world.sql("SELECT COUNT(*) FROM erasure_receipts") == [(0,)]
    assert world.sql(JOB_ROWS) == []
    assert world.issues() == (
        HealthIssue(
            "erasure_open",
            request_id,
            f"Erasure request still open: Contact {contact_id}; erase it again from the Lead "
            "page",
        ),
    )
    monkeypatch.undo()
    world.erase(contact_id)
    assert world.issues() == ()


def _explode(**fields: object) -> str:
    raise RuntimeError("the receipt could not be written")


# Fail closed and arguments ----------------------------------------------------------------------


def test_erase_contact_without_a_queue_fails_before_reading(world: World) -> None:
    _, contact_id = world.jane()
    unwired = Erasures(world.clock, Compliance(world.clock))
    counts = world.sql("SELECT COUNT(*) FROM events")

    with pytest.raises(RuntimeError, match="needs the job queue"):
        world.transact(lambda: unwired.erase_contact(contact_id, REF, "dsr"))
    assert world.sql("SELECT COUNT(*) FROM events") == counts


@pytest.mark.parametrize(
    ("contact_id", "store", "error"),
    [
        ("not-a-ulid", "logs", ValueError),
        ("01JD0000000000000000000000", "database", ValueError),
        ("01JD0000000000000000000000", "ghost", ValueError),
        ("01JD0000000000000000000000", 3, TypeError),
    ],
)
def test_run_checks_its_arguments(
    world: World, contact_id: str, store: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        world.run(contact_id, store)  # type: ignore[arg-type]


def test_the_handlers_cover_exactly_the_external_stores(world: World) -> None:
    assert tuple(world.stores.handlers) == EXTERNAL_STORES
    assert world.stores.handlers[ErasureStore.MAILBOXES] is MAILBOX_PLACEHOLDER
    with pytest.raises(TypeError):
        world.stores.handlers[ErasureStore.LOGS] = MAILBOX_PLACEHOLDER  # type: ignore[index]


# No personal data -------------------------------------------------------------------------------


def test_no_receipt_event_payload_or_log_holds_personal_data(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    lead_id, contact_id = world.jane(web_domain=None)
    world.evidence(lead_id, "Jane Doe leads our team.")
    world.page(lead_id, "https://firm.co.uk/people/jane-doe")
    world.alert_row(contact_id, "sent", message_id="101", at=world.clock.now(), kind="a")
    world.clock.set(LATER + timedelta(minutes=1))
    world.erase(contact_id)
    world.run_jobs()

    assert_no_personal_data(world.sql("SELECT * FROM erasure_receipt_stores"))
    assert_no_personal_data(
        world.sql("SELECT id, contact_id, cause, outcome, created_at FROM erasure_receipts")
    )
    assert_no_personal_data(world.sql("SELECT * FROM events"))
    assert_no_personal_data(world.sql("SELECT payload, idempotency_key FROM jobs"))
    assert_no_personal_data(
        [(record.getMessage(), record.__dict__) for record in caplog.records]
    )


# Raw SQL ----------------------------------------------------------------------------------------


@pytest.fixture
def settled(world: World) -> tuple[World, str, str]:
    """A receipt with its rows: one settled (``logs``), the others as written."""
    _, contact_id = world.jane()
    result = world.erase(contact_id)
    world.run(contact_id, "logs")
    return world, contact_id, result.receipt_id


def refused(
    world: World, statement: str, params: tuple[object, ...] = (), match: str | None = None
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=match):
        world.sql(statement, params)


def test_a_receipt_is_permanent(settled: tuple[World, str, str]) -> None:
    world, _, receipt_id = settled

    refused(world, "DELETE FROM erasure_receipts")
    refused(world, "UPDATE erasure_receipts SET outcome = 'already_erased'")
    refused(world, "UPDATE erasure_receipts SET created_at = created_at")
    refused(world, "DELETE FROM erasure_receipt_stores")
    assert world.sql("SELECT COUNT(*) FROM erasure_receipt_stores") == [(17,)]
    assert receipt_id


def test_a_store_row_settles_once_from_pending(settled: tuple[World, str, str]) -> None:
    world, _, receipt_id = settled
    where = "WHERE receipt_id = ? AND store = ?"

    refused(world, f"UPDATE erasure_receipt_stores SET status = 'done' {where}",
            (receipt_id, "logs"))  # settled already
    refused(world, f"UPDATE erasure_receipt_stores SET status = 'pending' {where}",
            (receipt_id, "logs"))
    refused(world, f"UPDATE erasure_receipt_stores SET detail = 'x' {where}",
            (receipt_id, "telegram"))  # still pending
    refused(world, f"UPDATE erasure_receipt_stores SET status = 'done', store = 'backups' {where}",
            (receipt_id, "telegram"))
    refused(world, f"UPDATE erasure_receipt_stores SET status = 'done', action = 'x' {where}",
            (receipt_id, "telegram"))
    refused(world, f"UPDATE erasure_receipt_stores SET status = 'done', detail = 'mail a@b.co' "
            f"{where}", (receipt_id, "telegram"))
    refused(world, f"UPDATE erasure_receipt_stores SET status = 'lost' {where}",
            (receipt_id, "telegram"))
    world.sql(f"UPDATE erasure_receipt_stores SET status = 'done', detail = 'ok' {where}",
              (receipt_id, "telegram"))  # the one change allowed


def test_a_store_row_is_inserted_once(settled: tuple[World, str, str]) -> None:
    world, _, receipt_id = settled
    [(row_id,)] = world.sql(
        "SELECT id FROM erasure_receipt_stores WHERE receipt_id = ? AND store = 'logs'",
        (receipt_id,),
    )
    insert = (
        "INSERT OR REPLACE INTO erasure_receipt_stores (id, receipt_id, store, action, status, "
        "detail, at) VALUES (?, ?, ?, 'x', 'pending', 'y', ?)"
    )

    refused(world, insert, (row_id, receipt_id, "logs", STAMP))
    refused(world, insert, ("01JD3QZ8X5V9K2M7N4P6R8T0Z9", receipt_id, "logs", STAMP))
    refused(world, insert, ("01JD3QZ8X5V9K2M7N4P6R8T0Z8", receipt_id, "nowhere", STAMP))


def test_a_receipt_keeps_its_checks(world: World) -> None:
    _, contact_id = world.jane()
    insert = (
        "INSERT INTO erasure_receipts (id, contact_id, request_ref, cause, outcome, created_at) "
        "VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0Z1', ?, ?, ?, ?, ?)"
    )

    refused(world, insert, (contact_id, None, "dsr", "erased", STAMP))  # dsr needs a ref
    refused(world, insert, (contact_id, REF, "retention", "erased", STAMP))  # retention: none
    refused(world, insert, (contact_id, "", "dsr", "erased", STAMP))
    refused(world, insert, (contact_id, REF, "ghost", "erased", STAMP))
    refused(world, insert, (contact_id, REF, "dsr", "forgotten", STAMP))


def test_a_request_names_only_a_receipt_of_its_own_contact(world: World) -> None:
    _, jane = world.jane()
    other_lead = world.lead("other.co.uk", "Other LLP")
    bob = world.contact(other_lead, name="Bob Other", email="bob@other.co.uk")
    bobs = world.erase(bob, OTHER_REF)
    request_id = world.transact(lambda: world.erasures.open_dsr(jane, "erase", REF))
    update = "UPDATE data_subject_requests SET receipt_id = ? WHERE id = ?"

    mismatch = "a request's receipt must exist and be its own Contact's"
    refused(world, update, (bobs.receipt_id, request_id), match=mismatch)  # another Contact's
    refused(world, update, ("01JD3QZ8X5V9K2M7N4P6R8T0Z7", request_id), match=mismatch)  # none
    [(receipt_id,)] = world.sql(
        "SELECT receipt_id FROM data_subject_requests WHERE contact_id = ?", (bob,)
    )
    assert receipt_id == bobs.receipt_id
