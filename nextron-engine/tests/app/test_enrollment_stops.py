"""Enrollments stop as soon as they exist (AD-3, AD-4, AD-16, AD-17; Story 4.8, decisions 1 and
7), on the world of ``tests.app.test_enroll``, through the real subscriber registry:

- **Suppression.** An operator suppression of the address stops the live Enrollment
  (``suppressed``, ``cause_ref`` the entry; the Lead stays open); a repeat is a no-op; an
  opt-out (``one_click``) stops it and then closes the Lead ``lost``; a domain suppression
  reaches a Contact the notification doesn't name; an Enrollment of an unblocked Contact stays.
- **Lead close.** ``Pipeline.close(lost|disqualified)`` stops the live Enrollment
  (``lead_closed``, ``cause_ref`` the close's), also when the Lead is already closed.
- **Erasure.** A retention erasure stops it with ``contact_erased``; a DSR erasure suppresses
  first, so it stops with ``suppressed``.
- **Merge.** Two Leads with a live Enrollment each: ``MergeRefused`` and nothing changes. One
  live: the merge proceeds and the loser's Enrollments move to the survivor.
- **``stop`` itself.** Its argument checks, an unknown Enrollment, a repeat, and a paused one.
"""

from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.pipeline.api import Pipeline
from nextron.domain.contactability import ContactSubject, SuppressionScope
from nextron.domain.enrollment import NewEnrollment, StopCause
from nextron.domain.leads import LeadIdentity, LeadSource, MergeRefused
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.suppression_entry import SuppressionSource
from tests.app.test_enroll import LATER, US_CAMPAIGN, World, build_world, stamp

UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AB"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def enrolled(world: World, web: str | None = None) -> tuple[str, str, str]:
    """A new Lead and Contact, enrolled in Law-PrivateAI x US: (lead, contact, enrollment)."""
    lead_id, contact_id = world.contact(web=web)
    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN))
    assert isinstance(found, NewEnrollment)
    return lead_id, contact_id, found.enrollment_id


def status(world: World, enrollment_id: str) -> tuple[Any, ...]:
    """The Enrollment's status, pause cause, stop cause and ended_at."""
    return world.sql(
        "SELECT status, pause_cause, stop_cause, ended_at FROM enrollments WHERE id = ?",
        (enrollment_id,),
    )[0]


def last_event(world: World, enrollment_id: str) -> tuple[Any, ...]:
    return world.events("enrollments", enrollment_id)[-1]


def entry_ids(world: World) -> set[str]:
    return {str(row[0]) for row in world.sql("SELECT id FROM suppression_entries")}


STOPPED_LATER = ("stopped", None, "suppressed", stamp(LATER))


# Suppression -------------------------------------------------------------------------------------


def test_an_operator_suppression_stops_the_enrollment_and_a_repeat_is_a_no_op(
    world: World,
) -> None:
    lead_id, _, enrollment_id = enrolled(world, web="acmelaw.com")
    _, _, other_id = enrolled(world)  # another firm: not blocked, stays active

    with world.units.open():
        Compliance(world.clock).add_value("zelda@acmelaw.com")

    assert status(world, enrollment_id) == STOPPED_LATER
    event = last_event(world, enrollment_id)
    assert event[:3] + event[4:] == (
        "active",
        "stopped",
        "suppressed",
        "system:sequencing",
        stamp(LATER),
    )
    assert event[3] in entry_ids(world)  # cause_ref: the published entry
    assert world.stage(lead_id) == "queued"  # an operator entry is no opt-out
    assert status(world, other_id)[0] == "active"
    events = len(world.events("enrollments", enrollment_id))

    with world.units.open():
        Compliance(world.clock).add_value("zelda@acmelaw.com")  # already active: no-op

    assert status(world, enrollment_id) == STOPPED_LATER
    assert len(world.events("enrollments", enrollment_id)) == events


def test_an_opt_out_stops_the_enrollment_then_closes_the_lead_lost(world: World) -> None:
    lead_id, contact_id, enrollment_id = enrolled(world)
    compliance = Compliance(world.clock)

    world.transact(
        lambda: compliance.suppress(
            ContactSubject(contact_id), SuppressionScope.PERSON, SuppressionSource.ONE_CLICK, None
        )
    )

    assert status(world, enrollment_id) == STOPPED_LATER  # stopped first, by suppression
    assert world.stage(lead_id) == "lost"


