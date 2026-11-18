"""``nextron.domain.contacts`` (FR-14, FR-15, FR-51): the Provenance sources and subjects,
``ContactCandidate``'s rules, each refusal naming its field and never the value, and (Story 3.7)
``provenance_recorded``'s table."""

import dataclasses
from dataclasses import dataclass

import pytest

from nextron.domain.contacts import (
    MAX_TEXT_LENGTH,
    ContactCandidate,
    InvalidContact,
    ProvenanceSource,
    ProvenanceSubject,
    check_name,
    check_role,
    provenance_recorded,
)
from nextron.domain.errors import DomainError

LI = "https://www.linkedin.com/in/jane-doe"
PLANTED = "plantedzq"


def test_the_sources_and_subjects() -> None:
    assert [str(source) for source in ProvenanceSource] == [
        "csv",
        "website",
        "signal_inbox",
        "referral",
        "replier",
        "operator",
        "pattern",
        "finder",
        "eligibility_basis",
    ]
    assert [str(subject) for subject in ProvenanceSubject] == ["contact", "address"]


def test_a_full_candidate_keeps_its_fields() -> None:
    candidate = ContactCandidate(
        name="Jane Doe", role="Managing Partner", email="jane@smith-law.co.uk", linkedin_url=LI
    )

    assert (candidate.name, candidate.role, candidate.email, candidate.linkedin_url) == (
        "Jane Doe",
        "Managing Partner",
        "jane@smith-law.co.uk",
        LI,
    )
    assert candidate.name_key == "jane doe"
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.name = "x"  # type: ignore[misc]
    assert not hasattr(candidate, "__dict__")


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "Jane"},
        {"email": "jane@firm.com"},
        {"name": "J", "role": "x" * MAX_TEXT_LENGTH},
        {"name": "x" * MAX_TEXT_LENGTH},
        {"email": "jane@firm.com", "linkedin_url": LI},
    ],
)
def test_a_name_or_an_email_is_enough(fields: dict[str, str]) -> None:
    ContactCandidate(**fields)


def test_name_key_is_none_without_a_name() -> None:
    assert ContactCandidate(email="jane@firm.com").name_key is None
    assert ContactCandidate(name="JANE  doe!").name_key == "jane doe"


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({}, "identity"),
        ({"role": "Partner"}, "identity"),
        ({"linkedin_url": LI}, "identity"),
        ({"name": ""}, "name"),
        ({"name": " Jane"}, "name"),
        ({"name": "Jane "}, "name"),
        ({"name": "x" * (MAX_TEXT_LENGTH + 1)}, "name"),
        ({"name": "Ja\x00ne"}, "name"),
        ({"name": "Ja​ne"}, "name"),
        ({"name": "Ja ne"}, "name"),
        ({"name": "Ja ne"}, "name"),
        ({"name": "---"}, "name"),  # no letters or digits: no name key
        ({"name": 7}, "name"),
        ({"name": "Jane", "role": ""}, "role"),
        ({"name": "Jane", "role": " CEO"}, "role"),
        ({"name": "Jane", "role": "x" * (MAX_TEXT_LENGTH + 1)}, "role"),
        ({"name": "Jane", "role": "C‮EO"}, "role"),
        ({"email": "Jane@Firm.com"}, "email"),  # not normalised
        ({"email": "jane"}, "email"),
        ({"email": b"jane@firm.com"}, "email"),
        ({"name": "Jane", "linkedin_url": "linkedin.com/in/jane-doe"}, "linkedin_url"),
        ({"name": "Jane", "linkedin_url": "https://www.linkedin.com/company/x"}, "linkedin_url"),
    ],
)
def test_each_refusal_names_its_field(fields: dict[str, object], field: str) -> None:
    with pytest.raises(InvalidContact) as caught:
        ContactCandidate(**fields)  # type: ignore[arg-type]

    assert caught.value.field == field
    assert caught.value.message == f"{field}: {caught.value.reason}"


@pytest.mark.parametrize(
    "fields",
    [
        {"name": PLANTED + "\x00"},
        {"name": "Jane", "role": PLANTED * 30},
        {"email": PLANTED + "@Firm.com"},
        {"name": "Jane", "linkedin_url": f"https://{PLANTED}.com/in/x"},
    ],
)
def test_no_refusal_shows_the_value(fields: dict[str, str]) -> None:
    with pytest.raises(InvalidContact) as caught:
        ContactCandidate(**fields)

    assert PLANTED not in caught.value.message
    assert PLANTED not in repr(caught.value.args)


def test_invalid_contact_is_a_domain_error() -> None:
    error = InvalidContact("name", "must be a str or None")

    assert isinstance(error, DomainError)
    assert error.code == "invalid_contact"


def test_check_name_and_check_role_apply_the_text_rules_alone() -> None:
    check_name("Jane Doe")
    check_role("Head of Legal Ops")
    with pytest.raises(InvalidContact, match="^name:"):
        check_name("!!!")
    check_role("!!!")  # a role needs no name key
    with pytest.raises(InvalidContact, match="^role:"):
        check_role("x" * (MAX_TEXT_LENGTH + 1))


# provenance_recorded (Story 3.7) ----------------------------------------------------------------

EMAIL = "jane@firm.com"


@dataclass(frozen=True, slots=True)
class Record:
    """A stand-in for a stored Provenance Record: only what ``provenance_recorded`` reads."""

    subject: str
    source_kind: str
    address: str | None = None


CONTACT = Record("contact", "csv")
ADDRESS = Record("address", "csv", EMAIL)
BASIS = Record("contact", "eligibility_basis")
BASIS_ADDRESS = Record("address", "eligibility_basis", EMAIL)  # never written; still ignored


@pytest.mark.parametrize(
    ("email", "records", "recorded"),
    [
        (None, [CONTACT], True),
        (EMAIL, [CONTACT, ADDRESS], True),
        (EMAIL, [ADDRESS, CONTACT], True),  # order doesn't matter
        (None, [CONTACT, BASIS], True),
        (EMAIL, [BASIS, ADDRESS, CONTACT], True),
        (None, [Record("contact", "operator")], True),
        (None, [Record("contact", "carrier_pigeon")], True),  # an unknown stored source counts
        (None, [], False),
        (EMAIL, [], False),
        (None, [BASIS], False),  # a basis isn't a source
        (EMAIL, [CONTACT], False),  # the email has no address record
        (EMAIL, [ADDRESS], False),  # no contact record
        (EMAIL, [CONTACT, Record("address", "csv", "other@firm.com")], False),
        (EMAIL, [CONTACT, Record("address", "csv", "Jane@firm.com")], False),  # exact address
        (EMAIL, [CONTACT, BASIS_ADDRESS], False),
        (EMAIL, [BASIS, ADDRESS], False),
        (None, [ADDRESS], False),
    ],
)
def test_provenance_recorded(email: str | None, records: list[Record], recorded: bool) -> None:
    assert provenance_recorded(email, records) is recorded


def test_provenance_recorded_takes_any_iterable_once() -> None:
    assert provenance_recorded(EMAIL, iter([CONTACT, ADDRESS])) is True
    assert provenance_recorded(EMAIL, (record for record in [ADDRESS, CONTACT])) is True
