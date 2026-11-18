"""Public command API of the ``discovery`` service (AD-3, FR-9).

Other services and the delivery layers change ``discovery`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Discovery(clock, pipeline, queue)`` owns the ``leads`` identity group and keeps exactly one
Lead per organization: a candidate matches an existing Lead by any of its keys (registrable domain,
Companies House number or place ID), at any Pipeline Stage, merged Leads included. Keys arrive
already normalised: ``LeadIdentity`` refuses any other (``nextron.domain.identity`` is the only
normaliser).

- ``@command upsert_lead(identity, source) -> UpsertedLead(lead_id, created, merged, blocked)``:

  1. A wrong type (``identity`` not a ``LeadIdentity``, ``source`` not a ``LeadSource``) raises
     ``TypeError``; a ``segment_id`` not among ``queries.setup.segment_ids()`` raises
     ``InvalidLeadIdentity("segment_id", ...)`` (it reads the ids alone, never decoding a
     Segment's definition).
  2. Each key that is set is looked up, and ``merged_into`` is followed to the survivor; a
     chain longer than ``MAX_MERGE_CHAIN`` (64) links raises ``RuntimeError``.
  3. No match: first the contactability gate (AD-16, Story 3.4):
     ``compliance_api.assert_contactable(NewLeadSubject(identity.web_domain), email,
     discovery)``. A ``Blocked`` answer returns ``UpsertedLead(lead_id=None, created=False,
     merged=(), blocked=<the answer>)`` with nothing written and nothing enqueued; an answer
     that is neither ``Allowed`` nor ``Blocked`` raises ``TypeError``. On ``Allowed`` a new
     Lead is inserted (``created_at`` = now, stage ``new``), and
     ``pipeline.record_created`` appends its creation ``events`` row (cause
     ``created_from_<source>``); ``created=True``. A Lead created from a ``csv`` source (Story
     2.5) or a ``places`` source (Story 8.3) with a ``web_domain`` also gets its
     ``enrich_lead`` job, enqueued in the same
     transaction: key ``enrich_lead:<lead ULID>:<UTC date>``, payload ``{v: 1, lead_id}``,
     concurrency key ``domain:<web_domain>``, on its default lane. A matched Lead (steps 4 and
     5) is returned as before, at any stage and whatever its domain, without the gate: it is
     never re-created (FR-9).
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

- ``@command set_ch_number(lead_id, number) -> str | None`` (Story 2.12, FR-9): Evidence on the
  Lead's website links it to the Companies House company ``number`` (normalised: ``[A-Z0-9]{8}``,
  else ``ValueError``; an unknown Lead raises ``LookupError``). It works on the Lead's survivor,
  as ``_canonicalize`` does, and returns the Lead the number now belongs to:

  - the survivor holds ``number``: nothing; the survivor;
  - the survivor holds another number: nothing (a number a Lead holds is never overwritten);
    ``None``, a conflict;
  - no Lead holds it: the survivor's ``ch_number`` becomes it; the survivor;
  - a Lead merged into the survivor holds it: nothing; the survivor;
  - another Lead holds it: the two survivors are ``merge``d, the older surviving, and the older
    is returned when it then holds ``number``; when it holds another (the number stayed on a
    Lead merged into it), the merge stands and the answer is ``None``.

  A ``ch_number`` that came from a CSV is never Evidence: only this command, called by the
  legal-form check, links a number to a Lead from its website.

- ``@command record_place_coords(lead_id, lat, lng)`` (Story 8.3): stores a Places location on
  the Lead ``lead_id`` (``place_lat``, ``place_lng`` and ``place_coords_at`` = now, together).
  ``lat`` and ``lng`` are finite floats in range (``TypeError``/``ValueError`` otherwise); an
  unknown Lead raises ``LookupError``. Only a Places discovery run calls it, for the Lead it
  just created; the daily purge nulls the three after 30 days.

- **A registry Lead's website (Story 8.4, FR-9, FR-49).** A Lead that Companies House
  discovery created has no ``web_domain`` until one is found or entered:

  - ``@command attach_web_domain(lead_id, domain) -> bool``: Companies House discovery's
    website lookup found ``domain`` (a registrable domain that isn't a shared platform; else
    ``ValueError``). It is attached only when the Lead exists, isn't merged and has no
    ``web_domain`` (else ``LookupError``, ``ValueError``), no Lead holds ``domain``, and
    ``compliance_api.assert_contactable(NewLeadSubject(domain), email, discovery)`` is
    ``Allowed``: then the Lead's ``web_domain`` becomes it and its ``enrich_lead`` job is
    enqueued (the keys ``upsert_lead`` uses), and the answer is ``True``. Otherwise nothing is
    written and the answer is ``False``.
  - ``@command record_website_lookup(lead_id)``: sets the Lead's ``website_lookup_at`` to now
    (an unknown Lead raises ``LookupError``).
  - ``@use_case enter_website(lead_id, url) -> EnteredWebsite(lead_id, merged)`` (the Leads
    list's "Website needed" form, ``POST /leads/{lead_id}/website``), in one transaction: the
    Lead must be stored and not merged (else ``WebsiteRefused(REFUSED_LEAD)``) and have no
    ``web_domain`` (``REFUSED_HAS_WEBSITE``). ``url`` (an address without a scheme is read as
    ``https://<address>``) must pass ``fetch_policy.url_refusal`` (the FR-49 denylist included)
    and key to a registrable domain (``REFUSED_URL``) that isn't a shared platform
    (``REFUSED_SHARED``), and the gate must allow it (``REFUSED_BLOCKED``). Then, when no Lead
    holds the domain, it becomes the Lead's ``web_domain``; when another Lead does, the two
    survivors are ``merge``d (FR-9), the older surviving (``merged=True``). Either way the
    survivor's ``enrich_lead`` job is enqueued and ``lead_id`` is the survivor. A refusal writes
    nothing and logs ``website_entry_refused`` (the reason); success logs ``website_entered``
    (ids and whether it merged). Nothing logged holds the URL or the domain.

- ``@use_case purge_place_coords() -> int`` (the daily ``purge_place_coords`` job, Story 8.2):
  in one transaction it nulls ``place_lat``, ``place_lng`` and ``place_coords_at`` on every
  Lead whose coordinates were stored strictly before ``now - 30 days``
  (``nextron.domain.places.coords_cutoff``), and on any Lead holding a coordinate without a
  ``place_coords_at``, keeps ``place_id``, logs ``place_coords_purged``
  (``count``) and returns the count.

**Google Places (Story 8.2).** ``PlaceSearches`` (``nextron.app.discovery.places``) runs one
Places search page at a time, after the budget's per-call check. ``PlaceDiscovery``
(``nextron.app.discovery.place_ingest``, Story 8.3) runs the ``discover_places`` jobs: it pages
through a Campaign's Places queries in one location and turns each candidate into a Lead
through ``upsert_lead`` (source ``places``) and ``record_place_coords``.

**Companies House discovery (Story 8.4).** ``RegistryDiscovery``
(``nextron.app.discovery.registry_ingest``) runs the ``discover_registry`` jobs: it pages
through a UK Campaign's Companies House searches and turns each active company into a Lead
through ``upsert_lead`` (source ``companies_house``), then looks its website up once through
Places (``attach_web_domain``, ``record_website_lookup``) and stores its kept officers.

**The legal form (Story 2.12, FR-12).** ``LegalFormChecks`` (``nextron.app.discovery
.legal_form``) runs the ``check_legal_form`` jobs: it links a GB Lead to its Companies House
record through ``set_ch_number`` and records the finding through ``research.record_legal_form``.

**The CSV import (FR-59, Story 2.3).** ``LeadImports(clock, discovery, research,
email_syntax)`` turns an uploaded CSV into Leads and Contacts; the rules for the file and its
rows are ``nextron.domain.lead_import``'s.

- ``@use_case run(request: ImportRequest(origin, default_segment, data)) -> ImportSummary``:

  1. **Checks before any write.** ``check_origin`` cleans the origin, then ``check_file_size``
     refuses an empty file or one over 5 MiB. One transaction reads
     ``queries.setup.segments()`` and ``queries.setup.markets()``; a ``default_segment`` key
     (``None`` for no default) that isn't stored raises ``ImportRefused("default_segment",
     ...)``. Then ``plan_import`` runs, outside any transaction, with each Segment's casefolded
     key and name, ``email_syntax.is_valid`` (the ``EmailSyntaxPort``, offline) and the country
     codes of the ``enabled`` Markets (Story 3.2: a row of any other country is blocked). An
     ``ImportRefused`` means nothing was written; it is logged as ``lead_import_refused`` with
     its ``field``.
  2. **Rows.** ``imported_at`` is the clock's now. The plan's rows run in batches of
     ``BATCH_ROWS`` (200), each in its own ``uow.transaction()``, so no write lock lasts long.
     Each row runs ``discovery.upsert_lead(identity, LeadSource.CSV)``: ``created`` counts as
     imported, a matched Lead as merged (a domain met earlier in the file or already stored),
     and a ``blocked`` answer (Story 3.4) adds a ``SuppressedRow(number, label)``: the row is
     neither imported nor merged, and its Contact isn't tried. With a Contact, an imported or
     merged row then runs ``research.add_contact(lead_id, contact, source=csv, detail=origin,
     retrieved_at=imported_at)``: a ``blocked`` answer adds ``NOTE_CONTACT_SUPPRESSED`` to the
     row when any reason is in ``SUPPRESSION_REASONS``, else ``NOTE_CONTACT_LEAD_CLOSED`` (the
     Lead is Lost or Disqualified); otherwise ``email_dropped`` adds ``NOTE_EMAIL_DROPPED``.
  3. **A failing batch** rolls back, and ``ImportStopped(summary, at_row)`` (a
     ``RuntimeError``) is raised from the error, logged as ``lead_import_stopped`` with
     ``at_row`` and the error's type. ``summary`` counts only the committed batches and
     ``at_row`` is the failed batch's first row number. Importing the same file again is safe:
     its committed rows are merged and their Contacts match.

  ``ImportSummary(imported, merged, rejected, noted, blocked, suppressed)`` holds the two
  counts, every ``RejectedRow`` of the plan (all of them, even after a stop: they import nothing
  either way), a ``NotedRow(number, label, notes)`` for each committed row with notes, every
  ``BlockedRow`` of the plan (Story 3.2, like the rejected rows) and a ``SuppressedRow`` for
  each committed row whose Lead the gate blocked (Story 3.4), each in row order. A finished
  import logs ``lead_import_finished`` with the six counts. No log line holds a CSV cell, the
  origin or the file name.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from urllib.parse import urlsplit

from nextron.app.compliance import api as compliance_api
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery.leads import StoredLead
from nextron.db.uow import command, current, use_case
from nextron.domain.contactability import (
    SUPPRESSION_REASONS,
    Allowed,
    Blocked,
    Channel,
    NewLeadSubject,
    Purpose,
)
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.crawl import ENRICH_LEAD_JOB, LEAD_ID_FIELD, PAYLOAD_VERSION, lead_job_keys
from nextron.domain.errors import DomainError
from nextron.domain.fetch_policy import check_url, url_refusal
from nextron.domain.identity import IdentityError, registrable_domain
from nextron.domain.ids import is_ulid
from nextron.domain.job import VERSION_FIELD
from nextron.domain.lead_import import (
    DEFAULT_SEGMENT_UNKNOWN,
    FIELD_DEFAULT_SEGMENT,
    NOTE_CONTACT_LEAD_CLOSED,
    NOTE_CONTACT_SUPPRESSED,
    NOTE_EMAIL_DROPPED,
    BlockedRow,
    ImportRefused,
    RejectedRow,
    SuppressedRow,
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
    is_shared_platform,
)
from nextron.domain.legal_form import COMPANY_NUMBER_PATTERN
from nextron.domain.notifications import LeadsMerged
from nextron.domain.places import coords_cutoff
from nextron.ports.clock import Clock
from nextron.ports.email_syntax import EmailSyntaxPort

log = logging.getLogger(__name__)

MAX_MERGE_CHAIN: Final = 64
WEBSITE_ENTERED: Final = "website_entered"
WEBSITE_ENTRY_REFUSED: Final = "website_entry_refused"
DEFAULT_SCHEME: Final = "https://"
# Why an entered website was refused (Story 8.4)
REFUSED_LEAD: Final = "lead_unknown"
REFUSED_HAS_WEBSITE: Final = "lead_has_website"
REFUSED_URL: Final = "url_refused"
REFUSED_SHARED: Final = "shared_platform"
REFUSED_BLOCKED: Final = "blocked"
WEBSITE_REFUSAL_MESSAGES: Final = {
    REFUSED_LEAD: "That Lead isn't on the Leads list any more. Nothing was saved.",
    REFUSED_HAS_WEBSITE: "That Lead already has a website. Nothing was saved.",
    REFUSED_URL: (
        "That isn't a website address the engine may use (a public http or https address, "
        "not LinkedIn, Instagram, Google Maps or a job board). Nothing was saved."
    ),
    REFUSED_SHARED: (
        "That address is on a shared platform, not the firm's own website. Nothing was saved."
    ),
    REFUSED_BLOCKED: (
        "That website is on the Suppression List or otherwise blocked from contact. "
        "Nothing was saved."
    ),
}
# The sources whose new Leads get their enrich_lead job in the creating transaction
CRAWLED_SOURCES: Final = frozenset({LeadSource.CSV, LeadSource.PLACES})
PLACE_COORDS_PURGED: Final = "place_coords_purged"
BATCH_ROWS: Final = 200
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
    on the way, in merge order. When the contactability gate blocked a new Lead, ``lead_id`` is
    ``None`` and ``blocked`` holds the gate's answer."""

    lead_id: str | None
    created: bool
    merged: tuple[str, ...]
    blocked: Blocked | None = None


