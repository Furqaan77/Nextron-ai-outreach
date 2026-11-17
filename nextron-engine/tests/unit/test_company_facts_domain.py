"""``nextron.domain.company_facts`` (FR-72, NFR-24, AD-7; Story 4.3): the fact types, the file
validation with its exact field paths, the value rules, rules 1 to 5 with the AC's reasons,
unknown and missing keys, the file form round trip and the Operator's path (its normalising and
its collected problems). Standard library, pytest and ``nextron.domain`` only."""

import copy
import dataclasses
from datetime import date, datetime
from typing import Any

import pytest

from nextron.domain.company_facts import (
    ABA_REASON,
    CHOOSE_A_MARKET,
    CHOOSE_A_SEGMENT,
    CHOOSE_A_TYPE,
    CONFIRMATION_WITHOUT_METRIC_REASON,
    DATE_REASON,
    ENTER_A_VALUE,
    FUTURE_REASON,
    LINK_REASON,
    METRIC_UNCONFIRMED_REASON,
    NO_STORED_MARKET_REASON,
    NO_STORED_SEGMENT_REASON,
    REGULATION_MARKETS_REASON,
    SRA_REASON,
    FactContent,
    FactCreator,
    FactFields,
    FactType,
    InvalidCompanyFacts,
    InvalidFact,
    SeedFact,
    check_fact_references,
    check_seed_facts,
    content_problems,
    date_problems,
    fact_form,
    fact_from_fields,
    fact_from_form,
    is_link,
    validate_company_facts,
)
from nextron.domain.errors import DomainError

LONG = "x" * 41  # a value a reason must never repeat
SEGMENT_KEYS = frozenset({"law_private_ai", "ops_automation"})
MARKET_COUNTRIES = {"us": "US", "uk": "GB"}
LATEST = date(2026, 11, 17)
LAW_ID = "01JD000000000000000000SEG1"  # ULIDs sort as text: SEG1 < SEG2, MKT1 < MKT2
OPS_ID = "01JD000000000000000000SEG2"
US_ID = "01JD000000000000000000MKT1"
UK_ID = "01JD000000000000000000MKT2"
UNKNOWN_ID = "01JD000000000000000000ZZZZ"
CA_ID = "01JD000000000000000000MKT3"
SEGMENT_IDS = frozenset({LAW_ID, OPS_ID})
MARKET_IDS = {US_ID: "US", UK_ID: "GB"}
ABA_WORDING = "ABA Formal Opinion 512 says a lawyer needs the client's informed consent."
SRA_WORDING = "The Solicitors Regulation Authority (SRA) warning notice allows it with safeguards."


def fact(**changes: object) -> dict[str, object]:
    """A valid case-study item of the file, with ``changes`` applied (``None`` removes a key)."""
    item: dict[str, object] = {
        "fact_id": "legal_ai",
        "type": "case_study",
        "wording": "Built an AI legal assistant platform for a legal-tech venture (Legal AI).",
        "source_url": "https://nextronai.com/",
        "segments": ["law_private_ai", "ops_automation"],
        "markets": ["us", "uk"],
        "scope_note": "The client was a legal-tech venture, not a law firm.",
        "last_checked_on": "2026-11-14",
    }
    for key, value in changes.items():
        if value is None:
            item.pop(key, None)
        else:
            item[key] = value
    return item


def regulation(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "fact_id": "aba_formal_opinion_512",
        "type": "regulation",
        "wording": ABA_WORDING,
        "source_url": "https://www.americanbar.org/opinion-512.pdf",
        "segments": ["law_private_ai"],
        "markets": ["us"],
        "scope_note": None,
    }
    base.update(changes)
    return fact(**base)


def file(*items: object) -> dict[str, object]:
    return {"company_facts": list(items)}


def refused(data: object) -> InvalidCompanyFacts:
    with pytest.raises(InvalidCompanyFacts) as caught:
        validate_company_facts(data)
    return caught.value


def content(**changes: Any) -> FactContent:
    base = FactContent(
        type=FactType.CASE_STUDY,
        wording="Built an AI legal assistant platform (Legal AI).",
        source_url="https://nextronai.com/",
        segments=(LAW_ID, OPS_ID),
        markets=(US_ID, UK_ID),
        metric=None,
        metric_confirmed_at=None,
        scope_note=None,
        last_checked_on=date(2026, 11, 14),
    )
    return dataclasses.replace(base, **changes)


