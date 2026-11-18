"""The Lead Pipeline Stage machine (``nextron.domain.pipeline_stage``, AD-4): every (stage,
event) pair is its table row or raises, ``CREATION`` for every source, and ``reopen`` and
``merged`` on every pair of stages (decision 2), and ``advance`` (Story 4.8) on every pair of
stage and target."""

import itertools
from types import MappingProxyType

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.leads import LeadSource
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.pipeline_stage import (
    ADVANCEMENT,
    AUTOMATIC_EVENTS,
    CLOSED_STAGES,
    CREATION,
    MACHINE,
    OPEN_STAGES,
    PARKED_STAGES,
    TRANSITIONS,
    PipelineStage,
    StageCause,
    StageEvent,
    advance,
    merged,
    reopen,
    transition,
)

S = PipelineStage
OPEN = [
    "new",
    "queued",
    "contacted",
    "replied",
    "interested",
    "call_booked",
    "call_held",
    "audit_proposed",
    "audit_won",
    "build_proposed",
    "won",
]

# The Design Notes table, written out by hand: event -> (from stages, to, cause).
TABLE: dict[str, tuple[list[str], str, str]] = {
    "enroll": (["new"], "queued", "enrolled"),
    "send": (["new", "queued"], "contacted", "message_sent"),
    "reply": (["new", "queued", "contacted"], "replied", "reply_received"),
    "hot_reply": (["new", "queued", "contacted", "replied", "not_now"], "interested", "hot_reply"),
    "park": (OPEN[:-1], "not_now", "not_now_reply"),
    "recontact": (["not_now"], "queued", "recontact_due"),
    "book_call": (["replied", "interested", "not_now"], "call_booked", "operator_move"),
    "hold_call": (["call_booked"], "call_held", "call_outcome"),
    "no_show": (["call_held"], "call_booked", "call_outcome"),
    "propose_audit": (["call_held"], "audit_proposed", "operator_move"),
    "win_audit": (["call_held", "audit_proposed"], "audit_won", "operator_move"),
    "propose_build": (
        ["call_held", "audit_proposed", "audit_won"],
        "build_proposed",
        "operator_move",
    ),
    "win": (
        ["call_held", "audit_proposed", "audit_won", "build_proposed"],
        "won",
        "operator_move",
    ),
    "close_lost": ([*OPEN, "not_now"], "lost", "closed_lost"),
    "close_disqualified": ([*OPEN, "not_now"], "disqualified", "closed_disqualified"),
}
EXPECTED = {
    (stage, event): (to, cause)
    for event, (sources, to, cause) in TABLE.items()
    for stage in sources
}


def test_the_stages_are_the_ad4_list_in_order() -> None:
    assert [str(stage) for stage in PipelineStage] == [
        *OPEN,
        "not_now",
        "lost",
        "disqualified",
    ]
    assert [str(stage) for stage in OPEN_STAGES] == OPEN
    assert PARKED_STAGES == (S.NOT_NOW,)
    assert CLOSED_STAGES == (S.LOST, S.DISQUALIFIED)
    assert MACHINE == "pipeline_stage"


def test_the_events_and_causes_are_the_closed_lists() -> None:
    assert [str(event) for event in StageEvent] == list(TABLE)
    assert {str(cause) for cause in StageCause} == {
        *(cause for _, _, cause in TABLE.values()),
        *(f"created_from_{source}" for source in LeadSource),
        "reopened",
        "leads_merged",
    }


def test_the_table_is_exactly_the_design_notes_table() -> None:
    assert isinstance(TRANSITIONS, MappingProxyType)
    found = {
        (str(stage), str(event)): (str(change.to), str(change.cause))
        for (stage, event), change in TRANSITIONS.items()
    }
    assert found == EXPECTED
    assert all(
        isinstance(change.to, PipelineStage) and isinstance(change.cause, StageCause)
        for change in TRANSITIONS.values()
    )


