"""``nextron.domain.restart`` (FR-63; Story 8.11): when a completed Sequence may restart, on which
Event, which Event times Eligibility reads, and whether a Draft cites the restart Event.

The frozen-``now`` matrix: too early (89 days) and the 90-day boundary, an old Event only, the
happy case, a stopped latest Enrollment (any cause), a completed ``restart`` Enrollment (a
Lead restarts once), ``not_now``/``lost``/``disqualified``/``replied`` stages, a live
Enrollment, Tier 3, retracted, ``score_only``, dead, Evidence-less, Attribute and future
Events; the newest-Event and smallest-id tie-breaks; argument types. A Lead with any ``restart``
Enrollment (``RestartFacts.restarted``) never restarts again, even when its latest Enrollment
is another Contact's ``operator_pick``. ``restart_event_cited`` refuses a bare ``str``.
``without_restart`` adds ``recontact_not_allowed`` once while the history holds an unanswered
Sequence (AD-21).
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from nextron.app.sequencing.api import ENROLLABLE_STAGES as SEQUENCING_ENROLLABLE
from nextron.domain.eligibility import (
    RECONTACT_TEXT,
    RECONTACT_WAIT,
    Decision,
    EndedSequence,
    Reason,
    ReasonKind,
)
from nextron.domain.restart import (
    ENROLLABLE_STAGES,
    RECONTACT_REASON,
    EventFacts,
    LatestEnrollment,
    RestartFacts,
    events_first_seen,
    qualifying_event,
    restart_event,
    restart_event_cited,
    without_restart,
)

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
LAST_STEP = NOW - timedelta(days=90)
DAYS_2, DAYS_9 = timedelta(days=2), timedelta(days=9)
ENDED = LAST_STEP
EVENT = EventFacts(
    signal_id="01JD3QZ8X5V9K2M7N4P6R8T0E1",
    signal_class="event",
    tier=1,
    score_only=False,
    event_date=None,
    first_seen_at=NOW - timedelta(days=5),
    retracted=False,
    evidence_ids=("01JD3QZ8X5V9K2M7N4P6R8T0V1",),
)
LATEST = LatestEnrollment(
    enrollment_id="01JD3QZ8X5V9K2M7N4P6R8T0N1",
    contact_id="01JD3QZ8X5V9K2M7N4P6R8T0C1",
    campaign_id="01JD3QZ8X5V9K2M7N4P6R8T0P1",
    origin="selection",
    status="completed",
    stop_cause=None,
    last_step_sent_at=LAST_STEP,
    ended_at=ENDED,
)
FACTS = RestartFacts(latest=LATEST, stage="contacted", live_enrollment=False, events=(EVENT,))


def test_the_happy_case_names_the_event() -> None:
    assert restart_event(FACTS, NOW) == EVENT.signal_id


def test_89_days_after_the_last_step_is_too_early_and_90_is_enough() -> None:
    early = replace(FACTS, latest=replace(LATEST, last_step_sent_at=NOW - timedelta(days=89)))
    assert restart_event(early, NOW) is None
    just = NOW - RECONTACT_WAIT + timedelta(microseconds=1)
    too_early = replace(FACTS, latest=replace(LATEST, last_step_sent_at=just))
    assert restart_event(too_early, NOW) is None
    exact = replace(FACTS, latest=replace(LATEST, last_step_sent_at=NOW - RECONTACT_WAIT))
    assert restart_event(exact, NOW) == EVENT.signal_id


def test_an_event_first_seen_before_or_when_the_sequence_ended_is_old() -> None:
    tier_2 = replace(EVENT, tier=2)  # a Tier 2 Event lives through day 90: only the end decides
    for seen in (ENDED - timedelta(days=1), ENDED):
        old = replace(FACTS, events=(replace(tier_2, first_seen_at=seen),))
        assert restart_event(old, NOW) is None
    after = replace(tier_2, first_seen_at=ENDED + timedelta(seconds=1))
    assert restart_event(replace(FACTS, events=(after,)), NOW) == EVENT.signal_id


@pytest.mark.parametrize(
    "stop_cause",
    ["reply", "manual_reply", "hard_bounce", "suppressed", "operator_stop", "lead_closed"],
)
def test_a_stopped_latest_enrollment_never_restarts(stop_cause: str) -> None:
    stopped = replace(FACTS, latest=replace(LATEST, status="stopped", stop_cause=stop_cause))
    assert restart_event(stopped, NOW) is None


def test_a_completed_row_with_a_stop_cause_or_no_step_or_no_enrollment_never_restarts() -> None:
    assert restart_event(replace(FACTS, latest=replace(LATEST, stop_cause="reply")), NOW) is None
    no_step = replace(LATEST, last_step_sent_at=None)
    assert restart_event(replace(FACTS, latest=no_step), NOW) is None
    assert restart_event(replace(FACTS, latest=replace(LATEST, ended_at=None)), NOW) is None
    assert restart_event(replace(FACTS, latest=None), NOW) is None
    for status in ("active", "paused"):
        assert restart_event(replace(FACTS, latest=replace(LATEST, status=status)), NOW) is None


@pytest.mark.parametrize("origin", ["selection", "operator_pick", "referral", "recontact"])
def test_a_completed_enrollment_of_any_other_origin_restarts(origin: str) -> None:
    facts = replace(FACTS, latest=replace(LATEST, origin=origin))
    assert restart_event(facts, NOW) == EVENT.signal_id


def test_a_completed_restart_never_restarts_again() -> None:
    later = NOW + timedelta(days=200)
    newer = replace(EVENT, first_seen_at=later - timedelta(days=1))
    ended = later - timedelta(days=100)
    latest = replace(LATEST, origin="restart", last_step_sent_at=ended, ended_at=ended)
    again = replace(FACTS, latest=latest, events=(newer,))
    assert restart_event(again, later) is None
    first_time = replace(again, latest=replace(latest, origin="selection"))
    assert restart_event(first_time, later) == EVENT.signal_id


@pytest.mark.parametrize(
    "stage",
    [
        None,
        "not_now",
        "lost",
        "disqualified",
        "replied",
        "interested",
        "call_booked",
        "won",
    ],
)
def test_a_lead_outside_the_enrollable_stages_never_restarts(stage: str | None) -> None:
    assert restart_event(replace(FACTS, stage=stage), NOW) is None


@pytest.mark.parametrize("stage", ["new", "queued", "contacted"])
def test_each_enrollable_stage_restarts(stage: str) -> None:
    assert restart_event(replace(FACTS, stage=stage), NOW) == EVENT.signal_id


def test_the_enrollable_stages_are_enrolls() -> None:
    assert ENROLLABLE_STAGES == SEQUENCING_ENROLLABLE


def test_a_live_enrollment_never_restarts() -> None:
    assert restart_event(replace(FACTS, live_enrollment=True), NOW) is None


@pytest.mark.parametrize(
    "event",
    [
        replace(EVENT, tier=3),
        replace(EVENT, retracted=True),
        replace(EVENT, score_only=True),
        replace(EVENT, evidence_ids=()),
        replace(EVENT, signal_class="attribute"),
        replace(EVENT, signal_class="nope"),
        replace(EVENT, tier=7),
        replace(EVENT, tier=True),  # type: ignore[arg-type]
        replace(EVENT, first_seen_at=NOW + timedelta(seconds=1)),  # not yet seen
        replace(EVENT, first_seen_at=NOW - timedelta(days=90 + 1), event_date=None),  # dead
        replace(EVENT, event_date=date(2026, 8, 20)),  # Tier 1, 90 days old by its date: dead
    ],
)
def test_a_tier_3_retracted_score_only_dead_or_bare_event_never_restarts(
    event: EventFacts,
) -> None:
    facts = replace(FACTS, latest=replace(LATEST, ended_at=NOW - timedelta(days=200)))
    assert restart_event(replace(facts, events=(event,)), NOW) is None
    assert not qualifying_event(event, NOW)
    assert events_first_seen((event,), NOW) == ()


def test_a_tier_2_event_lives_through_day_90() -> None:
    facts = replace(FACTS, latest=replace(LATEST, ended_at=NOW - timedelta(days=200)))
    event = replace(EVENT, tier=2, first_seen_at=NOW - timedelta(days=90))
    assert restart_event(replace(facts, events=(event,)), NOW) == EVENT.signal_id
    tier_1 = replace(event, tier=1)
    assert restart_event(replace(facts, events=(tier_1,)), NOW) is None


def test_the_newest_event_wins_and_a_tie_goes_to_the_smallest_id() -> None:
    older = replace(EVENT, signal_id="01JD3QZ8X5V9K2M7N4P6R8T0E0", first_seen_at=NOW - DAYS_9)
    newer_b = replace(EVENT, signal_id="01JD3QZ8X5V9K2M7N4P6R8T0EB", first_seen_at=NOW - DAYS_2)
    newer_a = replace(newer_b, signal_id="01JD3QZ8X5V9K2M7N4P6R8T0EA")
    facts = replace(FACTS, events=(older, newer_b, newer_a))
    assert restart_event(facts, NOW) == newer_a.signal_id
    tier_3_newest = replace(EVENT, signal_id="01JD3QZ8X5V9K2M7N4P6R8T0E9", tier=3)
    assert restart_event(replace(facts, events=(older, tier_3_newest)), NOW) == older.signal_id


def test_events_first_seen_are_the_qualifying_events_sorted() -> None:
    first = replace(EVENT, first_seen_at=NOW - timedelta(days=3))
    second = replace(EVENT, first_seen_at=NOW - timedelta(days=30))
    tier_3 = replace(EVENT, tier=3)
    assert events_first_seen((first, tier_3, second), NOW) == (
        second.first_seen_at,
        first.first_seen_at,
    )


def test_events_first_seen_refuses_a_naive_now_even_with_no_events() -> None:
    with pytest.raises(TypeError, match="aware"):
        events_first_seen((), NOW.replace(tzinfo=None))


def test_the_restart_event_is_cited_by_one_of_its_evidence() -> None:
    assert restart_event_cited(("a", "b"), ("b", "c"))
    assert not restart_event_cited(("a",), ("b", "c"))
    assert not restart_event_cited((), ("b",))
    assert not restart_event_cited(("a",), ())
    assert restart_event_cited(frozenset({"b"}), iter(["b"]))


@pytest.mark.parametrize(
    ("cited", "restart_ids"),
    [("ab", ("a", "b")), (("a",), "a"), (7, ("a",)), (("a",), None), (b"a", ("a",))],
)
def test_restart_event_cited_refuses_a_str_or_a_non_iterable(
    cited: object, restart_ids: object
) -> None:
    with pytest.raises(TypeError, match="iterable of ids"):
        restart_event_cited(cited, restart_ids)  # type: ignore[arg-type]


def test_a_lead_that_ever_restarted_never_restarts_again() -> None:
    # restart R completed, then another Contact's operator_pick completed: still restarted
    later = replace(LATEST, origin="operator_pick", contact_id="01JD3QZ8X5V9K2M7N4P6R8T0C2")
    assert restart_event(replace(FACTS, latest=later), NOW) == EVENT.signal_id
    assert restart_event(replace(FACTS, latest=later, restarted=True), NOW) is None
    assert FACTS.restarted is False


ELIGIBLE = Decision(True, "US basis", ())
UNANSWERED = EndedSequence(LAST_STEP, ENDED, False)
REPLIED = EndedSequence(LAST_STEP, ENDED, True)
PROVENANCE = Reason(ReasonKind.PROVENANCE_MISSING, "No source recorded.")
NOT_NOW = Reason(ReasonKind.NOT_NOW_UNDATED, "Not now.")


def test_without_restart_adds_recontact_not_allowed_once_for_an_unanswered_sequence() -> None:
    assert RECONTACT_REASON == Reason(ReasonKind.RECONTACT_NOT_ALLOWED, RECONTACT_TEXT)
    assert without_restart(ELIGIBLE, ()) is ELIGIBLE
    assert without_restart(ELIGIBLE, (REPLIED,)) is ELIGIBLE
    assert without_restart(ELIGIBLE, (REPLIED, UNANSWERED)) == Decision(
        False, None, (RECONTACT_REASON,)
    )
    failing = Decision(False, None, (PROVENANCE, NOT_NOW))
    added = without_restart(failing, (UNANSWERED,))
    assert set(added.kinds) == {
        ReasonKind.PROVENANCE_MISSING,
        ReasonKind.RECONTACT_NOT_ALLOWED,
        ReasonKind.NOT_NOW_UNDATED,
    }
    assert added.kinds.count(ReasonKind.RECONTACT_NOT_ALLOWED) == 1
    assert without_restart(added, (UNANSWERED,)) is added  # already there: kept as it is
    with pytest.raises(TypeError, match="Decision"):
        without_restart("eligible", ())  # type: ignore[arg-type]


def test_bad_arguments_raise_type_error() -> None:
    with pytest.raises(TypeError, match="aware"):
        restart_event(FACTS, NOW.replace(tzinfo=None))
    with pytest.raises(TypeError, match="RestartFacts"):
        restart_event(LATEST, NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="EventFacts"):
        qualifying_event(LATEST, NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="aware"):
        qualifying_event(EVENT, "now")  # type: ignore[arg-type]
