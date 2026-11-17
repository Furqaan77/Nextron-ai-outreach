"""``nextron.domain.campaign`` (FR-3, AD-4, AD-5, NFR-3, Story 4.2): the Campaign machine (all
nine status × event pairs), ``needs_lia``, ``may_run``, ``lia_from_fields`` and ``stored_lia``,
the file form's value rules and rules with their exact paths and reasons, the references and the
angle-note floor, the form round trip and the command path. Standard library, pytest and
``nextron.domain`` only."""

import copy
import dataclasses
from datetime import date, datetime
from typing import Any

import pytest

from nextron.domain.campaign import (
    DATE_REASON,
    ENTER_A_VALUE,
    FUTURE_REASON,
    LIA_FIRST,
    LIA_RECORDED_CAUSE,
    MACHINE,
    MAX_VARIANTS,
    NEEDS_ACTIVE_VARIANT,
    PAIR_TAKEN,
    PRICE_NOTE,
    TOO_MANY_VARIANTS,
    TRANSITIONS,
    VARIANT_ADDED_CAUSE,
    VARIANT_NAME_TAKEN,
    CampaignCause,
    CampaignDef,
    CampaignEvent,
    CampaignRefused,
    CampaignStatus,
    InvalidCampaigns,
    Lia,
    StepAngleNote,
    VariantDef,
    VariantStatus,
    campaign_form,
    campaign_from_form,
    check_campaign_references,
    lia_from_fields,
    may_run,
    names_price,
    needs_lia,
    stored_lia,
    transition,
    validate_campaigns,
    variant_from_fields,
)
from nextron.domain.machines import IllegalTransition, Transition

TODAY = date(2026, 11, 16)
KEY_REASON = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)
MARKET_REASON = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 16 "
    "characters"
)
SEGMENT_KEYS = frozenset({"law_private_ai", "ops_automation"})
MARKET_COUNTRIES = {"us": "US", "uk": "GB"}
OFFER_KEYS = frozenset({"default"})
SEQUENCE_STEPS = {"default": 4, "short": 1}
UK = "campaigns[law_private_ai_uk]"  # the default campaign's path
LIA_PAIR = "the LIA reference and date must both be set or both be empty"
ABA_ON_GB = (
    "names ABA Formal Opinion 512, which only a US Campaign's note may (this Market is GB)"
)
SRA_ON_US = "names the SRA notice, which only a GB Campaign's note may (this Market is US)"


def variant(name: str = "A", status: str = "active", **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, **extra}


def campaign(key: str = "law_private_ai_uk", **changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "key": key,
        "name": "Law-PrivateAI × UK",
        "segment": "law_private_ai",
        "market": "uk",
        "offer": "default",
        "sequence": "default",
        "variants": [variant()],
    }
    found.update(changes)
    return found


def data(*campaigns: dict[str, Any]) -> dict[str, Any]:
    return {"campaigns": list(campaigns) if campaigns else [campaign()]}


def refused(value: object) -> tuple[str, str]:
    with pytest.raises(InvalidCampaigns) as caught:
        validate_campaigns(value)
    assert str(caught.value) == f"{caught.value.field}: {caught.value.reason}"
    return caught.value.field, caught.value.reason


def checked(*campaigns: dict[str, Any]) -> tuple[str, str] | None:
    """The first reference problem of the campaigns, or ``None``."""
    found = validate_campaigns(data(*campaigns))
    try:
        check_campaign_references(
            found, SEGMENT_KEYS, MARKET_COUNTRIES, OFFER_KEYS, SEQUENCE_STEPS
        )
    except InvalidCampaigns as caught:
        return caught.field, caught.reason
    return None


# The machine -------------------------------------------------------------------------------------

