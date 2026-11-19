"""Story 8.4's pure rules: ``nextron.domain.registry_discovery`` (the searches, the query hash,
inactive companies, officer names and kept officers), the Segments' ``registry`` queries, the
registry-only legal-form finding and the registry quote's accounts category and SIC codes."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.job import idempotency_key
from nextron.domain.legal_form import (
    LegalFormFinding,
    LegalFormStatus,
    SiteQuote,
    registry_quote,
)
from nextron.domain.registry_discovery import (
    DISCOVER_REGISTRY_JOB,
    KEPT_ROLES,
    is_inactive,
    kept_officer,
    officer_name,
    officers_detail,
    registry_query_hash,
    registry_searches,
)
from nextron.domain.segments import (
    InvalidSegments,
    RegistryQueries,
    SegmentDef,
    definition_text,
    segment_from_text,
    validate_segments,
)

AT: Final = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
NUMBER: Final = "01234567"
CAMPAIGN: Final = "01JD0000000000000000000000"


def shipped() -> tuple[SegmentDef, ...]:
    text = (main.SEED_DIR / main.SEGMENTS_FILE_NAME).read_text(encoding="utf-8")
    return validate_segments(seed_yaml.parse(text))


def law() -> SegmentDef:
    return next(segment for segment in shipped() if segment.key == "law_private_ai")


@dataclass(frozen=True)
class Officer:
    name: str
    role: str = "director"
    occupation: str | None = "Managing Partner"
    resigned: bool = False


# The Segments' registry queries -----------------------------------------------------------------


def test_the_shipped_segments_carry_their_registry_queries() -> None:
    queries = {segment.key: segment.discovery_queries.registry for segment in shipped()}

    assert queries == {
        "law_private_ai": RegistryQueries(("solicitors", "law", "legal"), ("69102",)),
        "ops_automation": RegistryQueries(
            (),
            (
                "46190",
                "46690",
                "46900",
                "49410",
                "52103",
                "52290",
                "22290",
                "25990",
                "28290",
                "32990",
            ),
        ),
    }


def test_a_stored_definition_without_registry_parses_with_empty_queries() -> None:
    segment = law()
    data = json.loads(definition_text(segment))
    del data["discovery_queries"]["registry"]

    found = segment_from_text(json.dumps(data))

    assert found.discovery_queries.registry == RegistryQueries((), ())
    assert found.discovery_queries.places == segment.discovery_queries.places


def test_the_stored_form_round_trips_the_registry_queries() -> None:
    for segment in shipped():
        assert segment_from_text(definition_text(segment)) == segment


@pytest.mark.parametrize(
    ("registry", "field"),
    [
        ({"keywords": [], "sic_codes": ["6910"]}, "sic_codes[0]"),
        ({"keywords": [], "sic_codes": [69102]}, "sic_codes[0]"),
        ({"keywords": [], "sic_codes": ["69102", "69102"]}, "sic_codes[1]"),
        ({"keywords": [" law"], "sic_codes": []}, "keywords[0]"),
        ({"keywords": []}, "sic_codes"),
        ({"keywords": [], "sic_codes": [], "extra": []}, "extra"),
    ],
)
def test_a_bad_registry_query_is_refused(registry: dict[str, object], field: str) -> None:
    data = json.loads(definition_text(law()))
    data["discovery_queries"]["registry"] = registry

    with pytest.raises(InvalidSegments) as refused:
        segment_from_text(json.dumps(data))

    assert refused.value.field.endswith(f"discovery_queries.registry.{field}")


# Searches, hash, job key ------------------------------------------------------------------------


def test_each_keyword_searches_with_every_code_and_no_keyword_is_one_sic_search() -> None:
    assert registry_searches(RegistryQueries(("law", "legal"), ("69102", "69109"))) == (
        ("law", ("69102", "69109")),
        ("legal", ("69102", "69109")),
    )
    assert registry_searches(RegistryQueries((), ("46190",))) == ((None, ("46190",)),)
    assert registry_searches(RegistryQueries(("law",), ())) == ()


def test_the_query_hash_is_16_hex_of_the_canonical_queries() -> None:
    queries = RegistryQueries(("law",), ("69102",))
    hashed = registry_query_hash(queries)

    assert len(hashed) == 16 and set(hashed) <= set("0123456789abcdef")
    assert hashed != registry_query_hash(RegistryQueries(("legal",), ("69102",)))
    assert hashed == registry_query_hash(RegistryQueries(("law",), ("69102",)))
    assert idempotency_key(DISCOVER_REGISTRY_JOB, CAMPAIGN, hashed) == (
        f"discover_registry:{CAMPAIGN}:{hashed}"
    )


@pytest.mark.parametrize(
    ("status", "accounts", "inactive"),
    [
        ("active", "full", False),
        ("active", None, False),
        ("active", "dormant", True),
        ("dissolved", "full", True),
        ("liquidation", None, True),
        (None, None, True),
    ],
)
def test_inactive_means_not_active_or_dormant(
    status: str | None, accounts: str | None, inactive: bool
) -> None:
    assert is_inactive(status, accounts) is inactive


# Officers ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("SMITH, Jane", "Jane Smith"),
        ("SMITH, Jane Mary", "Jane Mary Smith"),
        ("O'NEILL, Mary", "Mary O'Neill"),
        ("SMITH-JONES, Ann", "Ann Smith-Jones"),
        ("McDonald, Ian", "Ian McDonald"),  # not all capitals: kept as it is
        ("SMITH, John, Dr", "John Dr Smith"),
        ("  SMITH ,   Jane  ", "Jane Smith"),
        ("Jane Smith", "Jane Smith"),
    ],
)
def test_officer_name_reads_surname_comma_forenames(raw: str, name: str) -> None:
    assert officer_name(raw) == name


@pytest.mark.parametrize("raw", [",", "   ,  ", "x" * 300])
def test_officer_name_is_none_when_it_is_no_contact_name(raw: str) -> None:
    assert officer_name(raw) is None


def test_only_active_natural_person_decision_makers_are_kept() -> None:
    segment = law()

    kept = kept_officer(Officer("SMITH, Jane"), segment)

    assert kept == ContactCandidate(name="Jane Smith", role="Managing Partner")
    assert kept_officer(Officer("SMITH, Jane", role="llp-designated-member"), segment) == kept
    assert kept_officer(Officer("SMITH, Jane", resigned=True), segment) is None
    assert kept_officer(Officer("ACME LTD", role="corporate-director"), segment) is None
    assert kept_officer(Officer("SMITH, Jane", role="secretary"), segment) is None
    assert kept_officer(Officer("SMITH, Jane", occupation=None), segment) is None
    assert kept_officer(Officer("SMITH, Jane", occupation="Accountant"), segment) is None
    assert kept_officer(Officer(",", occupation="Owner"), segment) is None
    assert KEPT_ROLES == frozenset({"director", "llp-designated-member"})


def test_the_officers_provenance_detail_names_the_api_and_the_company() -> None:
    assert officers_detail(NUMBER) == "Companies House API officers, company 01234567"
    assert ProvenanceSource.REGISTRY == "registry"


# The registry-only finding and the quote --------------------------------------------------------


def quote(**extra: object) -> str:
    return registry_quote(
        number=NUMBER,
        name="ACME LIMITED",
        company_type="ltd",
        company_status="active",
        office_postcode="LS1 4AP",
        **extra,  # type: ignore[arg-type]
    )


def test_the_registry_quote_adds_accounts_and_sic_codes_only_when_given() -> None:
    plain = (
        "Companies House record 01234567: company type ltd, company status active, "
        "registered office postcode LS1 4AP, registered name ACME LIMITED"
    )

    assert quote() == plain
    assert quote(accounts_category="full", sic_codes=("69102", "69109")) == (
        "Companies House record 01234567: company type ltd, company status active, "
        "registered office postcode LS1 4AP, accounts category full, SIC codes 69102 69109, "
        "registered name ACME LIMITED"
    )
    assert quote(sic_codes=("69102",)).endswith("postcode LS1 4AP, SIC codes 69102, registered name ACME LIMITED")
    with pytest.raises(TypeError):
        quote(sic_codes=["69102"])


def test_a_registry_only_finding_is_unconfirmed_with_its_record() -> None:
    finding = LegalFormFinding(
        LegalFormStatus.UNCONFIRMED, NUMBER, "ltd", "active", None, quote(), AT
    )

    assert finding.registry_only and finding.linked


@pytest.mark.parametrize(
    ("status", "legal_form", "company_status", "registry", "at"),
    [
        (LegalFormStatus.CONFIRMED, "ltd", "active", True, True),
        (LegalFormStatus.UNCONFIRMED, None, "active", True, True),
        (LegalFormStatus.UNCONFIRMED, "ltd", None, True, True),
        (LegalFormStatus.UNCONFIRMED, "ltd", "active", False, True),
        (LegalFormStatus.UNCONFIRMED, "ltd", "active", True, False),
    ],
)
def test_a_registry_only_finding_needs_everything_and_never_confirms(
    status: LegalFormStatus,
    legal_form: str | None,
    company_status: str | None,
    registry: bool,
    at: bool,
) -> None:
    with pytest.raises(ValueError):
        LegalFormFinding(
            status,
            NUMBER,
            legal_form,
            company_status,
            None,
            quote() if registry else None,
            AT if at else None,
        )


def test_a_finding_with_a_site_quote_is_not_registry_only() -> None:
    site = SiteQuote("https://acme.co.uk/", "company number 01234567", AT)

    finding = LegalFormFinding(
        LegalFormStatus.CONFIRMED, NUMBER, "ltd", "active", site, quote(), AT
    )

    assert not finding.registry_only
