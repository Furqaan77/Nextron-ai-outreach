"""The Signal tables' repository (FR-10, FR-11, FR-57, AD-3, AD-7, AD-18): the only code that
reads or writes ``evidence``, ``signals``, ``signal_evidence`` and ``extraction_runs``.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It stores and returns what it is given; the one check it
makes is that a Signal is never stored without Evidence. ``nextron.app.research`` checks the
rest. ``pages_checked`` is stored as a JSON array (``json.dumps``, no spaces) and returned as a
tuple of URLs.

- ``StoredEvidence`` (its ``quote`` and ``value`` never show in the ``repr``), ``StoredSignal``,
  ``StoredLink`` and ``StoredExtractionRun``: one row of each table.
- ``insert_evidence(...) -> id``: a new row (its ``type`` is ``evidence_type``), last confirmed
  at its ``retrieved_at``.
- ``confirm_evidence(evidence_id, at)``: sets ``last_confirmed_at``; ``supersede_evidence(old_id,
  replacement_id)``: sets ``superseded_by``; ``retract_evidence(evidence_id, at)``: sets
  ``retracted_at``. The ``evidence`` triggers refuse anything else.
- ``evidence(lead_id, *, live_only)``: the Lead's Evidence by ``created_at``, then ``id``; with
  ``live_only``, only rows neither superseded nor retracted.
- ``insert_signal(*, ..., evidence_ids) -> id``: a new Signal and one ``signal_evidence`` row
  per Evidence id; ``ValueError`` when ``evidence_ids`` is empty (nothing is written).
- ``link(signal_id, evidence_id)``: one more ``signal_evidence`` row.
- ``signals(lead_id, *, current_only)``: the Lead's Signals by ``first_seen_at``, then ``id``;
  with ``current_only``, only those not retracted. ``links(lead_id)``: every
  ``signal_evidence`` row of the Lead's Signals.
- ``retract_unsupported(lead_id, at) -> ids``: retracts each of the Lead's Signals that isn't
  retracted and has no live Evidence; returns their ids.
- ``insert_run(...) -> id``; ``last_ok_run(lead_id)``: the Lead's ``ok`` run started last (then
  the greatest id), or ``None``; ``runs(lead_id)``: every run of the Lead, by ``started_at``,
  then ``id``.
- ``reparent(loser_id, survivor_id)``: moves every ``evidence`` and ``signals`` row of the Lead
  ``loser_id`` to ``survivor_id``; ``extraction_runs`` stay.
- ``tombstone_person_evidence(contact_id) -> int`` (Story 3.13): replaces the ``quote`` and
  ``value`` of every Evidence row whose ``person_contact_id`` is the Contact with their
  tombstones (``nextron.db.erasure.tombstoned``: ``[erased]``; NULL stays NULL), one statement
  per row, the only change the ``evidence`` triggers allow besides the four above; returns how
  many rows it tombstoned.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, and_, exists, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.erasure import tombstoned
from nextron.db.models.base import new_id
from nextron.db.models.signals import (
    EvidenceRecord,
    ExtractionRunRecord,
    SignalEvidenceRecord,
    SignalRecord,
)
from nextron.db.uow import current

OK = "ok"  # nextron.domain.extraction.ExtractionOutcome.OK
EVIDENCE_TABLE = "evidence"


@dataclass(frozen=True, slots=True)
class StoredEvidence:
    """One ``evidence`` row; its ``quote`` and ``value`` never show in the ``repr``."""

    id: str
    lead_id: str
    type: str
    quote: str | None = field(repr=False)
    sought: str | None
    number: int | None
    fact: str | None
    value: str | None = field(repr=False)
    pages_checked: tuple[str, ...]
    source: str
    source_url: str | None
    retrieved_at: datetime
    extracted_by: str
    person_contact_id: str | None
    superseded_by: str | None
    retracted_at: datetime | None
    last_confirmed_at: datetime
    created_at: datetime

    @property
    def live(self) -> bool:
        """Neither superseded nor retracted."""
        return self.superseded_by is None and self.retracted_at is None


@dataclass(frozen=True, slots=True)
class StoredSignal:
    """One ``signals`` row."""

    id: str
    lead_id: str
    signal_type: str
    signal_class: str
    tier: int
    score_only: bool
    event_date: date | None
    first_seen_at: datetime
    retracted_at: datetime | None


@dataclass(frozen=True, slots=True)
class StoredLink:
    """One ``signal_evidence`` row."""

    id: str
    signal_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class StoredExtractionRun:
    """One ``extraction_runs`` row."""

    id: str
    lead_id: str
    crawl_run_id: str | None
    route: str
    actor: str | None
    outcome: str
    error_code: str | None
    pages_sent: int
    pages_truncated: int
    stored: int
    discarded: int
    started_at: datetime
    ended_at: datetime


_EVIDENCE_COLUMNS = (
    EvidenceRecord.id,
    EvidenceRecord.lead_id,
    EvidenceRecord.type,
    EvidenceRecord.quote,
    EvidenceRecord.sought,
    EvidenceRecord.number,
    EvidenceRecord.fact,
    EvidenceRecord.value,
    EvidenceRecord.pages_checked,
    EvidenceRecord.source,
    EvidenceRecord.source_url,
    EvidenceRecord.retrieved_at,
    EvidenceRecord.extracted_by,
    EvidenceRecord.person_contact_id,
    EvidenceRecord.superseded_by,
    EvidenceRecord.retracted_at,
    EvidenceRecord.last_confirmed_at,
    EvidenceRecord.created_at,
)
_SIGNAL_COLUMNS = (
    SignalRecord.id,
    SignalRecord.lead_id,
    SignalRecord.signal_type,
    SignalRecord.signal_class,
    SignalRecord.tier,
    SignalRecord.score_only,
    SignalRecord.event_date,
    SignalRecord.first_seen_at,
    SignalRecord.retracted_at,
)
_RUN_COLUMNS = (
    ExtractionRunRecord.id,
    ExtractionRunRecord.lead_id,
    ExtractionRunRecord.crawl_run_id,
    ExtractionRunRecord.route,
    ExtractionRunRecord.actor,
    ExtractionRunRecord.outcome,
    ExtractionRunRecord.error_code,
    ExtractionRunRecord.pages_sent,
    ExtractionRunRecord.pages_truncated,
    ExtractionRunRecord.stored,
    ExtractionRunRecord.discarded,
    ExtractionRunRecord.started_at,
    ExtractionRunRecord.ended_at,
)
_LIVE = and_(EvidenceRecord.superseded_by.is_(None), EvidenceRecord.retracted_at.is_(None))


def _stored_evidence(row: Row[Any]) -> StoredEvidence:
    values = list(row)
    values[8] = tuple(json.loads(values[8]))  # pages_checked
    return StoredEvidence(*values)


# Evidence -------------------------------------------------------------------------------------


def insert_evidence(
    *,
    lead_id: str,
    evidence_type: str,
    quote: str | None,
    sought: str | None,
    number: int | None,
    fact: str | None,
    value: str | None,
    pages_checked: Sequence[str],
    source: str,
    source_url: str | None,
    retrieved_at: datetime,
    extracted_by: str,
    person_contact_id: str | None,
    at: datetime,
) -> str:
    """Store a new Evidence row created ``at`` and last confirmed at ``retrieved_at``; return
    its new ULID."""
    evidence_id = new_id()
    current().session.execute(
        insert_rows(EvidenceRecord).values(
            id=evidence_id,
            lead_id=lead_id,
            type=evidence_type,
            quote=quote,
            sought=sought,
            number=number,
            fact=fact,
            value=value,
            pages_checked=json.dumps(list(pages_checked), separators=(",", ":")),
            source=source,
            source_url=source_url,
            retrieved_at=retrieved_at,
            extracted_by=extracted_by,
            person_contact_id=person_contact_id,
            superseded_by=None,
            retracted_at=None,
            last_confirmed_at=retrieved_at,
            created_at=at,
        )
    )
    return evidence_id


def confirm_evidence(evidence_id: str, at: datetime) -> None:
    """Record that the Evidence ``evidence_id`` was seen again ``at``."""
    current().session.execute(
        update_rows(EvidenceRecord)
        .where(EvidenceRecord.id == evidence_id)
        .values(last_confirmed_at=at)
    )


def supersede_evidence(old_id: str, replacement_id: str) -> None:
    """Record that the Evidence ``replacement_id`` replaced ``old_id``."""
    current().session.execute(
        update_rows(EvidenceRecord)
        .where(EvidenceRecord.id == old_id)
        .values(superseded_by=replacement_id)
    )


def retract_evidence(evidence_id: str, at: datetime) -> None:
    """Record that the Evidence ``evidence_id``'s source vanished ``at``."""
    current().session.execute(
        update_rows(EvidenceRecord)
        .where(EvidenceRecord.id == evidence_id)
        .values(retracted_at=at)
    )


