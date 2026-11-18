"""The erasure ledger (FR-50, FR-52, AD-16, AD-17, AD-20, Story 3.16) on a temporary database at
head with ``FrozenClock``, the real subscriber registry, the real ``Notify`` and a ledger file in
a temporary backup dir: every row of the spec's I/O matrix on real units of work.

Appends after each commit (suppress, a domain key, a void, a DSR and a retention erasure, each
flushed and fsynced with the acceptance criteria's fields); no line for a rolled-back change; a
failed append that commits, alerts and is re-appended by the startup sync; a failed alert that
is swallowed; the startup sync of rows from before the story (once, then 0); a torn tail; the
end-to-end T0/T1 restore through ``RecoveryRunner.startup`` (the gate blocks the address, the
Contact is tombstoned, the voided entry voided, the step done and recovery mode ended);
reapplying twice; a missing ledger (the step pending, the health issue, no file created); a
reapply whose erasure raises (all rolled back, the step pending, the health issue); the
redaction detector over a ledger an end-to-end DSR erasure wrote; and the restore test's count
check on a real ``suppression_entries`` count."""

import json
import logging
import os
import re
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
from nextron.app.compliance import ledger as ledger_module
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.compliance.ledger import (
    ERASURE_LEDGER_FAILED,
    ErasureLedger,
    LedgerAppender,
    LedgerFile,
    failed_text,
)
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Recovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.engine import create_engine
from nextron.db.migrations import head_revision
from nextron.db.repos.ops.snapshot import snapshot
from nextron.db.uow import UnitOfWorkFactory, command, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.alert import Channel
from nextron.domain.backup import ERASURE_LEDGER_FILE
from nextron.domain.contactability import (
    AddressSubject,
    Blocked,
    BlockReason,
    ContactSubject,
    SuppressionScope,
    key_hash,
)
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.erasure import ErasureCause
from nextron.domain.erasure_ledger import REAPPLIED, Erased, parse
from nextron.domain.health import HealthIssue
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.recovery import RecoveryStep, StepStatus
from nextron.domain.redaction import redact
from nextron.domain.restore_test import CheckOutcome, RestoreFailure, judge
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import SuppressionSource
from nextron.jobs.recovery import RecoveryRunner
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=2)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
WEB = "firm.co.uk"
REF = "DSR-2026-0042"
NAME = "Jane Plantdoe"
EMAIL = "jane.plantdoe@firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-plantdoe"
OPTED_OUT = "planted.optout@other.co.uk"
RULE_ADDRESS = "planted.rule@third.co.uk"
T1_DOMAIN = "laterfirm.com"
VOID_REASON = "Plantvoid: Jane wrote back to say yes"
LEDGER_LOGGER = "nextron.app.compliance.ledger"
HEX64 = re.compile(r"[0-9a-f]{64}")


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
    )
    return JobQueue(clock, job_types)


