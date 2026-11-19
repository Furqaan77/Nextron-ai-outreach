"""A Sequence's Draft slots, due times and lifecycle commands (AD-3, AD-4, AD-16, AD-21; Story
4.9), on the world of ``tests.app.test_enroll`` (a temporary database at head, ``FrozenClock``,
the real subscriber registry, the shipped seeds, Law-PrivateAI x US active):

- **Slots.** ``enroll`` creates one ``ready`` slot per Step (no revision, the Sequence's days
  snapshotted), Step 1 due at once, the others without a due time, one Draft event each; a
  re-contact's slots are kind ``recontact``; ``request_steps`` adds only missing Steps and
  checks its arguments before any write; ``Blocked`` writes nothing, and a ``Blocked`` inside
  ``enroll`` rolls the whole enroll back.
- **Sent Steps.** ``MessageSent`` sets the next Step's due time from the send time and the
  Sequence's gap (a repeat is a no-op), records ``last_step_sent_at`` and completes the
  Enrollment after the last Step; an unknown Draft is a no-op.
- **Lifecycle.** ``pause``/``resume`` and ``delay_until``.
- **Cancel.** Every stop (operator, suppression, Lead close, erasure) cancels the ``ready``
  Drafts in the same transaction, one event each; an ``approved`` one is left alone.
- **Capacity.** ``_follow_ups_due`` counts a live Enrollment's due follow-ups per inbox.
- **The table.** Its CHECKs and unique index, and no stored "stalled".
"""

import contextlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.approval.api import Approval
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.drafting.api import Drafting
from nextron.app.pipeline.api import Pipeline
from nextron.app.sequencing import api as sequencing_api
from nextron.app.setup.api import Setup
from nextron.db.uow import current
from nextron.domain.ai import Urgency
from nextron.domain.contactability import Allowed, Blocked, BlockReason
from nextron.domain.enrollment import EnrollmentOrigin, NewEnrollment, PauseCause, StopCause
from nextron.domain.notifications import MessageSent
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.sending_policy import step_due_at
from tests.app.test_enroll import LATER, US_CAMPAIGN, World, build_world, queue_on, stamp

UNKNOWN = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
OM = "01JD3QZ8X5V9K2M7N4P6R8T0AC"
ALL_STEPS = (1, 2, 3, 4)
DAYS = (0, 3, 7, 14)  # the shipped default Sequence
BLOCKED = Blocked((BlockReason.ADDRESS_SUPPRESSED,))
SLOT_COLUMNS = "kind, enrollment_id, step_no, day_offset, current_rev, due_at, status"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def enrolled(
    world: World,
    steps: tuple[int, ...] = ALL_STEPS,
    origin: EnrollmentOrigin = EnrollmentOrigin.SELECTION,
    web: str | None = None,
) -> tuple[str, str, str]:
    """A new Lead and Contact enrolled in Law-PrivateAI x US: (lead, contact, enrollment)."""
    lead_id, contact_id = world.contact(web=web)
    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN), origin, steps)
    assert isinstance(found, NewEnrollment)
    return lead_id, contact_id, found.enrollment_id


def slots(world: World, enrollment_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        f"SELECT {SLOT_COLUMNS} FROM drafts WHERE enrollment_id = ? ORDER BY step_no",
        (enrollment_id,),
    )


def draft_ids(world: World, enrollment_id: str) -> list[str]:
    rows = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? ORDER BY step_no", (enrollment_id,)
    )
    return [str(row[0]) for row in rows]


def due(world: World, draft_id: str) -> str | None:
    found = world.sql("SELECT due_at FROM drafts WHERE id = ?", (draft_id,))[0][0]
    return None if found is None else str(found)


def tz_of(world: World, contact_id: str) -> str:
    return str(world.sql("SELECT tz FROM contacts WHERE id = ?", (contact_id,))[0][0])


def enrollment(world: World, enrollment_id: str) -> tuple[Any, ...]:
    return world.sql(
        "SELECT status, pause_cause, stop_cause, last_step_sent_at, ended_at FROM enrollments "
        "WHERE id = ?",
        (enrollment_id,),
    )[0]


