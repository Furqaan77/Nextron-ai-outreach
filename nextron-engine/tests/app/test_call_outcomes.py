"""Story 9.4's Call Outcomes (FR-69, FR-45, AD-4) through ``Pipeline`` on a temporary database at
head: each I/O matrix row at the service level (the due read with its delay setting, held,
no-show at ``call_booked`` and at ``call_held``, a Lead past the call or closed, corrections, a
booking that isn't the Lead's or is superseded), the ``events`` rows each writes (never a
value), nothing written on a refusal, a recorded booking no longer pending, and the erasure hook
redacting ``reason`` and ``next_step`` on the Lead's and merged Leads' bookings."""

from datetime import date, timedelta
from typing import Any

import pytest

from nextron.app.pipeline.api import (
    ERASURE,
    OPERATOR,
    NotThisLeadsBooking,
    OutcomeSaved,
    Pipeline,
)
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.setup.api import Setup
from nextron.domain.call_outcomes import (
    CallResult,
    Outcome,
    OutcomeForm,
    OutcomeRefused,
    PriceReaction,
)
from nextron.domain.erasure import REDACTED, Coverage
from nextron.domain.notifications import ContactErased
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.reply_classes import ObjectionType
from nextron.domain.settings_schema import Key
from nextron.web.lead_pipeline import typed_from
from tests.app.test_pipeline_manual import (
    STAMP,
    dump,
    events,
    last_event,
    run,
    set_stage,
    stage,
)
from tests.app.test_suppression import LATER, World, world  # noqa: F401 (the fixture)

S = PipelineStage
HELD = Outcome(
    outcome=CallResult.HELD,
    qualified=True,
    audit_proposed=True,
    audit_price=2_500_000_000,
    price_reaction=PriceReaction.PUSHED_BACK,
    objections=(ObjectionType.NO_BUDGET,),
    next_step="Send the audit scope",
    next_step_on=date(2026, 11, 25),
)
NO_SHOW = Outcome(outcome=CallResult.NO_SHOW)
OUTCOME_COLUMNS = (
    "outcome, qualified, reason, next_step, next_step_on, audit_proposed, audit_price, "
    "objections, price_reaction, recorded_at, recorded_by"
)


def outcome_row(world: World, booking: str) -> tuple[Any, ...]:
    [row] = world.sql(f"SELECT {OUTCOME_COLUMNS} FROM call_outcomes WHERE id = ?", (booking,))
    return row


@pytest.fixture
def lead(world: World) -> str:
    return world.lead()


@pytest.fixture
def pat(world: World, lead: str) -> str:
    return world.contact(lead, name="Pat Lee", email="pat.lee@firm.co.uk")


def book(world: World, lead: str, contact: str, minutes_ago: int = 61) -> str:
    """A booking scheduled ``minutes_ago`` before the clock's now; the Lead at ``call_booked``."""
    if stage(world, lead) != "call_booked":
        set_stage(world, lead, S.REPLIED)
    at = LATER - timedelta(minutes=minutes_ago)
    return run(world, lambda p: p.book_call(lead, contact, at, "Europe/London", actor=OPERATOR))


def due(world: World) -> list[tuple[str, str, str]]:
    found = world.read(lambda: Pipeline(world.clock).call_outcomes_due())
    return [(each.booking_id, each.lead_id, each.lead_name) for each in found]


def operator(
    world: World, lead: str, booking: str, outcome: Outcome | OutcomeForm
) -> OutcomeSaved:
    """The use case with ``outcome`` as the form would post it."""
    form = typed_from(outcome) if isinstance(outcome, Outcome) else outcome
    pipeline = Pipeline(world.clock)
    with world.units.open():
        return pipeline.operator_record_call_outcome(lead, booking, form)


# The due prompt ----------------------------------------------------------------------------------


def test_a_booking_is_due_after_its_time_plus_the_delay(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat, minutes_ago=61)

    assert due(world) == [(booking, lead, "Firm LLP")]


def test_a_booking_59_minutes_ago_is_not_due_yet(world: World, lead: str, pat: str) -> None:
    book(world, lead, pat, minutes_ago=59)

    assert due(world) == []


def test_the_delay_setting_decides(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat, minutes_ago=31)
    with world.units.open():
        Setup(world.clock).save({Key.CALL_OUTCOME_PROMPT_DELAY: "PT30M"})

    assert due(world) == [(booking, lead, "Firm LLP")]


def test_a_closed_or_recorded_lead_does_not_nag(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    set_stage(world, lead, S.LOST)
    assert due(world) == []

    set_stage(world, lead, S.CALL_BOOKED)
    run(world, lambda p: p.record_call_outcome(booking, NO_SHOW, actor=OPERATOR))
    assert due(world) == []


def test_a_merged_lead_does_not_nag(world: World, lead: str, pat: str) -> None:
    book(world, lead, pat)
    survivor = world.lead("other.co.uk")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, lead))

    assert due(world) == []


# Recording -------------------------------------------------------------------------------------