def sql(path: Path, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        return [tuple(row) for row in connection.execute(statement, params).fetchall()]
    finally:
        connection.close()


@dataclass
class World:
    """Compliance with a ledger, Erasures and the ErasureLedger on real units of work."""

    engine: Engine
    units: UnitOfWorkFactory
    clock: FrozenClock
    file: LedgerFile
    notify: Notify
    discovery: Discovery
    research: Research
    compliance: Compliance
    erasures: Erasures
    ledger: ErasureLedger
    segment: str

    @property
    def db_file(self) -> Path:
        assert self.engine.url.database
        return Path(self.engine.url.database)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        return sql(self.db_file, statement, params)

    def transact[R](self, fn: Callable[[], R], units: UnitOfWorkFactory | None = None) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with (units or self.units).open():
            return caller()

    def read[R](self, fn: Callable[[], R], units: UnitOfWorkFactory | None = None) -> R:
        with (units or self.units).open(read_only=True):
            return fn()

    def lead(self, web_domain: str = WEB, name: str = "Firm LLP") -> str:
        identity = LeadIdentity(name=name, segment_id=self.segment, web_domain=web_domain)
        return self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)).lead_id

    def contact(self, lead_id: str, **fields: Any) -> str:
        candidate = ContactCandidate(**fields)
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id,
                candidate,
                source=ProvenanceSource.CSV,
                detail="directory export",
                retrieved_at=T0,
            )
        )
        assert added.contact_id is not None
        return added.contact_id

    def jane(self) -> str:
        return self.contact(self.lead(), name=NAME, email=EMAIL, linkedin_url=LINKEDIN)

    def add(self, text: str, compliance: Compliance | None = None) -> tuple[str, ...]:
        with self.units.open():
            return (compliance or self.compliance).add_value(text).written

    def rule_entry(self, address: str = RULE_ADDRESS, compliance: Compliance | None = None) -> str:
        """A ``reply_rule`` entry on ``address`` (Epic 7 writes these)."""
        chosen = compliance or self.compliance
        result = self.transact(
            lambda: chosen.suppress(
                AddressSubject(address), SuppressionScope.PERSON, SuppressionSource.REPLY_RULE, None
            )
        )
        [entry_id] = result.written
        return entry_id

    def reverse(self, entry_id: str, compliance: Compliance | None = None) -> None:
        with self.units.open():
            (compliance or self.compliance).reverse_entry(entry_id, VOID_REASON)

    def erase(self, contact_id: str, erasures: Erasures | None = None) -> None:
        with self.units.open():
            (erasures or self.erasures).erase_on_request(contact_id, REF)

    def retention(self, contact_id: str) -> None:
        self.transact(lambda: self.erasures.erase_contact(contact_id, None, "retention"))

    def lines(self) -> list[dict[str, Any]]:
        if not self.file.exists():
            return []
        text = self.file.path.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def entries(self, units_file: Path | None = None) -> list[tuple[Any, ...]]:
        return sql(
            units_file or self.db_file,
            "SELECT id, kind, value_hash, status, source, cause_ref, void_reason "
            "FROM suppression_entries ORDER BY rowid",
        )


def build_world(engine: Engine, ledger_path: Path) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    queue = queue_on(clock)
    notify = Notify(clock, queue, alert_ports={Channel.TELEGRAM: lambda: InMemoryAlert()})
    file = LedgerFile(ledger_path)
    file.ensure()  # the engine's first start makes it; a test that needs none removes it
    compliance = Compliance(clock, ledger=LedgerAppender(file, notify))
    erasures = Erasures(clock, compliance)
    [(segment,), *_] = sql(Path(str(engine.url.database)), "SELECT id FROM segments ORDER BY key")
    return World(
        engine,
        units,
        clock,
        file,
        notify,
        Discovery(clock, Pipeline(clock), queue),
        Research(clock),
        compliance,
        erasures,
        ErasureLedger(clock, file, compliance, erasures),
        segment,
    )


@pytest.fixture
def world(db_engine: Engine, tmp_path: Path) -> World:
    return build_world(db_engine, tmp_path / "backups" / ERASURE_LEDGER_FILE)


def no_ledger(world: World) -> None:
    """No ledger file: a fresh install, or a restore whose ledger is still missing."""
    world.file.path.unlink()


def unledgered(world: World) -> tuple[Compliance, Erasures]:
    """A Compliance and Erasures with no ledger: the writers as they were before Story 3.16."""
    compliance = Compliance(world.clock)
    return compliance, Erasures(world.clock, compliance)


def messages(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == name]


# Append ------------------------------------------------------------------------------------------


