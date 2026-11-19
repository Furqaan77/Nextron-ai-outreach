"""The ``grounding_results`` repository (FR-21, AD-3, AD-17, AD-18; Story 4.13): the only code
that writes a Draft revision's grounding result.

Every function runs explicit Core statements through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.drafting`` does.

- ``StoredFlag(rule, span, explanation, source="rules")`` and ``StoredGroundingResult``: one
  row, ``flags`` decoded from its JSON array of ``{rule, span, explanation, source}`` objects
  (``rule`` is the flag's code; ``source`` ``rules`` or ``ai``, Story 4.14; a flag stored
  without one reads as ``rules``);
- ``NewGroundingResult(draft_id, rev, status, flags, rules_version)`` and ``insert(result, at)
  -> id``: one new row checked at ``at`` (``route_tuple_id``, ``served_model`` and
  ``ai_checked_at`` NULL until the AI check, Story 4.14); its new ULID;
- ``record_ai_result(draft_id, rev, *, status, flags, route_tuple_id, served_model, at) ->
  bool`` (Story 4.14): the AI check's result for that revision: status, flags, tuple, served
  model and ``ai_checked_at`` = ``at``, set only while ``ai_checked_at`` is NULL (one
  conditional ``UPDATE``), so a re-run is a no-op; whether a row changed;
- ``of_revision(draft_id, rev)``: the row, or ``None``;
- ``tombstone(draft_ids) -> int``: every result of those Drafts gets each flag's non-empty
  ``span`` replaced by ``[erased]`` (an empty span, a whole-revision flag, stays ``""``), and
  each ``ai`` flag's explanation (model text) too (Story 4.14); rule, rules' explanations,
  status and the rest kept; the number of rows changed. Running it again changes nothing;
- ``for_export(contact_id) -> tuple[dict, ...]`` (Story 3.15's subject access export): every
  column of each result of the Drafts of the Contact's Enrollments (a generic ``select`` of the
  table), oldest first. It reads ``drafts.enrollment_id`` and ``enrollments.contact_id`` (owner
  ``sequencing``) read-only, to find them in one statement.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Row, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.drafting import DraftRecord, GroundingResultRecord
from nextron.db.models.sequencing import EnrollmentRecord
from nextron.db.uow import current
from nextron.domain.erasure import REDACTED
from nextron.domain.grounding_check import FlagSource

RULES_SOURCE: Final = FlagSource.RULES.value
AI_SOURCE: Final = FlagSource.AI.value


@dataclass(frozen=True, slots=True)
class StoredFlag:
    """One flag of a stored result."""

    rule: str
    span: str
    explanation: str
    source: str = RULES_SOURCE


@dataclass(frozen=True, slots=True)
class NewGroundingResult:
    """One result ``insert`` stores."""

    draft_id: str
    rev: int
    status: str
    flags: tuple[StoredFlag, ...]
    rules_version: int


@dataclass(frozen=True, slots=True)
class StoredGroundingResult:
    """One ``grounding_results`` row."""

    id: str
    draft_id: str
    rev: int
    status: str
    flags: tuple[StoredFlag, ...]
    rules_version: int
    route_tuple_id: str | None
    served_model: str | None
    checked_at: datetime
    ai_checked_at: datetime | None


_COLUMNS: Final = (
    GroundingResultRecord.id,
    GroundingResultRecord.draft_id,
    GroundingResultRecord.rev,
    GroundingResultRecord.status,
    GroundingResultRecord.flags,
    GroundingResultRecord.rules_version,
    GroundingResultRecord.route_tuple_id,
    GroundingResultRecord.served_model,
    GroundingResultRecord.checked_at,
    GroundingResultRecord.ai_checked_at,
)


def _encoded(flags: Iterable[StoredFlag]) -> str:
    return json.dumps(
        [
            {
                "rule": flag.rule,
                "span": flag.span,
                "explanation": flag.explanation,
                "source": flag.source,
            }
            for flag in flags
        ]
    )


def _decoded(text: str) -> tuple[StoredFlag, ...]:
    return tuple(
        StoredFlag(
            str(each["rule"]),
            str(each["span"]),
            str(each["explanation"]),
            str(each.get("source", RULES_SOURCE)),
        )
        for each in json.loads(text)
    )


def _stored(row: Row[Any]) -> StoredGroundingResult:
    return StoredGroundingResult(
        id=row.id,
        draft_id=row.draft_id,
        rev=row.rev,
        status=row.status,
        flags=_decoded(row.flags),
        rules_version=row.rules_version,
        route_tuple_id=row.route_tuple_id,
        served_model=row.served_model,
        checked_at=row.checked_at,
        ai_checked_at=row.ai_checked_at,
    )


def insert(result: NewGroundingResult, at: datetime) -> str:
    """Store ``result``, checked at ``at``; its new ULID."""
    result_id = new_id()
    current().session.execute(
        insert_rows(GroundingResultRecord).values(
            id=result_id,
            draft_id=result.draft_id,
            rev=result.rev,
            status=result.status,
            flags=_encoded(result.flags),
            rules_version=result.rules_version,
            route_tuple_id=None,
            served_model=None,
            checked_at=at,
            ai_checked_at=None,
        )
    )
    return result_id


def record_ai_result(
    draft_id: str,
    rev: int,
    *,
    status: str,
    flags: tuple[StoredFlag, ...],
    route_tuple_id: str | None,
    served_model: str | None,
    at: datetime,
) -> bool:
    """Record the AI check's result for the revision, once; whether a row changed."""
    changed = current().session.execute(
        update_rows(GroundingResultRecord)
        .where(
            GroundingResultRecord.draft_id == draft_id,
            GroundingResultRecord.rev == rev,
            GroundingResultRecord.ai_checked_at.is_(None),
        )
        .values(
            status=status,
            flags=_encoded(flags),
            route_tuple_id=route_tuple_id,
            served_model=served_model,
            ai_checked_at=at,
        )
    )
    return bool(changed.rowcount)