@pytest.mark.parametrize(
    ("stage", "event"), list(itertools.product(PipelineStage, StageEvent))
)
def test_every_stage_and_event_pair_is_its_row_or_raises(
    stage: PipelineStage, event: StageEvent
) -> None:
    expected = EXPECTED.get((str(stage), str(event)))
    if expected is None:
        with pytest.raises(IllegalTransition) as caught:
            transition(stage, event)
        assert isinstance(caught.value, DomainError)
        assert caught.value.code == "illegal_transition"
        assert caught.value.message == f"pipeline_stage: {event} is not allowed from {stage}"
    else:
        change = transition(stage, event)
        assert (str(change.to), str(change.cause)) == expected


def test_no_event_leaves_a_closed_stage() -> None:
    for stage, event in itertools.product(CLOSED_STAGES, StageEvent):
        with pytest.raises(IllegalTransition):
            transition(stage, event)


@pytest.mark.parametrize("bad", [None, "nope", ["new"], 7])
def test_transition_refuses_what_is_no_stage_or_event(bad: object) -> None:
    with pytest.raises(IllegalTransition):
        transition(bad, StageEvent.SEND)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        transition(S.NEW, bad)  # type: ignore[arg-type]


def test_creation_has_one_row_per_source() -> None:
    assert isinstance(CREATION, MappingProxyType)
    assert set(CREATION) == set(LeadSource)
    for source in LeadSource:
        assert CREATION[source] == Transition(S.NEW, StageCause(f"created_from_{source}"))
    assert [str(CREATION[source].cause) for source in LeadSource] == [
        "created_from_csv",
        "created_from_signal_inbox",
        "created_from_places",
        "created_from_companies_house",
        "created_from_job_aggregator",
    ]


@pytest.mark.parametrize(
    ("current", "previous"), list(itertools.product(PipelineStage, PipelineStage))
)
def test_reopen_on_every_pair(current: PipelineStage, previous: PipelineStage) -> None:
    if current in CLOSED_STAGES and previous not in CLOSED_STAGES:
        assert reopen(current, previous) == Transition(previous, StageCause.REOPENED)
    else:
        with pytest.raises(IllegalTransition) as caught:
            reopen(current, previous)
        assert caught.value.message == f"pipeline_stage: reopen is not allowed from {current}"


@pytest.mark.parametrize("bad", [None, "nope", ["lost"]])
def test_reopen_refuses_what_is_no_stage(bad: object) -> None:
    with pytest.raises(IllegalTransition):
        reopen(bad, S.NEW)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        reopen(S.LOST, bad)  # type: ignore[arg-type]


RANK = [
    "new",
    "queued",
    "contacted",
    "replied",
    "not_now",
    "interested",
    "call_booked",
    "call_held",
    "audit_proposed",
    "audit_won",
    "build_proposed",
    "won",
]


def expected_merge(survivor: str, loser: str) -> str | None:
    """Decision 2, written out: the survivor's new stage, or None when it keeps its own."""
    closed = {"lost", "disqualified"}
    if survivor in closed:
        return None
    if loser in closed:
        return loser
    return loser if RANK.index(loser) > RANK.index(survivor) else None


def test_advancement_ranks_not_now_just_after_replied() -> None:
    assert [str(stage) for stage in ADVANCEMENT] == RANK


@pytest.mark.parametrize(
    ("survivor", "loser"), list(itertools.product(PipelineStage, PipelineStage))
)
def test_merged_on_every_pair(survivor: PipelineStage, loser: PipelineStage) -> None:
    expected = expected_merge(str(survivor), str(loser))
    change = merged(survivor, loser)

    if expected is None:
        assert change is None
    else:
        assert change == Transition(PipelineStage(expected), StageCause.LEADS_MERGED)


