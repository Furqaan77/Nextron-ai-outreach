"""Public command API of the ``discovery`` service (AD-3, FR-9).

Other services and the delivery layers change ``discovery`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Discovery(clock, pipeline, queue)`` owns the ``leads`` identity group and keeps exactly one
Lead per organization: a candidate matches an existing Lead by any of its keys (registrable domain,
Companies House number or place ID), at any Pipeline Stage, merged Leads included. Keys arrive
already normalised: ``LeadIdentity`` refuses any other (``nextron.domain.identity`` is the only
normaliser).

- ``@command upsert_lead(identity, source) -> UpsertedLead(lead_id, created, merged)``:

  1. A wrong type (``identity`` not a ``LeadIdentity``, ``source`` not a ``LeadSource``) raises
     ``TypeError``; a ``segment_id`` not among ``queries.setup.segments()`` raises
     ``InvalidLeadIdentity("segment_id", ...)``.
  2. Each key that is set is looked up, and ``merged_into`` is followed to the survivor; a
     chain longer than ``MAX_MERGE_CHAIN`` (64) links raises ``RuntimeError``.
  3. No match: a new Lead is inserted (``created_at`` = now, stage ``new``), and
     ``pipeline.record_created`` appends its creation ``events`` row (cause
     ``created_from_<source>``); ``created=True``. A Lead created from a ``csv`` source with a
     ``web_domain`` also gets its ``enrich_lead`` job (Story 2.5), enqueued in the same
     transaction: key ``enrich_lead:<lead ULID>:<UTC date>``, payload ``{v: 1, lead_id}``,
     concurrency key ``domain:<web_domain>``, on its default lane.
  4. Two or more distinct survivors: the oldest (by ``created_at``, then ``id``) survives, and
     each of the others is ``merge``d into it in that order; ``merged`` lists them.
  5. Then each candidate key that no Lead holds and the survivor lacks is attached to the
     survivor. An existing Lead's ``name``, ``segment_id`` and ``country_code`` never change,
     and a key that conflicts with the survivor's own is dropped.

- ``@command merge(survivor_id, loser_id)`` raises ``MergeRefused`` (code ``merge_refused``,
  which dooms the caller's transaction) when the two ids are the same, either Lead is unknown or
  already merged, or the survivor is newer than the loser (by ``created_at``, then ``id``).
  Otherwise, in order: ``mark_merged`` sets the loser's ``merged_into`` and clears each key the
  survivor lacks; ``set_keys`` gives those keys to the survivor; and ``LeadsMerged(survivor,
  loser)`` is published, so ``pipeline.reparent_lead`` (and every later owner of Lead-scoped
  rows) runs in the same transaction. A key the survivor holds with another value stays on the
  loser, where a lookup still finds it and follows ``merged_into``. The merge itself appends no
  ``events`` row: ``merged_into`` is the record. Non-``str`` ids raise ``TypeError``.

- ``@use_case canonicalize_lead(lead_id, run_id) -> str`` (the ``canonicalize_lead`` job, Story
  2.5): a crawl found the Lead's homepage on another registrable domain. In one transaction it
  reads the run's ``final_domain`` through ``queries.research.crawl_run`` and works on the
  Lead's survivor (``merged_into`` followed):

  - an unknown Lead or run, a run without a ``final_domain``, or one whose final domain is a
    shared platform (``nextron.domain.leads.is_shared_platform``): nothing (``nothing``);
  - the survivor already holds the domain, itself or through a Lead merged into it: nothing
    (``kept``);
  - the survivor's ``web_domain`` isn't the run's ``site_domain`` (the crawled Lead was merged
    into a Lead with another website meanwhile): nothing (``nothing``), so the survivor's own
    domain is never replaced;
  - no Lead holds it: the survivor's ``web_domain`` becomes it (``leads.set_web_domain``), so
    the old domain is released (``moved``);
  - another Lead holds it: the two survivors are ``merge``d, the older surviving (``merged``).
    When the older one keeps its own ``web_domain``, the new domain stays on the merged Lead,
    where a lookup still finds it and follows ``merged_into``.

  It logs ``lead_canonicalized`` (``lead_id``, ``survivor_id``, ``run_id``, ``action``) and
  returns the action.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery.leads import StoredLead
from nextron.db.uow import command, current, use_case
from nextron.domain.crawl import ENRICH_LEAD_JOB, LEAD_ID_FIELD, PAYLOAD_VERSION, lead_job_keys
from nextron.domain.ids import is_ulid
from nextron.domain.job import VERSION_FIELD
from nextron.domain.leads import (
    KEY_FIELDS,
    InvalidLeadIdentity,
    LeadIdentity,
    LeadSource,
    MergeRefused,
    is_shared_platform,
)
from nextron.domain.notifications import LeadsMerged
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

MAX_MERGE_CHAIN: Final = 64
LEAD_CANONICALIZED: Final = "lead_canonicalized"
# What canonicalize_lead did
NOTHING: Final = "nothing"
KEPT: Final = "kept"
MOVED: Final = "moved"
MERGED: Final = "merged"

_LOOKUPS: Final = {
    "web_domain": leads_repo.by_web_domain,
    "ch_number": leads_repo.by_ch_number,
    "place_id": leads_repo.by_place_id,
}


@dataclass(frozen=True, slots=True)
class UpsertedLead:
    """The Lead a candidate became: its id, whether it was created, and the Leads merged into it
    on the way, in merge order."""

    lead_id: str
    created: bool
    merged: tuple[str, ...]


def _survivor(lead: StoredLead) -> StoredLead:
    """The Lead ``lead`` was merged into, following ``merged_into`` to the end."""
    start = lead.id
    links = 0
    while lead.merged_into is not None:
        if links == MAX_MERGE_CHAIN:
            raise RuntimeError(
                f"the merge chain from Lead {start} is longer than {MAX_MERGE_CHAIN} links"
            )
        following = leads_repo.get(lead.merged_into)
        if following is None:  # the foreign key makes this impossible
            raise RuntimeError(f"Lead {lead.id} was merged into a Lead with no row")
        lead = following
        links += 1
    return lead


def _age(lead: StoredLead) -> tuple[datetime, str]:
    """Older Leads sort first: by ``created_at``, then ``id``."""
    return (lead.created_at, lead.id)


class Discovery:
    """One Lead per organization; see the module docstring."""

    def __init__(self, clock: Clock, pipeline: Pipeline, queue: JobQueue) -> None:
        self._clock = clock
        self._pipeline = pipeline
        self._queue = queue

    @command
    def upsert_lead(self, identity: LeadIdentity, source: LeadSource) -> UpsertedLead:
        """Match ``identity`` to its organization's Lead, or create it; see the module
        docstring."""
        if not isinstance(identity, LeadIdentity):
            raise TypeError(f"upsert_lead takes a LeadIdentity, not {type(identity).__name__}")
        if not isinstance(source, LeadSource):
            raise TypeError(f"upsert_lead takes a LeadSource, not {type(source).__name__}")
        if identity.segment_id not in {segment.id for segment in setup_queries.segments()}:
            raise InvalidLeadIdentity("segment_id", "is not a stored Segment")
        candidate = identity.keys()
        holders = {field: _LOOKUPS[field](value) for field, value in candidate.items()}
        survivors: dict[str, StoredLead] = {}
        for holder in holders.values():
            if holder is not None:
                found = _survivor(holder)
                survivors[found.id] = found
        if not survivors:
            lead_id = leads_repo.insert(
                name=identity.name,
                segment_id=identity.segment_id,
                web_domain=identity.web_domain,
                ch_number=identity.ch_number,
                place_id=identity.place_id,
                country_code=identity.country_code,
                created_at=self._clock.now(),
            )
            self._pipeline.record_created(lead_id, source)
            if source is LeadSource.CSV and identity.web_domain is not None:
                self._enqueue_crawl(lead_id, identity.web_domain)
            return UpsertedLead(lead_id, True, ())
        keep, *others = sorted(survivors.values(), key=_age)
        for other in others:
            self.merge(keep.id, other.id)
        kept = leads_repo.get(keep.id)
        if kept is None:  # merging never deletes a row
            raise RuntimeError(f"Lead {keep.id} has no row")
        attach = {
            field: value
            for field, value in candidate.items()
            if holders[field] is None and getattr(kept, field) is None
        }
        leads_repo.set_keys(kept.id, **attach)
        return UpsertedLead(kept.id, False, tuple(other.id for other in others))

    def _enqueue_crawl(self, lead_id: str, web_domain: str) -> None:
        """Enqueue the new Lead's ``enrich_lead`` job in the caller's transaction."""
        discriminator, concurrency_key = lead_job_keys(web_domain, self._clock.now())
        self._queue.enqueue(
            ENRICH_LEAD_JOB,
            scope=lead_id,
            discriminator=discriminator,
            payload={VERSION_FIELD: PAYLOAD_VERSION, LEAD_ID_FIELD: lead_id},
            concurrency_key=concurrency_key,
        )

    @use_case
    def canonicalize_lead(self, lead_id: str, run_id: str) -> str:
        """Move the Lead to the domain its website now lives on; see the module docstring."""
        with current().transaction():
            survivor_id, action = self._canonicalize(lead_id, run_id)
        log.info(
            LEAD_CANONICALIZED,
            extra={
                "lead_id": lead_id,
                "survivor_id": survivor_id,
                "run_id": run_id,
                "action": action,
            },
        )
        return action

    def _canonicalize(self, lead_id: str, run_id: str) -> tuple[str | None, str]:
        run = research_queries.crawl_run(run_id) if isinstance(run_id, str) else None
        lead = leads_repo.get(lead_id) if isinstance(lead_id, str) and is_ulid(lead_id) else None
        if run is None or lead is None or run.final_domain is None:
            return None, NOTHING
        domain = run.final_domain
        if is_shared_platform(domain):  # never one organization's domain
            return None, NOTHING
        survivor = _survivor(lead)
        if survivor.web_domain == domain:
            return survivor.id, KEPT
        if survivor.web_domain != run.site_domain:  # the survivor's site isn't the one crawled
            return survivor.id, NOTHING
        holder = leads_repo.by_web_domain(domain)
        if holder is None:
            leads_repo.set_web_domain(survivor.id, domain)
            return survivor.id, MOVED
        other = _survivor(holder)
        if other.id == survivor.id:
            return survivor.id, KEPT
        older, newer = sorted((survivor, other), key=_age)
        self.merge(older.id, newer.id)
        return older.id, MERGED

    @command
    def merge(self, survivor_id: str, loser_id: str) -> None:
        """Merge the Lead ``loser_id`` into the older Lead ``survivor_id``; see the module
        docstring."""
        if not isinstance(survivor_id, str) or not isinstance(loser_id, str):
            raise TypeError("merge takes two Lead ids as str")
        if survivor_id == loser_id:
            raise MergeRefused("a Lead can't be merged into itself")
        survivor = leads_repo.get(survivor_id) if is_ulid(survivor_id) else None
        loser = leads_repo.get(loser_id) if is_ulid(loser_id) else None
        if survivor is None:
            raise MergeRefused("the survivor is not a known Lead")
        if loser is None:
            raise MergeRefused("the Lead to merge is not a known Lead")
        if survivor.merged_into is not None:
            raise MergeRefused("the survivor is already merged into another Lead")
        if loser.merged_into is not None:
            raise MergeRefused("the Lead to merge is already merged into another Lead")
        if _age(survivor) > _age(loser):
            raise MergeRefused("the survivor is newer than the Lead to merge")
        moved = {
            field: value
            for field in KEY_FIELDS
            if (value := getattr(loser, field)) is not None and getattr(survivor, field) is None
        }
        leads_repo.mark_merged(loser.id, survivor.id, tuple(moved))
        leads_repo.set_keys(survivor.id, **moved)
        current().publish(LeadsMerged(survivor_lead_id=survivor.id, merged_lead_id=loser.id))
