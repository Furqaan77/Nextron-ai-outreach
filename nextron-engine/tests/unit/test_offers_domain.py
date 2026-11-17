"""``nextron.domain.offers`` (FR-3, FR-4, Story 4.2): the Offer ladder, its file form, every
value rule and rule with its exact path and reason, and the form round trip. Standard library,
pytest and ``nextron.domain`` only."""

import copy
import dataclasses
from typing import Any

import pytest

from nextron.domain.offers import (
    MAX_OFFERS,
    MAX_RUNGS,
    InvalidOffers,
    OfferDef,
    OfferRung,
    OfferRungKind,
    offer_form,
    offer_from_form,
    validate_offers,
)

KEY_REASON = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)


def rung(kind: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "name": name, **extra}


def ladder() -> list[dict[str, Any]]:
    return [
        rung("reply", "A reply", terms="Only a reply."),
        rung("audit", "Audit", price_usd_min=2500, price_usd_max=3500, terms="Fixed fee."),
        rung("build", "Build", price_usd_min=10000, price_usd_max=30000),
    ]


def offer(key: str = "default", **changes: Any) -> dict[str, Any]:
    name = "Reply, then the audit, then a build"
    return {"key": key, "name": name, "ladder": ladder(), **changes}


def data(*offers: dict[str, Any]) -> dict[str, Any]:
    return {"offers": list(offers) if offers else [offer()]}


def refused(value: object) -> tuple[str, str]:
    with pytest.raises(InvalidOffers) as caught:
        validate_offers(value)
    assert str(caught.value) == f"{caught.value.field}: {caught.value.reason}"
    return caught.value.field, caught.value.reason


def with_rung(index: int, **changes: Any) -> dict[str, Any]:
    """The file with rung ``index`` of the one offer changed (a ``None`` value removes the
    field)."""
    found = data()
    target = found["offers"][0]["ladder"][index]
    for name, value in changes.items():
        if value is None:
            target.pop(name, None)
        else:
            target[name] = value
    return found


# The valid file ----------------------------------------------------------------------------------


def test_a_valid_file_gives_the_offer_with_its_ladder_in_order() -> None:
    [found] = validate_offers(data())

    assert found == OfferDef(
        key="default",
        name="Reply, then the audit, then a build",
        ladder=(
            OfferRung(OfferRungKind.REPLY, "A reply", None, None, "Only a reply."),
            OfferRung(OfferRungKind.AUDIT, "Audit", 2500, 3500, "Fixed fee."),
            OfferRung(OfferRungKind.BUILD, "Build", 10000, 30000, None),
        ),
    )
    assert isinstance(found.ladder, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.key = "other"  # type: ignore[misc]


def test_a_ladder_of_one_reply_rung_is_valid() -> None:
    [found] = validate_offers(data(offer(ladder=[rung("reply", "A reply")])))

    assert found.ladder == (OfferRung(OfferRungKind.REPLY, "A reply", None, None, None),)


def test_prices_may_be_equal_and_span_the_whole_range() -> None:
    found = with_rung(1, price_usd_min=1, price_usd_max=1)
    found["offers"][0]["ladder"][2].update(price_usd_min=1_000_000, price_usd_max=1_000_000)

    [result] = validate_offers(found)

    assert (result.ladder[1].price_usd_min, result.ladder[2].price_usd_max) == (1, 1_000_000)


def test_several_offers_keep_file_order() -> None:
    found = validate_offers(data(offer("zz"), offer("aa")))

    assert [o.key for o in found] == ["zz", "aa"]


def test_the_error_code() -> None:
    assert InvalidOffers.code == "invalid_offers"


# The top level -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([], ("(top level)", "must be a mapping with exactly one field, offers")),
        ({}, ("offers", "missing")),
        ({"offers": [offer()], "extra": 1}, ("extra", "unknown field")),
        ({"Offers": []}, ("(top level)", "has an unknown field")),
        ({"offers": {}}, ("offers", "must be a list")),
        ({"offers": []}, ("offers", "must hold at least 1 item")),
        (
            {"offers": [offer(f"o{i}") for i in range(MAX_OFFERS + 1)]},
            ("offers", "must hold at most 20 items"),
        ),
    ],
)
def test_the_top_level(value: object, expected: tuple[str, str]) -> None:
    assert refused(value) == expected


