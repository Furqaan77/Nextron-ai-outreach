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
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.drafting import DraftRecord
from nextron.db.models.sequencing import EnrollmentRecord
from nextron.db.uow import current

READY: Final = "ready"
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
