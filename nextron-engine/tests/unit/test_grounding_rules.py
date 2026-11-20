"""The deterministic grounding rules (FR-4, FR-21, AD-18; Story 4.13): each of the 14 rules with a
positive and a negative case, the flag order, the explanations' limit, ``claims=None`` running
only the text rules, and the input checks."""

from dataclasses import replace

import pytest

from nextron.domain import ai_test_sets
from nextron.domain.grounding_rules import (
    CLAIM_RULES,
    MAX_EXPLANATION,
    RULE_IDS,
    RULES_VERSION,
    TEXT_RULES,
    Claim,
    EarlierStep,
    Flag,
    GroundingStatus,
    LeadSignal,
    RevisionInput,
    RulesContext,
    RulesResult,
    check,
    count_words,
)
from nextron.domain.segments import SignalClass
from nextron.domain.sequences import SubjectRule, StepBrief, WordComparison, LinkRule

SIGNATURE = (
    "Sam\nNextron AI\nsam@mail.example.org | +44 20 7946 0000\nhttps://cal.example.com/intro"
)
PRIVACY = "https://www.example.com/privacy"
FACT = ("legal_ai", 1)
OTHER_FACT = ("nextron_team", 1)


def brief(step: int = 1) -> StepBrief:
    limits = {
        1: (80, WordComparison.UNDER),
        2: (70, WordComparison.AT_MOST),
        3: (70, WordComparison.AT_MOST),
        4: (50, WordComparison.AT_MOST),
    }
    limit, comparison = limits[step]
    return StepBrief(
        step=step,
        delay_days=(0, 3, 7, 14)[step - 1],
        purpose="purpose",
        word_limit=limit,
        word_comparison=comparison,
        allowed_content=("content",),
        ask="ask",
        link_rule=LinkRule.FOOTER_ONLY,
        no_price=True,
        no_audit=True,
        subject_rule=SubjectRule(7, WordComparison.UNDER, ("guidance",)) if step == 1 else None,
        segment_angles=(),
    )


SIGNALS = (
    LeadSignal("s1", "law_hiring_legal_ops", SignalClass.EVENT, 1, False, ("e1",)),
    LeadSignal("s2", "law_expansion", SignalClass.EVENT, 2, False, ("e2",)),
    LeadSignal("s3", "law_contact_form_only", SignalClass.ATTRIBUTE, 3, True, ("e3",)),
)
CLEAN_BODY = (
    "Hi Beth,\n\nSaw Hollis & Grant is hiring an intake coordinator. More intake usually means "
    "more client detail in more tools.\n\nWe built an AI legal assistant platform.\n\n"
    "Worth a look?"
)
LEAD_CLAIM = Claim("Hollis & Grant is hiring an intake coordinator", "e1", None)
FACT_CLAIM = Claim("We built an AI legal assistant platform", None, FACT)


def revision(
    subject: str = "your intake coordinator role",
    body: str = CLEAN_BODY,
    claims: tuple[Claim, ...] | None = (LEAD_CLAIM, FACT_CLAIM),
    cited: str | None = "s1",
) -> RevisionInput:
    return RevisionInput(
        subject=subject,
        body=body,
        claims=claims,
        cited_signal_id=cited,
        evidence_ids=("e1", "e2", "e3", "e4"),
        fact_versions=(FACT, OTHER_FACT),
    )


def context(step: int = 1, **changes: object) -> RulesContext:
    found = RulesContext(
        step=step,
        contact_details=(SIGNATURE, PRIVACY),
        evidence_kinds={"e1": "quote", "e2": "quote", "e3": "absence", "e4": "absence"},
        signals=SIGNALS,
        strongest_event_id="s1",
        allowed_facts=frozenset({FACT, OTHER_FACT}),
        earlier_steps=(),
    )
    return replace(found, **changes)  # type: ignore[arg-type]


def text_only(
    subject: str = "your intake coordinator role", body: str = CLEAN_BODY
) -> RevisionInput:
    """A revision with no claims array and no cited Signal (an Operator edit)."""
    return revision(subject=subject, body=body, claims=None, cited=None)