def test_twenty_offers_are_allowed() -> None:
    assert len(validate_offers({"offers": [offer(f"o{i}") for i in range(MAX_OFFERS)]})) == 20


# An offer's fields -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ("not a mapping", ("offers[0]", "must be a mapping")),
        ({"key": "x", "name": "N", "ladder": [rung("reply", "R")], "terms": "t"},
         ("offers[x].terms", "unknown field")),
        ({"name": "N", "ladder": [rung("reply", "R")]}, ("offers[0].key", "missing")),
        ({"key": "x", "ladder": [rung("reply", "R")]}, ("offers[x].name", "missing")),
        ({"key": "x", "name": "N"}, ("offers[x].ladder", "missing")),
        ({"key": "Bad", "name": "N", "ladder": []}, ("offers[0].key", KEY_REASON)),
        ({"key": "a" * 32, "name": "N", "ladder": []}, ("offers[0].key", KEY_REASON)),
        ({"key": "x", "name": "", "ladder": []}, ("offers[x].name", "must not be blank")),
        ({"key": "x", "name": " N", "ladder": []},
         ("offers[x].name", "must not start or end with whitespace")),
        ({"key": "x", "name": "a​b", "ladder": []},
         ("offers[x].name", "must not hold control, format or line-separator characters")),
        ({"key": "x", "name": "n" * 101, "ladder": []},
         ("offers[x].name", "must be at most 100 characters")),
        ({"key": "x", "name": 5, "ladder": []}, ("offers[x].name", "must be text")),
        ({"key": "x", "name": "N", "ladder": "reply"}, ("offers[x].ladder", "must be a list")),
        ({"key": "x", "name": "N", "ladder": []},
         ("offers[x].ladder", "must hold at least 1 item")),
        ({"key": "x", "name": "N", "ladder": [rung("reply", "R")] * (MAX_RUNGS + 1)},
         ("offers[x].ladder", "must hold at most 5 items")),
    ],
)
def test_an_offers_fields(changed: object, expected: tuple[str, str]) -> None:
    assert refused({"offers": [changed]}) == expected


def test_a_100_character_name_is_allowed() -> None:
    [found] = validate_offers(data(offer(name="n" * 100)))

    assert len(found.name) == 100


def test_a_repeated_key_is_refused_and_named_by_index() -> None:
    assert refused(data(offer("default"), offer("default"))) == (
        "offers[1].key",
        "repeats an earlier Offer key (default)",
    )


# A rung's fields ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "changes", "expected"),
    [
        (0, {"kind": None}, ("offers[default].ladder[0].kind", "missing")),
        (1, {"name": None}, ("offers[default].ladder[1].name", "missing")),
        (2, {"kind": "retainer"},
         ("offers[default].ladder[2].kind", "must be one of reply, audit, build")),
        (2, {"kind": "Build"},
         ("offers[default].ladder[2].kind", "must be one of reply, audit, build")),
        (1, {"name": "n" * 101},
         ("offers[default].ladder[1].name", "must be at most 100 characters")),
        (1, {"terms": "t" * 201},
         ("offers[default].ladder[1].terms", "must be at most 200 characters")),
        (1, {"terms": ""}, ("offers[default].ladder[1].terms", "must not be blank")),
        (1, {"price_usd_min": True},
         ("offers[default].ladder[1].price_usd_min", "must be a whole number of US dollars")),
        (1, {"price_usd_min": "2500"},
         ("offers[default].ladder[1].price_usd_min", "must be a whole number of US dollars")),
        (1, {"price_usd_min": 2500.0},
         ("offers[default].ladder[1].price_usd_min", "must be a whole number of US dollars")),
        (1, {"price_usd_min": 0},
         ("offers[default].ladder[1].price_usd_min", "must be from 1 to 1,000,000")),
        (2, {"price_usd_max": 1_000_001},
         ("offers[default].ladder[2].price_usd_max", "must be from 1 to 1,000,000")),
        (1, {"currency": "usd"}, ("offers[default].ladder[1].currency", "unknown field")),
    ],
)
def test_a_rungs_fields(index: int, changes: dict[str, Any], expected: tuple[str, str]) -> None:
    assert refused(with_rung(index, **changes)) == expected


