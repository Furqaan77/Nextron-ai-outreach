"""Public command API of the ``sequencing`` service (AD-3, AD-4, AD-16, AD-21, FR-3, FR-63;
Stories 4.8 and 4.9).

Other services and the delivery layers change ``sequencing`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Sequencing(clock)`` owns ``enrollments`` and ``variant_counters`` and applies the Enrollment
machine (``nextron.domain.enrollment``). Every status change appends one ``events`` row in the
same transaction: entity ``enrollments``, at the clock's now, actor ``operator`` for origin
``operator_pick`` and the stop causes ``operator_stop`` and ``operator_rejected``, otherwise
``system:sequencing``.

**``@command enroll(contact_id, campaign_id, origin, steps) -> NewEnrollment | EnrollRefusal``**
is the only creator of an Enrollment. It runs in the caller's transaction.

1. **Arguments.** ``contact_id`` and ``campaign_id`` must be ``str``, ``origin`` an
   ``EnrollmentOrigin`` value and ``steps`` a non-empty ``tuple`` or ``list`` of ``int`` Step
   numbers (``bool`` refused), strictly ascending, each within the Campaign's Sequence: another
   type raises ``TypeError``, a bad value ``ValueError``. An unknown Contact or Campaign, or a
   Campaign whose Sequence is missing, raises ``LookupError``; a stored Campaign that no longer
   decodes raises its ``InvalidCampaigns`` (fail closed). Nothing is written.
2. **Refusals**, checked in this order, each an ``EnrollRefusal`` with nothing written:
   ``blocked`` (``compliance_api.assert_contactable(ContactSubject(contact_id), "email",
   "cold")``, looked up at call time, answers ``Blocked``; ``blocks`` are its reasons);
   ``campaign_not_active`` (``queries.setup.campaign_active``); ``missing_tz_or_market`` (the
   Contact has no ``tz`` or no ``market_id``, AD-11); ``not_eligible``
   (``Compliance(clock).evaluate_eligibility``, with the Contact's Enrollment history; ``reasons``
   are the Decision's; the Decision is never stored); ``campaign_mismatch`` (the Lead's Segment
   or the Contact's Market isn't the Campaign's); ``lead_already_replied`` (the Lead's stage
   isn't ``new``, ``queued`` or ``contacted``); ``lead_already_enrolled`` (the Lead has a live
   Enrollment; ``uq_enrollments_one_live_per_lead`` also refuses a raw duplicate);
   ``no_active_variant``; ``no_sending_inbox`` (``queries.sending.inboxes_for_binding()`` is
   empty).
3. **Binding.** The Variant is ``next_variant(campaign.variants, last)`` from the Campaign's
   counter (round-robin in name order, skipping inactive Variants). The inbox is
   ``pick_inbox`` over the bindable inboxes, each weighed by ``capacity_forecast(its cold Daily
   Cap, its follow-ups due)`` on today's date in the ``sender_timezone`` (UTC while that is unset
   or unreadable), its live Enrollments and when it was registered. The cap is Story 6.4's
   ramped cold cap (``queries.sending.cold_daily_caps``: 0 until the inbox's warm-up is
   recorded and 14 days old). An inbox's follow-ups due that day (``_follow_ups_due``, Story
   4.9) are its live Enrollments' unsent (``ready`` or ``approved``) Step slots with ``step_no
   > 1`` due before the end of that date in the ``sender_timezone``, overdue ones included
   (``queries.sequencing.follow_ups_due``, over ``queries.drafting.follow_ups_due_before``).
   While every cap is 0 the inbox with the fewest live Enrollments wins, then the earliest
   registered.
4. **Writes**, in order: the Campaign's ``variant_counters`` row upserted; the Enrollment
   inserted ``active`` with its ``variant_id``, ``sending_inbox_id``, ``sequence_id``,
   ``sequence_version``, ``offer_id`` and ``origin`` bound for good, and one ``events`` row
   (``None`` to ``active``, cause ``enrolled``, ``cause_ref`` the Campaign);
   ``research.record_eligibility_basis(contact_id, decision, campaign_id, lia_ref, lia_date,
   now)`` with the Campaign's LIA only when ``needs_lia`` of its Market's country, else
   ``None, None``; ``Drafting(clock).request_steps(enrollment_id, steps, Urgency.SOON)`` (Story
   4.9: one ``ready`` slot per Step, Step 1 due at once), whose ``Blocked`` raises
   ``RuntimeError`` so the whole enroll rolls back (fail closed); then
   ``Enrolled(enrollment_id, lead_id)`` is published (its subscriber, ``Pipeline.on_enrolled``,
   moves a ``new`` Lead to ``queued``). No job is enqueued. It returns ``NewEnrollment``.

Only ``enroll`` has "enroll" in a def name here (the AD-16 manifest matches ``*enroll*``), and
the ``enrollments`` insert is called lexically inside it.

**``@command stop(*, cause, enrollment_id=None, lead_id=None, contact_id=None,
cause_ref=None) -> tuple[str, ...]``** stops live Enrollments: the one ``enrollment_id`` (an
unknown one raises ``LookupError``), the Lead's or every one of the Contact's; exactly one of
the three is given (otherwise ``ValueError``). ``cause`` is a ``StopCause`` value and
``cause_ref`` ``None`` or a ULID (otherwise ``ValueError``; a non-``str`` id ``TypeError``). Each
live one moves to ``stopped`` with its ``stop_cause``, its ``pause_cause`` cleared and
``ended_at`` = ``max(now, last_step_sent_at)``, so the ``ended_at >= last_step_sent_at`` CHECK
never rolls a stop back, with one ``events`` row. An ended Enrollment is left as it is, so a
repeat is a no-op. Then ``Approval(clock).cancel_pending`` cancels the stopped Enrollments'
``ready`` Drafts in the same transaction (Story 4.9). It returns the ids it stopped, oldest
first.

**Lifecycle** (Story 4.9). Each status change appends one ``events`` row; an unknown
Enrollment raises ``LookupError``, a non-``str`` id ``TypeError``.

- ``@command pause(enrollment_id, cause) -> bool``: an ``active`` Enrollment moves to
  ``paused`` with ``cause`` (a ``PauseCause`` value; actor ``operator`` for ``operator``);
  any other status is a no-op ``False``.
- ``@command resume(enrollment_id) -> bool``: a ``paused`` Enrollment moves back to
  ``active``, its ``pause_cause`` cleared (actor ``operator`` when the Operator paused it);
  any other status is a no-op ``False``, so a stopped one never resumes.
- ``@command delay_until(enrollment_id, until) -> datetime | None``: for a live Enrollment,
  the lowest-numbered ``ready`` or ``approved`` Step slot that has a ``due_at`` gets it
  recomputed as ``step_due_at(base, gap, the Contact's tz, not_before=until)`` through
  ``Drafting.set_due``: ``base`` is the previous Step's send time (``last_step_sent_at``) and
  ``gap`` the difference of the two slots' ``day_offset``, or, for the Enrollment's first
  slot, its own creation time and 0. It returns the new due time, or ``None`` (an ended
  Enrollment, no such slot, or a Contact with no ``tz``, logged). ``until`` must be a ``date``
  (not a ``datetime``): ``TypeError``.
- ``@command on_step_sent(n: MessageSent)`` (the ``MessageSent`` subscriber): the Draft
  ``n.draft_id`` must be a ``step`` or ``recontact`` slot (anything else, or an unknown one,
  is a logged no-op that never raises). The send time is the clock's now. For a live
  Enrollment, the next Step is the lowest-numbered still-pending (``ready`` or ``approved``)
  slot above the sent one (a rejected, superseded or cancelled slot is skipped). When it
  already has a ``due_at`` the message is a repeat and nothing is written. Otherwise
  ``last_step_sent_at`` becomes the later of its value and now, and the next Step gets
  ``set_due(step_due_at(now, gap, the Contact's tz))`` (a Contact with no ``tz`` leaves it
  unset, logged); with no next Step the Enrollment becomes ``completed`` (``ended_at`` = the
  send time, one event, ``cause_ref`` the Draft). A ``completed`` Enrollment writes nothing;
  a ``stopped`` one only records ``last_step_sent_at``, raising ``ended_at`` to keep the
  CHECK.

**Subscribers** (``nextron.main.build_subscribers``), each idempotent:

- ``@command stop_suppressed(n: SubjectSuppressed)``: stops, cause ``suppressed`` and
  ``cause_ref`` the entry, the named Contact's live Enrollments, then every live Enrollment whose
  Contact the gate now blocks on ``email`` for ``cold`` (a domain or shared key suppression
  reaches Contacts the notification doesn't name), cancelling their ``ready`` Drafts too.
- ``@command erase_pii(n: ContactErased)``: stops the Contact's live Enrollments, cause
  ``contact_erased``. ``sequencing``'s tables hold no personal data, so it declares no
  ``ERASURE``.
- ``@command reparent_lead(n: LeadsMerged)``: moves the merged Lead's Enrollments to the
  survivor (``Discovery.merge`` refuses a merge when both Leads have a live one); when the
  survivor is closed (``lost`` or ``disqualified``), the live one it now holds stops with
  ``lead_closed``, ``cause_ref`` the merged Lead.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.app.approval.api import Approval
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.drafting.api import Drafting
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import drafting as drafting_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.db import eventlog
from nextron.db.repos.sequencing import enrollments as enrollments_repo
from nextron.db.repos.sequencing import variant_counters as counters_repo
from nextron.db.repos.sequencing.enrollments import StoredEnrollment
from nextron.db.uow import command, current
from nextron.domain.ai import Urgency
from nextron.domain.campaign import needs_lia
from nextron.domain.contactability import Blocked, ContactSubject
from nextron.domain.draft import PENDING, STEP_KINDS
from nextron.domain.enrollment import (
    CREATION,
    LIVE_STATUSES,
    PAUSE_EVENTS,
    STOP_EVENTS,
    EnrollmentEvent,
    EnrollmentOrigin,
    EnrollmentStatus,
    EnrollRefusal,
    NewEnrollment,
    PauseCause,
    RefusalCode,
    StopCause,
    next_variant,
    transition,
)
from nextron.domain.ids import is_ulid
from nextron.domain.notifications import (
    ContactErased,
    Enrolled,
    LeadsMerged,
    MessageSent,
    SubjectSuppressed,
)
from nextron.domain.pipeline_stage import CLOSED_STAGES, PipelineStage
from nextron.domain.sending_policy import InboxLoad, capacity_forecast, pick_inbox, step_due_at
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

ENTITY: Final = "enrollments"
SYSTEM_ACTOR: Final = "system:sequencing"
OPERATOR_ACTOR: Final = "operator"
OPERATOR_STOPS: Final = frozenset({StopCause.OPERATOR_STOP, StopCause.OPERATOR_REJECTED})
ENROLLABLE_STAGES: Final = frozenset(
    {PipelineStage.NEW, PipelineStage.QUEUED, PipelineStage.CONTACTED}
)
CHANNEL: Final = "email"
PURPOSE: Final = "cold"


def _origin_of(origin: object) -> EnrollmentOrigin:
    if not isinstance(origin, str):
        raise TypeError(f"enroll takes an EnrollmentOrigin, not {type(origin).__name__}")
    try:
        return EnrollmentOrigin(origin)
    except ValueError:
        raise ValueError("enroll: the origin is unknown") from None


def _checked_steps(steps: object) -> tuple[int, ...]:
    """``steps`` as a tuple; ``TypeError`` or ``ValueError`` when it isn't Step numbers."""
    if not isinstance(steps, tuple | list):
        raise TypeError(f"enroll takes the steps as a tuple, not {type(steps).__name__}")
    if not all(isinstance(step, int) and not isinstance(step, bool) for step in steps):
        raise TypeError("enroll takes the steps as int Step numbers")
    found = tuple(steps)
    if not found:
        raise ValueError("enroll needs at least one Step")
    if any(later <= earlier for earlier, later in zip(found, found[1:], strict=False)):
        raise ValueError("enroll takes the steps in ascending order, each once")
    return found


