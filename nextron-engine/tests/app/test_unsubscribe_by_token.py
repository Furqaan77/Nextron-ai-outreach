"""``Compliance.unsubscribe_by_token`` (FR-70, FR-41, FR-50, AD-15, AD-16, Story 3.11) on a
temporary database at head with the real subscriber registry and ``FrozenClock``: every row of
the spec's I/O matrix (a new one-click with a Contact, no Contact, a repeat across and within a
transaction, tampered tokens through the real ``TokenCodec`` and the shared vectors, mailto, bad
arguments, a keyring failure), the Lead closed ``lost``, an erased Contact still matched, no
address or token in ``events``, the notification or a log line, the ``unsub_requests`` table's
triggers, checks and unique index, and ``queries.research.contacts_with_address_hash``."""

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.crypto.unsub_token import TokenCodec
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.research import contacts as contacts_repo
from nextron.db.uow import UnitOfWork, UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import key_hash
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.identity import IdentityError
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.notifications import Notification, SubjectSuppressed
from nextron.domain.segments import validate_segments
from nextron.domain.unsub_requests import UnsubOutcome
from nextron.domain.unsub_token import token_hash
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import KEYRING_FAILED
from tests.fakes.clock import FrozenClock
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.token_codec import FakeTokenCodec
from tests.migration_chains import run_sql

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
LATER_STAMP = "2026-11-18T10:00:00.000000Z"
RECEIVED = datetime(2026, 11, 17, 22, 15, 30, tzinfo=UTC)
RECEIVED_STAMP = "2026-11-17T22:15:30.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
EMAIL = "jane.doe@firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
ADDRESS = key_hash("address", EMAIL)
PERSON = key_hash("person", ("Jane Doe", "firm.co.uk"))
PROFILE = key_hash("profile", LINKEDIN)
VECTORS = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "unsub_token_vectors.json").read_text("utf-8")
)
PERMANENT = "unsubscribe request records are permanent"
# Text that must never reach events, a notification or a log line (none can occur in a ULID).
PLAIN = ("doe", "firm", "linkedin", "@")
LOGGERS = ("nextron.app.compliance", "nextron.adapters.crypto")


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    codec: FakeTokenCodec
    discovery: Discovery
    research: Research
    compliance: Compliance
    segment: str = ""

    def transact[R](self, fn: Callable[[], R]) -> R:
        """Run ``fn`` inside a caller's use case and transaction, as the poll does."""

        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()

    def lead(self, web_domain: str = "firm.co.uk", name: str = "Firm LLP") -> str:
        identity = LeadIdentity(name=name, segment_id=self.segment, web_domain=web_domain)
        return self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)).lead_id

    def contact(self, lead_id: str, **fields: str) -> str:
        candidate = ContactCandidate(**fields)
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id,
                candidate,
                source=ProvenanceSource.CSV,
                detail="Law Society directory export",
                retrieved_at=T0,
            )
        )
        return added.contact_id

    def token(self, address_hash: str = ADDRESS) -> str:
        return self.codec.encode(1, bytes.fromhex(address_hash), int(T0.timestamp()))

    def unsubscribe(
        self,
        token: object,
        source: object = "one_click",
        received_at: object = RECEIVED,
        compliance: Compliance | None = None,
    ) -> UnsubOutcome:
        chosen = compliance or self.compliance
        return self.transact(
            lambda: chosen.unsubscribe_by_token(token, source, received_at)  # type: ignore[arg-type]
        )

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

    def entries(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT kind, value_hash, status, source, cause_ref FROM suppression_entries "
            "ORDER BY rowid"
        )

    def requests(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT id, token_hash, received_at, processed_at FROM unsub_requests ORDER BY rowid"
        )

    def stage(self, lead_id: str) -> str:
        [(stage,)] = self.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (lead_id,))
        return str(stage)

    def dump(self) -> list[list[tuple[Any, ...]]]:
        return [
            self.sql(f"SELECT * FROM {table} ORDER BY rowid")
            for table in ("suppression_entries", "unsub_requests", "events", "leads")
        ]


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
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    codec = FakeTokenCodec()
    found = World(
        units,
        db_engine,
        clock,
        codec,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        Compliance(clock, codec=codec),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
    return found


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Notification]]:
    seen: list[Notification] = []
    original = UnitOfWork.publish

    def recording(self: UnitOfWork, notification: Notification) -> None:
        seen.append(notification)
        original(self, notification)

    monkeypatch.setattr(UnitOfWork, "publish", recording)
    yield seen


