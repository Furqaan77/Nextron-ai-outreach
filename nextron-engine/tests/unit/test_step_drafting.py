"""Drafting Step 1's pure core (``nextron.domain.step_drafting``, Story 4.15): the masker, the
trusted framing, the untrusted blocks, the proposal's code checks and the failure texts; and the
drafting smoke scorer (``nextron.domain.drafting_smoke``) over the shipped cases."""

import json
from datetime import UTC, date, datetime, time
from typing import Any, Final

import pytest

from nextron.adapters.ai.schemas import DRAFT_SEQUENCE_V1, DRAFT_SEQUENCE_V2, validate
from nextron.domain import grounding_rules, seed_yaml
from nextron.domain import pseudonymise as pseudonymise_module
from nextron.domain.ai import Framing, check_blocks
from nextron.domain.ai_test_gate import TestSet
from nextron.domain.ai_test_sets import set_case
from nextron.domain.drafting_smoke import (
    INVALID_PROPOSAL,
    NO_ANSWER,
    NO_GROUNDING,
    case_blocks,
    case_framing,
    draft_flags,
    grounding_codes,
    grounding_input_of,
    score_drafting,
)
from nextron.domain.grounding_rules import Claim
from nextron.domain.markets import EnglishVariant
from nextron.domain.pseudonymise import mask_contact_details, pseudonymise
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.domain.step_drafting import (
    FAILURE_TEXTS,
    MAX_REASON,
    DraftContact,
    DraftEvidence,
    DraftFact,
    DraftFailure,
    DraftSignal,
    Proposal,
    ProposedClaim,
    FollowUp,
    FollowUpProposal,
    brief_section,
    citations_of,
    claims_of,
    draft_deadline_of,
    facts_section,
    failure_text,
    follow_up_blocks,
    follow_up_citations,
    follow_up_claims,
    follow_up_fingerprint,
    follow_up_framing,
    framing,
    input_blocks,
    next_slot,
    note_text,
    read_follow_ups,
    read_proposal,
    read_step_1,
    regulation_steps,
    validate_follow_ups,
    validate_proposal,
)
from tests.contracts.support import ENGINE_ROOT

LEAD: Final = "lead:01JD3QZ8X5V9K2M7N4P6R8T0WY"
EVIDENCE: Final = "01JD3QZ8X5V9K2M7N4P6R8T0E1"
SIGNAL: Final = "01JD3QZ8X5V9K2M7N4P6R8T0S1"
[SEQUENCE] = validate_sequences(
    seed_yaml.parse((ENGINE_ROOT / "seed" / "sequences.yaml").read_text(encoding="utf-8"))
)
SEGMENTS: Final = validate_segments(
    seed_yaml.parse((ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8"))
)
SMOKE: Final = sorted((ENGINE_ROOT / "tests" / "ai_sets" / "drafting_smoke").glob("*.json"))


def proposal(**changes: Any) -> Proposal:
    fields: dict[str, Any] = {
        "subject": "Your intake hire",
        "body": "Hi Jane,\n\nSaw you are hiring an intake coordinator.\n\nWorth a look?",
        "claims": (
            ProposedClaim("hiring an intake coordinator", EVIDENCE, None, None),
            ProposedClaim("A 17+ person team", None, "nextron_team", 1),
        ),
        "cited_signal_id": SIGNAL,
        "explanation": "The newest Event.",
    }
    return Proposal(**(fields | changes))


def valid(found: Proposal) -> bool:
    return validate_proposal(
        found,
        evidence_ids=frozenset({EVIDENCE}),
        facts=frozenset({("nextron_team", 1)}),
        live_events=frozenset({SIGNAL}),
    )


# The masker -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Write to jane.doe@firm.co.uk today", "Write to [email] today"),
        ("Call +44 20 7946 0958 or (212) 555-0182.", "Call [phone] or [phone]."),
        ("Founded 2019-04-01, 250 staff", "Founded 2019-04-01, 250 staff"),
        ("Thornbury Lane Solicitors, Bristol", "Thornbury Lane Solicitors, Bristol"),
        ("[email] and [phone]", "[email] and [phone]"),
    ],
)
def test_the_masker_replaces_only_email_addresses_and_phone_numbers(
    text: str, masked: str
) -> None:
    assert mask_contact_details(text) == masked
    assert mask_contact_details(masked) == masked  # idempotent


