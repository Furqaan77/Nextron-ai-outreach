"""Drafting Step 1's pure core (``nextron.domain.step_drafting``, Story 4.15): the masker, the
trusted framing, the untrusted blocks, the proposal's code checks and the failure texts; and the
drafting smoke scorer (``nextron.domain.drafting_smoke``) over the shipped cases."""

import json
from datetime import date
from typing import Any, Final

import pytest

from nextron.adapters.ai.schemas import DRAFT_SEQUENCE_V1, validate
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
    brief_section,
    citations_of,
    claims_of,
    facts_section,
    failure_text,
    framing,
    input_blocks,
    note_text,
    read_proposal,
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