ALLOWED = {
    (CampaignStatus.DRAFT, CampaignEvent.ACTIVATE): (
        CampaignStatus.ACTIVE,
        CampaignCause.OPERATOR_ACTIVATE,
    ),
    (CampaignStatus.ACTIVE, CampaignEvent.PAUSE): (
        CampaignStatus.PAUSED,
        CampaignCause.OPERATOR_PAUSE,
    ),
    (CampaignStatus.PAUSED, CampaignEvent.RESUME): (
        CampaignStatus.ACTIVE,
        CampaignCause.OPERATOR_RESUME,
    ),
}


@pytest.mark.parametrize("status", list(CampaignStatus))
@pytest.mark.parametrize("event", list(CampaignEvent))
def test_all_nine_status_and_event_pairs(status: CampaignStatus, event: CampaignEvent) -> None:
    expected = ALLOWED.get((status, event))
    if expected is None:
        with pytest.raises(IllegalTransition) as caught:
            transition(status, event)
        assert caught.value.machine == "campaign"
        assert str(caught.value) == f"campaign: {event} is not allowed from {status}"
        return
    assert transition(status, event) == Transition(*expected)


def test_the_closed_enums_and_constants() -> None:
    assert [str(s) for s in CampaignStatus] == ["draft", "active", "paused"]
    assert [str(e) for e in CampaignEvent] == ["activate", "pause", "resume"]
    assert [str(c) for c in CampaignCause] == [
        "operator_activate",
        "operator_pause",
        "operator_resume",
    ]
    assert [str(s) for s in VariantStatus] == ["active", "inactive"]
    assert (MACHINE, LIA_RECORDED_CAUSE, VARIANT_ADDED_CAUSE) == (
        "campaign",
        "operator_record_lia",
        "operator_add_variant",
    )
    assert (MAX_VARIANTS, TOO_MANY_VARIANTS) == (2, "A Campaign can have at most 2 Variants")
    assert LIA_FIRST == "Record the legitimate-interests assessment reference and date first"


def test_the_transition_table_is_exactly_three_rows_and_read_only() -> None:
    assert {key: (value.to, value.cause) for key, value in TRANSITIONS.items()} == ALLOWED
    again = Transition(CampaignStatus.ACTIVE, CampaignCause.OPERATOR_ACTIVATE)
    with pytest.raises(TypeError):
        TRANSITIONS[(CampaignStatus.ACTIVE, CampaignEvent.ACTIVATE)] = again  # type: ignore[index]


@pytest.mark.parametrize("current", ["draft", ["draft"], None])
def test_a_value_that_is_no_status_is_an_illegal_transition(current: Any) -> None:
    if current == "draft":  # a plain text equal to the status is the status (StrEnum)
        assert transition(current, CampaignEvent.ACTIVATE).to == CampaignStatus.ACTIVE
        return
    with pytest.raises(IllegalTransition):
        transition(current, CampaignEvent.ACTIVATE)


# The LIA rule ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "expected"),
    [("GB", True), ("US", False), ("gb", False), (None, False), ("", False)],
)
def test_needs_lia_exactly_for_gb(country: object, expected: bool) -> None:
    assert needs_lia(country) is expected


LIA = Lia("LIA-2026-01", TODAY)


@pytest.mark.parametrize(
    ("status", "country", "lia", "expected"),
    [
        (CampaignStatus.ACTIVE, "US", None, True),
        (CampaignStatus.ACTIVE, "US", LIA, True),
        (CampaignStatus.ACTIVE, "GB", LIA, True),
        (CampaignStatus.ACTIVE, "GB", None, False),
        (CampaignStatus.ACTIVE, "GB", ("LIA-2026-01", TODAY), False),  # not a Lia
        (CampaignStatus.DRAFT, "US", None, False),
        (CampaignStatus.DRAFT, "GB", LIA, False),
        (CampaignStatus.PAUSED, "US", None, False),
        (CampaignStatus.PAUSED, "GB", LIA, False),
        ("active", "US", None, True),
        ("live", "US", None, False),
        (None, "US", None, False),
    ],
)
def test_may_run(status: object, country: object, lia: object, expected: bool) -> None:
    assert may_run(status, country, lia) is expected


