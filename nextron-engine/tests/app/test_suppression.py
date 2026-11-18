"""``Compliance.suppress`` with the real subscriber registry (FR-50, FR-63, AD-3, AD-4, AD-16,
AD-17, Story 3.3) on a temporary database at head, with ``FrozenClock``: every row of the
spec's I/O matrix on real units of work (all keys, partial keys, a repeat, the three firm rows,
the non-opt-out sources, a closed Lead, no keys, a failing subscriber that rolls everything
back, bad arguments, and raw SQL the table's triggers refuse), the ``events`` rows and the
notification free of personal data, no log line, only hashes stored for an address, a person
and a profile (so an erased person stays matchable), and the repository's readers."""

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
from nextron.app.compliance.api import ACTORS, Compliance, SuppressResult
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import (
    Subscribers,
    UnitOfWork,
    UnitOfWorkFactory,
    command,
    current,
    use_case,
)
from nextron.domain import seed_yaml
from nextron.domain.contactability import (
    ContactSubject,
    SuppressionScope,
    key_hash,
)
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.notifications import Notification, SubjectSuppressed
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import OPT_OUT_SOURCES, SuppressionSource
from tests.fakes.clock import FrozenClock
from tests.migration_chains import run_sql

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP = "2026-11-18T09:00:00.000000Z"
LATER = T0 + timedelta(hours=1)
LATER_STAMP = "2026-11-18T10:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
CAUSE_REF = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
EMAIL = "jane.doe@firm.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
# Text that must never reach events, the notification or the log. None of it can occur in a
# ULID (Crockford base32 has no I, L, O or U), so a random id can't make these checks flake;
# "jane" could ("JANE" is valid base32), so it isn't listed.
PLAIN = ("doe", "firm", "linkedin", "gmail", "@")
ENTRY_COLUMNS = (
    "id, kind, value_hash, domain_plain, status, source, cause_ref, created_at, voided_at, "
    "void_reason"
)
COMPLIANCE_LOGGER = "nextron.app.compliance"


@dataclass
class World:
    """Discovery, Research, Compliance and Pipeline on real units of work."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    compliance: Compliance
    segment: str = ""

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

    def lead(self, web_domain: str | None = "firm.co.uk", **keys: Any) -> str:
        identity = LeadIdentity(
            name="Firm LLP", segment_id=self.segment, web_domain=web_domain, **keys
        )
        result = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        return result.lead_id

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

    def suppress(
        self,
        contact_id: str,
        source: SuppressionSource = SuppressionSource.REPLY_RULE,
        scope: SuppressionScope = SuppressionScope.PERSON,
        cause_ref: str | None = CAUSE_REF,
    ) -> SuppressResult:
        subject = ContactSubject(contact_id)
        return self.transact(lambda: self.compliance.suppress(subject, scope, source, cause_ref))

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(self.db_file, isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    def entries(self) -> list[dict[str, Any]]:
        rows = self.sql(f"SELECT {ENTRY_COLUMNS} FROM suppression_entries ORDER BY rowid")
        names = [name.strip() for name in ENTRY_COLUMNS.split(",")]
        return [dict(zip(names, row, strict=True)) for row in rows]

    def entry_events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events WHERE entity = 'suppression_entries' ORDER BY rowid"
        )

    def lead_events(self, lead_id: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT from_status, to_status, cause, cause_ref, actor, at FROM events "
            "WHERE entity = 'leads' AND entity_id = ? ORDER BY rowid",
            (lead_id,),
        )

    def stage(self, lead_id: str) -> str:
        [(stage,)] = self.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (lead_id,))
        return str(stage)

    def set_stage(self, lead_id: str, stage: str) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead_id))

    def dump(self) -> list[list[tuple[Any, ...]]]:
        return [
            self.sql(f"SELECT * FROM {table} ORDER BY rowid")
            for table in ("suppression_entries", "events", "leads")
        ]


def no_jobs() -> Any:
    """``build_job_types``'s services: no job runs in these tests."""
    raise AssertionError("no job runs in these tests")


