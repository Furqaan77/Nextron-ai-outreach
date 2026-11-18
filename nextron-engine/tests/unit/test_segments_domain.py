"""Segment definitions (``nextron/domain/segments.py``, FR-1, Story 2.1): validation with exact
field paths, the market-fact rule, ``titles()`` and the stored form."""

import copy
import json
from typing import Any

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.segments import (
    DiscoveryQueries,
    Disqualifier,
    DisqualifierKind,
    EvidenceType,
    InvalidSegments,
    PreferredRoles,
    SegmentDef,
    SignalClass,
    SignalSource,
    SignalType,
    SizeClue,
    SizeRange,
    SizeUnit,
    definition_text,
    represents_market_fact,
    segment_from_text,
    validate_segments,
)

# Builders -------------------------------------------------------------------------------------


def signal(
    key: str,
    *,
    cls: str = "event",
    tier: int = 1,
    evidence: tuple[str, ...] = ("quote",),
    sources: tuple[str, ...] = ("signal_inbox",),
    score_only: bool = False,
    weight: int = 60,
    **extra: object,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": f"Signal {key}",
        "class": cls,
        "tier": tier,
        "evidence": list(evidence),
        "sources": list(sources),
        "score_only": score_only,
        "weight": weight,
        **extra,
    }


def segment(key: str, **extra: object) -> dict[str, Any]:
    return {
        "key": key,
        "name": f"Segment {key}",
        "industry_keywords": ["law firm", "solicitors"],
        "size_range": {"minimum": 5, "maximum": 50, "unit": "lawyers"},
        "decision_maker_roles": [["Managing Partner"], ["Owner"]],
        "disqualifiers": [
            {
                "key": f"{key}_small",
                "kind": "fewer_than",
                "size_clue": "lawyers_firm_wide",
                "threshold": 5,
                "description": "Fewer than 5 lawyers",
            }
        ],
        "signal_types": [
            signal(
                f"{key}_hiring",
                sources=("careers_page", "signal_inbox"),
                match_terms=["intake", "accounts payable"],
                preferred_roles=[{"when_terms": ["accounts payable"], "roles": ["CFO"]}],
            ),
            signal(f"{key}_t2", tier=2, weight=35),
            signal(
                f"{key}_t3",
                cls="attribute",
                tier=3,
                evidence=("count", "quote"),
                sources=("team_pages", "practice_pages"),
                weight=15,
            ),
        ],
        "discovery_queries": {"places": ["law firm"]},
        **extra,
    }


def seed_file() -> dict[str, Any]:
    return {"segments": [segment("law"), segment("ops")]}


def problem(data: object) -> InvalidSegments:
    with pytest.raises(InvalidSegments) as caught:
        validate_segments(data)
    return caught.value


def assert_problem(data: object, field: str, needle: str) -> None:
    error = problem(data)
    assert error.field == field, error.message
    assert needle in error.reason, error.message
    assert error.message == f"{field}: {error.reason}"


# The happy path ---------------------------------------------------------------------------------


def test_a_valid_file_gives_typed_segments_in_file_order() -> None:
    law, ops = validate_segments(seed_file())

    assert (law.key, ops.key) == ("law", "ops")
    assert law == SegmentDef(
        key="law",
        name="Segment law",
        industry_keywords=("law firm", "solicitors"),
        size_range=SizeRange(5, 50, SizeUnit.LAWYERS),
        decision_maker_roles=(("Managing Partner",), ("Owner",)),
        disqualifiers=(
            Disqualifier(
                "law_small",
                DisqualifierKind.FEWER_THAN,
                SizeClue.LAWYERS_FIRM_WIDE,
                5,
                "Fewer than 5 lawyers",
            ),
        ),
        signal_types=(
            SignalType(
                key="law_hiring",
                name="Signal law_hiring",
                signal_class=SignalClass.EVENT,
                tier=1,
                evidence=(EvidenceType.QUOTE,),
                sources=(SignalSource.CAREERS_PAGE, SignalSource.SIGNAL_INBOX),
                score_only=False,
                weight=60,
                match_terms=("intake", "accounts payable"),
                preferred_roles=(PreferredRoles(("accounts payable",), ("CFO",)),),
            ),
            SignalType(
                "law_t2",
                "Signal law_t2",
                SignalClass.EVENT,
                2,
                (EvidenceType.QUOTE,),
                (SignalSource.SIGNAL_INBOX,),
                False,
                35,
            ),
            SignalType(
                "law_t3",
                "Signal law_t3",
                SignalClass.ATTRIBUTE,
                3,
                (EvidenceType.COUNT, EvidenceType.QUOTE),
                (SignalSource.TEAM_PAGES, SignalSource.PRACTICE_PAGES),
                False,
                15,
            ),
        ),
        discovery_queries=DiscoveryQueries(("law firm",)),
    )
    assert type(law.signal_types[0].signal_class) is SignalClass
    assert type(law.size_range.unit) is SizeUnit


