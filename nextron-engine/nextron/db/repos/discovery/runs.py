"""The ``discovery_runs`` repository (Story 8.3, FR-5, AD-3): the only code that writes the table.

Every function runs one explicit Core statement through the current unit of work's session, as
``nextron.db.repos.discovery.leads`` does; it checks nothing (``nextron.app.discovery`` does).

- ``StoredRun``: one row;
- ``insert(...) -> id``: a new run, every count 0 (Story 8.10: with ``scheduled_for`` and
  ``ready_pool``, both ``None`` by default);
- ``count(run_id, outcome)``: adds 1 to ``seen`` and, unless ``outcome`` is ``None``, 1 to the
  outcome's count column (a ``nextron.domain.place_discovery.Outcome`` value), in one
  statement;
- ``add_outcome(run_id, outcome)`` (Story 8.4): adds 1 to the outcome's count alone;
- ``finish(run_id, status, at)``: sets ``status`` and ``finished_at``;
- ``fail_running(source, at) -> int``: ends every ``running`` row of ``source`` ``failed`` at
  ``at``; the count (Story 8.4: each source's runs recover only their own rows);
- ``get(run_id)``: the row, or ``None``;
- ``recent(limit)``: the newest ``limit`` runs (by ``started_at``, then ``id``, descending);
- ``last_finished(source, segment_id)`` (Story 8.10): for each ``(location, query_hash)`` of the
  Segment's ``finished`` runs of ``source``, the latest ``started_at``, as a dict;
- ``created_since(at)`` (Story 8.10): the sum of ``created`` over every run started at or after
  ``at`` (any source, any status);
- ``pool_rows(scheduled_for)`` (Story 8.10): the ``pool_sufficient`` rows for that date, oldest
  first;
- ``has_pool_row(campaign_id, scheduled_for)`` (Story 8.10): whether that Campaign has one;
- ``ran_scheduled(source, campaign_id, scheduled_for)`` (Story 8.10): whether a ``source`` run
  of that Campaign for that date already ended ``finished``, ``paced`` or ``target_reached``.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import insert as insert_rows
from sqlalchemy import func, select, type_coerce
from sqlalchemy import update as update_rows

from nextron.db.models.base import UtcDateTime, new_id
from nextron.db.models.discovery import DiscoveryRunRecord
from nextron.db.uow import current
from nextron.domain.place_discovery import Outcome, RunStatus


@dataclass(frozen=True, slots=True)
class StoredRun:
    """One ``discovery_runs`` row."""

    id: str
    source: str
    campaign_id: str
    segment_id: str
    country_code: str
    location: str | None
    query_hash: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    seen: int
    created: int
    merged: int
    suppressed: int
    no_website: int
    market_blocked: int
    inactive: int
    scheduled_for: date | None = None
    ready_pool: int | None = None


_COLUMNS = (
    DiscoveryRunRecord.id,
    DiscoveryRunRecord.source,
    DiscoveryRunRecord.campaign_id,
    DiscoveryRunRecord.segment_id,
    DiscoveryRunRecord.country_code,
    DiscoveryRunRecord.location,
    DiscoveryRunRecord.query_hash,
    DiscoveryRunRecord.status,
    DiscoveryRunRecord.started_at,
    DiscoveryRunRecord.finished_at,
    DiscoveryRunRecord.seen,
    DiscoveryRunRecord.created,
    DiscoveryRunRecord.merged,
    DiscoveryRunRecord.suppressed,
    DiscoveryRunRecord.no_website,
    DiscoveryRunRecord.market_blocked,
    DiscoveryRunRecord.inactive,
    DiscoveryRunRecord.scheduled_for,
    DiscoveryRunRecord.ready_pool,
)


def insert(
    *,
    source: str,
    campaign_id: str,
    segment_id: str,
    country_code: str,
    location: str | None,
    query_hash: str,
    status: RunStatus,
    started_at: datetime,
    finished_at: datetime | None,
    scheduled_for: date | None = None,
    ready_pool: int | None = None,
) -> str:
    """Store a new run with every count 0; return its new ULID."""
    run_id = new_id()
    current().session.execute(
        insert_rows(DiscoveryRunRecord).values(
            id=run_id,
            source=source,
            campaign_id=campaign_id,
            segment_id=segment_id,
            country_code=country_code,
            location=location,
            query_hash=query_hash,
            status=str(status),
            started_at=started_at,
            finished_at=finished_at,
            seen=0,
            created=0,
            merged=0,
            suppressed=0,
            no_website=0,
            market_blocked=0,
            inactive=0,
            scheduled_for=scheduled_for,
            ready_pool=ready_pool,
        )
    )
    return run_id


def count(run_id: str, outcome: Outcome | None) -> None:
    """Count one candidate seen by the run, and its outcome unless ``None``."""
    values = {"seen": DiscoveryRunRecord.seen + 1}
    if outcome is not None:
        column = getattr(DiscoveryRunRecord, str(Outcome(outcome)))
        values[str(outcome)] = column + 1
    current().session.execute(
        update_rows(DiscoveryRunRecord).where(DiscoveryRunRecord.id == run_id).values(**values)
    )


def add_outcome(run_id: str, outcome: Outcome) -> None:
    """Add 1 to the run's ``outcome`` count alone, ``seen`` unchanged (Story 8.4: a created Lead
    left without a website also counts ``no_website``)."""
    column = getattr(DiscoveryRunRecord, str(Outcome(outcome)))
    current().session.execute(
        update_rows(DiscoveryRunRecord)
        .where(DiscoveryRunRecord.id == run_id)
        .values(**{str(outcome): column + 1})
    )


def finish(run_id: str, status: RunStatus, at: datetime) -> None:
    """End the run with ``status`` at ``at``."""
    current().session.execute(
        update_rows(DiscoveryRunRecord)
        .where(DiscoveryRunRecord.id == run_id)
        .values(status=str(status), finished_at=at)
    )


def fail_running(source: str, at: datetime) -> int:
    """End every ``running`` run of ``source`` ``failed`` at ``at``; the number of rows."""
    result = current().session.execute(
        update_rows(DiscoveryRunRecord)
        .where(
            DiscoveryRunRecord.status == str(RunStatus.RUNNING),
            DiscoveryRunRecord.source == source,
        )
        .values(status=str(RunStatus.FAILED), finished_at=at)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


def get(run_id: str) -> StoredRun | None:
    """The run ``run_id``, or ``None``."""
    row = current().session.execute(
        select(*_COLUMNS).where(DiscoveryRunRecord.id == run_id)
    ).one_or_none()
    return None if row is None else StoredRun(*row)


def recent(limit: int) -> tuple[StoredRun, ...]:
    """The newest ``limit`` runs, newest first."""
    rows = current().session.execute(
        select(*_COLUMNS)
        .order_by(DiscoveryRunRecord.started_at.desc(), DiscoveryRunRecord.id.desc())
        .limit(limit)
    )
    return tuple(StoredRun(*row) for row in rows)


def last_finished(source: str, segment_id: str) -> dict[tuple[str | None, str], datetime]:
    """The latest ``started_at`` of the Segment's ``finished`` runs of ``source``, by
    ``(location, query_hash)``."""
    rows = current().session.execute(
        select(
            DiscoveryRunRecord.location,
            DiscoveryRunRecord.query_hash,
            type_coerce(func.max(DiscoveryRunRecord.started_at), UtcDateTime),
        )
        .where(
            DiscoveryRunRecord.source == source,
            DiscoveryRunRecord.segment_id == segment_id,
            DiscoveryRunRecord.status == str(RunStatus.FINISHED),
        )
        .group_by(DiscoveryRunRecord.location, DiscoveryRunRecord.query_hash)
    )
    return {(location, hashed): latest for location, hashed, latest in rows}


def created_since(at: datetime) -> int:
    """The Leads every run started at or after ``at`` created."""
    total = current().session.execute(
        select(func.coalesce(func.sum(DiscoveryRunRecord.created), 0)).where(
            DiscoveryRunRecord.started_at >= at
        )
    ).scalar_one()
    return int(total)


def pool_rows(scheduled_for: date) -> tuple[StoredRun, ...]:
    """The ``pool_sufficient`` rows for ``scheduled_for``, oldest first."""
    rows = current().session.execute(
        select(*_COLUMNS)
        .where(
            DiscoveryRunRecord.status == str(RunStatus.POOL_SUFFICIENT),
            DiscoveryRunRecord.scheduled_for == scheduled_for,
        )
        .order_by(DiscoveryRunRecord.started_at, DiscoveryRunRecord.id)
    )
    return tuple(StoredRun(*row) for row in rows)


_RAN: Final = (RunStatus.FINISHED, RunStatus.PACED, RunStatus.TARGET_REACHED)


def ran_scheduled(source: str, campaign_id: str, scheduled_for: date) -> bool:
    """Whether a scheduled ``source`` run of the Campaign for ``scheduled_for`` already ended
    ``finished``, ``paced`` or ``target_reached`` (Story 8.10)."""
    found = current().session.execute(
        select(DiscoveryRunRecord.id)
        .where(
            DiscoveryRunRecord.source == source,
            DiscoveryRunRecord.campaign_id == campaign_id,
            DiscoveryRunRecord.scheduled_for == scheduled_for,
            DiscoveryRunRecord.status.in_([str(status) for status in _RAN]),
        )
        .limit(1)
    ).first()
    return found is not None


def has_pool_row(campaign_id: str, scheduled_for: date) -> bool:
    """Whether the Campaign has a ``pool_sufficient`` row for ``scheduled_for``."""
    found = current().session.execute(
        select(DiscoveryRunRecord.id)
        .where(
            DiscoveryRunRecord.status == str(RunStatus.POOL_SUFFICIENT),
            DiscoveryRunRecord.campaign_id == campaign_id,
            DiscoveryRunRecord.scheduled_for == scheduled_for,
        )
        .limit(1)
    ).first()
    return found is not None