@pytest.mark.parametrize(
    "text",
    [
        "Active 2019-2024 in Ohio",
        "Active 2019 - 2024 in Ohio",
        "Filed 16/11/2026 and 2026.11.16",
        "Over 10 000 000 documents",
        "Over 1.250.000 documents and 1,250,000 pages",
        "Ref 1234567890123456789",  # more than 15 digits
    ],
)
def test_the_masker_keeps_dates_year_ranges_grouped_numbers_and_long_runs(text: str) -> None:
    assert mask_contact_details(text) == text


def test_the_masker_still_masks_phone_numbers_and_emails_among_numbers() -> None:
    text = "In 2019-2024: 020 7946 0318, +1 (614) 555-0142, ops@firm.example, 10 000 000 files"

    assert mask_contact_details(text) == (
        "In 2019-2024: [phone], [phone], [email], 10 000 000 files"
    )


def test_pseudonymise_still_masks_what_the_masker_keeps() -> None:
    assert pseudonymise("active 2019-2024, 10 000 000 files") == "active [phone], [phone] files"


def test_the_masker_keeps_the_grounding_rules_not_phone_shapes() -> None:
    assert pseudonymise_module._NOT_PHONE.pattern == grounding_rules._NOT_PHONE.pattern
    assert pseudonymise_module.MAX_PHONE_DIGITS == grounding_rules.MAX_PHONE_DIGITS


def test_the_masker_refuses_a_non_str() -> None:
    with pytest.raises(TypeError):
        mask_contact_details(None)  # type: ignore[arg-type]


# The framing ------------------------------------------------------------------------------------


def test_the_brief_section_holds_the_step_1_rules_and_the_segment_market_angle() -> None:
    step1 = SEQUENCE.steps[0]
    text = brief_section(step1, "law_private_ai", "uk")

    assert text.startswith("# Step brief\n\nThis email is Step 1 of the Sequence.")
    assert "Purpose: Event-led cold email" in text
    assert "Body length: at most 79 words." in text  # under 80
    assert "Subject: at most 6 words." in text
    assert "- One observed Signal" in text
    assert "Never name a price." in text and "Never offer or mention an audit." in text
    assert "Angle for" not in text  # the seed's Step 1 has no angle
    step2 = SEQUENCE.steps[1]
    assert "Angle for this Segment and Market: What the SRA notice" in brief_section(
        step2, "law_private_ai", "uk"
    )
    assert "Angle for" not in brief_section(step2, "ops_wholesale", "uk")


def test_the_facts_section_lists_each_fact_with_its_id_and_version() -> None:
    text = facts_section((DraftFact("nextron_team", 2, "Call us on 020 7946 0958."),))

    assert "- fact_id `nextron_team`, version 2: Call us on [phone]." in text
    assert facts_section(()).endswith("(none)")


def test_the_note_holds_the_angle_and_the_english_variant() -> None:
    assert note_text("Lead with intake.", EnglishVariant.EN_GB) == (
        "Campaign angle for this Step: Lead with intake.\nEnglish: British English"
    )
    assert note_text(None, EnglishVariant.EN_US) == "English: American English"
    assert note_text("  ", EnglishVariant.EN_US) == "English: American English"


def test_framing_masks_every_part() -> None:
    found = framing(
        SEQUENCE.steps[0],
        "law_private_ai",
        "us",
        (DraftFact("f", 1, "mail sales@nextronai.com"),),
        "ring 212 555 0182",
        EnglishVariant.EN_US,
    )

    assert isinstance(found, Framing)
    joined = found.brief + found.facts + found.note
    assert "@" not in joined and "555" not in joined
    assert "[email]" in found.facts and "[phone]" in found.note


# The blocks -------------------------------------------------------------------------------------