def of_revision(draft_id: str, rev: int) -> StoredGroundingResult | None:
    """The result of the revision ``rev`` of the Draft ``draft_id``, or ``None``."""
    statement = select(*_COLUMNS).where(
        GroundingResultRecord.draft_id == draft_id, GroundingResultRecord.rev == rev
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def tombstone(draft_ids: Iterable[str]) -> int:
    """Turn every non-empty span of every result of ``draft_ids`` into ``[erased]`` (the
    erasure; an empty span stays empty); the rows changed."""
    chosen = sorted(set(draft_ids))
    if not chosen:
        return 0
    session = current().session
    rows = session.execute(
        select(GroundingResultRecord.id, GroundingResultRecord.flags).where(
            GroundingResultRecord.draft_id.in_(chosen)
        )
    ).all()
    changed = 0
    for row in rows:
        flags = _decoded(row.flags)
        erased = tuple(
            StoredFlag(
                flag.rule,
                REDACTED if flag.span else "",
                REDACTED if flag.source == AI_SOURCE else flag.explanation,
                flag.source,
            )
            for flag in flags
        )
        if erased == flags:
            continue
        session.execute(
            update_rows(GroundingResultRecord)
            .where(GroundingResultRecord.id == row.id)
            .values(flags=_encoded(erased))
        )
        changed += 1
    return changed


def for_export(contact_id: str) -> tuple[dict[str, object], ...]:
    """Every column of each result of the Drafts of the Contact's Enrollments, oldest first."""
    table = GroundingResultRecord.__table__
    statement = (
        select(table)
        .join(DraftRecord, DraftRecord.id == table.c.draft_id)
        .join(EnrollmentRecord, EnrollmentRecord.id == DraftRecord.enrollment_id)
        .where(EnrollmentRecord.contact_id == contact_id)
        .order_by(table.c.checked_at, table.c.draft_id, table.c.rev)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
