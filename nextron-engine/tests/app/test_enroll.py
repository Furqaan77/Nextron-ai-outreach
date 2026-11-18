"""``Sequencing.enroll`` (FR-3, FR-63, AD-4, AD-16, AD-21; Story 4.8) on a temporary database at
head with ``FrozenClock``, the real subscriber registry, the shipped seeds (Law-PrivateAI x US
active) and registered Sending Inboxes, through real units of work.

- **Happy path.** Variants A then B, the inbox with the fewest live Enrollments (then the
  earliest registered), the bound columns, the ``events`` row, the ``eligibility_basis``
  Provenance Record, the counter row, ``Enrolled`` moving the Lead ``new`` -> ``queued``, and
  exactly one row more in each of those tables and none in ``jobs``.
- **Round-robin.** 100 enrollments (each stopped before the next) split 50/50; an inactive
  Variant is skipped.
- **Refusals**, each in its turn and each leaving every table's row count unchanged:
  ``blocked`` (a suppressed address, an erased Contact), ``campaign_not_active``,
  ``missing_tz_or_market``, ``not_eligible`` (FR-63: a Sequence completed without a Reply 30
  days ago), ``campaign_mismatch``, ``lead_already_replied``, ``lead_already_enrolled`` (and
  the partial unique index refusing a raw duplicate), ``no_active_variant`` and
  ``no_sending_inbox``. The argument, lookup and Sequence errors raise before any write.
- **UK.** A GB basis records the Campaign's LIA (a stubbed eligible Decision: no UK Contact can
  be eligible until the legal form is read, Story 2.12).
- **History.** ``queries.sequencing.ended_sequences`` holds only ended Enrollments that sent a
  Step; a Reply stop is ``replied`` and doesn't block a new enroll.
- **The table.** The bound-column and erased-insert triggers, and the repository's lack of a
  setter for a bound column.
- **Advance.** ``Enrolled`` leaves a Lead past ``new`` alone; ``advance_to_at_least`` refuses a
  target with no automatic move and an unknown Lead.
"""

import inspect
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.app.sending.api import Sending
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup.api import Campaigns, Setup, check_seed
from nextron.db.repos.sequencing import enrollments as enrollments_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.campaign import VariantStatus, validate_campaigns
from nextron.domain.contactability import BlockReason
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import (
    LEGAL_FORM_LABELS,
    UK_BASIS,
    US_BASIS,
    Decision,
    EndedSequence,
    ReasonKind,
)
from nextron.domain.enrollment import (
    EnrollmentOrigin,
    EnrollRefusal,
    NewEnrollment,
    RefusalCode,
    StopCause,
)
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
US_CAMPAIGN, UK_CAMPAIGN, OPS_US = "law_private_ai_us", "law_private_ai_uk", "ops_automation_us"
FIRST_INBOX, SECOND_INBOX = "furqaan@nextron-ai.com", "mac@nextron-mail.com"
NAME = "Zelda Quill"
UK_DECISION = Decision(True, UK_BASIS.format(label=LEGAL_FORM_LABELS["ltd"]), ())
LIA_REF, LIA_DATE = "LIA-R1", date(2026, 10, 1)
SELECTION = EnrollmentOrigin.SELECTION
ENROLLMENT_COLUMNS = (
    "lead_id, contact_id, campaign_id, variant_id, sending_inbox_id, sequence_id, "
    "sequence_version, offer_id, origin, status, pause_cause, stop_cause, created_at, "
    "updated_at, last_step_sent_at, ended_at"
)


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def shipped[T](name: str, validate: Callable[[object], T]) -> T:
    return validate(seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8")))


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
    )
    return JobQueue(clock, job_types)