def queue_on(clock: FrozenClock) -> JobQueue:
    """A job queue on the real job types; ``upsert_lead`` enqueues a CSV Lead's crawl there
    (Story 2.5)."""
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
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    found = World(
        units,
        db_engine,
        clock,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        Compliance(clock),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
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


def jane(world: World, **lead_keys: Any) -> tuple[str, str]:
    """A Lead on firm.co.uk and its Contact Jane Doe with an address and a LinkedIn URL."""
    lead_id = world.lead(**lead_keys)
    contact_id = world.contact(
        lead_id, name="Jane Doe", email=EMAIL, linkedin_url=LINKEDIN
    )
    return lead_id, contact_id


def entry(
    entry_id: str,
    kind: str,
    value_hash: str,
    source: str = "reply_rule",
    domain_plain: str | None = None,
    cause_ref: str | None = CAUSE_REF,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "kind": kind,
        "value_hash": value_hash,
        "domain_plain": domain_plain,
        "status": "active",
        "source": source,
        "cause_ref": cause_ref,
        "created_at": LATER_STAMP,
        "voided_at": None,
        "void_reason": None,
    }


def created(
    entry_id: str, source: str = "reply_rule", cause_ref: str | None = CAUSE_REF
) -> tuple[Any, ...]:
    actor = "operator" if source in ("operator", "import", "dsr") else "system:compliance"
    return ("suppression_entries", entry_id, None, "active", source, cause_ref, actor, LATER_STAMP)


ADDRESS = key_hash("address", EMAIL)
PERSON = key_hash("person", ("Jane Doe", "firm.co.uk"))
PROFILE = key_hash("profile", LINKEDIN)


def assert_no_personal_data(values: object) -> None:
    text = repr(values).lower()
    for plain in PLAIN:
        assert plain not in text, plain


# Person, all keys -------------------------------------------------------------------------------


def test_an_opt_out_writes_every_known_key_and_closes_the_lead_lost(
    world: World, published: list[Notification], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lead_id, contact_id = jane(world)

    result = world.suppress(contact_id)

    address, person, profile = result.written
    assert result == SuppressResult((address, person, profile), ())
    assert world.entries() == [
        entry(address, "address", ADDRESS),
        entry(person, "person", PERSON),
        entry(profile, "profile", PROFILE),
    ]
    assert world.entry_events() == [created(address), created(person), created(profile)]
    assert published == [
        SubjectSuppressed(
            suppression_entry_id=address,
            source="reply_rule",
            contact_id=contact_id,
            lead_id=lead_id,
        )
    ]
    assert world.stage(lead_id) == "lost"
    assert world.lead_events(lead_id)[-1] == (
        "new",
        "lost",
        "closed_lost",
        address,
        "system:pipeline",
        LATER_STAMP,
    )
    assert_no_personal_data(world.entry_events())
    assert_no_personal_data(world.lead_events(lead_id))
    assert_no_personal_data(published)
    assert [r for r in caplog.records if r.name.startswith(COMPLIANCE_LOGGER)] == []
    assert_no_personal_data([record.getMessage() for record in caplog.records])


def test_only_hashes_are_stored_so_an_erased_person_stays_matchable(world: World) -> None:
    _, contact_id = jane(world)
    world.suppress(contact_id)

    stored = repr(world.sql("SELECT * FROM suppression_entries")).lower()
    for plain in ("doe", "firm", "linkedin"):  # not "jane": "JANE" can occur in a ULID
        assert plain not in stored
    # an erasure (Story 3.13) tombstones the Contact; its keys still match when they reappear
    world.sql(
        "UPDATE contacts SET name = '[erased]', email = NULL, address_class = NULL, "
        "linkedin_url = NULL WHERE id = ?",
        (contact_id,),
    )
    reappearing: list[tuple[str, str | tuple[str, str]]] = [
        ("address", " JANE.DOE@firm.co.uk"),
        ("person", ("JANE  doe", "https://www.firm.co.uk")),
        ("profile", "linkedin.com/in/Jane-Doe?trk=x"),
    ]
    for kind, value in reappearing:
        value_hash = key_hash(kind, value)
        found = world.read(lambda: entries_repo.active_for(kind, value_hash))  # noqa: B023
        assert found is not None and found.status == "active", kind


# Partial keys -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "lead_keys", "kinds"),
    [
        ({"email": EMAIL}, {}, ["address"]),
        ({"name": "Jane Doe", "email": EMAIL}, {}, ["address", "person"]),
        (
            {"name": "Jane Doe", "email": EMAIL, "linkedin_url": LINKEDIN},
            {"web_domain": None, "ch_number": "OC123456"},
            ["address", "profile"],
        ),
        ({"name": "Jane Doe", "linkedin_url": LINKEDIN}, {}, ["person", "profile"]),
    ],
)
def test_only_the_known_keys_are_written(
    world: World, fields: dict[str, str], lead_keys: dict[str, Any], kinds: list[str]
) -> None:
    lead_id = world.lead(**lead_keys)
    contact_id = world.contact(lead_id, **fields)

    result = world.suppress(contact_id)

    assert [row["kind"] for row in world.entries()] == kinds
    assert [row["id"] for row in world.entries()] == list(result.written)
    assert len(world.entry_events()) == len(kinds)
    assert world.stage(lead_id) == "lost"


# Repeat -----------------------------------------------------------------------------------------


def test_a_repeat_writes_nothing_new_but_publishes_again(
    world: World, published: list[Notification]
) -> None:
    lead_id, contact_id = jane(world)
    first = world.suppress(contact_id)
    lead_events = world.lead_events(lead_id)
    before = (world.entries(), world.entry_events())

    second = world.suppress(contact_id, SuppressionSource.ONE_CLICK, cause_ref=None)

    assert second == SuppressResult((), first.written)
    assert (world.entries(), world.entry_events()) == before
    assert len(published) == 2
    assert published[1] == SubjectSuppressed(
        suppression_entry_id=first.written[0],
        source="one_click",
        contact_id=contact_id,
        lead_id=lead_id,
    )
    assert world.stage(lead_id) == "lost"
    assert world.lead_events(lead_id) == lead_events  # the closed Lead is a no-op


def test_a_new_key_beside_existing_ones_is_written_and_both_are_reported(
    world: World, published: list[Notification]
) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, email=EMAIL)
    first = world.suppress(contact_id, SuppressionSource.OPERATOR)
    world.sql("UPDATE contacts SET linkedin_url = ? WHERE id = ?", (LINKEDIN, contact_id))

    second = world.suppress(contact_id, SuppressionSource.MAILTO)

    [profile] = second.written
    assert second.existing == first.written
    assert published[-1].suppression_entry_id == profile  # type: ignore[attr-defined]
    assert [row["source"] for row in world.entries()] == ["operator", "mailto"]
    assert world.stage(lead_id) == "lost"