def spans(found: RulesResult) -> list[tuple[str, str]]:
    return [(flag.rule, flag.span) for flag in found.flags]


def rules(found: RulesResult) -> list[str]:
    return [flag.rule for flag in found.flags]


def run(given: RevisionInput, step: int = 1, **changes: object) -> RulesResult:
    return check(given, brief(step), context(step, **changes))


def words(count: int) -> str:
    return " ".join(["word"] * count)


# The vocabulary ---------------------------------------------------------------------------------


def test_the_rule_ids_are_the_test_sets_ids_and_the_clean_revision_has_no_flag() -> None:
    assert ai_test_sets.GROUNDING_RULE_IDS is RULE_IDS
    assert len(RULE_IDS) == 14 and TEXT_RULES + CLAIM_RULES == RULE_IDS
    clean = run(revision())
    assert clean == RulesResult((), RULES_VERSION)
    assert clean.status is GroundingStatus.PENDING


def test_count_words_counts_runs_holding_a_letter_or_digit() -> None:
    assert count_words("Hi Beth,\n\n& 17+ re-keying -- ok?") == 5
    assert count_words("") == 0


# The text rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "count", "flagged"),
    [
        (1, 79, False),
        (1, 80, True),
        (2, 70, False),
        (2, 71, True),
        (3, 71, True),
        (4, 50, False),
        (4, 51, True),
    ],
)
def test_word_limit_follows_the_brief(step: int, count: int, flagged: bool) -> None:
    found = run(revision(body=words(count), claims=None, cited=None), step=step)
    assert ("word_limit" in rules(found)) is flagged
    if flagged:
        [flag] = found.flags
        assert flag.span == "" and str(count) in flag.explanation


@pytest.mark.parametrize(
    ("subject", "span"),
    [
        ("one two three four five six seven", "one two three four five six seven"),
        ("RE: your intake hire", "RE:"),
        ("  fwd: order entry", "fwd:"),
        ("Fw : order entry", "Fw :"),
    ],
)
def test_subject_rule_flags_long_or_reply_subjects_on_step_1(subject: str, span: str) -> None:
    [flag] = run(revision(subject=subject, claims=None)).flags
    assert (flag.rule, flag.span) == ("subject_rule", span)


def test_subject_rule_allows_six_words_and_a_reply_prefix_on_a_follow_up() -> None:
    assert run(revision(subject="one two three four five six")).flags == ()
    assert run(text_only(subject="Re: your intake hire"), step=2).flags == ()


def test_link_in_step1_flags_even_an_approved_link_but_not_on_a_follow_up() -> None:
    body = CLEAN_BODY + " Book here: https://cal.example.com/intro."
    [flag] = run(revision(body=body, claims=None)).flags
    assert (flag.rule, flag.span) == ("link_in_step1", "https://cal.example.com/intro")
    assert run(revision(body=body, claims=None, cited=None), step=2).flags == ()


@pytest.mark.parametrize(
    "detail",
    [
        "https://demo.example.com/x",
        "www.other.example",
        "other.example.com/page",
        "bob@firm.example",
        "020 7946 0318",
        "+1 (415) 555-0134",
    ],
)
def test_unapproved_contact_detail_flags_any_detail_not_in_settings(detail: str) -> None:
    found = run(text_only(body=CLEAN_BODY + f" Reach me at {detail}."), step=2)
    assert spans(found) == [("unapproved_contact_detail", detail)]


@pytest.mark.parametrize(
    "detail",
    ["SAM@mail.example.org", "+44 20 7946 0000", "https://www.example.com/privacy/", PRIVACY],
)
def test_settings_details_are_approved(detail: str) -> None:
    assert run(text_only(body=CLEAN_BODY + f" Reach me at {detail}."), step=2).flags == ()


def test_a_detail_in_the_subject_is_checked_and_dates_and_counts_are_not_phones() -> None:
    found = run(revision(subject="call 020 7946 0318", claims=None, cited=None), step=2)
    assert [(flag.rule, flag.span) for flag in found.flags] == [
        ("unapproved_contact_detail", "020 7946 0318")
    ]
    body = CLEAN_BODY + " Since 2026-11-19, 80 to 100 orders a day, 17 August 2026."
    assert run(revision(body=body, claims=None, cited=None), step=2).flags == ()