def test_the_enums_hold_exactly_the_specified_values() -> None:
    assert [m.value for m in SignalClass] == ["event", "attribute"]
    assert [m.value for m in EvidenceType] == ["quote", "absence", "count"]
    assert [m.value for m in SignalSource] == [
        "careers_page",
        "ats_board",
        "signal_inbox",
        "news_pages",
        "team_pages",
        "practice_pages",
        "website",
        "job_ads",
    ]
    assert [m.value for m in SizeUnit] == ["lawyers", "staff", "usd_revenue"]
    assert [m.value for m in SizeClue] == [
        "lawyers_at_office",
        "lawyers_firm_wide",
        "staff",
        "group_staff",
        "locations",
    ]
    assert [m.value for m in DisqualifierKind] == ["fewer_than", "more_than"]


def test_the_types_are_frozen() -> None:
    law, _ = validate_segments(seed_file())

    with pytest.raises(AttributeError):
        law.key = "other"  # type: ignore[misc]


def test_optional_fields_may_be_left_out_or_empty() -> None:
    data = seed_file()
    del data["segments"][0]["signal_types"][1]["name"]  # restored below
    data["segments"][0]["signal_types"][1]["name"] = "Signal law_t2"
    data["segments"][0]["signal_types"][1]["match_terms"] = []
    data["segments"][0]["signal_types"][1]["preferred_roles"] = []
    data["segments"][0]["disqualifiers"] = []

    law, _ = validate_segments(data)

    assert law.signal_types[1].match_terms == ()
    assert law.signal_types[1].preferred_roles == ()
    assert law.signal_types[2].match_terms == ()
    assert law.disqualifiers == ()


def test_the_error_is_a_domain_error() -> None:
    error = problem({"segments": []})

    assert isinstance(error, DomainError)
    assert error.code == "invalid_segments"
    assert (error.field, error.reason) == ("segments", "must hold at least 1 item")
    assert str(error) == "segments: must hold at least 1 item"


# The file shape ---------------------------------------------------------------------------------


def edited(edit: Any) -> dict[str, Any]:
    data = seed_file()
    edit(data)
    return data


def law_of(data: dict[str, Any]) -> dict[str, Any]:
    return data["segments"][0]


def signal_of(data: dict[str, Any], index: int, segment_index: int = 0) -> dict[str, Any]:
    return data["segments"][segment_index]["signal_types"][index]


@pytest.mark.parametrize(
    ("data", "field", "needle"),
    [
        ([], "(top level)", "must be a mapping"),
        ("segments", "(top level)", "must be a mapping"),
        ({}, "segments", "missing"),
        ({"segments": [], "extra": 1}, "segments", "must hold at least 1 item"),
        ({"extra": 1, "segments": []}, "extra", "unknown field"),
        ({"Weird Field!": 1}, "(top level)", "unknown field"),
        ({"segments": {}}, "segments", "must be a list"),
        ({"segments": [segment(f"s{i}") for i in range(51)]}, "segments", "at most 50 items"),
        ({"segments": ["text"]}, "segments[0]", "must be a mapping"),
    ],
)
def test_the_top_level_is_exactly_a_segments_list(data: object, field: str, needle: str) -> None:
    assert_problem(data, field, needle)


@pytest.mark.parametrize(
    "missing",
    [
        "key",
        "name",
        "industry_keywords",
        "size_range",
        "decision_maker_roles",
        "disqualifiers",
        "signal_types",
        "discovery_queries",
    ],
)
def test_every_segment_field_is_required(missing: str) -> None:
    data = edited(lambda d: law_of(d).pop(missing))

    where = "segments[0]" if missing == "key" else "segments[law]"
    assert_problem(data, f"{where}.{missing}", "missing")


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "segments[law].extra"),
        (("size_range",), "segments[law].size_range.extra"),
        (("discovery_queries",), "segments[law].discovery_queries.extra"),
        (("disqualifiers", 0), "segments[law].disqualifiers[law_small].extra"),
        (("signal_types", 1), "segments[law].signal_types[law_t2].extra"),
        (
            ("signal_types", 0, "preferred_roles", 0),
            "segments[law].signal_types[law_hiring].preferred_roles[0].extra",
        ),
    ],
)
def test_an_unknown_field_is_refused_everywhere(path: tuple[object, ...], field: str) -> None:
    data = seed_file()
    target: Any = law_of(data)
    for step in path:
        target = target[step]
    target["extra"] = 1

    assert_problem(data, field, "unknown field")