# Firm scope -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "domains"),
    [
        ("jane@firm.co.uk", ["firm.co.uk"]),
        ("jane@firm-mail.com", ["firm-mail.com", "firm.co.uk"]),
        ("jane@gmail.com", ["firm.co.uk"]),
        ("jane@comcast.net", ["firm.co.uk"]),  # a US ISP mailbox domain
        ("jane@nc.rr.com", ["firm.co.uk"]),  # a subdomain of one
    ],
)
def test_firm_scope_adds_the_domain_keys_but_never_free_webmail(
    world: World, email: str, domains: list[str]
) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name="Jane Doe", email=email)

    result = world.suppress(
        contact_id, SuppressionSource.OPERATOR, scope=SuppressionScope.FIRM
    )

    rows = world.entries()
    assert [row["kind"] for row in rows] == ["address", "person", *(["domain"] * len(domains))]
    assert [row["domain_plain"] for row in rows if row["kind"] == "domain"] == domains
    assert [row["value_hash"] for row in rows if row["kind"] == "domain"] == [
        key_hash("domain", domain) for domain in domains
    ]
    assert all(row["domain_plain"] is None for row in rows if row["kind"] != "domain")
    assert len(result.written) == len(rows) == len(world.entry_events())
    assert world.stage(lead_id) == "new"  # operator: not an opt-out


def test_a_free_webmail_web_domain_is_never_written(world: World) -> None:
    lead_id = world.lead(web_domain="gmail.com")
    contact_id = world.contact(lead_id, email="jane@gmail.com")

    world.suppress(contact_id, scope=SuppressionScope.FIRM)

    assert [row["kind"] for row in world.entries()] == ["address"]