def test_lia_from_fields_strips_and_accepts_today() -> None:
    assert lia_from_fields("  LIA-2026-01 ", " 2026-11-16 ", TODAY) == LIA
    assert lia_from_fields("LIA", "2020-02-29", TODAY) == Lia("LIA", date(2020, 2, 29))


@pytest.mark.parametrize(
    ("ref", "day", "problems"),
    [
        ("", "2026-11-16", {"lia_ref": ENTER_A_VALUE, "form": LIA_FIRST}),
        ("LIA", "  ", {"lia_date": ENTER_A_VALUE, "form": LIA_FIRST}),
        (" ", "", {"lia_ref": ENTER_A_VALUE, "lia_date": ENTER_A_VALUE, "form": LIA_FIRST}),
        ("LIA", "2026-11-17", {"lia_date": FUTURE_REASON}),
        ("LIA", "2026-02-30", {"lia_date": DATE_REASON}),
        ("LIA", "2026-2-3", {"lia_date": DATE_REASON}),
        ("LIA", "16/11/2026", {"lia_date": DATE_REASON}),
        ("LIA", "２０２６-１１-１６", {"lia_date": DATE_REASON}),  # full-width digits
        ("r" * 201, "2026-11-16", {"lia_ref": "must be at most 200 characters"}),
        (
            "LIA‮01",
            "2026-11-16",
            {"lia_ref": "must not hold control, format or line-separator characters"},
        ),
        (
            "r" * 201,
            "2026-11-17",
            {"lia_ref": "must be at most 200 characters", "lia_date": FUTURE_REASON},
        ),
        ("", "2026-02-30", {"lia_ref": ENTER_A_VALUE, "lia_date": DATE_REASON, "form": LIA_FIRST}),
    ],
)
def test_lia_from_fields_collects_every_problem(
    ref: str, day: str, problems: dict[str, str]
) -> None:
    with pytest.raises(CampaignRefused) as caught:
        lia_from_fields(ref, day, TODAY)

    assert caught.value.problems == problems
    assert caught.value.code == "campaign_refused"
    assert str(caught.value) == f"campaign refused: {', '.join(problems)}"  # names, never values


def test_a_200_character_reference_is_allowed() -> None:
    assert lia_from_fields("r" * 200, "2026-11-16", TODAY).ref == "r" * 200


@pytest.mark.parametrize(
    ("ref", "day", "today"),
    [
        (None, "2026-11-16", TODAY),
        ("LIA", None, TODAY),
        ("LIA", "2026-11-16", datetime(2026, 11, 16)),
    ],
)
def test_lia_from_fields_takes_texts_and_a_date(ref: Any, day: Any, today: Any) -> None:
    with pytest.raises(TypeError):
        lia_from_fields(ref, day, today)


def test_stored_lia() -> None:
    assert stored_lia(None, None, "campaigns[x]") is None
    assert stored_lia("LIA-2026-01", "2026-11-16", "campaigns[x]") == LIA
    assert stored_lia("LIA", "2099-01-01", "campaigns[x]") == Lia("LIA", date(2099, 1, 1))


@pytest.mark.parametrize(
    ("ref", "day", "expected"),
    [
        ("LIA", None, ("campaigns[x].lia_ref", LIA_PAIR)),
        (None, "2026-11-16", ("campaigns[x].lia_ref", LIA_PAIR)),
        (" LIA", "2026-11-16", ("campaigns[x].lia_ref", "must not start or end with whitespace")),
        ("LIA", "2026-13-01", ("campaigns[x].lia_date", "must be a real date written YYYY-MM-DD")),
        ("LIA", 20261116, ("campaigns[x].lia_date", "must be a real date written YYYY-MM-DD")),
    ],
)
def test_a_stored_lia_that_does_not_decode(ref: Any, day: Any, expected: tuple[str, str]) -> None:
    with pytest.raises(InvalidCampaigns) as caught:
        stored_lia(ref, day, "campaigns[x]")

    assert (caught.value.field, caught.value.reason) == expected