@pytest.mark.parametrize(
    "text",
    [
        "19.11.2026",
        "01/02/2024",
        "15-01-2024",
        "2019-2024",
        "2019 - 2024",
        "1.250.000",
        "10 000 000",
        "1,250,000",
    ],
)
def test_dates_year_ranges_and_grouped_thousands_are_not_phones(text: str) -> None:
    body = CLEAN_BODY + f" It was {text} then."
    assert run(revision(body=body, claims=None, cited=None), step=2).flags == ()


def test_a_url_matches_a_settings_url_without_its_scheme_or_www() -> None:
    settings = ("Sam\nwww.x.example.com", "http://y.example.com/a/")
    for detail in ("https://www.x.example.com", "x.example.com/", "https://y.example.com/a"):
        given = text_only(body=CLEAN_BODY + f" See {detail} here.")
        assert run(given, step=2, contact_details=settings).flags == ()


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("I hope this finds you well.", "hope this finds you well"),
        ("Just following-up, as promised.", "Just following-up"),
        ("CIRCLING BACK on this.", "CIRCLING BACK"),
        ("We're the leading firm.", "We're the leading"),
        ("This is your last chance.", "last chance"),
    ],
)
def test_banned_phrase_ignores_case_and_punctuation(text: str, span: str) -> None:
    found = run(revision(body=f"Hi Beth,\n\n{text}", claims=None, cited=None), step=2)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("banned_phrase", span)]


def test_banned_phrase_matches_whole_words_only() -> None:
    body = "Hi Beth,\n\nWe are the leadingest. Unjust following updates."
    assert run(revision(body=body, claims=None, cited=None), step=2).flags == ()


@pytest.mark.parametrize(
    ("text", "span"), [("I've attached a note.", "attached"), ("See the PDF.", "See the PDF")]
)
def test_attachment_flags_a_mention(text: str, span: str) -> None:
    found = run(revision(body=f"Hi Beth,\n\n{text}", claims=None, cited=None), step=3)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("attachment", span)]


def test_attachment_leaves_other_words_alone() -> None:
    body = "Hi Beth,\n\nOrders arrive by PDF and the team is attached to Excel sheets."
    flagged = rules(run(revision(body=body, claims=None, cited=None), step=3))
    assert flagged == ["attachment"]  # "attached" is the mention; "PDF" alone is not
    assert run(text_only(body="Hi Beth,\n\nOrders arrive by PDF."), step=3).flags == ()


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("Our rate is £3,000 until Friday.", "Our rate is £3,000 until Friday."),
        ("It costs 900 USD a month!", "It costs 900 USD a month!"),
        ("We have two audit slots left.", "audit"),
    ],
)
def test_price_or_audit_flags_a_price_or_the_audit(text: str, span: str) -> None:
    found = run(revision(body=f"Hi Beth,\n\n{text}", claims=None, cited=None), step=4)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("price_or_audit_in_step", span)]


def test_price_or_audit_follows_the_brief_flags() -> None:
    given = revision(body="Hi Beth,\n\nTwo audit slots at $900.", claims=None, cited=None)
    assert check(given, replace(brief(4), no_price=False, no_audit=False), context(4)).flags == ()
    assert run(text_only(body="Hi Beth,\n\nAn auditor's view."), step=4).flags == ()


def test_multiple_ctas_flags_each_question_after_the_first_on_step_1() -> None:
    body = CLEAN_BODY + " And would a call make sense? Or a demo?"
    found = run(revision(body=body))
    assert [(flag.rule, flag.span) for flag in found.flags] == [
        ("multiple_ctas", "And would a call make sense?"),
        ("multiple_ctas", "Or a demo?"),
    ]
    assert run(revision(body=body, claims=None, cited=None), step=2).flags == ()