# Non-opt-out sources ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source", [SuppressionSource.OPERATOR, SuppressionSource.IMPORT, SuppressionSource.DSR]
)
def test_a_non_opt_out_source_writes_and_publishes_but_leaves_the_stage(
    world: World, published: list[Notification], source: SuppressionSource
) -> None:
    lead_id, contact_id = jane(world)
    lead_events = world.lead_events(lead_id)

    result = world.suppress(contact_id, source)

    assert len(result.written) == 3
    assert [row["source"] for row in world.entries()] == [str(source)] * 3
    assert world.entry_events() == [created(entry_id, str(source)) for entry_id in result.written]
    assert [event[6] for event in world.entry_events()] == ["operator"] * 3
    assert len(published) == 1
    assert world.stage(lead_id) == "new"
    assert world.lead_events(lead_id) == lead_events


@pytest.mark.parametrize("source", list(OPT_OUT_SOURCES))
def test_every_opt_out_source_closes_the_lead_as_system_compliance(
    world: World, source: SuppressionSource
) -> None:
    lead_id, contact_id = jane(world)

    world.suppress(contact_id, source)

    assert world.stage(lead_id) == "lost"
    assert {event[6] for event in world.entry_events()} == {"system:compliance"}
    assert {event[4] for event in world.entry_events()} == {str(source)}


def test_the_actor_follows_the_source() -> None:
    assert dict(ACTORS) == {
        SuppressionSource.REPLY_RULE: "system:compliance",
        SuppressionSource.REPLY_CLASS: "system:compliance",
        SuppressionSource.ONE_CLICK: "system:compliance",
        SuppressionSource.MAILTO: "system:compliance",
        SuppressionSource.OPERATOR: "operator",
        SuppressionSource.IMPORT: "operator",
        SuppressionSource.DSR: "operator",
    }


# Closed Lead ------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["disqualified", "lost"])
def test_an_opt_out_on_a_closed_lead_writes_the_rows_and_keeps_the_stage(
    world: World, stage: str
) -> None:
    lead_id, contact_id = jane(world)
    world.set_stage(lead_id, stage)
    lead_events = world.lead_events(lead_id)

    result = world.suppress(contact_id, SuppressionSource.ONE_CLICK)

    assert len(result.written) == 3
    assert world.stage(lead_id) == stage
    assert world.lead_events(lead_id) == lead_events


@pytest.mark.parametrize(
    "stage", ["queued", "contacted", "replied", "interested", "won", "not_now"]
)
def test_an_opt_out_closes_any_open_or_parked_lead(world: World, stage: str) -> None:
    lead_id, contact_id = jane(world)
    world.set_stage(lead_id, stage)

    world.suppress(contact_id, SuppressionSource.REPLY_CLASS)

    assert world.stage(lead_id) == "lost"
    assert world.lead_events(lead_id)[-1][:3] == (stage, "lost", "closed_lost")


# No keys ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("scope", list(SuppressionScope))
def test_a_name_only_contact_on_a_domainless_lead_is_an_empty_no_op(
    world: World, published: list[Notification], scope: SuppressionScope
) -> None:
    lead_id = world.lead(web_domain=None, ch_number="OC123456")
    contact_id = world.contact(lead_id, name="Jane Doe")
    before = world.dump()

    result = world.suppress(contact_id, scope=scope)

    assert result == SuppressResult((), ())
    assert world.dump() == before
    assert published == []
    assert world.stage(lead_id) == "new"


def test_stored_values_that_cannot_be_normalised_are_not_known_keys(
    world: World, published: list[Notification]
) -> None:
    lead_id, contact_id = jane(world)
    # values a later story or a restore could leave behind; none of them is a key
    world.sql(
        "UPDATE contacts SET name = '!!!', email = 'not an address', "
        "linkedin_url = 'https://example.com/in/jane' WHERE id = ?",
        (contact_id,),
    )
    before = world.dump()

    assert world.suppress(contact_id) == SuppressResult((), ())
    assert world.dump() == before
    assert published == []

    result = world.suppress(contact_id, scope=SuppressionScope.FIRM)

    [domain] = result.written
    assert [(row["kind"], row["domain_plain"]) for row in world.entries()] == [
        ("domain", "firm.co.uk")
    ]
    assert world.stage(lead_id) == "lost"
    assert published[-1].suppression_entry_id == domain  # type: ignore[attr-defined]