def fields(**changes: Any) -> FactFields:
    base = FactFields(
        type="case_study",
        wording="Built an AI legal assistant platform (Legal AI).",
        source_url="https://nextronai.com/",
        segments=(LAW_ID, OPS_ID),
        markets=(US_ID, UK_ID),
        metric="",
        metric_confirmed_at="",
        scope_note="",
        last_checked_on="2026-11-14",
    )
    return dataclasses.replace(base, **changes)


def operator_problems(given: FactFields) -> dict[str, str]:
    with pytest.raises(InvalidFact) as caught:
        fact_from_fields(
            given, segment_refs=SEGMENT_IDS, market_countries=MARKET_IDS, latest=LATEST
        )
    return caught.value.problems


# Types and errors ---------------------------------------------------------------------------------


def test_the_types_and_errors() -> None:
    assert [member.value for member in FactType] == [
        "company",
        "case_study",
        "offer",
        "regulation",
        "statistic",
    ]
    assert [member.value for member in FactCreator] == ["seed", "operator"]
    assert issubclass(InvalidCompanyFacts, DomainError)
    assert InvalidCompanyFacts.code == "invalid_company_facts"
    assert InvalidFact.code == "invalid_fact"
    error = InvalidCompanyFacts("company_facts[x].wording", "missing")
    assert (error.message, error.field, error.reason) == (
        "company_facts[x].wording: missing",
        "company_facts[x].wording",
        "missing",
    )
    problems = InvalidFact({"wording": ENTER_A_VALUE, "form": "No fact has this id"})
    assert problems.problems == {"wording": ENTER_A_VALUE, "form": "No fact has this id"}
    assert problems.message == "invalid fact: wording, form"
    assert dataclasses.fields(FactContent)[0].name == "type"
    with pytest.raises(dataclasses.FrozenInstanceError):
        content().wording = "x"  # type: ignore[misc]


# The file ----------------------------------------------------------------------------------------


def test_a_valid_file_validates_in_file_order_with_sorted_references() -> None:
    first = fact(segments=["ops_automation", "law_private_ai"], metric="40% faster intake",
                 metric_confirmed_at="2026-10-01")
    second = regulation()

    found = validate_company_facts(file(first, second))

    assert [item.fact_id for item in found] == ["legal_ai", "aba_formal_opinion_512"]
    assert found[0].content == FactContent(
        type=FactType.CASE_STUDY,
        wording="Built an AI legal assistant platform for a legal-tech venture (Legal AI).",
        source_url="https://nextronai.com/",
        segments=("law_private_ai", "ops_automation"),
        markets=("uk", "us"),
        metric="40% faster intake",
        metric_confirmed_at=date(2026, 10, 1),
        scope_note="The client was a legal-tech venture, not a law firm.",
        last_checked_on=date(2026, 11, 14),
    )
    assert found[1].content.scope_note is None and found[1].content.markets == ("us",)
    assert isinstance(found[0], SeedFact)


@pytest.mark.parametrize(
    ("data", "field", "reason"),
    [
        ([], "(top level)", "must be a mapping with exactly one field, company_facts"),
        ({}, "company_facts", "missing"),
        ({"company_facts": [], "other": 1}, "company_facts", "must hold at least 1 item"),
        ({"other": 1}, "other", "unknown field"),
        ({"Other Field": 1}, "(top level)", "has an unknown field"),
        ({"company_facts": {}}, "company_facts", "must be a list"),
        ({"company_facts": [fact()] * 201}, "company_facts", "must hold at most 200 items"),
        (file("legal_ai"), "company_facts[0]", "must be a mapping"),
    ],
)
def test_the_file_shape_is_refused_naming_its_field(
    data: object, field: str, reason: str
) -> None:
    error = refused(data)

    assert (error.field, error.reason) == (field, reason)


@pytest.mark.parametrize("key", ["fact_id", "type", "wording", "source_url", "segments",
                                 "markets", "last_checked_on"])
def test_each_required_key_is_refused_when_missing(key: str) -> None:
    error = refused(file(fact(**{key: None})))

    shown = "0" if key == "fact_id" else "legal_ai"
    assert (error.field, error.reason) == (f"company_facts[{shown}].{key}", "missing")