def test_the_blocks_are_evidence_signals_then_the_contact_masked() -> None:
    blocks = input_blocks(
        LEAD,
        (DraftEvidence(EVIDENCE, "quote", "Email jobs@firm.com or call 020 7946 0958."),),
        (DraftSignal(SIGNAL, "law_hiring", "event", 1, date(2026, 11, 10), (EVIDENCE,)),),
        DraftContact("Jane Doe", None),
    )

    assert check_blocks(blocks) == LEAD
    assert [(b.source, b.block_id) for b in blocks] == [
        ("evidence", EVIDENCE),
        ("signal", SIGNAL),
        ("contact", "contact"),
    ]
    assert blocks[0].text == "kind: quote\n\nEmail [email] or call [phone]."
    assert blocks[1].text == (
        "type: law_hiring\nclass: event\ntier: 1\nevent date: 2026-11-10\n"
        f"evidence: {EVIDENCE}"
    )
    assert blocks[2].text == "name: Jane Doe\nrole: unknown"
    undated = input_blocks(
        LEAD, (), (DraftSignal(SIGNAL, "x", "attribute", 3, None, ()),), DraftContact(None, "")
    )
    assert "event date: unknown\nevidence: none" in undated[0].text
    assert undated[1].text == "name: unknown\nrole: unknown"


# The proposal -----------------------------------------------------------------------------------


def test_a_schema_valid_answer_reads_as_a_proposal() -> None:
    answer = validate(
        DRAFT_SEQUENCE_V1,
        {
            "subject": "Your intake hire",
            "body": "Hi Jane",
            "claims": [
                {"span": "Hi", "evidence_id": EVIDENCE, "fact_id": None, "fact_version": None}
            ],
            "cited_signal_id": SIGNAL,
            "explanation": "x",
        },
    )
    assert answer is not None

    found = read_proposal(answer)

    assert found.claims == (ProposedClaim("Hi", EVIDENCE, None, None),)
    assert found.cited_signal_id == SIGNAL
    assert validate(DRAFT_SEQUENCE_V1, {**answer, "reasoning": "no"}) is None


def test_a_valid_proposal_passes_and_gives_its_claims_and_citations() -> None:
    found = proposal(
        claims=(
            ProposedClaim("a", EVIDENCE, None, None),
            ProposedClaim("b", None, "nextron_team", 1),
            ProposedClaim("c", EVIDENCE, None, None),
        )
    )

    assert valid(found)
    assert claims_of(found) == (
        Claim("a", EVIDENCE, None),
        Claim("b", None, ("nextron_team", 1)),
        Claim("c", EVIDENCE, None),
    )
    assert citations_of(found) == ((EVIDENCE,), (("nextron_team", 1),))


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "  "},
        {"body": "\n"},
        {"claims": (ProposedClaim("x", "01JD3QZ8X5V9K2M7N4P6R8T0ZZ", None, None),)},  # unknown
        {"claims": (ProposedClaim("x", None, "nextron_team", 2),)},  # not an input version
        {"claims": (ProposedClaim("x", None, None, None),)},  # cites nothing
        {"claims": (ProposedClaim("x", EVIDENCE, "nextron_team", 1),)},  # cites two
        {"claims": (ProposedClaim("x", None, "nextron_team", None),)},  # a fact without version
        {"claims": (ProposedClaim("x", EVIDENCE, None, 1),)},  # a stray version
        {"cited_signal_id": None},
        {"cited_signal_id": "01JD3QZ8X5V9K2M7N4P6R8T0S2"},  # not a live Event
    ],
)
def test_each_bad_proposal_is_refused(changes: dict[str, Any]) -> None:
    assert not valid(proposal(**changes))


def test_a_proposal_with_no_claims_may_pass_the_code_check() -> None:
    assert valid(proposal(claims=()))


def test_each_failure_has_a_short_code_and_a_page_text() -> None:
    assert set(FAILURE_TEXTS) == set(DraftFailure)
    assert all(len(reason.value) <= MAX_REASON for reason in DraftFailure)
    assert failure_text("no_route") == "no tested drafting model is active"
    assert failure_text("something_else") == "something_else"


# The drafting smoke scorer ----------------------------------------------------------------------


def smoke(path: Any) -> Any:
    return set_case(TestSet.DRAFTING_SMOKE, path.name, json.loads(path.read_text(encoding="utf-8")))


def test_the_smoke_set_is_there() -> None:
    assert len(SMOKE) >= 10


