"""The shipped seed file ``seed/sequences.yaml`` (FR-4, FR-71, Story 4.1).

It validates into exactly the spec's Design Notes content, field for field: the ``default``
Sequence's four Steps and their briefs, with Law-PrivateAI's Step 2 angle once per Market. Its
briefs are compared with a hard-coded transcription of the PRD's FR-4 table (decision 2: the Day
cell is the delay, the Ask cell the ask, the Words cell the limit and comparison, and the
Content cell holds every purpose but Step 1's and every allowed-content item), and that
transcription with the PRD's own table, parsed from ``prd.md`` (skipped only when the planning
folder isn't next to the engine). Its angles pass against the shipped Segments and Markets.
"""

import re

import pytest

from nextron.domain import seed_yaml
from nextron.domain.markets import validate_markets
from nextron.domain.segments import mentions_aba_512, mentions_sra_notice, validate_segments
from nextron.domain.sequences import (
    LinkRule,
    SegmentAngle,
    SequenceDef,
    StepBrief,
    SubjectRule,
    WordComparison,
    check_sequence_references,
    max_words,
    sequence_form,
    sequence_from_form,
    validate_sequences,
)
from nextron.main import (
    MARKETS_FILE_NAME,
    SEED_DIR,
    SEGMENTS_FILE_NAME,
    SEQUENCES_FILE_LABEL,
    SEQUENCES_FILE_NAME,
)
from tests.contracts.support import ENGINE_ROOT

SEED_FILE = ENGINE_ROOT / "seed" / "sequences.yaml"
SEED_TEXT = SEED_FILE.read_text(encoding="utf-8")
PRD = (
    ENGINE_ROOT.parent
    / "_bmad-output"
    / "planning-artifacts"
    / "prds"
    / "prd-nextron-outreach-engine-2026-11-14"
    / "prd.md"
)
FR4_SECTION = "#### FR-4: Sequence configuration"
FR4_HEADER = "| Step | Day | Content | Ask | Words |"

UNDER, AT_MOST = WordComparison.UNDER, WordComparison.AT_MOST
FOOTER_ONLY = LinkRule.FOOTER_ONLY

DEFAULT = SequenceDef(
    key="default",
    name="Default four-Step Sequence",
    steps=(
        StepBrief(
            step=1,
            delay_days=0,
            purpose="Event-led cold email",
            word_limit=80,
            word_comparison=UNDER,
            allowed_content=(
                "One observed Signal",
                "One sentence on the likely problem",
                "One credibility line from Approved Company Facts",
            ),
            ask='Relevance only, such as "Worth a look?" No meeting request or price',
            link_rule=FOOTER_ONLY,
            no_price=True,
            no_audit=True,
            subject_rule=SubjectRule(
                word_limit=7,
                word_comparison=UNDER,
                guidance=("About the prospect's world", "Never misleading"),
            ),
            segment_angles=(),
        ),
        StepBrief(
            step=2,
            delay_days=3,
            purpose="New angle",
            word_limit=70,
            word_comparison=AT_MOST,
            allowed_content=("One short Segment insight from Approved Company Facts",),
            ask="The relevance question, reworded",
            link_rule=FOOTER_ONLY,
            no_price=True,
            no_audit=True,
            subject_rule=None,
            segment_angles=(
                SegmentAngle(
                    segment="law_private_ai",
                    market="us",
                    angle="What ABA Formal Opinion 512 means for their intake or document work",
                ),
                SegmentAngle(
                    segment="law_private_ai",
                    market="uk",
                    angle="What the SRA notice means for their intake or document work",
                ),
            ),
        ),
        StepBrief(
            step=3,
            delay_days=7,
            purpose="Proof",
            word_limit=70,
            word_comparison=AT_MOST,
            allowed_content=(
                "One relevant case study or regulatory note from Approved Company Facts",
                'Never "just following up"',
            ),
            ask="Offer to send more detail by reply (no link or attachment)",
            link_rule=FOOTER_ONLY,
            no_price=True,
            no_audit=True,
            subject_rule=None,
            segment_angles=(),
        ),
        StepBrief(
            step=4,
            delay_days=14,
            purpose="Respectful close",
            word_limit=50,
            word_comparison=AT_MOST,
            allowed_content=(
                "The timing may be wrong",
                "The door stays open",
                "No guilt or urgency",
            ),
            ask='"Should I close the loop?" or a check-back date of their choosing',
            link_rule=FOOTER_ONLY,
            no_price=True,
            no_audit=True,
            subject_rule=None,
            segment_angles=(),
        ),
    ),
)

