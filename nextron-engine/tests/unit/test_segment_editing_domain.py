"""Story 11.5's additions to ``nextron/domain/segments.py``: the optional discovery queries
(Companies House keywords and SIC codes, aggregator job titles), byte-stable stored texts, the
editing rule, the form diff and the editor's form fields."""

import copy
import dataclasses
import json
from typing import Any

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.segments import (
    EDITING_RULE,
    FORM_FIELD,
    DiscoveryQueries,
    FieldChange,
    InvalidSegments,
    SegmentRefused,
    SignalClass,
    definition_text,
    edited_field,
    file_form,
    segment_editing_rule,
    segment_form_diff,
    segment_from_text,
    validate_segments,
)
from tests.unit.test_segments_domain import law_of, segment, seed_file, signal

SHIPPED_TEXT = (main.SEED_DIR / main.SEGMENTS_FILE_NAME).read_text(encoding="utf-8")


def shipped() -> tuple[Any, ...]:
    return validate_segments(seed_yaml.parse(SHIPPED_TEXT))


def old_text(segment_def: Any) -> str:
    """The text Story 2.1's definition_text gave: discovery_queries held only places."""
    form = file_form(segment_def)
    form["discovery_queries"] = {"places": list(segment_def.discovery_queries.places)}
    return json.dumps(form, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# The optional discovery queries ------------------------------------------------------------------


def test_the_new_queries_default_to_empty() -> None:
    law, _ = validate_segments(seed_file())

    assert law.discovery_queries == DiscoveryQueries(("law firm",))
    assert law.discovery_queries.registry_keywords == ()
    assert law.discovery_queries.registry_sic_codes == ()
    assert law.discovery_queries.job_titles == ()


def test_the_new_queries_are_read_from_the_file_form() -> None:
    data = seed_file()
    law_of(data)["discovery_queries"] = {
        "places": ["law firm"],
        "registry": {"keywords": ["solicitors"], "sic_codes": ["69102", "6910"]},
        "job_titles": ["Legal Operations Manager"],
    }

    law, _ = validate_segments(data)

    assert law.discovery_queries == DiscoveryQueries(
        ("law firm",), ("solicitors",), ("69102", "6910"), ("Legal Operations Manager",)
    )


@pytest.mark.parametrize(
    "queries",
    [
        {"places": ["law firm"], "registry": {}},
        {"places": ["law firm"], "registry": {"keywords": []}},
        {"places": ["law firm"], "registry": {"sic_codes": []}, "job_titles": []},
    ],
)
def test_the_new_queries_may_be_empty_or_left_out(queries: dict[str, Any]) -> None:
    data = seed_file()
    law_of(data)["discovery_queries"] = queries

    law, _ = validate_segments(data)

    assert law.discovery_queries == DiscoveryQueries(("law firm",))


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("691", "a SIC code of 4 or 5 digits"),
        ("691020", "a SIC code of 4 or 5 digits"),
        ("69a02", "a SIC code of 4 or 5 digits"),
        ("６９１０２", "a SIC code of 4 or 5 digits"),  # full-width digits aren't ASCII
        (69102, "must be text"),
    ],
)
def test_a_sic_code_is_4_or_5_ascii_digits(code: object, needle: str) -> None:
    data = seed_file()
    law_of(data)["discovery_queries"]["registry"] = {"sic_codes": ["69102", code]}

    with pytest.raises(InvalidSegments) as caught:
        validate_segments(data)

    assert caught.value.field == "segments[law].discovery_queries.registry.sic_codes[1]"
    assert needle in caught.value.reason


@pytest.mark.parametrize(
    ("change", "field", "needle"),
    [
        ({"registry": {"other": []}}, "discovery_queries.registry.other", "unknown field"),
        ({"registry": []}, "discovery_queries.registry", "must be a mapping"),
        ({"job_titles": ["a", "A"]}, "discovery_queries.job_titles[1]", "repeats"),
        ({"registry": {"keywords": [" x"]}}, "discovery_queries.registry.keywords[0]", "space"),
    ],
)
def test_the_new_queries_follow_the_list_rules(
    change: dict[str, Any], field: str, needle: str
) -> None:
    data = seed_file()
    law_of(data)["discovery_queries"].update(change)

    with pytest.raises(InvalidSegments) as caught:
        validate_segments(data)

    assert caught.value.field == f"segments[law].{field}"
    assert needle in caught.value.reason


# Byte stability and the round trip ---------------------------------------------------------------


def test_the_shipped_texts_are_byte_identical_to_story_2_1s() -> None:
    for each in shipped():
        assert definition_text(each) == old_text(each)
        assert '"registry"' not in definition_text(each)
        assert '"job_titles"' not in definition_text(each)


def test_only_non_empty_new_queries_are_stored() -> None:
    law = shipped()[0]
    with_codes = dataclasses.replace(
        law,
        discovery_queries=dataclasses.replace(law.discovery_queries, registry_sic_codes=("69102",)),
    )

    form = file_form(with_codes)

    assert form["discovery_queries"] == {
        "places": list(law.discovery_queries.places),
        "registry": {"sic_codes": ["69102"]},
    }


def test_a_segment_with_every_new_query_round_trips() -> None:
    law = shipped()[0]
    full = dataclasses.replace(
        law,
        discovery_queries=DiscoveryQueries(
            law.discovery_queries.places, ("solicitors",), ("69102",), ("Head of Legal Ops",)
        ),
    )

    assert segment_from_text(definition_text(full)) == full