# The file form: valid ----------------------------------------------------------------------------


def test_a_valid_file_gives_each_campaign_in_order() -> None:
    angles = [{"step": 2, "note": "Lead with the client-data angle"}, {"step": 1, "note": "Brief"}]
    found = validate_campaigns(
        data(
            campaign(variants=[variant("A", step_angles=angles), variant("B", "inactive")]),
            campaign("ops_automation_us", segment="ops_automation", market="us", name="Ops"),
        )
    )

    assert found[0] == CampaignDef(
        key="law_private_ai_uk",
        name="Law-PrivateAI × UK",
        segment="law_private_ai",
        market="uk",
        offer="default",
        sequence="default",
        variants=(
            VariantDef(
                "A",
                VariantStatus.ACTIVE,
                (StepAngleNote(2, "Lead with the client-data angle"), StepAngleNote(1, "Brief")),
            ),
            VariantDef("B", VariantStatus.INACTIVE, ()),
        ),
    )
    assert [c.key for c in found] == ["law_private_ai_uk", "ops_automation_us"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        found[0].status = "active"  # type: ignore[attr-defined]


def test_fifty_campaigns_are_allowed_and_fifty_one_are_not() -> None:
    many = [campaign(f"c{i}", segment=f"s{i}") for i in range(51)]

    assert len(validate_campaigns({"campaigns": many[:50]})) == 50
    assert refused({"campaigns": many}) == ("campaigns", "must hold at most 50 items")


def test_the_error_codes() -> None:
    assert InvalidCampaigns.code == "invalid_campaigns"
    assert CampaignRefused.code == "campaign_refused"


# The file form: value rules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([], ("(top level)", "must be a mapping with exactly one field, campaigns")),
        ({}, ("campaigns", "missing")),
        ({"campaigns": [campaign()], "offers": []}, ("offers", "unknown field")),
        ({"campaigns": "x"}, ("campaigns", "must be a list")),
        ({"campaigns": []}, ("campaigns", "must hold at least 1 item")),
        ({"campaigns": ["x"]}, ("campaigns[0]", "must be a mapping")),
    ],
)
def test_the_top_level(value: object, expected: tuple[str, str]) -> None:
    assert refused(value) == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"key": "Law"}, ("campaigns[0].key", KEY_REASON)),
        ({"key": "a" * 32}, ("campaigns[0].key", KEY_REASON)),
        ({"name": ""}, (f"{UK}.name", "must not be blank")),
        ({"name": "n" * 101}, (f"{UK}.name", "must be at most 100 characters")),
        ({"segment": "Law"}, (f"{UK}.segment", KEY_REASON)),
        ({"market": "a" * 17}, (f"{UK}.market", MARKET_REASON)),
        ({"market": "UK"}, (f"{UK}.market", MARKET_REASON)),
        ({"offer": 1}, (f"{UK}.offer", KEY_REASON)),
        ({"sequence": "de fault"}, (f"{UK}.sequence", KEY_REASON)),
        ({"status": "active"}, (f"{UK}.status", "unknown field")),
        ({"lia_ref": "LIA"}, (f"{UK}.lia_ref", "unknown field")),
        ({"variants": "A"}, (f"{UK}.variants", "must be a list")),
        ({"variants": []}, (f"{UK}.variants", "must hold at least 1 item")),
    ],
)
def test_a_campaigns_fields(changes: dict[str, Any], expected: tuple[str, str]) -> None:
    assert refused(data(campaign(**changes))) == expected