def _stop_cause_of(cause: object) -> StopCause:
    if not isinstance(cause, str):
        raise TypeError(f"stop takes a StopCause, not {type(cause).__name__}")
    try:
        return StopCause(cause)
    except ValueError:
        raise ValueError("stop: the cause is unknown") from None


def _actor_of_origin(origin: EnrollmentOrigin) -> str:
    return OPERATOR_ACTOR if origin is EnrollmentOrigin.OPERATOR_PICK else SYSTEM_ACTOR


def _actor_of_stop(cause: StopCause) -> str:
    return OPERATOR_ACTOR if cause in OPERATOR_STOPS else SYSTEM_ACTOR


def _sender_zone() -> tzinfo:
    """The ``sender_timezone``, or UTC while it is unset or unreadable."""
    try:
        return ZoneInfo(str(setup_queries.get(Key.SENDER_TIMEZONE)))
    except (SettingNotSet, InvalidSettingValue, ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def _local_date(now: datetime) -> date:
    """The date of ``now`` in ``sender_timezone``, or in UTC while it is unset or unreadable."""
    return now.astimezone(_sender_zone()).date()


def _follow_ups_due(local_date: date) -> Mapping[str, int]:
    """Each inbox's follow-ups due on ``local_date`` in the ``sender_timezone``
    (``queries.sequencing.follow_ups_due``, Story 4.10)."""
    return sequencing_queries.follow_ups_due(local_date, _sender_zone())


def _picked_inbox(inboxes: Sequence[sending_queries.SendingInboxView], now: datetime) -> str:
    """The bindable inbox a new Enrollment binds to (``pick_inbox``)."""
    local_date = _local_date(now)
    caps = sending_queries.cold_daily_caps(local_date)
    due = _follow_ups_due(local_date)
    live = sequencing_queries.live_counts_by_inbox()
    loads = [
        InboxLoad(
            inbox_id=inbox.id,
            capacity=capacity_forecast(caps.get(inbox.id, 0), due.get(inbox.id, 0)),
            live=live.get(inbox.id, 0),
            registered_at=inbox.created_at,
        )
        for inbox in inboxes
    ]
    picked = pick_inbox(loads)
    if picked is None:  # the caller refused an empty list already
        raise LookupError("no Sending Inbox to bind")
    return picked


def _pause_cause_of(cause: object) -> PauseCause:
    if not isinstance(cause, str):
        raise TypeError(f"pause takes a PauseCause, not {type(cause).__name__}")
    try:
        return PauseCause(cause)
    except ValueError:
        raise ValueError("pause: the cause is unknown") from None


def _stored_row(row_id: object, name: str) -> StoredEnrollment:
    """The Enrollment ``row_id``; ``TypeError`` for a non-``str`` id, ``LookupError`` when it
    is unknown."""
    if not isinstance(row_id, str):
        raise TypeError(f"{name} takes the Enrollment id as str")
    found = sequencing_queries.enrollment(row_id)
    if found is None:
        raise LookupError(f"{name}: the Enrollment is unknown")
    return found


def _contact_tz(contact_id: str) -> str | None:
    contact = research_queries.contact(contact_id)
    return None if contact is None else contact.tz


def _step_slots(row_id: str) -> list[drafting_queries.DraftView]:
    """The Enrollment's ``step`` and ``recontact`` slots, by ``step_no``."""
    return [
        draft
        for draft in drafting_queries.drafts_of(row_id)
        if draft.kind in STEP_KINDS and draft.step_no is not None
    ]


def _country_of(market_id: str) -> str | None:
    found = next((market for market in setup_queries.markets() if market.id == market_id), None)
    return None if found is None else found.definition.country_code


class Sequencing:
    """Enrollments of Contacts in Campaigns; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def enroll(
        self, contact_id: str, campaign_id: str, origin: EnrollmentOrigin, steps: Sequence[int]
    ) -> NewEnrollment | EnrollRefusal:
        """Enroll the Contact ``contact_id`` in the Campaign ``campaign_id``, or refuse; see the
        module docstring."""
        if not isinstance(contact_id, str) or not isinstance(campaign_id, str):
            raise TypeError("enroll takes the Contact and Campaign ids as str")
        chosen_origin = _origin_of(origin)
        chosen_steps = _checked_steps(steps)
        contact = research_queries.contact(contact_id)
        if contact is None:
            raise LookupError("enroll: the Contact is unknown")
        campaign = setup_queries.campaign(campaign_id)
        if campaign is None:
            raise LookupError("enroll: the Campaign is unknown")
        sequence = setup_queries.sequence_by_id(campaign.sequence_id)
        if sequence is None:
            raise LookupError("enroll: the Campaign's Sequence is missing")
        if chosen_steps[-1] > len(sequence.definition.steps) or chosen_steps[0] < 1:
            raise ValueError("enroll: a Step isn't in the Campaign's Sequence")
        lead = discovery_queries.lead(contact.lead_id)
        if lead is None:  # the contacts table's foreign key keeps it
            raise LookupError("enroll: the Contact's Lead is unknown")

        # (1) the gate, a global looked up at call time (AD-16), before anything else
        answer = compliance_api.assert_contactable(ContactSubject(contact.id), CHANNEL, PURPOSE)
        if isinstance(answer, Blocked):
            return EnrollRefusal(RefusalCode.BLOCKED, blocks=answer.reasons)
        if not setup_queries.campaign_active(campaign.id):
            return EnrollRefusal(RefusalCode.CAMPAIGN_NOT_ACTIVE)
        if contact.tz is None or contact.market_id is None:
            return EnrollRefusal(RefusalCode.MISSING_TZ_OR_MARKET)
        decision = Compliance(self._clock).evaluate_eligibility(contact.id)
        if not decision.eligible:
            return EnrollRefusal(RefusalCode.NOT_ELIGIBLE, reasons=decision.reasons)
        if lead.segment_id != campaign.segment_id or contact.market_id != campaign.market_id:
            return EnrollRefusal(RefusalCode.CAMPAIGN_MISMATCH)
        if pipeline_queries.stage(lead.id) not in ENROLLABLE_STAGES:
            return EnrollRefusal(RefusalCode.LEAD_ALREADY_REPLIED)
        if sequencing_queries.live_enrollment(lead.id) is not None:
            return EnrollRefusal(RefusalCode.LEAD_ALREADY_ENROLLED)
        variant_id = next_variant(campaign.variants, counters_repo.last_variant_id(campaign.id))
        if variant_id is None:
            return EnrollRefusal(RefusalCode.NO_ACTIVE_VARIANT)
        inboxes = sending_queries.inboxes_for_binding()
        if not inboxes:
            return EnrollRefusal(RefusalCode.NO_SENDING_INBOX)

        now = self._clock.now()
        inbox_id = _picked_inbox(inboxes, now)
        counters_repo.record(campaign.id, variant_id, now)
        enrollment_id = enrollments_repo.insert(
            lead_id=lead.id,
            contact_id=contact.id,
            campaign_id=campaign.id,
            variant_id=variant_id,
            sending_inbox_id=inbox_id,
            sequence_id=sequence.id,
            sequence_version=sequence.version,
            offer_id=campaign.offer_id,
            origin=str(chosen_origin),
            status=str(CREATION.to),
            at=now,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=enrollment_id,
            from_status=None,
            to_status=str(CREATION.to),
            cause=str(CREATION.cause),
            cause_ref=campaign.id,
            actor=_actor_of_origin(chosen_origin),
            at=now,
        )
        lia = campaign.lia if needs_lia(_country_of(campaign.market_id)) else None
        Research(self._clock).record_eligibility_basis(
            contact.id,
            decision,
            campaign.id,
            None if lia is None else lia.ref,
            None if lia is None else lia.date,
            now,
        )
        slots = Drafting(self._clock).request_steps(enrollment_id, chosen_steps, Urgency.SOON)
        if isinstance(slots, Blocked):  # the gate answered Allowed above: fail closed
            raise RuntimeError("enroll: the gate blocked the Enrollment's Draft slots")
        current().publish(Enrolled(enrollment_id=enrollment_id, lead_id=lead.id))
        return NewEnrollment(
            enrollment_id=enrollment_id,
            lead_id=lead.id,
            contact_id=contact.id,
            campaign_id=campaign.id,
            variant_id=variant_id,
            sending_inbox_id=inbox_id,
        )

    @command
    def stop(
        self,
        *,
        cause: StopCause,
        enrollment_id: str | None = None,
        lead_id: str | None = None,
        contact_id: str | None = None,
        cause_ref: str | None = None,
    ) -> tuple[str, ...]:
        """Stop the live Enrollments named; the ids stopped. See the module docstring."""
        chosen = _stop_cause_of(cause)
        named = [value for value in (enrollment_id, lead_id, contact_id) if value is not None]
        if len(named) != 1:
            raise ValueError("stop takes exactly one of enrollment_id, lead_id and contact_id")
        if not isinstance(named[0], str):
            raise TypeError("stop takes the id as str")
        if not (cause_ref is None or is_ulid(cause_ref)):
            raise ValueError("stop: cause_ref must be None or a ULID")
        if enrollment_id is not None:
            found = sequencing_queries.enrollment(enrollment_id)
            if found is None:
                raise LookupError("stop: the Enrollment is unknown")
            rows = [found] if EnrollmentStatus(found.status) in LIVE_STATUSES else []
        elif lead_id is not None:
            live = sequencing_queries.live_enrollment(lead_id)
            rows = [] if live is None else [live]
        else:
            assert contact_id is not None
            rows = enrollments_repo.live_for_contact(contact_id) if is_ulid(contact_id) else []
        now = self._clock.now()
        stopped = tuple(self._stop_row(row, chosen, cause_ref, now) for row in rows)
        Approval(self._clock).cancel_pending(stopped)
        return stopped

    def _stop_row(
        self, row: StoredEnrollment, cause: StopCause, cause_ref: str | None, now: datetime
    ) -> str:
        """Move one live Enrollment to ``stopped``; its id."""
        current_status = EnrollmentStatus(row.status)
        change = transition(current_status, STOP_EVENTS[cause])
        last_step = row.last_step_sent_at
        ended_at = now if last_step is None else max(now, last_step)
        enrollments_repo.set_status(
            row.id,
            status=str(change.to),
            pause_cause=None,
            stop_cause=str(cause),
            ended_at=ended_at,
            at=now,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=row.id,
            from_status=str(current_status),
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=cause_ref,
            actor=_actor_of_stop(cause),
            at=now,
        )
        return row.id

    @command
    def stop_suppressed(self, n: SubjectSuppressed) -> None:
        """Stop the live Enrollments a suppression now blocks; see the module docstring."""
        if n.contact_id is not None:
            self.stop(
                cause=StopCause.SUPPRESSED,
                contact_id=n.contact_id,
                cause_ref=n.suppression_entry_id,
            )
        now = self._clock.now()
        stopped = []
        for row in enrollments_repo.live():
            answer = compliance_api.assert_contactable(
                ContactSubject(row.contact_id), CHANNEL, PURPOSE
            )
            if isinstance(answer, Blocked):
                stopped.append(
                    self._stop_row(row, StopCause.SUPPRESSED, n.suppression_entry_id, now)
                )
        Approval(self._clock).cancel_pending(tuple(stopped))

    @command
    def erase_pii(self, n: ContactErased) -> None:
        """Stop the erased Contact's live Enrollments (cause ``contact_erased``)."""
        self.stop(cause=StopCause.CONTACT_ERASED, contact_id=n.contact_id)

    @command
    def reparent_lead(self, n: LeadsMerged) -> None:
        """Move the merged Lead's Enrollments to the survivor; stop a moved live one when the
        survivor is closed."""
        enrollments_repo.move_lead(n.merged_lead_id, n.survivor_lead_id, self._clock.now())
        if pipeline_queries.stage(n.survivor_lead_id) in CLOSED_STAGES:
            self.stop(
                cause=StopCause.LEAD_CLOSED,
                lead_id=n.survivor_lead_id,
                cause_ref=n.merged_lead_id,
            )


    # Lifecycle (Story 4.9) ----------------------------------------------------------------------

    def _move(
        self,
        row: StoredEnrollment,
        event: EnrollmentEvent,
        *,
        pause_cause: str | None,
        ended_at: datetime | None,
        cause_ref: str | None,
        actor: str,
        now: datetime,
    ) -> None:
        """Apply ``event`` to the live Enrollment ``row`` and append its ``events`` row."""
        current_status = EnrollmentStatus(row.status)
        change = transition(current_status, event)
        enrollments_repo.set_status(
            row.id,
            status=str(change.to),
            pause_cause=pause_cause,
            stop_cause=None,
            ended_at=ended_at,
            at=now,
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=row.id,
            from_status=str(current_status),
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=cause_ref,
            actor=actor,
            at=now,
        )

    @command
    def pause(self, enrollment_id: str, cause: PauseCause) -> bool:
        """Pause an ``active`` Enrollment; whether it was paused."""
        chosen = _pause_cause_of(cause)
        row = _stored_row(enrollment_id, "pause")
        if EnrollmentStatus(row.status) is not EnrollmentStatus.ACTIVE:
            return False
        actor = OPERATOR_ACTOR if chosen is PauseCause.OPERATOR else SYSTEM_ACTOR
        self._move(
            row,
            PAUSE_EVENTS[chosen],
            pause_cause=str(chosen),
            ended_at=None,
            cause_ref=None,
            actor=actor,
            now=self._clock.now(),
        )
        return True

    @command
    def resume(self, enrollment_id: str) -> bool:
        """Resume a ``paused`` Enrollment; whether it was resumed."""
        row = _stored_row(enrollment_id, "resume")
        if EnrollmentStatus(row.status) is not EnrollmentStatus.PAUSED:
            return False
        actor = OPERATOR_ACTOR if row.pause_cause == PauseCause.OPERATOR else SYSTEM_ACTOR
        self._move(
            row,
            EnrollmentEvent.RESUME,
            pause_cause=None,
            ended_at=None,
            cause_ref=None,
            actor=actor,
            now=self._clock.now(),
        )
        return True

    @command
    def delay_until(self, enrollment_id: str, until: date) -> datetime | None:
        """Recompute the next due Step's time so it is never before ``until``; the new due
        time, or ``None``. See the module docstring."""
        if isinstance(until, datetime) or not isinstance(until, date):
            raise TypeError("delay_until takes a date")
        row = _stored_row(enrollment_id, "delay_until")
        if EnrollmentStatus(row.status) not in LIVE_STATUSES:
            return None
        slots = _step_slots(row.id)
        position = next(
            (
                index
                for index, slot in enumerate(slots)
                if slot.status in PENDING and slot.due_at is not None
            ),
            None,
        )
        if position is None:
            return None
        target = slots[position]
        tz = _contact_tz(row.contact_id)
        if tz is None:
            log.warning("delay_without_timezone", extra={"enrollment_id": row.id})
            return None
        base, gap = target.created_at, 0
        if position > 0:
            previous = slots[position - 1]
            base = row.last_step_sent_at or target.created_at
            gap = (target.day_offset or 0) - (previous.day_offset or 0)
        due = step_due_at(base, gap, tz, not_before=until)
        if not Drafting(self._clock).set_due(target.id, due):
            return None
        return due

    @command
    def on_step_sent(self, n: MessageSent) -> None:
        """Record a sent Step: the next Step's due time, or the Enrollment's completion. See
        the module docstring."""
        draft = drafting_queries.draft(n.draft_id) if isinstance(n.draft_id, str) else None
        if draft is None or draft.kind not in STEP_KINDS or draft.enrollment_id is None:
            log.info("message_sent_not_a_step", extra={"om_id": str(n.om_id)})
            return
        row = enrollments_repo.get(draft.enrollment_id)
        if row is None:  # the drafts foreign key keeps it
            log.info("message_sent_without_enrollment", extra={"draft_id": draft.id})
            return
        now = self._clock.now()
        last = row.last_step_sent_at
        sent_at = now if last is None else max(last, now)
        status = EnrollmentStatus(row.status)
        if status is EnrollmentStatus.COMPLETED:
            return  # a repeat after the last Step: nothing to record
        if status not in LIVE_STATUSES:
            ended_at = sent_at if row.ended_at is None else max(row.ended_at, sent_at)
            enrollments_repo.record_step_sent(row.id, sent_at=sent_at, ended_at=ended_at, at=now)
            return
        sent_no = draft.step_no or 0
        following = [
            slot
            for slot in _step_slots(row.id)
            if (slot.step_no or 0) > sent_no and slot.status in PENDING
        ]
        if following and following[0].due_at is not None:
            return  # a repeat: the next Step is already due
        enrollments_repo.record_step_sent(row.id, sent_at=sent_at, ended_at=None, at=now)
        if not following:
            self._move(
                row,
                EnrollmentEvent.COMPLETE,
                pause_cause=None,
                ended_at=sent_at,
                cause_ref=draft.id,
                actor=SYSTEM_ACTOR,
                now=now,
            )
            return
        upcoming = following[0]
        tz = _contact_tz(row.contact_id)
        if tz is None:
            log.warning("follow_up_without_timezone", extra={"enrollment_id": row.id})
            return
        gap = (upcoming.day_offset or 0) - (draft.day_offset or 0)
        Drafting(self._clock).set_due(upcoming.id, step_due_at(now, gap, tz))
