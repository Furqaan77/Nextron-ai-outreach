"""``nextron.domain.extraction`` (FR-10, FR-11, FR-57, AD-11, AD-18; Story 2.9): the page budget
(``allot``), the date and number checks, every ``validate`` row of the spec's matrix (unknown
type, block and quote, the 300-character limit, the size clue's number, the profile fact's
value, the Event date's rules, repeats), every ``reconcile`` step (identical, still present,
gone and replaced, leftovers, out of scope) and the headquarters rules. Standard library, pytest
and ``nextron.domain`` only, with the shipped Segments."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from nextron.domain import seed_yaml
from nextron.domain.budget import BudgetClass
from nextron.domain.extraction import (
    BUDGET_CLASS,
    EXTRACT_LEAD_JOB,
    MAX_QUOTE,
    MAX_SIZE_NUMBER,
    MAX_VALUE,
    PAGE_TEXT_BUDGET,
    PROFILE_FACTS,
    ROUTE,
    SIZE_FACTS,
    Claim,
    Discard,
    DiscardCause,
    Discarded,
    EvidenceSource,
    ExtractionOutcome,
    FactKind,
    Headquarters,
    LiveEvidence,
    LocationQuote,
    Observation,
    SentPage,
    Superseding,
    allot,
    headquarters,
    number_in,
    reconcile,
    stated_date,
    validate,
)
from nextron.domain.segments import SegmentDef, SizeClue, validate_segments
from tests.contracts.support import ENGINE_ROOT

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
FETCHED = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
TEAM = "01KA00000000000000000000T1"
NEWS = "01KA00000000000000000000N1"
TEAM_URL = "https://smith-law.co.uk/team"
NEWS_URL = "https://smith-law.co.uk/news"
TEAM_TEXT = (
    "Our team\nSmith & Jones LLP has 12 lawyers in Leeds.\nWe employ 1,200 staff across the "
    "group.\nWe are hiring a Legal Operations Manager.\nOur head office is in Leeds, UK."
)
NEWS_TEXT = (
    "News\nOn 14 October 2026 we opened a new office in Manchester.\n"
    "In March 2026 we merged with Brown LLP.\nWe expect to open in Bristol on 1 March 2027."
)


def law() -> SegmentDef:
    text = (ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8")
    segments = validate_segments(seed_yaml.parse(text))
    return next(segment for segment in segments if segment.key == "law_private_ai")


PAGES = {
    TEAM: SentPage(TEAM_URL, FETCHED, TEAM_TEXT),
    NEWS: SentPage(NEWS_URL, FETCHED, NEWS_TEXT),
}


def answer(**lists: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "signals": lists.get("signals", []),
        "profile_facts": lists.get("profile_facts", []),
        "size_clues": lists.get("size_clues", []),
        "job_postings": lists.get("job_postings", []),
    }


def signal(
    quote: str,
    signal_type: str = "law_ai_confidentiality_post",
    block_id: str = TEAM,
    **more: Any,
) -> dict[str, Any]:
    return {
        "signal_type": signal_type,
        "block_id": block_id,
        "quote": quote,
        "event_date": more.get("event_date"),
    }


def size(quote: str, number: int, kind: str = "lawyers_firm_wide", block_id: str = TEAM) -> Any:
    return {"kind": kind, "number": number, "block_id": block_id, "quote": quote}


def fact(quote: str, value: str, kind: str = "location", block_id: str = TEAM) -> Any:
    return {"kind": kind, "value": value, "block_id": block_id, "quote": quote}


def run(parsed: dict[str, Any]) -> Any:
    return validate(parsed, law(), PAGES, NOW)


# Constants and enums -----------------------------------------------------------------------------


def test_the_constants() -> None:
    assert (EXTRACT_LEAD_JOB, ROUTE, BUDGET_CLASS) == (
        "extract_lead",
        "extract_signals",
        BudgetClass.BLOCKABLE,
    )
    assert (MAX_QUOTE, MAX_VALUE, MAX_SIZE_NUMBER, PAGE_TEXT_BUDGET) == (
        300,
        200,
        1_000_000,
        180_000,
    )
    assert [str(source) for source in EvidenceSource] == [
        "website",
        "ats",
        "registry",
        "signal_inbox",
        "csv",
        "referral",
        "operator",
    ]
    assert [str(outcome) for outcome in ExtractionOutcome] == [
        "ok",
        "refused",
        "truncated",
        "invalid",
        "failed",
        "no_activation",
        "no_pages",
        "lead_merged",
    ]
    assert {str(kind) for kind in PROFILE_FACTS} == {"what_we_do", "location", "technology"}
    assert {str(kind) for kind in SIZE_FACTS} == {str(clue) for clue in SizeClue}
    assert PROFILE_FACTS | SIZE_FACTS == set(FactKind)


def test_a_claim_is_a_signal_type_or_a_fact() -> None:
    with pytest.raises(ValueError):
        Claim()
    with pytest.raises(ValueError):
        Claim(signal_type="x", fact=FactKind.STAFF)
    with pytest.raises(TypeError):
        Claim(fact="staff")  # type: ignore[arg-type]
    assert Claim(signal_type="x").sort_key < Claim(fact=FactKind.STAFF).sort_key


def test_page_text_and_quotes_never_show_in_a_repr() -> None:
    page = SentPage(TEAM_URL, FETCHED, "SECRET PAGE TEXT")
    observation = Observation(Claim(fact=FactKind.LOCATION), TEAM, TEAM_URL, "QUOTED", value="V1")

    assert "SECRET" not in repr(page)
    assert "QUOTED" not in repr(observation) and "V1" not in repr(observation)
    assert "QUOTED" not in repr(LocationQuote("x", "QUOTED"))


# allot -------------------------------------------------------------------------------------------


def test_short_texts_are_kept_whole_and_their_leftover_goes_to_the_rest() -> None:
    texts = ["a" * 10, "b" * 100, "c" * 50]

    cut = allot(texts, 60)

    assert [len(text) for text in cut] == [10, 25, 25]
    assert cut[1] == "b" * 25  # cut to its share: its first characters
    assert sum(len(text) for text in cut) <= 60


def test_texts_within_the_budget_are_kept_whole() -> None:
    texts = ["one", "two two", "three three three"]

    assert allot(texts, PAGE_TEXT_BUDGET) == tuple(texts)


def test_equal_shares_and_the_leftover_of_integer_division() -> None:
    cut = allot(["x" * 100] * 3, 100)

    assert [len(text) for text in cut] == [33, 33, 34]


def test_a_text_is_never_dropped() -> None:
    assert allot(["abc", "defg"], 0) == ("", "")
    assert allot([], 100) == ()
    assert len(allot(["a" * 50_000] * 15, PAGE_TEXT_BUDGET)) == 15


@pytest.mark.parametrize(
    ("texts", "budget", "error"),
    [
        ("abc", 10, TypeError),
        ([b"abc"], 10, TypeError),
        (["abc"], 10.0, TypeError),
        (["abc"], True, TypeError),
        (["abc"], -1, ValueError),
    ],
)
def test_allot_refuses_bad_arguments(texts: object, budget: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        allot(texts, budget)  # type: ignore[arg-type]


# stated_date and number_in -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "value", "stated"),
    [
        ("We opened on 2026-10-14.", date(2026, 10, 14), True),
        ("We opened on 14 October 2026.", date(2026, 10, 14), True),
        ("We opened on 14th October 2026.", date(2026, 10, 14), True),
        ("We opened on 14 of October, 2026.", date(2026, 10, 14), True),
        ("We opened on October 14, 2026.", date(2026, 10, 14), True),
        ("We opened on October 14th 2026.", date(2026, 10, 14), True),
        ("WE OPENED ON OCTOBER 14, 2026", date(2026, 10, 14), True),
        ("On 14/10/2026", date(2026, 10, 14), True),  # D/M/YYYY
        ("On 10/14/2026", date(2026, 10, 14), True),  # M/D/YYYY
        ("On 14.10.2026", date(2026, 10, 14), True),
        ("On 14-10-2026", date(2026, 10, 14), True),
        ("On 03/04/2026", date(2026, 4, 3), True),  # both readings count
        ("On 03/04/2026", date(2026, 3, 4), True),
        ("On 03/04/2026", date(2026, 3, 5), False),
        ("On 14/10-2026", date(2026, 10, 14), False),  # two different separators
        ("On 14 October 2026", date(2026, 10, 15), False),
        ("In October 2026", date(2026, 10, 1), True),  # month and year: the 1st only
        ("In October 2026", date(2026, 10, 14), False),
        ("In October, 2026", date(2026, 10, 1), True),
        ("Since 2026", date(2026, 1, 1), True),  # a year alone: 1 January only
        ("Since 2026", date(2026, 6, 1), False),
        ("On 14 October 2026", date(2026, 1, 1), False),  # a year inside a full date
        ("On 14 October 2026", date(2026, 10, 1), False),
        ("In October 2026", date(2026, 1, 1), False),
        ("On 31/02/2026", date(2026, 2, 28), False),  # not a real date
        ("Room 12026 and 2026x", date(2026, 1, 1), False),  # not a year on its own
        ("We opened recently", date(2026, 10, 14), False),
        ("On 14  October\n2026", date(2026, 10, 14), True),  # whitespace is normalised
        ("In 1850 the firm began", date(1850, 1, 1), False),  # years 1900 to 2099 only
    ],
)
def test_stated_date(quote: str, value: date, stated: bool) -> None:
    assert stated_date(quote, value) is stated


@pytest.mark.parametrize(
    ("quote", "value"),
    [
        ("We opened on Oct 14, 2026.", date(2026, 10, 14)),
        ("We opened on Oct. 14, 2026.", date(2026, 10, 14)),
        ("We opened on oct 14th 2026.", date(2026, 10, 14)),
        ("We opened on 14 Sept 2026.", date(2026, 9, 14)),
        ("We opened on 14 Sept. 2026.", date(2026, 9, 14)),
        ("We opened on 14 Sep 2026.", date(2026, 9, 14)),
        ("We opened on 14th of Feb, 2026.", date(2026, 2, 14)),
        ("We opened in Sep. 2026.", date(2026, 9, 1)),
        ("We opened in Sept 2026.", date(2026, 9, 1)),
        ("We opened in Jan 2026.", date(2026, 1, 1)),
        ("We opened in DEC. 2025.", date(2025, 12, 1)),
        ("We opened on Mar 3, 2026.", date(2026, 3, 3)),
        ("We opened on Jun 30, 2026.", date(2026, 6, 30)),
        ("We opened in May 2026.", date(2026, 5, 1)),
    ],
)
def test_an_abbreviated_month_is_read_in_every_position(quote: str, value: date) -> None:
    assert stated_date(quote, value) is True


@pytest.mark.parametrize(
    ("quote", "value"),
    [
        ("Mayor 2026", date(2026, 5, 1)),  # not a month: no year alone on 1 May
        ("Marching 2026", date(2026, 3, 1)),
        ("Junes 14, 2026", date(2026, 6, 14)),
        ("Septem 2026", date(2026, 9, 1)),
        ("14 Decembers 2026", date(2026, 12, 14)),
        ("Octo 14, 2026", date(2026, 10, 14)),
        ("14 Sept.. 2026", date(2026, 9, 14)),  # one full stop at most
    ],
)
def test_a_word_that_only_starts_like_a_month_is_no_month(quote: str, value: date) -> None:
    assert stated_date(quote, value) is False


def test_stated_date_refuses_the_wrong_types() -> None:
    with pytest.raises(TypeError):
        stated_date(None, date(2026, 1, 1))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        stated_date("2026", datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(TypeError):
        stated_date("2026", "2026-01-01")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("number", "quote", "found"),
    [
        (12, "Smith & Jones LLP has 12 lawyers", True),
        (12, "twelve lawyers", False),
        (1200, "We employ 1,200 staff", True),
        (1200, "We employ 1200 staff", True),
        (200, "We employ 1,200 staff", False),  # 200 is part of 1,200
        (1, "We employ 1,200 staff", False),
        (12, "Our 120 lawyers", False),
        (5, "5 offices", True),
        (34, "12,34 offices", True),  # not a thousands group: two runs
        (1_000_000, "1,000,000 clients", True),
        (5, "1.5 million staff", False),  # a decimal is no whole number
        (1, "1.5 million staff", False),
        (12, "12.25 offices", False),
        (25, "12.25 offices", False),
        (1, "1,200.5 staff", False),
        (1200, "1,200.5 staff", False),
        (12, "We have 12 lawyers.", True),  # a full stop after the digits is no decimal
        (2026, "Since 2026. We have 12 lawyers.", True),
        (12, "We have 12. Our staff", True),
    ],
)
def test_number_in(number: int, quote: str, found: bool) -> None:
    assert number_in(number, quote) is found


def test_number_in_refuses_the_wrong_types() -> None:
    with pytest.raises(TypeError):
        number_in(True, "1")
    with pytest.raises(TypeError):
        number_in("1", "1")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        number_in(1, None)  # type: ignore[arg-type]


# validate ----------------------------------------------------------------------------------------


def test_valid_proposals_are_kept_in_order_with_normalised_quotes() -> None:
    parsed = answer(
        signals=[
            signal("We are hiring a  Legal Operations\nManager.", " LAW_AI_Confidentiality_Post ")
        ],
        profile_facts=[fact("Our head office is in Leeds, UK.", "  Leeds  ")],
        size_clues=[size("Smith & Jones LLP has 12 lawyers", 12)],
    )

    found = run(parsed)

    assert (found.discards, found.dropped_dates) == ((), ())
    assert found.observations == (
        Observation(
            Claim(signal_type="law_ai_confidentiality_post"),
            TEAM,
            TEAM_URL,
            "We are hiring a Legal Operations Manager.",
        ),
        Observation(
            Claim(fact=FactKind.LOCATION),
            TEAM,
            TEAM_URL,
            "Our head office is in Leeds, UK.",
            value="Leeds",
        ),
        Observation(
            Claim(fact=FactKind.LAWYERS_FIRM_WIDE),
            TEAM,
            TEAM_URL,
            "Smith & Jones LLP has 12 lawyers",
            number=12,
        ),
    )


HIRED = "We are hiring"
TWELVE = "Smith & Jones LLP has 12 lawyers"
LEEDS = "Our head office is in Leeds, UK."
OTHER_ULID = "01KA0000000000000000000ZZZ"
SIG, SIZE, FACT = Discarded.SIGNAL, Discarded.SIZE_CLUE, Discarded.PROFILE_FACT


@pytest.mark.parametrize(
    ("proposal", "status", "cause", "cited"),
    [
        (signal(HIRED, "made_up"), SIG, "unknown_signal_type", TEAM),
        # a rule-only type (absence Evidence only) can't be quoted
        (signal(HIRED, "law_contact_form_only"), SIG, "unknown_signal_type", TEAM),
        # Story 2.11: hiring Events come only from job postings
        (signal(HIRED, "law_hiring_legal_ops"), SIG, "hiring_type", TEAM),
        (signal(HIRED, " LAW_HIRING_LEGAL_OPS "), SIG, "hiring_type", TEAM),
        (signal(HIRED, block_id="signal_types"), SIG, "unknown_block", None),
        (signal(HIRED, block_id=OTHER_ULID), SIG, "unknown_block", None),
        (signal("We are hiring a robot"), SIG, "quote_not_in_page", TEAM),
        (signal("we are hiring"), SIG, "quote_not_in_page", TEAM),  # case counts
        (signal(" \n "), SIG, "quote_empty", TEAM),
        (signal("x" * 301), SIG, "quote_too_long", TEAM),
        (size("twelve lawyers", 12), SIZE, "quote_not_in_page", TEAM),
        (size(TWELVE, 0), SIZE, "number_out_of_range", TEAM),
        (size("We employ 1,200 staff", 1_000_001, "staff"), SIZE, "number_out_of_range", TEAM),
        (size(TWELVE, 13), SIZE, "number_not_in_quote", TEAM),
        (fact(LEEDS, "  "), FACT, "value_invalid", TEAM),
        (fact(LEEDS, "x" * 201), FACT, "value_invalid", TEAM),
        (fact("Our head office is in York", "York"), FACT, "quote_not_in_page", TEAM),
    ],
)
def test_each_refusal_is_one_discard_with_its_cause(
    proposal: dict[str, Any], status: Discarded, cause: str, cited: str | None
) -> None:
    key = {
        Discarded.SIGNAL: "signals",
        Discarded.SIZE_CLUE: "size_clues",
        Discarded.PROFILE_FACT: "profile_facts",
    }[status]

    found = run(answer(**{key: [proposal]}))

    assert found.observations == ()
    assert found.discards == (Discard(status, DiscardCause(cause), cited),)


@pytest.mark.parametrize("value", ["Leeds\x00", "Le\x00eds", "Leeds\nUK", "Leeds\tUK", "\x7fLeeds"])
def test_a_value_holding_a_control_character_is_invalid(value: str) -> None:
    found = run(answer(profile_facts=[fact(LEEDS, value)]))

    assert found.observations == ()
    assert found.discards == (Discard(FACT, DiscardCause.VALUE_INVALID, TEAM),)


def test_a_quote_of_300_characters_and_a_value_of_200_are_kept() -> None:
    text = "y" * 300
    pages = {TEAM: SentPage(TEAM_URL, FETCHED, f"start {text} end")}
    parsed = answer(
        signals=[signal(text)], profile_facts=[fact(text, "v" * 200, kind="what_we_do")]
    )

    found = validate(parsed, law(), pages, NOW)

    assert len(found.observations) == 2 and found.discards == ()


def test_size_clue_twelve_in_words_is_refused_even_on_a_page_that_has_it() -> None:
    pages = {TEAM: SentPage(TEAM_URL, FETCHED, "We have twelve lawyers.")}

    found = validate(answer(size_clues=[size("We have twelve lawyers.", 12)]), law(), pages, NOW)

    assert found.discards == (Discard(SIZE, DiscardCause.NUMBER_NOT_IN_QUOTE, TEAM),)


OPENED = "On 14 October 2026 we opened a new office in Manchester."
MERGED = "In March 2026 we merged with Brown LLP."
EXPECTED = "We expect to open in Bristol on 1 March 2027."


@pytest.mark.parametrize(
    ("quote", "proposed", "kept", "cause"),
    [
        (OPENED, "2026-10-14", date(2026, 10, 14), None),
        (MERGED, "2026-03-01", date(2026, 3, 1), None),
        (MERGED, "2026-03-15", None, "date_not_in_quote"),
        (EXPECTED, "2027-03-01", None, "date_in_future"),
        (OPENED, None, None, None),
    ],
)
def test_an_events_date_is_kept_only_when_the_quote_states_it_and_it_isnt_in_the_future(
    quote: str, proposed: str | None, kept: date | None, cause: str | None
) -> None:
    parsed = answer(
        signals=[signal(quote, "law_expansion", NEWS, event_date=proposed)],
    )

    found = run(parsed)

    [observation] = found.observations  # the Signal is kept either way
    assert observation.event_date == kept
    assert found.discards == ()
    expected = () if cause is None else (Discard(Discarded.EVENT_DATE, DiscardCause(cause), NEWS),)
    assert found.dropped_dates == expected


def test_todays_date_is_not_in_the_future() -> None:
    pages = {NEWS: SentPage(NEWS_URL, FETCHED, "We opened on 18 November 2026.")}
    today = "We opened on 18 November 2026."
    parsed = answer(signals=[signal(today, "law_expansion", NEWS, event_date="2026-11-18")])

    [observation] = validate(parsed, law(), pages, NOW).observations

    assert observation.event_date == date(2026, 11, 18)


def test_an_attribute_never_carries_a_date_and_dropping_it_is_not_recorded() -> None:
    pages = {TEAM: SentPage(TEAM_URL, FETCHED, "Since 2026 we have handled 500 matters.")}
    parsed = answer(
        signals=[
            signal(
                "Since 2026 we have handled 500 matters.",
                "law_size_document_heavy",
                event_date="2026-01-01",
            )
        ]
    )

    found = validate(parsed, law(), pages, NOW)

    [observation] = found.observations
    assert (observation.event_date, found.dropped_dates) == (None, ())


def test_a_repeat_is_dropped_without_a_record_and_job_postings_are_ignored() -> None:
    quote = "We are hiring a Legal Operations Manager."
    parsed = answer(
        signals=[signal(quote), signal(f" {quote} "), signal(quote, "law_expansion")],
        size_clues=[size("Smith & Jones LLP has 12 lawyers", 12)] * 2,
        job_postings=[
            {
                "title": "Legal Operations Manager",
                "url": None,
                "posted_date": None,
                "block_id": "nowhere",
                "quote": "not on the page",
            }
        ],
    )

    found = run(parsed)

    assert [o.claim for o in found.observations] == [
        Claim(signal_type="law_ai_confidentiality_post"),
        Claim(signal_type="law_expansion"),  # another claim: kept
        Claim(fact=FactKind.LAWYERS_FIRM_WIDE),
    ]
    assert found.discards == ()


@pytest.mark.parametrize(
    "parsed",
    [
        [],
        {"signals": None, "profile_facts": [], "size_clues": []},
        {"signals": [1], "profile_facts": [], "size_clues": []},
        answer(size_clues=[size("12", "12")]),  # type: ignore[arg-type]
        answer(profile_facts=[fact("x", "y", kind="staff")]),
        answer(signals=[signal("x", event_date="14/10/2026")]),
        answer(signals=[{"signal_type": 1, "block_id": TEAM, "quote": "x", "event_date": None}]),
    ],
)
def test_an_answer_without_the_schemas_shape_raises(parsed: Any) -> None:
    with pytest.raises(ValueError):
        validate(parsed, law(), PAGES, NOW)


def test_validate_refuses_the_wrong_argument_types() -> None:
    with pytest.raises(TypeError):
        validate(answer(), "law", PAGES, NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        validate(answer(), law(), {TEAM: TEAM_TEXT}, NOW)  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        validate(answer(), law(), PAGES, datetime(2026, 1, 1))


# reconcile ---------------------------------------------------------------------------------------

HIRING = Claim(signal_type="law_hiring_legal_ops")
STAFF = Claim(fact=FactKind.STAFF)
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def live(
    evidence_id: str, quote: str, claim: Claim = HIRING, url: str = TEAM_URL, days: int = 0
) -> LiveEvidence:
    return LiveEvidence(evidence_id, claim, url, quote, T0 + timedelta(days=days))


def observed(quote: str, claim: Claim = HIRING, url: str = TEAM_URL) -> Observation:
    return Observation(claim, TEAM if url == TEAM_URL else NEWS, url, quote)


def test_an_identical_quote_confirms_its_row() -> None:
    plan = reconcile(
        [live("E1", "We are hiring.")], [observed("We are hiring.")], {TEAM_URL: "changed"}
    )

    assert (plan.confirm, plan.insert, plan.supersede, plan.retract) == (("E1",), (), (), ())


def test_a_quote_still_in_the_page_is_confirmed_even_when_not_proposed_again() -> None:
    plan = reconcile([live("E1", "We are hiring.")], [], {TEAM_URL: "News. We are hiring. More."})

    assert (plan.confirm, plan.retract) == (("E1",), ())


def test_gone_rows_are_superseded_oldest_first_and_the_leftovers_retracted_or_inserted() -> None:
    rows = [
        live("E2", "Second old quote.", days=2),
        live("E1", "First old quote.", days=1),
        live("E3", "Third old quote.", days=3),
    ]
    first, second = observed("A new quote."), observed("Another new quote.")

    plan = reconcile(rows, [first, second], {TEAM_URL: "Nothing old here."})

    assert plan.supersede == (Superseding("E1", first), Superseding("E2", second))
    assert (plan.retract, plan.insert, plan.confirm) == (("E3",), (), ())


def test_more_proposals_than_gone_rows_are_inserted() -> None:
    first, second = observed("A new quote."), observed("Another new quote.")

    plan = reconcile([live("E1", "Old.")], [first, second], {TEAM_URL: "nothing"})

    assert (plan.supersede, plan.insert) == ((Superseding("E1", first),), (second,))


def test_reconciliation_is_per_claim_and_per_page_and_ignores_pages_not_sent() -> None:
    rows = [
        live("E1", "Old staff count.", claim=STAFF),
        live("E2", "We are hiring.", url=NEWS_URL),
        live("E3", "Elsewhere.", url="https://smith-law.co.uk/about"),  # not sent: untouched
    ]
    new_hire = observed("We are hiring.")  # the same quote, but on the team page

    plan = reconcile(rows, [new_hire], {TEAM_URL: "We are hiring.", NEWS_URL: "nothing"})

    assert plan.insert == (new_hire,)
    assert set(plan.retract) == {"E1", "E2"}
    assert (plan.confirm, plan.supersede) == ((), ())


def test_identical_pairs_one_row_each_and_a_spare_identical_row_is_confirmed_by_its_text() -> None:
    rows = [live("E1", "We are hiring."), live("E2", "We are hiring.", days=1)]

    plan = reconcile(rows, [observed("We are hiring.")], {TEAM_URL: "We are hiring."})

    assert plan.confirm == ("E1", "E2")


def test_a_proposal_of_a_page_not_sent_raises() -> None:
    with pytest.raises(ValueError):
        reconcile([], [observed("x", url=NEWS_URL)], {TEAM_URL: "x"})


# headquarters ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quotes", "expected"),
    [
        ([("E1", "Our office is in Chicago, IL 60601.")], Headquarters("America/Chicago", "E1")),
        ([("E1", "Offices in Leeds, UK")], Headquarters("Europe/London", "E1")),
        ([("E1", "Based in Washington, D.C.")], Headquarters("America/New_York", "E1")),
        # two zones, no headquarters named: unknown
        ([("E1", "Chicago, IL"), ("E2", "Leeds, UK")], None),
        # a headquarters quote wins over the other locations
        (
            [("E1", "Chicago, IL"), ("E2", "Our headquarters: Leeds, UK"), ("E3", "Miami, FL")],
            Headquarters("Europe/London", "E2"),
        ),
        (
            [("E1", "Denver, CO"), ("E2", "HQ in Boston, MA"), ("E3", "Main office: Albany, NY")],
            Headquarters("America/New_York", "E2"),
        ),
        # two headquarters quotes in different zones: unknown
        ([("E1", "Head office: Leeds, UK"), ("E2", "Registered office: Dallas, TX")], None),
        # the same zone twice: the first quote's Evidence
        ([("E1", "Boston, MA"), ("E2", "New York, NY")], Headquarters("America/New_York", "E1")),
        # a headquarters quote with no place: the other headquarters quotes decide
        (
            [("E1", "Our headquarters"), ("E2", "Principal office: Austin, TX")],
            Headquarters("America/Chicago", "E2"),
        ),
        # a headquarters quote with no place, and no other: unknown (the plain ones don't count)
        ([("E1", "Our headquarters are lovely"), ("E2", "Leeds, UK")], None),
        ([("E1", "Paris, France")], None),
        ([("E1", "Chicago, IL and London, UK")], None),
        ([], None),
    ],
)
def test_headquarters(quotes: list[tuple[str, str]], expected: Headquarters | None) -> None:
    locations = [LocationQuote(evidence_id, quote) for evidence_id, quote in quotes]

    assert headquarters(locations) == expected


def test_headquarters_refuses_anything_but_location_quotes() -> None:
    with pytest.raises(TypeError):
        headquarters([("E1", "Leeds, UK")])  # type: ignore[list-item]
