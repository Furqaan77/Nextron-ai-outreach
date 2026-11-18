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
  ``system:scoring``; Story 9.1: the Operator's close passes ``operator``); it returns ``True``.
  A Lead already closed (``lost`` or ``disqualified``) is left as it is and gives ``False``; an
  unknown Lead (a text that isn't a ULID included) raises ``LookupError``. Story 4.8 adds
  stopping the Lead's Enrollments (AD-4).
- ``@command close_suppressed(n: SubjectSuppressed)``: the ``SubjectSuppressed`` subscriber
  (``nextron.main.build_subscribers``). For an opt-out source (``OPT_OUT_SOURCES``:
  ``reply_rule``, ``reply_class``, ``one_click``, ``mailto``) with a ``lead_id``, it calls
  ``close(n.lead_id, lost, n.suppression_entry_id)``; the ``operator``, ``import`` and ``dsr``
  sources leave the stage unchanged. A closed Lead is a no-op, so it is idempotent.

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

**Erasure (Story 9.1, AD-17).** ``ERASURE`` declares ``leads`` ``tombstoned``: ``@command
erase_pii(n: ContactErased)`` sets the ``notes`` (PII class ``redact``) of the erased Contact's
Lead, and of every Lead merged into it (``queries.discovery.merged_lead_ids``), to ``[erased]``
when they hold any; an unknown Contact changes nothing. ``call_outcomes`` holds
no personal data (the Contact is kept as its id).
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.db import eventlog
from nextron.db.repos.pipeline import bookings as bookings_repo
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.erasure import REDACTED, Coverage, ErasureCause, TableErasure
from nextron.domain.ids import is_ulid
from nextron.domain.leads import LeadSource
from nextron.domain.machines import IllegalTransition
from nextron.domain.manual_stages import StageRefusal, StageRefused, check_deal, check_timezone
from nextron.domain.notifications import ContactErased, LeadsMerged, SubjectSuppressed
from nextron.domain.pipeline_stage import (
    AUTOMATIC_STAGES,
    CLOSED_STAGES,
    CREATION,
    MACHINE,
    OPERATOR_EVENTS,
    REOPEN,
    PipelineStage,
    StageCause,
    StageEvent,
    merged,
    reopen,
    transition,
)
from nextron.domain.suppression_entry import OPT_OUT_SOURCES
from nextron.ports.clock import Clock

ENTITY: Final = "leads"
BOOKINGS_ENTITY: Final = "call_outcomes"
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
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "leads": TableErasure(
            Coverage.TOMBSTONED,
            "the Operator's notes on the Contact's Lead (free text that may name the person) "
            "become [erased]; the rest of the Lead is the firm's",
        ),
    }
)


def _check_actor(actor: object) -> None:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor must be a non-blank str")


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
        """Pipeline's erasure hook: redact the erased Contact's Lead's notes."""
        ErasureCause(n.cause)  # a data subject request or a retention erasure
        contact = research_queries.contact(n.contact_id) if is_ulid(n.contact_id) else None
        if contact is None:
            return
        for lead_id in (contact.lead_id, *discovery_queries.merged_lead_ids(contact.lead_id)):
            stages_repo.redact_notes(lead_id, REDACTED)

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