def send(world: World, draft_id: str, at: datetime) -> None:
    """``MessageSent`` for the Draft, published at ``at``."""
    world.clock.set(at)
    world.transact(
        lambda: current().publish(MessageSent(om_id=OM, draft_id=draft_id, kind="step"))
    )


@contextlib.contextmanager
def gate_answers(*answers: Allowed | Blocked) -> Iterator[None]:
    """The gate answers ``answers`` in turn, then the last one forever."""
    original = compliance_api.assert_contactable
    queue = list(answers)

    def fake(subject: object, channel: object, purpose: object) -> Allowed | Blocked:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    compliance_api.assert_contactable = fake  # type: ignore[assignment]
    try:
        yield
    finally:
        compliance_api.assert_contactable = original  # type: ignore[assignment]


# Slots -------------------------------------------------------------------------------------------


def test_enroll_creates_a_ready_slot_per_step_and_step_1_is_due_at_once(world: World) -> None:
    _, contact_a = world.contact()
    before = world.counts()

    campaign_id = world.campaign(US_CAMPAIGN)
    found = world.enroll(contact_a, campaign_id, EnrollmentOrigin.SELECTION, ALL_STEPS)

    assert isinstance(found, NewEnrollment)
    enrollment_id = found.enrollment_id
    first_due = stamp(step_due_at(LATER, 0, tz_of(world, contact_a)))
    assert first_due == stamp(LATER)  # a Wednesday morning: due now
    assert slots(world, enrollment_id) == [
        ("step", enrollment_id, 1, 0, None, first_due, "ready"),
        ("step", enrollment_id, 2, 3, None, None, "ready"),
        ("step", enrollment_id, 3, 7, None, None, "ready"),
        ("step", enrollment_id, 4, 14, None, None, "ready"),
    ]
    gained = {k: world.counts()[k] - before[k] for k in ("drafts", "events", "jobs")}
    # events: + the Enrollment's and the Lead's; jobs: Step 1's draft_step (Story 4.15)
    assert gained == {"drafts": 4, "events": 7, "jobs": 1}
    for draft_id in draft_ids(world, enrollment_id):
        assert world.events("drafts", draft_id) == [
            (None, "ready", "requested", enrollment_id, "system:drafting", stamp(LATER))
        ]
    flags = world.sql(
        "SELECT generic, stale, reapproval_required, event_expired, skipped_at, status_at, "
        "created_at, updated_at FROM drafts WHERE enrollment_id = ?",
        (enrollment_id,),
    )
    assert set(flags) == {(0, 0, 0, 0, None, None, stamp(LATER), stamp(LATER))}


def test_a_saturday_enroll_is_due_monday_at_the_same_local_time(world: World) -> None:
    _, contact_id = world.contact()
    saturday = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)
    world.clock.set(saturday)

    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN))

    assert isinstance(found, NewEnrollment)
    [step_1] = draft_ids(world, found.enrollment_id)
    expected = step_due_at(saturday, 0, tz_of(world, contact_id))
    assert expected.date() == date(2026, 11, 23)  # Monday
    assert due(world, step_1) == stamp(expected)


def test_a_recontact_enrollment_gets_recontact_slots(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,), origin=EnrollmentOrigin.RECONTACT)

    assert [row[0] for row in slots(world, enrollment_id)] == ["recontact"]