@pytest.mark.parametrize("key", ["metric", "metric_confirmed_at", "scope_note"])
def test_the_optional_keys_may_be_left_out(key: str) -> None:
    [found] = validate_company_facts(file(fact(**{key: None})))

    assert getattr(found.content, key) is None


def test_an_unknown_key_is_refused_where_it_appears() -> None:
    assert refused(file(fact(price="$1"))).field == "company_facts[legal_ai].price"
    assert refused(file(fact(price="$1"))).reason == "unknown field"
    item = fact()
    item[LONG] = 1
    assert (refused(file(item)).field, refused(file(item)).reason) == (
        "company_facts[legal_ai]",
        "has an unknown field",
    )


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("Legal_AI", "must be lowercase letters, digits and underscores"),
        ("1legal", "must be lowercase letters"),
        ("a" * 32, "at most 31 characters"),
        (7, "must be lowercase letters"),
    ],
)
def test_a_bad_fact_id_is_named_by_its_index(value: object, reason: str) -> None:
    error = refused(file(fact(fact_id=value)))

    assert error.field == "company_facts[0].fact_id"
    assert reason in error.reason


def test_a_fact_id_of_31_characters_is_valid() -> None:
    [found] = validate_company_facts(file(fact(fact_id="a" * 31)))

    assert found.fact_id == "a" * 31


def test_a_repeated_fact_id_is_named_by_its_index() -> None:
    error = refused(file(fact(), fact(wording="Another wording.")))

    assert (error.field, error.reason) == (
        "company_facts[1].fact_id",
        "repeats an earlier fact id (legal_ai)",
    )


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("wording", 5, "must be text"),
        ("wording", "   ", "must not be blank"),
        ("wording", " Leading", "must not start or end with whitespace"),
        ("wording", "Two\nlines", "must not hold control, format or line-separator characters"),
        ("wording", "Zero​width", "must not hold control, format or line-separator"),
        ("wording", "Line separator", "must not hold control, format or line-separator"),
        ("wording", "x" * 501, "must be at most 500 characters"),
        ("scope_note", "x" * 501, "must be at most 500 characters"),
        ("scope_note", "", "must not be blank"),
        ("metric", "x" * 201, "must be at most 200 characters"),
        ("type", "case-study", "must be one of company, case_study, offer, regulation, statistic"),
        ("type", True, "must be one of company"),
        ("source_url", "ftp://nextronai.com/", LINK_REASON),
        ("source_url", "javascript:alert(1)", LINK_REASON),
        ("source_url", "//nextronai.com/", LINK_REASON),
        ("source_url", "https://nextronai.com@evil.example/", LINK_REASON),
        ("source_url", "https://user:pass@nextronai.com/", LINK_REASON),
        ("source_url", "https://nextronai.com:99999/", LINK_REASON),
        ("source_url", "https://next ronai.com/", LINK_REASON),
        ("source_url", " https://nextronai.com/", LINK_REASON),
        ("source_url", "https://nextronai.com/‮", LINK_REASON),
        ("source_url", "http:\\\\nextronai.com", LINK_REASON),
        ("source_url", "https:///path", LINK_REASON),
        ("source_url", "https://[::1/", LINK_REASON),
        ("source_url", 5, LINK_REASON),
        ("source_url", "https://nextronai.com/" + "a" * 479, "must be at most 500 characters"),
        ("segments", "law_private_ai", "must be a list"),
        ("segments", [], "must hold at least 1 item"),
        ("markets", [f"m{i}" for i in range(51)], "must hold at most 50 items"),
        ("last_checked_on", "2026-02-30", "must be a real date written YYYY-MM-DD"),
        ("last_checked_on", "2026-2-3", DATE_REASON),
        ("last_checked_on", "14/11/2026", DATE_REASON),
        ("last_checked_on", 20261114, DATE_REASON),
        ("metric_confirmed_at", "2026-13-01", DATE_REASON),
    ],
)
def test_each_value_rule_names_its_field(key: str, value: object, reason: str) -> None:
    changes: dict[str, object] = {key: value}
    if key == "metric":
        changes["metric_confirmed_at"] = "2026-11-01"
    if key == "metric_confirmed_at":
        changes["metric"] = "A confirmed result"

    error = refused(file(fact(**changes)))

    assert error.field == f"company_facts[legal_ai].{key}"
    assert error.reason.startswith(reason) or reason in error.reason
    assert LONG not in error.reason