@dataclass
class World:
    """The real services on real units of work, with the shipped seeds."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    discovery: Discovery
    research: Research
    sequencing: Sequencing
    segment: str = ""
    tags: int = 0

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
        tables = [
            name
            for (name,) in self.sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {table: self.sql(f'SELECT COUNT(*) FROM "{table}"')[0][0] for table in tables}

    def campaign(self, key: str) -> str:
        [found] = [c.id for c in self.read(setup_queries.campaigns) if c.key == key]
        return found

    def variants(self, key: str) -> dict[str, str]:
        [found] = [c for c in self.read(setup_queries.campaigns) if c.key == key]
        return {variant.name: variant.id for variant in found.variants}

    def activate(self, key: str, lia: tuple[str, str] | None = None) -> str:
        campaign_id = self.campaign(key)
        with self.units.open():
            if lia is None:
                Campaigns(self.clock).activate(campaign_id)
            else:
                Campaigns(self.clock).activate(campaign_id, *lia)
        return campaign_id

    def add_variant(self, key: str, name: str) -> None:
        campaign_id = self.campaign(key)
        with self.units.open():
            Campaigns(self.clock).add_variant(campaign_id, name)

    def inbox(self, address: str) -> str:
        with self.units.open():
            return Sending(self.clock, env="prod").register_inbox(address, "Main").inbox_id

    def contact(
        self, country: str = "US", web: str | None = None, segment: str | None = None
    ) -> tuple[str, str]:
        """A new Lead and its Contact from the website (a Published address); their ids."""
        self.tags += 1
        tld = "com" if country == "US" else "co.uk"
        domain = web or f"firm{self.tags}.{tld}"
        identity = LeadIdentity(
            f"Firm {self.tags}", segment or self.segment, web_domain=domain, country_code=country
        )
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV))
        lead_id = upserted.lead_id
        assert lead_id is not None
        candidate = ContactCandidate(name=NAME, email=f"zelda@{domain}")
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id,
                candidate,
                source=ProvenanceSource.WEBSITE,
                detail="Firm website team page",
                retrieved_at=T0,
            )
        )
        assert added.contact_id is not None
        return lead_id, added.contact_id

    def enroll(
        self,
        contact_id: str,
        campaign_id: str,
        origin: object = SELECTION,
        steps: object = (1,),
    ) -> NewEnrollment | EnrollRefusal:
        return self.transact(
            lambda: self.sequencing.enroll(contact_id, campaign_id, origin, steps)  # type: ignore[arg-type]
        )

    def stop(self, enrollment_id: str, cause: StopCause = StopCause.OPERATOR_STOP) -> None:
        self.transact(lambda: self.sequencing.stop(cause=cause, enrollment_id=enrollment_id))

    def stage(self, lead_id: str) -> str:
        return str(self.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (lead_id,))[0][0])

    def row(self, enrollment_id: str) -> tuple[Any, ...]:
        return self.sql(
            f"SELECT {ENROLLMENT_COLUMNS} FROM enrollments WHERE id = ?", (enrollment_id,)
        )[0]

    def events(self, entity: str, entity_id: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT from_status, to_status, cause, cause_ref, actor, at FROM events "
            "WHERE entity = ? AND entity_id = ? ORDER BY rowid",
            (entity, entity_id),
        )

    def bases(self, contact_id: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT basis_rule, basis_campaign_id, lia_ref, lia_date, basis_recorded_at "
            "FROM provenance_records WHERE contact_id = ? AND source_kind = 'eligibility_basis' "
            "ORDER BY rowid",
            (contact_id,),
        )


def seed_all(world: World) -> None:
    checked = check_seed(
        shipped(main.SEGMENTS_FILE_NAME, validate_segments),
        shipped(main.MARKETS_FILE_NAME, validate_markets),
        shipped(main.SEQUENCES_FILE_NAME, validate_sequences),
        shipped(main.OFFERS_FILE_NAME, validate_offers),
        shipped(main.CAMPAIGNS_FILE_NAME, validate_campaigns),
    )
    world.transact(lambda: Setup(world.clock).seed_sync(checked, world.clock.now()))


def build_world(engine: Engine, *, inboxes: tuple[str, ...] = (FIRST_INBOX,)) -> World:
    """The world at ``T0``: seeds, the Sender's name, ``inboxes`` registered (oldest first, one
    second apart), Law-PrivateAI x US active; then the clock at ``LATER``."""
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(engine, main.build_subscribers(clock=clock))
    world = World(
        units,
        engine,
        clock,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        Sequencing(clock),
    )
    seed_all(world)
    [(segment,)] = world.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
    world.segment = segment
    with units.open():
        Setup(clock).save({"sender_name": "Furqaan Ahmed"})
    for number, address in enumerate(inboxes):
        clock.set(T0 + timedelta(seconds=number))
        world.inbox(address)
    world.activate(US_CAMPAIGN)
    clock.set(LATER)
    return world


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


@pytest.fixture
def two_inboxes(db_engine: Engine) -> World:
    return build_world(db_engine, inboxes=(FIRST_INBOX, SECOND_INBOX))


def inbox_ids(world: World) -> list[str]:
    return [row[0] for row in world.sql("SELECT id FROM sending_inboxes ORDER BY created_at, id")]


def gained(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {table: after[table] - before[table] for table in after if after[table] != before[table]}


# Happy path ---------------------------------------------------------------------------------------


def test_an_enroll_binds_variant_a_then_b_and_the_least_loaded_inbox(two_inboxes: World) -> None:
    world = two_inboxes
    world.add_variant(US_CAMPAIGN, "B")
    campaign_id = world.campaign(US_CAMPAIGN)
    variants = world.variants(US_CAMPAIGN)
    first_inbox, second_inbox = inbox_ids(world)
    [(sequence_id, version)] = world.sql("SELECT id, version FROM sequences WHERE key = 'default'")
    [(offer_id,)] = world.sql("SELECT id FROM offers WHERE key = 'default'")
    lead_a, contact_a = world.contact()
    lead_b, contact_b = world.contact()
    before = world.counts()

    first = world.enroll(contact_a, campaign_id)

    assert isinstance(first, NewEnrollment)
    assert first == NewEnrollment(
        first.enrollment_id, lead_a, contact_a, campaign_id, variants["A"], first_inbox
    )
    assert gained(before, world.counts()) == {
        "drafts": 1,  # Story 4.9: the Step 1 slot
        "enrollments": 1,
        "events": 3,  # the Enrollment's and the slot's creation, and the Lead's move to queued
        "provenance_records": 1,
        "variant_counters": 1,
    }
    assert world.row(first.enrollment_id) == (
        lead_a,
        contact_a,
        campaign_id,
        variants["A"],
        first_inbox,
        sequence_id,
        version,
        offer_id,
        "selection",
        "active",
        None,
        None,
        stamp(LATER),
        stamp(LATER),
        None,
        None,
    )
    assert world.events("enrollments", first.enrollment_id) == [
        (None, "active", "enrolled", campaign_id, "system:sequencing", stamp(LATER))
    ]
    assert world.bases(contact_a) == [(US_BASIS, campaign_id, None, None, stamp(LATER))]
    assert world.stage(lead_a) == "queued"
    assert world.events("leads", lead_a)[-1] == (
        "new",
        "queued",
        "enrolled",
        first.enrollment_id,
        "system:pipeline",
        stamp(LATER),
    )
    assert world.sql("SELECT campaign_id, last_variant_id FROM variant_counters") == [
        (campaign_id, variants["A"])
    ]

    second = world.enroll(contact_b, campaign_id)

    assert isinstance(second, NewEnrollment)
    assert (second.variant_id, second.sending_inbox_id) == (variants["B"], second_inbox)
    assert world.sql("SELECT campaign_id, last_variant_id FROM variant_counters") == [
        (campaign_id, variants["B"])
    ]
    assert world.stage(lead_b) == "queued"


def test_ties_go_to_the_earliest_registered_inbox(two_inboxes: World) -> None:
    world = two_inboxes
    campaign_id = world.campaign(US_CAMPAIGN)
    first_inbox, second_inbox = inbox_ids(world)
    _, contact_a = world.contact()
    _, contact_b = world.contact()
    _, contact_c = world.contact()

    found = [world.enroll(contact, campaign_id) for contact in (contact_a, contact_b)]
    assert [e.sending_inbox_id for e in found if isinstance(e, NewEnrollment)] == [
        first_inbox,
        second_inbox,
    ]
    world.stop(found[0].enrollment_id)  # type: ignore[union-attr]
    third = world.enroll(contact_c, campaign_id)  # the first now has no live Enrollment
    assert isinstance(third, NewEnrollment)
    assert third.sending_inbox_id == first_inbox


def test_an_operator_pick_is_recorded_with_the_operator_actor(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()

    found = world.enroll(contact_id, campaign_id, EnrollmentOrigin.OPERATOR_PICK, [1, 2])

    assert isinstance(found, NewEnrollment)
    assert world.events("enrollments", found.enrollment_id)[0][4] == "operator"
    assert world.row(found.enrollment_id)[8] == "operator_pick"


# Round-robin -------------------------------------------------------------------------------------


def test_one_hundred_enrollments_split_fifty_fifty(world: World) -> None:
    world.add_variant(US_CAMPAIGN, "B")
    campaign_id = world.campaign(US_CAMPAIGN)
    variants = world.variants(US_CAMPAIGN)
    _, contact_id = world.contact()

    picks = []
    for _ in range(100):
        found = world.enroll(contact_id, campaign_id)
        assert isinstance(found, NewEnrollment)
        picks.append(found.variant_id)
        world.stop(found.enrollment_id)

    assert picks.count(variants["A"]) == picks.count(variants["B"]) == 50
    assert picks[:2] == [variants["A"], variants["B"]]


def test_an_inactive_variant_is_never_assigned(world: World) -> None:
    world.add_variant(US_CAMPAIGN, "B")
    campaign_id = world.campaign(US_CAMPAIGN)
    variants = world.variants(US_CAMPAIGN)
    world.sql("UPDATE variants SET status = 'inactive' WHERE id = ?", (variants["B"],))
    _, contact_id = world.contact()

    picks = []
    for _ in range(4):
        found = world.enroll(contact_id, campaign_id)
        assert isinstance(found, NewEnrollment)
        picks.append(found.variant_id)
        world.stop(found.enrollment_id)

    assert picks == [variants["A"]] * 4


# Refusals ------------------------------------------------------------------------------------------


def refused(world: World, contact_id: str, campaign_id: str) -> EnrollRefusal:
    before = world.counts()
    found = world.enroll(contact_id, campaign_id)
    assert isinstance(found, EnrollRefusal)
    assert world.counts() == before  # nothing written: no Enrollment, counter, event or job
    return found


def test_a_suppressed_address_is_blocked(world: World) -> None:
    _, contact_id = world.contact(web="acmelaw.com")
    with world.units.open():
        Compliance(world.clock).add_value("zelda@acmelaw.com")

    found = refused(world, contact_id, world.campaign(US_CAMPAIGN))

    assert found.code is RefusalCode.BLOCKED
    assert BlockReason.ADDRESS_SUPPRESSED in found.blocks
    assert found.reasons == ()


def test_an_erased_contact_is_blocked(world: World) -> None:
    _, contact_id = world.contact()
    erasures = Erasures(world.clock, Compliance(world.clock))
    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    found = refused(world, contact_id, world.campaign(US_CAMPAIGN))

    assert found.code is RefusalCode.BLOCKED
    assert BlockReason.CONTACT_ERASED in found.blocks


def test_a_draft_campaign_is_not_active(world: World) -> None:
    _, contact_id = world.contact()

    found = refused(world, contact_id, world.campaign(OPS_US))  # never activated

    assert found == EnrollRefusal(RefusalCode.CAMPAIGN_NOT_ACTIVE)


def test_a_contact_without_a_timezone_and_market_is_refused(world: World) -> None:
    _, contact_id = world.contact()
    world.sql(  # the CHECKs keep the Market and timezone, and their bases, set together
        "UPDATE contacts SET tz = NULL, market_id = NULL, market_basis = NULL, tz_basis = NULL "
        "WHERE id = ?",
        (contact_id,),
    )

    found = refused(world, contact_id, world.campaign(US_CAMPAIGN))

    assert found == EnrollRefusal(RefusalCode.MISSING_TZ_OR_MARKET)


def test_fr63_a_sequence_completed_without_a_reply_30_days_ago_is_not_eligible(
    world: World,
) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    first = world.enroll(contact_id, campaign_id)
    assert isinstance(first, NewEnrollment)
    sent = stamp(LATER - timedelta(days=30))
    world.sql(
        "UPDATE enrollments SET status = 'completed', last_step_sent_at = ?, ended_at = ? "
        "WHERE id = ?",
        (sent, sent, first.enrollment_id),
    )

    found = refused(world, contact_id, campaign_id)

    assert found.code is RefusalCode.NOT_ELIGIBLE
    assert [reason.kind for reason in found.reasons] == [ReasonKind.RECONTACT_NOT_ALLOWED]
    assert found.blocks == ()


def test_a_sequence_that_ended_with_a_reply_is_history_but_allows_a_new_enroll(
    world: World,
) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    first = world.enroll(contact_id, campaign_id)
    assert isinstance(first, NewEnrollment)
    sent = LATER - timedelta(days=30)
    world.sql(
        "UPDATE enrollments SET status = 'stopped', stop_cause = 'reply', "
        "last_step_sent_at = ?, ended_at = ? WHERE id = ?",
        (stamp(sent), stamp(sent + timedelta(days=1)), first.enrollment_id),
    )

    assert world.read(lambda: sequencing_queries.ended_sequences(contact_id)) == (
        EndedSequence(sent, sent + timedelta(days=1), True),
    )
    assert isinstance(world.enroll(contact_id, campaign_id), NewEnrollment)


def test_an_ended_enrollment_without_a_sent_step_is_no_history(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    first = world.enroll(contact_id, campaign_id)
    assert isinstance(first, NewEnrollment)
    world.stop(first.enrollment_id)

    assert world.read(lambda: sequencing_queries.ended_sequences(contact_id)) == ()
    assert world.read(lambda: sequencing_queries.ended_sequences("not-a-ulid")) == ()


def test_another_segment_or_market_is_a_mismatch(world: World) -> None:
    ops = world.activate(OPS_US)  # Ops-Automation x US: the Contact's Lead is a Law Lead
    _, contact_id = world.contact()

    assert refused(world, contact_id, ops) == EnrollRefusal(RefusalCode.CAMPAIGN_MISMATCH)

    uk = world.activate(UK_CAMPAIGN, (LIA_REF, LIA_DATE.isoformat()))  # a US Contact
    assert refused(world, contact_id, uk) == EnrollRefusal(RefusalCode.CAMPAIGN_MISMATCH)


@pytest.mark.parametrize("stage", ["replied", "interested", "call_booked", "won"])
def test_a_lead_past_contacted_has_already_replied(world: World, stage: str) -> None:
    lead_id, contact_id = world.contact()
    world.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead_id))

    found = refused(world, contact_id, world.campaign(US_CAMPAIGN))

    assert found == EnrollRefusal(RefusalCode.LEAD_ALREADY_REPLIED)


def test_a_second_enroll_on_the_lead_is_refused_and_the_index_refuses_a_raw_duplicate(
    world: World,
) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    first = world.enroll(contact_id, campaign_id)
    assert isinstance(first, NewEnrollment)

    assert refused(world, contact_id, campaign_id) == EnrollRefusal(
        RefusalCode.LEAD_ALREADY_ENROLLED
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        world.sql(
            f"INSERT INTO enrollments (id, {ENROLLMENT_COLUMNS}) "
            f"SELECT '01JD3QZ8X5V9K2M7N4P6R8T0AB', {ENROLLMENT_COLUMNS} FROM enrollments"
        )


def test_no_active_variant_is_refused(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    # a stored Campaign always keeps an active Variant (InvalidCampaigns otherwise): a stub
    stored = world.read(lambda: setup_queries.campaign(campaign_id))
    assert stored is not None
    retired = replace(
        stored,
        variants=tuple(replace(v, status=VariantStatus.INACTIVE) for v in stored.variants),
    )
    monkeypatch.setattr(setup_queries, "campaign", lambda _: retired)

    found = refused(world, contact_id, campaign_id)

    assert found == EnrollRefusal(RefusalCode.NO_ACTIVE_VARIANT)


def test_no_bindable_inbox_is_refused(world: World) -> None:
    _, contact_id = world.contact()
    world.sql("UPDATE sending_inboxes SET status = 'paused'")

    found = refused(world, contact_id, world.campaign(US_CAMPAIGN))

    assert found == EnrollRefusal(RefusalCode.NO_SENDING_INBOX)


def test_the_gate_answers_before_the_campaign_is_checked(world: World) -> None:
    _, contact_id = world.contact(web="gated.com")
    with world.units.open():
        Compliance(world.clock).add_value("gated.com")

    found = refused(world, contact_id, world.campaign(OPS_US))  # draft, and blocked

    assert found.code is RefusalCode.BLOCKED


# Errors raise before any write --------------------------------------------------------------------


def raises(world: World, error: type[Exception], *args: object) -> None:
    before = world.counts()
    with pytest.raises(error):
        world.enroll(*args)  # type: ignore[arg-type]
    assert world.counts() == before


def test_bad_arguments_and_unknown_ids_raise_before_any_write(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    unknown = "01JD3QZ8X5V9K2M7N4P6R8T0AB"

    for error, args in [
        (TypeError, (1, campaign_id)),
        (TypeError, (contact_id, None)),
        (TypeError, (contact_id, campaign_id, 3)),
        (ValueError, (contact_id, campaign_id, "nope")),
        (TypeError, (contact_id, campaign_id, SELECTION, 1)),
        (TypeError, (contact_id, campaign_id, SELECTION, (True,))),
        (TypeError, (contact_id, campaign_id, SELECTION, ("1",))),
        (ValueError, (contact_id, campaign_id, SELECTION, ())),
        (ValueError, (contact_id, campaign_id, SELECTION, (2, 1))),
        (ValueError, (contact_id, campaign_id, SELECTION, (1, 1))),
        (ValueError, (contact_id, campaign_id, SELECTION, (0,))),
        (ValueError, (contact_id, campaign_id, SELECTION, (99,))),
        (LookupError, (unknown, campaign_id)),
        (LookupError, ("not-a-ulid", campaign_id)),
        (LookupError, (contact_id, unknown)),
    ]:
        raises(world, error, *args)


def test_a_missing_sequence_raises(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    monkeypatch.setattr(setup_queries, "sequence_by_id", lambda _: None)

    raises(world, LookupError, contact_id, campaign_id)


# UK: the LIA on the basis -------------------------------------------------------------------------


def test_a_gb_basis_records_the_campaigns_lia(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    uk = world.activate(UK_CAMPAIGN, (LIA_REF, LIA_DATE.isoformat()))
    _, contact_id = world.contact("GB")
    # no UK Contact is eligible until the legal form is read (Story 2.12): a stubbed Decision
    monkeypatch.setattr(Compliance, "evaluate_eligibility", lambda self, _: UK_DECISION)

    found = world.enroll(contact_id, uk)

    assert isinstance(found, NewEnrollment)
    assert world.bases(contact_id) == [
        (UK_DECISION.basis_rule, uk, LIA_REF, LIA_DATE.isoformat(), stamp(LATER))
    ]


def test_a_real_uk_contact_is_not_eligible_yet(world: World) -> None:
    uk = world.activate(UK_CAMPAIGN, (LIA_REF, LIA_DATE.isoformat()))
    _, contact_id = world.contact("GB")

    found = refused(world, contact_id, uk)

    assert found.code is RefusalCode.NOT_ELIGIBLE
    assert ReasonKind.UK_LEGAL_FORM_UNCONFIRMED in [reason.kind for reason in found.reasons]


# The table ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("contact_id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("campaign_id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("variant_id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("sending_inbox_id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("sequence_id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("sequence_version", 2),
        ("offer_id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
        ("origin", "referral"),
        ("created_at", "2026-11-19T09:00:00.000000Z"),
        ("id", "01JD3QZ8X5V9K2M7N4P6R8T0AB"),
    ],
)
def test_the_trigger_refuses_a_change_to_a_bound_column(
    world: World, column: str, value: object
) -> None:
    _, contact_id = world.contact()
    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN))
    assert isinstance(found, NewEnrollment)

    with pytest.raises(sqlite3.IntegrityError, match="bound columns"):
        world.sql(f"UPDATE enrollments SET {column} = ? WHERE id = ?", (value, found.enrollment_id))


@pytest.mark.parametrize(
    ("assignments", "constraint"),
    [
        ("origin = 'nope'", "ck_enrollments_origin"),
        ("status = 'nope'", "ck_enrollments_status"),
        ("status = 'paused', pause_cause = 'nope'", "ck_enrollments_pause_cause"),
        (
            "status = 'stopped', stop_cause = 'nope', ended_at = updated_at",
            "ck_enrollments_stop_cause",
        ),
        ("pause_cause = 'operator'", "ck_enrollments_paused"),
        ("stop_cause = 'reply'", "ck_enrollments_stopped"),
        ("status = 'completed'", "ck_enrollments_ended"),
        ("sequence_version = 0", "ck_enrollments_sequence_version"),
    ],
)
def test_the_checks_refuse_a_bad_row(world: World, assignments: str, constraint: str) -> None:
    _, contact_id = world.contact()
    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN))
    assert isinstance(found, NewEnrollment)
    # origin and sequence_version are bound columns too: without their trigger the CHECKs answer
    world.sql("DROP TRIGGER trg_enrollments_bound")

    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        world.sql(f"UPDATE enrollments SET {assignments} WHERE id = ?", (found.enrollment_id,))


def test_the_repository_has_no_setter_for_a_bound_column() -> None:
    functions = {
        name: value
        for name, value in vars(enrollments_repo).items()
        if inspect.isfunction(value)
        and value.__module__ == enrollments_repo.__name__
        and not name.startswith("__")
    }
    assert set(functions) == {
        "_stored",
        "get",
        "live_for_lead",
        "live_for_contact",
        "live",
        "ended_with_step",
        "live_counts_by_inbox",
        "insert",
        "set_status",
        "record_step_sent",  # Story 4.9
        "move_lead",
    }
    # the three updates write the status group, the send times and lead_id only
    assert list(inspect.signature(functions["set_status"]).parameters) == [
        "enrollment_id",
        "status",
        "pause_cause",
        "stop_cause",
        "ended_at",
        "at",
    ]
    assert list(inspect.signature(functions["move_lead"]).parameters) == [
        "from_lead_id",
        "to_lead_id",
        "at",
    ]
    assert list(inspect.signature(functions["record_step_sent"]).parameters) == [
        "enrollment_id",
        "sent_at",
        "ended_at",
        "at",
    ]


def test_the_trigger_refuses_an_enrollment_for_an_erased_contact(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    found = world.enroll(contact_id, campaign_id)
    assert isinstance(found, NewEnrollment)
    erasures = Erasures(world.clock, Compliance(world.clock))
    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    with pytest.raises(sqlite3.IntegrityError, match="erased Contact"):
        world.sql(
            f"INSERT INTO enrollments (id, {ENROLLMENT_COLUMNS}) "
            f"SELECT '01JD3QZ8X5V9K2M7N4P6R8T0AB', lead_id, contact_id, campaign_id, variant_id, "
            "sending_inbox_id, sequence_id, sequence_version, offer_id, origin, 'active', NULL, "
            "NULL, created_at, updated_at, NULL, NULL FROM enrollments"
        )


def test_a_stop_never_ends_before_the_last_step(world: World) -> None:
    _, contact_id = world.contact()
    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN))
    assert isinstance(found, NewEnrollment)
    ahead = stamp(LATER + timedelta(minutes=5))  # a Step sent "after" now (clock skew)
    world.sql(
        "UPDATE enrollments SET last_step_sent_at = ? WHERE id = ?", (ahead, found.enrollment_id)
    )

    world.stop(found.enrollment_id)

    assert world.row(found.enrollment_id)[9:] == (
        "stopped",
        None,
        "operator_stop",
        stamp(LATER),
        stamp(LATER),
        ahead,
        ahead,
    )
    assert world.events("enrollments", found.enrollment_id)[-1] == (
        "active",
        "stopped",
        "operator_stop",
        None,
        "operator",
        stamp(LATER),
    )


# Advance ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["queued", "contacted"])
def test_enrolled_leaves_a_lead_past_new_alone(world: World, stage: str) -> None:
    lead_id, contact_id = world.contact()
    world.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead_id))
    events = len(world.events("leads", lead_id))

    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN))

    assert isinstance(found, NewEnrollment)
    assert world.stage(lead_id) == stage
    assert len(world.events("leads", lead_id)) == events


def test_advance_to_at_least_refuses_a_bad_target_and_an_unknown_lead(world: World) -> None:
    lead_id, _ = world.contact()
    pipeline = Pipeline(world.clock)

    for error, call in [
        (ValueError, lambda: pipeline.advance_to_at_least(lead_id, PipelineStage.WON)),
        (ValueError, lambda: pipeline.advance_to_at_least(lead_id, "lost")),  # type: ignore[arg-type]
        (ValueError, lambda: pipeline.advance_to_at_least(lead_id, PipelineStage.QUEUED, "x")),
        (
            LookupError,
            lambda: pipeline.advance_to_at_least(
                "01JD3QZ8X5V9K2M7N4P6R8T0AB", PipelineStage.QUEUED
            ),
        ),
    ]:
        with pytest.raises(error):
            world.transact(call)
    assert world.stage(lead_id) == "new"

    assert world.transact(lambda: pipeline.advance_to_at_least(lead_id, PipelineStage.QUEUED))
    assert not world.transact(lambda: pipeline.advance_to_at_least(lead_id, PipelineStage.QUEUED))
    world.sql("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (lead_id,))
    assert not world.transact(
        lambda: pipeline.advance_to_at_least(lead_id, PipelineStage.CONTACTED)
    )