# The PRD's FR-4 table, cell for cell (Step, Day, Content, Ask, Words).
FR4_ROWS = (
    (
        "1",
        "0",
        "One observed Signal; one sentence on the likely problem; one credibility line from "
        "Approved Company Facts",
        'Relevance only, such as "Worth a look?" No meeting request or price',
        "Under 80",
    ),
    (
        "2",
        "3",
        "New angle: one short Segment insight from Approved Company Facts (Law-PrivateAI "
        "default: what ABA Op. 512 or the SRA notice means for their intake or document work)",
        "The relevance question, reworded",
        "70 or fewer",
    ),
    (
        "3",
        "7",
        "Proof: one relevant case study or regulatory note from Approved Company Facts; never "
        '"just following up"',
        "Offer to send more detail by reply (no link or attachment)",
        "70 or fewer",
    ),
    (
        "4",
        "14",
        "Respectful close: the timing may be wrong; the door stays open. No guilt or urgency",
        '"Should I close the loop?" or a check-back date of their choosing',
        "50 or fewer",
    ),
)
WORDS = {  # decision 2: the Words cell as (limit, comparison)
    re.compile(r"Under (\d+)"): UNDER,
    re.compile(r"(\d+) or fewer"): AT_MOST,
}
LAW_ANGLE_TAIL = "means for their intake or document work"


def shipped() -> tuple[SequenceDef, ...]:
    return validate_sequences(seed_yaml.parse(SEED_TEXT))


def words(cell: str) -> tuple[int, WordComparison]:
    """A Words cell as ``(word_limit, word_comparison)``; ``ValueError`` for any other form."""
    for pattern, comparison in WORDS.items():
        match = pattern.fullmatch(cell)
        if match is not None:
            return int(match.group(1)), comparison
    raise ValueError(f"an unknown Words cell: {cell}")


# The shipped file ---------------------------------------------------------------------------------


def test_the_shipped_file_is_the_one_seed_sync_loads() -> None:
    assert SEED_DIR / SEQUENCES_FILE_NAME == SEED_FILE
    assert SEQUENCES_FILE_LABEL == "seed/sequences.yaml"


def test_the_shipped_file_validates_into_exactly_the_design_notes_sequence() -> None:
    assert shipped() == (DEFAULT,)


def test_each_step_matches_the_design_notes_field_for_field() -> None:
    [default] = shipped()

    assert len(default.steps) == len(DEFAULT.steps) == 4
    for found, expected in zip(default.steps, DEFAULT.steps, strict=True):
        for name in StepBrief.__dataclass_fields__:
            assert getattr(found, name) == getattr(expected, name), (found.step, name)


def test_the_default_sequence_has_the_ac_s_shape() -> None:
    [default] = shipped()

    assert [s.delay_days for s in default.steps] == [0, 3, 7, 14]
    assert [max_words(s.word_limit, s.word_comparison) for s in default.steps] == [79, 70, 70, 50]
    assert all(s.no_price and s.no_audit for s in default.steps)
    assert {s.link_rule for s in default.steps} == {FOOTER_ONLY}
    subject = default.steps[0].subject_rule
    assert subject is not None
    assert max_words(subject.word_limit, subject.word_comparison) == 6  # fewer than 7 words
    assert [s.subject_rule for s in default.steps[1:]] == [None, None, None]


def test_the_header_comment_explains_the_file() -> None:
    header = SEED_TEXT.split("\nsequences:", 1)[0]

    assert all(line.startswith("#") or not line for line in header.splitlines())
    assert "`nextron seed sync`" in header
    assert "strict subset of YAML" in header
    for brief in DEFAULT.steps:
        assert f"      # Step {brief.step}, day {brief.delay_days}\n" in SEED_TEXT


def test_the_shipped_sequence_survives_the_file_form_round_trip() -> None:
    [default] = shipped()

    assert sequence_from_form(sequence_form(default)) == default


def test_the_shipped_references_pass_against_the_shipped_segments_and_markets() -> None:
    segments = validate_segments(
        seed_yaml.parse((SEED_DIR / SEGMENTS_FILE_NAME).read_text(encoding="utf-8"))
    )
    markets = validate_markets(
        seed_yaml.parse((SEED_DIR / MARKETS_FILE_NAME).read_text(encoding="utf-8"))
    )

    check_sequence_references(
        shipped(),
        {segment.key for segment in segments},
        {market.code: market.country_code for market in markets},
    )


