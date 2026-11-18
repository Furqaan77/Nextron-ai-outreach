"""Story 2.15's scoring domain (``nextron.domain.scoring``) with a fixed ``now``: the spec's
matrix, Decisions 2 to 6 and 8, the decay boundaries (days 30, 31, 89 and 90) and the caps."""

from dataclasses import replace
from fractions import Fraction
from datetime import UTC, date, datetime, timedelta

import pytest

from nextron.domain import seed_yaml
from nextron.domain.scoring import (
    Disqualified,
    Factor,
    FactorKind,
    Score,
    ScoringSignal,
    SizeEvidence,
    Tier,
    disqualifier,
    evidence_from_text,
    evidence_text,
    factors_from_text,
    factors_text,
    fit_score,
    lead_score,
    retier,
    score,
    signal_score,
    size_readings,
    tier,
    weights_sum_ok,
)
from nextron.domain.scoring import _event_factor  # noqa: PLC2701 - the exact decay it recovers
from nextron.domain.segments import SegmentDef, SizeClue, validate_segments
from tests.contracts.support import ENGINE_ROOT

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
TODAY = NOW.date()
SEEN = NOW - timedelta(days=200)


def _segments() -> dict[str, SegmentDef]:
    text = (ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8")
    return {each.key: each for each in validate_segments(seed_yaml.parse(text))}


SEGMENTS = _segments()
LAW = SEGMENTS["law_private_ai"]
OPS = SEGMENTS["ops_automation"]

_ids = iter(range(10_000))


def sid() -> str:
    return f"01JD{next(_ids):022d}"


def event(
    signal_type: str = "law_hiring_legal_ops",
    age: int = 10,
    tier_: int = 1,
    *,
    signal_id: str | None = None,
    first_seen: datetime = SEEN,
    open_: bool = True,
) -> ScoringSignal:
    return ScoringSignal(
        signal_id=signal_id or sid(),
        signal_type=signal_type,
        signal_class="event",
        tier=tier_,
        event_date=TODAY - timedelta(days=age),
        first_seen_at=first_seen,
        evidence_ids=(sid(),),
        open=open_,
    )


def attribute(
    signal_type: str = "law_contact_form_only",
    *,
    tier_: int = 3,
    first_seen: datetime = SEEN,
    signal_id: str | None = None,
) -> ScoringSignal:
    return ScoringSignal(
        signal_id=signal_id or sid(),
        signal_type=signal_type,
        signal_class="attribute",
        tier=tier_,
        event_date=None,
        first_seen_at=first_seen,
        evidence_ids=(sid(),),
    )


def lawyers(number: int, *, counted: bool = False, days: int = 0) -> SizeEvidence:
    return SizeEvidence(
        evidence_id=sid(),
        clue="lawyers_firm_wide",
        number=number,
        retrieved_at=NOW - timedelta(days=days),
        counted=counted,
    )


# The matrix -------------------------------------------------------------------------------------


def test_a_law_lead_in_range_with_a_live_tier_1_hiring_event_and_contact_form_only() -> None:
    hiring = event(age=10)
    form = attribute()
    size = lawyers(20)

    found = score(LAW, [hiring, form], [size], 50, 50, NOW)

    assert isinstance(found, Score)
    assert (found.fit, found.signal, found.lead, found.tier) == (65, 60, 63, Tier.A)
    assert found.fit_factors == (
        Factor(FactorKind.SIZE_IN_RANGE, "lawyers_firm_wide", 50.0, None, None, None, (
            size.evidence_id,
        )),
        Factor(
            FactorKind.ATTRIBUTE, "law_contact_form_only", 15.0, 15, 3, form.signal_id,
            form.evidence_ids,
        ),
    )
    assert found.signal_factors == (
        Factor(
            FactorKind.EVENT, "law_hiring_legal_ops", 60.0, 60, 1, hiring.signal_id,
            hiring.evidence_ids,
        ),
    )


@pytest.mark.parametrize(
    ("age", "points", "found_tier"),
    [
        (0, 60.0, Tier.A),
        (30, 60.0, Tier.A),
        (31, 59.0, Tier.A),
        (60, 30.0, Tier.A),
        (89, 1.0, Tier.A),
        (90, None, Tier.C),
    ],
)
def test_a_tier_1_event_decays_from_day_31_and_is_gone_at_day_90(
    age: int, points: float | None, found_tier: Tier
) -> None:
    hiring = event(age=age)

    value, factors = signal_score(LAW, [hiring], NOW)

    assert [factor.points for factor in factors] == ([] if points is None else [points])
    assert value == (0 if points is None else round(points))
    assert tier([hiring], NOW) is found_tier


def test_a_decayed_factor_keeps_its_exact_points_to_2_decimals() -> None:
    _, [factor] = signal_score(LAW, [event(age=59)], NOW)  # 60 * 31 / 60

    assert factor.points == 31.0
    _, [expansion] = signal_score(LAW, [event("law_expansion", 90, 2)], NOW)
    assert expansion.points == 35.0  # a Tier 2 Event keeps its weight through day 90
    _, [third] = signal_score(LAW, [event(age=88)], NOW)
    assert third.points == 2.0


def test_rounding_is_half_up_on_the_exact_sum() -> None:
    # two Tier 1 types at day 89: 1 point each
    value, _ = signal_score(
        LAW, [event(age=89), event("law_ai_confidentiality_post", 89)], NOW
    )

    assert value == 2
    assert lead_score(65, 60, 50, 50) == 63  # 62.5 rounds up
    assert lead_score(65, 60, 60, 40) == 63  # 63.0
    assert lead_score(1, 0, 50, 50) == 1  # 0.5 rounds up
    assert lead_score(0, 0, 50, 50) == 0


def test_two_events_of_one_type_count_once_the_stronger() -> None:
    older = event(age=60)
    newer = event(age=10)

    value, [factor] = signal_score(LAW, [older, newer], NOW)

    assert (value, factor.signal_id, factor.points) == (60, newer.signal_id, 60.0)


def test_a_tie_goes_to_the_younger_then_the_earlier_first_seen_then_the_smaller_id() -> None:
    young = event(age=5)
    old = event(age=20)  # both factor 1.0
    _, [factor] = signal_score(LAW, [old, young], NOW)
    assert factor.signal_id == young.signal_id

    first = event(age=5, first_seen=SEEN - timedelta(days=1))
    later = event(age=5)
    _, [factor] = signal_score(LAW, [later, first], NOW)
    assert factor.signal_id == first.signal_id

    small = event(age=5, signal_id="01JD0000000000000000000001")
    large = event(age=5, signal_id="01JD0000000000000000000002")
    _, [factor] = signal_score(LAW, [large, small], NOW)
    assert factor.signal_id == small.signal_id


def test_a_closed_posting_event_is_not_live() -> None:
    closed = event(age=10, open_=False)

    assert signal_score(LAW, [closed], NOW) == (0, ())
    assert tier([closed], NOW) is Tier.C


def test_the_signal_score_caps_at_100() -> None:
    found = [event(age=1), event("law_ai_confidentiality_post", 1), event("law_expansion", 1, 2)]

    value, factors = signal_score(LAW, found, NOW)

    assert (value, len(factors)) == (100, 3)  # 60 + 60 + 35


def test_the_fit_score_caps_at_100() -> None:
    ops = [
        attribute("ops_dynamics_gp", tier_=2),
        attribute("ops_manual_ordering"),
    ]
    big_law = replace(LAW, signal_types=tuple(
        replace(each, weight=90) if each.key == "law_contact_form_only" else each
        for each in LAW.signal_types
    ))

    value, _ = fit_score(big_law, size_readings([lawyers(20)]), [attribute()])

    assert value == 100  # 50 + 90
    assert fit_score(OPS, {}, ops)[0] == 70  # 25 + 30 + 15


def test_an_attribute_type_counts_once_its_earliest() -> None:
    early = attribute(first_seen=SEEN - timedelta(days=3))
    late = attribute()

    _, factors = fit_score(LAW, {}, [late, early])

    assert [factor.signal_id for factor in factors[1:]] == [early.signal_id]


def test_a_type_outside_the_segment_or_a_refused_class_or_tier_counts_for_nothing() -> None:
    foreign = event("ops_hiring_manual_entry")
    bad_class = replace(event(), signal_class="rumour")
    bad_tier = replace(event(), tier=4)
    wrong_class = replace(attribute(), signal_type="law_hiring_legal_ops")

    found = score(LAW, [foreign, bad_class, bad_tier, wrong_class], [], 50, 50, NOW)

    assert isinstance(found, Score)
    assert (found.signal, found.signal_factors, found.tier) == (0, (), Tier.C)
    assert [factor.kind for factor in found.fit_factors] == [FactorKind.SIZE_UNKNOWN]


def test_the_segment_weight_and_tier_apply_not_the_stored_tier() -> None:
    stored_as_tier_2 = event(tier_=2, age=60)  # the Segment says Tier 1, weight 60

    found = score(LAW, [stored_as_tier_2], [], 50, 50, NOW)

    assert isinstance(found, Score)
    assert (found.signal, found.tier) == (30, Tier.A)


def test_tier_b_for_a_tier_2_event_and_c_for_tier_3_or_nothing() -> None:
    assert tier([event("law_expansion", 10, 2)], NOW) is Tier.B
    assert tier([event("x", 10, 3)], NOW) is Tier.C
    assert tier([], NOW) is Tier.C
    assert tier([event("law_expansion", 10, 2), event(age=10)], NOW) is Tier.A
    assert tier([event("law_expansion", 91, 2)], NOW) is Tier.C


# Size and disqualifiers -------------------------------------------------------------------------


def test_three_lawyers_listed_and_no_fact_disqualifies_a_law_lead() -> None:
    count = lawyers(3, counted=True)

    found = score(LAW, [event()], [count], 50, 50, NOW)

    assert found == Disqualified("law_fewer_than_5_lawyers", (count.evidence_id,))


def test_a_fact_row_beats_the_count_and_the_latest_fact_wins() -> None:
    count = lawyers(3, counted=True)
    old = lawyers(60, days=5)
    new = lawyers(20, days=1)

    readings = size_readings([count, old, new])

    assert readings == {SizeClue.LAWYERS_FIRM_WIDE: new}
    assert disqualifier(LAW, readings) is None


def test_the_latest_retrieval_then_the_largest_id_is_the_reading() -> None:
    first = SizeEvidence("01JD0000000000000000000001", "staff", 10, NOW, False)
    second = SizeEvidence("01JD0000000000000000000002", "staff", 30, NOW, False)

    assert size_readings([second, first]) == {SizeClue.STAFF: second}


def test_a_count_only_reads_lawyers_firm_wide() -> None:
    staff_count = SizeEvidence(sid(), "staff", 3, NOW, True)

    assert size_readings([staff_count]) == {}


def test_disqualifiers_run_in_order_and_the_bounds_are_strict() -> None:
    assert disqualifier(LAW, size_readings([lawyers(5)])) is None
    assert disqualifier(LAW, size_readings([lawyers(50)])) is None
    big = lawyers(51)
    assert disqualifier(LAW, size_readings([big])) == Disqualified(
        "law_office_of_large_firm", (big.evidence_id,)
    )
    group = SizeEvidence(sid(), "group_staff", 1001, NOW, False)
    few = SizeEvidence(sid(), "staff", 19, NOW, False)
    assert disqualifier(OPS, size_readings([group, few])) == Disqualified(
        "ops_fewer_than_20_staff", (few.evidence_id,)
    )


def test_an_unknown_size_is_25_points_and_never_disqualifies() -> None:
    found = score(LAW, [], [], 50, 50, NOW)

    assert isinstance(found, Score)
    assert found.fit_factors == (
        Factor(FactorKind.SIZE_UNKNOWN, "lawyers", 25.0, None, None, None, ()),
    )
    assert found.fit == 25


def test_an_ops_lead_reads_usd_revenue_as_unknown() -> None:
    found = score(OPS, [], [SizeEvidence(sid(), "staff", 100, NOW, False)], 50, 50, NOW)

    assert isinstance(found, Score)
    assert found.fit_factors[0].kind is FactorKind.SIZE_UNKNOWN
    assert found.fit_factors[0].points == 25.0


def test_a_size_outside_the_range_is_0_points() -> None:
    narrow = replace(LAW, disqualifiers=())
    size = lawyers(70)

    value, [factor] = fit_score(narrow, size_readings([size]), [])

    assert (value, factor.kind, factor.points) == (0, FactorKind.SIZE_OUTSIDE, 0.0)


# Weights ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fit", "signal", "ok"),
    [(50, 50, True), (60, 40, True), (0, 100, True), (60, 30, False), (101, -1, False)],
)
def test_the_weights_must_sum_to_100(fit: int, signal: int, ok: bool) -> None:
    assert weights_sum_ok(fit, signal) is ok
    if not ok:
        with pytest.raises(ValueError, match="add up to 100"):
            lead_score(10, 10, fit, signal)