def test_an_unknown_field_with_an_odd_name_is_not_repeated() -> None:
    long_name = "x" * 100
    data = edited(lambda d: law_of(d).update({long_name: 1}))

    error = problem(data)

    assert (error.field, error.reason) == ("segments[law]", "has an unknown field")
    assert long_name not in error.message


@pytest.mark.parametrize(
    ("missing", "field"),
    [
        ("minimum", "segments[law].size_range.minimum"),
        ("unit", "segments[law].size_range.unit"),
    ],
)
def test_size_range_fields_are_required(missing: str, field: str) -> None:
    assert_problem(edited(lambda d: law_of(d)["size_range"].pop(missing)), field, "missing")


@pytest.mark.parametrize(
    "missing", ["key", "kind", "size_clue", "threshold", "description"]
)
def test_disqualifier_fields_are_required(missing: str) -> None:
    data = edited(lambda d: law_of(d)["disqualifiers"][0].pop(missing))

    where = "disqualifiers[0]" if missing == "key" else "disqualifiers[law_small]"
    assert_problem(data, f"segments[law].{where}.{missing}", "missing")


@pytest.mark.parametrize(
    "missing", ["key", "name", "class", "tier", "evidence", "sources", "score_only", "weight"]
)
def test_signal_type_fields_are_required(missing: str) -> None:
    data = edited(lambda d: signal_of(d, 1).pop(missing))

    where = "signal_types[1]" if missing == "key" else "signal_types[law_t2]"
    assert_problem(data, f"segments[law].{where}.{missing}", "missing")


@pytest.mark.parametrize("missing", ["when_terms", "roles"])
def test_preferred_roles_fields_are_required(missing: str) -> None:
    data = edited(lambda d: signal_of(d, 0)["preferred_roles"][0].pop(missing))

    assert_problem(
        data,
        f"segments[law].signal_types[law_hiring].preferred_roles[0].{missing}",
        "missing",
    )


def test_discovery_queries_needs_places() -> None:
    data = edited(lambda d: law_of(d)["discovery_queries"].pop("places"))

    assert_problem(data, "segments[law].discovery_queries.places", "missing")


# Value rules -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "needle"),
    [
        ("Law", "lowercase letters"),
        ("1law", "lowercase letters"),
        ("law-firm", "lowercase letters"),
        ("", "lowercase letters"),
        ("l" * 32, "at most 31 characters"),
        (7, "lowercase letters"),
        (None, "lowercase letters"),
    ],
)
def test_a_segment_key_must_match_the_pattern(key: object, needle: str) -> None:
    data = edited(lambda d: law_of(d).update({"key": key}))

    assert_problem(data, "segments[0].key", needle)


def test_a_key_of_31_characters_is_fine() -> None:
    key = "l" * 31
    data = edited(lambda d: law_of(d).update({"key": key}))

    assert validate_segments(data)[0].key == key


def test_a_repeated_segment_key_is_refused() -> None:
    data = {"segments": [segment("law"), segment("law")]}

    assert_problem(data, "segments[1].key", "repeats an earlier segment key (law)")


def test_signal_type_keys_are_unique_across_segments() -> None:
    def edit(data: dict[str, Any]) -> None:
        signal_of(data, 1, segment_index=1)["key"] = "law_t2"

    assert_problem(
        edited(edit),
        "segments[ops].signal_types[1].key",
        "repeats an earlier signal type key (law_t2)",
    )


def test_signal_type_keys_are_unique_within_a_segment() -> None:
    def edit(data: dict[str, Any]) -> None:
        signal_of(data, 2)["key"] = "law_t2"

    assert_problem(
        edited(edit), "segments[law].signal_types[2].key", "repeats an earlier signal type key"
    )


def test_disqualifier_keys_are_unique_across_segments() -> None:
    def edit(data: dict[str, Any]) -> None:
        data["segments"][1]["disqualifiers"][0]["key"] = "law_small"

    assert_problem(
        edited(edit),
        "segments[ops].disqualifiers[0].key",
        "repeats an earlier disqualifier key (law_small)",
    )


