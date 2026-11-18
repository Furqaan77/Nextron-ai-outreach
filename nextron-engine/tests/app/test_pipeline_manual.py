"""The Operator's manual Pipeline moves (Story 9.1, FR-45, FR-37, FR-63, AD-4) through
``Pipeline`` on a temporary database at head with the real subscriber registry: every row of the
I/O matrix at the service level (the moves offered, a forged automatic stage, an illegal move,
booking and rebooking a call, closing, reopening and refusing to, saving and clearing the deal
details), the ``events`` rows each writes (actor ``operator``, never an amount, a note, a date or
a timezone), nothing written on a refusal, the suppression entries untouched by a reopen and the
contactability gate still ``Blocked`` after reopening an opt-out Lead, the notes redacted by the
erasure hook, a merge moving the bookings, and ``queries.pipeline.lead_pipeline``."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from nextron.app.compliance import api as compliance_api
from nextron.app.pipeline.api import ERASURE, OPERATOR, Pipeline
from nextron.app.queries import pipeline as pipeline_queries
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.erasure import REDACTED, Coverage
from nextron.domain.manual_stages import StageRefusal, StageRefused
from nextron.domain.notifications import ContactErased
from nextron.domain.pipeline_stage import (
    AUTOMATIC_STAGES,
    CLOSED_STAGES,
    REOPEN,
    PipelineStage,
    StageCause,
    operator_moves,
)
from nextron.domain.suppression_entry import SuppressionSource
from tests.app.test_suppression import LATER, World, world  # noqa: F401 (the fixture)

S = PipelineStage
CALL = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
NOTE = "Pat Lee wants the audit\nbefore Q1"
EVENT_COLUMNS = "entity, entity_id, from_status, to_status, cause, cause_ref, actor, at"
STAMP = "2026-11-18T10:00:00.000000Z"  # LATER


def events(world: World, *, after: int = 0) -> list[tuple[Any, ...]]:
    return world.sql(f"SELECT {EVENT_COLUMNS} FROM events WHERE rowid > ? ORDER BY rowid", (after,))


def last_event(world: World) -> int:
    [(rowid,)] = world.sql("SELECT COALESCE(MAX(rowid), 0) FROM events")
    return int(rowid)


def stage(world: World, lead_id: str) -> str:
    [(found,)] = world.sql("SELECT pipeline_stage FROM leads WHERE id = ?", (lead_id,))
    return str(found)


def set_stage(world: World, lead_id: str, to: PipelineStage) -> None:
    world.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (str(to), lead_id))


def bookings(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT id, lead_id, contact_id, scheduled_at, prospect_tz, booked_at, booked_by, "
        "superseded_at FROM call_outcomes ORDER BY rowid"
    )


def dump(world: World) -> list[list[tuple[Any, ...]]]:
    return [
        world.sql(f"SELECT * FROM {table} ORDER BY rowid")
        for table in ("leads", "events", "call_outcomes", "suppression_entries")
    ]


def run[R](world: World, fn: Callable[[Pipeline], R]) -> R:
    pipeline = Pipeline(world.clock)
    return world.transact(lambda: fn(pipeline))


def refused(world: World, fn: Callable[[Pipeline], object]) -> StageRefusal:
    before = dump(world)
    with pytest.raises(StageRefused) as caught:
        run(world, fn)
    assert dump(world) == before  # nothing written
    return caught.value.reason


@pytest.fixture
def lead(world: World) -> str:
    return world.lead()


@pytest.fixture
def pat(world: World, lead: str) -> str:
    return world.contact(lead, name="Pat Lee", email="pat.lee@firm.co.uk")


# Moves -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", [S.AUDIT_PROPOSED, S.AUDIT_WON, S.BUILD_PROPOSED, S.WON])
def test_each_later_move_from_call_held(world: World, lead: str, target: PipelineStage) -> None:
    set_stage(world, lead, S.CALL_HELD)
    assert target in operator_moves(S.CALL_HELD)
    mark = last_event(world)

    run(world, lambda p: p.move(lead, target, actor=OPERATOR))

    assert stage(world, lead) == target
    assert events(world, after=mark) == [
        ("leads", lead, "call_held", str(target), "operator_move", None, "operator", STAMP)
    ]


@pytest.mark.parametrize("target", AUTOMATIC_STAGES)
def test_a_forged_automatic_stage_is_refused(
    world: World, lead: str, target: PipelineStage
) -> None:
    set_stage(world, lead, S.CALL_HELD)

    reason = refused(world, lambda p: p.move(lead, target, actor=OPERATOR))

    assert reason is StageRefusal.AUTOMATIC


def test_an_illegal_move_is_refused(world: World, lead: str) -> None:
    set_stage(world, lead, S.REPLIED)

    assert refused(world, lambda p: p.move(lead, S.WON, actor=OPERATOR)) is StageRefusal.ILLEGAL
    assert stage(world, lead) == "replied"


def test_a_move_to_call_booked_needs_the_call_form(world: World, lead: str) -> None:
    set_stage(world, lead, S.REPLIED)

    reason = refused(world, lambda p: p.move(lead, S.CALL_BOOKED, actor=OPERATOR))

    assert reason is StageRefusal.BOOKING_INCOMPLETE


@pytest.mark.parametrize("target", CLOSED_STAGES)
def test_close_by_hand(world: World, lead: str, target: PipelineStage) -> None:
    set_stage(world, lead, S.AUDIT_WON)
    mark = last_event(world)

    run(world, lambda p: p.move(lead, target, actor=OPERATOR))

    assert stage(world, lead) == target
    assert events(world, after=mark) == [
        ("leads", lead, "audit_won", str(target), f"closed_{target}", None, "operator", STAMP)
    ]
    reason = refused(world, lambda p: p.move(lead, S.LOST, actor=OPERATOR))
    assert reason is StageRefusal.ALREADY_CLOSED


def test_close_takes_a_cause_and_an_actor_and_checks_them(world: World, lead: str) -> None:
    set_stage(world, lead, S.REPLIED)
    for bad in (
        {"cause": "closed_lost"},  # a str, not a StageCause
        {"actor": " "},
        {"actor": 7},
    ):
        before = dump(world)
        with pytest.raises(ValueError):
            run(world, lambda p, kw=bad: p.close(lead, S.LOST, None, **kw))  # type: ignore[arg-type]
        assert dump(world) == before
    mark = last_event(world)

    closed = run(
        world,
        lambda p: p.close(
            lead, S.DISQUALIFIED, None, cause=StageCause.LEADS_MERGED, actor="system:scoring"
        ),
    )

    assert closed is True
    assert events(world, after=mark) == [
        ("leads", lead, "replied", "disqualified", "leads_merged", None, "system:scoring", STAMP)
    ]


def test_an_unknown_lead_and_bad_arguments_raise(world: World, lead: str) -> None:
    for unknown in ("01JD3QZ8X5V9K2M7N4P6R8T0AC", "not-a-ulid"):
        with pytest.raises(LookupError):
            run(world, lambda p, u=unknown: p.move(u, S.WON, actor=OPERATOR))
    with pytest.raises(TypeError):
        run(world, lambda p: p.move(lead, "won", actor=OPERATOR))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        run(world, lambda p: p.move(lead, S.WON, actor=" "))


# Booking a call --------------------------------------------------------------------------------


def test_book_a_call(world: World, lead: str, pat: str) -> None:
    set_stage(world, lead, S.REPLIED)
    mark = last_event(world)
    local = CALL.astimezone(timezone(timedelta(hours=5, minutes=30)))  # any aware time

    booking = run(world, lambda p: p.book_call(lead, pat, local, "Europe/London", actor=OPERATOR))

    assert stage(world, lead) == "call_booked"
    assert bookings(world) == [
        (
            booking,
            lead,
            pat,
            "2026-11-20T14:30:00.000000Z",
            "Europe/London",
            STAMP,
            "operator",
            None,
        )
    ]
    assert events(world, after=mark) == [
        ("call_outcomes", booking, None, "booked", "operator_move", None, "operator", STAMP),
        ("leads", lead, "replied", "call_booked", "operator_move", booking, "operator", STAMP),
    ]


def test_rebook_supersedes_the_pending_booking(world: World, lead: str, pat: str) -> None:
    set_stage(world, lead, S.INTERESTED)
    first = run(world, lambda p: p.book_call(lead, pat, CALL, "Europe/London", actor=OPERATOR))
    world.clock.set(LATER + timedelta(minutes=5))
    mark = last_event(world)

    second = run(
        world,
        lambda p: p.book_call(lead, pat, CALL + timedelta(days=1), "Asia/Kolkata", actor=OPERATOR),
    )

    later = "2026-11-18T10:05:00.000000Z"
    assert stage(world, lead) == "call_booked"
    [(_, *old), (_, *new)] = bookings(world)
    assert old[-1] == later  # superseded
    assert (new[2], new[3], new[-1]) == ("2026-11-21T14:30:00.000000Z", "Asia/Kolkata", None)
    assert events(world, after=mark) == [
        ("call_outcomes", first, "booked", "superseded", "rebooked", None, "operator", later),
        ("call_outcomes", second, None, "booked", "operator_move", None, "operator", later),
    ]
    with world.units.open(read_only=True):
        found = pipeline_queries.lead_pipeline(lead)
    assert found is not None and found.booking is not None
    assert found.booking.id == second


@pytest.mark.parametrize("at", [S.NEW, S.CONTACTED, S.CALL_HELD, S.WON, S.LOST])
def test_booking_from_a_stage_without_book_call_is_refused(
    world: World, lead: str, pat: str, at: PipelineStage
) -> None:
    set_stage(world, lead, at)

    reason = refused(
        world, lambda p: p.book_call(lead, pat, CALL, "Europe/London", actor=OPERATOR)
    )

    assert reason is StageRefusal.ILLEGAL


def test_booking_with_a_bad_contact_or_timezone_is_refused(
    world: World, lead: str, pat: str
) -> None:
    set_stage(world, lead, S.REPLIED)
    other_lead = world.lead("other.co.uk")
    stranger = world.contact(other_lead, name="Sam Roe", email="sam@other.co.uk")
    erased = world.contact(lead, name="Eve Gone", email="eve@firm.co.uk")
    world.sql(  # a tombstone, as the erasure leaves it
        "UPDATE contacts SET erased_at = ?, name = '[erased]', email = NULL, "
        "address_class = NULL WHERE id = ?",
        (STAMP, erased),
    )

    for contact in (stranger, erased, "01JD3QZ8X5V9K2M7N4P6R8T0AC", "nope"):
        reason = refused(
            world,
            lambda p, c=contact: p.book_call(lead, c, CALL, "Europe/London", actor=OPERATOR),
        )
        assert reason is StageRefusal.BAD_CONTACT
    reason = refused(world, lambda p: p.book_call(lead, pat, CALL, "Mars/Base", actor=OPERATOR))
    assert reason is StageRefusal.BAD_TIMEZONE
    with pytest.raises(ValueError):
        run(
            world,
            lambda p: p.book_call(
                lead, pat, CALL.replace(tzinfo=None), "Europe/London", actor=OPERATOR
            ),
        )


# Reopening -------------------------------------------------------------------------------------


def test_reopen_goes_back_to_the_stage_before_the_close(world: World, lead: str) -> None:
    set_stage(world, lead, S.INTERESTED)
    run(world, lambda p: p.move(lead, S.LOST, actor=OPERATOR))
    entries = world.entries()
    mark = last_event(world)

    reopened = run(world, lambda p: p.reopen(lead, actor=OPERATOR))

    assert reopened is S.INTERESTED and stage(world, lead) == "interested"
    assert events(world, after=mark) == [
        ("leads", lead, "lost", "interested", "reopened", None, "operator", STAMP)
    ]
    assert world.entries() == entries


def test_a_deal_save_on_a_closed_lead_is_no_close(world: World, lead: str) -> None:
    set_stage(world, lead, S.AUDIT_PROPOSED)
    run(world, lambda p: p.move(lead, S.DISQUALIFIED, actor=OPERATOR))
    run(world, lambda p: p.save_deal(lead, None, "dead", actor=OPERATOR))  # lost -> lost row

    assert run(world, lambda p: p.reopen(lead, actor=OPERATOR)) is S.AUDIT_PROPOSED


def test_reopen_is_refused_without_a_reopenable_close(world: World, lead: str) -> None:
    # not closed
    assert refused(world, lambda p: p.reopen(lead, actor=OPERATOR)) is StageRefusal.NOT_REOPENABLE
    # closed with no close row
    set_stage(world, lead, S.LOST)
    assert refused(world, lambda p: p.reopen(lead, actor=OPERATOR)) is StageRefusal.NOT_REOPENABLE
    # the stage before the close isn't open or parked
    world.sql(
        f"INSERT INTO events ({EVENT_COLUMNS}, id) VALUES ('leads', ?, 'disqualified', 'lost', "
        "'closed_lost', NULL, 'operator', ?, '01JD3QZ8X5V9K2M7N4P6R8T0AD')",
        (lead, STAMP),
    )
    assert refused(world, lambda p: p.reopen(lead, actor=OPERATOR)) is StageRefusal.NOT_REOPENABLE


def test_reopening_an_opt_out_lead_keeps_it_suppressed(world: World, lead: str, pat: str) -> None:
    set_stage(world, lead, S.INTERESTED)
    world.suppress(pat, SuppressionSource.ONE_CLICK)  # closes the Lead lost
    assert stage(world, lead) == "lost"
    entries = world.entries()

    run(world, lambda p: p.reopen(lead, actor=OPERATOR))

    assert stage(world, lead) == "interested"
    assert world.entries() == entries
    with world.units.open(read_only=True):
        answer = compliance_api.assert_contactable(ContactSubject(pat), "email", "cold")
        found = pipeline_queries.lead_pipeline(lead)
    assert isinstance(answer, Blocked)
    assert found is not None and found.close_ref == entries[0]["id"]


# Deal details ----------------------------------------------------------------------------------


def test_save_and_clear_the_deal_details(world: World, lead: str) -> None:
    set_stage(world, lead, S.CALL_HELD)
    mark = last_event(world)

    run(world, lambda p: p.save_deal(lead, 12_000_000_000, NOTE, actor=OPERATOR))

    assert world.sql("SELECT deal_value, notes FROM leads WHERE id = ?", (lead,)) == [
        (12_000_000_000, NOTE)
    ]
    written = events(world, after=mark)
    assert written == [
        ("leads", lead, "call_held", "call_held", "deal_details_saved", None, "operator", STAMP)
    ]
    text = repr(written)
    assert "12000" not in text and "Pat" not in text and "audit" not in text
    run(world, lambda p: p.save_deal(lead, None, None, actor=OPERATOR))
    assert world.sql("SELECT deal_value, notes FROM leads WHERE id = ?", (lead,)) == [
        (None, None)
    ]
    with world.units.open(read_only=True):
        board = pipeline_queries.board()
    [held] = [group for group in board.groups if group.stage is S.CALL_HELD]
    assert held.leads[0].entered_at is None  # same-stage rows never set the entry time


def test_a_bad_deal_is_refused(world: World, lead: str) -> None:
    for value in (-1_000_000, 1_500_000, 1_000_000_001 * 1_000_000):
        reason = refused(world, lambda p, v=value: p.save_deal(lead, v, None, actor=OPERATOR))
        assert reason is StageRefusal.BAD_DEAL_VALUE
    reason = refused(world, lambda p: p.save_deal(lead, None, "x" * 5_001, actor=OPERATOR))
    assert reason is StageRefusal.NOTES_TOO_LONG


# Erasure, merging and the panel's read -------------------------------------------------------


def test_erasure_redacts_the_leads_notes(world: World, lead: str, pat: str) -> None:
    assert ERASURE["leads"].coverage is Coverage.TOMBSTONED
    run(world, lambda p: p.save_deal(lead, 5_000_000, NOTE, actor=OPERATOR))
    other = world.lead("other.co.uk")
    run(world, lambda p: p.save_deal(other, None, "keep", actor=OPERATOR))

    run(world, lambda p: p.erase_pii(ContactErased(pat, "dsr")))
    run(world, lambda p: p.erase_pii(ContactErased("01JD3QZ8X5V9K2M7N4P6R8T0AC", "dsr")))

    assert world.sql("SELECT id, deal_value, notes FROM leads ORDER BY rowid") == [
        (lead, 5_000_000, REDACTED),
        (other, None, "keep"),
    ]


def test_a_merge_moves_the_bookings_to_the_survivor(world: World, lead: str) -> None:
    newer = world.lead("other.co.uk")
    sam = world.contact(newer, name="Sam Roe", email="sam@other.co.uk")
    set_stage(world, newer, S.REPLIED)
    booking = run(world, lambda p: p.book_call(newer, sam, CALL, "UTC", actor=OPERATOR))

    world.transact(lambda: world.discovery.merge(lead, newer))

    assert world.sql("SELECT id, lead_id FROM call_outcomes") == [(booking, lead)]
    assert stage(world, lead) == "call_booked"


def test_lead_pipeline(world: World, lead: str) -> None:
    set_stage(world, lead, S.LOST)
    with world.units.open(read_only=True):
        found = pipeline_queries.lead_pipeline(lead)
        missing = pipeline_queries.lead_pipeline("01JD3QZ8X5V9K2M7N4P6R8T0AC")
        bad = pipeline_queries.lead_pipeline("nope")

    assert found == pipeline_queries.LeadPipeline(
        stage=S.LOST, deal_value=None, notes=None, booking=None, moves=(REOPEN,), close_ref=None
    )
    assert missing is None and bad is None


def test_a_failing_later_step_leaves_no_row(world: World, lead: str, pat: str) -> None:
    set_stage(world, lead, S.REPLIED)
    before = dump(world)
    pipeline = Pipeline(world.clock)

    def book_then_fail() -> None:
        pipeline.book_call(lead, pat, CALL, "UTC", actor=OPERATOR)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        world.transact(book_then_fail)

    assert dump(world) == before
