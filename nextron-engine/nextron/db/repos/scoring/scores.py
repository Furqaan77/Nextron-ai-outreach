"""The ``leads`` score group's repository (FR-17, AD-3, AD-7; Story 2.15): the only code that
writes the ``scoring``-owned columns of ``leads``: ``fit_score``, ``signal_score``,
``lead_score``, ``tier``, ``tier_computed_at``, ``score_segment_version``, ``score_factors``,
``disqualified_rule`` and ``disqualified_evidence``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores and returns what it is given and checks nothing;
``nextron.app.scoring`` does.

- ``StoredScoreRow``: the score group of one row, as stored (the factors and the Evidence ids
  as their JSON text).
- ``stored_row(lead_id)``: the row's score group, or ``None`` for an unknown Lead.
- ``write_score(lead_id, *, fit, signal, lead, tier, computed_at, segment_version,
  factors)``: a scored Lead (the disqualifier columns cleared).
- ``write_disqualified(lead_id, *, rule, evidence, computed_at, segment_version)``: a
  Disqualified Lead (the scores, the Tier and the factors cleared).
- ``write_tier(lead_id, *, signal, lead, tier, computed_at, factors)``: a re-tiered Lead (the
  Fit Score and the Segment version kept).
- ``retier_candidates()``: the ids of the Leads with a stored ``tier`` that aren't merged and
  aren't ``lost`` or ``disqualified``, by id. It reads ``merged_into`` (discovery's) and
  ``pipeline_stage`` (pipeline's) read-only.
- ``segment_leads(segment_id)`` (Story 11.5): the ids of the Segment's Leads that aren't merged
  and aren't ``lost`` or ``disqualified``, by id (read-only, like ``retier_candidates``): the
  Leads "Re-score with the current definition" covers for a whole Segment.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.leads import LeadRecord
from nextron.db.uow import current
from nextron.domain.pipeline_stage import CLOSED_STAGES

CLOSED: Final = tuple(str(stage) for stage in CLOSED_STAGES)


@dataclass(frozen=True, slots=True)
class StoredScoreRow:
    """The score group of one ``leads`` row, as stored."""

    lead_id: str
    fit_score: int | None
    signal_score: int | None
    lead_score: int | None
    tier: str | None
    tier_computed_at: datetime | None
    score_segment_version: int | None
    score_factors: str | None
    disqualified_rule: str | None
    disqualified_evidence: str | None


_COLUMNS = (
    LeadRecord.id,
    LeadRecord.fit_score,
    LeadRecord.signal_score,
    LeadRecord.lead_score,
    LeadRecord.tier,
    LeadRecord.tier_computed_at,
    LeadRecord.score_segment_version,
    LeadRecord.score_factors,
    LeadRecord.disqualified_rule,
    LeadRecord.disqualified_evidence,
)


def stored_row(lead_id: str) -> StoredScoreRow | None:
    """The Lead's score group, or ``None`` when the Lead has no row."""
    row = (
        current().session.execute(select(*_COLUMNS).where(LeadRecord.id == lead_id))
    ).one_or_none()
    return None if row is None else StoredScoreRow(*row)


def _write(lead_id: str, **values: object) -> None:
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(**values)
    )


def write_score(
    lead_id: str,
    *,
    fit: int,
    signal: int,
    lead: int,
    tier: str,
    computed_at: datetime,
    segment_version: int,
    factors: str,
) -> None:
    """Store a scored Lead; the disqualifier columns are cleared."""
    _write(
        lead_id,
        fit_score=fit,
        signal_score=signal,
        lead_score=lead,
        tier=tier,
        tier_computed_at=computed_at,
        score_segment_version=segment_version,
        score_factors=factors,
        disqualified_rule=None,
        disqualified_evidence=None,
    )


def write_disqualified(
    lead_id: str, *, rule: str, evidence: str, computed_at: datetime, segment_version: int
) -> None:
    """Store a Disqualified Lead; the scores, the Tier and the factors are cleared."""
    _write(
        lead_id,
        fit_score=None,
        signal_score=None,
        lead_score=None,
        tier=None,
        tier_computed_at=computed_at,
        score_segment_version=segment_version,
        score_factors=None,
        disqualified_rule=rule,
        disqualified_evidence=evidence,
    )


def write_tier(
    lead_id: str, *, signal: int, lead: int, tier: str, computed_at: datetime, factors: str
) -> None:
    """Store a re-tiered Lead; the Fit Score and the Segment version are kept."""
    _write(
        lead_id,
        signal_score=signal,
        lead_score=lead,
        tier=tier,
        tier_computed_at=computed_at,
        score_factors=factors,
    )


def segment_leads(segment_id: str) -> tuple[str, ...]:
    """The Segment's open, unmerged Leads, by id."""
    statement = (
        select(LeadRecord.id)
        .where(
            LeadRecord.segment_id == segment_id,  # discovery's column, read only
            LeadRecord.merged_into.is_(None),  # discovery's column, read only
            LeadRecord.pipeline_stage.not_in(CLOSED),  # pipeline's column, read only
        )
        .order_by(LeadRecord.id)
    )
    return tuple(current().session.execute(statement).scalars())


def retier_candidates() -> tuple[str, ...]:
    """The Leads a daily re-tiering visits, by id."""
    statement = (
        select(LeadRecord.id)
        .where(
            LeadRecord.tier.is_not(None),
            LeadRecord.merged_into.is_(None),  # discovery's column, read only
            LeadRecord.pipeline_stage.not_in(CLOSED),  # pipeline's column, read only
        )
        .order_by(LeadRecord.id)
    )
    return tuple(current().session.execute(statement).scalars())