def test_request_steps_adds_only_missing_steps(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    drafting = Drafting(world.clock, queue_on(world.clock))

    added = world.transact(lambda: drafting.request_steps(enrollment_id, [1, 3], Urgency.BULK))

    assert isinstance(added, tuple)
    assert len(added) == 1
    assert [row[2:5] for row in slots(world, enrollment_id)] == [(1, 0, None), (3, 7, None)]
    assert slots(world, enrollment_id)[1][5] is None  # Step 3 waits for a sent Step
    again = world.transact(lambda: drafting.request_steps(enrollment_id, (1, 3), Urgency.SOON))
    assert again == ()


def test_request_steps_checks_its_arguments_before_any_write(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    _, _, ended_id = enrolled(world, steps=(1,))
    world.stop(ended_id)
    drafting = Drafting(world.clock, queue_on(world.clock))
    soon = Urgency.SOON
    before = world.counts()

    for error, call in [
        (TypeError, lambda: drafting.request_steps(3, (2,), soon)),  # type: ignore[arg-type]
        (TypeError, lambda: drafting.request_steps(enrollment_id, 2, soon)),  # type: ignore[arg-type]
        (TypeError, lambda: drafting.request_steps(enrollment_id, (True,), soon)),
        (TypeError, lambda: drafting.request_steps(enrollment_id, (2,), "soon")),  # type: ignore[arg-type]
        (ValueError, lambda: drafting.request_steps(enrollment_id, (), soon)),
        (ValueError, lambda: drafting.request_steps(enrollment_id, (3, 2), soon)),
        (ValueError, lambda: drafting.request_steps(enrollment_id, (5,), soon)),
        (ValueError, lambda: drafting.request_steps(enrollment_id, (0,), soon)),
        (LookupError, lambda: drafting.request_steps(UNKNOWN, (2,), soon)),
        (ValueError, lambda: drafting.request_steps(ended_id, (2,), soon)),
    ]:
        with pytest.raises(error):
            world.transact(call)
    assert world.counts() == before


def test_request_steps_refuses_an_enrollment_whose_sequence_version_moved_on(
    world: World,
) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    world.sql("UPDATE sequences SET version = version + 1")
    drafting = Drafting(world.clock, queue_on(world.clock))

    with pytest.raises(LookupError):
        world.transact(lambda: drafting.request_steps(enrollment_id, (2,), Urgency.SOON))


def test_a_blocked_request_writes_nothing(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    drafting = Drafting(world.clock, queue_on(world.clock))
    before = world.counts()

    with gate_answers(BLOCKED):
        found = world.transact(lambda: drafting.request_steps(enrollment_id, (2,), Urgency.SOON))

    assert found == BLOCKED
    assert world.counts() == before


def test_a_blocked_request_inside_enroll_rolls_the_whole_enroll_back(world: World) -> None:
    _, contact_id = world.contact()
    before = world.counts()

    with gate_answers(Allowed(), Allowed(), BLOCKED), pytest.raises(RuntimeError):
        world.enroll(contact_id, world.campaign(US_CAMPAIGN))

    assert world.counts() == before


# Sent Steps --------------------------------------------------------------------------------------


def test_each_sent_step_sets_the_next_due_time_and_the_last_completes(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    tz = tz_of(world, contact_id)
    step_1, step_2, step_3, step_4 = draft_ids(world, enrollment_id)
    friday = datetime(2026, 11, 20, 23, 30, tzinfo=UTC)

    send(world, step_1, friday)

    assert due(world, step_2) == stamp(step_due_at(friday, 3, tz))
    assert (due(world, step_3), due(world, step_4)) == (None, None)
    assert enrollment(world, enrollment_id) == ("active", None, None, stamp(friday), None)

    send(world, step_1, friday + timedelta(hours=2))  # a repeat writes nothing

    assert due(world, step_2) == stamp(step_due_at(friday, 3, tz))
    assert enrollment(world, enrollment_id) == ("active", None, None, stamp(friday), None)

    second = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    send(world, step_2, second)
    assert due(world, step_3) == stamp(step_due_at(second, 4, tz))  # day 7 - day 3
    third = datetime(2026, 11, 30, 15, 0, tzinfo=UTC)
    send(world, step_3, third)
    assert due(world, step_4) == stamp(step_due_at(third, 7, tz))  # day 14 - day 7
    events = len(world.events("enrollments", enrollment_id))

    last = datetime(2026, 12, 8, 15, 0, tzinfo=UTC)
    send(world, step_4, last)

    assert enrollment(world, enrollment_id) == ("completed", None, None, stamp(last), stamp(last))
    assert world.events("enrollments", enrollment_id)[events:] == [
        ("active", "completed", "completed", step_4, "system:sequencing", stamp(last))
    ]
    later = last + timedelta(days=1)
    send(world, step_4, later)  # a repeat on a completed Enrollment writes nothing

    assert enrollment(world, enrollment_id) == ("completed", None, None, stamp(last), stamp(last))
    assert len(world.events("enrollments", enrollment_id)) == events + 1


def test_a_terminal_next_slot_is_skipped(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    tz = tz_of(world, contact_id)
    step_1, step_2, step_3, step_4 = draft_ids(world, enrollment_id)
    world.sql("UPDATE drafts SET status = 'rejected' WHERE id = ?", (step_2,))

    send(world, step_1, LATER)

    assert due(world, step_2) is None
    assert due(world, step_3) == stamp(step_due_at(LATER, 7, tz))  # day 7 - day 0
    world.sql(
        "UPDATE drafts SET status = 'cancelled' WHERE id IN (?, ?)", (step_3, step_4)
    )
    later = LATER + timedelta(days=1)

    send(world, step_1, later)  # no pending Step after it: the Enrollment completes

    assert enrollment(world, enrollment_id)[0] == "completed"
    assert enrollment(world, enrollment_id)[4] == stamp(later)


def test_a_step_sent_while_paused_still_sets_the_next_due_time(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    step_1, step_2, *_ = draft_ids(world, enrollment_id)
    world.transact(lambda: world.sequencing.pause(enrollment_id, PauseCause.INBOUND_PENDING))

    send(world, step_1, LATER)

    assert due(world, step_2) == stamp(step_due_at(LATER, 3, tz_of(world, contact_id)))


def test_a_stopped_enrollment_only_records_the_send(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    step_1, step_2, *_ = draft_ids(world, enrollment_id)
    world.stop(enrollment_id)
    after = LATER + timedelta(hours=3)

    send(world, step_1, after)

    assert enrollment(world, enrollment_id) == (
        "stopped",
        None,
        "operator_stop",
        stamp(after),
        stamp(after),  # raised, so ended_at never comes before the last Step
    )
    assert due(world, step_2) is None


def test_the_only_requested_step_sent_completes_the_enrollment(world: World) -> None:
    _, _, enrollment_id = enrolled(world, steps=(1,))
    [step_1] = draft_ids(world, enrollment_id)

    send(world, step_1, LATER)

    assert enrollment(world, enrollment_id)[0] == "completed"


def test_a_message_for_an_unknown_draft_is_a_no_op(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    before = world.counts()
    row = enrollment(world, enrollment_id)

    send(world, UNKNOWN, LATER)

    assert world.counts() == before
    assert enrollment(world, enrollment_id) == row


def test_a_contact_without_a_timezone_leaves_the_next_step_unset(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    step_1, step_2, *_ = draft_ids(world, enrollment_id)
    world.sql("UPDATE contacts SET tz = NULL, market_id = NULL WHERE id = ?", (contact_id,))

    send(world, step_1, LATER)

    assert due(world, step_2) is None
    assert enrollment(world, enrollment_id)[3] == stamp(LATER)


# Lifecycle ---------------------------------------------------------------------------------------


def test_pause_and_resume(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    sequencing = world.sequencing

    assert world.transact(lambda: sequencing.pause(enrollment_id, PauseCause.OPERATOR))
    assert enrollment(world, enrollment_id)[:2] == ("paused", "operator")
    assert not world.transact(lambda: sequencing.pause(enrollment_id, PauseCause.OPERATOR))
    assert world.transact(lambda: sequencing.resume(enrollment_id))
    assert enrollment(world, enrollment_id)[:2] == ("active", None)
    assert not world.transact(lambda: sequencing.resume(enrollment_id))
    assert world.events("enrollments", enrollment_id)[1:] == [
        ("active", "paused", "operator", None, "operator", stamp(LATER)),
        ("paused", "active", "resumed", None, "operator", stamp(LATER)),
    ]
    world.transact(lambda: sequencing.pause(enrollment_id, PauseCause.UNCERTAIN_CLASS))
    world.transact(lambda: sequencing.resume(enrollment_id))
    assert [event[4] for event in world.events("enrollments", enrollment_id)[3:]] == [
        "system:sequencing",
        "system:sequencing",
    ]


def test_a_stopped_enrollment_never_pauses_or_resumes(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    world.transact(lambda: world.sequencing.pause(enrollment_id, PauseCause.OPERATOR))
    world.stop(enrollment_id)
    events = len(world.events("enrollments", enrollment_id))

    assert not world.transact(lambda: world.sequencing.resume(enrollment_id))
    assert not world.transact(lambda: world.sequencing.pause(enrollment_id, PauseCause.OPERATOR))
    assert enrollment(world, enrollment_id)[0] == "stopped"
    assert len(world.events("enrollments", enrollment_id)) == events


def test_lifecycle_commands_check_their_arguments(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    sequencing = world.sequencing

    for error, call in [
        (LookupError, lambda: sequencing.pause(UNKNOWN, PauseCause.OPERATOR)),
        (LookupError, lambda: sequencing.resume(UNKNOWN)),
        (LookupError, lambda: sequencing.delay_until(UNKNOWN, date(2026, 12, 1))),
        (TypeError, lambda: sequencing.pause(3, PauseCause.OPERATOR)),  # type: ignore[arg-type]
        (TypeError, lambda: sequencing.resume(None)),  # type: ignore[arg-type]
        (ValueError, lambda: sequencing.pause(enrollment_id, "nope")),  # type: ignore[arg-type]
        (TypeError, lambda: sequencing.pause(enrollment_id, None)),  # type: ignore[arg-type]
        (TypeError, lambda: sequencing.delay_until(enrollment_id, LATER)),
        (TypeError, lambda: sequencing.delay_until(enrollment_id, "2026-12-01")),  # type: ignore[arg-type]
    ]:
        with pytest.raises(error):
            world.transact(call)
    assert enrollment(world, enrollment_id)[0] == "active"


def test_delay_until_recomputes_the_next_due_step(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    tz = tz_of(world, contact_id)
    step_1, step_2, *_ = draft_ids(world, enrollment_id)
    sequencing = world.sequencing
    back = date(2026, 12, 2)

    first = world.transact(lambda: sequencing.delay_until(enrollment_id, back))

    assert first == step_due_at(LATER, 0, tz, not_before=back)  # from Step 1's creation
    assert due(world, step_1) == stamp(first)

    sent = datetime(2026, 12, 2, 15, 0, tzinfo=UTC)
    send(world, step_1, sent)
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (step_1,))  # as Epic 5 will
    later = date(2026, 12, 14)

    second = world.transact(lambda: sequencing.delay_until(enrollment_id, later))

    assert second == step_due_at(sent, 3, tz, not_before=later)
    assert due(world, step_2) == stamp(second)
    assert world.transact(lambda: sequencing.delay_until(enrollment_id, date(2026, 12, 3))) == (
        step_due_at(sent, 3, tz, not_before=date(2026, 12, 3))  # an earlier date: the plain rule
    )


def test_delay_until_of_an_ended_enrollment_is_none(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    [step_1, *_] = draft_ids(world, enrollment_id)
    before = due(world, step_1)
    world.stop(enrollment_id)

    found = world.transact(lambda: world.sequencing.delay_until(enrollment_id, date(2027, 1, 4)))
    assert found is None
    assert due(world, step_1) == before


def test_set_due_only_moves_a_pending_draft(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    step_1, step_2, *_ = draft_ids(world, enrollment_id)
    world.sql("UPDATE drafts SET status = 'rejected' WHERE id = ?", (step_2,))
    drafting = Drafting(world.clock, queue_on(world.clock))
    when = datetime(2026, 12, 1, 15, 0, tzinfo=UTC)

    assert world.transact(lambda: drafting.set_due(step_1, when))
    assert due(world, step_1) == stamp(when)
    assert not world.transact(lambda: drafting.set_due(step_2, when))
    assert due(world, step_2) is None
    for error, call in [
        (LookupError, lambda: drafting.set_due(UNKNOWN, when)),
        (TypeError, lambda: drafting.set_due(step_1, datetime(2026, 12, 1))),
        (TypeError, lambda: drafting.set_due(3, when)),  # type: ignore[arg-type]
    ]:
        with pytest.raises(error):
            world.transact(call)


# Cancel ------------------------------------------------------------------------------------------


def cancelled_events(world: World, enrollment_id: str) -> list[tuple[Any, ...]]:
    return [
        world.events("drafts", draft_id)[-1] for draft_id in draft_ids(world, enrollment_id)
    ]


CANCELLED = ("ready", "cancelled", "sequence_stopped")


def assert_cancelled(world: World, enrollment_id: str) -> None:
    assert {row[6] for row in slots(world, enrollment_id)} == {"cancelled"}
    for event in cancelled_events(world, enrollment_id):
        assert event[:5] == (*CANCELLED, enrollment_id, "system:approval")
    assert set(
        world.sql("SELECT status_at FROM drafts WHERE enrollment_id = ?", (enrollment_id,))
    ) == {(stamp(LATER),)}


def test_an_operator_stop_cancels_the_ready_drafts_and_leaves_an_approved_one(
    world: World,
) -> None:
    _, _, enrollment_id = enrolled(world)
    step_1, *rest = draft_ids(world, enrollment_id)
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (step_1,))

    world.stop(enrollment_id)

    assert [row[6] for row in slots(world, enrollment_id)] == ["approved"] + ["cancelled"] * 3
    for draft_id in rest:
        assert world.events("drafts", draft_id) == [
            (None, "ready", "requested", enrollment_id, "system:drafting", stamp(LATER)),
            (*CANCELLED, enrollment_id, "system:approval", stamp(LATER)),
        ]
    events = world.counts()["events"]

    world.stop(enrollment_id)  # a repeat: nothing more

    assert world.counts()["events"] == events


def test_a_suppression_cancels_the_ready_drafts(world: World) -> None:
    _, _, enrollment_id = enrolled(world, web="acmelaw.com")
    _, _, other_id = enrolled(world)

    with world.units.open():
        Compliance(world.clock).add_value("acmelaw.com")  # a domain entry: the sweep stops it

    assert enrollment(world, enrollment_id)[:3] == ("stopped", None, "suppressed")
    assert_cancelled(world, enrollment_id)
    assert {row[6] for row in slots(world, other_id)} == {"ready"}


def test_the_suppression_backstop_cancels_ready_drafts_of_ended_enrollments(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    # an Enrollment ended without its Drafts cancelled (as before this story)
    world.sql(
        "UPDATE enrollments SET status = 'completed', ended_at = ? WHERE id = ?",
        (stamp(LATER), enrollment_id),
    )
    approval = Approval(world.clock)

    with world.units.open():
        Compliance(world.clock).add_value("someone-else@other.com")

    assert_cancelled(world, enrollment_id)
    events = world.counts()["events"]
    world.transact(lambda: approval.cancel_pending((enrollment_id,)))  # idempotent
    assert world.counts()["events"] == events
    with pytest.raises(TypeError):
        world.transact(lambda: approval.cancel_pending(enrollment_id))  # type: ignore[arg-type]


def test_closing_the_lead_cancels_the_ready_drafts(world: World) -> None:
    lead_id, _, enrollment_id = enrolled(world)
    pipeline = Pipeline(world.clock)

    world.transact(lambda: pipeline.close(lead_id, PipelineStage.LOST, None))

    assert enrollment(world, enrollment_id)[2] == "lead_closed"
    assert_cancelled(world, enrollment_id)


def test_an_erasure_cancels_the_ready_drafts(world: World) -> None:
    _, contact_id, enrollment_id = enrolled(world)
    erasures = Erasures(world.clock, Compliance(world.clock), queue=queue_on(world.clock))

    world.transact(lambda: erasures.erase_contact(contact_id, None, "retention"))

    assert enrollment(world, enrollment_id)[2] == "contact_erased"
    assert_cancelled(world, enrollment_id)


# Capacity ----------------------------------------------------------------------------------------


def follow_ups(world: World, day: date) -> dict[str, int]:
    return dict(world.read(lambda: sequencing_api._follow_ups_due(day)))


def test_follow_ups_due_counts_unsent_follow_ups_of_live_enrollments(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    _, _, stopped_id = enrolled(world)
    step_1, step_2, *_ = draft_ids(world, enrollment_id)
    [inbox] = [str(row[0]) for row in world.sql("SELECT id FROM sending_inboxes")]
    assert follow_ups(world, date(2026, 11, 18)) == {}  # Step 1 is no follow-up
    friday = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    send(world, step_1, friday)
    stopped_1, stopped_2, *_ = draft_ids(world, stopped_id)
    send(world, stopped_1, friday)
    # approved, so the stop leaves it pending: only the live-Enrollment filter drops it
    world.sql("UPDATE drafts SET status = 'approved' WHERE id = ?", (stopped_2,))
    world.stop(stopped_id)
    assert world.sql("SELECT status FROM drafts WHERE id = ?", (stopped_2,)) == [("approved",)]
    step_2_due = datetime.fromisoformat(str(due(world, step_2)).replace("Z", "+00:00"))

    assert follow_ups(world, step_2_due.date() - timedelta(days=1)) == {}
    assert follow_ups(world, step_2_due.date()) == {inbox: 1}
    assert follow_ups(world, step_2_due.date() + timedelta(days=5)) == {inbox: 1}  # overdue
    world.sql("UPDATE drafts SET status = 'sent' WHERE id = ?", (step_2,))
    assert follow_ups(world, step_2_due.date()) == {}


def test_follow_ups_due_uses_the_sender_timezone_day(world: World) -> None:
    _, _, enrollment_id = enrolled(world)
    _, step_2, *_ = draft_ids(world, enrollment_id)
    [inbox] = [str(row[0]) for row in world.sql("SELECT id FROM sending_inboxes")]
    # 03:00 UTC on Tuesday is still Monday evening in Los Angeles
    world.sql(
        "UPDATE drafts SET due_at = ? WHERE id = ?",
        (stamp(datetime(2026, 11, 24, 3, 0, tzinfo=UTC)), step_2),
    )
    assert follow_ups(world, date(2026, 11, 23)) == {}  # UTC while the setting is unset
    with world.units.open():
        Setup(world.clock).save({"sender_timezone": "America/Los_Angeles"})

    assert follow_ups(world, date(2026, 11, 23)) == {inbox: 1}


# The table ---------------------------------------------------------------------------------------


def test_the_table_has_no_stalled_column(world: World) -> None:
    columns = {str(row[1]) for row in world.sql("PRAGMA table_info(drafts)")}

    assert not any("stall" in column for column in columns)
    assert "day_offset" in columns


@pytest.mark.parametrize(
    ("assignments", "constraint"),
    [
        ("kind = 'other'", "ck_drafts_kind"),
        ("status = 'stalled'", "ck_drafts_status"),
        ("step_no = 0", "ck_drafts_step_no"),
        ("day_offset = -1", "ck_drafts_day_offset"),
        ("current_rev = 0", "ck_drafts_current_rev"),
        ("day_offset = NULL", "ck_drafts_step_slot"),
        ("step_no = 2", "UNIQUE"),
    ],
)
def test_the_checks_refuse_a_bad_row(world: World, assignments: str, constraint: str) -> None:
    _, _, enrollment_id = enrolled(world)
    [step_1, *_] = draft_ids(world, enrollment_id)

    with pytest.raises(Exception, match=constraint):
        world.sql(f"UPDATE drafts SET {assignments} WHERE id = ?", (step_1,))


def test_a_reply_draft_needs_no_enrollment(world: World) -> None:
    world.sql(
        "INSERT INTO drafts (id, kind, created_at, updated_at) VALUES (?, 'reply', ?, ?)",
        (UNKNOWN, stamp(LATER), stamp(LATER)),
    )

    assert world.sql("SELECT status, generic FROM drafts WHERE id = ?", (UNKNOWN,)) == [
        ("ready", 0)
    ]