@pytest.mark.parametrize(
    ("key", "items", "index", "reason"),
    [
        ("segments", ["law_private_ai", "Law"], 1, "must be a Segment key such as law_private_ai"),
        ("segments", ["law_private_ai", "law_private_ai"], 1, "repeats an earlier item"),
        ("segments", ["law_private_ai", 3], 1, "must be a Segment key such as law_private_ai"),
        ("markets", ["us", "united_states_of_america"], 1, "must be a Market code such as us"),
        ("markets", ["uk", "us", "uk"], 2, "repeats an earlier item"),
    ],
)
def test_a_bad_reference_names_its_index(
    key: str, items: list[object], index: int, reason: str
) -> None:
    error = refused(file(fact(**{key: items})))

    assert (error.field, error.reason) == (f"company_facts[legal_ai].{key}[{index}]", reason)


def test_a_ulid_reference_passes_the_value_rules() -> None:
    [found] = validate_company_facts(file(fact(segments=[OPS_ID, LAW_ID], markets=[US_ID])))

    assert found.content.segments == (LAW_ID, OPS_ID)


def test_a_long_value_is_never_repeated_in_a_reason() -> None:
    for item in (fact(type=LONG), fact(source_url=LONG), fact(segments=[LONG]),
                 fact(markets=[LONG]), fact(last_checked_on=LONG)):
        assert LONG not in refused(file(item)).message


def test_the_fields_are_checked_in_file_order_then_missing_then_rules() -> None:
    item = {"wording": "", "fact_id": "legal_ai", "type": "nope"}
    assert refused(file(item)).field == "company_facts[legal_ai].wording"  # the id is shown
    assert refused(file(fact(type="nope", wording=""))).field == "company_facts[legal_ai].type"
    missing = fact(markets=None, metric="A result")
    assert refused(file(missing)).field == "company_facts[legal_ai].markets"  # before rule 2


# Rules 1 and 2 ------------------------------------------------------------------------------------


def test_rule_1_a_regulatory_fact_has_exactly_one_market() -> None:
    error = refused(file(regulation(markets=["us", "uk"], wording="A regulation.")))

    assert (error.field, error.reason) == (
        "company_facts[aba_formal_opinion_512].markets",
        "A regulatory fact applies to one Market",
    )
    assert REGULATION_MARKETS_REASON == "A regulatory fact applies to one Market"
    [ok] = validate_company_facts(file(fact(type="offer", markets=["us", "uk"])))
    assert ok.content.type is FactType.OFFER


def test_rule_2_a_metric_and_its_confirmation_date_come_together() -> None:
    error = refused(file(fact(metric="40% faster intake")))
    assert (error.field, error.reason) == (
        "company_facts[legal_ai].metric",
        "Add a metric only once the client confirms it",
    )
    error = refused(file(fact(metric_confirmed_at="2026-11-01")))
    assert (error.field, error.reason) == (
        "company_facts[legal_ai].metric_confirmed_at",
        "Add the confirmation date only with the client's metric",
    )
    assert METRIC_UNCONFIRMED_REASON.startswith("Add a metric")
    assert CONFIRMATION_WITHOUT_METRIC_REASON.startswith("Add the confirmation date")


def test_rule_1_runs_before_rule_2() -> None:
    error = refused(file(regulation(markets=["us", "uk"], metric="A result")))

    assert error.field.endswith(".markets")


# Rules 3 to 5 -------------------------------------------------------------------------------------


def seed(*items: dict[str, object]) -> tuple[SeedFact, ...]:
    return validate_company_facts(file(*items))


def seed_refused(items: tuple[SeedFact, ...], latest: date = LATEST) -> InvalidCompanyFacts:
    with pytest.raises(InvalidCompanyFacts) as caught:
        check_seed_facts(items, SEGMENT_KEYS, MARKET_COUNTRIES, latest)
    return caught.value


