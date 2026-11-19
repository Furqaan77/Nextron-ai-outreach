"""Public command API of the ``pipeline`` service (AD-3, AD-4, FR-9).

Other services and the delivery layers change ``pipeline`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Pipeline(clock)`` owns the ``leads`` pipeline group (``pipeline_stage``, ``deal_value``,
``notes``) and ``call_outcomes``, and applies the Lead Pipeline Stage machine
(``nextron.domain.pipeline_stage``). Every stage change appends one ``events`` row in the same
transaction: entity ``leads``, actor ``system:pipeline`` (``close`` and the Operator's commands
take another), at the clock's now.

- ``@command record_created(lead_id, source)``: ``discovery`` calls it right after inserting a
  Lead. The stored stage must be ``new`` (otherwise, an unknown Lead included,
  ``IllegalTransition``); it appends the creation row: ``None`` to ``new``, cause
  ``CREATION[source].cause`` (``created_from_<source>``), no ``cause_ref``. A ``source`` that
  isn't a ``LeadSource`` raises ``TypeError``.
- ``@command reparent_lead(n: LeadsMerged)``: the ``LeadsMerged`` subscriber
  (``nextron.main.build_subscribers``). First every ``call_outcomes`` row of the merged Lead
  moves to the survivor (Story 9.1). Then it reads both Leads' stages and applies
  ``merged(survivor, loser)``: on a ``Transition`` it sets the survivor's stage and appends a
  row from the old stage to the new one, cause ``leads_merged``, ``cause_ref`` the merged
  Lead's id. The merged Lead keeps its stage. A Lead with no row raises ``LookupError``.
- ``@command close(lead_id, stage, cause_ref, *, cause=None, actor=None) -> bool``: closes the
  Lead at ``stage``, which must be ``lost`` or ``disqualified`` (anything else raises
  ``ValueError``, and so does a ``cause_ref`` that is neither ``None`` nor a ULID, a ``cause``
  that is neither ``None`` nor a ``StageCause``, and an ``actor`` that is neither ``None`` nor a
  non-blank ``str``). It applies ``close_lost`` or ``close_disqualified``, sets the stage and
  appends a row from the old stage, with the ``cause_ref``; the row's cause is ``cause``, else
  the machine's (``closed_lost`` or ``closed_disqualified``), and its actor ``actor``, else
  ``system:pipeline`` (Story 2.15: the ``scoring`` service passes ``segment_disqualifier`` and
  ``system:scoring``; Story 9.1: the Operator's close passes ``operator``); it returns ``True``. A
  Lead already closed (``lost`` or ``disqualified``) is left as it is and gives ``False``; an
  unknown Lead (a text that isn't a ULID included) raises ``LookupError``. Before the
  already-closed check it stops the Lead's live Enrollment (AD-4, Story 4.8):
  ``Sequencing(clock).stop(lead_id=lead_id, cause=lead_closed, cause_ref=cause_ref)``, so a
  Lead closed earlier still has nothing live.
- ``@command advance_to_at_least(lead_id, stage, cause_ref=None) -> bool`` (Story 4.8): the only
  automatic move (AD-4). ``advance(stored stage, stage)``
  (``nextron.domain.pipeline_stage``): a ``Transition`` sets the stage and appends a row from
  the old stage, with the move's cause and ``cause_ref``, and gives ``True``; ``None`` (the
  Lead at or past ``stage``, closed or parked) is a no-op with no event and gives ``False``. A
  ``stage`` with no automatic move raises ``ValueError`` (before anything is read), a
  ``cause_ref`` that is neither ``None`` nor a ULID ``ValueError``, and an unknown Lead
  ``LookupError``.
- ``@command on_enrolled(n: Enrolled)``: the ``Enrolled`` subscriber; it calls
  ``advance_to_at_least(n.lead_id, queued, n.enrollment_id)``, so a ``new`` Lead moves to
  ``queued`` (cause ``enrolled``) and any later stage stays. A repeat is a no-op.
- ``@command close_suppressed(n: SubjectSuppressed)``: the ``SubjectSuppressed`` subscriber
  (``nextron.main.build_subscribers``). For an opt-out source (``OPT_OUT_SOURCES``:
  ``reply_rule``, ``reply_class``, ``one_click``, ``mailto``) with a ``lead_id``, it calls
  ``close(n.lead_id, lost, n.suppression_entry_id)``; the ``operator``, ``import`` and ``dsr``
  sources leave the stage unchanged. A closed Lead is a no-op, so it is idempotent.
- The LinkedIn account commands are ``nextron.app.pipeline.assist_accounts.AssistAccounts``
  (Story 10.2); the Channel Permission commands
  ``nextron.app.pipeline.channel_permissions.ChannelPermissions`` (Story 10.7).

**The Operator's moves (Story 9.1, FR-45, FR-37, FR-63).** Each command takes a keyword-only
``actor`` (``operator`` from the dashboard; a value that isn't a non-blank ``str`` raises
``ValueError``), checks everything before it writes, and on a refusal raises
``nextron.domain.manual_stages.StageRefused`` with its ``StageRefusal`` reason, having written
nothing. An unknown Lead (a text that isn't a ULID included) raises ``LookupError``. Events never
hold an amount, a note, a date or a timezone.

- ``@command move(lead_id, target, *, actor) -> None``: a ``target`` (a ``PipelineStage``; any
  other type raises ``TypeError``) in ``AUTOMATIC_STAGES`` is refused ``automatic``, and
  ``call_booked`` ``booking_incomplete`` (only ``book_call`` books). ``lost`` and
  ``disqualified`` close the Lead through ``close(lead_id, target, None, actor=actor)``; a Lead
  already closed is refused ``already_closed``. Any other target applies its
  ``OPERATOR_EVENTS`` event (``illegal`` when ``TRANSITIONS`` has no such row), sets the stage
  and appends a ``leads`` row from the old stage, cause ``operator_move``.
- ``@command book_call(lead_id, contact_id, scheduled_at, prospect_tz, *, actor) -> str``:
  books (or rebooks) a discovery call and returns the new ``call_outcomes`` id. ``scheduled_at``
  must be an aware ``datetime`` (``TypeError``, naive ``ValueError``); it is stored in UTC. At
  ``call_booked`` it is a rebooking: every pending booking of the Lead is superseded
  (``superseded_at`` the clock's now, with a ``call_outcomes`` row ``booked`` to ``superseded``,
  cause ``rebooked``, each) and the stage stays. Otherwise ``book_call`` must be legal from the
  stage (else ``illegal``). The Contact must be one of the Lead's and not erased (else
  ``bad_contact``), and ``prospect_tz`` an IANA zone (``check_timezone``, else
  ``bad_timezone``). It inserts the booking (``booked_at`` the clock's now, ``booked_by``
  ``actor``) with a ``call_outcomes`` row ``None`` to ``booked``, cause ``operator_move``, then,
  unless rebooking, sets ``call_booked`` with a ``leads`` row from the old stage, cause
  ``operator_move``, ``cause_ref`` the booking's id.
- ``@command reopen(lead_id, *, actor) -> PipelineStage``: a closed Lead goes back to the stage
  it had before its latest close (``stages.last_close``: a same-stage row is no close), through
  ``pipeline_stage.reopen``, with a ``leads`` row, cause ``reopened``; it returns that stage. A
  Lead that isn't closed, or has no such row, or whose earlier stage isn't open or parked, is
  refused ``not_reopenable``. Suppression entries are never touched.
- ``@command save_deal(lead_id, deal_value, notes, *, actor) -> None``: stores both
  (``manual_stages.check_deal``: micro-dollars of whole dollars and normalized notes, ``None``
  clearing either) and appends one ``leads`` row from the current stage to itself, cause
  ``deal_details_saved``.

**The dashboard's use cases (Story 9.1).** Each runs one transaction and calls one of the
commands above with actor ``operator`` (``OPERATOR``); a ``StageRefused`` (or ``LookupError``)
leaves it rolled back and reaches the caller:

- ``@use_case operator_move(lead_id, move) -> PipelineStage``: ``move`` ``reopen`` runs
  ``reopen``; any other ``move`` must be a ``PipelineStage`` value (else ``ValueError``) and
  runs ``move``. It returns the Lead's new stage.
- ``@use_case operator_book_call(lead_id, contact_id, scheduled_at, prospect_tz) -> bool``: runs
  ``book_call``; ``True`` when it was a rebooking (the Lead was at ``call_booked``).
- ``@use_case operator_save_deal(lead_id, deal_value, notes) -> None``: runs ``save_deal``.

**Call Outcomes (Story 9.4, FR-69, FR-45).** A booking's outcome is a
``nextron.domain.call_outcomes.Outcome`` (any other type raises ``TypeError``); ``actor`` is
checked as above. Events never hold a field's value.

- ``@command record_call_outcome(booking_id, outcome, *, actor) -> None``: the booking must be
  pending (not superseded, not yet recorded; else ``ValueError``; an unknown one
  ``LookupError``). It stores the outcome with ``recorded_at`` (the clock's now) and
  ``recorded_by`` ``actor``, appends a ``call_outcomes`` row ``booked`` to ``held`` or
  ``no_show``, cause ``call_outcome``, and moves the Lead through the AD-4 machine: ``held`` at
  ``call_booked`` applies ``hold_call`` (to ``call_held``), ``no_show`` at ``call_held`` applies
  ``no_show`` (back to ``call_booked``), each with a ``leads`` row, cause ``call_outcome``,
  ``cause_ref`` the booking's id; at any other stage (a ``no_show`` at ``call_booked``, a Lead
  already past the call, a closed Lead) the stage is unchanged.
- ``@command correct_call_outcome(booking_id, outcome, *, actor) -> tuple[str, ...]``: the
  booking must be recorded and not superseded (else ``ValueError``; unknown ``LookupError``).
  It stores the new outcome (``recorded_at``/``recorded_by`` keep the first recording) and
  appends one ``call_outcomes`` row per changed field (``call_outcomes.changed_fields``, in
  ``CORRECTABLE_FIELDS`` order), from the stored outcome before to the one after, cause
  ``corrected_<field>``, no ``cause_ref``; it returns those fields. Nothing changed: nothing is
  written and it returns ``()``. The stage is never recomputed.
- ``@use_case operator_record_call_outcome(lead_id, booking_id, form) -> OutcomeSaved``:
  the dashboard's form (an ``OutcomeForm``), actor ``operator``. A booking that is unknown,
  isn't ``lead_id``'s, or is superseded raises ``NotThisLeadsBooking`` (nothing written),
  checked before the fields; then ``call_outcomes.check_outcome(form)`` may raise
  ``OutcomeRefused`` (nothing written). An unrecorded booking is
  recorded (``recorded``), a recorded one corrected (``corrected``, or ``unchanged`` when
  nothing differed).
- ``call_outcomes_due()``: a read in the caller's unit of work (no write, no job, no stored
  flag): ``queries.pipeline.call_outcomes_due(now - call_outcome_prompt_delay)``, the clock's
  now and the setting (``queries.setup.get``).
- ``lead_panel(lead_id)``: ``queries.pipeline.lead_pipeline(lead_id, now=<the clock's now>)``,
  for the Lead page's Pipeline panel and its Call Outcome form.

**Erasure (Stories 9.1, 9.4, 10.2 and 10.7, AD-17).** ``ERASURE`` declares ``leads``,
``call_outcomes`` and ``channel_permissions`` ``tombstoned`` and ``linkedin_accounts`` ``not_held`` (a Nextron staff
member's label and name, never a Contact's data). ``@command Pipeline.erase_pii(n:
ContactErased)`` is pipeline's one ``ContactErased`` subscriber, registered last
(``nextron.main.build_subscribers``); a ``cause`` that isn't an ``ErasureCause`` raises
``ValueError``. In the publisher's transaction it:

- sets the ``notes`` (PII class ``redact``) of the erased Contact's Lead, and of every Lead
  merged into it (``queries.discovery.merged_lead_ids``), to ``[erased]`` when they hold any
  (Story 9.1; an unknown Contact's Leads are left alone), and the ``reason`` and ``next_step``
  (``redact``) of every ``call_outcomes`` row of those Leads too (Story 9.4);
- turns each of the Contact's ``active`` Channel Permissions ``revoked`` (``revoked_at`` now)
  through the Channel Permission machine (event ``erase``), each with one ``events`` row (entity
  ``channel_permissions``, cause ``contact_erased``, actor ``system:pipeline``), then sets every
  row's ``whatsapp_number`` and ``instagram_handle`` (PII class ``null``) to NULL (Story 10.7).
  Research's hook tombstones the linked operator Evidence.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import pipeline as pipeline_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.sequencing.api import Sequencing
from nextron.db import eventlog
from nextron.db.repos.pipeline import bookings as bookings_repo
from nextron.db.repos.pipeline import channel_permissions as permissions_repo
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.call_outcomes import (
    CallResult,
    Outcome,
    OutcomeForm,
    changed_fields,
    check_outcome,
    correction_cause,
)
from nextron.domain.channel_permission import PermissionEvent, PermissionStatus
from nextron.domain.channel_permission import transition as permission_transition
from nextron.domain.enrollment import StopCause
from nextron.domain.erasure import REDACTED, Coverage, ErasureCause, TableErasure
from nextron.domain.ids import is_ulid
from nextron.domain.leads import LeadSource
from nextron.domain.machines import IllegalTransition
from nextron.domain.manual_stages import StageRefusal, StageRefused, check_deal, check_timezone
from nextron.domain.notifications import ContactErased, Enrolled, LeadsMerged, SubjectSuppressed
from nextron.domain.pipeline_stage import (
    AUTOMATIC_EVENTS,
    AUTOMATIC_STAGES,
    CLOSED_STAGES,
    CREATION,
    MACHINE,
    OPERATOR_EVENTS,
    REOPEN,
    PipelineStage,
    StageCause,
    StageEvent,
    advance,
    merged,
    reopen,
    transition,
)
from nextron.domain.settings_schema import Key
from nextron.domain.suppression_entry import OPT_OUT_SOURCES
from nextron.ports.clock import Clock

ENTITY: Final = "leads"
BOOKINGS_ENTITY: Final = "call_outcomes"
PERMISSIONS_ENTITY: Final = "channel_permissions"  # Story 10.7
ACTOR: Final = "system:pipeline"
OPERATOR: Final = "operator"
BOOKED: Final = "booked"
SUPERSEDED: Final = "superseded"
CLOSE_EVENTS: Final[Mapping[PipelineStage, StageEvent]] = MappingProxyType(
    {
        PipelineStage.LOST: StageEvent.CLOSE_LOST,
        PipelineStage.DISQUALIFIED: StageEvent.CLOSE_DISQUALIFIED,
    }
)
# What pipeline's erase_pii does to each of its tables with a personal-data column.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "leads": TableErasure(
            Coverage.TOMBSTONED,
            "the Operator's notes on the Contact's Lead (free text that may name the person) "
            "become [erased]; the rest of the Lead is the firm's",
        ),
        "linkedin_accounts": TableErasure(
            Coverage.NOT_HELD,
            "the LinkedIn account a Nextron staff member sends Tasks from: its label and the "
            "owner's name and role, never a Contact's data",
        ),
        "channel_permissions": TableErasure(
            Coverage.TOMBSTONED,
            "the Contact's WhatsApp number and Instagram handle become NULL and each active "
            "permission is revoked; research tombstones the linked operator Evidence",
        ),
        "call_outcomes": TableErasure(  # Story 9.4
            Coverage.TOMBSTONED,
            "the free texts of every Call Outcome of the Contact's Lead and of the Leads merged "
            "into it (why it isn't qualified, the next step) become [erased], whichever Contact "
            "the call was with; the codes, amounts and times are kept",
        ),
    }
)


class OutcomeSaved(StrEnum):
    """What ``operator_record_call_outcome`` did."""

    RECORDED = "recorded"
    CORRECTED = "corrected"
    UNCHANGED = "unchanged"


class NotThisLeadsBooking(Exception):
    """A Call Outcome posted for a booking that is unknown, another Lead's or superseded;
    nothing was written."""


def _check_actor(actor: object) -> None:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-blank str")


def _check_outcome(outcome: object) -> None:
    if not isinstance(outcome, Outcome):
        raise TypeError(f"a Call Outcome must be an Outcome, not {type(outcome).__name__}")


class Pipeline:
    """The Lead Pipeline Stage; see the module docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @command
    def record_created(self, lead_id: str, source: LeadSource) -> None:
        """Append the creation ``events`` row of the new Lead ``lead_id``."""
        if not isinstance(source, LeadSource):
            raise TypeError(f"record_created takes a LeadSource, not {type(source).__name__}")
        change = CREATION[source]
        stored = stages_repo.stage(lead_id)
        if stored != PipelineStage.NEW:
            raise IllegalTransition(MACHINE, change.cause, stored)
        eventlog.append(
            entity=ENTITY,
            entity_id=lead_id,
            from_status=None,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=None,
            actor=ACTOR,
            at=self._clock.now(),
        )

    @command
    def reparent_lead(self, n: LeadsMerged) -> None:
        """Give the survivor of a merge the merged Lead's bookings and the more advanced stage
        of the two."""
        survivor = stages_repo.stage(n.survivor_lead_id)
        loser = stages_repo.stage(n.merged_lead_id)
        if survivor is None or loser is None:
            raise LookupError("reparent_lead: a merged Lead has no row")
        bookings_repo.reparent(n.survivor_lead_id, n.merged_lead_id)
        change = merged(PipelineStage(survivor), PipelineStage(loser))
        if change is None:
            return
        stages_repo.set_stage(n.survivor_lead_id, str(change.to))
        eventlog.append(
            entity=ENTITY,
            entity_id=n.survivor_lead_id,
            from_status=survivor,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=n.merged_lead_id,
            actor=ACTOR,
            at=self._clock.now(),
        )

    @command
    def close(
        self,
        lead_id: str,
        stage: PipelineStage,
        cause_ref: str | None,
        *,
        cause: StageCause | None = None,
        actor: str | None = None,
    ) -> bool:
        """Close the Lead ``lead_id`` at ``stage`` (``lost`` or ``disqualified``); ``False``
        when it is closed already. See the module docstring."""
        try:
            event = CLOSE_EVENTS.get(stage)
        except TypeError:  # an unhashable argument is no stage
            event = None
        if event is None:
            raise ValueError("close takes the stage lost or disqualified")
        if not (cause_ref is None or is_ulid(cause_ref)):
            raise ValueError("close: cause_ref must be None or a ULID")
        if cause is not None and not isinstance(cause, StageCause):
            raise ValueError("close: cause must be None or a StageCause")
        if actor is not None and (not isinstance(actor, str) or not actor.strip()):
            raise ValueError("close: actor must be None or a non-blank str")
        stored = stages_repo.stage(lead_id) if is_ulid(lead_id) else None
        if stored is None:
            raise LookupError("close: the Lead is unknown")
        Sequencing(self._clock).stop(
            lead_id=lead_id, cause=StopCause.LEAD_CLOSED, cause_ref=cause_ref
        )
        if stored in CLOSED_STAGES:
            return False
        change = transition(PipelineStage(stored), event)
        stages_repo.set_stage(lead_id, str(change.to))
        eventlog.append(
            entity=ENTITY,
            entity_id=lead_id,
            from_status=stored,
            to_status=str(change.to),
            cause=str(change.cause if cause is None else cause),
            cause_ref=cause_ref,
            actor=ACTOR if actor is None else actor,
            at=self._clock.now(),
        )
        return True

    @command
    def close_suppressed(self, n: SubjectSuppressed) -> None:
        """Close the Lead ``lost`` when an opt-out suppressed one of its Contacts."""
        if n.lead_id is None or n.source not in OPT_OUT_SOURCES:
            return
        self.close(n.lead_id, PipelineStage.LOST, n.suppression_entry_id)

    @command
    def advance_to_at_least(
        self, lead_id: str, stage: PipelineStage, cause_ref: str | None = None
    ) -> bool:
        """Move the Lead ``lead_id`` up to ``stage`` when it is behind it; ``False`` for a
        no-op. See the module docstring."""
        try:
            automatic = stage in AUTOMATIC_EVENTS
        except TypeError:  # an unhashable argument is no stage
            automatic = False
        if not automatic:
            raise ValueError("advance_to_at_least: the stage has no automatic move")
        if not (cause_ref is None or is_ulid(cause_ref)):
            raise ValueError("advance_to_at_least: cause_ref must be None or a ULID")
        stored = stages_repo.stage(lead_id) if is_ulid(lead_id) else None
        if stored is None:
            raise LookupError("advance_to_at_least: the Lead is unknown")
        change = advance(PipelineStage(stored), PipelineStage(stage))
        if change is None:
            return False
        stages_repo.set_stage(lead_id, str(change.to))
        eventlog.append(
            entity=ENTITY,
            entity_id=lead_id,
            from_status=stored,
            to_status=str(change.to),
            cause=str(change.cause),
            cause_ref=cause_ref,
            actor=ACTOR,
            at=self._clock.now(),
        )
        return True

    @command
    def on_enrolled(self, n: Enrolled) -> None:
        """Move a newly enrolled Lead to ``queued``."""
        self.advance_to_at_least(n.lead_id, PipelineStage.QUEUED, n.enrollment_id)

    # Story 9.1: the Operator's moves -------------------------------------------------------------

    def _stored(self, lead_id: str) -> PipelineStage:
        stored = stages_repo.stage(lead_id) if is_ulid(lead_id) else None
        if stored is None:
            raise LookupError("the Lead is unknown")
        return PipelineStage(stored)

    def _stage_row(
        self,
        lead_id: str,
        stages: tuple[PipelineStage, PipelineStage],
        cause: StageCause,
        actor: str,
        cause_ref: str | None = None,
    ) -> None:
        eventlog.append(
            entity=ENTITY,
            entity_id=lead_id,
            from_status=str(stages[0]),
            to_status=str(stages[1]),
            cause=str(cause),
            cause_ref=cause_ref,
            actor=actor,
            at=self._clock.now(),
        )

    @command
    def move(self, lead_id: str, target: PipelineStage, *, actor: str) -> None:
        """Move the Lead to ``target`` by hand; see the module docstring."""
        if not isinstance(target, PipelineStage):
            raise TypeError(f"move takes a PipelineStage, not {type(target).__name__}")
        _check_actor(actor)
        if target in AUTOMATIC_STAGES:
            raise StageRefused(StageRefusal.AUTOMATIC)
        if target is PipelineStage.CALL_BOOKED:
            raise StageRefused(StageRefusal.BOOKING_INCOMPLETE)
        stored = self._stored(lead_id)
        if target in CLOSED_STAGES:
            if stored in CLOSED_STAGES:
                raise StageRefused(StageRefusal.ALREADY_CLOSED)
            self.close(lead_id, target, None, actor=actor)
            return
        try:
            change = transition(stored, OPERATOR_EVENTS[target])
        except IllegalTransition:
            raise StageRefused(StageRefusal.ILLEGAL) from None
        stages_repo.set_stage(lead_id, str(change.to))
        self._stage_row(lead_id, (stored, change.to), change.cause, actor)

    @command
    def book_call(
        self,
        lead_id: str,
        contact_id: str,
        scheduled_at: datetime,
        prospect_tz: str,
        *,
        actor: str,
    ) -> str:
        """Book (or rebook) the Lead's discovery call; see the module docstring."""
        if not isinstance(scheduled_at, datetime):
            raise TypeError(f"scheduled_at must be a datetime, not {type(scheduled_at).__name__}")
        if scheduled_at.utcoffset() is None:
            raise ValueError("scheduled_at must be aware")
        _check_actor(actor)
        stored = self._stored(lead_id)
        change = None
        if stored is not PipelineStage.CALL_BOOKED:  # otherwise a rebooking
            try:
                change = transition(stored, StageEvent.BOOK_CALL)
            except IllegalTransition:
                raise StageRefused(StageRefusal.ILLEGAL) from None
        contact = research_queries.contact(contact_id) if is_ulid(contact_id) else None
        if contact is None or contact.lead_id != lead_id or contact.erased_at is not None:
            raise StageRefused(StageRefusal.BAD_CONTACT)
        check_timezone(prospect_tz)
        now = self._clock.now()
        if change is None:
            for pending in bookings_repo.pending(lead_id):
                bookings_repo.supersede(pending.id, now)
                eventlog.append(
                    entity=BOOKINGS_ENTITY,
                    entity_id=pending.id,
                    from_status=BOOKED,
                    to_status=SUPERSEDED,
                    cause=str(StageCause.REBOOKED),
                    cause_ref=None,
                    actor=actor,
                    at=now,
                )
        booking_id = bookings_repo.insert(
            lead_id=lead_id,
            contact_id=contact_id,
            scheduled_at=scheduled_at.astimezone(UTC),
            prospect_tz=prospect_tz,
            booked_at=now,
            booked_by=actor,
        )
        eventlog.append(
            entity=BOOKINGS_ENTITY,
            entity_id=booking_id,
            from_status=None,
            to_status=BOOKED,
            cause=str(StageCause.OPERATOR_MOVE),
            cause_ref=None,
            actor=actor,
            at=now,
        )
        if change is not None:
            stages_repo.set_stage(lead_id, str(change.to))
            self._stage_row(lead_id, (stored, change.to), change.cause, actor, booking_id)
        return booking_id

    @command
    def reopen(self, lead_id: str, *, actor: str) -> PipelineStage:
        """Reopen a closed Lead at the stage it had before; see the module docstring."""
        _check_actor(actor)
        stored = self._stored(lead_id)
        if stored not in CLOSED_STAGES:
            raise StageRefused(StageRefusal.NOT_REOPENABLE)
        closing = stages_repo.last_close(lead_id)
        if closing is None or closing.from_status is None:
            raise StageRefused(StageRefusal.NOT_REOPENABLE)
        try:
            change = reopen(stored, PipelineStage(closing.from_status))
        except (IllegalTransition, ValueError):
            raise StageRefused(StageRefusal.NOT_REOPENABLE) from None
        stages_repo.set_stage(lead_id, str(change.to))
        self._stage_row(lead_id, (stored, change.to), change.cause, actor)
        return change.to

    @command
    def save_deal(
        self, lead_id: str, deal_value: int | None, notes: str | None, *, actor: str
    ) -> None:
        """Store the Lead's deal value and notes; see the module docstring."""
        check_deal(deal_value, notes)
        _check_actor(actor)
        stored = self._stored(lead_id)
        stages_repo.set_deal(lead_id, deal_value, notes)
        self._stage_row(lead_id, (stored, stored), StageCause.DEAL_DETAILS_SAVED, actor)

    @command
    def erase_pii(self, n: ContactErased) -> None:
        """Pipeline's erasure hook (Stories 9.1, 9.4 and 10.7), the last ``ContactErased``
        subscriber: redact the erased Contact's Lead's notes, then revoke and clear its Channel
        Permissions; see the module docstring."""
        ErasureCause(n.cause)  # a data subject request or a retention erasure
        if not is_ulid(n.contact_id):
            return
        contact = research_queries.contact(n.contact_id)
        if contact is not None:
            lead_ids = (contact.lead_id, *discovery_queries.merged_lead_ids(contact.lead_id))
            for lead_id in lead_ids:
                stages_repo.redact_notes(lead_id, REDACTED)
            bookings_repo.redact_text(lead_ids, REDACTED)  # Story 9.4
        now = self._clock.now()
        for permission in permissions_repo.for_contact(n.contact_id):
            if permission.status != PermissionStatus.ACTIVE.value:
                continue
            change = permission_transition(PermissionStatus.ACTIVE, PermissionEvent.ERASE)
            permissions_repo.revoke(permission.id, now)
            eventlog.append(
                entity=PERMISSIONS_ENTITY,
                entity_id=permission.id,
                from_status=permission.status,
                to_status=change.to.value,
                cause=change.cause.value,
                cause_ref=None,
                actor=ACTOR,
                at=now,
            )
        permissions_repo.clear_identifiers(n.contact_id, now)

    # Story 9.4: Call Outcomes ---------------------------------------------------------------------

    def _booking(self, booking_id: str) -> bookings_repo.StoredBooking:
        booking = bookings_repo.get(booking_id) if is_ulid(booking_id) else None
        if booking is None:
            raise LookupError("the booking is unknown")
        return booking

    @command
    def record_call_outcome(self, booking_id: str, outcome: Outcome, *, actor: str) -> None:
        """Record a pending booking's outcome and move the stage; see the module docstring."""
        _check_outcome(outcome)
        _check_actor(actor)
        booking = self._booking(booking_id)
        if booking.superseded_at is not None or booking.recorded_at is not None:
            raise ValueError("record_call_outcome: the booking isn't pending")
        stored = self._stored(booking.lead_id)
        change = None
        if outcome.outcome is CallResult.HELD and stored is PipelineStage.CALL_BOOKED:
            change = transition(stored, StageEvent.HOLD_CALL)
        elif outcome.outcome is CallResult.NO_SHOW and stored is PipelineStage.CALL_HELD:
            change = transition(stored, StageEvent.NO_SHOW)
        now = self._clock.now()
        bookings_repo.record(booking_id, outcome, now, actor)
        eventlog.append(
            entity=BOOKINGS_ENTITY,
            entity_id=booking_id,
            from_status=BOOKED,
            to_status=str(outcome.outcome),
            cause=str(StageCause.CALL_OUTCOME),
            cause_ref=None,
            actor=actor,
            at=now,
        )
        if change is not None:
            stages_repo.set_stage(booking.lead_id, str(change.to))
            self._stage_row(booking.lead_id, (stored, change.to), change.cause, actor, booking_id)

    @command
    def correct_call_outcome(
        self, booking_id: str, outcome: Outcome, *, actor: str
    ) -> tuple[str, ...]:
        """Correct a recorded booking's outcome; see the module docstring."""
        _check_outcome(outcome)
        _check_actor(actor)
        booking = self._booking(booking_id)
        before = booking.result
        if booking.superseded_at is not None or before is None:
            raise ValueError("correct_call_outcome: the booking has no current outcome")
        changed = changed_fields(before, outcome)
        if not changed:
            return ()
        now = self._clock.now()
        bookings_repo.update_outcome(booking_id, outcome)
        for field in changed:
            eventlog.append(
                entity=BOOKINGS_ENTITY,
                entity_id=booking_id,
                from_status=str(before.outcome),
                to_status=str(outcome.outcome),
                cause=correction_cause(field),
                cause_ref=None,
                actor=actor,
                at=now,
            )
        return changed

    def call_outcomes_due(self) -> tuple[pipeline_queries.DueBooking, ...]:
        """The bookings whose Call Outcome is due now; see the module docstring."""
        delay = setup_queries.get(Key.CALL_OUTCOME_PROMPT_DELAY)
        return pipeline_queries.call_outcomes_due(self._clock.now() - delay)

    def lead_panel(self, lead_id: str) -> pipeline_queries.LeadPipeline | None:
        """The Lead's Pipeline panel facts at the clock's now; see the module docstring."""
        return pipeline_queries.lead_pipeline(lead_id, now=self._clock.now())

    # Story 9.1: the dashboard's use cases -------------------------------------------------------

    @use_case
    def operator_move(self, lead_id: str, move: str) -> PipelineStage:
        """The Pipeline panel's move (a stage, or ``reopen``); returns the new stage."""
        with current().transaction():
            if move == REOPEN:
                return self.reopen(lead_id, actor=OPERATOR)
            target = PipelineStage(move)
            self.move(lead_id, target, actor=OPERATOR)
            return target

    @use_case
    def operator_book_call(
        self, lead_id: str, contact_id: str, scheduled_at: datetime, prospect_tz: str
    ) -> bool:
        """The Pipeline panel's call form; ``True`` when it rebooked."""
        with current().transaction():
            rebooking = stages_repo.stage(lead_id) == PipelineStage.CALL_BOOKED
            self.book_call(lead_id, contact_id, scheduled_at, prospect_tz, actor=OPERATOR)
            return rebooking

    @use_case
    def operator_save_deal(self, lead_id: str, deal_value: int | None, notes: str | None) -> None:
        """The Pipeline panel's deal form."""
        with current().transaction():
            self.save_deal(lead_id, deal_value, notes, actor=OPERATOR)

    @use_case
    def operator_record_call_outcome(
        self, lead_id: str, booking_id: str, form: OutcomeForm
    ) -> OutcomeSaved:
        """The Pipeline panel's Call Outcome form: records or corrects the booking's outcome.
        The booking is checked before the form's fields, so a foreign booking is never a field
        refusal."""
        if not isinstance(form, OutcomeForm):
            raise TypeError(f"the form must be an OutcomeForm, not {type(form).__name__}")
        with current().transaction():
            booking = bookings_repo.get(booking_id) if is_ulid(booking_id) else None
            if booking is None or booking.lead_id != lead_id or booking.superseded_at is not None:
                raise NotThisLeadsBooking
            outcome = check_outcome(form)  # OutcomeRefused before any write
            if booking.recorded_at is None:
                self.record_call_outcome(booking_id, outcome, actor=OPERATOR)
                return OutcomeSaved.RECORDED
            changed = self.correct_call_outcome(booking_id, outcome, actor=OPERATOR)
            return OutcomeSaved.CORRECTED if changed else OutcomeSaved.UNCHANGED
