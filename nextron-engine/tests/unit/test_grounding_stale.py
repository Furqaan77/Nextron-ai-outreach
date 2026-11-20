"""``nextron.domain.grounding.is_stale`` (AD-7, Story 4.20): the one staleness test, with a fixed
``now``. Each reason alone, the order they are found in, and the bad inputs."""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import pytest

from nextron.domain.grounding import (
    STALE_TEXTS,
    CitedEvent,
    CitedEvidence,
    CitedFact,
    StaleInput,
    StaleReason,
    is_stale,
    stale_text,
)

NOW: Final = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
LIVE: Final = CitedEvidence("ev1", None, None)
FACT: Final = CitedFact("legal_ai", 1, 1, True)
EVENT: Final = CitedEvent("sig1", "event", 1, date(2026, 11, 10), NOW - timedelta(days=8), None)


def revision(**changes: Any) -> StaleInput:
    fields: dict[str, Any] = {
        "evidence": (LIVE,),
        "facts": (FACT,),
        "cited_signal_id": "sig1",
        "event": EVENT,
    }
    return StaleInput(**(fields | changes))


def test_a_revision_whose_inputs_stand_is_not_stale() -> None:
    assert is_stale(revision(), NOW) is None
    assert is_stale(revision(cited_signal_id=None, event=None), NOW) is None  # a follow-up


@pytest.mark.parametrize(
    "evidence",
    [
        CitedEvidence("ev1", "ev2", None),  # superseded
        CitedEvidence("ev1", None, NOW),  # retracted
        CitedEvidence("ev1", None, None, missing=True),  # gone
    ],
)
def test_a_changed_cited_evidence_row_is_evidence_changed(evidence: CitedEvidence) -> None:
    assert is_stale(revision(evidence=(LIVE, evidence)), NOW) is StaleReason.EVIDENCE_CHANGED


def test_a_cited_fact_version_that_isnt_current_is_fact_changed() -> None:
    edited = CitedFact("legal_ai", 1, 2, False)
    retracted = CitedFact("legal_ai", 1, None, False)

    assert is_stale(revision(facts=(edited,)), NOW) is StaleReason.FACT_CHANGED
    assert is_stale(revision(facts=(retracted,)), NOW) is StaleReason.FACT_CHANGED


def test_a_current_fact_no_longer_allowed_is_fact_not_allowed() -> None:
    narrowed = CitedFact("legal_ai", 1, 1, False)

    assert is_stale(revision(facts=(narrowed,)), NOW) is StaleReason.FACT_NOT_ALLOWED


@pytest.mark.parametrize(
    "event",
    [
        None,  # missing
        CitedEvent("sig1", "event", 1, date(2026, 11, 10), NOW, NOW),  # retracted
        CitedEvent("sig1", "event", 1, date(2026, 8, 1), NOW - timedelta(days=200), None),  # old
        CitedEvent("sig1", "attribute", 1, None, NOW, None),  # not an Event
    ],
)
def test_a_cited_signal_that_isnt_a_live_event_is_event_not_live(
    event: CitedEvent | None,
) -> None:
    assert is_stale(revision(event=event), NOW) is StaleReason.EVENT_NOT_LIVE


def test_the_first_reason_found_wins_in_the_specified_order() -> None:
    every = revision(
        evidence=(CitedEvidence("ev1", "ev2", None),),
        facts=(CitedFact("old", 1, 2, True), CitedFact("narrow", 1, 1, False)),
        event=None,
    )

    assert is_stale(every, NOW) is StaleReason.EVIDENCE_CHANGED
    assert is_stale(revision(facts=every.facts, event=None), NOW) is StaleReason.FACT_CHANGED
    assert is_stale(
        revision(facts=(CitedFact("narrow", 1, 1, False),), event=None), NOW
    ) is StaleReason.FACT_NOT_ALLOWED


def test_bad_inputs_raise() -> None:
    with pytest.raises(TypeError, match="StaleInput"):
        is_stale("revision", NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="aware"):
        is_stale(revision(), datetime(2026, 11, 18))  # noqa: DTZ001


def test_every_reason_has_a_page_text() -> None:
    assert set(STALE_TEXTS) == set(StaleReason)
    assert stale_text(StaleReason.EVIDENCE_CHANGED) == "Evidence changed"
