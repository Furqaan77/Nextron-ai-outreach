"""``nextron.domain.leads``: ``LeadSource``, ``LeadIdentity`` (each refusal names its field and
never shows the value) and the two error codes."""

import dataclasses

import pytest

from nextron.domain.errors import DomainError
from nextron.domain.leads import (
    KEY_FIELDS,
    InvalidLeadIdentity,
    LeadIdentity,
    LeadSource,
    MergeRefused,
)

SEGMENT = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
PLANTED = "plantedzq"


def test_the_sources_are_the_five_the_epics_name() -> None:
    assert [str(source) for source in LeadSource] == [
        "csv",
        "signal_inbox",
        "places",
        "companies_house",
        "job_aggregator",
    ]


def test_a_valid_identity_keeps_its_fields() -> None:
    identity = LeadIdentity(
        name="Smith Law LLP",
        segment_id=SEGMENT,
        web_domain="smith-law.co.uk",
        ch_number="OC123456",
        place_id="ChIJN1t_tDeuEmsRUsoyG83frY4",
        country_code="GB",
    )

    assert identity.keys() == {
        "web_domain": "smith-law.co.uk",
        "ch_number": "OC123456",
        "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
    }
    assert KEY_FIELDS == ("web_domain", "ch_number", "place_id")
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.name = "Other"  # type: ignore[misc]


@pytest.mark.parametrize("field", KEY_FIELDS)
def test_one_key_is_enough(field: str) -> None:
    value = {"web_domain": "example.com", "ch_number": "00012345", "place_id": "abc"}[field]

    identity = LeadIdentity(name="X", segment_id=SEGMENT, **{field: value})

    assert identity.keys() == {field: value}
    assert identity.country_code is None


@pytest.mark.parametrize("name", ["A", "x" * 200, "Smith & Jones, LLP", "Müller GmbH"])
def test_a_name_of_1_to_200_clean_characters_passes(name: str) -> None:
    assert LeadIdentity(name=name, segment_id=SEGMENT, web_domain="example.com").name == name


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "x" * 201}, "name"),
        ({"name": f" {PLANTED}"}, "name"),
        ({"name": f"{PLANTED}\n"}, "name"),
        ({"name": f"{PLANTED}\x00Ltd"}, "name"),
        ({"name": f"{PLANTED}​Ltd"}, "name"),  # Cf
        ({"name": f"{PLANTED}‮Ltd"}, "name"),  # a bidi override, Cf
        ({"name": f"{PLANTED} Ltd"}, "name"),  # Zl
        ({"name": f"{PLANTED} Ltd"}, "name"),  # Zp
        ({"name": None}, "name"),
        ({"name": 7}, "name"),
        ({"segment_id": PLANTED}, "segment_id"),
        ({"segment_id": SEGMENT.lower()}, "segment_id"),
        ({"segment_id": None}, "segment_id"),
        ({"web_domain": f"www.{PLANTED}.com"}, "web_domain"),
        ({"web_domain": f"{PLANTED}.COM"}, "web_domain"),
        ({"web_domain": f"https://{PLANTED}.com"}, "web_domain"),
        ({"web_domain": f"{PLANTED}.local"}, "web_domain"),
        ({"web_domain": b"example.com"}, "web_domain"),
        ({"ch_number": "12345"}, "ch_number"),
        ({"ch_number": "sc123456"}, "ch_number"),
        ({"ch_number": PLANTED}, "ch_number"),
        ({"place_id": f" {PLANTED}"}, "place_id"),
        ({"place_id": f"{PLANTED}!"}, "place_id"),
        ({"place_id": 7}, "place_id"),
        ({"country_code": "gb"}, "country_code"),
        ({"country_code": "UK"}, "country_code"),
        ({"country_code": PLANTED}, "country_code"),
        ({"country_code": 44}, "country_code"),
        ({"web_domain": None}, "keys"),
    ],
)
def test_each_refusal_names_its_field_and_never_the_value(
    changes: dict[str, object], field: str
) -> None:
    fields: dict[str, object] = {"name": "Smith Law", "segment_id": SEGMENT}
    fields["web_domain"] = "example.com"
    fields.update(changes)

    with pytest.raises(InvalidLeadIdentity) as caught:
        LeadIdentity(**fields)  # type: ignore[arg-type]

    error = caught.value
    assert isinstance(error, DomainError)
    assert error.code == "invalid_lead_identity"
    assert error.field == field
    assert error.message == f"{field}: {error.reason}"
    assert PLANTED not in error.message
    assert PLANTED not in repr(error.args)


def test_merge_refused_carries_its_reason() -> None:
    error = MergeRefused("the survivor is newer than the Lead to merge")

    assert isinstance(error, DomainError)
    assert error.code == "merge_refused"
    assert error.message == error.reason == "the survivor is newer than the Lead to merge"