# The editing rule --------------------------------------------------------------------------------


def test_the_shipped_segments_keep_the_editing_rule() -> None:
    assert [segment_editing_rule(each) for each in shipped()] == [None, None]


def test_a_segment_with_no_tier_1_or_2_event_breaks_the_rule() -> None:
    data = {"segments": [segment("law")]}
    law_of(data)["signal_types"] = [
        signal("law_t3", cls="attribute", tier=3, evidence=("count",), weight=15),
        signal("law_event3", tier=3, weight=10),
    ]
    [law] = validate_segments(data)

    broken = segment_editing_rule(law)

    assert isinstance(broken, InvalidSegments)
    assert (broken.field, broken.reason) == ("segments[law].signal_types", EDITING_RULE)
    assert EDITING_RULE == (
        "A Segment needs a decision-maker role and at least one Tier 1 or 2 Event"
    )


def test_a_tier_2_attribute_does_not_satisfy_the_rule() -> None:
    [law] = validate_segments({"segments": [segment("law")]})
    attributes = tuple(
        dataclasses.replace(each, signal_class=SignalClass.ATTRIBUTE) for each in law.signal_types
    )

    broken = segment_editing_rule(dataclasses.replace(law, signal_types=attributes))

    assert broken is not None and broken.field == "segments[law].signal_types"


def test_a_segment_with_no_role_breaks_the_rule_on_its_roles() -> None:
    [law] = validate_segments({"segments": [segment("law")]})

    broken = segment_editing_rule(dataclasses.replace(law, decision_maker_roles=()))

    assert broken is not None
    assert (broken.field, broken.reason) == ("segments[law].decision_maker_roles", EDITING_RULE)


def test_a_tier_2_event_alone_satisfies_the_rule() -> None:
    data = {"segments": [segment("law")]}
    law_of(data)["signal_types"] = [signal("law_t2", tier=2, weight=35)]
    [law] = validate_segments(data)

    assert segment_editing_rule(law) is None


# The diff ----------------------------------------------------------------------------------------


def test_an_identical_segment_has_no_changes() -> None:
    law = shipped()[0]

    assert segment_form_diff(law, law) == ()


def test_the_diff_names_each_changed_field_with_json_values() -> None:
    data = seed_file()
    old, _ = validate_segments(copy.deepcopy(data))
    law_of(data)["name"] = "Law firms"
    law_of(data)["signal_types"][1]["weight"] = 30
    law_of(data)["size_range"]["maximum"] = 60
    new, _ = validate_segments(data)

    assert segment_form_diff(old, new) == (
        FieldChange("name", '"Segment law"', '"Law firms"'),
        FieldChange("size_range.maximum", "50", "60"),
        FieldChange("signal_types[law_t2].weight", "35", "30"),
    )


def test_an_added_or_removed_item_is_one_change() -> None:
    data = seed_file()
    old, _ = validate_segments(copy.deepcopy(data))
    removed = law_of(data)["signal_types"].pop(1)
    law_of(data)["disqualifiers"] = []
    law_of(data)["discovery_queries"]["job_titles"] = ["Paralegal"]
    new, _ = validate_segments(data)

    changes = segment_form_diff(old, new)

    assert [change.path for change in changes] == [
        "disqualifiers[law_small]",
        "signal_types[law_t2]",
        "discovery_queries.job_titles",
    ]
    assert changes[1].new is None
    assert json.loads(changes[1].old or "") == {**removed, "match_terms": [], "preferred_roles": []}
    assert changes[2] == FieldChange("discovery_queries.job_titles", None, '["Paralegal"]')


# The editor's form fields ------------------------------------------------------------------------


FORM = {
    "signal_types": [{"key": "a"}, {"key": "b"}, {"key": "b"}],
    "disqualifiers": [{"key": "d"}],
}


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("segments[law].name", "name"),
        ("segments[3].key", "key"),
        ("segments[law].size_range.maximum", "size_range.maximum"),
        ("segments[law].industry_keywords[2]", "industry_keywords"),
        ("segments[law].decision_maker_roles[0][1]", "decision_maker_roles"),
        ("segments[law].signal_types[b].weight", "signal_types[1].weight"),
        ("segments[law].signal_types[2].key", "signal_types[2].key"),
        ("segments[law].signal_types[a].match_terms[0]", "signal_types[0].match_terms"),
        (
            "segments[law].signal_types[a].preferred_roles[0].when_terms[1]",
            "signal_types[0].preferred_roles",
        ),
        ("segments[law].disqualifiers[d].threshold", "disqualifiers[0].threshold"),
        ("segments[law].signal_types", "signal_types"),
        (
            "segments[law].discovery_queries.registry.sic_codes[0]",
            "discovery_queries.registry.sic_codes",
        ),
        ("segments[law]", FORM_FIELD),
        ("segments[law].signal_types[zzz].weight", FORM_FIELD),
        ("(top level)", FORM_FIELD),
    ],
)
def test_edited_field_names_the_forms_field(path: str, field: str) -> None:
    assert edited_field(path, FORM) == field


def test_segment_refused_names_fields_never_values() -> None:
    refused = SegmentRefused({"name": "must not be blank", FORM_FIELD: "stale"})

    assert refused.problems == {"name": "must not be blank", FORM_FIELD: "stale"}
    assert refused.message == "segment refused: name, form"