def test_a_domain_suppression_reaches_a_contact_it_does_not_name(world: World) -> None:
    _, _, enrollment_id = enrolled(world, web="acmelaw.com")

    with world.units.open():
        Compliance(world.clock).add_value("acmelaw.com")  # a domain entry: no contact_id

    assert status(world, enrollment_id) == STOPPED_LATER


# Lead close --------------------------------------------------------------------------------------


@pytest.mark.parametrize("stage", [PipelineStage.LOST, PipelineStage.DISQUALIFIED])
def test_closing_the_lead_stops_its_enrollment(world: World, stage: PipelineStage) -> None:
    lead_id, _, enrollment_id = enrolled(world)
    pipeline = Pipeline(world.clock)

    assert world.transact(lambda: pipeline.close(lead_id, stage, UNKNOWN))

    assert status(world, enrollment_id) == ("stopped", None, "lead_closed", stamp(LATER))
    assert last_event(world, enrollment_id)[:5] == (
        "active",
        "stopped",
        "lead_closed",
        UNKNOWN,
        "system:sequencing",
    )
    assert world.stage(lead_id) == str(stage)


def test_closing_an_already_closed_lead_still_stops_its_enrollment(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world)
    world.sql("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (lead_id,))
    pipeline = Pipeline(world.clock)

    assert not world.transact(lambda: pipeline.close(lead_id, PipelineStage.LOST, None))

    assert status(world, enrollment_id) == ("stopped", None, "lead_closed", stamp(LATER))


# Erasure -----------------------------------------------------------------------------------------


def test_a_retention_erasure_stops_the_enrollment_as_contact_erased(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    erasures = Erasures(world.clock, Compliance(world.clock))

    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    assert status(world, enrollment_id) == ("stopped", None, "contact_erased", stamp(LATER))
    assert last_event(world, enrollment_id)[2:5] == ("contact_erased", None, "system:sequencing")


def test_a_dsr_erasure_suppresses_first_so_the_enrollment_stops_as_suppressed(
    world: World,
) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    erasures = Erasures(world.clock, Compliance(world.clock))

    with world.units.open():
        erasures.erase_on_request(contact_id, "DSR-2026-001")

    assert status(world, enrollment_id) == STOPPED_LATER
    assert len(world.events("enrollments", enrollment_id)) == 2  # created, then stopped once


# Merge -------------------------------------------------------------------------------------------


def merge(world: World, survivor: str, loser: str) -> None:
    world.transact(lambda: world.discovery.merge(survivor, loser))


def test_a_merge_of_two_leads_with_live_enrollments_is_refused(world: World) -> None:
    survivor, _, _ = enrolled(world)
    loser, _, _ = enrolled(world)
    before = world.counts()

    with pytest.raises(MergeRefused, match="both Leads have a live Enrollment"):
        merge(world, survivor, loser)

    assert world.counts() == before
    assert world.sql("SELECT merged_into FROM leads WHERE id = ?", (loser,)) == [(None,)]


def test_a_merge_with_one_live_enrollment_moves_the_losers_enrollments(world: World) -> None:
    survivor, _, ended_id = enrolled(world)
    loser, loser_contact, live_id = enrolled(world)
    world.stop(ended_id)  # the survivor's ended one; the loser's stays live

    merge(world, survivor, loser)

    rows = world.sql("SELECT id, lead_id, contact_id, status FROM enrollments ORDER BY rowid")
    assert rows == [
        (ended_id, survivor, rows[0][2], "stopped"),
        (live_id, survivor, loser_contact, "active"),
    ]
    assert world.sql("SELECT merged_into FROM leads WHERE id = ?", (loser,)) == [(survivor,)]


def test_an_upsert_that_reaches_a_both_live_merge_is_refused_and_rolled_back(
    world: World,
) -> None:
    survivor, _, _ = enrolled(world, web="older-firm.com")
    loser, _, _ = enrolled(world, web="newer-firm.com")
    world.sql("UPDATE leads SET ch_number = '01234567' WHERE id = ?", (loser,))
    identity = LeadIdentity(
        "Older Firm", world.segment, web_domain="older-firm.com", ch_number="01234567"
    )
    before = world.counts()

    with pytest.raises(MergeRefused, match="both Leads have a live Enrollment"):
        world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.CSV))

    assert world.counts() == before
    assert world.sql("SELECT merged_into FROM leads WHERE id = ?", (loser,)) == [(None,)]