def test_a_bool_weight_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        weights_sum_ok(True, 99)  # noqa: FBT003


# Re-tiering -------------------------------------------------------------------------------------


def test_retier_drops_an_aged_out_event_and_keeps_the_fit() -> None:
    hiring = event(age=10)
    first = score(LAW, [hiring, attribute()], [lawyers(20)], 50, 50, NOW)
    assert isinstance(first, Score)
    later = NOW + timedelta(days=80)  # the Event is 90 days old

    found = retier(first.fit, first.fit_factors, first.signal_factors, [hiring], 50, 50, later)

    assert found.dropped == 1
    assert found.score == Score(65, 0, 33, Tier.C, first.fit_factors, ())


def test_retier_re_evaluates_the_stored_weight_and_tier() -> None:
    hiring = event(age=10)
    stored = Factor(FactorKind.EVENT, "law_hiring_legal_ops", 60.0, 40, 2, hiring.signal_id, ())

    found = retier(50, (), (stored,), [hiring], 60, 40, NOW + timedelta(days=50))

    assert found.dropped == 0
    assert (found.score.signal, found.score.tier) == (40, Tier.B)  # Tier 2 holds until day 90
    assert found.score.lead == 46


def test_retier_drops_a_missing_or_closed_event() -> None:
    hiring = event(age=10)
    stored = Factor(FactorKind.EVENT, "law_hiring_legal_ops", 60.0, 60, 1, hiring.signal_id, ())

    assert retier(25, (), (stored,), [], 50, 50, NOW).dropped == 1
    closed = replace(hiring, open=False)
    assert retier(25, (), (stored,), [closed], 50, 50, NOW).dropped == 1


