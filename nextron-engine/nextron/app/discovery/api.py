"""Public command API of the ``discovery`` service (AD-3, FR-9).

Other services and the delivery layers change ``discovery`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Discovery(clock, pipeline)`` owns the ``leads`` identity group and keeps exactly one Lead per
organization: a candidate matches an existing Lead by any of its keys (registrable domain,
Companies House number or place ID), at any Pipeline Stage, merged Leads included. Keys arrive
already normalised: ``LeadIdentity`` refuses any other (``nextron.domain.identity`` is the only
normaliser).

- ``@command upsert_lead(identity, source) -> UpsertedLead(lead_id, created, merged)``:

  1. A wrong type (``identity`` not a ``LeadIdentity``, ``source`` not a ``LeadSource``) raises
     ``TypeError``; a ``segment_id`` not among ``queries.setup.segment_ids()`` raises
     ``InvalidLeadIdentity("segment_id", ...)`` (it reads the ids alone, never decoding a
     Segment's definition).
  2. Each key that is set is looked up, and ``merged_into`` is followed to the survivor; a
     chain longer than ``MAX_MERGE_CHAIN`` (64) links raises ``RuntimeError``.
  3. No match: a new Lead is inserted (``created_at`` = now, stage ``new``), and
     ``pipeline.record_created`` appends its creation ``events`` row (cause
     ``created_from_<source>``); ``created=True``.
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
  rows) runs in the same transaction: ``pipeline.reparent_lead``, then
  ``research.reparent_lead``. A key the survivor holds with another value stays on the loser,
  where a lookup still finds it and follows ``merged_into``. The merge itself appends no
  ``events`` row: ``merged_into`` is the record. Non-``str`` ids raise ``TypeError``.

**The CSV import (FR-59, Story 2.3).** ``LeadImports(clock, discovery, research,
email_syntax)`` turns an uploaded CSV into Leads and Contacts; the rules for the file and its
rows are ``nextron.domain.lead_import``'s.

- ``@use_case run(request: ImportRequest(origin, default_segment, data)) -> ImportSummary``:

  1. **Checks before any write.** ``check_origin`` cleans the origin, then ``check_file_size``
     refuses an empty file or one over 5 MiB. One transaction reads
     ``queries.setup.segments()``; a ``default_segment`` key (``None`` for no default) that
     isn't stored raises ``ImportRefused("default_segment", ...)``. Then ``plan_import`` runs,
     outside any transaction, with each Segment's casefolded key and name and
     ``email_syntax.is_valid`` (the ``EmailSyntaxPort``, offline). An ``ImportRefused`` means
     nothing was written; it is logged as ``lead_import_refused`` with its ``field``.
  2. **Rows.** ``imported_at`` is the clock's now. The plan's rows run in batches of
     ``BATCH_ROWS`` (200), each in its own ``uow.transaction()``, so no write lock lasts long.
     Each row runs ``discovery.upsert_lead(identity, LeadSource.CSV)``: ``created`` counts as
     imported, otherwise merged (a domain met earlier in the file or already stored). With a
     Contact, it then runs ``research.add_contact(lead_id, contact, source=csv, detail=origin,
     retrieved_at=imported_at)``; ``email_dropped`` adds ``NOTE_EMAIL_DROPPED`` to the row.
  3. **A failing batch** rolls back, and ``ImportStopped(summary, at_row)`` (a
     ``RuntimeError``) is raised from the error, logged as ``lead_import_stopped`` with
     ``at_row`` and the error's type. ``summary`` counts only the committed batches and
     ``at_row`` is the failed batch's first row number. Importing the same file again is safe:
     its committed rows are merged and their Contacts match.

  ``ImportSummary(imported, merged, rejected, noted)`` holds the two counts, every
  ``RejectedRow`` of the plan (all of them, even after a stop: they import nothing either way),
  and a ``NotedRow(number, label, notes)`` for each committed row with notes, both in row
  order. A finished import logs ``lead_import_finished`` with the four counts. No log line holds
  a CSV cell, the origin or the file name.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery.leads import StoredLead
from nextron.db.uow import command, current, use_case
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.ids import is_ulid
from nextron.domain.lead_import import (
    DEFAULT_SEGMENT_UNKNOWN,
    FIELD_DEFAULT_SEGMENT,
    NOTE_EMAIL_DROPPED,
    ImportRefused,
    RejectedRow,
    check_file_size,
    check_origin,
    plan_import,
)
from nextron.domain.leads import (
    KEY_FIELDS,
    InvalidLeadIdentity,
    LeadIdentity,
    LeadSource,
    MergeRefused,
)
from nextron.domain.notifications import LeadsMerged
from nextron.ports.clock import Clock
from nextron.ports.email_syntax import EmailSyntaxPort

MAX_MERGE_CHAIN: Final = 64
BATCH_ROWS: Final = 200

log = logging.getLogger(__name__)

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

    def __init__(self, clock: Clock, pipeline: Pipeline) -> None:
        self._clock = clock
        self._pipeline = pipeline

    @command
    def upsert_lead(self, identity: LeadIdentity, source: LeadSource) -> UpsertedLead:
        """Match ``identity`` to its organization's Lead, or create it; see the module
        docstring."""
        if not isinstance(identity, LeadIdentity):
            raise TypeError(f"upsert_lead takes a LeadIdentity, not {type(identity).__name__}")
        if not isinstance(source, LeadSource):
            raise TypeError(f"upsert_lead takes a LeadSource, not {type(source).__name__}")
        if identity.segment_id not in setup_queries.segment_ids():
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


# The CSV import ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportRequest:
    """What the import form sent: the list's origin, the default Segment's key (``None`` for no
    default) and the file's bytes."""

    origin: str
    default_segment: str | None
    data: bytes