@pytest.mark.parametrize(
    ("extra", "span"),
    [(" Free on Friday?!", "Free on Friday?!"), (' Is that "fair?!"', 'Is that "fair?!')],
)
def test_a_question_ending_in_more_than_a_question_mark_counts(extra: str, span: str) -> None:
    found = run(revision(body=CLEAN_BODY + extra))
    assert spans(found) == [("multiple_ctas", span)]


def test_a_url_query_is_not_a_question() -> None:
    body = CLEAN_BODY + " See https://www.example.com/privacy?x=1 here."
    assert "multiple_ctas" not in rules(run(revision(body=body)))


# The claim rules --------------------------------------------------------------------------------


def test_claims_none_runs_only_the_text_rules() -> None:
    body = CLEAN_BODY + " Your website has no chat."
    given = revision(body=body, claims=None, cited="s3")
    assert run(given, strongest_event_id="s2", allowed_facts=frozenset()).flags == ()
    with_claims = replace(given, claims=(Claim("Your website has no chat", "e3", None), FACT_CLAIM))
    flagged = set(rules(run(with_claims, strongest_event_id="s2", allowed_facts=frozenset())))
    assert flagged <= set(CLAIM_RULES) and flagged


def test_citation_outside_set_flags_evidence_and_facts_outside_the_sets() -> None:
    outside = Claim("Hollis & Grant is hiring an intake coordinator", "e9", None)
    unset = Claim("We built an AI legal assistant platform", None, ("legal_ai", 2))
    disallowed = replace(FACT_CLAIM, fact=OTHER_FACT)
    found = run(revision(claims=(outside, unset)))
    assert [(flag.rule, flag.span) for flag in found.flags] == [
        ("citation_outside_set", "Hollis & Grant is hiring an intake coordinator"),
        ("citation_outside_set", "We built an AI legal assistant platform"),
    ]
    found = run(revision(claims=(LEAD_CLAIM, disallowed)), allowed_facts=frozenset({FACT}))
    assert rules(found) == ["citation_outside_set"]
    assert "Segment and Market" in found.flags[0].explanation


@pytest.mark.parametrize(
    ("claim", "flagged"),
    [
        ("Your website has no chat", True),
        ("it looks like your website has no chat", False),
        ("your website doesn't seem to have chat", False),
        ("I couldn't see a chat on your website", False),
        ("there might be no chat on your website", False),
    ],
)
def test_unhedged_absence(claim: str, flagged: bool) -> None:
    body = f"Hi Beth,\n\nA different angle: {claim}.\n\nWe built an AI legal assistant platform."
    given = revision(body=body, claims=(Claim(claim, "e4", None), FACT_CLAIM), cited=None)
    found = run(given, step=2)
    assert (rules(found) == ["unhedged_absence"]) is flagged
    if flagged:
        assert found.flags[0].span == claim


def test_a_hedge_in_the_claims_sentence_counts() -> None:
    claim = Claim("you don't offer online booking", "e4", None)
    for lead, flagged in (("It looks like", False), ("Clearly", True)):
        body = f"Hi Beth,\n\n{lead} {claim.text}.\n\nWe built an AI legal assistant platform."
        given = revision(body=body, claims=(claim, FACT_CLAIM), cited=None)
        assert (rules(run(given, step=2)) == ["unhedged_absence"]) is flagged
    body = (
        f"Hi Beth,\n\nIt looks like {claim.text}. Clearly {claim.text}.\n\n"
        "We built an AI legal assistant platform."
    )  # every place the claim appears must be hedged
    given = revision(body=body, claims=(claim, FACT_CLAIM), cited=None)
    assert rules(run(given, step=2)) == ["unhedged_absence"]


def test_unhedged_absence_ignores_quote_evidence() -> None:
    claim = Claim("Hollis & Grant is hiring an intake coordinator", "e1", None)
    assert run(revision(claims=(claim, FACT_CLAIM))).flags == ()


