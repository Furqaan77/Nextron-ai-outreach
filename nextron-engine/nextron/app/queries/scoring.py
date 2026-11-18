"""Read API of the ``scoring`` service (AD-3).

Import rule: ``nextron.domain`` and ``nextron.db.repos.scoring`` only. All read through the
current unit of work, read-only ones included.

Story 2.15 adds the score group of ``leads``:

- ``stored(lead_id) -> StoredScore | None``: the Lead's score as ``StoredScore``, its factors
  decoded (``nextron.domain.scoring.factors_from_text``) and its Disqualified rule's Evidence ids
  read; ``None`` for a Lead never scored (no ``tier_computed_at``), an unknown Lead and a text
  that isn't a ULID. A stored text the domain can no longer read gives no factors (or no
  Evidence ids). ``StoredScore.disqualified`` says whether it holds a Disqualified rule.
- ``retier_candidates()``: the ids of the Leads the daily ``recompute_tiers`` re-tiers (a stored
  ``tier``, not merged, not ``lost`` or ``disqualified``), by id.

Story 4.10 adds ``ready_candidates()``: ``(lead_id, tier, lead_score)`` of every Lead with a
stored Tier and Lead Score that isn't merged and whose stage is ``new``, ``queued`` or
``contacted``, by id (the "Ready to draft" list filters and orders them further).
"""

from dataclasses import dataclass
from datetime import datetime

from nextron.db.repos.scoring import scores as scores_repo
from nextron.domain.ids import is_ulid
from nextron.domain.scoring import Factor, Tier, evidence_from_text, factors_from_text

__all__ = ["StoredScore", "ready_candidates", "retier_candidates", "stored"]


@dataclass(frozen=True, slots=True)
class StoredScore:
    """A Lead's stored score group: the scores and Tier (``None`` for a Disqualified Lead),
    when it was computed, the Segment version used, the factors, and a Disqualified Lead's
    rule and Evidence ids."""

    lead_id: str
    fit: int | None
    signal: int | None
    lead: int | None
    tier: Tier | None
    computed_at: datetime
    segment_version: int | None
    fit_factors: tuple[Factor, ...]
    signal_factors: tuple[Factor, ...]
    disqualified_rule: str | None
    disqualified_evidence: tuple[str, ...]

    @property
    def disqualified(self) -> bool:
        """Whether a Segment disqualifier matched."""
        return self.disqualified_rule is not None


def _tier(text: str | None) -> Tier | None:
    try:
        return None if text is None else Tier(text)
    except ValueError:
        return None


def stored(lead_id: str) -> StoredScore | None:
    """The Lead's stored score, or ``None``; see the module docstring."""
    row = scores_repo.stored_row(lead_id) if is_ulid(lead_id) else None
    if row is None or row.tier_computed_at is None:
        return None
    fit_factors: tuple[Factor, ...] = ()
    signal_factors: tuple[Factor, ...] = ()
    if row.score_factors is not None:
        try:
            fit_factors, signal_factors = factors_from_text(row.score_factors)
        except ValueError:
            pass
    evidence: tuple[str, ...] = ()
    if row.disqualified_evidence is not None:
        try:
            evidence = evidence_from_text(row.disqualified_evidence)
        except ValueError:
            pass
    return StoredScore(
        lead_id=row.lead_id,
        fit=row.fit_score,
        signal=row.signal_score,
        lead=row.lead_score,
        tier=_tier(row.tier),
        computed_at=row.tier_computed_at,
        segment_version=row.score_segment_version,
        fit_factors=fit_factors,
        signal_factors=signal_factors,
        disqualified_rule=row.disqualified_rule,
        disqualified_evidence=evidence,
    )


def retier_candidates() -> tuple[str, ...]:
    """The Leads the daily re-tiering visits, by id."""
    return scores_repo.retier_candidates()


def ready_candidates() -> tuple[tuple[str, str, int], ...]:
    """The Leads the "Ready to draft" list may show, by id; see the module docstring."""
    return scores_repo.ready_candidates()