def test_a_signal_and_a_disqualifier_may_share_a_key() -> None:
    data = edited(lambda d: law_of(d)["disqualifiers"][0].update({"key": "law_t2"}))

    assert validate_segments(data)[0].disqualifiers[0].key == "law_t2"


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        (None, "must be text"),
        (5, "must be text"),
        ("", "must not be blank"),
        ("   ", "must not be blank"),
        (" padded", "whitespace"),
        ("padded ", "whitespace"),
        ("tab\there", "control, format or line-separator"),
        ("zero\u200bwidth", "control, format or line-separator"),
        ("bidi\u202eoverride", "control, format or line-separator"),
        ("line\u2028separator", "control, format or line-separator"),
        ("para\u2029separator", "control, format or line-separator"),
        ("x" * 201, "at most 200 characters"),
    ],
)
def test_a_name_must_be_clean_text_of_at_most_200(value: object, needle: str) -> None:
    assert_problem(edited(lambda d: law_of(d).update({"name": value})), "segments[law].name", needle)


def test_a_name_of_200_characters_is_fine() -> None:
    name = "x" * 200

    assert validate_segments(edited(lambda d: law_of(d).update({"name": name})))[0].name == name


@pytest.mark.parametrize(
    ("set_value", "field"),
    [
        (lambda d: law_of(d)["industry_keywords"].append("k" * 101), "industry_keywords[2]"),
        (lambda d: law_of(d)["discovery_queries"]["places"].append("q" * 101), "discovery_queries.places[1]"),
        (lambda d: law_of(d)["decision_maker_roles"][0].append("t" * 101), "decision_maker_roles[0][1]"),
        (lambda d: signal_of(d, 0)["match_terms"].append("m" * 101), "signal_types[law_hiring].match_terms[2]"),
        (
            lambda d: signal_of(d, 0)["preferred_roles"][0]["roles"].append("r" * 101),
            "signal_types[law_hiring].preferred_roles[0].roles[1]",
        ),
        (lambda d: law_of(d)["disqualifiers"][0].update({"description": "d" * 201}), "disqualifiers[law_small].description"),
        (lambda d: signal_of(d, 1).update({"name": "n" * 201}), "signal_types[law_t2].name"),
    ],
)
def test_terms_titles_keywords_and_queries_stop_at_100(set_value: Any, field: str) -> None:
    error = problem(edited(set_value))

    assert error.field == f"segments[law].{field}"
    assert "at most" in error.reason


@pytest.mark.parametrize(
    ("set_value", "field", "needle"),
    [
        (lambda d: law_of(d).update({"industry_keywords": []}), "industry_keywords", "at least 1"),
        (lambda d: law_of(d).update({"industry_keywords": "law"}), "industry_keywords", "must be a list"),
        (
            lambda d: law_of(d).update({"industry_keywords": [f"k{i}" for i in range(51)]}),
            "industry_keywords",
            "at most 50",
        ),
        (
            lambda d: law_of(d).update({"industry_keywords": ["Law Firm", "law firm"]}),
            "industry_keywords[1]",
            "repeats an earlier item",
        ),
        (lambda d: law_of(d).update({"decision_maker_roles": []}), "decision_maker_roles", "at least 1"),
        (lambda d: law_of(d).update({"decision_maker_roles": [[]]}), "decision_maker_roles[0]", "at least 1"),
        (
            lambda d: law_of(d).update({"decision_maker_roles": ["Owner"]}),
            "decision_maker_roles[0]",
            "must be a list",
        ),
        (lambda d: law_of(d).update({"signal_types": []}), "signal_types", "at least 1"),
        (lambda d: law_of(d).update({"disqualifiers": None}), "disqualifiers", "must be a list"),
        (lambda d: law_of(d)["discovery_queries"].update({"places": []}), "discovery_queries.places", "at least 1"),
        (lambda d: signal_of(d, 1).update({"evidence": []}), "signal_types[law_t2].evidence", "at least 1"),
        (lambda d: signal_of(d, 1).update({"sources": []}), "signal_types[law_t2].sources", "at least 1"),
        (
            lambda d: signal_of(d, 2).update({"evidence": ["quote", "quote"]}),
            "signal_types[law_t3].evidence[1]",
            "repeats an earlier item",
        ),
        (
            lambda d: signal_of(d, 1).update({"sources": ["website", "website"]}),
            "signal_types[law_t2].sources[1]",
            "repeats an earlier item",
        ),
        (
            lambda d: signal_of(d, 0).update({"match_terms": ["Intake", "intake"]}),
            "signal_types[law_hiring].match_terms[1]",
            "repeats an earlier item",
        ),
        (
            lambda d: signal_of(d, 0)["preferred_roles"][0].update({"roles": []}),
            "signal_types[law_hiring].preferred_roles[0].roles",
            "at least 1",
        ),
        (
            lambda d: signal_of(d, 0)["preferred_roles"].append(
                {"when_terms": ["Accounts Payable"], "roles": ["cfo"]}
            ),
            "signal_types[law_hiring].preferred_roles[1]",
            "repeats an earlier item",
        ),
    ],
)
def test_lists_hold_1_to_50_distinct_items(set_value: Any, field: str, needle: str) -> None:
    assert_problem(edited(set_value), f"segments[law].{field}", needle)