class WebsiteRefused(DomainError):
    """An entered website that can't be saved; ``reason`` is one of the ``REFUSED_*`` codes
    and ``message`` what the page says."""

    code = "website_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(WEBSITE_REFUSAL_MESSAGES[reason])
        self.reason = reason
        self.message = WEBSITE_REFUSAL_MESSAGES[reason]


@dataclass(frozen=True, slots=True)
class EnteredWebsite:
    """What an entered website did: the Lead that now holds it (the survivor) and whether a
    merge happened."""

    lead_id: str
    merged: bool


def website_domain(url: str) -> str | None:
    """The registrable domain of an egress-allowed URL that isn't a shared platform, else
    ``None`` (Stories 8.3 and 8.4)."""
    if not isinstance(url, str) or url_refusal(url) is not None:
        return None
    try:
        domain = registrable_domain(check_url(url).host)
    except IdentityError:
        return None
    return None if is_shared_platform(domain) else domain


def _entered_domain(url: str) -> str:
    """The registrable domain of an Operator-entered website; ``WebsiteRefused`` otherwise."""
    if not isinstance(url, str):
        raise WebsiteRefused(REFUSED_URL)
    text = url.strip()
    if text and not urlsplit(text).scheme:
        text = f"{DEFAULT_SCHEME}{text}"
    if not text or url_refusal(text) is not None:
        raise WebsiteRefused(REFUSED_URL)
    try:
        domain = registrable_domain(check_url(text).host)
    except IdentityError:
        raise WebsiteRefused(REFUSED_URL) from None
    if is_shared_platform(domain):
        raise WebsiteRefused(REFUSED_SHARED)
    return domain


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
            answer = compliance_api.assert_contactable(
                NewLeadSubject(identity.web_domain), Channel.EMAIL, Purpose.DISCOVERY
            )
            if isinstance(answer, Blocked):
                return UpsertedLead(None, False, (), blocked=answer)
            if not isinstance(answer, Allowed):  # never proceed on anything else
                raise TypeError(f"assert_contactable answered a {type(answer).__name__}")
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
            if source in CRAWLED_SOURCES and identity.web_domain is not None:
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

    # A registry Lead's website (Story 8.4) ------------------------------------------------------

    @command
    def attach_web_domain(self, lead_id: str, domain: str) -> bool:
        """Give the registry Lead ``lead_id`` the website its lookup found; whether it was
        attached. See the module docstring."""
        if not isinstance(lead_id, str) or not isinstance(domain, str):
            raise TypeError("attach_web_domain takes a Lead id and a domain as str")
        if website_domain(f"{DEFAULT_SCHEME}{domain}/") != domain:
            raise ValueError("attach_web_domain takes a registrable domain of a firm's own site")
        lead = leads_repo.get(lead_id) if is_ulid(lead_id) else None
        if lead is None:
            raise LookupError("attach_web_domain: the Lead is unknown")
        if lead.merged_into is not None or lead.web_domain is not None:
            raise ValueError("attach_web_domain: the Lead is merged or already has a website")
        if leads_repo.by_web_domain(domain) is not None:
            return False
        answer = compliance_api.assert_contactable(
            NewLeadSubject(domain), Channel.EMAIL, Purpose.DISCOVERY
        )
        if isinstance(answer, Blocked):
            return False
        if not isinstance(answer, Allowed):  # never proceed on anything else
            raise TypeError(f"assert_contactable answered a {type(answer).__name__}")
        leads_repo.set_web_domain(lead.id, domain)
        self._enqueue_crawl(lead.id, domain)
        return True

    @command
    def record_website_lookup(self, lead_id: str) -> None:
        """Record that the Lead's website lookup was decided now; see the module docstring."""
        if not isinstance(lead_id, str) or not is_ulid(lead_id) or leads_repo.get(lead_id) is None:
            raise LookupError("record_website_lookup: the Lead is unknown")
        leads_repo.set_website_lookup_at(lead_id, self._clock.now())

    @use_case
    def enter_website(self, lead_id: str, url: str) -> EnteredWebsite:
        """Save the website the Operator entered for a Lead without one; see the module
        docstring."""
        try:
            with current().transaction():
                entered = self._enter_website(lead_id, url)
        except WebsiteRefused as refused:
            log.info(WEBSITE_ENTRY_REFUSED, extra={"reason": refused.reason})
            raise
        log.info(
            WEBSITE_ENTERED,
            extra={"lead_id": lead_id, "survivor_id": entered.lead_id, "merged": entered.merged},
        )
        return entered

    def _enter_website(self, lead_id: str, url: str) -> EnteredWebsite:
        lead = leads_repo.get(lead_id) if isinstance(lead_id, str) and is_ulid(lead_id) else None
        if lead is None or lead.merged_into is not None:
            raise WebsiteRefused(REFUSED_LEAD)
        if lead.web_domain is not None:
            raise WebsiteRefused(REFUSED_HAS_WEBSITE)
        domain = _entered_domain(url)
        answer = compliance_api.assert_contactable(
            NewLeadSubject(domain), Channel.EMAIL, Purpose.DISCOVERY
        )
        if isinstance(answer, Blocked):
            raise WebsiteRefused(REFUSED_BLOCKED)
        if not isinstance(answer, Allowed):  # never proceed on anything else
            raise TypeError(f"assert_contactable answered a {type(answer).__name__}")
        holder = leads_repo.by_web_domain(domain)
        merged = False
        if holder is None:
            leads_repo.set_web_domain(lead.id, domain)
            survivor_id = lead.id
        else:
            other = _survivor(holder)
            if other.id == lead.id:  # only a Lead holding a domain keeps a loser's other domain
                raise WebsiteRefused(REFUSED_HAS_WEBSITE)
            older, newer = sorted((lead, other), key=_age)
            self.merge(older.id, newer.id)
            survivor_id, merged = older.id, True
        survivor = leads_repo.get(survivor_id)
        if survivor is None:  # merging never deletes a row
            raise RuntimeError(f"Lead {survivor_id} has no row")
        if survivor.web_domain is not None:
            self._enqueue_crawl(survivor.id, survivor.web_domain)
        return EnteredWebsite(survivor.id, merged)

    @command
    def record_place_coords(self, lead_id: str, lat: float, lng: float) -> None:
        """Store the Places location of the Lead ``lead_id``; see the module docstring."""
        for value, limit in ((lat, 90.0), (lng, 180.0)):
            if isinstance(value, bool) or not isinstance(value, float):
                raise TypeError("record_place_coords takes the coordinates as float")
            if not math.isfinite(value) or not -limit <= value <= limit:
                raise ValueError("record_place_coords: a coordinate is out of range")
        if not isinstance(lead_id, str) or not is_ulid(lead_id) or leads_repo.get(lead_id) is None:
            raise LookupError("record_place_coords: the Lead is unknown")
        leads_repo.set_place_coords(lead_id, lat, lng, self._clock.now())

    @use_case
    def purge_place_coords(self) -> int:
        """Null every Places location older than 30 days; the number of Leads purged."""
        with current().transaction():
            count = leads_repo.purge_place_coords(coords_cutoff(self._clock.now()))
        log.info(PLACE_COORDS_PURGED, extra={"count": count})
        return count

    @command
    def set_ch_number(self, lead_id: str, number: str) -> str | None:
        """Give the Lead's survivor the Companies House ``number``, merging per FR-9; the Lead
        it now belongs to, or ``None`` on a conflict. See the module docstring."""
        if not isinstance(lead_id, str) or not isinstance(number, str):
            raise TypeError("set_ch_number takes a Lead id and a company number as str")
        if COMPANY_NUMBER_PATTERN.fullmatch(number) is None:
            raise ValueError("set_ch_number takes a normalised Companies House number")
        lead = leads_repo.get(lead_id) if is_ulid(lead_id) else None
        if lead is None:
            raise LookupError("set_ch_number: the Lead is unknown")
        survivor = _survivor(lead)
        if survivor.ch_number == number:
            return survivor.id
        if survivor.ch_number is not None:  # never overwritten
            return None
        holder = leads_repo.by_ch_number(number)
        if holder is None:
            leads_repo.set_keys(survivor.id, ch_number=number)
            return survivor.id
        other = _survivor(holder)
        if other.id == survivor.id:  # held by a Lead merged into the survivor
            return survivor.id
        older, newer = sorted((survivor, other), key=_age)
        self.merge(older.id, newer.id)
        kept = leads_repo.get(older.id)
        if kept is None:  # merging never deletes a row
            raise RuntimeError(f"Lead {older.id} has no row")
        return kept.id if kept.ch_number == number else None

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
    (``merged``), the rejected rows, the imported rows with notes, the rows blocked by Market
    and the rows whose Lead the contactability gate blocked (``suppressed``), in row order."""

    imported: int
    merged: int
    rejected: tuple[RejectedRow, ...]
    noted: tuple[NotedRow, ...]
    blocked: tuple[BlockedRow, ...]
    suppressed: tuple[SuppressedRow, ...] = ()


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
                markets = setup_queries.markets()
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
                enabled_countries=frozenset(
                    market.definition.country_code
                    for market in markets
                    if market.definition.enabled
                ),
            )
        except ImportRefused as refused:
            log.info("lead_import_refused", extra={"field": refused.field})
            raise
        imported_at = self._clock.now()
        imported = merged = 0
        noted: list[NotedRow] = []
        suppressed: list[SuppressedRow] = []
        for start in range(0, len(plan.rows), BATCH_ROWS):
            batch = plan.rows[start : start + BATCH_ROWS]
            created = matched = 0
            batch_noted: list[NotedRow] = []
            batch_suppressed: list[SuppressedRow] = []
            try:
                with uow.transaction():
                    for row in batch:
                        upserted = self._discovery.upsert_lead(row.identity, LeadSource.CSV)
                        if upserted.lead_id is None:  # the gate blocked a new Lead
                            batch_suppressed.append(SuppressedRow(row.number, row.label))
                            continue
                        created += upserted.created
                        matched += not upserted.created
                        notes = list(row.notes)
                        if row.contact is not None:
                            added = self._research.add_contact(
                                upserted.lead_id,
                                row.contact,
                                source=ProvenanceSource.CSV,
                                detail=origin,
                                retrieved_at=imported_at,
                            )
                            if added.blocked is not None:
                                notes.append(_contact_note(added.blocked))
                            elif added.email_dropped:
                                notes.append(NOTE_EMAIL_DROPPED)
                        if notes:
                            batch_noted.append(NotedRow(row.number, row.label, tuple(notes)))
            except Exception as error:
                at_row = batch[0].number
                log.warning(
                    "lead_import_stopped",
                    extra={"at_row": at_row, "error": type(error).__name__},
                )
                summary = ImportSummary(
                    imported,
                    merged,
                    plan.rejected,
                    tuple(noted),
                    plan.blocked,
                    tuple(suppressed),
                )
                raise ImportStopped(summary, at_row) from error
            imported += created
            merged += matched
            noted.extend(batch_noted)
            suppressed.extend(batch_suppressed)
        summary = ImportSummary(
            imported, merged, plan.rejected, tuple(noted), plan.blocked, tuple(suppressed)
        )
        log.info(
            "lead_import_finished",
            extra={
                "imported": imported,
                "merged": merged,
                "rejected": len(plan.rejected),
                "noted": len(noted),
                "blocked": len(plan.blocked),
                "suppressed": len(suppressed),
            },
        )
        return summary


def _contact_note(blocked: Blocked) -> str:
    """The note of a row whose Contact the gate blocked: suppressed, else the Lead is closed."""
    if any(reason in SUPPRESSION_REASONS for reason in blocked.reasons):
        return NOTE_CONTACT_SUPPRESSED
    return NOTE_CONTACT_LEAD_CLOSED
