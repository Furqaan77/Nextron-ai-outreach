"""``nextron.domain.segments.signal_catalogue`` (Story 2.8): the text of the ``signal_types``
block an extraction call sends, from the shipped ``seed/segments.yaml``."""

import dataclasses

import pytest

from nextron.domain import seed_yaml
from nextron.domain.segments import (
    EvidenceType,
    SegmentDef,
    signal_catalogue,
    validate_segments,
)
from tests.ai_eval.extraction import SEGMENTS_FILE


@pytest.fixture(scope="module")
def segments() -> tuple[SegmentDef, ...]:
    return validate_segments(seed_yaml.parse(SEGMENTS_FILE.read_text(encoding="utf-8")))


def test_the_shipped_catalogue(segments: tuple[SegmentDef, ...]) -> None:
    assert signal_catalogue(segments) == "\n".join(
        [
            "segment law_private_ai: Law-PrivateAI",
            "- law_hiring_legal_ops: Hiring intake, legal-ops, knowledge-management, or "
            "innovation staff",
            "- law_ai_confidentiality_post: Post about AI or client confidentiality",
            "- law_expansion: New office, merger, or practice-area expansion",
            "- law_size_document_heavy: 5–50 attorneys; document-heavy practice",
            "segment ops_automation: Ops-Automation",
            "- ops_hiring_manual_entry: Hiring data-entry, order-entry, AP/AR, or dispatch staff",
            "- ops_rekeying_post: Post about manual re-keying or ERP pain",
            "- ops_expansion: New facility, acquisition, or expansion",
            "- ops_dynamics_gp: Dynamics GP still in use",
            "- ops_manual_ordering: Ordering by PDF or order form with no portal; "
            '"advanced Excel" in job ads',
        ]
    )


def test_a_rule_only_signal_type_is_left_out(segments: tuple[SegmentDef, ...]) -> None:
    [law, _] = segments
    rule_only = [t.key for t in law.signal_types if EvidenceType.QUOTE not in t.evidence]

    assert rule_only == ["law_contact_form_only"]
    assert "law_contact_form_only" not in signal_catalogue(segments)


def test_one_segment_lists_only_its_own_types(segments: tuple[SegmentDef, ...]) -> None:
    [law, ops] = segments

    text = signal_catalogue([ops])

    assert text.splitlines()[0] == "segment ops_automation: Ops-Automation"
    assert "law_" not in text
    assert signal_catalogue([law, ops]) == signal_catalogue(segments)


def test_a_segment_with_no_quotable_type_is_its_heading_only(
    segments: tuple[SegmentDef, ...],
) -> None:
    [law, _] = segments
    bare = dataclasses.replace(
        law, signal_types=tuple(t for t in law.signal_types if t.key == "law_contact_form_only")
    )

    assert signal_catalogue([bare]) == "segment law_private_ai: Law-PrivateAI"


def test_no_segments_give_an_empty_text_and_anything_else_is_refused() -> None:
    assert signal_catalogue([]) == ""
    with pytest.raises(TypeError):
        signal_catalogue(["law_private_ai"])  # type: ignore[list-item]