@pytest.mark.parametrize(
    ("set_value", "field", "needle"),
    [
        (lambda d: signal_of(d, 1).update({"tier": 0}), "signal_types[law_t2].tier", "from 1 to 3"),
        (lambda d: signal_of(d, 1).update({"tier": 4}), "signal_types[law_t2].tier", "from 1 to 3"),
        (lambda d: signal_of(d, 1).update({"tier": True}), "signal_types[law_t2].tier", "whole number"),
        (lambda d: signal_of(d, 1).update({"tier": 2.0}), "signal_types[law_t2].tier", "whole number"),
        (lambda d: signal_of(d, 1).update({"tier": "2"}), "signal_types[law_t2].tier", "whole number"),
        (lambda d: signal_of(d, 1).update({"weight": 0}), "signal_types[law_t2].weight", "from 1 to 100"),
        (lambda d: signal_of(d, 1).update({"weight": 101}), "signal_types[law_t2].weight", "from 1 to 100"),
        (lambda d: signal_of(d, 1).update({"weight": False}), "signal_types[law_t2].weight", "whole number"),
        (
            lambda d: law_of(d)["disqualifiers"][0].update({"threshold": 0}),
            "disqualifiers[law_small].threshold",
            "at least 1",
        ),
        (
            lambda d: law_of(d)["disqualifiers"][0].update({"threshold": True}),
            "disqualifiers[law_small].threshold",
            "whole number",
        ),
        (lambda d: law_of(d)["size_range"].update({"minimum": -1}), "size_range.minimum", "at least 0"),
        (lambda d: law_of(d)["size_range"].update({"maximum": 1.5}), "size_range.maximum", "whole number"),
        (
            lambda d: law_of(d)["size_range"].update({"minimum": 51}),
            "size_range.maximum",
            "at least the minimum",
        ),
        (lambda d: signal_of(d, 1).update({"score_only": "false"}), "signal_types[law_t2].score_only", "true or false"),
        (lambda d: signal_of(d, 1).update({"score_only": 0}), "signal_types[law_t2].score_only", "true or false"),
    ],
)
def test_integers_and_booleans_are_typed_and_in_range(set_value: Any, field: str, needle: str) -> None:
    assert_problem(edited(set_value), f"segments[law].{field}", needle)


def test_a_size_range_may_be_a_single_value_from_0() -> None:
    data = edited(lambda d: law_of(d).update({"size_range": {"minimum": 0, "maximum": 0, "unit": "staff"}}))

    assert validate_segments(data)[0].size_range == SizeRange(0, 0, SizeUnit.STAFF)


@pytest.mark.parametrize(
    ("set_value", "field", "allowed"),
    [
        (lambda d: signal_of(d, 1).update({"class": "Event"}), "signal_types[law_t2].class", "event, attribute"),
        (
            lambda d: signal_of(d, 1).update({"evidence": ["quote", "photo"]}),
            "signal_types[law_t2].evidence[1]",
            "quote, absence, count",
        ),
        (lambda d: signal_of(d, 1).update({"sources": ["linkedin"]}), "signal_types[law_t2].sources[0]", "careers_page"),
        (lambda d: law_of(d)["size_range"].update({"unit": "people"}), "size_range.unit", "lawyers, staff, usd_revenue"),
        (lambda d: law_of(d)["disqualifiers"][0].update({"kind": "below"}), "disqualifiers[law_small].kind", "fewer_than, more_than"),
        (
            lambda d: law_of(d)["disqualifiers"][0].update({"size_clue": 5}),
            "disqualifiers[law_small].size_clue",
            "lawyers_at_office, lawyers_firm_wide, staff, group_staff, locations",
        ),
    ],
)
def test_enum_fields_take_only_their_values(set_value: Any, field: str, allowed: str) -> None:
    assert_problem(edited(set_value), f"segments[law].{field}", f"must be one of {allowed}")


def test_a_reason_never_repeats_a_long_value() -> None:
    long_value = "This is a very long value that must never be echoed back " * 2
    for edit in (
        lambda d: law_of(d).update({"name": long_value * 4}),
        lambda d: signal_of(d, 1).update({"class": long_value}),
        lambda d: law_of(d)["industry_keywords"].append(long_value * 2),
        lambda d: law_of(d).update({"key": long_value}),
    ):
        error = problem(edited(edit))
        assert long_value[:41] not in error.message, error.message