def test_held_moves_call_booked_to_call_held(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    mark = last_event(world)

    assert operator(world, lead, booking, HELD) is OutcomeSaved.RECORDED

    assert stage(world, lead) == "call_held"
    assert outcome_row(world, booking) == (
        "held",
        1,
        None,
        "Send the audit scope",
        "2026-11-25",
        1,
        2_500_000_000,
        "no_budget",
        "pushed_back",
        STAMP,
        "operator",
    )
    assert events(world, after=mark) == [
        ("call_outcomes", booking, "booked", "held", "call_outcome", None, "operator", STAMP),
        ("leads", lead, "call_booked", "call_held", "call_outcome", booking, "operator", STAMP),
    ]
    assert due(world) == []  # the prompt clears


def test_no_show_at_call_booked_keeps_the_stage(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    mark = last_event(world)

    assert operator(world, lead, booking, NO_SHOW) is OutcomeSaved.RECORDED

    assert stage(world, lead) == "call_booked"
    assert outcome_row(world, booking) == (
        "no_show",
        *([None] * 8),
        STAMP,
        "operator",
    )
    assert events(world, after=mark) == [
        ("call_outcomes", booking, "booked", "no_show", "call_outcome", None, "operator", STAMP)
    ]
    # recorded: no longer pending, so a rebooking never supersedes it
    assert world.read(lambda: pipeline_queries.lead_pipeline(lead)).booking is None  # type: ignore[union-attr]


def test_no_show_at_call_held_goes_back_to_call_booked(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    set_stage(world, lead, S.CALL_HELD)
    mark = last_event(world)

    operator(world, lead, booking, NO_SHOW)

    assert stage(world, lead) == "call_booked"
    assert events(world, after=mark)[1] == (
        "leads", lead, "call_held", "call_booked", "call_outcome", booking, "operator", STAMP
    )


@pytest.mark.parametrize("past", [S.AUDIT_PROPOSED, S.LOST])
def test_a_lead_past_the_call_or_closed_keeps_its_stage(
    world: World, lead: str, pat: str, past: PipelineStage
) -> None:
    booking = book(world, lead, pat)
    set_stage(world, lead, past)
    mark = last_event(world)

    operator(world, lead, booking, HELD)

    assert stage(world, lead) == str(past)
    assert outcome_row(world, booking)[0] == "held"
    assert [row[0] for row in events(world, after=mark)] == ["call_outcomes"]


# Corrections -----------------------------------------------------------------------------------


def test_a_correction_writes_one_row_per_changed_field(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    operator(world, lead, booking, HELD)
    mark = last_event(world)
    corrected = Outcome(
        outcome=CallResult.HELD,
        qualified=False,
        reason="Pat Lee has no budget this year",
        audit_proposed=True,
        audit_price=2_500_000_000,
        price_reaction=PriceReaction.PUSHED_BACK,
        objections=(ObjectionType.NO_BUDGET, ObjectionType.USES_OTHER_TOOL),
        next_step="Send the audit scope",
        next_step_on=date(2026, 11, 25),
    )

    assert operator(world, lead, booking, corrected) is OutcomeSaved.CORRECTED

    assert stage(world, lead) == "call_held"
    row = outcome_row(world, booking)
    assert row[1:3] == (0, "Pat Lee has no budget this year")
    assert row[7] == "no_budget,uses_other_tool"
    assert row[9:] == (STAMP, "operator")  # the first recording is kept
    assert events(world, after=mark) == [
        ("call_outcomes", booking, "held", "held", cause, None, "operator", STAMP)
        for cause in ("corrected_qualified", "corrected_reason", "corrected_objections")
    ]


def test_a_correction_to_no_show_never_recomputes_the_stage(
    world: World, lead: str, pat: str
) -> None:
    booking = book(world, lead, pat)
    operator(world, lead, booking, HELD)
    mark = last_event(world)

    operator(world, lead, booking, NO_SHOW)

    assert stage(world, lead) == "call_held"
    found = events(world, after=mark)
    assert {row[2:4] for row in found} == {("held", "no_show")}
    assert [row[4] for row in found] == [
        "corrected_outcome",
        "corrected_qualified",
        "corrected_audit_proposed",
        "corrected_audit_price",
        "corrected_price_reaction",
        "corrected_objections",
        "corrected_next_step",
        "corrected_next_step_on",
    ]


def test_an_unchanged_correction_writes_nothing(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    operator(world, lead, booking, HELD)
    before = dump(world)

    assert operator(world, lead, booking, HELD) is OutcomeSaved.UNCHANGED

    assert dump(world) == before


# Refusals --------------------------------------------------------------------------------------


def test_another_leads_or_a_superseded_booking_is_refused(
    world: World, lead: str, pat: str
) -> None:
    first = book(world, lead, pat)
    second = book(world, lead, pat)  # a rebooking supersedes the first
    other = world.lead("other.co.uk")
    before = dump(world)

    for lead_id, booking in (
        (lead, first),
        (other, second),
        (lead, "01JD3QZ8X5V9K2M7N4P6R8T0AC"),
        (lead, "nope"),
    ):
        with pytest.raises(NotThisLeadsBooking):
            operator(world, lead_id, booking, HELD)
        with pytest.raises(NotThisLeadsBooking):  # the booking is checked before the fields
            operator(world, lead_id, booking, OutcomeForm(outcome="held"))

    assert dump(world) == before


def test_an_incomplete_form_for_the_leads_booking_is_refused_unwritten(
    world: World, lead: str, pat: str
) -> None:
    booking = book(world, lead, pat)
    before = dump(world)

    with pytest.raises(OutcomeRefused) as refused:
        operator(world, lead, booking, OutcomeForm(outcome="held"))

    assert refused.value.field == "qualified"
    assert dump(world) == before


def test_the_commands_check_before_writing(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    before = dump(world)

    with pytest.raises(TypeError):
        run(world, lambda p: p.record_call_outcome(booking, "held", actor=OPERATOR))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="actor"):
        run(world, lambda p: p.record_call_outcome(booking, HELD, actor=" "))
    with pytest.raises(LookupError):
        run(world, lambda p: p.record_call_outcome("nope", HELD, actor=OPERATOR))
    with pytest.raises(ValueError, match="no current outcome"):
        run(world, lambda p: p.correct_call_outcome(booking, HELD, actor=OPERATOR))
    assert dump(world) == before

    run(world, lambda p: p.record_call_outcome(booking, HELD, actor=OPERATOR))
    after = dump(world)
    with pytest.raises(ValueError, match="isn't pending"):
        run(world, lambda p: p.record_call_outcome(booking, HELD, actor=OPERATOR))
    assert dump(world) == after


def test_a_failing_later_step_leaves_no_row(world: World, lead: str, pat: str) -> None:
    booking = book(world, lead, pat)
    before = dump(world)
    pipeline = Pipeline(world.clock)

    def record_then_fail() -> None:
        pipeline.record_call_outcome(booking, HELD, actor=OPERATOR)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        world.transact(record_then_fail)

    assert dump(world) == before


# The panel's read and erasure ------------------------------------------------------------------


def test_a_due_rebooking_takes_the_form_over_a_recorded_no_show(
    world: World, lead: str, pat: str
) -> None:
    first = book(world, lead, pat)
    operator(world, lead, first, NO_SHOW)
    assert stage(world, lead) == "call_booked"  # a no-show keeps the stage
    second = book(world, lead, pat, minutes_ago=10)  # rebooked, its time already past

    found = world.read(lambda: Pipeline(world.clock).lead_panel(lead))

    assert found is not None and found.outcome_booking is not None
    assert found.outcome_booking.id == second


def test_the_outcome_form_booking(world: World, lead: str, pat: str) -> None:
    def outcome_booking() -> str | None:
        found = world.read(lambda: Pipeline(world.clock).lead_panel(lead))
        assert found is not None
        return None if found.outcome_booking is None else found.outcome_booking.id

    assert outcome_booking() is None
    future = book(world, lead, pat, minutes_ago=-30)
    assert outcome_booking() is None  # not yet held
    world.sql("UPDATE call_outcomes SET scheduled_at = '2026-11-18T09:59:00.000000Z'")
    assert outcome_booking() == future  # the time has passed: no delay for the form
    run(world, lambda p: p.record_call_outcome(future, HELD, actor=OPERATOR))
    assert outcome_booking() == future  # now to correct it
    with pytest.raises(ValueError, match="aware"):
        world.read(lambda: pipeline_queries.lead_pipeline(lead, now=LATER.replace(tzinfo=None)))


def test_erasure_redacts_the_free_texts_of_the_leads_and_merged_leads_bookings(
    world: World, lead: str, pat: str
) -> None:
    assert ERASURE["call_outcomes"].coverage is Coverage.TOMBSTONED
    booking = book(world, lead, pat)
    not_qualified = Outcome(
        outcome=CallResult.HELD,
        qualified=False,
        reason="Pat Lee left",
        audit_proposed=False,
        price_reaction=PriceReaction.NOT_DISCUSSED,
        objections=(),
        next_step="Ask Pat Lee's successor",
    )
    run(world, lambda p: p.record_call_outcome(booking, not_qualified, actor=OPERATOR))
    merged = world.lead("old.co.uk")
    sam = world.contact(merged, name="Sam Roe", email="sam@old.co.uk")
    old = book(world, merged, sam)
    missed = Outcome(outcome=CallResult.NO_SHOW, next_step="Pat Lee again")
    run(world, lambda p: p.record_call_outcome(old, missed, actor=OPERATOR))
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead, merged))
    other = world.lead("other.co.uk")
    olive = world.contact(other, name="Olive Kept", email="olive@other.co.uk")
    kept = book(world, other, olive)
    run(world, lambda p: p.record_call_outcome(kept, not_qualified, actor=OPERATOR))

    run(world, lambda p: p.erase_pii(ContactErased(pat, "dsr")))

    assert world.sql("SELECT id, reason, next_step FROM call_outcomes ORDER BY rowid") == [
        (booking, REDACTED, REDACTED),
        (old, None, REDACTED),
        (kept, "Pat Lee left", "Ask Pat Lee's successor"),
    ]