# Failing subscriber -----------------------------------------------------------------------------


@command
def failing_subscriber(n: SubjectSuppressed) -> None:
    raise RuntimeError("a later subscriber failed")


def test_a_failing_subscriber_rolls_back_the_entries_the_events_and_the_stage(
    world: World,
) -> None:
    lead_id, contact_id = jane(world)
    subscribers = Subscribers()
    subscribers.register_subscriber(SubjectSuppressed, Pipeline(world.clock).close_suppressed)
    subscribers.register_subscriber(SubjectSuppressed, failing_subscriber)
    subscribers.freeze()
    units = UnitOfWorkFactory(world.engine, subscribers)
    before = world.dump()
    subject = ContactSubject(contact_id)

    with pytest.raises(RuntimeError, match="a later subscriber failed"):
        world.transact(
            lambda: world.compliance.suppress(
                subject, SuppressionScope.FIRM, SuppressionSource.REPLY_RULE, CAUSE_REF
            ),
            units,
        )

    assert world.dump() == before
    assert world.entries() == []
    assert world.stage(lead_id) == "new"


# Bad arguments ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "scope", "source", "cause_ref", "error"),
    [
        ("contact", SuppressionScope.PERSON, SuppressionSource.OPERATOR, None, TypeError),
        (None, SuppressionScope.PERSON, SuppressionSource.OPERATOR, None, TypeError),
        ("SUBJECT", "person", SuppressionSource.OPERATOR, None, TypeError),
        ("SUBJECT", None, SuppressionSource.OPERATOR, None, TypeError),
        ("SUBJECT", SuppressionScope.PERSON, "reply_rule", None, TypeError),
        ("SUBJECT", SuppressionScope.PERSON, SuppressionScope.FIRM, None, TypeError),
        ("SUBJECT", SuppressionScope.PERSON, SuppressionSource.OPERATOR, "x", ValueError),
        ("SUBJECT", SuppressionScope.PERSON, SuppressionSource.OPERATOR, 7, ValueError),
        (
            "SUBJECT",
            SuppressionScope.PERSON,
            SuppressionSource.OPERATOR,
            CAUSE_REF.lower(),
            ValueError,
        ),
        ("UNKNOWN", SuppressionScope.PERSON, SuppressionSource.OPERATOR, None, LookupError),
    ],
)
def test_bad_arguments_raise_and_write_nothing(
    world: World,
    published: list[Notification],
    subject: object,
    scope: object,
    source: object,
    cause_ref: object,
    error: type[Exception],
) -> None:
    _, contact_id = jane(world)
    if subject == "SUBJECT":
        subject = ContactSubject(contact_id)
    elif subject == "UNKNOWN":
        subject = ContactSubject(CAUSE_REF)
    before = world.dump()

    with pytest.raises(error) as caught:
        world.transact(
            lambda: world.compliance.suppress(
                subject,  # type: ignore[arg-type]
                scope,  # type: ignore[arg-type]
                source,  # type: ignore[arg-type]
                cause_ref,  # type: ignore[arg-type]
            )
        )

    assert world.dump() == before
    assert published == []
    assert contact_id not in str(caught.value)
    assert CAUSE_REF not in str(caught.value)


def test_an_unknown_contact_says_so_without_its_id(world: World) -> None:
    with pytest.raises(LookupError) as caught:
        world.suppress(CAUSE_REF)

    assert str(caught.value) == "suppress: the Contact is unknown"


# The repository and the query -------------------------------------------------------------------


def test_the_readers_find_an_entry_and_only_an_active_key(world: World) -> None:
    _, contact_id = jane(world)
    [address, *_] = world.suppress(contact_id).written

    stored = world.read(lambda: entries_repo.get(address))
    assert stored is not None
    assert (stored.id, stored.kind, stored.value_hash, stored.status) == (
        address,
        "address",
        ADDRESS,
        "active",
    )
    assert stored.created_at == LATER
    assert world.read(lambda: entries_repo.get(CAUSE_REF)) is None
    assert world.read(lambda: entries_repo.active_for("address", ADDRESS)) == stored
    assert world.read(lambda: entries_repo.active_for("domain", ADDRESS)) is None
    # a voided entry (Story 3.5 writes one) is no longer the key's active entry
    world.sql(
        "UPDATE suppression_entries SET status = 'voided', voided_at = ?, void_reason = 'x' "
        "WHERE id = ?",
        (LATER_STAMP, address),
    )
    assert world.read(lambda: entries_repo.active_for("address", ADDRESS)) is None