# The rules (the AC's, plus what keeps later stories safe) ----------------------------------------


def test_rule_1_a_tier_3_weight_above_a_tier_2_weight_is_refused() -> None:
    data = edited(lambda d: signal_of(d, 2).update({"weight": 70}))

    assert_problem(
        data,
        "segments[law].signal_types[law_t3].weight",
        "a Tier 3 weight must be below every Tier 1 weight (law_hiring has 60)",
    )


def test_rule_1_names_the_later_type_of_a_clashing_pair() -> None:
    data = edited(lambda d: signal_of(d, 2).update({"weight": 35}))  # equal to the Tier 2 weight

    assert_problem(
        data,
        "segments[law].signal_types[law_t3].weight",
        "a Tier 3 weight must be below every Tier 2 weight (law_t2 has 35)",
    )


def test_rule_1_a_later_tier_1_weight_below_an_earlier_tier_2_weight_is_refused() -> None:
    def edit(data: dict[str, Any]) -> None:
        law_of(data)["signal_types"].append(signal("law_late", weight=30))

    assert_problem(
        edited(edit),
        "segments[law].signal_types[law_late].weight",
        "a Tier 1 weight must be above every Tier 2 weight (law_t2 has 35)",
    )


def test_rule_1_types_of_one_tier_may_differ_and_segments_are_checked_apart() -> None:
    def edit(data: dict[str, Any]) -> None:
        law_of(data)["signal_types"].append(signal("law_t1_low", weight=40))
        for item in data["segments"][1]["signal_types"]:
            item["weight"] += 30  # ops: 90 / 65 / 45, above every Law weight

    law, ops = validate_segments(edited(edit))

    assert [t.weight for t in law.signal_types] == [60, 35, 15, 40]
    assert [t.weight for t in ops.signal_types] == [90, 65, 45]


def test_rule_2_an_events_evidence_is_exactly_quote() -> None:
    data = edited(lambda d: signal_of(d, 1).update({"evidence": ["quote", "absence"]}))

    assert_problem(
        data, "segments[law].signal_types[law_t2].evidence", "an Event's evidence must be exactly [quote]"
    )


def test_rule_2_an_event_with_count_evidence_is_refused() -> None:
    data = edited(lambda d: signal_of(d, 1).update({"evidence": ["count"]}))

    assert_problem(data, "segments[law].signal_types[law_t2].evidence", "exactly [quote]")


def test_rule_3_an_event_is_never_score_only() -> None:
    data = edited(lambda d: signal_of(d, 1).update({"score_only": True}))

    assert_problem(data, "segments[law].signal_types[law_t2].score_only", "an Event can't be score-only")


def test_rule_3_an_attribute_may_be_score_only_with_absence_evidence() -> None:
    data = edited(lambda d: signal_of(d, 2).update({"score_only": True, "evidence": ["absence"]}))

    law, _ = validate_segments(data)
    assert law.signal_types[2].score_only is True


@pytest.mark.parametrize("source", ["careers_page", "ats_board"])
def test_rule_4_a_hiring_signal_needs_match_terms(source: str) -> None:
    def edit(data: dict[str, Any]) -> None:
        signal_of(data, 1).update({"sources": [source]})

    assert_problem(edited(edit), "segments[law].signal_types[law_t2].match_terms", "needs match terms")

    def empty(data: dict[str, Any]) -> None:
        signal_of(data, 1).update({"sources": [source], "match_terms": []})

    assert_problem(edited(empty), "segments[law].signal_types[law_t2].match_terms", "needs match terms")


def test_rule_5_when_terms_are_among_the_match_terms() -> None:
    def edit(data: dict[str, Any]) -> None:
        signal_of(data, 0)["preferred_roles"][0]["when_terms"] = ["Accounts Payable", "payroll"]

    assert_problem(
        edited(edit),
        "segments[law].signal_types[law_hiring].preferred_roles[0].when_terms[1]",
        "must be one of the type's match terms",
    )


def test_rule_5_compares_case_insensitively_and_needs_no_hiring_source() -> None:
    def edit(data: dict[str, Any]) -> None:
        signal_of(data, 1)["match_terms"] = ["ERP"]
        signal_of(data, 1)["preferred_roles"] = [{"when_terms": ["erp"], "roles": ["CIO"]}]

    law, _ = validate_segments(edited(edit))

    assert law.signal_types[1].preferred_roles == (PreferredRoles(("erp",), ("CIO",)),)


