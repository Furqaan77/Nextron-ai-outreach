"""Story 9.6's Offer validation rules (``nextron.domain.offer_validation``): what counts as
booked, held and no-show, the validation threshold, proposed and qualified shares rounded half
up, accepted audits as distinct Leads with a held call, the price range and median (odd and
even counts), every price reaction shown, the top objections' order and ties, and the
checkpoint threshold."""

import pytest

from nextron.domain.call_outcomes import CallResult, PriceReaction
from nextron.domain.leads import LeadSource
from nextron.domain.offer_validation import (
    AUDITS_NEEDED,
    CHECKPOINT,
    ENGINE_SOURCES,
    HELD_CALLS_NEEDED,
    CallFact,
    build_report,
    engine_creation_causes,
    median,
    percent,
)
from nextron.domain.reply_classes import ObjectionType

O = ObjectionType
R = PriceReaction
DOLLAR = 1_000_000


def held(
    lead: str = "L",
    *,
    qualified: bool = True,
    price: int | None = None,
    objections: tuple[ObjectionType, ...] = (),
    reaction: PriceReaction = R.NOT_DISCUSSED,
) -> CallFact:
    return CallFact(
        lead_id=lead,
        outcome=CallResult.HELD,
        qualified=qualified,
        audit_proposed=price is not None,
        audit_price=price,
        objections=objections,
        price_reaction=reaction,
    )


def test_the_thresholds_and_the_checkpoint_text() -> None:
    assert (HELD_CALLS_NEEDED, AUDITS_NEEDED) == (10, 2)
    assert CHECKPOINT == "Review the Offer's price and scope before the next Variant"


def test_every_lead_source_is_an_engine_source_with_its_creation_cause() -> None:
    assert ENGINE_SOURCES == frozenset(LeadSource)
    assert engine_creation_causes() == {f"created_from_{source}" for source in LeadSource}


@pytest.mark.parametrize(
    ("n", "d", "expected"),
    [(5, 12, 42), (1, 8, 13), (1, 200, 1), (1, 201, 0), (0, 10, 0), (10, 10, 100), (3, 0, 0)],
)
def test_percent_rounds_half_up(n: int, d: int, expected: int) -> None:
    assert percent(n, d) == expected


def test_median_odd_even_and_empty() -> None:
    assert median([]) is None
    assert median([3 * DOLLAR, 1 * DOLLAR, 2 * DOLLAR]) == 2 * DOLLAR
    assert median([4 * DOLLAR, 1 * DOLLAR, 2 * DOLLAR, 3 * DOLLAR]) == 2_500_000
    assert median([2_000 * DOLLAR, 3_001 * DOLLAR]) == 2_500_500_000


def test_booked_held_and_no_shows() -> None:
    facts = [
        held("A"),
        held("B"),
        CallFact("C", CallResult.NO_SHOW),
        CallFact("D"),  # pending: booked only
    ]

    report = build_report(facts, frozenset())

    assert (report.booked, report.held, report.no_shows) == (4, 2, 1)
    assert not report.validating
    assert not report.checkpoint  # never below 10 held calls


def test_the_empty_report() -> None:
    report = build_report([], frozenset())

    assert (report.booked, report.held, report.no_shows, report.accepted) == (0, 0, 0, 0)
    assert (report.proposed, report.proposed_percent, report.qualified_percent) == (0, 0, 0)
    assert report.price_low is report.price_high is report.price_median is None
    assert report.price_reactions == ((R.ACCEPTED, 0), (R.PUSHED_BACK, 0), (R.NOT_DISCUSSED, 0))
    assert report.top_objections == ()
    assert not report.validating and not report.checkpoint


