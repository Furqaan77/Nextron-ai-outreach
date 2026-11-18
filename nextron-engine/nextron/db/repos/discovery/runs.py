"""The ``discovery_runs`` repository (Story 8.3, FR-5, AD-3): the only code that writes the table.

Every function runs one explicit Core statement through the current unit of work's session, as
``nextron.db.repos.discovery.leads`` does; it checks nothing (``nextron.app.discovery`` does).

- ``StoredRun``: one row;
- ``insert(...) -> id``: a new run, every count 0;
- ``count(run_id, outcome)``: adds 1 to ``seen`` and, unless ``outcome`` is ``None``, 1 to the
  outcome's count column (a ``nextron.domain.place_discovery.Outcome`` value), in one
  statement;
- ``add_outcome(run_id, outcome)`` (Story 8.4): adds 1 to the outcome's count alone;
- ``finish(run_id, status, at)``: sets ``status`` and ``finished_at``;
- ``fail_running(source, at) -> int``: ends every ``running`` row of ``source`` ``failed`` at
  ``at``; the count (Story 8.4: each source's runs recover only their own rows);
- ``get(run_id)``: the row, or ``None``;
- ``recent(limit)``: the newest ``limit`` runs (by ``started_at``, then ``id``, descending).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
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
