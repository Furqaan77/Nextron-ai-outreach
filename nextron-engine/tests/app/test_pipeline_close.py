"""``Pipeline.close`` and ``Pipeline.close_suppressed`` (AD-3, AD-4, FR-9, Story 3.3) on a
temporary database at head, with ``FrozenClock``: every open and parked stage closes ``lost``
or ``disqualified`` with one ``events`` row; a closed Lead is a no-op returning ``False``; an
unknown Lead raises ``LookupError`` and a stage other than ``lost`` or ``disqualified`` or a bad
``cause_ref`` raises ``ValueError``, each writing nothing; and ``close_suppressed`` closes the
Lead ``lost`` only for the opt-out sources with a ``lead_id``."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import CLOSE_EVENTS, Pipeline
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.notifications import SubjectSuppressed
from nextron.domain.pipeline_stage import CLOSED_STAGES, OPEN_STAGES, PARKED_STAGES, PipelineStage
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import OPT_OUT_SOURCES, SuppressionSource
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP = "2026-11-18T09:00:00.000000Z"
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
CAUSE_REF = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AC"


@dataclass
class World:
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    pipeline: Pipeline
    lead_id: str = ""

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        assert self.engine.url.database
        connection = sqlite3.connect(Path(self.engine.url.database), isolation_level=None)
        try:
            return [tuple(row) for row in connection.execute(statement, params).fetchall()]
        finally:
            connection.close()

    def stage(self) -> str:
        [(stage,)] = self.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (self.lead_id,))
        return str(stage)

    def set_stage(self, stage: str) -> None:
        self.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, self.lead_id))

    def events(self) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
            "FROM events WHERE entity = 'leads' ORDER BY rowid"
        )

    def dump(self) -> list[list[tuple[Any, ...]]]:
        return [self.sql(f"SELECT * FROM {table} ORDER BY rowid") for table in ("leads", "events")]


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
    )
    return JobQueue(clock, job_types)


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    found = World(units, db_engine, clock, Pipeline(clock))
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    identity = LeadIdentity(name="Firm LLP", segment_id=segment, web_domain="firm.co.uk")
    discovery = Discovery(clock, found.pipeline, queue_on(clock))
    found.lead_id = found.transact(
        lambda: discovery.upsert_lead(identity, LeadSource.CSV)
    ).lead_id
    return found


def test_the_close_events_are_the_two_closed_stages() -> None:
    assert set(CLOSE_EVENTS) == set(CLOSED_STAGES)


@pytest.mark.parametrize("stage", [*OPEN_STAGES, *PARKED_STAGES])
@pytest.mark.parametrize(
    ("closed", "cause"),
    [(PipelineStage.LOST, "closed_lost"), (PipelineStage.DISQUALIFIED, "closed_disqualified")],
)
def test_close_moves_any_open_or_parked_lead_and_records_it(
    world: World, stage: PipelineStage, closed: PipelineStage, cause: str
) -> None:
    world.set_stage(str(stage))
    before = len(world.events())

    changed = world.transact(lambda: world.pipeline.close(world.lead_id, closed, CAUSE_REF))

    assert changed is True
    assert world.stage() == str(closed)
    assert len(world.events()) == before + 1
    assert world.events()[-1] == (
        "leads",
        world.lead_id,
        str(stage),
        str(closed),
        cause,
        CAUSE_REF,
        "system:pipeline",
        STAMP,
    )


def test_close_takes_no_cause_ref_too(world: World) -> None:
    assert world.transact(lambda: world.pipeline.close(world.lead_id, PipelineStage.LOST, None))

    assert world.events()[-1][5] is None


def test_close_takes_the_stage_as_its_code(world: World) -> None:
    code: Any = "disqualified"

    assert world.transact(lambda: world.pipeline.close(world.lead_id, code, None))

    assert world.stage() == "disqualified"


@pytest.mark.parametrize("already", list(CLOSED_STAGES))
@pytest.mark.parametrize("closed", list(CLOSED_STAGES))
def test_closing_a_closed_lead_is_a_no_op(
    world: World, already: PipelineStage, closed: PipelineStage
) -> None:
    world.set_stage(str(already))
    before = world.dump()

    changed = world.transact(lambda: world.pipeline.close(world.lead_id, closed, CAUSE_REF))

    assert changed is False
    assert world.dump() == before


@pytest.mark.parametrize(
    "stage",
    [PipelineStage.NEW, PipelineStage.WON, PipelineStage.NOT_NOW, "won", "LOST", None, ["lost"]],
)
def test_close_refuses_any_other_stage(world: World, stage: Any) -> None:
    before = world.dump()

    with pytest.raises(ValueError, match="lost or disqualified"):
        world.transact(lambda: world.pipeline.close(world.lead_id, stage, None))

    assert world.dump() == before


@pytest.mark.parametrize("cause_ref", ["x", CAUSE_REF.lower(), 7])
def test_close_refuses_a_cause_ref_that_is_not_a_ulid(world: World, cause_ref: Any) -> None:
    before = world.dump()

    with pytest.raises(ValueError, match="cause_ref"):
        world.transact(
            lambda: world.pipeline.close(world.lead_id, PipelineStage.LOST, cause_ref)
        )

    assert world.dump() == before


@pytest.mark.parametrize("lead_id", [UNKNOWN, "not-a-ulid"])
def test_close_of_an_unknown_lead_raises_lookup_error(world: World, lead_id: str) -> None:
    before = world.dump()

    with pytest.raises(LookupError) as caught:
        world.transact(lambda: world.pipeline.close(lead_id, PipelineStage.LOST, None))

    assert str(caught.value) == "close: the Lead is unknown"
    assert world.dump() == before


# close_suppressed ------------------------------------------------------------------------------


def notice(source: str, lead_id: str | None) -> SubjectSuppressed:
    return SubjectSuppressed(
        suppression_entry_id=CAUSE_REF, source=source, contact_id=UNKNOWN, lead_id=lead_id
    )


@pytest.mark.parametrize("source", list(OPT_OUT_SOURCES))
def test_close_suppressed_closes_the_lead_lost_for_an_opt_out(world: World, source: str) -> None:
    world.transact(lambda: world.pipeline.close_suppressed(notice(str(source), world.lead_id)))

    assert world.stage() == "lost"
    assert world.events()[-1][2:6] == ("new", "lost", "closed_lost", CAUSE_REF)


@pytest.mark.parametrize(
    "source",
    [
        *(source for source in SuppressionSource if source not in OPT_OUT_SOURCES),
        "something_else",
    ],
)
def test_close_suppressed_leaves_the_stage_for_other_sources(world: World, source: str) -> None:
    before = world.dump()

    world.transact(lambda: world.pipeline.close_suppressed(notice(str(source), world.lead_id)))

    assert world.dump() == before


def test_close_suppressed_without_a_lead_does_nothing(world: World) -> None:
    before = world.dump()

    world.transact(lambda: world.pipeline.close_suppressed(notice("one_click", None)))

    assert world.dump() == before


def test_close_suppressed_is_idempotent(world: World) -> None:
    world.transact(lambda: world.pipeline.close_suppressed(notice("mailto", world.lead_id)))
    after_first = world.dump()

    world.transact(lambda: world.pipeline.close_suppressed(notice("mailto", world.lead_id)))

    assert world.dump() == after_first