@pytest.mark.parametrize("path", SMOKE, ids=[path.stem for path in SMOKE])
def test_each_smoke_case_builds_one_leads_blocks_and_a_framing(path: Any) -> None:
    case = smoke(path)

    blocks, _urgency = case_blocks(case, 3, SEGMENTS)
    found = case_framing(case, SEQUENCE.steps[0], EnglishVariant.EN_GB)

    assert check_blocks(blocks) == "lead:case3"
    assert {block.source for block in blocks} == {"evidence", "signal", "contact"}
    assert found.brief.startswith("# Step brief")
    assert all(f"`{row['fact_id']}`" in found.facts for row in case.input["facts"])


def clean_answer(case: Any) -> dict[str, Any]:
    """A clean Step 1 for ``case``: one claim on the expected Event's Evidence, one fact."""
    signal = next(
        row for row in case.input["signals"] if row["id"] == case.expected["cited_signal_id"]
    )
    fact = case.input["facts"][0]
    return {
        "subject": "Your new office",
        "body": "Hi there,\n\nIt looks like the firm made a change.\n\nWorth a look?",
        "claims": [
            {
                "span": "It looks like the firm made a change",
                "evidence_id": signal["evidence_id"],
                "fact_id": None,
                "fact_version": None,
            },
            {
                "span": "Worth a look",
                "evidence_id": None,
                "fact_id": fact["fact_id"],
                "fact_version": fact["version"],
            },
        ],
        "cited_signal_id": signal["id"],
        "explanation": "the strongest Event",
    }


def test_a_clean_draft_and_a_clean_check_score_zero_flags() -> None:
    case = smoke(SMOKE[0])
    answer = clean_answer(case)

    scored = draft_flags(case, answer, SEQUENCE.steps[0], SEGMENTS)
    assert scored.codes == ()
    assert scored.proposal is not None
    given = grounding_input_of(case, scored.proposal)
    assert given.step == 1 and given.market == case.input["market"]
    assert [row.id for row in given.evidence] == [answer["claims"][0]["evidence_id"]]
    codes = grounding_codes(scored.proposal, {"claims": [], "flags": []})
    outcome, result = score_drafting(case, (*scored.codes, *codes))
    assert outcome.flags == 0 and result.passed


def test_no_answer_an_invalid_proposal_or_no_grounding_tuple_each_flag() -> None:
    case = smoke(SMOKE[0])
    answer = clean_answer(case)

    assert draft_flags(case, "LlmRefused", SEQUENCE.steps[0], SEGMENTS).codes == (NO_ANSWER,)
    wrong = {**answer, "cited_signal_id": "nope"}
    bad = draft_flags(case, wrong, SEQUENCE.steps[0], SEGMENTS)
    assert INVALID_PROPOSAL in bad.codes and bad.proposal is None
    good = draft_flags(case, answer, SEQUENCE.steps[0], SEGMENTS).proposal
    assert good is not None
    assert grounding_codes(good, None) == (NO_GROUNDING,)
    assert grounding_codes(good, "LlmInvalid") == (NO_ANSWER,)
    outcome, result = score_drafting(case, (NO_GROUNDING,))
    assert outcome.flags == 1 and not result.passed
    assert result.got == f"flagged: {NO_GROUNDING}"


def test_the_rules_flag_a_draft_that_breaks_the_brief() -> None:
    case = smoke(SMOKE[0])
    answer = {**clean_answer(case), "body": "Hi,\n\nSee https://example.com now?\n\nWorth a look?"}

    scored = draft_flags(case, answer, SEQUENCE.steps[0], SEGMENTS)

    assert "link_in_step1" in scored.codes


# Story 4.18: schema 2 and the follow-ups ---------------------------------------------------------

KEY: Final = "claude-opus-5-5:medium:v2:s2:abcdef012345"
ABA: Final = ("aba_formal_opinion_512", 1)
WISH: Final = ("wish_wheels", 1)
MP: Final = ("mp_online_digital", 1)
STEP_1_FACT: Final = "legal_ai"
FACTS: Final = frozenset({ABA, WISH, MP, (STEP_1_FACT, 1)})
REGULATION: Final = frozenset({ABA})


