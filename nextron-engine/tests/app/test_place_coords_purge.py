"""Story 8.2's daily ``purge_place_coords`` on a temporary database at head (FR-49, AD-10, AD-17):
``Discovery.purge_place_coords`` at a frozen now nulls ``place_lat``, ``place_lng`` and
``place_coords_at`` stored strictly more than 30 days earlier, keeps those stored exactly 30
days earlier or later and keeps every ``place_id``; the real ``purge_place_coords`` job handler
reaches it through the job type registry, and the health panel lists the job."""

import logging
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.db.models.base import utc_text
from nextron.domain.health import LastRun, job_group_runs
from nextron.domain.job import Job, JobGroup, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.places import PURGE_PLACE_COORDS_JOB
from tests.app.test_signal_extraction import T0, World, build_world, no_jobs

LOGGER = "nextron.app.discovery.api"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine, prompts=False)


def placed(world: World, place_id: str, stored_at: Any) -> str:
    """A Lead holding ``place_id`` and coordinates stored at ``stored_at`` (``None``: none)."""
    identity = LeadIdentity(name="Smith CPA", segment_id=world.segment, place_id=place_id)
    upserted = world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.PLACES))
    assert upserted.lead_id is not None
    if stored_at is not None:
        world.sql(
            "UPDATE leads SET place_lat = 39.96, place_lng = -82.99, place_coords_at = ? "
            "WHERE id = ?",
            (utc_text(stored_at), upserted.lead_id),
        )
    return upserted.lead_id


def coords(world: World, lead_id: str) -> tuple[Any, ...]:
    [row] = world.sql(
        "SELECT place_id, place_lat, place_lng, place_coords_at FROM leads WHERE id = ?",
        (lead_id,),
    )
    return tuple(row)


def test_coordinates_older_than_30_days_are_nulled_and_the_place_id_kept(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    over = placed(world, "ChIJ-over", T0 - timedelta(days=30, seconds=1))
    exactly = placed(world, "ChIJ-exactly", T0 - timedelta(days=30))
    fresh = placed(world, "ChIJ-fresh", T0 - timedelta(days=1))
    never = placed(world, "ChIJ-never", None)
    untimed = placed(world, "ChIJ-untimed", None)
    world.sql("UPDATE leads SET place_lat = 39.96 WHERE id = ?", (untimed,))
    untimed_lng = placed(world, "ChIJ-untimed-lng", None)
    world.sql("UPDATE leads SET place_lng = -82.99 WHERE id = ?", (untimed_lng,))

    with world.units.open():
        assert world.discovery.purge_place_coords() == 3

    assert coords(world, over) == ("ChIJ-over", None, None, None)
    # coordinates with no time are of unknown age: purged too
    assert coords(world, untimed) == ("ChIJ-untimed", None, None, None)
    assert coords(world, untimed_lng) == ("ChIJ-untimed-lng", None, None, None)
    assert coords(world, exactly)[:3] == ("ChIJ-exactly", 39.96, -82.99)
    assert coords(world, exactly)[3] is not None
    assert coords(world, fresh)[:3] == ("ChIJ-fresh", 39.96, -82.99)
    assert coords(world, never) == ("ChIJ-never", None, None, None)
    [purged] = [
        r for r in caplog.records if r.name == LOGGER and r.getMessage() == "place_coords_purged"
    ]
    assert getattr(purged, "count") == 3

    world.clock.advance(timedelta(seconds=1))  # exactly is now 30 days and 1 second old
    with world.units.open():
        assert world.discovery.purge_place_coords() == 1
    assert coords(world, exactly) == ("ChIJ-exactly", None, None, None)
    with world.units.open():
        assert world.discovery.purge_place_coords() == 0  # nothing more is due


def test_the_job_handler_purges_through_the_registry(world: World) -> None:
    over = placed(world, "ChIJ-over", T0 - timedelta(days=31))
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=lambda: world.discovery,
        research=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        test_runs=no_jobs,
        job_boards=no_jobs,
        legal_forms=no_jobs,
        scores=no_jobs,
    )
    job = Job("01JD0000000000000000000099", PURGE_PLACE_COORDS_JOB, Lane.BULK, {"v": 1}, 1)

    with world.units.open():
        job_types.get(PURGE_PLACE_COORDS_JOB).handler(job)

    assert coords(world, over) == ("ChIJ-over", None, None, None)
    found = job_types.get(PURGE_PLACE_COORDS_JOB)
    assert (found.group, found.lanes, found.priority) == (
        JobGroup.OTHER,
        frozenset({Lane.BULK}),
        20,
    )
    # the health panel lists it with the other jobs of group Other
    pairs = [(name, job_types.get(name).group) for name in job_types.names()]
    last = world.read(jobqueue_queries.last_successes)
    other = next(runs for runs in job_group_runs(pairs, last) if runs.group is JobGroup.OTHER)
    assert LastRun(PURGE_PLACE_COORDS_JOB, None) in other.runs