def test_merged_examples() -> None:
    assert merged(S.NEW, S.CONTACTED) == Transition(S.CONTACTED, StageCause.LEADS_MERGED)
    assert merged(S.WON, S.DISQUALIFIED) == Transition(S.DISQUALIFIED, StageCause.LEADS_MERGED)
    assert merged(S.LOST, S.DISQUALIFIED) is None  # two closed stages keep the survivor's
    assert merged(S.DISQUALIFIED, S.WON) is None
    assert merged(S.REPLIED, S.NOT_NOW) == Transition(S.NOT_NOW, StageCause.LEADS_MERGED)
    assert merged(S.NOT_NOW, S.INTERESTED) == Transition(S.INTERESTED, StageCause.LEADS_MERGED)
    assert merged(S.INTERESTED, S.NOT_NOW) is None
    assert merged(S.QUEUED, S.QUEUED) is None  # a tie


@pytest.mark.parametrize("bad", [None, "nope", 3])
def test_merged_refuses_what_is_no_stage(bad: object) -> None:
    with pytest.raises(IllegalTransition):
        merged(bad, S.NEW)  # type: ignore[arg-type]
    with pytest.raises(IllegalTransition):
        merged(S.NEW, bad)  # type: ignore[arg-type]


# advance (Story 4.8) ------------------------------------------------------------------------------

TARGETS = {"queued": "enroll", "contacted": "send", "replied": "reply", "interested": "hot_reply"}


def expected_advance(current: str, target: str) -> tuple[str, str] | None:
    """The spec's rule, written out: closed and parked stay (but Not Now on a hot Reply), and so
    does a stage at or past the target; otherwise the target's event row."""
    if current in ("lost", "disqualified"):
        return None
    if current == "not_now":
        return ("interested", "hot_reply") if target == "interested" else None
    order = [str(stage) for stage in ADVANCEMENT]
    if order.index(current) >= order.index(target):
        return None
    return EXPECTED[(current, TARGETS[target])]


def test_the_automatic_targets_are_the_four_moves() -> None:
    assert {str(k): str(v) for k, v in AUTOMATIC_EVENTS.items()} == TARGETS
    assert isinstance(AUTOMATIC_EVENTS, MappingProxyType)


@pytest.mark.parametrize(
    ("current", "target"), list(itertools.product(PipelineStage, [S(t) for t in TARGETS]))
)
def test_advance_on_every_stage_and_target(current: PipelineStage, target: PipelineStage) -> None:
    found = advance(current, target)
    expected = expected_advance(str(current), str(target))

    if expected is None:
        assert found is None
    else:
        assert found == Transition(S(expected[0]), StageCause(expected[1]))


def test_advance_examples() -> None:
    assert advance(S.NEW, S.QUEUED) == Transition(S.QUEUED, StageCause.ENROLLED)
    assert advance(S.QUEUED, S.QUEUED) is None
    assert advance(S.CONTACTED, S.QUEUED) is None
    assert advance(S.LOST, S.QUEUED) is None
    assert advance(S.NOT_NOW, S.QUEUED) is None  # the recontact event, not advance
    assert advance(S.NOT_NOW, S.INTERESTED) == Transition(S.INTERESTED, StageCause.HOT_REPLY)
    assert advance(S.NEW, S.INTERESTED) == Transition(S.INTERESTED, StageCause.HOT_REPLY)
    assert advance("new", "queued") == Transition(S.QUEUED, StageCause.ENROLLED)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "target", [S.NEW, S.CALL_BOOKED, S.WON, S.LOST, S.NOT_NOW, "nope", None, 3, []]
)
def test_advance_refuses_a_target_without_an_automatic_move(target: object) -> None:
    with pytest.raises(ValueError, match="no automatic move"):
        advance(S.NEW, target)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["nope", None, 3, []])
def test_advance_refuses_what_is_no_stage(bad: object) -> None:
    with pytest.raises(IllegalTransition):
        advance(bad, S.QUEUED)  # type: ignore[arg-type]
