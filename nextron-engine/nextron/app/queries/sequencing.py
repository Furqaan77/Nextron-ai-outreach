"""Read API of the ``sequencing`` service (AD-3, AD-21, FR-63; Story 4.8).

Import rule: ``nextron.domain``, ``nextron.db.repos.sequencing`` and
``nextron.app.queries.drafting``, plus (Story 8.11) ``nextron.app.queries.pipeline`` and
``nextron.app.queries.research``, only. All read through the current unit of work, read-only
ones included.

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
  any (``pick_inbox`` spreads new Enrollments with it);
- ``follow_ups_due(local_date, zone) -> Mapping[str, int]`` (Story 4.10; moved from Story 4.9's
  ``sequencing`` service): ``{sending_inbox_id: n}`` for each inbox with any follow-ups due on
  ``local_date``, overdue ones included: its live Enrollments' unsent (``ready`` or
  ``approved``) Step slots with ``step_no > 1`` due before the end of that date in ``zone``
  (``queries.drafting.follow_ups_due_before``). Both ``pick_inbox``'s weighing and the
  "Ready to draft" capacity read it.
- **Restarts (Story 8.11, FR-63).** ``StoredEnrollment.restart_event_id`` is the Event a
  ``restart`` Enrollment restarted on.

  - ``restart_facts(lead_id) -> domain.restart.RestartFacts``: the Lead's latest Enrollment
    (the last of ``enrollments_of_lead``: by ``created_at``, then id; ``None`` without one),
    its stage (``queries.pipeline.stage``), whether it has a live Enrollment, its Signals
    (``queries.research.event_facts``) and ``restarted``: whether any of the Lead's
    Enrollments has origin ``restart`` (a Lead restarts once, Story 8.11 loop 2).
  - ``restart_candidate(lead_id, now) -> RestartCandidate | None``: the Lead's restart, when
    ``domain.restart.restart_event`` gives one: ``RestartCandidate(lead_id, contact_id,
    campaign_id, enrollment_id, ended_at, last_step_sent_at, event_id, event_age)``, the
    completed Enrollment's Contact, Campaign, id and times, the restart Event and its
    ``signals.age_days`` at ``now``. ``None`` for a text that isn't a ULID.
  - ``restart_candidates(now) -> tuple[RestartCandidate, ...]``: ``restart_candidate`` of each
    Lead with a ``completed`` Enrollment that sent a Step, by Lead id (Story 8.9's
    ``daily_selection`` reads it through ``Selection.restart_candidates``).
  - ``restart_context(enrollment_id) -> RestartContext | None``: for a ``restart``
    Enrollment, ``RestartContext(enrollment_id, previous_enrollment_id, ended_at, sent_steps,
    event_id)``: the Lead's Enrollment created just before it, when that one ended, each of its
    ``sent`` Step Drafts as ``SentStep(step_no, sent_at)`` by Step (``sent_at`` the Draft's
    ``status_at``), and the restart Event. ``None`` for any other Enrollment, an unknown one or
    a restart with no earlier ended Enrollment. Step 1 drafting (4.15), the review card (Epic 5)
    and the Lead page read it.
- ``export_pii(contact_id) -> {"enrollments": tuple[dict, ...]}`` (Story 3.15's subject access
  exporter, ``nextron.domain.contact_export``; added at the merge of Story 4.8): every column of
  each of the Contact's Enrollments (Campaign, Variant, Sending Inbox, Sequence, Offer, origin,
  status, pause and stop causes and dates), oldest first; none for a text that isn't a ULID.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from types import MappingProxyType

from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.db.repos.sequencing import enrollments as enrollments_repo
from nextron.db.repos.sequencing.enrollments import StoredEnrollment
from nextron.domain.draft import DraftStatus
from nextron.domain.eligibility import EndedSequence
from nextron.domain.enrollment import (
    ENDED_STATUSES,
    REPLY_CAUSES,
    EnrollmentOrigin,
    EnrollmentStatus,
    StopCause,
)
from nextron.domain.ids import is_ulid
from nextron.domain.restart import LatestEnrollment, RestartFacts, restart_event
from nextron.domain.signals import age_days


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


# Restarts (Story 8.11) --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestartCandidate:
    """A Lead whose completed Sequence may restart, on its restart Event."""

    lead_id: str
    contact_id: str
    campaign_id: str
    enrollment_id: str
    ended_at: datetime
    last_step_sent_at: datetime
    event_id: str
    event_age: int


@dataclass(frozen=True, slots=True)
class SentStep:
    """A Step the earlier Sequence sent, and when."""

    step_no: int
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class RestartContext:
    """What a restart Enrollment restarted: the earlier Sequence and the new Event."""

    enrollment_id: str
    previous_enrollment_id: str
    ended_at: datetime
    sent_steps: tuple[SentStep, ...]
    event_id: str


def _latest(rows: tuple[StoredEnrollment, ...]) -> LatestEnrollment | None:
    if not rows:
        return None
    row = rows[-1]
    return LatestEnrollment(
        enrollment_id=row.id,
        contact_id=row.contact_id,
        campaign_id=row.campaign_id,
        origin=row.origin,
        status=row.status,
        stop_cause=row.stop_cause,
        last_step_sent_at=row.last_step_sent_at,
        ended_at=row.ended_at,
    )


def restart_facts(lead_id: str) -> RestartFacts:
    """What ``domain.restart.restart_event`` reads of the Lead; see the module docstring."""
    stage = pipeline_queries.stage(lead_id)
    rows = enrollments_of_lead(lead_id)
    return RestartFacts(
        latest=_latest(rows),
        stage=None if stage is None else str(stage),
        live_enrollment=live_enrollment(lead_id) is not None,
        events=research_queries.event_facts(lead_id),
        restarted=any(row.origin == EnrollmentOrigin.RESTART for row in rows),
    )


def restart_candidate(lead_id: str, now: datetime) -> RestartCandidate | None:
    """The Lead's restart at ``now``, or ``None``; see the module docstring."""
    if not is_ulid(lead_id):
        return None
    facts = restart_facts(lead_id)
    event_id = restart_event(facts, now)
    latest = facts.latest
    if event_id is None or latest is None:
        return None
    if latest.ended_at is None or latest.last_step_sent_at is None:  # restart_event checks it
        return None
    event = next(each for each in facts.events if each.signal_id == event_id)
    return RestartCandidate(
        lead_id=lead_id,
        contact_id=latest.contact_id,
        campaign_id=latest.campaign_id,
        enrollment_id=latest.enrollment_id,
        ended_at=latest.ended_at,
        last_step_sent_at=latest.last_step_sent_at,
        event_id=event_id,
        event_age=age_days(event.event_date, event.first_seen_at, now),
    )