def v2_step_1(**changes: Any) -> dict[str, Any]:
    step = {
        "step": 1,
        "subject": "Your intake hire",
        "body": "Hi Jane,\n\nSaw you are hiring.\n\nWorth a look?",
        "claims": [
            {"span": "hiring", "evidence_id": EVIDENCE, "fact_id": None, "fact_version": None}
        ],
        "angle_fact_id": None,
        "angle_fact_version": None,
    }
    return {"steps": [step | changes], "cited_signal_id": SIGNAL, "explanation": "Newest."}


def fact_claim(fact: tuple[str, int]) -> dict[str, Any]:
    return {"span": "a new angle", "evidence_id": None, "fact_id": fact[0], "fact_version": fact[1]}


def follow_up(step: int, angle: tuple[str, int] | None, **changes: Any) -> dict[str, Any]:
    row = {
        "step": step,
        "subject": "ignored",
        "body": f"Hi Jane,\n\nStep {step} on a new angle.\n\nUseful?",
        "claims": [] if angle is None else [fact_claim(angle)],
        "angle_fact_id": None if angle is None else angle[0],
        "angle_fact_version": None if angle is None else angle[1],
    }
    return row | changes


def answer(*rows: dict[str, Any]) -> dict[str, Any]:
    found = rows or (follow_up(2, ABA), follow_up(3, WISH), follow_up(4, MP))
    return {"steps": list(found), "cited_signal_id": None, "explanation": "A new angle each."}


def accepts(parsed: dict[str, Any], regulation: frozenset[int] = frozenset({2})) -> bool:
    return validate_follow_ups(
        read_follow_ups(parsed),
        evidence_ids={EVIDENCE},
        facts=FACTS,
        step_1_fact_ids={STEP_1_FACT},
        regulation_steps=regulation,
        regulation_facts=REGULATION,
    )


def test_a_schema_2_step_1_answer_reads_as_the_step_1_proposal() -> None:
    parsed = v2_step_1()
    assert validate(DRAFT_SEQUENCE_V2, parsed) == parsed

    found = read_step_1(parsed, 2)

    assert found == Proposal(
        "Your intake hire",
        "Hi Jane,\n\nSaw you are hiring.\n\nWorth a look?",
        (ProposedClaim("hiring", EVIDENCE, None, None),),
        SIGNAL,
        "Newest.",
    )
    assert read_step_1(v2_step_1(angle_fact_id="x", angle_fact_version=1), 2) == found  # ignored
    v1 = {"subject": "S", "body": "B", "claims": [], "cited_signal_id": None, "explanation": ""}
    assert read_step_1(v1, 1) == read_proposal(v1)


@pytest.mark.parametrize(
    "parsed",
    [
        v2_step_1(step=2),  # not Step 1
        {**v2_step_1(), "steps": []},  # no Step
        {**v2_step_1(), "steps": v2_step_1()["steps"] * 2},  # two entries
        answer(),  # the follow-ups
    ],
    ids=["step_2", "none", "two", "follow_ups"],
)
def test_a_schema_2_answer_that_isnt_exactly_step_1_is_discarded(parsed: dict[str, Any]) -> None:
    assert read_step_1(parsed, 2) is None


def test_an_unknown_schema_version_reads_as_nothing() -> None:
    assert read_step_1(v2_step_1(), 3) is None
    with pytest.raises(TypeError):
        read_step_1("text", 2)  # type: ignore[arg-type]


def test_the_follow_up_framing_names_the_steps_and_the_law_uk_sra_angle() -> None:
    briefs = SEQUENCE.steps[1:4]

    found = follow_up_framing(
        briefs,
        "law_private_ai",
        "uk",
        (DraftFact("sra_notice", 1, "Ring 020 7946 0958."),),
        {2: " Lead with the SRA notice. ", 3: "", 1: "Step 1 only"},
        EnglishVariant.EN_GB,
    )

    assert found.brief.startswith("# Steps to write\n\nWrite Steps 2, 3 and 4 of the Sequence")
    for step in (2, 3, 4):
        assert f"This email is Step {step} of the Sequence." in found.brief
    assert "Angle for this Segment and Market: What the SRA notice" in found.brief
    assert "ABA" not in found.brief  # the US angle stays out of a UK framing
    assert "[phone]" in found.facts and "020 7946" not in found.facts
    assert found.note == (
        "Campaign angle for Step 2: Lead with the SRA notice.\nEnglish: British English"
    )
    with pytest.raises(ValueError, match="Step 2, 3 and 4"):
        follow_up_framing(
            SEQUENCE.steps[0:3], "law_private_ai", "uk", (), {}, EnglishVariant.EN_GB
        )