@pytest.mark.parametrize(
    "missing", ["key", "name", "segment", "market", "offer", "sequence", "variants"]
)
def test_every_field_is_required(missing: str) -> None:
    found = campaign()
    del found[missing]
    shown = "0" if missing == "key" else "law_private_ai_uk"

    assert refused(data(found)) == (f"campaigns[{shown}].{missing}", "missing")


@pytest.mark.parametrize(
    ("variants", "expected"),
    [
        (
            [variant("A"), variant("B"), variant("C")],
            (f"{UK}.variants", TOO_MANY_VARIANTS),
        ),
        (
            [variant("A"), variant("a")],
            (f"{UK}.variants[1].name", "repeats an earlier Variant's name (a)"),
        ),
        (
            [variant("A", "inactive"), variant("B", "inactive")],
            (f"{UK}.variants", NEEDS_ACTIVE_VARIANT),
        ),
        (
            [variant("A", "paused")],
            (f"{UK}.variants[A].status", "must be one of active, inactive"),
        ),
        (
            [variant("v" * 41)],
            (f"{UK}.variants[0].name", "must be at most 40 characters"),
        ),
        (
            [{"name": "A"}],
            (f"{UK}.variants[A].status", "missing"),
        ),
        (
            [variant("A", weight=50)],
            (f"{UK}.variants[A].weight", "unknown field"),
        ),
        (
            [variant("A", step_angles=[{"step": 1, "note": "x"}] * 7)],
            (f"{UK}.variants[A].step_angles", "must hold at most 6 items"),
        ),
        (
            [variant("A", step_angles=[{"step": 1, "note": "x"}, {"step": 1, "note": "y"}])],
            (
                "campaigns[law_private_ai_uk].variants[A].step_angles[1].step",
                "repeats an earlier note's step (1)",
            ),
        ),
        (
            [variant("A", step_angles=[{"step": 0, "note": "x"}])],
            (f"{UK}.variants[A].step_angles[0].step", "must be from 1 to 6"),
        ),
        (
            [variant("A", step_angles=[{"step": 7, "note": "x"}])],
            (f"{UK}.variants[A].step_angles[0].step", "must be from 1 to 6"),
        ),
        (
            [variant("A", step_angles=[{"step": True, "note": "x"}])],
            (f"{UK}.variants[A].step_angles[0].step", "must be a whole number"),
        ),
        (
            [variant("A", step_angles=[{"step": 1, "note": "n" * 201}])],
            (
                "campaigns[law_private_ai_uk].variants[A].step_angles[0].note",
                "must be at most 200 characters",
            ),
        ),
        (
            [variant("A", step_angles=[{"step": 1}])],
            (f"{UK}.variants[A].step_angles[0].note", "missing"),
        ),
        (
            [variant("A", step_angles=[{"step": 1, "note": "x", "market": "uk"}])],
            (f"{UK}.variants[A].step_angles[0].market", "unknown field"),
        ),
    ],
)
def test_the_variant_rules(variants: list[dict[str, Any]], expected: tuple[str, str]) -> None:
    assert refused(data(campaign(variants=variants))) == expected


def test_the_limits_themselves_are_allowed() -> None:
    angles = [{"step": step, "note": "n" * 200} for step in range(1, 7)]
    variants = [variant("v" * 40, step_angles=angles), variant("B")]
    found = validate_campaigns(data(campaign(name="n" * 100, variants=variants)))

    assert len(found[0].variants[0].step_angles) == 6


def test_a_repeated_key_and_a_repeated_pair() -> None:
    assert refused(data(campaign(), campaign())) == (
        "campaigns[1].key",
        "repeats an earlier Campaign key (law_private_ai_uk)",
    )
    assert refused(data(campaign(), campaign("other"))) == ("campaigns[other]", PAIR_TAKEN)
    assert PAIR_TAKEN == "A Campaign already exists for this Segment and Market"


# References and the floor ------------------------------------------------------------------------


