"""Story 4.10's "Ready to draft" order and badge (``nextron.domain.scoring``): Tier A by newest
live Event first (a tie, then a Tier A Lead without one, by the higher Lead Score), then Tier B
and Tier C by Lead Score, the Lead id last; Story 4.16's Signal Inbox refusals first, in the
same order among themselves; ``below_minimum``; and the argument checks."""

import pytest

from nextron.domain.scoring import ReadyCandidate, Tier, below_minimum, ready_order


def ids(candidates: tuple[ReadyCandidate, ...]) -> list[str]:
    return [candidate.lead_id for candidate in candidates]


def test_tier_a_by_newest_event_then_b_and_c_by_score_then_id() -> None:
    found = ready_order(
        [
            ReadyCandidate("c-high", Tier.C, 90, None),
            ReadyCandidate("b-low", Tier.B, 40, 3),
            ReadyCandidate("a-old", Tier.A, 99, 20),
            ReadyCandidate("a-new", Tier.A, 10, 2),
            ReadyCandidate("b-high", Tier.B, 80, None),
            ReadyCandidate("c-low", Tier.C, 20, 1),
            ReadyCandidate("a-none", Tier.A, 100, None),
        ]
    )
    assert ids(found) == ["a-new", "a-old", "a-none", "b-high", "b-low", "c-high", "c-low"]


def test_a_tier_a_tie_goes_to_the_higher_score_then_the_id() -> None:
    found = ready_order(
        [
            ReadyCandidate("02", Tier.A, 50, 5),
            ReadyCandidate("01", Tier.A, 50, 5),
            ReadyCandidate("03", Tier.A, 70, 5),
        ]
    )
    assert ids(found) == ["03", "01", "02"]


def test_equal_scores_in_tier_b_and_c_go_by_id() -> None:
    found = ready_order(
        [
            ReadyCandidate("z", Tier.B, 50, None),
            ReadyCandidate("y", Tier.C, 50, None),
            ReadyCandidate("a", Tier.B, 50, 1),
            ReadyCandidate("b", Tier.C, 50, None),
        ]
    )
    assert ids(found) == ["a", "z", "b", "y"]


def test_signal_inbox_refusals_come_first_in_the_usual_order() -> None:
    found = ready_order(
        [
            ReadyCandidate("a-new", Tier.A, 10, 2),
            ReadyCandidate("c-refused", Tier.C, 20, None, True),
            ReadyCandidate("b-high", Tier.B, 80, None),
            ReadyCandidate("a-refused", Tier.A, 30, 9, True),
            ReadyCandidate("b-refused", Tier.B, 90, None, True),
        ]
    )
    assert ids(found) == ["a-refused", "b-refused", "c-refused", "a-new", "b-high"]
    assert ReadyCandidate("x", Tier.A, 1, None).signal_inbox_refused is False


def test_the_order_is_a_tuple_and_empty_gives_empty() -> None:
    assert ready_order([]) == ()


@pytest.mark.parametrize(
    "candidate",
    [
        ReadyCandidate("a", "D", 50, None),  # type: ignore[arg-type]
        ReadyCandidate(1, Tier.A, 50, None),  # type: ignore[arg-type]
        ReadyCandidate("a", Tier.A, True, None),  # type: ignore[arg-type]
        ReadyCandidate("a", Tier.A, 50, 1.5),  # type: ignore[arg-type]
        ReadyCandidate("a", Tier.A, 50, None, 1),  # type: ignore[arg-type]
    ],
)
def test_a_bad_candidate_is_refused(candidate: ReadyCandidate) -> None:
    with pytest.raises((TypeError, ValueError)):
        ready_order([candidate, ReadyCandidate("b", Tier.A, 50, None)])


def test_a_non_candidate_is_refused() -> None:
    with pytest.raises(TypeError):
        ready_order([("a", Tier.A, 50, None)])  # type: ignore[list-item]


def test_below_minimum() -> None:
    assert below_minimum(49, 50)
    assert not below_minimum(50, 50)
    assert not below_minimum(0, 0)
    with pytest.raises(TypeError):
        below_minimum(True, 50)
    with pytest.raises(TypeError):
        below_minimum(50, "50")  # type: ignore[arg-type]