@pytest.mark.parametrize(
    ("item", "field", "reason"),
    [
        (regulation(markets=["uk"]), "company_facts[aba_formal_opinion_512].markets", ABA_REASON),
        (
            fact(wording="Built it under ABA Formal Opinion 512 rules."),
            "company_facts[legal_ai].markets",
            ABA_REASON,
        ),
        (
            regulation(fact_id="sra_ai_notice", wording=SRA_WORDING, markets=["us"]),
            "company_facts[sra_ai_notice].markets",
            SRA_REASON,
        ),
        (
            fact(scope_note="Cite it only beside the SRA guidance."),
            "company_facts[legal_ai].markets",
            SRA_REASON,
        ),
        (
            fact(segments=["law_private_ai", "law_privte_ai"]),
            "company_facts[legal_ai].segments[1]",
            "names law_privte_ai, which isn't a Segment of this sync",
        ),
        (
            fact(markets=["us", "ca"]),
            "company_facts[legal_ai].markets[0]",
            "names ca, which isn't a Market of this sync",
        ),
        (
            fact(segments=[LAW_ID]),
            "company_facts[legal_ai].segments[0]",
            NO_STORED_SEGMENT_REASON,
        ),
        (fact(last_checked_on="2026-11-18"), "company_facts[legal_ai].last_checked_on",
         FUTURE_REASON),
        (
            fact(metric="A result", metric_confirmed_at="2026-11-18"),
            "company_facts[legal_ai].metric_confirmed_at",
            FUTURE_REASON,
        ),
    ],
)
def test_rules_3_to_5_are_named_in_check_seed_facts(
    item: dict[str, object], field: str, reason: str
) -> None:
    error = seed_refused(seed(fact(fact_id="first"), item))

    assert (error.field, error.reason) == (field, reason)


def test_the_floor_reasons_are_the_acs() -> None:
    assert ABA_REASON == "ABA Formal Opinion 512 applies only to a US Market"
    assert SRA_REASON == "The SRA notice applies only to a UK Market"
    assert FUTURE_REASON == "can't be in the future"


def test_the_shipped_shape_passes_and_latest_itself_is_allowed() -> None:
    items = seed(
        fact(),
        regulation(),
        regulation(fact_id="sra_ai_notice", wording=SRA_WORDING, markets=["uk"]),
        fact(fact_id="today", last_checked_on="2026-11-17"),
    )

    assert check_seed_facts(items, SEGMENT_KEYS, MARKET_COUNTRIES, LATEST) is None


def test_check_seed_facts_stops_at_the_first_fact_in_file_order() -> None:
    items = seed(fact(fact_id="aaa", markets=["ca"]), fact(fact_id="zzz", segments=["x"]))

    assert seed_refused(items).field == "company_facts[aaa].markets[0]"


def test_rule_3_runs_before_rule_4_and_rule_4_before_rule_5() -> None:
    both = regulation(markets=["uk"], segments=["nope"], last_checked_on="2026-12-01")
    assert seed_refused(seed(both)).reason == ABA_REASON
    late = fact(segments=["nope"], last_checked_on="2026-12-01")
    assert seed_refused(seed(late)).field.endswith(".segments[0]")


def test_an_unknown_market_is_rule_4s_not_the_floors() -> None:
    error = seed_refused(seed(regulation(markets=["ca"])))

    assert (error.field, error.reason) == (
        "company_facts[aba_formal_opinion_512].markets[0]",
        "names ca, which isn't a Market of this sync",
    )


def test_check_fact_references_and_date_problems_list_every_problem() -> None:
    bad = content(
        wording=ABA_WORDING,
        segments=(LAW_ID, UNKNOWN_ID),
        markets=(UK_ID, CA_ID),
    )

    assert check_fact_references(bad, SEGMENT_IDS, MARKET_IDS) == [
        ("markets", ABA_REASON),
        ("segments[1]", NO_STORED_SEGMENT_REASON),
        ("markets[1]", NO_STORED_MARKET_REASON),
    ]
    assert check_fact_references(content(), SEGMENT_IDS, MARKET_IDS) == []
    late = content(
        metric="A result", metric_confirmed_at=date(2026, 11, 20), last_checked_on=LATEST
    )
    assert date_problems(late, LATEST) == [("metric_confirmed_at", FUTURE_REASON)]


def test_the_sra_floor_and_an_aba_scope_note() -> None:
    assert check_fact_references(
        content(wording=SRA_WORDING, markets=(UK_ID,)), SEGMENT_IDS, MARKET_IDS
    ) == []
    assert check_fact_references(
        content(scope_note="Mentions ABA Formal Opinion 512.", markets=(US_ID, UK_ID)),
        SEGMENT_IDS,
        MARKET_IDS,
    ) == [("markets", ABA_REASON)]