def test_a_rung_that_is_not_a_mapping() -> None:
    found = data()
    found["offers"][0]["ladder"][1] = "audit"

    assert refused(found) == ("offers[default].ladder[1]", "must be a mapping")


def test_a_200_character_terms_is_allowed() -> None:
    [found] = validate_offers(with_rung(1, terms="t" * 200))

    assert len(found.ladder[1].terms or "") == 200


# The rules ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rungs", "expected"),
    [
        (  # rule 1: kinds are unique
            [rung("reply", "R"), rung("reply", "R2")],
            ("offers[default].ladder[1].kind", "repeats an earlier rung's kind (reply)"),
        ),
        (  # rule 2: the first rung is a reply
            [rung("audit", "A", price_usd_min=1, price_usd_max=2)],
            (
                "offers[default].ladder[0].kind",
                "must be reply: the first ask is only a reply (FR-4)",
            ),
        ),
        (  # rule 3: a reply has no price
            [rung("reply", "R", price_usd_max=5)],
            (
                "offers[default].ladder[0].price_usd_max",
                "a reply rung has no price: the first ask is only a reply (FR-4)",
            ),
        ),
        (
            [rung("reply", "R", price_usd_min=5, price_usd_max=6)],
            (
                "offers[default].ladder[0].price_usd_min",
                "a reply rung has no price: the first ask is only a reply (FR-4)",
            ),
        ),
        (  # rule 4: an audit or build has both prices, the lower first
            [rung("reply", "R"), rung("audit", "A", price_usd_max=3500)],
            ("offers[default].ladder[1].price_usd_min", "missing: an audit rung needs both prices"),
        ),
        (
            [rung("reply", "R"), rung("build", "B", price_usd_min=10000)],
            ("offers[default].ladder[1].price_usd_max", "missing: a build rung needs both prices"),
        ),
        (
            [rung("reply", "R"), rung("audit", "A", price_usd_min=3500, price_usd_max=2500)],
            ("offers[default].ladder[1].price_usd_max", "must be at least price_usd_min (3,500)"),
        ),
    ],
)
def test_the_ladder_rules(rungs: list[dict[str, Any]], expected: tuple[str, str]) -> None:
    assert refused(data(offer(ladder=rungs))) == expected


def test_fields_are_checked_before_the_rules() -> None:
    rungs = [rung("audit", "A", price_usd_min=0)]  # not a reply, and a bad price

    assert refused(data(offer(ladder=rungs))) == (
        "offers[default].ladder[0].price_usd_min",
        "must be from 1 to 1,000,000",
    )


# The form round trip -----------------------------------------------------------------------------


def test_the_form_round_trips() -> None:
    [found] = validate_offers(data())

    form = offer_form(found)

    assert form == offer()  # the file's own shape: optional keys only when set
    assert offer_from_form(copy.deepcopy(form)) == found


def test_from_form_validates_one_offer_and_names_it_by_key() -> None:
    with pytest.raises(InvalidOffers) as caught:
        offer_from_form(offer(ladder=[]))

    assert (caught.value.field, caught.value.reason) == (
        "offers[default].ladder",
        "must hold at least 1 item",
    )


def test_a_hand_built_offer_of_the_wrong_types_is_refused_by_its_form() -> None:
    [found] = validate_offers(data())
    loose = dataclasses.replace(found, ladder=[found.ladder[0]])  # type: ignore[arg-type]
    wrong = dataclasses.replace(found, ladder="reply")  # type: ignore[arg-type]

    assert offer_from_form(offer_form(loose)).ladder == (found.ladder[0],)
    with pytest.raises(InvalidOffers):
        offer_from_form(offer_form(wrong))
