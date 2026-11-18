"""The Pipeline board's reads (Story 9.7, FR-46, UX-DR18): the Leads that aren't merged, by
Pipeline Stage, read-only.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``), so it works in a read-only unit of work. It writes nothing and
checks nothing; ``nextron.app.queries.pipeline.board`` validates its arguments.

It reads the ``leads`` identity columns (``id``, ``name``, ``segment_id``, ``merged_into``,
``created_at``) next to the pipeline group's ``pipeline_stage``, ``contacts.market_id`` and the
``events`` rows of ``leads``, all read-only, so each group's count and page stay one statement.

**Filters** (``BoardFilter``; a ``None`` field is unset): ``segment_id`` keeps the Leads in that
Segment; ``market_id`` keeps the Leads with at least one Contact whose ``market_id`` is that
Market (``EXISTS`` on ``contacts``; Leads have no Market); ``entered_from`` and
``entered_before`` (aware UTC, the second exclusive) keep the Leads whose **entry time** falls in
``[entered_from, entered_before)``. A Lead's entry time is the latest ``events.at`` with
``entity = 'leads'``, ``entity_id`` the Lead and ``to_status`` its current stage (a correlated
``MAX``); a Lead with no such row has none, so either date bound leaves it out. Merged Leads
(``merged_into`` set) are always left out.

- ``stage_counts(where)``: each stage's count of matching Leads, in one ``GROUP BY`` (a stage
  with none is absent);
- ``stage_rows(stage, where, *, limit, offset)``: at most ``limit`` matching Leads at ``stage``,
  newest first (``created_at DESC, id DESC``), after skipping ``offset``, each a ``BoardLead``
  with its entry time (``None`` without one).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, ScalarSelect, exists, func, select

from nextron.db.models.events import EventRecord
from nextron.db.models.leads import LeadRecord
from nextron.db.models.research import ContactRecord
from nextron.db.uow import current

LEADS_ENTITY = "leads"


@dataclass(frozen=True, slots=True)
class BoardFilter:
    """The board's filters, each ``None`` when unset."""

    segment_id: str | None = None
    market_id: str | None = None
    entered_from: datetime | None = None
    entered_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class BoardLead:
    """One Lead on the board: its id, name, Segment id, stored stage and entry time."""

    id: str
    name: str
    segment_id: str
    pipeline_stage: str
    entered_at: datetime | None


def _entered_at() -> ScalarSelect[datetime]:
    """The Lead's entry time into its current stage (correlated to ``leads``)."""
    return (
        select(func.max(EventRecord.at))
        .where(
            EventRecord.entity == LEADS_ENTITY,
            EventRecord.entity_id == LeadRecord.id,
            EventRecord.to_status == LeadRecord.pipeline_stage,
        )
        .correlate(LeadRecord)
        .scalar_subquery()
    )


def _where(where: BoardFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [LeadRecord.merged_into.is_(None)]
    if where.segment_id is not None:
        conditions.append(LeadRecord.segment_id == where.segment_id)
    if where.market_id is not None:
        conditions.append(
            exists()
            .where(
                ContactRecord.lead_id == LeadRecord.id,
                ContactRecord.market_id == where.market_id,
            )
            .correlate(LeadRecord)
        )
    if where.entered_from is not None or where.entered_before is not None:
        entered = _entered_at()
        if where.entered_from is not None:
            conditions.append(entered >= where.entered_from)
        if where.entered_before is not None:
            conditions.append(entered < where.entered_before)
    return conditions


def stage_counts(where: BoardFilter) -> dict[str, int]:
    """How many matching Leads each stored stage holds; a stage with none is absent."""
    statement = (
        select(LeadRecord.pipeline_stage, func.count())
        .where(*_where(where))
        .group_by(LeadRecord.pipeline_stage)
    )
    return {stage: int(count) for stage, count in current().session.execute(statement)}


def stage_rows(
    stage: str, where: BoardFilter, *, limit: int, offset: int
) -> tuple[BoardLead, ...]:
    """At most ``limit`` matching Leads at ``stage``, newest first, after skipping ``offset``."""
    statement = (
        select(
            LeadRecord.id,
            LeadRecord.name,
            LeadRecord.segment_id,
            LeadRecord.pipeline_stage,
            _entered_at().label("entered_at"),
        )
        .where(LeadRecord.pipeline_stage == stage, *_where(where))
        .order_by(LeadRecord.created_at.desc(), LeadRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return tuple(BoardLead(*row) for row in current().session.execute(statement))