@pytest.mark.parametrize(
    ("metric", "market_id", "code", "reason"),
    [
        ("Cut ABA Formal Opinion 512 consent reviews by 40%", UK_ID, "uk", ABA_REASON),
        ("Passed its SRA notice review in 2 weeks", US_ID, "us", SRA_REASON),
    ],
    ids=["aba-on-uk", "sra-on-us"],
)
def test_a_metric_naming_a_regulation_is_bound_to_its_market_on_every_path(
    metric: str, market_id: str, code: str, reason: str
) -> None:
    confirmed = "2026-11-01"
    found = content(metric=metric, metric_confirmed_at=date(2026, 11, 1), markets=(market_id,))

    assert check_fact_references(found, SEGMENT_IDS, MARKET_IDS) == [("markets", reason)]
    item = fact(metric=metric, metric_confirmed_at=confirmed, markets=[code])
    error = seed_refused(seed(item))
    assert (error.field, error.reason) == ("company_facts[legal_ai].markets", reason)
    given = fields(metric=metric, metric_confirmed_at=confirmed, markets=(market_id,))
    assert operator_problems(given) == {"markets": reason}


# content_problems -------------------------------------------------------------------------------


def test_content_problems_of_a_valid_content_is_empty() -> None:
    assert content_problems(content()) == []
    assert content_problems(content(type=FactType.REGULATION, markets=(US_ID,))) == []


def test_content_problems_lists_every_problem_in_field_order() -> None:
    bad = content(
        type=FactType.REGULATION,
        wording=" padded",
        source_url="javascript:alert(1)",
        segments=(),
        metric="A result",
        last_checked_on="2026-11-14",  # type: ignore[arg-type]
    )

    assert content_problems(bad) == [
        ("wording", "must not start or end with whitespace"),
        ("source_url", LINK_REASON),
        ("segments", "must hold at least 1 item"),
        ("markets", REGULATION_MARKETS_REASON),
        ("metric", METRIC_UNCONFIRMED_REASON),
        ("last_checked_on", DATE_REASON),
    ]


def test_content_problems_refuses_a_datetime_and_a_plain_type() -> None:
    at = datetime(2026, 11, 14, 9, 0)
    assert content_problems(content(last_checked_on=at)) == [("last_checked_on", DATE_REASON)]
    assert content_problems(content(type="case_study")) == []  # type: ignore[arg-type]
    assert content_problems(content(type="other")) == [  # type: ignore[arg-type]
        ("type", "must be one of company, case_study, offer, regulation, statistic")
    ]
    assert content_problems(content(segments=("Bad Key",))) == [
        ("segments[0]", "must be a Segment key such as law_private_ai")
    ]
    with pytest.raises(TypeError):
        content_problems({"type": "company"})  # type: ignore[arg-type]


# The file form -----------------------------------------------------------------------------------


def test_fact_form_gives_the_file_item_back() -> None:
    found = seed(fact(metric="A result", metric_confirmed_at="2026-11-01"))[0]

    form = fact_form(found.fact_id, found.content)

    assert form == {
        "fact_id": "legal_ai",
        "type": "case_study",
        "wording": "Built an AI legal assistant platform for a legal-tech venture (Legal AI).",
        "source_url": "https://nextronai.com/",
        "segments": ["law_private_ai", "ops_automation"],
        "markets": ["uk", "us"],
        "metric": "A result",
        "metric_confirmed_at": "2026-11-01",
        "scope_note": "The client was a legal-tech venture, not a law firm.",
        "last_checked_on": "2026-11-14",
    }
    assert "metric" not in fact_form("x", content())
    assert fact_from_form(form) == found


@pytest.mark.parametrize(
    "item",
    [
        fact(),
        regulation(),
        fact(metric="A result", metric_confirmed_at="2026-11-01", scope_note=None),
        fact(segments=[OPS_ID, LAW_ID], markets=[US_ID]),
    ],
)
def test_the_round_trip_holds(item: dict[str, object]) -> None:
    [found] = seed(item)

    assert fact_from_form(fact_form(found.fact_id, found.content)) == found
    assert fact_from_form(copy.deepcopy(item)) == found


def test_fact_from_form_refuses_a_bad_item_naming_it() -> None:
    with pytest.raises(InvalidCompanyFacts) as caught:
        fact_from_form(fact_form("legal_ai", content(wording="")))

    assert caught.value.field == "company_facts[legal_ai].wording"


