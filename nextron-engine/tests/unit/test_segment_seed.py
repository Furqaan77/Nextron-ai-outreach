"""The shipped seed file ``seed/segments.yaml`` (FR-1, Story 2.1).

It validates into exactly the spec's Design Notes content. Its Signal types are compared row
for row with a hard-coded transcription of the PRD's FR-1 table through a fixed PRD-word to code
mapping, and that transcription with the PRD's own table, parsed from ``prd.md`` (skipped only
when the planning folder isn't next to the engine).
"""

import re

import pytest

from nextron.domain import seed_yaml
from nextron.domain.segments import (
    DiscoveryQueries,
    Disqualifier,
    DisqualifierKind,
    EvidenceType,
    PreferredRoles,
    RegistryQueries,
    SegmentDef,
    SignalClass,
    SignalSource,
    SignalType,
    SizeClue,
    SizeRange,
    SizeUnit,
    definition_text,
    represents_market_fact,
    segment_from_text,
    validate_segments,
)
from nextron.main import SEED_DIR, SEGMENTS_FILE_NAME
from tests.contracts.support import ENGINE_ROOT

SEED_FILE = ENGINE_ROOT / "seed" / "segments.yaml"
PRD = (
    ENGINE_ROOT.parent
    / "_bmad-output"
    / "planning-artifacts"
    / "prds"
    / "prd-nextron-outreach-engine-2026-11-14"
    / "prd.md"
)
FR1_HEADER = "| Segment | Signal | Class | Signal Tier | Evidence | Source |"

Q, A, C = EvidenceType.QUOTE, EvidenceType.ABSENCE, EvidenceType.COUNT
S = SignalSource

LAW = SegmentDef(
    key="law_private_ai",
    name="Law-PrivateAI",
    industry_keywords=("law firm", "legal services", "attorneys", "solicitors"),
    size_range=SizeRange(5, 50, SizeUnit.LAWYERS),
    decision_maker_roles=(("Managing Partner",), ("Owner",)),
    disqualifiers=(
        Disqualifier(
            "law_fewer_than_5_lawyers",
            DisqualifierKind.FEWER_THAN,
            SizeClue.LAWYERS_FIRM_WIDE,
            5,
            "Fewer than 5 lawyers",
        ),
        Disqualifier(
            "law_office_of_large_firm",
            DisqualifierKind.MORE_THAN,
            SizeClue.LAWYERS_FIRM_WIDE,
            50,
            "Office of a firm with more than 50 lawyers",
        ),
    ),
    signal_types=(
        SignalType(
            "law_hiring_legal_ops",
            "Hiring intake, legal-ops, knowledge-management, or innovation staff",
            SignalClass.EVENT,
            1,
            (Q,),
            (S.CAREERS_PAGE, S.ATS_BOARD, S.SIGNAL_INBOX),
            False,
            60,
            ("intake", "legal operations", "knowledge management", "innovation"),
        ),
        SignalType(
            "law_ai_confidentiality_post",
            "Post about AI or client confidentiality",
            SignalClass.EVENT,
            1,
            (Q,),
            (S.SIGNAL_INBOX,),
            False,
            60,
        ),
        SignalType(
            "law_expansion",
            "New office, merger, or practice-area expansion",
            SignalClass.EVENT,
            2,
            (Q,),
            (S.NEWS_PAGES, S.SIGNAL_INBOX),
            False,
            35,
        ),
        SignalType(
            "law_size_document_heavy",
            "5–50 attorneys; document-heavy practice",
            SignalClass.ATTRIBUTE,
            3,
            (C, Q),
            (S.TEAM_PAGES, S.PRACTICE_PAGES),
            False,
            15,
        ),
        SignalType(
            "law_contact_form_only",
            "Contact form only (no chat, booking, or after-hours line)",
            SignalClass.ATTRIBUTE,
            3,
            (A,),
            (S.WEBSITE,),
            True,
            15,
        ),
    ),
    discovery_queries=DiscoveryQueries(
        ("law firm", "solicitors"),
        RegistryQueries(("solicitors", "law", "legal"), ("69102",)),  # Story 8.4
    ),
)

