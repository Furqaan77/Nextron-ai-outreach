"""The Grounding set's deterministic fixtures (FR-21, AD-12; Story 4.13): for every
``tests/ai_sets/grounding/*.json`` case, ``grounding_rules.check`` over its input returns exactly
the case's ``expected.rules``, and no flag on a ``clean_control``.

Each case's ``RulesContext`` is built from the shipped seeds and the case: the Step's brief from
``seed/sequences.yaml``; each Signal's class, score-only flag and weight from
``seed/segments.yaml`` (Tier from the case); the strongest live Event by
``signals.strongest_event`` at a fixed now (2026-11-19), an Event's ``observed_on`` its event
date and first-seen day; the allowed facts and the revision's fact set the case's ``facts``; the
revision's Evidence set the case's Evidence; the contact details the case's ``settings`` values.
"""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from nextron.domain import seed_yaml
from nextron.domain.grounding_rules import (
    RULE_IDS,
    Claim,
    EarlierStep,
    LeadSignal,
    RevisionInput,
    RulesContext,
    check,
)
from nextron.domain.segments import SegmentDef, SignalClass, validate_segments
from nextron.domain.sequences import StepBrief, validate_sequences
from nextron.domain.signals import EventSignal, strongest_event
from tests.contracts.support import ENGINE_ROOT

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
CASES = sorted((ENGINE_ROOT / "tests" / "ai_sets" / "grounding").glob("*.json"))
SEGMENTS: Mapping[str, SegmentDef] = {
    segment.key: segment
    for segment in validate_segments(
        seed_yaml.parse((ENGINE_ROOT / "seed" / "segments.yaml").read_text(encoding="utf-8"))
    )
}
[SEQUENCE] = validate_sequences(
    seed_yaml.parse((ENGINE_ROOT / "seed" / "sequences.yaml").read_text(encoding="utf-8"))
)


def claims_of(rows: list[dict[str, Any]] | None) -> tuple[Claim, ...] | None:
    if rows is None:
        return None
    return tuple(
        Claim(
            row["text"],
            row["evidence_id"],
            None if row["fact"] is None else (row["fact"]["fact_id"], row["fact"]["version"]),
        )
        for row in rows
    )


def built(case: dict[str, Any]) -> tuple[RevisionInput, StepBrief, RulesContext]:
    given = case["input"]
    types = {kind.key: kind for kind in SEGMENTS[given["segment"]].signal_types}
    signals = tuple(
        LeadSignal(
            id=row["id"],
            signal_type=row["signal_type"],
            signal_class=types[row["signal_type"]].signal_class,
            tier=row["tier"],
            score_only=types[row["signal_type"]].score_only,
            evidence_ids=(row["evidence_id"],),
        )
        for row in given["signals"]
    )
    events = []
    for row in given["signals"]:
        signal_type = types[row["signal_type"]]
        if signal_type.signal_class is not SignalClass.EVENT or row["observed_on"] is None:
            continue
        observed = date.fromisoformat(row["observed_on"])
        events.append(
            EventSignal(
                row["id"],
                row["signal_type"],
                row["tier"],
                signal_type.weight,
                observed,
                datetime(observed.year, observed.month, observed.day, tzinfo=UTC),
            )
        )
    strongest = strongest_event(events, NOW)
    facts = tuple((row["fact_id"], row["version"]) for row in given["facts"])
    revision = RevisionInput(
        subject=given["draft"]["subject"],
        body=given["draft"]["body"],
        claims=claims_of(given["claims"]),
        cited_signal_id=given["cited_signal_id"],
        evidence_ids=tuple(row["id"] for row in given["evidence"]),
        fact_versions=facts,
    )
    context = RulesContext(
        step=given["step"],
        contact_details=tuple(given["settings"].values()),
        evidence_kinds={row["id"]: row["kind"] for row in given["evidence"]},
        signals=signals,
        strongest_event_id=None if strongest is None else strongest.signal_id,
        allowed_facts=frozenset(facts),
        earlier_steps=tuple(
            EarlierStep(
                step=row["step"],
                fact_ids=frozenset(
                    claim["fact"]["fact_id"] for claim in row["claims"] if claim["fact"] is not None
                ),
                cited_signal_id=row["cited_signal_id"],
            )
            for row in given["earlier_steps"]
        ),
    )
    return revision, SEQUENCE.steps[given["step"] - 1], context


def test_the_grounding_set_is_there() -> None:
    assert len(CASES) >= 30


@pytest.mark.parametrize("path", CASES, ids=[path.stem for path in CASES])
def test_each_fixture_yields_exactly_its_expected_rules(path: Path) -> None:
    case = json.loads(path.read_text(encoding="utf-8"))
    result = check(*built(case))

    fired = {flag.rule for flag in result.flags}
    assert fired == set(case["expected"]["rules"])
    assert set(case["expected"]["rules"]) <= set(RULE_IDS)
    if case["expected"]["kind"] == "clean_control":
        assert result.flags == ()
    draft = case["input"]["draft"]
    for flag in result.flags:  # every span is the subject's or the body's text
        assert flag.span in draft["subject"] or flag.span in draft["body"]