def test_queries_research_contact_reads_one_contact(world: World) -> None:
    lead_id, contact_id = jane(world)

    found = world.read(lambda: research_queries.contact(contact_id))

    assert found is not None
    assert (found.id, found.lead_id, found.email) == (contact_id, lead_id, EMAIL)
    assert world.read(lambda: research_queries.contact(CAUSE_REF)) is None
    assert world.read(lambda: research_queries.contact("not-a-ulid")) is None
    assert world.read(lambda: research_queries.contact(contact_id.lower())) is None


# Raw SQL: the triggers and the checks -----------------------------------------------------------

PERMANENT = "suppression entries are permanent"
VOIDED_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AC"


def seeded(world: World) -> tuple[str, list[str]]:
    """Jane suppressed (three ``reply_rule`` entries) plus one voided ``reply_rule`` entry (only
    that source can be voided); the address entry's id and the ids of all four."""
    _, contact_id = jane(world)
    written = list(world.suppress(contact_id).written)
    world.sql(
        f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) VALUES "
        "(?, 'address', ?, NULL, 'active', 'reply_rule', NULL, ?, NULL, NULL)",
        (VOIDED_ID, "b" * 64, LATER_STAMP),
    )
    world.sql(
        "UPDATE suppression_entries SET status = 'voided', voided_at = ?, void_reason = 'x' "
        "WHERE id = ?",
        (LATER_STAMP, VOIDED_ID),
    )
    return written[0], [*written, VOIDED_ID]


REFUSED = [
    "DELETE FROM suppression_entries",
    "DELETE FROM suppression_entries WHERE id = '{address}'",
    "DELETE FROM suppression_entries WHERE status = 'voided'",
    # INSERT OR REPLACE of an existing id, and of an existing active key under a new id
    f"INSERT OR REPLACE INTO suppression_entries ({ENTRY_COLUMNS}) SELECT id, kind, value_hash, "
    "domain_plain, status, 'dsr', cause_ref, created_at, voided_at, void_reason "
    "FROM suppression_entries WHERE id = '{address}'",
    f"INSERT OR REPLACE INTO suppression_entries ({ENTRY_COLUMNS}) SELECT "
    "'01JD3QZ8X5V9K2M7N4P6R8T0AD', kind, value_hash, domain_plain, status, 'dsr', cause_ref, "
    "created_at, voided_at, void_reason FROM suppression_entries WHERE id = '{address}'",
    f"REPLACE INTO suppression_entries ({ENTRY_COLUMNS}) SELECT "
    "'01JD3QZ8X5V9K2M7N4P6R8T0AD', kind, value_hash, domain_plain, status, 'dsr', cause_ref, "
    "created_at, voided_at, void_reason FROM suppression_entries WHERE id = '{address}'",
    # a reused id, even for a voided copy
    f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) SELECT id, kind, '" + "c" * 64 + "', "
    "domain_plain, status, source, cause_ref, created_at, voided_at, void_reason "
    "FROM suppression_entries WHERE id = '{address}'",
    # inserted voided
    f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) VALUES ('01JD3QZ8X5V9K2M7N4P6R8T0AE', "
    "'address', '" + "d" * 64 + "', NULL, 'voided', 'operator', NULL, "
    f"'{LATER_STAMP}', '{LATER_STAMP}', 'x')",
    # an UPDATE of a fixed column, or out of voided
    "UPDATE suppression_entries SET value_hash = '" + "e" * 64 + "' WHERE id = '{address}'",
    "UPDATE suppression_entries SET status = 'active', voided_at = NULL, void_reason = NULL "
    f"WHERE id = '{VOIDED_ID}'",
    f"UPDATE suppression_entries SET voided_at = '{STAMP}' WHERE id = '{VOIDED_ID}'",
]