# is_link ------------------------------------------------------------------------------------------


def test_is_link_matches_what_safe_link_renders() -> None:
    assert is_link("https://nextronai.com/")
    assert is_link("HTTP://nextronai.com:8080/a?b=c#d")
    for bad in ("", "https://", "mailto:a@b.c", "https://a b.com", "https://a.com\\x", None):
        assert not is_link(bad)


# The Operator's path ----------------------------------------------------------------------------


def test_fact_from_fields_returns_the_content() -> None:
    found = fact_from_fields(
        fields(), segment_refs=SEGMENT_IDS, market_countries=MARKET_IDS, latest=LATEST
    )

    assert found == content()


def test_fact_from_fields_normalises_before_the_rules() -> None:
    given = fields(
        type=" offer ",
        wording="  A fixed-fee\r\n audit,\t credited   in full. ",
        source_url=" https://nextronai.com/ ",
        segments=(f" {LAW_ID} ",),
        markets=(US_ID,),
        metric="  ",
        metric_confirmed_at=" ",
        scope_note=" Name it only\nin replies. ",
        last_checked_on=" 2026-11-14 ",
    )

    found = fact_from_fields(
        given, segment_refs=SEGMENT_IDS, market_countries=MARKET_IDS, latest=LATEST
    )

    assert found == content(
        type=FactType.OFFER,
        wording="A fixed-fee audit, credited in full.",
        segments=(LAW_ID,),
        markets=(US_ID,),
        scope_note="Name it only in replies.",
    )


def test_fact_from_fields_collects_every_problem_one_per_field() -> None:
    given = fields(
        type="",
        wording="",
        source_url="javascript:alert(1)",
        segments=(),
        markets=(),
        metric="A result",
        last_checked_on="",
    )

    assert operator_problems(given) == {
        "type": CHOOSE_A_TYPE,
        "wording": ENTER_A_VALUE,
        "source_url": LINK_REASON,
        "segments": CHOOSE_A_SEGMENT,
        "markets": CHOOSE_A_MARKET,
        "metric": METRIC_UNCONFIRMED_REASON,
        "last_checked_on": ENTER_A_VALUE,
    }
    assert (ENTER_A_VALUE, CHOOSE_A_TYPE) == ("Enter a value", "Choose a type")


def test_fact_from_fields_names_the_acs_reasons() -> None:
    assert operator_problems(fields(type="regulation", wording="A regulation.")) == {
        "markets": REGULATION_MARKETS_REASON
    }
    assert operator_problems(fields(metric_confirmed_at="2026-11-01")) == {
        "metric_confirmed_at": CONFIRMATION_WITHOUT_METRIC_REASON
    }
    assert operator_problems(
        fields(type="regulation", wording=ABA_WORDING, markets=(UK_ID,))
    ) == {"markets": ABA_REASON}
    assert operator_problems(fields(wording=SRA_WORDING)) == {"markets": SRA_REASON}
    assert operator_problems(fields(last_checked_on="2026-11-18")) == {
        "last_checked_on": FUTURE_REASON
    }
    assert operator_problems(fields(source_url="ftp://nextronai.com/")) == {
        "source_url": LINK_REASON
    }


def test_fact_from_fields_names_unknown_and_repeated_references() -> None:
    other = UNKNOWN_ID
    assert operator_problems(fields(segments=(LAW_ID, other), markets=(other,))) == {
        "segments": NO_STORED_SEGMENT_REASON,
        "markets": NO_STORED_MARKET_REASON,
    }
    assert operator_problems(fields(segments=(LAW_ID, LAW_ID))) == {
        "segments": "repeats an earlier item"
    }


def test_fact_from_fields_keeps_the_first_problem_of_a_field() -> None:
    # rule 1 and the floor both fail on markets; the first found wins
    given = fields(type="regulation", wording=ABA_WORDING, markets=(UK_ID, US_ID))

    assert operator_problems(given) == {"markets": REGULATION_MARKETS_REASON}


def test_fact_from_fields_takes_only_fact_fields() -> None:
    with pytest.raises(TypeError):
        fact_from_fields(
            {"type": "company"},  # type: ignore[arg-type]
            segment_refs=SEGMENT_IDS,
            market_countries=MARKET_IDS,
            latest=LATEST,
        )
