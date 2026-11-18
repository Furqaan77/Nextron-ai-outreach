"""The ``drafts`` content group's repository (AD-3, AD-4, AD-21; Story 4.9): the only code that
writes ``id``, ``kind``, ``enrollment_id``, ``step_no``, ``day_offset``, ``current_rev``,
``due_at``, ``generic``, ``stale``, ``created_at`` and ``updated_at``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.drafting`` does. The reads
return the status group's columns too, read-only: only ``nextron.db.repos.approval.drafts``
writes them (AD-3), and ``insert_slots`` leaves them to their server defaults (``status``
``ready``, the flags 0, the times NULL).

- ``StoredDraft``: one row;
- ``get(draft_id)``: the row, or ``None``;
- ``of_enrollment(enrollment_id)``: the Enrollment's Drafts, by ``step_no``, then ``id``;
- ``NewSlot(kind, enrollment_id, step_no, day_offset, due_at)`` and
  ``insert_slots(slots, at) -> list[id]``: the slots in **one** ``INSERT`` statement (none with
  no slot); their new ULIDs, in order;
- ``set_due(draft_id, due_at, at) -> bool``: ``due_at`` and ``updated_at`` of a ``ready`` or
  ``approved`` Draft (a conditional update); whether it changed a row;
- ``follow_ups_due_before(end) -> dict[enrollment_id, int]``: per Enrollment, its ``ready`` or
  ``approved`` ``step``/``recontact`` Drafts with ``step_no > 1`` and ``due_at < end``;
- ``set_current_rev(draft_id, rev, at) -> bool`` (Story 4.12): ``current_rev`` and
  ``updated_at`` (the foreign key makes ``rev`` name an existing revision); whether a row
  changed;
- ``ready_with_text()`` (Story 4.12): every ``ready`` Draft with a ``current_rev``, by ``id``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Row, func, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.drafting import DraftRecord
from nextron.db.uow import current

READY: Final = "ready"
PENDING: Final = (READY, "approved")
STEP_KINDS: Final = ("step", "recontact")


@dataclass(frozen=True, slots=True)
class StoredDraft:
    """One ``drafts`` row."""

    id: str
    kind: str
    enrollment_id: str | None
    step_no: int | None
    day_offset: int | None
    current_rev: int | None
    due_at: datetime | None
    generic: bool
    stale: bool
    created_at: datetime
    updated_at: datetime
    status: str
    reapproval_required: bool
    event_expired: bool
    skipped_at: datetime | None
    status_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewSlot:
    """One slot ``insert_slots`` stores."""

    kind: str
    enrollment_id: str
    step_no: int
    day_offset: int
    due_at: datetime | None


_COLUMNS: Final = (
    DraftRecord.id,
    DraftRecord.kind,
    DraftRecord.enrollment_id,
    DraftRecord.step_no,
    DraftRecord.day_offset,
    DraftRecord.current_rev,
    DraftRecord.due_at,
    DraftRecord.generic,
    DraftRecord.stale,
    DraftRecord.created_at,
    DraftRecord.updated_at,
    # the status group, read only
    DraftRecord.status,
    DraftRecord.reapproval_required,
    DraftRecord.event_expired,
    DraftRecord.skipped_at,
    DraftRecord.status_at,
)


def _stored(row: Row[Any]) -> StoredDraft:
    return StoredDraft(*row)


def get(draft_id: str) -> StoredDraft | None:
    """The Draft ``draft_id``, or ``None``."""
    statement = select(*_COLUMNS).where(DraftRecord.id == draft_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def of_enrollment(enrollment_id: str) -> list[StoredDraft]:
    """The Enrollment's Drafts, by ``step_no``, then ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(DraftRecord.enrollment_id == enrollment_id)
        .order_by(DraftRecord.step_no, DraftRecord.id)
    )
    return [_stored(row) for row in current().session.execute(statement)]


def insert_slots(slots: Sequence[NewSlot], at: datetime) -> list[str]:
    """Store ``slots``, created at ``at``, in one ``INSERT`` statement; their new ULIDs."""
    if not slots:
        return []
    ids = [new_id() for _ in slots]
    current().session.execute(
        insert_rows(DraftRecord).values(
            [
                {
                    "id": draft_id,
                    "kind": slot.kind,
                    "enrollment_id": slot.enrollment_id,
                    "step_no": slot.step_no,
                    "day_offset": slot.day_offset,
                    "current_rev": None,
                    "due_at": slot.due_at,
                    "generic": False,
                    "stale": False,
                    "created_at": at,
                    "updated_at": at,
                }
                for draft_id, slot in zip(ids, slots, strict=True)
            ]
        )
    )
    return ids


def set_due(draft_id: str, due_at: datetime, at: datetime) -> bool:
    """Set the due time of a ``ready`` or ``approved`` Draft; whether a row changed."""
    result = current().session.execute(
        update_rows(DraftRecord)
        .where(DraftRecord.id == draft_id, DraftRecord.status.in_(PENDING))
        .values(due_at=due_at, updated_at=at)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def follow_ups_due_before(end: datetime) -> dict[str, int]:
    """Per Enrollment, its unsent follow-up Step Drafts due before ``end``."""
    statement = (
        select(DraftRecord.enrollment_id, func.count())
        .where(
            DraftRecord.kind.in_(STEP_KINDS),
            DraftRecord.status.in_(PENDING),
            DraftRecord.step_no > 1,
            DraftRecord.due_at.is_not(None),
            DraftRecord.due_at < end,
        )
        .group_by(DraftRecord.enrollment_id)
    )
    return {str(found): int(count) for found, count in current().session.execute(statement)}


def set_current_rev(draft_id: str, rev: int, at: datetime) -> bool:
    """Point the Draft at its revision ``rev``, at ``at``; whether a row changed."""
    result = current().session.execute(
        update_rows(DraftRecord)
        .where(DraftRecord.id == draft_id)
        .values(current_rev=rev, updated_at=at)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


def ready_with_text() -> list[StoredDraft]:
    """Every ``ready`` Draft that has a revision, by ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(DraftRecord.status == READY, DraftRecord.current_rev.is_not(None))
        .order_by(DraftRecord.id)
    )
    return [_stored(row) for row in current().session.execute(statement)]