def test_signal_count_needs_exactly_one_signal_on_step_1() -> None:
    assert rules(run(revision(cited=None, claims=(FACT_CLAIM,)))) == ["signal_count"]
    second = Claim("More intake usually means more client detail", "e2", None)
    found = run(revision(claims=(LEAD_CLAIM, second, FACT_CLAIM)))
    assert rules(found) == ["signal_count"] and found.flags[0].span == ""
    assert run(revision(cited=None, claims=(FACT_CLAIM,)), step=2).flags == ()


def test_wrong_signal_needs_the_strongest_live_event_on_step_1() -> None:
    claim = Claim("Hollis & Grant is hiring an intake coordinator", "e2", None)
    assert rules(run(revision(cited="s2", claims=(claim, FACT_CLAIM)))) == ["wrong_signal"]
    found = run(revision(), strongest_event_id=None)
    assert rules(found) == ["wrong_signal"] and "no live Event" in found.flags[0].explanation
    assert run(revision(cited="s2", claims=(claim, FACT_CLAIM)), step=2).flags == ()


def test_wrong_signal_checks_the_one_signal_the_claims_cite_without_a_cited_signal() -> None:
    claim = Claim("Hollis & Grant is hiring an intake coordinator", "e2", None)
    found = run(revision(cited=None, claims=(claim, FACT_CLAIM)))
    assert spans(found) == [("wrong_signal", "")]
    assert run(revision(cited=None, claims=(LEAD_CLAIM, FACT_CLAIM))).flags == ()
    # two Signals: signal_count only, not wrong_signal too
    second = Claim("More intake usually means more client detail", "e2", None)
    found = run(revision(cited=None, claims=(LEAD_CLAIM, second, FACT_CLAIM)))
    assert rules(found) == ["signal_count"]


def test_score_only_cited_flags_a_claim_or_the_cited_signal() -> None:
    hedged = Claim("it looks like your site has only a contact form", "e3", None)
    body = f"Hi Beth,\n\nA different angle: {hedged.text}.\n\nWorth a look?"
    found = run(revision(body=body, claims=(hedged,), cited=None), step=2)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("score_only_cited", hedged.text)]
    found = run(revision(body=body, claims=(), cited="s3"), step=2)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("score_only_cited", "")]
    assert run(revision(body=body, claims=(), cited="s2"), step=2).flags == ()


def test_repeated_angle_flags_a_fact_or_signal_an_earlier_step_used() -> None:
    body = "Hi Beth,\n\nWe built an AI legal assistant platform.\n\nIs this on your radar?"
    given = revision(body=body, claims=(FACT_CLAIM,), cited=None)
    earlier = (EarlierStep(1, frozenset({"legal_ai"}), "s1"),)
    found = run(given, step=2, earlier_steps=earlier)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("repeated_angle", FACT_CLAIM.text)]
    found = run(replace(given, claims=(), cited_signal_id="s1"), step=2, earlier_steps=earlier)
    assert [(flag.rule, flag.span) for flag in found.flags] == [("repeated_angle", "")]
    fresh = (EarlierStep(1, frozenset({"nextron_team"}), "s2"),)
    assert run(given, step=2, earlier_steps=fresh).flags == ()
    later = (EarlierStep(3, frozenset({"legal_ai"}), None),)  # only earlier Steps count
    assert run(given, step=2, earlier_steps=later).flags == ()


def test_repeated_angle_flags_a_claim_on_an_earlier_steps_signal_once() -> None:
    again = Claim("More intake usually means more client detail", "e1", None)
    given = revision(claims=(LEAD_CLAIM, again), cited=None)
    earlier = (EarlierStep(1, frozenset(), "s1"),)
    found = run(given, step=2, earlier_steps=earlier)
    assert spans(found) == [("repeated_angle", LEAD_CLAIM.text)]
    # the cited Signal and a claim on its Evidence are one flag
    found = run(replace(given, cited_signal_id="s1"), step=2, earlier_steps=earlier)
    assert spans(found) == [("repeated_angle", LEAD_CLAIM.text)]
    fresh = (EarlierStep(1, frozenset(), "s2"),)
    assert run(given, step=2, earlier_steps=fresh).flags == ()


# Order, explanations and inputs -----------------------------------------------------------------