def evidence(lead_id: str, *, live_only: bool) -> tuple[StoredEvidence, ...]:
    """The Lead's Evidence, oldest first; only the live rows with ``live_only``."""
    statement = select(*_EVIDENCE_COLUMNS).where(EvidenceRecord.lead_id == lead_id)
    if live_only:
        statement = statement.where(_LIVE)
    statement = statement.order_by(EvidenceRecord.created_at, EvidenceRecord.id)
    return tuple(_stored_evidence(row) for row in current().session.execute(statement))


# Signals --------------------------------------------------------------------------------------


def insert_signal(
    *,
    lead_id: str,
    signal_type: str,
    signal_class: str,
    tier: int,
    score_only: bool,
    event_date: date | None,
    first_seen_at: datetime,
    evidence_ids: Iterable[str],
) -> str:
    """Store a new Signal supported by ``evidence_ids`` (at least one); return its ULID."""
    supporting = list(dict.fromkeys(evidence_ids))
    if not supporting:
        raise ValueError("a Signal is never stored without at least one Evidence row")
    signal_id = new_id()
    current().session.execute(
        insert_rows(SignalRecord).values(
            id=signal_id,
            lead_id=lead_id,
            signal_type=signal_type,
            signal_class=signal_class,
            tier=tier,
            score_only=score_only,
            event_date=event_date,
            first_seen_at=first_seen_at,
            retracted_at=None,
        )
    )
    for evidence_id in supporting:
        link(signal_id, evidence_id)
    return signal_id