def jane(world: World) -> tuple[str, str]:
    lead_id = world.lead()
    return lead_id, world.contact(lead_id, name="Jane Doe", email=EMAIL, linkedin_url=LINKEDIN)


def assert_no_personal_data(values: object, *tokens: str) -> None:
    text = repr(values)
    for plain in PLAIN:
        assert plain not in text.lower(), plain
    assert ADDRESS not in text
    for token in tokens:
        assert token not in text


# The matrix -------------------------------------------------------------------------------------


def test_a_new_one_click_suppresses_the_address_and_person_and_closes_the_lead_lost(
    world: World, published: list[Notification], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lead_id, contact_id = jane(world)
    token = world.token()

    assert world.unsubscribe(token) is UnsubOutcome.APPLIED

    [(request_id, hashed, received, processed)] = world.requests()
    assert (hashed, received, processed) == (token_hash(token), RECEIVED_STAMP, LATER_STAMP)
    assert world.entries() == [
        ("address", ADDRESS, "active", "one_click", request_id),
        ("person", PERSON, "active", "one_click", request_id),
        ("profile", PROFILE, "active", "one_click", request_id),
    ]
    # suppress(ContactSubject) publishes its first written entry (here the person key)
    [(person_entry,)] = world.sql("SELECT id FROM suppression_entries WHERE kind = 'person'")
    assert published == [
        SubjectSuppressed(
            suppression_entry_id=person_entry,
            source="one_click",
            contact_id=contact_id,
            lead_id=lead_id,
        )
    ]
    assert world.stage(lead_id) == "lost"
    events = world.sql(
        "SELECT cause, cause_ref, actor FROM events WHERE entity = 'suppression_entries'"
    )
    assert events == [("one_click", request_id, "system:compliance")] * 3
    assert_no_personal_data(world.sql("SELECT * FROM events"), token)
    assert_no_personal_data(published, token)
    assert [r for r in caplog.records if r.name.startswith(LOGGERS)] == []


def test_every_contact_holding_the_address_is_suppressed_oldest_first(
    world: World, published: list[Notification]
) -> None:
    first_lead, first = jane(world)
    other_lead = world.lead("other-firm.co.uk", "Other Firm LLP")
    second = world.contact(other_lead, name="J Doe", email=EMAIL)

    assert world.unsubscribe(world.token()) is UnsubOutcome.APPLIED

    assert [(n.contact_id, n.lead_id) for n in published] == [
        (first, first_lead),
        (second, other_lead),
    ]
    assert (world.stage(first_lead), world.stage(other_lead)) == ("lost", "lost")


def test_with_no_contact_only_the_address_key_is_written_and_published_alone(
    world: World, published: list[Notification]
) -> None:
    token = world.token()

    assert world.unsubscribe(token) is UnsubOutcome.APPLIED

    [(request_id, *_)] = world.requests()
    assert world.entries() == [("address", ADDRESS, "active", "one_click", request_id)]
    [(entry_id,)] = world.sql("SELECT id FROM suppression_entries")
    assert published == [
        SubjectSuppressed(
            suppression_entry_id=entry_id, source="one_click", contact_id=None, lead_id=None
        )
    ]


def test_a_repeat_in_a_later_transaction_is_a_duplicate_and_writes_nothing(
    world: World, published: list[Notification]
) -> None:
    jane(world)
    token = world.token()
    assert world.unsubscribe(token) is UnsubOutcome.APPLIED
    before = world.dump()
    published.clear()
    world.codec.decoded.clear()

    assert world.unsubscribe(token, received_at=RECEIVED + timedelta(days=1)) is (
        UnsubOutcome.DUPLICATE
    )

    assert world.dump() == before
    assert published == []
    assert world.codec.decoded == []  # the record answers before any keyring read


def test_the_same_token_twice_in_one_transaction_lands_once(world: World) -> None:
    jane(world)
    token = world.token()

    outcomes = world.transact(
        lambda: [
            world.compliance.unsubscribe_by_token(token, "one_click", RECEIVED),
            world.compliance.unsubscribe_by_token(token, "one_click", RECEIVED),
        ]
    )

    assert outcomes == [UnsubOutcome.APPLIED, UnsubOutcome.DUPLICATE]
    assert len(world.requests()) == 1
    assert len(world.entries()) == 3


def test_an_address_already_suppressed_still_records_the_request_and_closes_the_lead(
    world: World,
) -> None:
    lead_id, _ = jane(world)
    assert world.unsubscribe(world.token()) is UnsubOutcome.APPLIED  # a first link
    world.sql("UPDATE leads SET pipeline_stage = 'new' WHERE id = ?", (lead_id,))

    assert world.unsubscribe(world.token()) is UnsubOutcome.APPLIED  # another message's link

    assert len(world.requests()) == 2
    assert len(world.entries()) == 3  # every key was active already
    assert world.stage(lead_id) == "lost"


def test_mailto_is_applied_the_same_way_with_its_own_source(world: World) -> None:
    lead_id, _ = jane(world)

    assert world.unsubscribe(world.token(), source="mailto") is UnsubOutcome.APPLIED

    assert {row[3] for row in world.entries()} == {"mailto"}
    assert world.stage(lead_id) == "lost"


def test_an_erased_contact_is_still_matched_by_its_tombstone(
    world: World, published: list[Notification]
) -> None:
    lead_id, contact_id = jane(world)
    world.transact(lambda: contacts_repo.tombstone(contact_id, LATER))

    assert world.unsubscribe(world.token()) is UnsubOutcome.APPLIED

    assert [(n.contact_id, n.lead_id) for n in published] == [(contact_id, lead_id)]
    assert [row[:2] for row in world.entries()] == [("address", ADDRESS)]


@pytest.mark.parametrize(
    "token",
    [None, 7, b"x" * 108, "", "A" * 107, "A" * 109, "A" * 107 + "=", "A" * 107 + "+", "é" * 108],
)
def test_a_token_that_is_not_108_base64url_characters_is_invalid_with_nothing_read(
    world: World, token: object
) -> None:
    before = world.dump()

    assert world.unsubscribe(token) is UnsubOutcome.INVALID

    assert world.dump() == before
    assert world.codec.decoded == []


@pytest.mark.parametrize("case", VECTORS["tampered"], ids=lambda case: str(case["name"]))
def test_a_tampered_vector_is_invalid_and_nothing_is_written(
    world: World, case: dict[str, Any]
) -> None:
    secrets = InMemorySecrets()
    for kid, keys in VECTORS["keys"].items():
        for name, value in keys.items():
            secrets.write(f"unsub/{name}/{kid}", value)
    real = Compliance(world.clock, codec=TokenCodec(secrets))
    jane(world)
    before = world.dump()

    assert world.unsubscribe(case["token"], compliance=real) is UnsubOutcome.INVALID

    assert world.dump() == before


def test_a_valid_vector_through_the_real_codec_suppresses_its_address(world: World) -> None:
    secrets = InMemorySecrets()
    for kid, keys in VECTORS["keys"].items():
        for name, value in keys.items():
            secrets.write(f"unsub/{name}/{kid}", value)
    real = Compliance(world.clock, codec=TokenCodec(secrets))
    vector = VECTORS["valid"][0]

    assert world.unsubscribe(vector["token"], compliance=real) is UnsubOutcome.APPLIED

    assert [row[:2] for row in world.entries()] == [("address", vector["addr_hash"])]
    assert [row[1] for row in world.requests()] == [vector["token_hash"]]


def test_a_keyring_failure_propagates_and_nothing_is_written(world: World) -> None:
    jane(world)
    token = world.token()
    world.codec.fail = AdapterError(True, KEYRING_FAILED, "Credential Manager failed")
    before = world.dump()

    with pytest.raises(AdapterError) as raised:
        world.unsubscribe(token)

    assert raised.value.code == KEYRING_FAILED
    assert world.dump() == before


@pytest.mark.parametrize("source", ["operator", "reply_rule", "dsr", "import", None, 1, ""])
def test_a_source_other_than_one_click_or_mailto_is_refused(world: World, source: object) -> None:
    with pytest.raises(ValueError, match="one_click or mailto"):
        world.unsubscribe(world.token(), source=source)


@pytest.mark.parametrize("received_at", [datetime(2026, 11, 17, 22, 0), "2026-11-17", None])
def test_received_at_must_be_an_aware_datetime(world: World, received_at: object) -> None:
    with pytest.raises(TypeError, match="aware datetime"):
        world.unsubscribe(world.token(), received_at=received_at)


def test_an_offset_received_at_is_stored_in_utc(world: World) -> None:
    india = timezone(timedelta(hours=5, minutes=30))

    world.unsubscribe(world.token(), received_at=RECEIVED.astimezone(india))

    assert [row[2] for row in world.requests()] == [RECEIVED_STAMP]


def test_without_a_codec_it_raises_type_error(world: World) -> None:
    with pytest.raises(TypeError, match="codec"):
        world.unsubscribe(world.token(), compliance=Compliance(world.clock))


# The table --------------------------------------------------------------------------------------


def test_the_records_are_permanent(world: World) -> None:
    world.unsubscribe(world.token())
    [(request_id, hashed, *_)] = world.requests()

    for statement in (
        "DELETE FROM unsub_requests",
        "UPDATE unsub_requests SET processed_at = received_at",
        f"INSERT OR REPLACE INTO unsub_requests VALUES ('{request_id}', '{'0' * 64}', "
        f"'{RECEIVED_STAMP}', '{LATER_STAMP}')",
        f"INSERT INTO unsub_requests VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0ZZ', '{hashed}', "
        f"'{RECEIVED_STAMP}', '{LATER_STAMP}')",
    ):
        with pytest.raises(sqlite3.IntegrityError, match=PERMANENT):
            run_sql(world.db_file, statement)
    assert len(world.requests()) == 1


@pytest.mark.parametrize("bad_hash", ["A" * 64, "0" * 63, "g" * 64, "0" * 65])
def test_the_token_hash_must_be_64_lowercase_hex(world: World, bad_hash: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="ck_unsub_requests_token_hash"):
        run_sql(
            world.db_file,
            f"INSERT INTO unsub_requests VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0ZZ', '{bad_hash}', "
            f"'{RECEIVED_STAMP}', '{LATER_STAMP}')",
        )


def test_the_table_has_its_unique_index_and_three_triggers(world: World) -> None:
    indexes = world.sql("PRAGMA index_list('unsub_requests')")
    assert {(row[1], row[2]) for row in indexes if not row[1].startswith("sqlite_")} == {
        ("uq_unsub_requests_token_hash", 1)
    }
    triggers = world.sql(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'unsub_requests' "
        "ORDER BY name"
    )
    assert triggers == [
        ("trg_unsub_requests_immutable",),
        ("trg_unsub_requests_no_delete",),
        ("trg_unsub_requests_no_reuse",),
    ]
    columns = world.sql("PRAGMA table_info('unsub_requests')")
    assert [(row[1], row[3]) for row in columns] == [
        ("id", 1),
        ("token_hash", 1),
        ("received_at", 1),
        ("processed_at", 1),
    ]


# The query --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "A" * 64, "0" * 63, None, 7, ADDRESS.upper()])
def test_contacts_with_address_hash_is_empty_for_a_malformed_hash(world: World, bad: object) -> None:
    jane(world)

    assert world.read(lambda: research_queries.contacts_with_address_hash(bad)) == ()  # type: ignore[arg-type]


def test_contacts_with_address_hash_finds_live_and_erased_contacts_oldest_first(
    world: World,
) -> None:
    lead_id, first = jane(world)
    world.clock.set(LATER + timedelta(minutes=1))
    second = world.contact(world.lead("other.co.uk", "Other LLP"), name="J Doe", email=EMAIL)
    world.contact(lead_id, name="Someone Else", email="someone@firm.co.uk")
    world.contact(lead_id, name="No Address")
    world.transact(lambda: contacts_repo.tombstone(first, LATER))

    found = world.read(lambda: research_queries.contacts_with_address_hash(ADDRESS))

    assert [contact.id for contact in found] == [first, second]


def test_a_stored_email_that_no_longer_normalises_is_skipped_not_raised(world: World) -> None:
    lead_id, contact_id = jane(world)
    broken = world.contact(lead_id, name="Sam Lee", email="sam.lee@firm.co.uk")
    world.sql("UPDATE contacts SET email = ? WHERE id = ?", ("not an address", broken))
    with pytest.raises(IdentityError):
        key_hash("address", "not an address")  # the stored value the hash refuses

    found = world.read(lambda: research_queries.contacts_with_address_hash(ADDRESS))

    assert [contact.id for contact in found] == [contact_id]
    assert world.unsubscribe(world.token()) is UnsubOutcome.APPLIED