def test_flags_come_in_rule_order_then_position() -> None:
    body = (
        "Hi Beth,\n\nCircling back: see https://demo.example.com/x and the attached note. "
        + words(80)
        + " Is this on your radar? Last chance?"
    )
    given = revision(subject="Re: a b c d e f g", body=body, claims=None)
    found = run(given)
    assert rules(found) == [
        "word_limit",
        "subject_rule",
        "subject_rule",
        "link_in_step1",
        "unapproved_contact_detail",
        "banned_phrase",
        "banned_phrase",
        "attachment",
        "multiple_ctas",
    ]
    assert [flag.span for flag in found.flags][:3] == ["", "Re: a b c d e f g", "Re:"]
    assert found.flags[5].span == "Circling back" and found.flags[6].span == "Last chance"
    assert found.status is GroundingStatus.FAIL
    assert check(given, brief(1), context(1)) == found  # deterministic


def test_explanations_are_short_and_never_quote_the_draft() -> None:
    marker = "Zyxwvut"
    body = (
        f"Hi {marker},\n\nCircling back {marker} https://{marker.lower()}.example.com attached "
        f"£5 audit? {marker}? " + words(90)
    )
    claims = (
        Claim(f"{marker} has no chat", "e3", None),
        Claim(f"Hi {marker}", "e9", ("legal_ai", 9)),
        Claim("Circling back", "e1", None),
    )
    subject = f"Re: {marker} " + words(8)
    found = run(revision(subject=subject, body=body, claims=claims, cited="s3"))
    assert {flag.rule for flag in found.flags} >= set(RULE_IDS) - {"repeated_angle"}
    for flag in found.flags:
        assert 0 < len(flag.explanation) <= MAX_EXPLANATION
        assert marker.casefold() not in flag.explanation.casefold()


def test_a_flag_refuses_an_unknown_rule_or_a_long_explanation() -> None:
    with pytest.raises(ValueError, match="RULE_IDS"):
        Flag("no_such_rule", "", "why")
    with pytest.raises(ValueError, match="200"):
        Flag("word_limit", "", "x" * (MAX_EXPLANATION + 1))


def test_check_refuses_wrong_inputs() -> None:
    with pytest.raises(TypeError):
        check(object(), brief(1), context(1))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        check(revision(), object(), context(1))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        check(revision(), brief(1), object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Step"):
        check(revision(), brief(2), context(1))


# Story 4.19: a generic revision's waiver ---------------------------------------------------------

GENERIC = frozenset({"signal_count"})


def test_a_generic_step_1_waives_signal_count_and_records_it() -> None:
    found = run(revision(cited=None, claims=(FACT_CLAIM,)), waived_rules=GENERIC)

    assert found.flags == () and found.waived == ("signal_count",)
    assert found.rules_version == RULES_VERSION == 2


def test_the_waiver_is_recorded_for_a_step_1_edit_with_no_claims() -> None:
    found = run(text_only(), waived_rules=GENERIC)

    assert found.flags == () and found.waived == ("signal_count",)


def test_every_other_rule_still_runs_on_a_generic_revision() -> None:
    body = CLEAN_BODY + " Is that useful? See https://evil.example.net"
    outside = Claim("We built an AI legal assistant platform", None, ("other", 1))

    found = run(revision(body=body, cited=None, claims=(outside,)), waived_rules=GENERIC)

    assert "signal_count" not in rules(found)
    assert {"link_in_step1", "multiple_ctas", "citation_outside_set"} <= set(rules(found))


def test_a_waiver_on_a_follow_up_waives_nothing_that_applied() -> None:
    found = run(revision(cited=None, claims=(FACT_CLAIM,)), step=2, waived_rules=GENERIC)

    assert found.waived == ()


def test_without_the_waiver_the_same_revision_flags_signal_count() -> None:
    found = run(revision(cited=None, claims=(FACT_CLAIM,)))

    assert rules(found) == ["signal_count"] and found.waived == ()


def test_only_signal_count_can_be_waived() -> None:
    with pytest.raises(ValueError, match="only signal_count"):
        run(revision(), waived_rules=frozenset({"word_limit"}))