def link(signal_id: str, evidence_id: str) -> None:
    """Record that the Evidence ``evidence_id`` supports the Signal ``signal_id``."""
    current().session.execute(
        insert_rows(SignalEvidenceRecord).values(
            id=new_id(), signal_id=signal_id, evidence_id=evidence_id
        )
    )


def signals(lead_id: str, *, current_only: bool) -> tuple[StoredSignal, ...]:
    """The Lead's Signals, by ``first_seen_at``, then ``id``; only those not retracted with
    ``current_only``."""
    statement = select(*_SIGNAL_COLUMNS).where(SignalRecord.lead_id == lead_id)
    if current_only:
        statement = statement.where(SignalRecord.retracted_at.is_(None))
    statement = statement.order_by(SignalRecord.first_seen_at, SignalRecord.id)
    return tuple(StoredSignal(*row) for row in current().session.execute(statement))


def links(lead_id: str) -> tuple[StoredLink, ...]:
    """Every ``signal_evidence`` row of the Lead's Signals, by Signal, then Evidence."""
    statement = (
        select(
            SignalEvidenceRecord.id,
            SignalEvidenceRecord.signal_id,
            SignalEvidenceRecord.evidence_id,
        )
        .join(SignalRecord, SignalRecord.id == SignalEvidenceRecord.signal_id)
        .where(SignalRecord.lead_id == lead_id)
        .order_by(SignalEvidenceRecord.signal_id, SignalEvidenceRecord.evidence_id)
    )
    return tuple(StoredLink(*row) for row in current().session.execute(statement))