@pytest.mark.parametrize("statement", REFUSED)
def test_the_triggers_refuse_a_delete_a_replace_a_voided_insert_and_a_fixed_change(
    world: World, statement: str
) -> None:
    address, _ = seeded(world)
    before = world.dump()

    with pytest.raises(sqlite3.IntegrityError, match=PERMANENT):
        run_sql(world.db_file, statement.replace("{address}", address))

    assert world.dump() == before


DOMAIN_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AG"
# For each probed row: a value per fixed column that differs from the row's own and passes the
# column's checks alone, so only the immutability trigger can refuse it.
FIXED_CHANGES = {
    "active": {
        "id": "'01JD3QZ8X5V9K2M7N4P6R8T0AF'",
        "kind": "'person'",
        "value_hash": "'" + "f" * 64 + "'",
        "domain_plain": "NULL",
        "source": "'dsr'",
        "cause_ref": "NULL",
        "created_at": "'2026-01-01T00:00:00.000000Z'",
    },
    "voided": {
        "id": "'01JD3QZ8X5V9K2M7N4P6R8T0AF'",
        "kind": "'profile'",
        "value_hash": "'" + "f" * 64 + "'",
        "domain_plain": "'firm.co.uk'",
        "source": "'dsr'",
        "cause_ref": f"'{CAUSE_REF}'",
        "created_at": "'2026-01-01T00:00:00.000000Z'",
    },
}


@pytest.mark.parametrize("which", ["active", "voided"])
def test_the_immutability_trigger_refuses_a_change_to_every_fixed_column(
    world: World, which: str
) -> None:
    seeded(world)
    run_sql(
        world.db_file,
        f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) VALUES ('{DOMAIN_ID}', 'domain', '"
        + "9" * 64
        + f"', 'firm.co.uk', 'active', 'import', '{CAUSE_REF}', '{STAMP}', NULL, NULL)",
    )
    target = {"active": DOMAIN_ID, "voided": VOIDED_ID}[which]
    before = world.dump()

    not_refused = []
    for column, value in FIXED_CHANGES[which].items():
        try:
            run_sql(
                world.db_file,
                f"UPDATE suppression_entries SET {column} = {value} WHERE id = '{target}'",
            )
        except sqlite3.IntegrityError as error:
            if PERMANENT not in str(error):
                not_refused.append(f"{column}: {error}")
        else:
            not_refused.append(f"{column}: changed")

    assert not_refused == []
    assert world.dump() == before
    assert set(FIXED_CHANGES[which]) == {
        "id",
        "kind",
        "value_hash",
        "domain_plain",
        "source",
        "cause_ref",
        "created_at",
    }


def test_an_active_entry_can_be_voided_once_and_its_reason_redacted(world: World) -> None:
    address, _ = seeded(world)

    run_sql(
        world.db_file,
        f"UPDATE suppression_entries SET status = 'voided', voided_at = '{LATER_STAMP}', "
        f"void_reason = 'reason' WHERE id = '{address}'",
    )
    run_sql(
        world.db_file,
        f"UPDATE suppression_entries SET void_reason = '[erased]' WHERE id = '{address}'",
    )

    [row] = [row for row in world.entries() if row["id"] == address]
    assert (row["status"], row["voided_at"], row["void_reason"]) == (
        "voided",
        LATER_STAMP,
        "[erased]",
    )
    with pytest.raises(sqlite3.IntegrityError, match=PERMANENT):
        run_sql(
            world.db_file,
            f"UPDATE suppression_entries SET status = 'active' WHERE id = '{address}'",
        )