def test_a_live_enrollment_moved_onto_a_closed_survivor_stops(world: World) -> None:
    survivor, _ = world.contact()
    loser, _, live_id = enrolled(world)
    world.sql("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (survivor,))

    merge(world, survivor, loser)

    assert world.sql("SELECT lead_id FROM enrollments WHERE id = ?", (live_id,)) == [(survivor,)]
    assert status(world, live_id) == ("stopped", None, "lead_closed", stamp(LATER))
    assert last_event(world, live_id)[2:5] == ("lead_closed", loser, "system:sequencing")


def test_a_merge_with_no_live_enrollment_proceeds(world: World) -> None:
    survivor, _ = world.contact()
    loser, _ = world.contact()

    merge(world, survivor, loser)

    assert world.sql("SELECT merged_into FROM leads WHERE id = ?", (loser,)) == [(survivor,)]


# stop itself -------------------------------------------------------------------------------------


def test_stop_checks_its_arguments(world: World) -> None:
    lead_id, contact_id, enrollment_id = enrolled(world)
    sequencing = world.sequencing
    cause = StopCause.OPERATOR_STOP

    for error, call in [
        (ValueError, lambda: sequencing.stop(cause=cause)),
        (ValueError, lambda: sequencing.stop(cause=cause, lead_id=lead_id, contact_id=contact_id)),
        (TypeError, lambda: sequencing.stop(cause=cause, lead_id=3)),  # type: ignore[arg-type]
        (ValueError, lambda: sequencing.stop(cause="nope", lead_id=lead_id)),  # type: ignore[arg-type]
        (TypeError, lambda: sequencing.stop(cause=None, lead_id=lead_id)),  # type: ignore[arg-type]
        (ValueError, lambda: sequencing.stop(cause=cause, lead_id=lead_id, cause_ref="x")),
        (LookupError, lambda: sequencing.stop(cause=cause, enrollment_id=UNKNOWN)),
    ]:
        with pytest.raises(error):
            world.transact(call)
    assert status(world, enrollment_id)[0] == "active"


def test_stop_by_each_id_and_a_repeat_is_a_no_op(world: World) -> None:
    lead_id, contact_id, enrollment_id = enrolled(world)
    sequencing = world.sequencing
    cause = StopCause.HARD_BOUNCE

    assert world.transact(lambda: sequencing.stop(cause=cause, contact_id=contact_id)) == (
        enrollment_id,
    )
    assert world.transact(lambda: sequencing.stop(cause=cause, contact_id=contact_id)) == ()
    assert world.transact(lambda: sequencing.stop(cause=cause, lead_id=lead_id)) == ()
    assert world.transact(lambda: sequencing.stop(cause=cause, enrollment_id=enrollment_id)) == ()
    assert world.transact(lambda: sequencing.stop(cause=cause, lead_id="not-a-ulid")) == ()
    assert status(world, enrollment_id) == ("stopped", None, "hard_bounce", stamp(LATER))


def test_a_paused_enrollment_stops_and_its_pause_cause_clears(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world)
    world.sql(
        "UPDATE enrollments SET status = 'paused', pause_cause = 'inbound_pending' WHERE id = ?",
        (enrollment_id,),
    )
    sequencing = world.sequencing

    found = world.transact(
        lambda: sequencing.stop(cause=StopCause.OPERATOR_REJECTED, lead_id=lead_id)
    )

    assert found == (enrollment_id,)
    assert status(world, enrollment_id) == ("stopped", None, "operator_rejected", stamp(LATER))
    assert last_event(world, enrollment_id)[:2] == ("paused", "stopped")
    assert last_event(world, enrollment_id)[4] == "operator"