def test_only_a_step_whose_brief_has_the_segment_market_angle_needs_a_regulation_fact() -> None:
    briefs = SEQUENCE.steps[1:4]

    assert regulation_steps(briefs, "law_private_ai", "uk") == {2}
    assert regulation_steps(briefs, "law_private_ai", "us") == {2}
    assert regulation_steps(briefs, "ops_wholesale", "uk") == frozenset()


def test_the_follow_up_blocks_start_with_step_1_as_an_untrusted_earlier_step() -> None:
    blocks = follow_up_blocks(
        LEAD,
        "Your intake hire",
        "Hi Jane, write to jane@firm.example",
        (DraftEvidence(EVIDENCE, "quote", "We are hiring."),),
        (),
        DraftContact("Jane", None),
    )

    first, *rest = blocks
    assert (first.subject, first.source, first.block_id) == (LEAD, "earlier_step", "step_1")
    assert first.text == "subject: Your intake hire\n\nHi Jane, write to [email]"
    assert [block.source for block in rest] == ["evidence", "contact"]
    check_blocks(blocks)


def test_a_clean_follow_up_answer_passes_the_code_check() -> None:
    parsed = answer()
    assert validate(DRAFT_SEQUENCE_V2, parsed) == parsed

    assert accepts(parsed)
    shuffled = answer(follow_up(4, MP), follow_up(2, ABA), follow_up(3, WISH))
    assert accepts(shuffled)  # any order, each Step once


ON_OTHER_EVIDENCE: Final = [
    {"span": "x", "evidence_id": "other", "fact_id": None, "fact_version": None}
]
ON_TWO: Final = [{"span": "x", "evidence_id": EVIDENCE, "fact_id": WISH[0], "fact_version": 1}]


@pytest.mark.parametrize(
    "parsed",
    [
        answer(follow_up(2, ABA), follow_up(3, WISH)),
        answer(follow_up(2, ABA), follow_up(3, WISH), follow_up(3, MP)),
        answer(follow_up(2, ABA), follow_up(3, WISH), follow_up(4, MP), follow_up(5, ("x", 1))),
        answer(follow_up(1, ABA), follow_up(3, WISH), follow_up(4, MP)),
        answer(follow_up(2, ABA), follow_up(3, WISH, body="  "), follow_up(4, MP)),
        answer(follow_up(2, ABA), follow_up(3, WISH), follow_up(4, None)),
        answer(follow_up(2, ABA), follow_up(3, WISH), follow_up(4, ("unknown", 1))),
        answer(follow_up(2, ABA), follow_up(3, WISH), follow_up(4, (MP[0], 2))),
        answer(follow_up(2, ABA), follow_up(3, ABA), follow_up(4, MP)),
        answer(follow_up(2, ABA), follow_up(3, (STEP_1_FACT, 1)), follow_up(4, MP)),
        answer(follow_up(2, WISH), follow_up(3, ABA), follow_up(4, MP)),
        answer(follow_up(2, ABA), follow_up(3, WISH, claims=ON_OTHER_EVIDENCE), follow_up(4, MP)),
        answer(follow_up(2, ABA), follow_up(3, WISH, claims=ON_TWO), follow_up(4, MP)),
    ],
    ids=[
        "step_4_missing",
        "step_3_twice",
        "step_5_extra",
        "step_1_isnt_a_follow_up",
        "empty_body",
        "no_angle",
        "unknown_fact",
        "wrong_version",
        "step_2_angle_again",
        "step_1_fact",
        "step_2_not_regulation",
        "evidence_not_sent",
        "two_citations",
    ],
)
def test_each_bad_follow_up_answer_is_refused(parsed: dict[str, Any]) -> None:
    assert not accepts(parsed)


def test_without_a_regulation_step_any_input_fact_may_be_step_2s_angle() -> None:
    assert accepts(answer(follow_up(2, WISH), follow_up(3, ABA), follow_up(4, MP)), frozenset())


