"""Story 2.9's tables on a temporary database at head (AD-7, AD-18, NFR-24): the repository
(``nextron.db.repos.research.signals``: no Signal without Evidence, the live and current reads,
retracting unsupported Signals, reparenting), the ``evidence`` triggers (no delete; only
``lead_id``, ``last_confirmed_at`` and, once each, ``superseded_by`` and ``retracted_at``
change) and the named CHECKs that keep each Evidence type's shape, the Signals' and the runs'."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.setup.api import Setup
from nextron.db.repos.research import profile as profile_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.segments import validate_segments
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
URL = "https://smith-law.com/team"
ACTOR = "ai:extract_signals@v1"
SEGMENTS = main.SEED_DIR / main.SEGMENTS_FILE_NAME


def no_jobs() -> Any:
    raise AssertionError("no job runs in these tests")


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    discovery: Discovery
    segment: str

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

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def lead(self, domain: str = "smith-law.com") -> str:
        identity = LeadIdentity(name="Smith Law LLP", segment_id=self.segment, web_domain=domain)
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.PLACES))
        return upserted.lead_id

    def quote(self, lead_id: str, text: str = "We are hiring.", **more: Any) -> str:
        values: dict[str, Any] = {
            "lead_id": lead_id,
            "evidence_type": "quote",
            "quote": text,
            "sought": None,
            "number": None,
            "fact": None,
            "value": None,
            "pages_checked": (URL,),
            "source": "website",
            "source_url": URL,
            "retrieved_at": T0,
            "extracted_by": ACTOR,
            "person_contact_id": None,
            "at": T0,
        }
        values.update(more)
        return self.transact(lambda: signals_repo.insert_evidence(**values))

    def signal(self, lead_id: str, evidence_ids: list[str], **more: Any) -> str:
        values: dict[str, Any] = {
            "lead_id": lead_id,
            "signal_type": "law_hiring_legal_ops",
            "signal_class": "event",
            "tier": 1,
            "score_only": False,
            "event_date": None,
            "first_seen_at": T0,
            "evidence_ids": evidence_ids,
        }
        values.update(more)
        return self.transact(lambda: signals_repo.insert_signal(**values))


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
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
        legal_forms=no_jobs,
    )
    discovery = Discovery(clock, Pipeline(clock), JobQueue(clock, job_types))
    found = World(units, db_engine, discovery, "")
    [(found.segment,)] = found.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
    return found


# The repository ---------------------------------------------------------------------------------


def test_a_signal_cant_be_stored_without_evidence(world: World) -> None:
    lead = world.lead()

    with pytest.raises(ValueError, match="without at least one Evidence row"):
        world.signal(lead, [])

    assert world.sql("SELECT COUNT(*) FROM signals") == [(0,)]


def test_a_signal_is_stored_with_a_link_per_evidence_row(world: World) -> None:
    lead = world.lead()
    first, second = world.quote(lead), world.quote(lead, "We are hiring again.")

    signal_id = world.signal(lead, [first, second, first], event_date=date(2026, 10, 14))

    assert set(world.sql("SELECT signal_id, evidence_id FROM signal_evidence")) == {
        (signal_id, first),
        (signal_id, second),
    }
    [stored] = world.read(lambda: signals_repo.signals(lead, current_only=True))
    assert (stored.signal_type, stored.event_date, stored.score_only) == (
        "law_hiring_legal_ops",
        date(2026, 10, 14),
        False,
    )
    with pytest.raises(IntegrityError):  # the pair is unique
        world.transact(lambda: signals_repo.link(signal_id, first))


def test_live_evidence_and_current_signals(world: World) -> None:
    lead = world.lead()
    kept, gone, old = world.quote(lead, "A."), world.quote(lead, "B."), world.quote(lead, "C.")
    world.transact(lambda: signals_repo.retract_evidence(gone, T0))
    world.transact(lambda: signals_repo.supersede_evidence(old, kept))
    supported = world.signal(lead, [kept])
    bare = world.signal(lead, [gone], signal_type="law_expansion", signal_class="event", tier=2)

    live = world.read(lambda: signals_repo.evidence(lead, live_only=True))
    retracted = world.transact(lambda: signals_repo.retract_unsupported(lead, T0))

    assert [row.id for row in live] == [kept]
    every = world.read(lambda: signals_repo.evidence(lead, live_only=False))
    assert {row.id: row.live for row in every} == {kept: True, gone: False, old: False}
    assert retracted == (bare,)
    current = world.read(lambda: signals_repo.signals(lead, current_only=True))
    assert [signal.id for signal in current] == [supported]
    assert world.transact(lambda: signals_repo.retract_unsupported(lead, T0)) == ()


def test_reparent_moves_evidence_and_signals_only(world: World) -> None:
    survivor, loser = world.lead("older.com"), world.lead()
    evidence_id = world.quote(loser)
    world.signal(loser, [evidence_id])
    world.transact(
        lambda: signals_repo.insert_run(
            lead_id=loser,
            crawl_run_id=None,
            route="extract_signals",
            actor=ACTOR,
            outcome="ok",
            error_code=None,
            pages_sent=1,
            pages_truncated=0,
            stored=1,
            discarded=0,
            started_at=T0,
            ended_at=T0,
        )
    )

    world.transact(lambda: signals_repo.reparent(loser, survivor))

    assert world.sql("SELECT lead_id FROM evidence") == [(survivor,)]
    assert world.sql("SELECT lead_id FROM signals") == [(survivor,)]
    assert world.sql("SELECT lead_id FROM extraction_runs") == [(loser,)]


def test_the_profile_group_reads_and_writes_the_headquarters(world: World) -> None:
    lead = world.lead()
    evidence_id = world.quote(lead)

    unknown = profile_repo.StoredHeadquarters(None, None)
    assert world.read(lambda: profile_repo.headquarters(lead)) == unknown
    world.transact(lambda: profile_repo.set_headquarters(lead, "Europe/London", evidence_id))
    assert world.sql("SELECT hq_tz, hq_evidence_id FROM leads WHERE id = ?", (lead,)) == [
        ("Europe/London", evidence_id)
    ]
    assert world.read(lambda: profile_repo.headquarters("01KA0000000000000000000000")) is None


def test_last_ok_run_is_the_ok_run_of_its_route_started_last(world: World) -> None:
    lead = world.lead()

    def run(outcome: str, minutes: int, route: str = "extract_signals") -> str:
        at = T0 + timedelta(minutes=minutes)
        return world.transact(
            lambda: signals_repo.insert_run(
                lead_id=lead,
                crawl_run_id=None,
                route=route,
                actor=ACTOR,
                outcome=outcome,
                error_code=None,
                pages_sent=0,
                pages_truncated=0,
                stored=0,
                discarded=0,
                started_at=at,
                ended_at=at,
            )
        )

    assert world.read(lambda: signals_repo.last_ok_run(lead, "extract_signals")) is None
    run("ok", 0)
    latest = run("ok", 10)
    run("failed", 20)
    people = run("ok", 30, route="extract_people")  # Story 2.10: another route's run

    found = world.read(lambda: signals_repo.last_ok_run(lead, "extract_signals"))
    assert found is not None and found.id == latest
    found_people = world.read(lambda: signals_repo.last_ok_run(lead, "extract_people"))
    assert found_people is not None and found_people.id == people
    assert [row.outcome for row in world.read(lambda: signals_repo.runs(lead))] == [
        "ok",
        "ok",
        "failed",
        "ok",
    ]


# The triggers -----------------------------------------------------------------------------------


def raises_immutable(world: World, statement: str, params: tuple[object, ...]) -> None:
    with pytest.raises(IntegrityError, match="Evidence is immutable"):
        world.sql(statement, params)


def test_evidence_is_never_deleted(world: World) -> None:
    evidence_id = world.quote(world.lead())

    raises_immutable(world, "DELETE FROM evidence WHERE id = ?", (evidence_id,))
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(1,)]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("quote", "Something else."),
        ("type", "absence"),
        ("number", 3),
        ("fact", "staff"),
        ("pages_checked", '["https://other.com/"]'),
        ("source", "operator"),
        ("source_url", "https://other.com/"),
        ("retrieved_at", "2026-01-01T00:00:00.000000Z"),
        ("extracted_by", "operator"),
        ("created_at", "2026-01-01T00:00:00.000000Z"),
        ("id", "01KA0000000000000000000000"),
    ],
)
def test_a_fixed_column_never_changes(world: World, column: str, value: object) -> None:
    evidence_id = world.quote(world.lead())

    raises_immutable(world, f"UPDATE evidence SET {column} = ? WHERE id = ?", (value, evidence_id))


def test_lead_id_and_last_confirmed_at_change_and_the_end_columns_change_once(
    world: World,
) -> None:
    lead, other = world.lead(), world.lead("other.com")
    first, second, third = world.quote(lead, "A."), world.quote(lead, "B."), world.quote(lead, "C.")

    world.sql(
        "UPDATE evidence SET lead_id = ?, last_confirmed_at = ? WHERE id = ?",
        (other, "2026-12-01T00:00:00.000000Z", first),
    )
    world.transact(lambda: signals_repo.supersede_evidence(first, second))
    world.transact(lambda: signals_repo.retract_evidence(third, T0))

    raises_immutable(world, "UPDATE evidence SET superseded_by = ? WHERE id = ?", (third, first))
    raises_immutable(world, "UPDATE evidence SET superseded_by = NULL WHERE id = ?", (first,))
    raises_immutable(
        world,
        "UPDATE evidence SET retracted_at = ? WHERE id = ?",
        ("2027-01-01T00:00:00.000000Z", third),
    )
    raises_immutable(world, "UPDATE evidence SET retracted_at = NULL WHERE id = ?", (third,))
    assert world.sql("SELECT lead_id, superseded_by FROM evidence WHERE id = ?", (first,)) == [
        (other, second)
    ]


# The CHECKs --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "check"),
    [
        ({"evidence_type": "rumour"}, "ck_evidence_type"),
        ({"source": "linkedin"}, "ck_evidence_source"),
        ({"fact": "revenue", "number": 1}, "ck_evidence_fact"),
        ({"extracted_by": "ai:"}, "ck_evidence_extracted_by"),
        ({"extracted_by": "model"}, "ck_evidence_extracted_by"),
        ({"quote": None}, "ck_evidence_quote_shape"),  # a quote has a quote
        ({"sought": "chat widget"}, "ck_evidence_quote_shape"),  # and no sought
        ({"evidence_type": "absence", "sought": "chat widget"}, "ck_evidence_sought_shape"),
        ({"evidence_type": "absence", "quote": None}, "ck_evidence_sought_shape"),
        (
            {"evidence_type": "count", "quote": None, "sought": "lawyers"},
            "ck_evidence_number_shape",
        ),
        (
            {"evidence_type": "absence", "quote": None, "sought": "chat", "number": 0},
            "ck_evidence_number_shape",
        ),
        ({"fact": "location"}, "ck_evidence_value_shape"),  # a profile fact has a value
        ({"value": "Leeds"}, "ck_evidence_value_shape"),  # and only a profile fact has one
        ({"fact": "staff", "value": "40"}, "ck_evidence_value_shape"),
        ({"fact": "staff"}, "ck_evidence_size_number"),  # a size fact has a number
        ({"quote": "x" * 301}, "ck_evidence_lengths"),
        ({"quote": ""}, "ck_evidence_lengths"),
        ({"fact": "what_we_do", "value": "v" * 201}, "ck_evidence_lengths"),
        ({"number": -1}, "ck_evidence_number"),
    ],
)
def test_each_evidence_shape_is_checked(
    world: World, changes: dict[str, Any], check: str
) -> None:
    lead = world.lead()

    with pytest.raises(IntegrityError, match=check):
        world.quote(lead, **changes)


def test_the_valid_shapes_are_accepted(world: World) -> None:
    lead = world.lead()

    world.quote(lead)
    world.quote(lead, fact="location", value="Leeds")
    world.quote(lead, fact="staff", number=40)
    world.quote(lead, evidence_type="count", quote=None, sought="lawyers on the team", number=12)
    world.quote(
        lead,
        evidence_type="absence",
        quote=None,
        sought="chat widget, booking link, after-hours line",
        extracted_by="rule",
        source_url=None,
    )
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(5,)]


def test_superseded_and_retracted_are_never_both_set(world: World) -> None:
    lead = world.lead()
    old, new = world.quote(lead, "A."), world.quote(lead, "B.")
    world.transact(lambda: signals_repo.supersede_evidence(old, new))

    with pytest.raises(IntegrityError, match="ck_evidence_not_superseded_and_retracted"):
        world.transact(lambda: signals_repo.retract_evidence(old, T0))


@pytest.mark.parametrize(
    ("changes", "check"),
    [
        ({"signal_class": "trend"}, "ck_signals_signal_class"),
        ({"tier": 4}, "ck_signals_tier"),
        ({"tier": 0}, "ck_signals_tier"),
        ({"signal_class": "attribute", "event_date": date(2026, 1, 1)}, "ck_signals_event_date"),
    ],
)
def test_each_signal_rule_is_checked(world: World, changes: dict[str, Any], check: str) -> None:
    lead = world.lead()
    evidence_id = world.quote(lead)

    with pytest.raises(IntegrityError, match=check):
        world.signal(lead, [evidence_id], **changes)


@pytest.mark.parametrize(
    ("changes", "check"),
    [
        ({"outcome": "maybe"}, "ck_extraction_runs_outcome"),
        ({"stored": -1}, "ck_extraction_runs_counts"),
    ],
)
def test_each_run_rule_is_checked(world: World, changes: dict[str, Any], check: str) -> None:
    lead = world.lead()
    values: dict[str, Any] = {
        "lead_id": lead,
        "crawl_run_id": None,
        "route": "extract_signals",
        "actor": None,
        "outcome": "ok",
        "error_code": None,
        "pages_sent": 0,
        "pages_truncated": 0,
        "stored": 0,
        "discarded": 0,
        "started_at": T0,
        "ended_at": T0,
    }
    values.update(changes)

    with pytest.raises(IntegrityError, match=check):
        world.transact(lambda: signals_repo.insert_run(**values))