def test_the_shipped_shape_passes_the_references() -> None:
    angles = [{"step": 2, "note": "The SRA notice on client data"}]
    assert checked(
        campaign(variants=[variant(step_angles=angles)]),
        campaign(
            "law_private_ai_us",
            market="us",
            variants=[variant(step_angles=[{"step": 4, "note": "ABA Formal Opinion 512"}])],
        ),
    ) is None


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"segment": "dentists"},
            (f"{UK}.segment", "isn't the key of a Segment in this seed sync"),
        ),
        ({"market": "ca"}, (f"{UK}.market", "isn't the code of a Market in this seed sync")),
        ({"offer": "premium"}, (f"{UK}.offer", "isn't the key of an Offer in this seed sync")),
        ({"sequence": "long"}, (f"{UK}.sequence", "isn't the key of a Sequence in this seed sync")),
        (
            {"variants": [variant(step_angles=[{"step": 5, "note": "x"}])]},
            (
                "campaigns[law_private_ai_uk].variants[A].step_angles[0].step",
                "must be at most 4: the Sequence default has 4 Steps",
            ),
        ),
        (
            {"sequence": "short", "variants": [variant(step_angles=[{"step": 2, "note": "x"}])]},
            (
                "campaigns[law_private_ai_uk].variants[A].step_angles[0].step",
                "must be at most 1: the Sequence short has 1 Step",
            ),
        ),
    ],
)
def test_an_unknown_reference_or_step(changes: dict[str, Any], expected: tuple[str, str]) -> None:
    assert checked(campaign(**changes)) == expected


def first_field(*campaigns: dict[str, Any]) -> str:
    found = checked(*campaigns)
    assert found is not None
    return found[0]


def test_the_references_are_checked_in_order() -> None:
    assert first_field(campaign(segment="dentists", market="ca")) == f"{UK}.segment"
    assert first_field(campaign(market="ca", offer="x")) == f"{UK}.market"
    assert first_field(campaign(offer="x", sequence="y")) == f"{UK}.offer"
    other = campaign("b", segment="ops_automation", offer="x")
    assert first_field(campaign(), other) == "campaigns[b].offer"


@pytest.mark.parametrize(
    ("market", "note", "reason"),
    [
        ("uk", "ABA Formal Opinion 512 covers this", ABA_ON_GB),
        ("uk", "aba 512", ABA_ON_GB),
        ("us", "The SRA notice on AI", SRA_ON_US),
        ("us", "Solicitors Regulation Authority guidance", SRA_ON_US),
        ("us", "From $2,500", PRICE_NOTE),
        ("uk", "From £2,000", PRICE_NOTE),
        ("uk", "€3k", PRICE_NOTE),
        ("us", "2500 USD", PRICE_NOTE),
        ("uk", "about 2000 gbp", PRICE_NOTE),
        ("uk", "2.000 EUR", PRICE_NOTE),
    ],
)
def test_the_angle_note_floor(market: str, note: str, reason: str) -> None:
    angles = [{"step": 1, "note": "Fine"}, {"step": 2, "note": note}]
    found = campaign(market=market, variants=[variant(step_angles=angles)])

    assert checked(found) == (
        "campaigns[law_private_ai_uk].variants[A].step_angles[1].note",
        reason,
    )
    assert PRICE_NOTE == "An angle note can't name a price (FR-4)"


@pytest.mark.parametrize(
    "note",
    [
        "Europe-wide guidance",
        "Ask about their euro clients",
        "Reuse the casebook",
        "The audit trail",
    ],
)
def test_a_note_without_a_currency_word_passes(note: str) -> None:
    assert not names_price(note)
    assert checked(campaign(variants=[variant(step_angles=[{"step": 1, "note": note}])])) is None


# The form round trip -----------------------------------------------------------------------------


