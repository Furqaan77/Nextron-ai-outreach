"""Read API of the ``drafting`` service (AD-3, AD-21; Story 4.9).

Import rule: ``nextron.domain`` and ``nextron.db.repos.drafting`` only. All read through the
current unit of work, read-only ones included.

- ``DraftView``: one Draft, both column groups; ``reviewable`` is true once it has a revision
  (``current_rev is not None``; Story 4.12 writes the first);
- ``draft(draft_id)``: the Draft, or ``None`` (a text that isn't a ULID included);
- ``drafts_of(enrollment_id)``: the Enrollment's Drafts, by ``step_no`` (``()`` for a text
  that isn't a ULID);
- ``follow_ups_due_before(end) -> Mapping[enrollment_id, int]``: per Enrollment, its unsent
  (``ready`` or ``approved``) Step Drafts with ``step_no > 1`` and ``due_at`` before ``end``
  (overdue ones included). Whether the Enrollment is live is ``sequencing``'s to filter.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from nextron.db.repos.drafting import drafts as drafts_repo
from nextron.db.repos.drafting.drafts import StoredDraft
from nextron.domain.draft import DraftKind, DraftStatus
from nextron.domain.ids import is_ulid


@dataclass(frozen=True, slots=True)
class DraftView:
    """One Draft as other services read it."""

    id: str
    kind: DraftKind
    enrollment_id: str | None
    step_no: int | None
    day_offset: int | None
    current_rev: int | None
    due_at: datetime | None
    generic: bool
    stale: bool
    created_at: datetime
    updated_at: datetime
    status: DraftStatus
    reapproval_required: bool
    event_expired: bool
    skipped_at: datetime | None
    status_at: datetime | None

    @property
    def reviewable(self) -> bool:
        """Whether the Draft has text to review."""
        return self.current_rev is not None


def _view(stored: StoredDraft) -> DraftView:
    return DraftView(
        id=stored.id,
        kind=DraftKind(stored.kind),
        enrollment_id=stored.enrollment_id,
        step_no=stored.step_no,
        day_offset=stored.day_offset,
        current_rev=stored.current_rev,
        due_at=stored.due_at,
        generic=bool(stored.generic),
        stale=bool(stored.stale),
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        status=DraftStatus(stored.status),
        reapproval_required=bool(stored.reapproval_required),
        event_expired=bool(stored.event_expired),
        skipped_at=stored.skipped_at,
        status_at=stored.status_at,
    )


def draft(draft_id: str) -> DraftView | None:
    """The Draft ``draft_id``, or ``None``."""
    if not is_ulid(draft_id):
        return None
    stored = drafts_repo.get(draft_id)
    return None if stored is None else _view(stored)


def drafts_of(enrollment_id: str) -> tuple[DraftView, ...]:
    """The Enrollment's Drafts, by ``step_no``."""
    if not is_ulid(enrollment_id):
        return ()
    return tuple(_view(stored) for stored in drafts_repo.of_enrollment(enrollment_id))


def follow_ups_due_before(end: datetime) -> Mapping[str, int]:
    """Per Enrollment, its unsent follow-up Step Drafts due before ``end``."""
    return MappingProxyType(drafts_repo.follow_ups_due_before(end))