@pytest.mark.parametrize(
    ("edit", "field"),
    [
        (lambda t: t.update({"name": "ABA Formal Opinion 512"}), "name"),
        (lambda t: t.update({"name": "The SRA notice"}), "name"),
        (lambda t: t.update({"key": "law_sra_notice"}), "key"),
        (lambda t: t.update({"key": "law_aba_512"}), "key"),
        (lambda t: t.update({"match_terms": ["Solicitors Regulation Authority"]}), "match_terms[0]"),
        (lambda t: t.update({"match_terms": ["intake", "formal-opinion"]}), "match_terms[1]"),
    ],
)
def test_rule_6_no_signal_type_represents_a_market_fact(edit: Any, field: str) -> None:
    data = seed_file()
    edit(signal_of(data, 1))
    key = signal_of(data, 1)["key"]

    assert_problem(data, f"segments[law].signal_types[{key}].{field}", "market-wide fact")


def test_rule_7_a_title_is_unique_across_ranks() -> None:
    data = edited(lambda d: law_of(d).update({"decision_maker_roles": [["COO", "Owner"], ["owner"]]}))

    assert_problem(data, "segments[law].decision_maker_roles[1][0]", "repeats a title")


def test_rule_7_titles_within_one_rank_are_unique_too() -> None:
    data = edited(lambda d: law_of(d).update({"decision_maker_roles": [["COO", "coo"]]}))

    assert_problem(data, "segments[law].decision_maker_roles[0][1]", "repeats a title")


def test_the_first_problem_in_file_order_is_reported() -> None:
    def edit(data: dict[str, Any]) -> None:
        data["segments"][1]["name"] = ""  # a later segment
        signal_of(data, 2)["weight"] = 0  # earlier in the file
        signal_of(data, 1)["evidence"] = ["count"]  # earlier still

    assert_problem(edited(edit), "segments[law].signal_types[law_t2].evidence", "exactly [quote]")


def test_fields_are_checked_in_the_files_order() -> None:
    data = seed_file()
    law = law_of(data)
    reordered = {"name": "", **{k: v for k, v in law.items() if k != "name"}}
    reordered["key"] = "Bad Key"
    data["segments"][0] = reordered

    assert_problem(data, "segments[0].name", "must not be blank")


def test_the_problems_of_the_invalid_rule_row_name_their_field() -> None:
    """The I/O matrix's "Invalid rule" row: each case with its exact field path."""
    cases = [
        (lambda d: signal_of(d, 2).update({"weight": 70}), "segments[law].signal_types[law_t3].weight"),
        (
            lambda d: signal_of(d, 1).update({"evidence": ["quote", "absence"]}),
            "segments[law].signal_types[law_t2].evidence",
        ),
        (lambda d: signal_of(d, 1).update({"score_only": True}), "segments[law].signal_types[law_t2].score_only"),
        (lambda d: signal_of(d, 2).update({"key": "law_t2"}), "segments[law].signal_types[2].key"),
        (
            lambda d: signal_of(d, 1).update({"name": "ABA Formal Opinion 512"}),
            "segments[law].signal_types[law_t2].name",
        ),
        (lambda d: signal_of(d, 1).update({"key": "law_sra_notice"}), "segments[law].signal_types[law_sra_notice].key"),
    ]
    for edit, field in cases:
        assert problem(edited(edit)).field == field


# represents_market_fact ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "SRA",
        "sra",
        "the SRA notice",
        "SRA's warning",
        "law_sra_notice",
        "sra-notice",
        "Solicitors Regulation Authority",
        "the solicitors  regulation-authority notice",
        "ABA Formal Opinion 512",
        "Formal Opinion",
        "formal_opinion",
        "formal-opinion 512",
        "ABA 512",
        "512 (ABA)",
        "aba/512",
        "law_aba_512",
    ],
)
def test_market_fact_texts_are_recognised(text: str) -> None:
    assert represents_market_fact(text)


@pytest.mark.parametrize(
    "text",
    [
        "Hiring intake, legal-ops, knowledge-management, or innovation staff",
        "Post about AI or client confidentiality",
        "Contact form only (no chat, booking, or after-hours line)",
        "law_hiring_legal_ops",
        "Israel",
        "sras",
        "ABA therapy",
        "512 lawyers",
        "Opinion 512",
        "formal opinions",
        "informal opinion",
        "regulation authority",
        "ABA5 12",
        "",
    ],
)
def test_safe_texts_are_not_market_facts(text: str) -> None:
    assert not represents_market_fact(text)


# titles() -----------------------------------------------------------------------------------------


