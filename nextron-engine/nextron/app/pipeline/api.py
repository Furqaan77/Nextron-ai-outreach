"""Public command API of the ``pipeline`` service (AD-3, AD-4, FR-9).

Other services and the delivery layers change ``pipeline`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Pipeline(clock)`` owns ``leads.pipeline_stage`` and applies the Lead Pipeline Stage machine
(``nextron.domain.pipeline_stage``). Every stage change appends one ``events`` row in the same
transaction: entity ``leads``, actor ``system:pipeline``, at the clock's now.

- ``@command record_created(lead_id, source)``: ``discovery`` calls it right after inserting a
  Lead. The stored stage must be ``new`` (otherwise, an unknown Lead included,
  ``IllegalTransition``); it appends the creation row: ``None`` to ``new``, cause
  ``CREATION[source].cause`` (``created_from_<source>``), no ``cause_ref``. A ``source`` that
  isn't a ``LeadSource`` raises ``TypeError``.
- ``@command reparent_lead(n: LeadsMerged)``: the ``LeadsMerged`` subscriber
  (``nextron.main.build_subscribers``). It reads both Leads' stages and applies
  ``merged(survivor, loser)``: on a ``Transition`` it sets the survivor's stage and appends a
  row from the old stage to the new one, cause ``leads_merged``, ``cause_ref`` the merged
  Lead's id. The merged Lead keeps its stage. A Lead with no row raises ``LookupError``.
- ``@command close(lead_id, stage, cause_ref) -> bool``: closes the Lead at ``stage``, which
  must be ``lost`` or ``disqualified`` (anything else raises ``ValueError``, and so does a
  ``cause_ref`` that is neither ``None`` nor a ULID). It applies ``close_lost`` or
  ``close_disqualified``, sets the stage and appends a row from the old stage, cause
  ``closed_lost`` or ``closed_disqualified``, with the ``cause_ref``; it returns ``True``. A
  Lead already closed (``lost`` or ``disqualified``) is left as it is and gives ``False``; an
  unknown Lead (a text that isn't a ULID included) raises ``LookupError``. Story 4.8 adds
  stopping the Lead's Enrollments (AD-4).
- ``@command close_suppressed(n: SubjectSuppressed)``: the ``SubjectSuppressed`` subscriber
  (``nextron.main.build_subscribers``). For an opt-out source (``OPT_OUT_SOURCES``:
  ``reply_rule``, ``reply_class``, ``one_click``, ``mailto``) with a ``lead_id``, it calls
  ``close(n.lead_id, lost, n.suppression_entry_id)``; the ``operator``, ``import`` and ``dsr``
  sources leave the stage unchanged. A closed Lead is a no-op, so it is idempotent.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from nextron.db import eventlog
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.uow import command
from nextron.domain.ids import is_ulid
from nextron.domain.leads import LeadSource
from nextron.domain.machines import IllegalTransition
from nextron.domain.notifications import LeadsMerged, SubjectSuppressed
from nextron.domain.pipeline_stage import (
    CLOSED_STAGES,
    CREATION,
    MACHINE,
    PipelineStage,
    StageEvent,
    merged,
    transition,
)
from nextron.domain.suppression_entry import OPT_OUT_SOURCES
from nextron.ports.clock import Clock

ENTITY: Final = "leads"
ACTOR: Final = "system:pipeline"
CLOSE_EVENTS: Final[Mapping[PipelineStage, StageEvent]] = MappingProxyType(
    {
        PipelineStage.LOST: StageEvent.CLOSE_LOST,
        PipelineStage.DISQUALIFIED: StageEvent.CLOSE_DISQUALIFIED,
    }
)


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
        """Give the survivor of a merge the more advanced stage of the two."""
        survivor = stages_repo.stage(n.survivor_lead_id)
        loser = stages_repo.stage(n.merged_lead_id)
        if survivor is None or loser is None:
            raise LookupError("reparent_lead: a merged Lead has no row")
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
    def close(self, lead_id: str, stage: PipelineStage, cause_ref: str | None) -> bool:
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
            cause=str(change.cause),
            cause_ref=cause_ref,
            actor=ACTOR,
            at=self._clock.now(),
        )
        return True

    @command
    def close_suppressed(self, n: SubjectSuppressed) -> None:
        """Close the Lead ``lost`` when an opt-out suppressed one of its Contacts."""
        if n.lead_id is None or n.source not in OPT_OUT_SOURCES:
            return
        self.close(n.lead_id, PipelineStage.LOST, n.suppression_entry_id)
