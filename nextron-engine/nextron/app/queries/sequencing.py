"""Read API of the ``sequencing`` service (AD-3, AD-21, FR-63; Story 4.8).

Import rule: ``nextron.domain``, ``nextron.db.repos.sequencing`` and
``nextron.app.queries.drafting`` only. All read through the current unit of work, read-only ones
included.

- ``enrollment(enrollment_id)``: the Enrollment as a ``StoredEnrollment``, or ``None`` (a text
  that isn't a ULID included);
- ``live_enrollment(lead_id)``: the Lead's ``active`` or ``paused`` Enrollment, or ``None``;
- ``ended_sequences(contact_id) -> tuple[EndedSequence, ...]``: the Contact's Enrollment
  history for FR-63 (``compliance.evaluate_eligibility``): one
  ``EndedSequence(last_step_at, ended_at, replied)`` per ``stopped`` or ``completed``
  Enrollment that sent a Step, ordered by ``ended_at``; ``replied`` is true exactly when it
  stopped with ``reply`` or ``manual_reply``. An Enrollment that never sent a Step isn't
  history: a Contact never emailed hasn't been contacted. A text that isn't a ULID gives ``()``.
- ``live_counts_by_inbox()``: ``{sending_inbox_id: live Enrollments}`` for each inbox that has
  any (``pick_inbox`` spreads new Enrollments with it);
- ``follow_ups_due(local_date, zone) -> Mapping[str, int]`` (Story 4.10; moved from Story 4.9's
  ``sequencing`` service): ``{sending_inbox_id: n}`` for each inbox with any follow-ups due on
  ``local_date``, overdue ones included: its live Enrollments' unsent (``ready`` or
  ``approved``) Step slots with ``step_no > 1`` due before the end of that date in ``zone``
  (``queries.drafting.follow_ups_due_before``). Both ``pick_inbox``'s weighing and the
  "Ready to draft" capacity read it.
- ``export_pii(contact_id) -> {"enrollments": tuple[dict, ...]}`` (Story 3.15's subject access
  exporter, ``nextron.domain.contact_export``; added at the merge of Story 4.8): every column of
  each of the Contact's Enrollments (Campaign, Variant, Sending Inbox, Sequence, Offer, origin,
  status, pause and stop causes and dates), oldest first; none for a text that isn't a ULID.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from types import MappingProxyType

from nextron.app.queries import drafting as drafting_queries
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


def follow_ups_due(local_date: date, zone: tzinfo) -> Mapping[str, int]:
    """Each inbox's follow-ups due on ``local_date`` in ``zone``, overdue ones included; see
    the module docstring."""
    if isinstance(local_date, datetime) or not isinstance(local_date, date):
        raise TypeError("follow_ups_due takes a date")
    if not isinstance(zone, tzinfo):
        raise TypeError("follow_ups_due takes a tzinfo")
    end = datetime.combine(local_date + timedelta(days=1), time(), tzinfo=zone)
    due = drafting_queries.follow_ups_due_before(end.astimezone(UTC))
    found: dict[str, int] = {}
    for row in enrollments_repo.live():
        count = due.get(row.id, 0)
        if count:
            found[row.sending_inbox_id] = found.get(row.sending_inbox_id, 0) + count
    return MappingProxyType(found)


def export_pii(contact_id: str) -> dict[str, tuple[dict[str, object], ...]]:
    """The Contact's Enrollments, every column; see the module docstring."""
    rows = enrollments_repo.for_export(contact_id) if is_ulid(contact_id) else ()
    return {"enrollments": rows}