def test_a_follow_ups_citations_end_with_its_angle_fact_when_no_claim_cites_it() -> None:
    proposal_ = read_follow_ups(answer())
    assert isinstance(proposal_, FollowUpProposal)
    assert follow_up_citations(proposal_.steps[0]) == ((), (ABA,))
    bare = FollowUp(3, "s", "b", (ProposedClaim("x", EVIDENCE, None, None),), *WISH)
    assert follow_up_citations(bare) == ((EVIDENCE,), (WISH,))
    assert follow_up_claims(bare) == (Claim("x", EVIDENCE, None),)


def test_the_follow_up_fingerprint_moves_with_step_1_the_evidence_and_the_facts() -> None:
    base = follow_up_fingerprint(KEY, "D1", 1, (EVIDENCE,), (ABA, WISH))

    assert base.encode() == follow_up_fingerprint(KEY, "D1", 1, [EVIDENCE], (WISH, ABA)).encode()
    for changed in (
        follow_up_fingerprint(KEY, "D1", 2, (EVIDENCE,), (ABA, WISH)),  # Step 1 edited
        follow_up_fingerprint(KEY, "D1", 1, (), (ABA, WISH)),  # its Evidence gone
        follow_up_fingerprint(KEY, "D1", 1, (EVIDENCE,), ((ABA[0], 2), WISH)),  # a new version
        follow_up_fingerprint(KEY.replace("v2", "v3"), "D1", 1, (EVIDENCE,), (ABA, WISH)),
    ):
        assert changed.encode() != base.encode()


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        ("2026-11-18T09:00:00+00:00", "2026-11-19T07:00:00+00:00"),  # that afternoon's batch
        ("2026-11-18T14:00:00+00:00", "2026-11-20T07:00:00+00:00"),  # at 14:00: the next one
        ("2026-11-18T16:30:00+00:00", "2026-11-20T07:00:00+00:00"),  # after: tomorrow's
        ("2026-11-18T23:59:00+00:00", "2026-11-20T07:00:00+00:00"),
    ],
)
def test_the_deadline_is_the_first_07_00_after_the_first_submit_slot(
    created: str, expected: str
) -> None:
    found = draft_deadline_of(at(created), time(14, 0), time(7, 0), "UTC")

    assert found == at(expected)
    assert found.tzinfo is UTC


def test_the_deadline_follows_the_senders_timezone_across_a_clock_change() -> None:
    # London moves to BST on 2026-03-29: 07:00 local that day is 06:00 UTC
    created = at("2026-03-28T10:00:00+00:00")
    found = draft_deadline_of(created, time(14, 0), time(7, 0), "Europe/London")

    assert found == at("2026-03-29T06:00:00+00:00")
    assert next_slot(at("2026-11-18T13:59:00+00:00"), time(9, 0), "America/New_York") == at(
        "2026-11-18T14:00:00+00:00"
    )
    with pytest.raises(TypeError):
        next_slot(datetime(2026, 11, 18, 9), time(14, 0), "UTC")  # noqa: DTZ001
    with pytest.raises(TypeError):
        next_slot(at("2026-11-18T09:00:00+00:00"), "14:00", "UTC")  # type: ignore[arg-type]


def test_the_smoke_scorer_reads_a_schema_2_answer_as_step_1() -> None:
    case = smoke(SMOKE[0])
    clean = clean_answer(case)
    step = {key: clean[key] for key in ("subject", "body", "claims")}
    parsed = {
        "steps": [{"step": 1, **step, "angle_fact_id": None, "angle_fact_version": None}],
        "cited_signal_id": clean["cited_signal_id"],
        "explanation": clean["explanation"],
    }

    scored = draft_flags(case, parsed, SEQUENCE.steps[0], SEGMENTS, 2)

    assert scored.codes == () and scored.proposal is not None
    assert scored.proposal.subject == clean["subject"]
    two = {**parsed, "steps": parsed["steps"] * 2}
    refused = draft_flags(case, two, SEQUENCE.steps[0], SEGMENTS, 2)
    assert refused.codes == (INVALID_PROPOSAL,) and refused.proposal is None
