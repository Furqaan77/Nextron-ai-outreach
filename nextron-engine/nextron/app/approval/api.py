"""Public command API of the ``approval`` service (AD-3, AD-4, AD-21; Story 4.9).

Other services and the delivery layers change ``approval`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Approval(clock)`` owns the ``drafts`` status group (``nextron.db.repos.approval.drafts``) and
applies the Draft machine (``nextron.domain.draft``). Every status change sets ``status_at``
and appends one ``events`` row in the same transaction: entity ``drafts``, actor
``system:approval``, at the clock's now.

- **``@command cancel_pending(enrollment_ids) -> tuple[str, ...]``**: each ``ready`` Draft of
  those Enrollments moves to ``cancelled`` (event ``cancel``, cause ``sequence_stopped``,
  ``cause_ref`` its Enrollment). It returns the ids cancelled, by Enrollment, then Step. An
  ``approved`` Draft is left as it is: cancelling it needs ``sending.cancel`` of its queued
  message (Epic 5). ``enrollment_ids`` must be a ``tuple`` or ``list`` of ``str``
  (``TypeError`` otherwise); a repeat is a no-op. ``Sequencing.stop`` and its suppression sweep
  call it for what they stopped.
- **``@command cancel_pending_suppressed(n: SubjectSuppressed)``**: the ``SubjectSuppressed``
  backstop, registered after ``Sequencing.stop_suppressed``: it cancels, the same way, every
  ``ready`` Draft whose Enrollment has ended (``stopped`` or ``completed``). Idempotent.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from nextron.db import eventlog
from nextron.db.repos.approval import drafts as drafts_repo
from nextron.db.repos.approval.drafts import ReadyDraft
from nextron.db.uow import command
from nextron.domain.draft import DraftEvent, DraftStatus, transition
from nextron.domain.notifications import SubjectSuppressed
from nextron.ports.clock import Clock

ENTITY: Final = "drafts"
ACTOR: Final = "system:approval"


class Approval:
    """The status of Drafts; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def cancel_pending(self, enrollment_ids: Sequence[str]) -> tuple[str, ...]:
        """Cancel the ``ready`` Drafts of the Enrollments ``enrollment_ids``; the ids
        cancelled."""
        if not isinstance(enrollment_ids, tuple | list) or not all(
            isinstance(found, str) for found in enrollment_ids
        ):
            raise TypeError("cancel_pending takes a tuple or list of Enrollment ids")
        return self._cancel(drafts_repo.ready_of(list(enrollment_ids)), self._clock.now())

    @command
    def cancel_pending_suppressed(self, n: SubjectSuppressed) -> None:
        """Cancel every ``ready`` Draft of an ended Enrollment (the suppression backstop)."""
        self._cancel(drafts_repo.ready_of_ended_enrollments(), self._clock.now())

    def _cancel(self, drafts: Sequence[ReadyDraft], now: datetime) -> tuple[str, ...]:
        change = transition(DraftStatus.READY, DraftEvent.CANCEL)
        cancelled = []
        for draft in drafts:
            moved = drafts_repo.set_status(
                draft.id, from_status=str(DraftStatus.READY), to_status=str(change.to), at=now
            )
            if not moved:
                continue
            eventlog.append(
                entity=ENTITY,
                entity_id=draft.id,
                from_status=str(DraftStatus.READY),
                to_status=str(change.to),
                cause=str(change.cause),
                cause_ref=draft.enrollment_id,
                actor=ACTOR,
                at=now,
            )
            cancelled.append(draft.id)
        return tuple(cancelled)
