"""Read API of the ``sequencing`` service (AD-3, AD-21, FR-63; Story 4.8).

Import rule: ``nextron.domain`` and ``nextron.db.repos.sequencing`` only. All read through the
current unit of work, read-only ones included.

- ``enrollment(enrollment_id)``: the Enrollment as a ``StoredEnrollment``, or ``None`` (a text
  that isn't a ULID included);
- ``live_enrollment(lead_id)``: the Lead's ``active`` or ``paused`` Enrollment, or ``None``;
- ``enrollments_of_lead(lead_id)`` and ``enrollments_of_contact(contact_id)`` (Story 4.12):
  every Enrollment of the Lead or the Contact, whatever its status, oldest first (``()`` for a
  text that isn't a ULID);
- ``ended_sequences(contact_id) -> tuple[EndedSequence, ...]``: the Contact's Enrollment
  history for FR-63 (``compliance.evaluate_eligibility``): one
  ``EndedSequence(last_step_at, ended_at, replied)`` per ``stopped`` or ``completed``
  Enrollment that sent a Step, ordered by ``ended_at``; ``replied`` is true exactly when it
  stopped with ``reply`` or ``manual_reply``. An Enrollment that never sent a Step isn't
  history: a Contact never emailed hasn't been contacted. A text that isn't a ULID gives ``()``.
- ``live_counts_by_inbox()``: ``{sending_inbox_id: live Enrollments}`` for each inbox that has
  any (``pick_inbox`` spreads new Enrollments with it).
"""

from collections.abc import Mapping
from types import MappingProxyType

from nextron.db.repos.sequencing import enrollments as enrollments_repo
from nextron.db.repos.sequencing.enrollments import StoredEnrollment
from nextron.domain.eligibility import EndedSequence
from nextron.domain.enrollment import REPLY_CAUSES, StopCause
from nextron.domain.ids import is_ulid


def enrollment(enrollment_id: str) -> StoredEnrollment | None:
    """The Enrollment ``enrollment_id``, or ``None``."""
    if not is_ulid(enrollment_id):
        return None
    return enrollments_repo.get(enrollment_id)


def live_enrollment(lead_id: str) -> StoredEnrollment | None:
    """The Lead's ``active`` or ``paused`` Enrollment, or ``None``."""
    if not is_ulid(lead_id):
        return None
    return enrollments_repo.live_for_lead(lead_id)


def enrollments_of_lead(lead_id: str) -> tuple[StoredEnrollment, ...]:
    """Every Enrollment of the Lead, oldest first."""
    if not is_ulid(lead_id):
        return ()
    return tuple(enrollments_repo.for_lead(lead_id))


def enrollments_of_contact(contact_id: str) -> tuple[StoredEnrollment, ...]:
    """Every Enrollment of the Contact, oldest first."""
    if not is_ulid(contact_id):
        return ()
    return tuple(enrollments_repo.for_contact(contact_id))


def _replied(stop_cause: str | None) -> bool:
    return stop_cause is not None and StopCause(stop_cause) in REPLY_CAUSES


def ended_sequences(contact_id: str) -> tuple[EndedSequence, ...]:
    """The Contact's ended Sequences that sent a Step, by ``ended_at``."""
    if not is_ulid(contact_id):
        return ()
    found = []
    for row in enrollments_repo.ended_with_step(contact_id):
        # the CHECKs keep both set for an ended row that sent a Step
        if row.last_step_sent_at is None or row.ended_at is None:
            raise RuntimeError("ended_sequences: an ended Enrollment lacks its times")
        found.append(EndedSequence(row.last_step_sent_at, row.ended_at, _replied(row.stop_cause)))
    return tuple(found)


def live_counts_by_inbox() -> Mapping[str, int]:
    """The number of live Enrollments bound to each Sending Inbox that has any."""
    return MappingProxyType(enrollments_repo.live_counts_by_inbox())