def fixture() -> list[CallFact]:
    """The matrix fixture: 12 held, 5 proposed, objections no_budget x4, uses_other_tool x3,
    send_information x2."""
    objections = [
        (O.NO_BUDGET, O.USES_OTHER_TOOL),
        (O.NO_BUDGET, O.USES_OTHER_TOOL),
        (O.NO_BUDGET, O.SEND_INFORMATION),
        (O.NO_BUDGET,),
        (O.USES_OTHER_TOOL, O.SEND_INFORMATION),
    ] + [()] * 7
    prices = [2_000, 2_500, 2_500, 3_000, 2_500] + [None] * 7
    reactions = [R.ACCEPTED, R.PUSHED_BACK, R.PUSHED_BACK] + [R.NOT_DISCUSSED] * 9
    return [
        held(
            f"L{index}",
            qualified=index < 8,
            price=None if price is None else price * DOLLAR,
            objections=objections[index],
            reaction=reactions[index],
        )
        for index, price in enumerate(prices)
    ] + [CallFact("N", CallResult.NO_SHOW)]


def test_the_matrix_fixture() -> None:
    report = build_report(fixture(), frozenset({"L0", "not-held"}))

    assert (report.booked, report.held, report.no_shows) == (13, 12, 1)
    assert report.validating
    assert (report.proposed, report.proposed_percent) == (5, 42)
    assert report.accepted == 1
    assert (report.price_low, report.price_high, report.price_median) == (
        2_000 * DOLLAR,
        3_000 * DOLLAR,
        2_500 * DOLLAR,
    )
    assert report.price_reactions == ((R.ACCEPTED, 1), (R.PUSHED_BACK, 2), (R.NOT_DISCUSSED, 9))
    assert (report.qualified, report.qualified_percent) == (8, 67)
    assert report.top_objections == ((O.NO_BUDGET, 4), (O.USES_OTHER_TOOL, 3), (O.SEND_INFORMATION, 2))
    assert report.checkpoint


def test_two_accepted_audits_pass_the_checkpoint() -> None:
    report = build_report(fixture(), frozenset({"L0", "L1"}))

    assert report.accepted == 2
    assert not report.checkpoint


def test_accepted_counts_distinct_leads_with_a_held_call() -> None:
    facts = [held("A") for _ in range(9)] + [held("B"), CallFact("C", CallResult.NO_SHOW)]

    report = build_report(facts, frozenset({"A", "C"}))

    assert report.accepted == 1  # A once; C has no held call
    assert report.checkpoint


def test_exactly_ten_held_calls_validate() -> None:
    nine = [held(f"L{index}") for index in range(9)]
    assert not build_report(nine, frozenset()).validating
    assert build_report([*nine, held("L9")], frozenset()).validating


def test_no_proposals_leave_the_prices_unset() -> None:
    report = build_report([held(f"L{index}") for index in range(10)], frozenset())

    assert report.proposed == 0 and report.proposed_percent == 0
    assert report.price_low is report.price_high is report.price_median is None


def test_objection_ties_follow_the_objection_type_order_and_zero_is_left_out() -> None:
    facts = [
        held("A", objections=(O.USES_OTHER_TOOL,)),
        held("B", objections=(O.NO_BUDGET,)),
        held("C", objections=(O.USES_OTHER_TOOL, O.NO_BUDGET)),
    ]

    report = build_report(facts, frozenset())

    assert report.top_objections == ((O.NO_BUDGET, 2), (O.USES_OTHER_TOOL, 2))


def test_an_objection_counts_once_per_held_call_and_only_held_calls_count() -> None:
    facts = [
        held("A", objections=(O.NO_BUDGET, O.NO_BUDGET)),
        CallFact("B", CallResult.NO_SHOW, objections=(O.SEND_INFORMATION,)),
    ]

    assert build_report(facts, frozenset()).top_objections == ((O.NO_BUDGET, 1),)


def test_the_top_list_keeps_at_most_three() -> None:
    facts = [held("A", objections=tuple(ObjectionType))]

    assert len(build_report(facts, frozenset()).top_objections) == min(3, len(ObjectionType))