def retract_unsupported(lead_id: str, at: datetime) -> tuple[str, ...]:
    """Retract each of the Lead's current Signals with no live Evidence; their ids."""
    supported = exists(
        select(SignalEvidenceRecord.id)
        .join(EvidenceRecord, EvidenceRecord.id == SignalEvidenceRecord.evidence_id)
        .where(SignalEvidenceRecord.signal_id == SignalRecord.id, _LIVE)
    )
    session = current().session
    ids = tuple(
        session.execute(
            select(SignalRecord.id)
            .where(
                SignalRecord.lead_id == lead_id,
                SignalRecord.retracted_at.is_(None),
                ~supported,
            )
            .order_by(SignalRecord.id)
        ).scalars()
    )
    if ids:
        session.execute(
            update_rows(SignalRecord).where(SignalRecord.id.in_(ids)).values(retracted_at=at)
        )
    return ids


# Extraction runs ------------------------------------------------------------------------------


def insert_run(
    *,
    lead_id: str,
    crawl_run_id: str | None,
    route: str,
    actor: str | None,
    outcome: str,
    error_code: str | None,
    pages_sent: int,
    pages_truncated: int,
    stored: int,
    discarded: int,
    started_at: datetime,
    ended_at: datetime,
) -> str:
    """Store an extraction run; return its new ULID."""
    run_id = new_id()
    current().session.execute(
        insert_rows(ExtractionRunRecord).values(
            id=run_id,
            lead_id=lead_id,
            crawl_run_id=crawl_run_id,
            route=route,
            actor=actor,
            outcome=outcome,
            error_code=error_code,
            pages_sent=pages_sent,
            pages_truncated=pages_truncated,
            stored=stored,
            discarded=discarded,
            started_at=started_at,
            ended_at=ended_at,
        )
    )
    return run_id


def last_ok_run(lead_id: str) -> StoredExtractionRun | None:
    """The Lead's ``ok`` run started last, or ``None``."""
    statement = (
        select(*_RUN_COLUMNS)
        .where(ExtractionRunRecord.lead_id == lead_id, ExtractionRunRecord.outcome == OK)
        .order_by(ExtractionRunRecord.started_at.desc(), ExtractionRunRecord.id.desc())
        .limit(1)
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredExtractionRun(*row)


def runs(lead_id: str) -> tuple[StoredExtractionRun, ...]:
    """Every extraction run of the Lead, by ``started_at``, then ``id``."""
    statement = (
        select(*_RUN_COLUMNS)
        .where(ExtractionRunRecord.lead_id == lead_id)
        .order_by(ExtractionRunRecord.started_at, ExtractionRunRecord.id)
    )
    return tuple(StoredExtractionRun(*row) for row in current().session.execute(statement))


# Merges ---------------------------------------------------------------------------------------


def reparent(loser_id: str, survivor_id: str) -> None:
    """Move the Lead ``loser_id``'s Evidence and Signals to ``survivor_id``."""
    session = current().session
    session.execute(
        update_rows(EvidenceRecord)
        .where(EvidenceRecord.lead_id == loser_id)
        .values(lead_id=survivor_id)
    )
    session.execute(
        update_rows(SignalRecord)
        .where(SignalRecord.lead_id == loser_id)
        .values(lead_id=survivor_id)
    )


def tombstone_person_evidence(contact_id: str) -> int:
    """Replace the personal data of every Evidence row about the Contact ``contact_id`` with
    tombstones; the number of rows."""
    session = current().session
    rows = session.execute(
        select(EvidenceRecord.__table__)
        .where(EvidenceRecord.person_contact_id == contact_id)
        .order_by(EvidenceRecord.id)
    ).mappings().all()
    for row in rows:
        session.execute(
            update_rows(EvidenceRecord)
            .where(EvidenceRecord.id == row["id"])
            .values(**tombstoned(EVIDENCE_TABLE, row))
        )
    return len(rows)