@dataclass(frozen=True, slots=True)
class NotedRow:
    """An imported row with notes on what it didn't store: its number, label and notes."""

    number: int
    label: str
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """What an import did: how many rows created a Lead (``imported``) and how many matched one
    (``merged``), the rejected rows and the imported rows with notes, in row order."""

    imported: int
    merged: int
    rejected: tuple[RejectedRow, ...]
    noted: tuple[NotedRow, ...]


class ImportStopped(RuntimeError):
    """A batch failed: it was rolled back and the import stopped at ``at_row``; ``summary``
    counts the batches committed before it."""

    def __init__(self, summary: ImportSummary, at_row: int) -> None:
        super().__init__(f"the import stopped at row {at_row}; the rows before it were saved")
        self.summary = summary
        self.at_row = at_row


def _segment_lookup(stored: tuple[setup_queries.SeededSegment, ...]) -> dict[str, str]:
    """Each Segment's key, then each name, whitespace-collapsed (as the CSV cells are) and
    casefolded, with its id; a key wins over a name that folds to the same text."""
    lookup: dict[str, str] = {}
    for segment in stored:
        lookup.setdefault(_lookup_key(segment.key), segment.id)
    for segment in stored:
        lookup.setdefault(_lookup_key(segment.definition.name), segment.id)
    return lookup


def _lookup_key(text: str) -> str:
    return " ".join(text.split()).casefold()


class LeadImports:
    """The CSV Lead import; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        discovery: Discovery,
        research: Research,
        email_syntax: EmailSyntaxPort,
    ) -> None:
        self._clock = clock
        self._discovery = discovery
        self._research = research
        self._email_syntax = email_syntax

    @use_case
    def run(self, request: ImportRequest) -> ImportSummary:
        """Import the CSV in ``request``; see the module docstring."""
        if not isinstance(request, ImportRequest):
            raise TypeError(f"run takes an ImportRequest, not {type(request).__name__}")
        uow = current()
        try:
            origin = check_origin(request.origin)
            check_file_size(request.data)
            with uow.transaction():
                stored = setup_queries.segments()
            default_id: str | None = None
            if request.default_segment is not None:
                chosen = [found.id for found in stored if found.key == request.default_segment]
                if not chosen:
                    raise ImportRefused(FIELD_DEFAULT_SEGMENT, DEFAULT_SEGMENT_UNKNOWN)
                default_id = chosen[0]
            plan = plan_import(
                request.data,
                segments=_segment_lookup(stored),
                default_segment_id=default_id,
                email_ok=self._email_syntax.is_valid,
            )
        except ImportRefused as refused:
            log.info("lead_import_refused", extra={"field": refused.field})
            raise
        imported_at = self._clock.now()
        imported = merged = 0
        noted: list[NotedRow] = []
        for start in range(0, len(plan.rows), BATCH_ROWS):
            batch = plan.rows[start : start + BATCH_ROWS]
            created = 0
            batch_noted: list[NotedRow] = []
            try:
                with uow.transaction():
                    for row in batch:
                        upserted = self._discovery.upsert_lead(row.identity, LeadSource.CSV)
                        created += upserted.created
                        notes = list(row.notes)
                        if row.contact is not None:
                            added = self._research.add_contact(
                                upserted.lead_id,
                                row.contact,
                                source=ProvenanceSource.CSV,
                                detail=origin,
                                retrieved_at=imported_at,
                            )
                            if added.email_dropped:
                                notes.append(NOTE_EMAIL_DROPPED)
                        if notes:
                            batch_noted.append(NotedRow(row.number, row.label, tuple(notes)))
            except Exception as error:
                at_row = batch[0].number
                log.warning(
                    "lead_import_stopped",
                    extra={"at_row": at_row, "error": type(error).__name__},
                )
                summary = ImportSummary(imported, merged, plan.rejected, tuple(noted))
                raise ImportStopped(summary, at_row) from error
            imported += created
            merged += len(batch) - created
            noted.extend(batch_noted)
        summary = ImportSummary(imported, merged, plan.rejected, tuple(noted))
        log.info(
            "lead_import_finished",
            extra={
                "imported": imported,
                "merged": merged,
                "rejected": len(plan.rejected),
                "noted": len(noted),
            },
        )
        return summary