# Stored form ------------------------------------------------------------------------------------


def test_the_factors_text_is_canonical_and_round_trips() -> None:
    found = score(LAW, [event(age=59), attribute()], [lawyers(20)], 50, 50, NOW)
    assert isinstance(found, Score)

    text = factors_text(found.fit_factors, found.signal_factors)

    assert " " not in text
    assert text.startswith('{"fit":[{"evidence_ids":[')
    assert factors_from_text(text) == (found.fit_factors, found.signal_factors)


@pytest.mark.parametrize(
    "text",
    ["", "[]", '{"fit":[]}', '{"fit":[{}],"signal":[]}', '{"fit":[],"signal":{}}', "{"],
)
def test_a_bad_factors_text_is_a_value_error(text: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        factors_from_text(text)


def test_the_evidence_text_round_trips() -> None:
    ids = (sid(), sid())

    assert evidence_from_text(evidence_text(ids)) == ids
    with pytest.raises(ValueError):  # noqa: PT011
        evidence_from_text("{}")


def test_a_wrong_argument_type_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        score("law", [], [], 50, 50, NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        tier([event()], datetime(2026, 1, 1))  # noqa: DTZ001
    with pytest.raises(TypeError):
        signal_score(LAW, ["not a signal"], NOW)  # type: ignore[list-item]
    assert date(2026, 1, 1) < TODAY


@pytest.mark.parametrize("age", range(121))
def test_the_recovered_decay_is_exactly_the_tier_1_fraction(age: int) -> None:
    exact = min(Fraction(1), max(Fraction(0), Fraction(90 - age, 60)))

    found = _event_factor(event(age=age), 1, NOW)

    assert found == (None if exact == 0 else exact)


@pytest.mark.parametrize(("age", "expected"), [(0, Fraction(1)), (90, Fraction(1)), (91, None)])
def test_the_recovered_decay_of_a_tier_2_event(age: int, expected: Fraction | None) -> None:
    assert _event_factor(event("law_expansion", age, 2), 2, NOW) == expected