# Against the PRD's FR-4 table (decision 2) --------------------------------------------------------


def test_each_step_s_day_ask_and_words_are_the_prd_s_cells() -> None:
    [default] = shipped()

    for brief, (step, day, _, ask, words_cell) in zip(default.steps, FR4_ROWS, strict=True):
        assert brief.step == int(step)
        assert brief.delay_days == int(day)
        assert brief.ask == ask
        assert (brief.word_limit, brief.word_comparison) == words(words_cell)


def test_every_later_purpose_and_allowed_content_item_is_in_the_content_cell() -> None:
    [default] = shipped()

    for brief, row in zip(default.steps, FR4_ROWS, strict=True):
        content = row[2].casefold()
        if brief.step > 1:
            assert content.startswith(f"{brief.purpose.casefold()}:"), brief.step
        for item in brief.allowed_content:
            assert item.casefold() in content, (brief.step, item)


def test_step_1_s_purpose_is_the_playbook_s_label() -> None:
    assert shipped()[0].steps[0].purpose == "Event-led cold email"
    assert ":" not in FR4_ROWS[0][2].split(";", 1)[0]  # the PRD's Step 1 cell has no label


def test_step_1_asks_only_about_relevance_never_for_a_meeting() -> None:
    ask = shipped()[0].steps[0].ask

    assert "Worth a look?" in ask
    assert "20-minute" not in ask
    assert "No meeting request" in ask


def test_the_law_private_ai_angles_are_one_per_market() -> None:
    angles = shipped()[0].steps[1].segment_angles
    law_cell = FR4_ROWS[1][2]

    assert angles == DEFAULT.steps[1].segment_angles
    us, uk = angles
    assert (us.segment, us.market, uk.segment, uk.market) == (
        "law_private_ai",
        "us",
        "law_private_ai",
        "uk",
    )
    assert mentions_aba_512(us.angle) and not mentions_sra_notice(us.angle)
    assert mentions_sra_notice(uk.angle) and not mentions_aba_512(uk.angle)
    assert all(angle.angle.endswith(LAW_ANGLE_TAIL) for angle in angles)
    assert f"Law-PrivateAI default: what ABA Op. 512 or the SRA notice {LAW_ANGLE_TAIL}" in (
        law_cell
    )


def test_the_words_mapping_reads_each_prd_form() -> None:
    assert words("Under 80") == (80, UNDER)
    assert words("70 or fewer") == (70, AT_MOST)
    with pytest.raises(ValueError, match="an unknown Words cell"):
        words("About 70")


def fr4_table(text: str) -> tuple[tuple[str, ...], ...]:
    """The rows of the table under ``#### FR-4: Sequence configuration``, each cell stripped."""
    section = text.split(FR4_SECTION, 1)[1].split("\n#### ", 1)[0].split("\n### ", 1)[0]
    lines = [line.strip() for line in section.splitlines()]
    start = lines.index(FR4_HEADER)
    rows = []
    for line in lines[start + 2 :]:  # past the header and its |---| rule
        if not line.startswith("|"):
            break
        rows.append(tuple(cell.strip() for cell in line.strip("|").split("|")))
    return tuple(rows)


def test_the_transcription_is_the_prds_own_fr4_table() -> None:
    if not PRD.is_file():
        pytest.skip(f"the PRD is not at {PRD}")

    assert fr4_table(PRD.read_text(encoding="utf-8")) == FR4_ROWS


def test_the_prd_parser_reads_exactly_the_fr4_table() -> None:
    text = (
        "#### FR-3: Before\n"
        f"{FR4_HEADER}\n"
        "| 9 | 9 | Not this table | Ask | Under 9 |\n"
        f"{FR4_SECTION}\n"
        "Some text.\n"
        "\n"
        f"  {FR4_HEADER}\n"
        "  |---|---|---|---|---|\n"
        '  | 1 | 0 | One; two | "Worth a look?" | Under 80 |\n'
        "| 2 | 3 |New angle: x|Reworded| 70 or fewer |\n"
        "\n"
        "| 3 | 7 | After a blank line | Ask | 70 or fewer |\n"
        "### 4.2 Lead discovery\n"
        "| 4 | 14 | Later | Ask | 50 or fewer |\n"
    )

    assert fr4_table(text) == (
        ("1", "0", "One; two", '"Worth a look?"', "Under 80"),
        ("2", "3", "New angle: x", "Reworded", "70 or fewer"),
    )