def test_the_form_round_trips() -> None:
    angles = [{"step": 2, "note": "Client-data angle"}]
    raw = campaign(variants=[variant("A", step_angles=angles), variant("B", "inactive")])
    [found] = validate_campaigns(data(raw))

    form = campaign_form(found)

    assert form == raw  # the file's own shape: step_angles only when non-empty
    assert campaign_from_form(copy.deepcopy(form)) == found


def test_from_form_validates_one_campaign() -> None:
    with pytest.raises(InvalidCampaigns) as caught:
        campaign_from_form(campaign(variants=[variant("A", "inactive")]))

    assert (caught.value.field, caught.value.reason) == (
        "campaigns[law_private_ai_uk].variants",
        NEEDS_ACTIVE_VARIANT,
    )


# The command path --------------------------------------------------------------------------------


def added(name: str, notes: tuple[StepAngleNote, ...] = (), **changes: Any) -> VariantDef:
    arguments: dict[str, Any] = {
        "existing_names": ["A"],
        "steps": 4,
        "country_code": "GB",
        **changes,
    }
    return variant_from_fields(name, notes, **arguments)


def problems(name: str, notes: tuple[StepAngleNote, ...] = (), **changes: Any) -> dict[str, str]:
    with pytest.raises(CampaignRefused) as caught:
        added(name, notes, **changes)
    return caught.value.problems


def test_a_new_variant_is_active_and_stripped() -> None:
    assert added(" B ", (StepAngleNote(2, " The SRA notice "),)) == VariantDef(
        "B", VariantStatus.ACTIVE, (StepAngleNote(2, "The SRA notice"),)
    )


@pytest.mark.parametrize(
    ("name", "notes", "changes", "expected"),
    [
        ("", (), {}, {"name": ENTER_A_VALUE}),
        ("a", (), {}, {"name": VARIANT_NAME_TAKEN}),
        ("v" * 41, (), {}, {"name": "must be at most 40 characters"}),
        ("C", (), {"existing_names": ["A", "B"]}, {"form": TOO_MANY_VARIANTS}),
        (
            "B",
            (StepAngleNote(5, "x"),),
            {},
            {"step_angles": "Step 5 is beyond the Sequence's 4 Steps"},
        ),
        ("B", (StepAngleNote(0, "x"),), {}, {"step_angles": "A step must be from 1 to 6"}),
        (
            "B",
            (StepAngleNote(True, "x"),),  # type: ignore[arg-type]
            {},
            {"step_angles": "A step must be a whole number"},
        ),
        (
            "B",
            (StepAngleNote(1, "x"), StepAngleNote(1, "y")),
            {},
            {"step_angles": "repeats an earlier note's step (1)"},
        ),
        ("B", (StepAngleNote(1, ""),), {}, {"step_angles": "note 1 must not be blank"}),
        ("B", (StepAngleNote(1, "Only $2,500"),), {}, {"step_angles": PRICE_NOTE}),
        (
            "B",
            (StepAngleNote(1, "ABA Formal Opinion 512"),),
            {},
            {"step_angles": ABA_ON_GB},
        ),
        (
            "B",
            tuple(StepAngleNote(step, "x") for step in range(1, 8)),
            {"steps": 6},
            {"step_angles": "must hold at most 6 items"},
        ),
        (
            "",
            (StepAngleNote(9, "x"),),
            {"existing_names": ["A", "B"]},
            {
                "name": ENTER_A_VALUE,
                "step_angles": "A step must be from 1 to 6",
                "form": TOO_MANY_VARIANTS,
            },
        ),
    ],
)
def test_a_new_variant_is_refused_with_every_problem(
    name: str, notes: tuple[StepAngleNote, ...], changes: dict[str, Any], expected: dict[str, str]
) -> None:
    assert problems(name, notes, **changes) == expected


def test_the_command_path_takes_step_angle_notes_and_a_text_name() -> None:
    with pytest.raises(TypeError):
        added("B", ({"step": 1, "note": "x"},))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        added(None)  # type: ignore[arg-type]
