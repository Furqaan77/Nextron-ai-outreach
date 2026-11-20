"""Whether a Draft revision has gone stale (FR-21, FR-22, AD-7; Story 4.20). Standard library
only, plus ``nextron.domain``.

``is_stale(revision, now) -> StaleReason | None`` is **the only staleness test** (AD-7; a
contract test holds every other module to it). Its input, ``StaleInput``, is what the revision
cites, as the caller read it now:

- ``evidence``: one ``CitedEvidence(id, superseded_by, retracted_at, missing=False)`` per cited
  Evidence id (``missing`` when the row is gone);
- ``facts``: one ``CitedFact(fact_id, version, current_version, allowed)`` per cited ``(fact_id,
  version)``: ``current_version`` is the fact's current version (``None`` when it has none, a
  retracted fact), ``allowed`` whether that cited version is among the facts allowed for the
  Draft's Segment x Market (``queries.playbook.allowed_facts``);
- ``cited_signal_id`` and ``event``: the Signal the revision opens with (Step 1) and, when the
  revision cites one, that Signal as ``CitedEvent(signal_id, signal_class, tier, event_date,
  first_seen_at, retracted_at)`` (``None`` when it is gone).

The reasons, the first found in this order:

1. ``evidence_changed``: a cited Evidence row is superseded, retracted or missing;
2. ``fact_changed``: a cited fact version is not its fact's current version;
3. ``fact_not_allowed``: a cited fact version is no longer allowed for the Segment x Market;
4. ``event_not_live``: the cited Signal is missing, retracted, not an Event, or not live at
   ``now`` (``signals.is_live`` with ``signals.age_days``).

``None``: nothing it cites changed. ``STALE_TEXTS`` gives the Lead page's text for each reason
(``stale_text(reason)``).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from nextron.domain.segments import SignalClass
from nextron.domain.signals import age_days, is_live


class StaleReason(StrEnum):
    """Why a revision is stale; see the module docstring."""

    EVIDENCE_CHANGED = "evidence_changed"
    FACT_CHANGED = "fact_changed"
    FACT_NOT_ALLOWED = "fact_not_allowed"
    EVENT_NOT_LIVE = "event_not_live"


STALE_TEXTS: Final[Mapping[StaleReason, str]] = {
    StaleReason.EVIDENCE_CHANGED: "Evidence changed",
    StaleReason.FACT_CHANGED: "a cited fact was edited or retracted",
    StaleReason.FACT_NOT_ALLOWED: "a cited fact is no longer allowed",
    StaleReason.EVENT_NOT_LIVE: "the Event it opens with is no longer live",
}


def stale_text(reason: StaleReason) -> str:
    """The Lead page's text for ``reason``."""
    return STALE_TEXTS[StaleReason(reason)]


@dataclass(frozen=True, slots=True)
class CitedEvidence:
    """One cited Evidence row as it stands now."""

    id: str
    superseded_by: str | None
    retracted_at: datetime | None
    missing: bool = False

    @property
    def changed(self) -> bool:
        """Whether the row is superseded, retracted or gone."""
        return self.missing or self.superseded_by is not None or self.retracted_at is not None


@dataclass(frozen=True, slots=True)
class CitedFact:
    """One cited ``(fact_id, version)`` as it stands now."""

    fact_id: str
    version: int
    current_version: int | None
    allowed: bool


@dataclass(frozen=True, slots=True)
class CitedEvent:
    """The cited Signal as it stands now."""

    signal_id: str
    signal_class: str
    tier: int
    event_date: date | None
    first_seen_at: datetime
    retracted_at: datetime | None


@dataclass(frozen=True, slots=True)
class StaleInput:
    """What a revision cites, read now; see the module docstring."""

    evidence: tuple[CitedEvidence, ...]
    facts: tuple[CitedFact, ...]
    cited_signal_id: str | None
    event: CitedEvent | None


def _event_live(event: CitedEvent | None, now: datetime) -> bool:
    if event is None or event.retracted_at is not None:
        return False
    if event.signal_class != SignalClass.EVENT:
        return False
    return is_live(
        SignalClass.EVENT, event.tier, age_days(event.event_date, event.first_seen_at, now)
    )


def is_stale(revision: StaleInput, now: datetime) -> StaleReason | None:
    """The first reason ``revision`` is stale at ``now``, or ``None``; see the module
    docstring."""
    if not isinstance(revision, StaleInput):
        raise TypeError("is_stale takes a StaleInput")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise TypeError("now must be an aware datetime")
    if any(found.changed for found in revision.evidence):
        return StaleReason.EVIDENCE_CHANGED
    if any(found.current_version != found.version for found in revision.facts):
        return StaleReason.FACT_CHANGED
    if not all(found.allowed for found in revision.facts):
        return StaleReason.FACT_NOT_ALLOWED
    if revision.cited_signal_id is not None and not _event_live(revision.event, now):
        return StaleReason.EVENT_NOT_LIVE
    return None