def test_a_voided_key_can_be_suppressed_again(world: World) -> None:
    address, _ = seeded(world)
    run_sql(
        world.db_file,
        f"UPDATE suppression_entries SET status = 'voided', voided_at = '{LATER_STAMP}', "
        f"void_reason = 'reason' WHERE id = '{address}'",
    )
    contact_id = world.sql("SELECT id FROM contacts")[0][0]

    result = world.suppress(contact_id, SuppressionSource.MAILTO)

    [again] = result.written
    assert len(result.existing) == 2
    found = world.read(lambda: entries_repo.active_for("address", ADDRESS))
    assert found is not None and found.id == again


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ("'email', '{h}', NULL, 'active', 'operator'", "ck_suppression_entries_kind"),
        ("'address', 'ABC', NULL, 'active', 'operator'", "ck_suppression_entries_value_hash"),
        ("'address', '{H}', NULL, 'active', 'operator'", "ck_suppression_entries_value_hash"),
        (
            "'address', '{h}', 'firm.co.uk', 'active', 'operator'",
            "ck_suppression_entries_domain_plain",
        ),
        ("'domain', '{h}', NULL, 'active', 'operator'", "ck_suppression_entries_domain_plain"),
        ("'address', '{h}', NULL, 'active', 'retention'", "ck_suppression_entries_source"),
        ("'address', '{h}', NULL, 'expired', 'operator'", PERMANENT),
    ],
)
def test_the_checks_refuse_a_bad_row(world: World, values: str, constraint: str) -> None:
    before = world.dump()
    filled = values.replace("{h}", "a" * 64).replace("{H}", "A" * 64)

    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        run_sql(
            world.db_file,
            f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) VALUES "
            f"('01JD3QZ8X5V9K2M7N4P6R8T0AH', {filled}, NULL, '{STAMP}', NULL, NULL)",
        )

    assert world.dump() == before


def test_a_void_needs_both_its_time_and_its_reason(world: World) -> None:
    address, _ = seeded(world)

    for assignments in (
        f"status = 'voided', voided_at = '{LATER_STAMP}'",
        "status = 'voided', void_reason = 'x'",
        f"voided_at = '{LATER_STAMP}'",
        "void_reason = 'x'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="ck_suppression_entries_voided"):
            run_sql(
                world.db_file,
                f"UPDATE suppression_entries SET {assignments} WHERE id = '{address}'",
            )


DUPLICATE_ACTIVE_KEY = (
    f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) SELECT "
    "'01JD3QZ8X5V9K2M7N4P6R8T0AJ', kind, value_hash, domain_plain, status, source, "
    "cause_ref, created_at, voided_at, void_reason FROM suppression_entries "
    "WHERE id = '{address}'"
)


def test_a_plain_duplicate_active_key_is_refused(world: World) -> None:
    address, _ = seeded(world)

    with pytest.raises(sqlite3.IntegrityError, match=PERMANENT):  # the trigger fires first
        run_sql(world.db_file, DUPLICATE_ACTIVE_KEY.replace("{address}", address))


def test_the_partial_unique_index_refuses_a_second_active_row_without_the_trigger(
    world: World,
) -> None:
    address, _ = seeded(world)
    run_sql(world.db_file, "DROP TRIGGER trg_suppression_entries_no_id_reuse")  # this temp DB only

    with pytest.raises(
        sqlite3.IntegrityError,
        match=(
            r"UNIQUE constraint failed: "
            r"suppression_entries\.kind, suppression_entries\.value_hash"
        ),
    ):
        run_sql(world.db_file, DUPLICATE_ACTIVE_KEY.replace("{address}", address))


def test_the_status_check_refuses_an_unknown_status(world: World) -> None:
    address, _ = seeded(world)
    before = world.dump()

    with pytest.raises(sqlite3.IntegrityError, match="ck_suppression_entries_status"):
        run_sql(
            world.db_file,
            f"UPDATE suppression_entries SET status = 'expired' WHERE id = '{address}'",
        )

    assert world.dump() == before


@pytest.mark.parametrize(
    "source", ["reply_class", "one_click", "mailto", "operator", "import", "dsr"]
)
def test_only_a_reply_rule_entry_can_be_voided(world: World, source: str) -> None:
    entry_id = "01JD3QZ8X5V9K2M7N4P6R8T0AK"
    run_sql(
        world.db_file,
        f"INSERT INTO suppression_entries ({ENTRY_COLUMNS}) VALUES ('{entry_id}', 'address', '"
        + "7" * 64
        + f"', NULL, 'active', '{source}', NULL, '{STAMP}', NULL, NULL)",
    )
    before = world.dump()

    with pytest.raises(sqlite3.IntegrityError, match=PERMANENT):
        run_sql(
            world.db_file,
            f"UPDATE suppression_entries SET status = 'voided', voided_at = '{LATER_STAMP}', "
            f"void_reason = 'reason' WHERE id = '{entry_id}'",
        )

    assert world.dump() == before