def restart_candidates(now: datetime) -> tuple[RestartCandidate, ...]:
    """Every Lead's restart at ``now``, by Lead id; see the module docstring."""
    found = (restart_candidate(lead, now) for lead in enrollments_repo.leads_with_completed())
    return tuple(candidate for candidate in found if candidate is not None)


def restart_context(enrollment_id: str) -> RestartContext | None:
    """What the ``restart`` Enrollment restarted, or ``None``; see the module docstring."""
    row = enrollment(enrollment_id)
    if row is None or row.origin != EnrollmentOrigin.RESTART or row.restart_event_id is None:
        return None
    rows = enrollments_of_lead(row.lead_id)
    position = next(index for index, each in enumerate(rows) if each.id == row.id)
    if position == 0:
        return None
    previous = rows[position - 1]
    if EnrollmentStatus(previous.status) not in ENDED_STATUSES or previous.ended_at is None:
        return None
    sent = [
        SentStep(draft.step_no, draft.status_at)
        for draft in drafting_queries.drafts_of(previous.id)
        if draft.status is DraftStatus.SENT
        and draft.step_no is not None
        and draft.status_at is not None
    ]
    return RestartContext(
        enrollment_id=row.id,
        previous_enrollment_id=previous.id,
        ended_at=previous.ended_at,
        sent_steps=tuple(sorted(sent, key=lambda step: step.step_no)),
        event_id=row.restart_event_id,
    )


def export_pii(contact_id: str) -> dict[str, tuple[dict[str, object], ...]]:
    """The Contact's Enrollments, every column; see the module docstring."""
    rows = enrollments_repo.for_export(contact_id) if is_ulid(contact_id) else ()
    return {"enrollments": rows}
