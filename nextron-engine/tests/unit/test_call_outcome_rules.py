"""Story 9.4's pure rules (``nextron.domain.call_outcomes``): every decision-3 rule of
``check_outcome`` (required fields, ignored fields, price, date and text checks, unknown codes),
the ``Outcome`` invariants, its stored columns round trip, and the correction causes."""

from datetime import date, datetime

import pytest

from nextron.domain.call_outcomes import (
    CORRECTABLE_FIELDS,
    CallResult,
    Outcome,
    OutcomeForm,
    OutcomeRefusal,
    OutcomeRefused,
    PriceReaction,
    changed_fields,
    check_outcome,
    correction_cause,
)
from nextron.domain.reply_classes import ObjectionType

HELD = OutcomeForm(
    outcome="held",
    qualified="yes",
    audit_proposed="yes",
    audit_price=" 2500 ",
    price_reaction="accepted",
    objections=("uses_other_tool", "no_budget"),
    next_step="Send\r\nthe scope",
    next_step_on="2026-11-25",
)


def refusal(form: OutcomeForm) -> tuple[str, OutcomeRefusal]:
    with pytest.raises(OutcomeRefused) as caught:
        check_outcome(form)
    return caught.value.field, caught.value.reason


def with_(**changes: object) -> OutcomeForm:
    values = {name: getattr(HELD, name) for name in OutcomeForm.__dataclass_fields__}
    values.update(changes)
    return OutcomeForm(**values)  # type: ignore[arg-type]


def test_a_held_form() -> None:
    assert check_outcome(HELD) == Outcome(
        outcome=CallResult.HELD,
        qualified=True,
        reason=None,
        audit_proposed=True,
        audit_price=2_500_000_000,
        price_reaction=PriceReaction.ACCEPTED,
        objections=(ObjectionType.NO_BUDGET, ObjectionType.USES_OTHER_TOOL),
        next_step="Send\nthe scope",
        next_step_on=date(2026, 11, 25),
    )


def test_a_no_show_ignores_every_held_field() -> None:
    form = with_(outcome="no_show", qualified="", reason="x" * 600, audit_price="abc")

    assert check_outcome(form) == Outcome(
        outcome=CallResult.NO_SHOW, next_step="Send\nthe scope", next_step_on=date(2026, 11, 25)
    )


def test_ignored_fields_are_stored_none() -> None:
    found = check_outcome(with_(reason="ignored", audit_proposed="no", audit_price="abc"))

    assert (found.reason, found.audit_price) == (None, None)


def test_no_objections_is_an_empty_tuple_and_blank_next_step_is_none() -> None:
    found = check_outcome(with_(objections=(), next_step="  ", next_step_on=""))

    assert (found.objections, found.next_step, found.next_step_on) == ((), None, None)


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        (with_(outcome=""), ("outcome", OutcomeRefusal.MISSING)),
        (with_(qualified=""), ("qualified", OutcomeRefusal.MISSING)),
        (with_(qualified="no", reason=" "), ("reason", OutcomeRefusal.MISSING)),
        (with_(qualified="no", reason="y" * 501), ("reason", OutcomeRefusal.TOO_LONG)),
        (with_(audit_proposed=""), ("audit_proposed", OutcomeRefusal.MISSING)),
        (with_(audit_price=""), ("audit_price", OutcomeRefusal.MISSING)),
        (with_(audit_price="1.5"), ("audit_price", OutcomeRefusal.BAD_PRICE)),
        (with_(audit_price="-5"), ("audit_price", OutcomeRefusal.BAD_PRICE)),
        (with_(audit_price="1000000001"), ("audit_price", OutcomeRefusal.BAD_PRICE)),
        (with_(price_reaction=""), ("price_reaction", OutcomeRefusal.MISSING)),
        (with_(next_step="z" * 501), ("next_step", OutcomeRefusal.TOO_LONG)),
        (with_(next_step_on="2026-02-30"), ("next_step_on", OutcomeRefusal.BAD_DATE)),
        (with_(next_step_on="25/11/2026"), ("next_step_on", OutcomeRefusal.BAD_DATE)),
        (with_(outcome="no_show", next_step_on="soon"), ("next_step_on", OutcomeRefusal.BAD_DATE)),
    ],
)
def test_each_refusal(form: OutcomeForm, expected: tuple[str, OutcomeRefusal]) -> None:
    assert refusal(form) == expected


def test_500_characters_after_crlf_folding_fit() -> None:
    found = check_outcome(with_(qualified="no", reason="a\r\n" * 250))

    assert found.reason == "a\n" * 250


@pytest.mark.parametrize(
    "form",
    [
        with_(outcome="maybe"),
        with_(qualified="perhaps"),
        with_(audit_proposed="Y"),
        with_(price_reaction="loved_it"),
        with_(objections=("too_busy",)),
    ],
)
def test_an_unknown_code_is_a_value_error(form: OutcomeForm) -> None:
    with pytest.raises(ValueError):
        check_outcome(form)


def test_check_outcome_takes_a_form() -> None:
    with pytest.raises(TypeError):
        check_outcome({"outcome": "held"})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fields",
    [
        {"outcome": "held"},
        {"outcome": CallResult.NO_SHOW, "qualified": True},
        {"outcome": CallResult.NO_SHOW, "objections": ()},
        {"outcome": CallResult.NO_SHOW, "next_step": "a\r\nb"},
        {"outcome": CallResult.NO_SHOW, "next_step": " "},
        {"outcome": CallResult.NO_SHOW, "next_step_on": datetime(2026, 1, 1)},
        {
            "outcome": CallResult.HELD,
            "qualified": False,
            "audit_proposed": False,
            "price_reaction": PriceReaction.ACCEPTED,
            "objections": (),
        },  # not qualified without a reason
        {
            "outcome": CallResult.HELD,
            "qualified": True,
            "audit_proposed": True,
            "audit_price": 1_500_000,
            "price_reaction": PriceReaction.ACCEPTED,
            "objections": (),
        },
        {
            "outcome": CallResult.HELD,
            "qualified": True,
            "audit_proposed": False,
            "price_reaction": PriceReaction.ACCEPTED,
            "objections": (ObjectionType.USES_OTHER_TOOL, ObjectionType.NO_BUDGET),
        },
    ],
)
def test_an_inconsistent_outcome_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="Outcome"):
        Outcome(**fields)  # type: ignore[arg-type]


def test_columns_round_trip() -> None:
    held = check_outcome(HELD)
    no_objections = check_outcome(with_(objections=()))
    no_show = Outcome(outcome=CallResult.NO_SHOW)

    assert held.columns()["objections"] == "no_budget,uses_other_tool"
    assert no_objections.columns()["objections"] == ""
    assert no_show.columns()["objections"] is None
    for each in (held, no_objections, no_show):
        assert Outcome.from_columns(each.columns()) == each


def test_changed_fields_and_correction_causes() -> None:
    held = check_outcome(HELD)
    other = check_outcome(with_(qualified="no", reason="No budget", next_step_on=""))

    assert changed_fields(held, held) == ()
    assert changed_fields(held, other) == ("qualified", "reason", "next_step_on")
    assert [correction_cause(field) for field in CORRECTABLE_FIELDS] == [
        "corrected_outcome",
        "corrected_qualified",
        "corrected_reason",
        "corrected_audit_proposed",
        "corrected_audit_price",
        "corrected_price_reaction",
        "corrected_objections",
        "corrected_next_step",
        "corrected_next_step_on",
    ]
    with pytest.raises(ValueError):
        correction_cause("recorded_at")
