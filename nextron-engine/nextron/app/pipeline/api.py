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
"""

from typing import Final

from nextron.db import eventlog
from nextron.db.repos.pipeline import stages as stages_repo
from nextron.db.uow import command
from nextron.domain.leads import LeadSource
from nextron.domain.machines import IllegalTransition
from nextron.domain.notifications import LeadsMerged
from nextron.domain.pipeline_stage import CREATION, MACHINE, PipelineStage, merged
from nextron.ports.clock import Clock

ENTITY: Final = "leads"
ACTOR: Final = "system:pipeline"


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