def test_each_writer_appends_one_fsynced_line_after_its_commit(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(ledger_module.os, "fsync", fsync)
    stamp = "2026-11-18T09:00:00.000000Z"

    [address_id] = world.add(OPTED_OUT)
    assert world.lines() == [
        {
            "v": 1,
            "type": "suppressed",
            "entry_id": address_id,
            "kind": "address",
            "value_hash": key_hash("address", OPTED_OUT),
            "source": "operator",
            "at": stamp,
        }
    ]
    assert len(synced) == 1

    [domain_id] = world.add("otherfirm.com")
    assert world.lines()[-1] == {
        "v": 1,
        "type": "suppressed",
        "entry_id": domain_id,
        "kind": "domain",
        "value_hash": key_hash("domain", "otherfirm.com"),
        "source": "operator",
        "at": stamp,
        "domain": "otherfirm.com",
    }

    rule_id = world.rule_entry()
    world.clock.set(T1)
    world.reverse(rule_id)
    assert world.lines()[-1] == {
        "v": 1,
        "type": "voided",
        "entry_id": rule_id,
        "at": "2026-11-18T11:00:00.000000Z",
    }

    jane = world.jane()
    before = len(world.lines())
    world.erase(jane)
    added = world.lines()[before:]
    assert [line["type"] for line in added] == ["suppressed"] * 3 + ["erased"]
    assert {line["kind"] for line in added[:3]} == {"address", "person", "profile"}
    assert all(line["source"] == "dsr" for line in added[:3])
    assert added[3] == {
        "v": 1,
        "type": "erased",
        "contact_id": jane,
        "cause": "dsr",
        "at": "2026-11-18T11:00:00.000000Z",
    }

    bob = world.contact(world.lead("bobfirm.co.uk", "Bob LLP"), name="Bob Other")
    world.retention(bob)
    assert world.lines()[-1] == {
        "v": 1,
        "type": "erased",
        "contact_id": bob,
        "cause": "retention",
        "at": "2026-11-18T11:00:00.000000Z",
    }
    # one write per commit, each fsynced; every line is one JSON object ending in a newline
    assert len(synced) == 7
    data = world.file.path.read_bytes()
    assert data.endswith(b"\n")
    assert parse(data).malformed == 0


def test_a_rolled_back_change_writes_no_line(world: World) -> None:
    @use_case
    def failing() -> None:
        with current().transaction():
            world.compliance.suppress(
                AddressSubject(OPTED_OUT),
                SuppressionScope.PERSON,
                SuppressionSource.ONE_CLICK,
                None,
            )
            raise KeyError("boom")

    with world.units.open(), pytest.raises(KeyError):
        failing()

    assert world.lines() == []
    assert world.entries() == []


def test_a_failed_append_commits_alerts_and_the_next_sync_appends_it(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(lines: object) -> None:
        raise PermissionError("the backup dir is read-only")

    monkeypatch.setattr(world.file, "append", refuse)
    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        [entry_id] = world.add(OPTED_OUT)

    assert [row[0] for row in world.entries()] == [entry_id]  # committed, not rolled back
    [failed] = messages(caplog, "erasure_ledger_append_failed")
    assert (failed.levelno, failed.count, failed.code) == (  # type: ignore[attr-defined]
        logging.ERROR,
        1,
        "PermissionError",
    )
    text = failed_text(1, "PermissionError")
    assert text == (
        "Erasure ledger append failed: 1 line(s) not written (PermissionError); the next engine "
        "start re-appends them"
    )
    assert world.sql("SELECT kind, subject, channel, text FROM alerts") == [
        (ERASURE_LEDGER_FAILED, entry_id, "telegram", text)
    ]
    assert world.sql("SELECT kind, subject, text FROM notifications") == [
        (ERASURE_LEDGER_FAILED, entry_id, text)
    ]
    assert "read-only" not in caplog.text

    monkeypatch.undo()
    with world.units.open():
        assert world.ledger.sync() == 1
    assert [line["entry_id"] for line in world.lines()] == [entry_id]
    with world.units.open():
        assert world.ledger.sync() == 0


def test_a_failed_alert_is_logged_and_swallowed(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(lines: object) -> None:
        raise OSError("disk gone")

    @command
    def broken_alert(*args: object) -> None:
        raise RuntimeError("alerts down")

    monkeypatch.setattr(world.file, "append", refuse)
    monkeypatch.setattr(world.notify, "alert", broken_alert)
    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        [entry_id] = world.add(OPTED_OUT)

    assert [row[0] for row in world.entries()] == [entry_id]
    [alert_failed] = messages(caplog, "erasure_ledger_alert_failed")
    assert alert_failed.code == "RuntimeError"  # type: ignore[attr-defined]
    assert world.sql("SELECT COUNT(*) FROM alerts") == [(0,)]


def test_a_torn_tail_is_skipped_and_the_next_append_starts_a_new_line(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    [first] = world.add(OPTED_OUT)
    with open(world.file.path, "ab") as handle:  # a crash mid-write left half a line
        handle.write(b'{"v":1,"type":"voided","entry_id":"01JD3QZ8')
    torn = world.file.read()
    assert torn is not None and torn.torn and torn.malformed == 0

    [second] = world.add("planted.second@other.co.uk")

    found = world.file.read()
    assert found is not None
    assert (found.torn, found.malformed) == (False, 1)
    assert [line.entry_id for line in found.lines] == [first, second]  # type: ignore[union-attr]

    with world.units.open():
        Recovery(world.clock).begin(
            backup_at=T0, source_name="nextron-test.db", pre_restore_name=None
        )
    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER), world.units.open():
        assert world.ledger.reapply() is True
    [damaged] = messages(caplog, "erasure_ledger_damaged_lines")
    assert damaged.levelno == logging.WARNING
    assert (damaged.malformed, damaged.torn) == (1, False)  # type: ignore[attr-defined]


# Startup sync ------------------------------------------------------------------------------------


def test_the_startup_sync_appends_rows_from_before_the_story_once(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    no_ledger(world)
    compliance, erasures = unledgered(world)
    [address_id] = world.add(OPTED_OUT, compliance)
    [domain_id] = world.add("unledgered.com", compliance)
    rule_id = world.rule_entry(compliance=compliance)
    world.clock.set(T1)
    world.reverse(rule_id, compliance)
    jane = world.jane()
    world.erase(jane, erasures)
    bob = world.contact(world.lead("bobfirm.co.uk", "Bob LLP"), name="Bob Other")
    world.transact(lambda: erasures.erase_contact(bob, None, "retention"))
    assert not world.file.exists()
    entries = [
        entry_id
        for (entry_id,) in world.sql("SELECT id FROM suppression_entries ORDER BY created_at, id")
    ]
    erased = [
        contact_id
        for (contact_id,) in world.sql(
            "SELECT id FROM contacts WHERE erased_at IS NOT NULL ORDER BY erased_at, id"
        )
    ]

    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER), world.units.open():
        appended = world.ledger.sync()

    lines = world.lines()
    assert appended == len(lines) == len(entries) + 3
    [domain_line] = [line for line in lines if line.get("entry_id") == domain_id]
    assert (domain_line["kind"], domain_line["domain"]) == ("domain", "unledgered.com")
    assert list(domain_line)[-1] == "domain"
    assert [line["entry_id"] for line in lines[: len(entries)]] == entries
    assert address_id in entries
    assert lines[len(entries)] == {
        "v": 1,
        "type": "voided",
        "entry_id": rule_id,
        "at": "2026-11-18T11:00:00.000000Z",
    }
    causes = {jane: "dsr", bob: "retention"}
    assert [(line["contact_id"], line["cause"]) for line in lines[-2:]] == [
        (contact_id, causes[contact_id]) for contact_id in erased
    ]
    [synced] = messages(caplog, "erasure_ledger_synced")
    assert synced.appended == appended  # type: ignore[attr-defined]

    with world.units.open():
        assert world.ledger.sync() == 0
    assert len(world.lines()) == appended


def test_the_sync_creates_an_empty_ledger_on_a_fresh_install(world: World) -> None:
    no_ledger(world)
    with world.units.open():
        assert world.ledger.sync() == 0
    assert world.file.exists()
    assert world.file.path.read_bytes() == b""


def test_the_sync_never_creates_a_ledger_while_a_reapply_is_pending(world: World) -> None:
    no_ledger(world)
    world.add(OPTED_OUT, unledgered(world)[0])
    with world.units.open():
        Recovery(world.clock).begin(
            backup_at=T0, source_name="nextron-test.db", pre_restore_name=None
        )

    with world.units.open():
        assert world.ledger.sync() == 0

    assert not world.file.exists()


# Restore T0, changes at T1 -----------------------------------------------------------------------


@dataclass
class Restored:
    engine: Engine
    units: UnitOfWorkFactory
    db_file: Path


def take_backup(world: World, tmp_path: Path) -> Path:
    """A copy of the database now, as ``backup_db`` takes one."""
    target = tmp_path / "copies" / "t0.db"
    snapshot(world.db_file, target)
    return target


def restore(world: World, copy: Path) -> Iterator[Restored]:
    """The copy turned into a restored database, as ``nextron restore`` leaves it: recovery
    mode on, every step pending."""
    engine = create_engine(copy)
    try:
        units = UnitOfWorkFactory(engine, main.build_subscribers(clock=world.clock))
        with units.open():
            Recovery(world.clock).begin(
                backup_at=T0, source_name="nextron-test.db", pre_restore_name=None
            )
        yield Restored(engine, units, copy)
    finally:
        engine.dispose()


@pytest.fixture
def t0_t1(world: World, tmp_path: Path) -> Iterator[tuple[Restored, dict[str, str]]]:
    """At T0 a ``reply_rule`` entry and Jane; the backup; at T1 an opt-out, Jane erased and the
    rule entry voided; then the T0 copy restored."""
    rule_id = world.rule_entry()
    jane = world.jane()
    copy = take_backup(world, tmp_path)
    world.clock.set(T1)
    world.add(OPTED_OUT)
    world.add(T1_DOMAIN)
    world.erase(jane)
    world.reverse(rule_id)
    for restored in restore(world, copy):
        yield restored, {"rule": rule_id, "jane": jane}


def steps_of(units: UnitOfWorkFactory) -> dict[str, str]:
    with units.open(read_only=True):
        row = ops_queries.recovery_state()
    assert row is not None
    return {str(step): str(status) for step, status in row.steps}


def test_a_restore_reapplies_the_ledger_before_any_job_runs(
    world: World, t0_t1: tuple[Restored, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    restored, ids = t0_t1
    # the restored database has forgotten T1
    assert world.read(
        lambda: compliance_queries.active_entry("address", key_hash("address", OPTED_OUT)),
        restored.units,
    ) is None
    runner = RecoveryRunner(
        restored.units,
        Recovery(world.clock),
        main.build_recovery_steps(reapply=world.ledger.reapply),
    )

    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        runner.startup()

    def gate() -> Any:
        return compliance_api.assert_contactable(ContactSubject(ids["jane"]), "email", "cold")

    assert world.read(
        lambda: compliance_queries.active_entry("address", key_hash("address", OPTED_OUT)),
        restored.units,
    ) is not None
    [domain_row] = sql(
        restored.db_file,
        "SELECT status, domain_plain, source FROM suppression_entries WHERE value_hash = ?",
        (key_hash("domain", T1_DOMAIN),),
    )
    assert domain_row == ("active", T1_DOMAIN, "operator")
    blocked = world.read(gate, restored.units)
    assert isinstance(blocked, Blocked)
    assert BlockReason.CONTACT_ERASED in blocked.reasons
    [(erased_at, name)] = sql(
        restored.db_file, "SELECT erased_at, name FROM contacts WHERE id = ?", (ids["jane"],)
    )
    assert erased_at is not None and name == "[erased]"
    [(status, reason)] = sql(
        restored.db_file,
        "SELECT status, void_reason FROM suppression_entries WHERE id = ?",
        (ids["rule"],),
    )
    assert (status, reason) == ("voided", REAPPLIED)
    [(request_ref, request_status)] = sql(
        restored.db_file, "SELECT request_ref, status FROM data_subject_requests"
    )
    assert (request_ref, request_status) == (REAPPLIED, "completed")
    assert sql(restored.db_file, "SELECT COUNT(*) FROM jobs WHERE status = 'running'") == [(0,)]
    assert steps_of(restored.units)[RecoveryStep.REAPPLY_ERASURE_LEDGER] == StepStatus.DONE
    [done] = messages(caplog, "erasure_ledger_reapplied")
    assert done.erased == 1 and done.voided == 1  # type: ignore[attr-defined]
    # every other step has no handler: check_recovery ends recovery mode
    assert runner.check() is False
    with restored.units.open(read_only=True):
        assert ops_queries.recovery_mode() is False


def test_reapplying_twice_changes_nothing_more(
    world: World, t0_t1: tuple[Restored, dict[str, str]]
) -> None:
    restored, _ = t0_t1
    with restored.units.open():
        assert world.ledger.reapply() is True
    entries = world.entries(restored.db_file)
    contacts = sql(restored.db_file, "SELECT * FROM contacts ORDER BY id")
    requests = sql(restored.db_file, "SELECT * FROM data_subject_requests ORDER BY id")
    events = sql(restored.db_file, "SELECT COUNT(*) FROM events")

    with restored.units.open():
        assert world.ledger.reapply() is True

    assert world.entries(restored.db_file) == entries
    assert sql(restored.db_file, "SELECT * FROM contacts ORDER BY id") == contacts
    assert sql(restored.db_file, "SELECT * FROM data_subject_requests ORDER BY id") == requests
    assert sql(restored.db_file, "SELECT COUNT(*) FROM events") == events


def test_a_reinstated_entry_keeps_its_source_and_names_the_original(
    world: World, t0_t1: tuple[Restored, dict[str, str]]
) -> None:
    restored, _ = t0_t1
    [original] = [
        line for line in world.lines() if line.get("value_hash") == key_hash("address", OPTED_OUT)
    ]
    with restored.units.open():
        world.ledger.reapply()

    [row] = [
        row
        for row in world.entries(restored.db_file)
        if row[2] == key_hash("address", OPTED_OUT)
    ]
    assert row[3:6] == ("active", "operator", original["entry_id"])
    assert row[0] != original["entry_id"]
    [event] = sql(
        restored.db_file,
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'suppression_entries' AND entity_id = ?",
        (row[0],),
    )
    assert event == (None, "active", "operator", original["entry_id"], "system:compliance")
    # the new entry is in the ledger too, appended after the reapply's commit
    assert row[0] in {line.get("entry_id") for line in world.lines()}


def test_a_missing_ledger_keeps_recovery_mode_on_and_shows_the_issue(
    world: World, t0_t1: tuple[Restored, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    restored, _ = t0_t1
    world.file.path.rename(world.file.path.with_name("elsewhere.jsonl"))
    runner = RecoveryRunner(
        restored.units,
        Recovery(world.clock),
        main.build_recovery_steps(reapply=world.ledger.reapply),
    )

    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        runner.startup()

    assert steps_of(restored.units)[RecoveryStep.REAPPLY_ERASURE_LEDGER] == StepStatus.PENDING
    assert messages(caplog, "erasure_ledger_missing")[0].levelno == logging.WARNING
    assert runner.check() is True  # still on: sending stays held
    with restored.units.open(read_only=True):
        recovery_id = compliance_queries.pending_reapply()
        issues = compliance_queries.health_issues(world.file.path)
    assert recovery_id is not None
    assert issues == (
        HealthIssue(
            "erasure_ledger_missing",
            recovery_id,
            "Erasure ledger not found; sending stays blocked",
            detail=f"Put the ledger at {world.file.path} and restart the engine",
        ),
    )
    with restored.units.open():
        assert world.ledger.sync() == 0
    assert not world.file.exists()


def test_a_failing_reapply_rolls_everything_back_and_stays_pending(
    world: World,
    t0_t1: tuple[Restored, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    restored, ids = t0_t1

    @command
    def failing_erase(*args: object) -> None:
        raise RuntimeError("an erase hook failed")

    monkeypatch.setattr(world.erasures, "erase_contact", failing_erase)
    entries = world.entries(restored.db_file)
    lines = len(world.lines())
    runner = RecoveryRunner(
        restored.units,
        Recovery(world.clock),
        main.build_recovery_steps(reapply=world.ledger.reapply),
    )

    with caplog.at_level(logging.ERROR, logger="nextron"):
        runner.startup()

    assert world.entries(restored.db_file) == entries  # the voids and inserts rolled back too
    assert len(world.lines()) == lines
    assert steps_of(restored.units)[RecoveryStep.REAPPLY_ERASURE_LEDGER] == StepStatus.PENDING
    [failed] = messages(caplog, "recovery_step_failed")
    assert failed.step == "reapply_erasure_ledger"  # type: ignore[attr-defined]
    with restored.units.open(read_only=True):
        [issue] = compliance_queries.health_issues(world.file.path)
    assert (issue.kind, issue.text, issue.detail) == (
        "erasure_ledger_not_reapplied",
        "Erasure ledger not yet reapplied; sending stays blocked",
        "Restart the engine to retry; the log has recovery_step_failed",
    )
    assert sql(
        restored.db_file, "SELECT erased_at FROM contacts WHERE id = ?", (ids["jane"],)
    ) == [(None,)]


def test_no_issue_once_the_ledger_is_reapplied_or_without_a_restore(world: World) -> None:
    with world.units.open(read_only=True):
        assert compliance_queries.health_issues(world.file.path) == ()
        assert compliance_queries.pending_reapply() is None


# Redaction ---------------------------------------------------------------------------------------


def test_a_ledger_written_by_a_dsr_erasure_holds_no_personal_data(world: World) -> None:
    rule_id = world.rule_entry(EMAIL)  # a reply rule matched Jane's address
    world.reverse(rule_id)  # voided with a reason that names her
    jane = world.jane()
    world.erase(jane)

    data = world.file.path.read_bytes()
    text = data.decode("utf-8")
    masked = HEX64.sub("<hash>", text)
    assert redact(masked) == masked
    for plain in (NAME, "Plantdoe", "plantdoe", EMAIL, LINKEDIN, "linkedin", VOID_REASON, "@"):
        assert plain.encode() not in data, plain
    assert REF.encode() not in data


# The restore test's counts -----------------------------------------------------------------------


def test_the_restore_test_checks_a_real_suppression_entries_count(
    world: World, tmp_path: Path
) -> None:
    world.add(OPTED_OUT)
    world.add("otherfirm.com")
    copy = snapshot(world.db_file, tmp_path / "copies" / "counted.db")
    assert copy.suppression_count == 2

    passed = judge(
        integrity="ok",
        revision=head_revision(),
        head=head_revision(),
        revision_known=True,
        counts=(2, copy.provenance_count),
        recorded=(copy.suppression_count, copy.provenance_count),
    )
    fewer = judge(
        integrity="ok",
        revision=head_revision(),
        head=head_revision(),
        revision_known=True,
        counts=(1, copy.provenance_count),
        recorded=(copy.suppression_count, copy.provenance_count),
    )

    assert passed.checks[3] is CheckOutcome.PASS  # checked, never skipped as absent
    assert fewer.checks[3] is CheckOutcome.FAIL
    assert fewer.reason is RestoreFailure.SUPPRESSION_FEWER


# Reapply: a missing ledger is never created by a writer, and each erased line's branch -------------


def test_a_writer_never_creates_a_missing_ledger_while_a_reapply_is_pending(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    no_ledger(world)
    with world.units.open():
        Recovery(world.clock).begin(
            backup_at=T0, source_name="nextron-test.db", pre_restore_name=None
        )

    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        [entry_id] = world.add(OPTED_OUT)

    assert not world.file.exists()
    [failed] = messages(caplog, "erasure_ledger_append_failed")
    assert failed.code == "FileNotFoundError"  # type: ignore[attr-defined]
    assert world.sql("SELECT kind, subject FROM notifications") == [
        (ERASURE_LEDGER_FAILED, entry_id)
    ]
    with world.units.open():
        assert world.ledger.reapply() is False
    assert not world.file.exists()
    with world.units.open(read_only=True):
        assert compliance_queries.pending_reapply() is not None


def reapply_lines(world: World, *lines: Any) -> None:
    """A ledger holding ``lines``, a pending restore, then the reapply (which must succeed)."""
    world.file.append(lines)
    with world.units.open():
        Recovery(world.clock).begin(
            backup_at=T0, source_name="nextron-test.db", pre_restore_name=None
        )
    with world.units.open():
        assert world.ledger.reapply() is True


def contact_row(world: World, contact_id: str) -> tuple[Any, ...]:
    [row] = world.sql("SELECT erased_at, name FROM contacts WHERE id = ?", (contact_id,))
    return row


def test_an_erased_line_for_an_unknown_or_tombstoned_contact_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    jane = world.jane()
    world.retention(jane)  # tombstoned already
    before = world.sql("SELECT * FROM contacts ORDER BY id")
    unknown = "01JD3QZ8X5V9K2M7N4P6R8T0ZZ"

    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        reapply_lines(
            world,
            Erased(unknown, ErasureCause.DSR, T0),
            Erased(jane, ErasureCause.DSR, T0),
        )

    assert world.sql("SELECT * FROM contacts ORDER BY id") == before
    assert world.sql("SELECT COUNT(*) FROM data_subject_requests") == [(0,)]
    [done] = messages(caplog, "erasure_ledger_reapplied")
    assert done.erased == 0  # type: ignore[attr-defined]


def test_a_retention_line_is_reerased_as_retention_with_no_request(world: World) -> None:
    bob = world.contact(world.lead("bobfirm.co.uk", "Bob LLP"), name="Bob Other")
    entries = world.entries()

    reapply_lines(world, Erased(bob, ErasureCause.RETENTION, T0))

    erased_at, name = contact_row(world, bob)
    assert erased_at is not None and name == "[erased]"
    assert world.sql("SELECT COUNT(*) FROM data_subject_requests") == [(0,)]
    assert world.entries() == entries  # retention suppresses nothing
    assert world.lines()[-1]["cause"] == "retention"


def test_a_dsr_line_whose_name_the_reference_would_hold_is_reerased_as_retention(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    # every word of the name is in "reapplied after restore", so check_request_ref refuses it
    named = world.contact(world.lead("restorefirm.co.uk", "Restore LLP"), name="Restore")

    with caplog.at_level(logging.INFO, logger=LEDGER_LOGGER):
        reapply_lines(world, Erased(named, ErasureCause.DSR, T0))

    erased_at, name = contact_row(world, named)
    assert erased_at is not None and name == "[erased]"
    assert world.sql("SELECT COUNT(*) FROM data_subject_requests") == [(0,)]
    [warned] = messages(caplog, "erasure_ledger_dsr_reapplied_as_retention")
    assert warned.levelno == logging.WARNING
    assert warned.contact_id == named  # type: ignore[attr-defined]
    with world.units.open(read_only=True):
        row = ops_queries.recovery_state()
    assert row is not None
    with world.units.open():
        assert Recovery(world.clock).mark_done(row.id, RecoveryStep.REAPPLY_ERASURE_LEDGER)
    assert steps_of(world.units)[RecoveryStep.REAPPLY_ERASURE_LEDGER] == StepStatus.DONE