def test_titles_are_the_ranks_then_the_preferred_roles_not_already_ranked() -> None:
    def edit(data: dict[str, Any]) -> None:
        law_of(data)["decision_maker_roles"] = [["COO", "VP Operations"], ["Owner", "CEO"]]
        signal_of(data, 0)["preferred_roles"] = [
            {"when_terms": ["accounts payable"], "roles": ["Controller", "ceo", "CFO"]},
            {"when_terms": ["intake"], "roles": ["cfo", "Finance Director"]},
        ]

    law, _ = validate_segments(edited(edit))

    assert law.titles() == (
        "COO",
        "VP Operations",
        "Owner",
        "CEO",
        "Controller",
        "CFO",
        "Finance Director",
    )


def test_titles_without_preferred_roles_are_the_ranks() -> None:
    data = edited(lambda d: signal_of(d, 0).pop("preferred_roles"))

    assert validate_segments(data)[0].titles() == ("Managing Partner", "Owner")


# The stored form ----------------------------------------------------------------------------------


def test_definition_text_is_canonical_json_of_the_file_form() -> None:
    data = seed_file()
    law_of(data)["name"] = "Law – 5–50"  # non-ASCII stays as it is
    law, _ = validate_segments(data)

    text = definition_text(law)

    assert text == json.dumps(
        json.loads(text), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert "Law – 5–50" in text
    assert " " not in text.replace("Law – 5–50", "").replace("Segment", "").replace(
        "Managing Partner", ""
    ).replace("Fewer than 5 lawyers", "").replace("law firm", "").replace(
        "accounts payable", ""
    ).replace("Signal ", "")
    expected = copy.deepcopy(law_of(data))
    for item in expected["signal_types"]:
        item.setdefault("match_terms", [])
        item.setdefault("preferred_roles", [])
    expected["discovery_queries"].setdefault(  # Story 8.4: always written
        "registry", {"keywords": [], "sic_codes": []}
    )
    assert json.loads(text) == expected


def test_definition_text_writes_enums_as_values_and_every_optional_field() -> None:
    _, ops = validate_segments(seed_file())

    stored = json.loads(definition_text(ops))

    assert stored["signal_types"][1] == {
        "key": "ops_t2",
        "name": "Signal ops_t2",
        "class": "event",
        "tier": 2,
        "evidence": ["quote"],
        "sources": ["signal_inbox"],
        "score_only": False,
        "weight": 35,
        "match_terms": [],
        "preferred_roles": [],
    }
    assert stored["size_range"] == {"minimum": 5, "maximum": 50, "unit": "lawyers"}


def test_segment_from_text_is_the_inverse_of_definition_text() -> None:
    for seg in validate_segments(seed_file()):
        assert segment_from_text(definition_text(seg)) == seg


def test_the_same_definition_always_gives_the_same_text() -> None:
    first = validate_segments(seed_file())
    data = seed_file()
    law_of(data)["size_range"] = {"unit": "lawyers", "maximum": 50, "minimum": 5}  # reordered
    second = validate_segments(data)

    assert [definition_text(s) for s in first] == [definition_text(s) for s in second]


@pytest.mark.parametrize(
    "text",
    ["", "not json", "{", "[1, 2]", "null", '{"key": NaN}', b"{}"],
)
def test_a_stored_text_that_is_not_a_segment_raises(text: object) -> None:
    with pytest.raises(InvalidSegments):
        segment_from_text(text)  # type: ignore[arg-type]


def test_a_stored_text_that_is_not_json_names_the_definition() -> None:
    with pytest.raises(InvalidSegments) as caught:
        segment_from_text("not json")

    assert (caught.value.field, caught.value.reason) == ("definition", "is not valid JSON")


def test_a_stored_text_with_a_repeated_field_raises() -> None:
    law, _ = validate_segments(seed_file())
    text = definition_text(law).replace('{"decision_maker_roles"', '{"key":"law","decision_maker_roles"', 1)

    with pytest.raises(InvalidSegments) as caught:
        segment_from_text(text)

    assert caught.value.field == "definition"


def test_a_stored_text_that_no_longer_validates_raises_naming_its_field() -> None:
    law, _ = validate_segments(seed_file())
    stored = json.loads(definition_text(law))
    stored["signal_types"][1]["weight"] = 0
    text = json.dumps(stored)

    with pytest.raises(InvalidSegments) as caught:
        segment_from_text(text)

    assert caught.value.field == "segments[law].signal_types[law_t2].weight"


def test_a_stored_text_is_validated_as_one_segment() -> None:
    law, _ = validate_segments(seed_file())
    stored = json.loads(definition_text(law))
    stored["disqualifiers"].append({**stored["disqualifiers"][0]})

    with pytest.raises(InvalidSegments) as caught:
        segment_from_text(json.dumps(stored))

    assert caught.value.field == "segments[law].disqualifiers[1].key"
