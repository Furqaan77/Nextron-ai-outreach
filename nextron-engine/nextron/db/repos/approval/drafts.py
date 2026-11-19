"""The ``drafts`` status group's repository (AD-3, AD-4; Story 4.9): the only code that writes
``status``, ``reapproval_required``, ``event_expired``, ``skipped_at`` and ``status_at``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.approval`` applies the
Draft machine. It reads the content group's ``enrollment_id`` and ``kind`` and, for
``ready_of_ended_enrollments``, the ``enrollments`` status, read-only: only
``nextron.db.repos.drafting.drafts`` and ``nextron.db.repos.sequencing.enrollments`` write them.

- ``ReadyDraft(id, enrollment_id)``: one ``ready`` Draft of an Enrollment;
- ``ready_of(enrollment_ids)``: the ``ready`` Drafts of those Enrollments, by Enrollment, then
  ``step_no``, then ``id``;
- ``ready_of_ended_enrollments()``: the ``ready`` Drafts whose Enrollment is ``stopped`` or
  ``completed``, in the same order;
- ``set_status(draft_id, from_status, to_status, at) -> bool``: moves the Draft from
  ``from_status`` to ``to_status`` and sets ``status_at`` (a conditional update); whether it
  changed a row.

Story 5.1 (the Approval Queue) adds two reads; they also read ``current_rev``, ``step_no``,
``due_at`` and ``created_at`` (drafting's) and the Enrollment's ``status``, ``lead_id`` and
``contact_id`` (sequencing's), read-only:

- ``QueueCandidate``: one ``ready`` Draft with a revision, with its Enrollment's status, Lead
  and Contact (``None`` without an Enrollment);
- ``queue_candidates(end)``: the queue's candidates, by ``id``: every ``ready`` Draft with a
  revision that is responsive (``reply``, ``rebook``, ``channel``; any Enrollment or none), or
  of a Step kind with ``due_at`` set, ``due_at <= end`` and an ``active`` Enrollment;
- ``earliest_ready_due()``: the earliest ``due_at`` among every ``ready`` Draft that has one
  (any Enrollment status, revision or not), or ``None``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy import update as update_rows

from nextron.db.models.drafting import DraftRecord
from nextron.db.models.sequencing import EnrollmentRecord
from nextron.db.uow import current
from nextron.domain.draft import RESPONSIVE_KINDS, STEP_KINDS

READY: Final = "ready"
ACTIVE: Final = "active"
ENDED: Final = ("stopped", "completed")
_ORDER: Final = (DraftRecord.enrollment_id, DraftRecord.step_no, DraftRecord.id)


@dataclass(frozen=True, slots=True)
class ReadyDraft:
    """A ``ready`` Draft of an Enrollment."""

    id: str
    enrollment_id: str


def ready_of(enrollment_ids: Sequence[str]) -> list[ReadyDraft]:
    """The ``ready`` Drafts of the Enrollments ``enrollment_ids``."""
    if not enrollment_ids:
        return []
    statement = (
        select(DraftRecord.id, DraftRecord.enrollment_id)
        .where(DraftRecord.enrollment_id.in_(list(enrollment_ids)), DraftRecord.status == READY)
        .order_by(*_ORDER)
    )
    return [ReadyDraft(str(i), str(e)) for i, e in current().session.execute(statement)]


def ready_of_ended_enrollments() -> list[ReadyDraft]:
    """The ``ready`` Drafts whose Enrollment has ended."""
    statement = (
        select(DraftRecord.id, DraftRecord.enrollment_id)
        .join(EnrollmentRecord, EnrollmentRecord.id == DraftRecord.enrollment_id)
        .where(DraftRecord.status == READY, EnrollmentRecord.status.in_(ENDED))
        .order_by(*_ORDER)
    )
    return [ReadyDraft(str(i), str(e)) for i, e in current().session.execute(statement)]


def set_status(draft_id: str, *, from_status: str, to_status: str, at: datetime) -> bool:
    """Move the Draft from ``from_status`` to ``to_status``; whether a row changed."""
    result = current().session.execute(
        update_rows(DraftRecord)
        .where(DraftRecord.id == draft_id, DraftRecord.status == from_status)
        .values(status=to_status, status_at=at)
    )
    return int(result.rowcount) == 1  # type: ignore[attr-defined]


# The Approval Queue (Story 5.1) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueueCandidate:
    """A ``ready`` Draft with a revision, with its Enrollment's status, Lead and Contact."""

    id: str
    kind: str
    step_no: int | None
    due_at: datetime | None
    created_at: datetime
    enrollment_id: str | None
    enrollment_status: str | None
    lead_id: str | None
    contact_id: str | None


def queue_candidates(end: datetime) -> list[QueueCandidate]:
    """The Approval Queue's candidates up to ``end``; see the module docstring."""
    statement = (
        select(
            DraftRecord.id,
            DraftRecord.kind,
            DraftRecord.step_no,
            DraftRecord.due_at,
            DraftRecord.created_at,
            DraftRecord.enrollment_id,
            EnrollmentRecord.status,
            EnrollmentRecord.lead_id,
            EnrollmentRecord.contact_id,
        )
        .outerjoin(EnrollmentRecord, EnrollmentRecord.id == DraftRecord.enrollment_id)
        .where(
            DraftRecord.status == READY,
            DraftRecord.current_rev.is_not(None),
            or_(
                DraftRecord.kind.in_([str(kind) for kind in RESPONSIVE_KINDS]),
                and_(
                    DraftRecord.kind.in_([str(kind) for kind in STEP_KINDS]),
                    DraftRecord.due_at.is_not(None),
                    DraftRecord.due_at <= end,
                    EnrollmentRecord.status == ACTIVE,
                ),
            ),
        )
        .order_by(DraftRecord.id)
    )
    return [
        QueueCandidate(
            id=str(row[0]),
            kind=str(row[1]),
            step_no=None if row[2] is None else int(row[2]),
            due_at=row[3],
            created_at=row[4],
            enrollment_id=None if row[5] is None else str(row[5]),
            enrollment_status=None if row[6] is None else str(row[6]),
            lead_id=None if row[7] is None else str(row[7]),
            contact_id=None if row[8] is None else str(row[8]),
        )
        for row in current().session.execute(statement)
    ]


def earliest_ready_due() -> datetime | None:
    """The earliest ``due_at`` of a ``ready`` Draft, or ``None``."""
    statement = select(func.min(DraftRecord.due_at)).where(
        DraftRecord.status == READY, DraftRecord.due_at.is_not(None)
    )
    found: datetime | None = current().session.execute(statement).scalar_one()
    return found