OPS = SegmentDef(
    key="ops_automation",
    name="Ops-Automation",
    industry_keywords=("distributor", "wholesale", "logistics", "freight", "light manufacturing"),
    size_range=SizeRange(10_000_000, 200_000_000, SizeUnit.USD_REVENUE),
    decision_maker_roles=(("COO", "VP Operations"), ("Owner", "CEO")),
    disqualifiers=(
        Disqualifier(
            "ops_fewer_than_20_staff",
            DisqualifierKind.FEWER_THAN,
            SizeClue.STAFF,
            20,
            "Fewer than 20 staff",
        ),
        Disqualifier(
            "ops_group_over_1000_staff",
            DisqualifierKind.MORE_THAN,
            SizeClue.GROUP_STAFF,
            1000,
            "Part of a group with more than 1,000 staff",
        ),
    ),
    signal_types=(
        SignalType(
            "ops_hiring_manual_entry",
            "Hiring data-entry, order-entry, AP/AR, or dispatch staff",
            SignalClass.EVENT,
            1,
            (Q,),
            (S.CAREERS_PAGE, S.ATS_BOARD, S.SIGNAL_INBOX),
            False,
            60,
            (
                "data entry",
                "order entry",
                "accounts payable",
                "accounts receivable",
                "AP/AR",
                "dispatch",
            ),
            (
                PreferredRoles(
                    ("accounts payable", "accounts receivable", "AP/AR"),
                    ("Controller", "CFO", "Finance Director"),
                ),
            ),
        ),
        SignalType(
            "ops_rekeying_post",
            "Post about manual re-keying or ERP pain",
            SignalClass.EVENT,
            1,
            (Q,),
            (S.SIGNAL_INBOX,),
            False,
            60,
        ),
        SignalType(
            "ops_expansion",
            "New facility, acquisition, or expansion",
            SignalClass.EVENT,
            2,
            (Q,),
            (S.NEWS_PAGES, S.SIGNAL_INBOX),
            False,
            35,
        ),
        SignalType(
            "ops_dynamics_gp",
            "Dynamics GP still in use",
            SignalClass.ATTRIBUTE,
            2,
            (Q,),
            (S.JOB_ADS, S.WEBSITE),
            False,
            30,
        ),
        SignalType(
            "ops_manual_ordering",
            'Ordering by PDF or order form with no portal; "advanced Excel" in job ads',
            SignalClass.ATTRIBUTE,
            3,
            (Q, A),
            (S.WEBSITE, S.JOB_ADS),
            False,
            15,
        ),
    ),
    discovery_queries=DiscoveryQueries(
        ("wholesale distributor", "logistics company", "freight company", "manufacturer"),
        RegistryQueries(  # Story 8.4: wholesale, freight, light manufacturing
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
    ),
)


def shipped() -> tuple[SegmentDef, ...]:
    return validate_segments(seed_yaml.parse(SEED_FILE.read_text(encoding="utf-8")))


def all_types() -> list[tuple[SegmentDef, SignalType]]:
    return [(segment, signal) for segment in shipped() for signal in segment.signal_types]


# The content ----------------------------------------------------------------------------------


def test_seed_sync_reads_this_file() -> None:
    assert SEED_DIR / SEGMENTS_FILE_NAME == SEED_FILE
    assert SEED_FILE.is_file()


def test_the_shipped_file_validates_into_exactly_the_design_notes_content() -> None:
    assert shipped() == (LAW, OPS)


def test_the_file_says_what_it_is() -> None:
    header = SEED_FILE.read_text(encoding="utf-8").split("\nsegments:", 1)[0]

    assert header.startswith("# ")
    assert "nextron seed sync" in header
    assert "subset of YAML" in header


def test_each_segment_has_every_part_the_ac_names() -> None:
    for segment in shipped():
        assert segment.key and segment.name
        assert segment.industry_keywords
        assert segment.size_range.minimum <= segment.size_range.maximum
        assert segment.decision_maker_roles and all(segment.decision_maker_roles)
        assert segment.disqualifiers
        assert segment.signal_types
        assert segment.discovery_queries.places


def test_law_is_5_to_50_lawyers_with_managing_partner_then_owner() -> None:
    law, _ = shipped()

    assert law.name == "Law-PrivateAI"
    assert law.size_range == SizeRange(5, 50, SizeUnit.LAWYERS)
    assert law.decision_maker_roles == (("Managing Partner",), ("Owner",))
    assert law.titles() == ("Managing Partner", "Owner")


def test_ops_is_10m_to_200m_revenue_with_coo_or_vp_ops_then_owner_or_ceo() -> None:
    _, ops = shipped()

    assert ops.name == "Ops-Automation"
    assert ops.size_range == SizeRange(10_000_000, 200_000_000, SizeUnit.USD_REVENUE)
    assert ops.decision_maker_roles == (("COO", "VP Operations"), ("Owner", "CEO"))
    # the finance roles are only for finance-role Events, and still decision-makers
    assert ops.titles() == (
        "COO",
        "VP Operations",
        "Owner",
        "CEO",
        "Controller",
        "CFO",
        "Finance Director",
    )


@pytest.mark.parametrize(
    ("segment_index", "key", "kind", "clue", "threshold", "description"),
    [
        (0, "law_fewer_than_5_lawyers", "fewer_than", "lawyers_firm_wide", 5, "Fewer than 5 lawyers"),
        (
            0,
            "law_office_of_large_firm",
            "more_than",
            "lawyers_firm_wide",
            50,
            "Office of a firm with more than 50 lawyers",
        ),
        (1, "ops_fewer_than_20_staff", "fewer_than", "staff", 20, "Fewer than 20 staff"),
        (
            1,
            "ops_group_over_1000_staff",
            "more_than",
            "group_staff",
            1000,
            "Part of a group with more than 1,000 staff",
        ),
    ],
)
def test_the_disqualifiers_carry_a_rule_key_a_description_and_a_size_clue(
    segment_index: int, key: str, kind: str, clue: str, threshold: int, description: str
) -> None:
    segment = shipped()[segment_index]
    [found] = [d for d in segment.disqualifiers if d.key == key]

    assert (found.kind, found.size_clue, found.threshold, found.description) == (
        kind,
        clue,
        threshold,
        description,
    )
    assert len(segment.disqualifiers) == 2


DEFAULT_WEIGHTS = {
    (SignalClass.EVENT, 1): 60,
    (SignalClass.EVENT, 2): 35,
    (SignalClass.ATTRIBUTE, 2): 30,
    (SignalClass.ATTRIBUTE, 3): 15,
}


def test_each_signal_type_has_its_default_weight_by_class_and_tier() -> None:
    for _, signal in all_types():
        assert signal.weight == DEFAULT_WEIGHTS[(signal.signal_class, signal.tier)], signal.key


def test_the_hiring_types_carry_story_2_11s_match_terms() -> None:
    hiring = {
        signal.key: signal.match_terms
        for _, signal in all_types()
        if {S.CAREERS_PAGE, S.ATS_BOARD} & set(signal.sources)
    }

    assert hiring == {
        "law_hiring_legal_ops": ("intake", "legal operations", "knowledge management", "innovation"),
        "ops_hiring_manual_entry": (
            "data entry",
            "order entry",
            "accounts payable",
            "accounts receivable",
            "AP/AR",
            "dispatch",
        ),
    }
    others = [s for _, s in all_types() if s.key not in hiring]
    assert all(s.match_terms == () and s.preferred_roles == () for s in others)


def test_only_the_ops_ap_ar_hiring_type_names_preferred_roles() -> None:
    preferred = {signal.key: signal.preferred_roles for _, signal in all_types() if signal.preferred_roles}

    assert preferred == {
        "ops_hiring_manual_entry": (
            PreferredRoles(
                ("accounts payable", "accounts receivable", "AP/AR"),
                ("Controller", "CFO", "Finance Director"),
            ),
        )
    }


def test_law_contact_form_only_is_score_only_with_absence_evidence() -> None:
    [found] = [s for _, s in all_types() if s.key == "law_contact_form_only"]

    assert (found.score_only, found.evidence) == (True, (EvidenceType.ABSENCE,))
    assert [s.key for _, s in all_types() if s.score_only] == ["law_contact_form_only"]


def test_no_signal_type_represents_aba_formal_opinion_512_or_the_sra_notice() -> None:
    for _, signal in all_types():
        for text in (signal.key, signal.name, *signal.match_terms):
            assert not represents_market_fact(text), text


def test_the_stored_form_round_trips_and_keys_fit_the_column() -> None:
    for segment in shipped():
        assert segment_from_text(definition_text(segment)) == segment
        assert len(segment.key) <= 31


# FR-1, row for row -------------------------------------------------------------------------------

# The PRD's FR-1 table, word for word: Segment, Signal, Class, Signal Tier, Evidence, Source.
FR1_ROWS = (
    (
        "Law",
        "Hiring intake, legal-ops, knowledge-management, or innovation staff",
        "Event",
        "1",
        "Quote",
        "Careers page, ATS board, Signal Inbox",
    ),
    ("Law", "Post about AI or client confidentiality", "Event", "1", "Quote", "Signal Inbox"),
    (
        "Law",
        "New office, merger, or practice-area expansion",
        "Event",
        "2",
        "Quote",
        "News or press pages, Signal Inbox",
    ),
    (
        "Law",
        "5–50 attorneys; document-heavy practice",
        "Attribute",
        "3",
        "Count; Quote",
        "Team and practice pages",
    ),
    (
        "Law",
        "Contact form only (no chat, booking, or after-hours line)",
        "Attribute, score-only",
        "3",
        "Absence",
        "Website",
    ),
    (
        "Ops",
        "Hiring data-entry, order-entry, AP/AR, or dispatch staff",
        "Event",
        "1",
        "Quote",
        "Careers page, ATS board, Signal Inbox",
    ),
    ("Ops", "Post about manual re-keying or ERP pain", "Event", "1", "Quote", "Signal Inbox"),
    (
        "Ops",
        "New facility, acquisition, or expansion",
        "Event",
        "2",
        "Quote",
        "News or press pages, Signal Inbox",
    ),
    ("Ops", "Dynamics GP still in use", "Attribute", "2", "Quote", "Job ads, website"),
    (
        "Ops",
        'Ordering by PDF or order form with no portal; "advanced Excel" in job ads',
        "Attribute",
        "3",
        "Quote or Absence",
        "Website, job ads",
    ),
)

# The fixed PRD-word to code mapping (decision 9 for the sources).
SEGMENT_WORDS = {"Law": "law_private_ai", "Ops": "ops_automation"}
CLASS_WORDS = {
    "Event": (SignalClass.EVENT, False),
    "Attribute": (SignalClass.ATTRIBUTE, False),
    "Attribute, score-only": (SignalClass.ATTRIBUTE, True),
}
EVIDENCE_WORDS = {"quote": Q, "absence": A, "count": C}
SOURCE_WORDS = {
    "careers page": (S.CAREERS_PAGE,),
    "ats board": (S.ATS_BOARD,),
    "signal inbox": (S.SIGNAL_INBOX,),
    "news or press pages": (S.NEWS_PAGES,),
    "team and practice pages": (S.TEAM_PAGES, S.PRACTICE_PAGES),
    "website": (S.WEBSITE,),
    "job ads": (S.JOB_ADS,),
}

type Coded = tuple[str, str, SignalClass, int, tuple[EvidenceType, ...], tuple[SignalSource, ...], bool]


def coded(row: tuple[str, ...]) -> Coded:
    """One FR-1 row in codes: segment key, name, class, tier, evidence, sources, score-only."""
    segment, name, class_words, tier, evidence, sources = row
    signal_class, score_only = CLASS_WORDS[class_words]
    return (
        SEGMENT_WORDS[segment],
        name,
        signal_class,
        int(tier),
        tuple(EVIDENCE_WORDS[word.strip().lower()] for word in re.split(r";| or ", evidence)),
        tuple(
            source
            for words in sources.split(", ")
            for source in SOURCE_WORDS[words.strip().lower()]
        ),
        score_only,
    )


def seeded(segment: SegmentDef, signal: SignalType) -> Coded:
    return (
        segment.key,
        signal.name,
        signal.signal_class,
        signal.tier,
        signal.evidence,
        signal.sources,
        signal.score_only,
    )


def test_the_seeded_types_match_the_fr1_transcription_row_for_row() -> None:
    types = all_types()

    assert len(types) == len(FR1_ROWS) == 10
    for (segment, signal), row in zip(types, FR1_ROWS, strict=True):
        assert seeded(segment, signal) == coded(row), signal.key


def test_the_mapping_reads_each_prd_word() -> None:
    assert coded(FR1_ROWS[3])[4] == (C, Q)
    assert coded(FR1_ROWS[9])[4] == (Q, A)
    assert coded(FR1_ROWS[3])[5] == (S.TEAM_PAGES, S.PRACTICE_PAGES)
    assert coded(FR1_ROWS[4])[2:] == (SignalClass.ATTRIBUTE, 3, (A,), (S.WEBSITE,), True)
    assert coded(FR1_ROWS[8])[5] == (S.JOB_ADS, S.WEBSITE)


def fr1_table(text: str) -> tuple[tuple[str, ...], ...]:
    """The rows of the table under ``#### FR-1: Segment definitions``, each cell stripped."""
    section = text.split("#### FR-1: Segment definitions", 1)[1].split("\n#### ", 1)[0]
    lines = [line.strip() for line in section.splitlines()]
    start = lines.index(FR1_HEADER)
    rows = []
    for line in lines[start + 2 :]:  # past the header and its |---| rule
        if not line.startswith("|"):
            break
        rows.append(tuple(cell.strip() for cell in line.strip("|").split("|")))
    return tuple(rows)


def test_the_transcription_is_the_prds_own_fr1_table() -> None:
    if not PRD.is_file():
        pytest.skip(f"the PRD is not at {PRD}")

    assert fr1_table(PRD.read_text(encoding="utf-8")) == FR1_ROWS


def test_the_prd_parser_reads_exactly_the_fr1_table() -> None:
    text = (
        "#### FR-0: Before\n"
        f"{FR1_HEADER}\n"
        "| Law | Not this table | Event | 1 | Quote | Website |\n"
        "#### FR-1: Segment definitions\n"
        "Some text.\n"
        "\n"
        f"  {FR1_HEADER}\n"
        "  |---|---|---|---|---|---|\n"
        '  | Law | A, "b"; c | Attribute, score-only | 3 | Count; Quote | Team and practice pages |\n'
        "| Ops | Second |Event|2| Quote or Absence |Website, job ads|\n"
        "\n"
        "| Law | After a blank line | Event | 1 | Quote | Website |\n"
        "#### FR-2: Market rules\n"
        "| Law | Later | Event | 1 | Quote | Website |\n"
    )

    assert fr1_table(text) == (
        ("Law", 'A, "b"; c', "Attribute, score-only", "3", "Count; Quote", "Team and practice pages"),
        ("Ops", "Second", "Event", "2", "Quote or Absence", "Website, job ads"),
    )
