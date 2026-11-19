"""Companies House discovery runs (Story 8.4; FR-6, FR-9, FR-12, FR-13, FR-49, AD-3, AD-7,
AD-9, AD-10, AD-16, AD-17), owned by the ``discovery`` service. ``nextron.app.discovery.api``'s
docstring points here.

**``RegistryDiscovery(clock, meter, registry, place_searches, crawler, discovery, research,
queue)``** turns active UK companies the register finds for a Campaign's Segment into Leads.
``registry`` is the same ``RegistryPort`` instance the legal-form check uses, so both share the
adapter's rate window.

``@use_case request_run(campaign_id) -> Enqueued`` (the Discovery page's "Run Companies House
discovery"): in one transaction it reads the Campaign, the stored Markets and the Segment. The
Campaign must be stored and able to run (``queries.setup.campaign_active``), else
``RegistryRunRefused(REFUSED_CAMPAIGN)``; its Market's country must be ``GB`` and an enabled
Market's, else ``RegistryRunRefused(REFUSED_NOT_UK)``; its Segment must have SIC codes, else
``RegistryRunRefused(REFUSED_NO_QUERIES)``. Then it enqueues ``discover_registry`` on the bulk
lane, key ``discover_registry:<campaign ULID>:<query hash>``, payload ``{v: 1, campaign_id}``,
concurrency key ``domain:api.company-information.service.gov.uk``; while that job is live the
same request returns it (``created=False``). It logs ``registry_run_requested`` or
``registry_run_refused`` (the reason).

``@use_case run(campaign_id) -> bool`` (the job): ``False`` means the budget refused the run
at its start or the run ended ``deferred`` (the handler returns ``DEFERRED``).

1. **Start** (a transaction). Every ``running`` ``companies_house`` run row is ended ``failed``
   (logged ``registry_runs_recovered``): every such job shares one concurrency key, so such a
   row was left by a crash; a Places run's row is never touched. An unknown Campaign or Segment
   logs ``registry_run_skipped`` and returns ``True`` with no row. A Campaign that can't run, or
   whose Market isn't an enabled ``GB`` one: a ``stopped`` row, finished at once, and ``True``.
   **The pre-check (Story 8.1):** otherwise, still in that transaction and before any row is
   written, ``budget.allow(BudgetClass.BLOCKABLE, meter.snapshot(now), budget_limits())`` (the
   check every page makes); a refusal logs ``registry_run_deferred`` (``campaign_id``,
   ``reason`` ``ai_budget``) and ``run`` returns ``False`` with no run row and nothing
   fetched, so an hourly re-deferral adds no row.
   Otherwise a ``running`` row: source ``companies_house``, the Segment, ``GB``, no location,
   the ``registry_query_hash`` of the Segment's current queries; the page cap is
   ``registry_max_pages_per_run``.
2. **Pages.** ``registry_discovery.registry_searches`` in order, each from page 0, the next page
   only after a full page of 20, the whole run stopping at the cap. Before each page a short
   transaction re-checks the Campaign (as at the start; if not, the run ends ``stopped``) and
   then runs ``budget.allow(BudgetClass.BLOCKABLE, meter.snapshot(now), budget_limits())``; a
   refusal ends the run ``deferred``. Each page is ``registry.search(keyword, sic_codes,
   page)`` with no transaction open, and each hit is ingested.
3. **Each hit** (``ingest_hit``), its outcome counted on the run in the transaction that
   decides it:

   a. a Lead holds the company number (any stage, merged included): ``merged``, nothing
      fetched;
   b. ``registry.company(number)`` (no transaction): ``None``: counted as seen only; a
      non-retryable ``AdapterError``: logged (its code) and counted as seen only;
   c. ``registry_discovery.is_inactive`` (status not ``active``, or ``dormant`` accounts):
      ``inactive``;
   d. ``GB`` isn't an enabled Market: ``market_blocked``;
   e. a registered name that isn't a valid Lead name (``place_discovery.clean_name``): seen
      only;
   f. one transaction: ``discovery.upsert_lead(LeadIdentity(<registered name>, <segment>,
      ch_number=<number>, country_code="GB"), companies_house)`` (the AD-16 gate runs first in
      it: ``blocked``: ``suppressed``; matched: ``merged``); created: ``created``, and, when the
      record has a company type, ``research.record_legal_form(lead, <the registry-only
      finding>)``: ``unconfirmed``, the type, the status and the registry quote with the
      accounts category and SIC codes, read at ``retrieved_at`` (FR-12: only the Lead's site
      can confirm it, through ``check_legal_form``).

   Then, for a created Lead only:

   g. **The website** (``nextron.app.discovery.place_ingest.own_site``): with no registered
      office postcode, decided with none. Otherwise one ``PlaceSearches.page(<registered
      name>, <postcode>)`` (its own per-call budget check, Places Enterprise; no transaction
      open). ``None`` (refused) or a Places ``AdapterError``: undecided, ``website_lookup_at``
      stays NULL. No candidate, or the first candidate's ``website_uri`` isn't an own website
      ``own_site`` can fetch: decided with none. Else, in one transaction,
      ``discovery.attach_web_domain(lead, domain)`` (no other Lead holds it and the gate
      allows it; it enqueues ``enrich_lead``). Each decided lookup runs
      ``discovery.record_website_lookup`` in the same transaction. A Lead left without a website
      adds 1 to ``no_website``. Nothing from the Places candidate is stored.
   h. **Officers:** ``registry.officers(number)`` (a non-retryable ``AdapterError``: logged, no
      officers), each turned into a Contact by ``registry_discovery.kept_officer`` or
      discarded, and the kept ones stored in one transaction through
      ``research.add_contact(lead, candidate, source=registry, detail="Companies House API
      officers, company <number>", retrieved_at=<when they were read>)``: no email; AD-16 runs
      inside ``add_contact``.

4. **End.** ``finished``. A ``registry_rate_limited`` ``AdapterError`` anywhere in steps 2 to 3
   ends the run ``deferred`` (the Leads already created stay). Any other exception ends it
   ``failed`` in a transaction of its own and is raised again (a retryable ``AdapterError`` is
   retried by the runner).

**Scheduled runs (Story 8.10).** ``@use_case run_scheduled(campaign_id, scheduled_for) -> bool``
(a ``discover_registry`` job the weekday top-up queued, ``nextron.app.discovery.topup``)
searches one registry query and is paced:

1. **Start** (a transaction). The stale-row recovery as above. Then, each logging
   ``registry_run_skipped`` (``campaign_id``, ``reason``) and returning ``True`` with no row: an
   unknown Campaign or Segment (``unknown``); ``scheduled_for`` that isn't the Operator's local
   date now (``not_today``); a Campaign that can't run or whose Market isn't an enabled ``GB``
   one (``campaign_not_active``); a Companies House run of the Campaign for ``scheduled_for``
   that already ended ``finished``, ``paced`` or ``target_reached`` (``already_ran``).
   **Rotation:** the pairs are the Segment's
   ``registry_searches`` in order, location NULL, each hashed as ``registry_query_hash`` of
   that single search (its keyword, or none, with every SIC code);
   ``discovery_topup.next_pair`` picks one against ``runs.last_finished(companies_house,
   segment)`` and ``discovery_requery_days``; none due logs ``discovery_nothing_due``
   (``source``, ``campaign_id``) and returns ``True`` with no row. Then the budget pre-check as
   above, then a ``running`` row with that pair's hash and ``scheduled_for``.
2. **Pages** as above, for the one search, with ``daily.target_reached`` checked before each
   page and each hit: reached ends the run ``target_reached``.
3. **Each hit** as above, except that the website lookup's ``PlaceSearches.page`` is paced
   (``pace=Pace(<local date>, <zone>)``): ``Paced`` leaves the lookup undecided
   (``website_lookup_at`` NULL, ``no_website`` counted) and the run goes on.

Logs hold ids, outcomes, codes and counts only: never a company or officer name, a postcode, a
keyword, a website or anything Places returned.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from nextron.app.discovery.api import Discovery
from nextron.app.discovery.daily import DISCOVERY_NOTHING_DUE, sender_timezone, target_reached
from nextron.app.discovery.place_ingest import own_site
from nextron.app.discovery.places import REASON_AI_BUDGET, Pace, Paced, PlaceSearches
from nextron.app.jobqueue.api import Enqueued, JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import Research
from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.uow import current, use_case
from nextron.domain.budget import BudgetClass, allow
from nextron.domain.contacts import ProvenanceSource
from nextron.domain.discovery_topup import local_date_of, next_pair
from nextron.domain.errors import DomainError
from nextron.domain.job import VERSION_FIELD, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.legal_form import LegalFormFinding, LegalFormStatus, registry_quote
from nextron.domain.markets import GB, market_for_country
from nextron.domain.place_discovery import (
    SOURCE_COMPANIES_HOUSE,
    Outcome,
    RunStatus,
    clean_name,
)
from nextron.domain.registry_discovery import (
    CAMPAIGN_ID_FIELD,
    DISCOVER_REGISTRY_JOB,
    PAYLOAD_VERSION,
    REGISTRY_CONCURRENCY_KEY,
    is_inactive,
    kept_officer,
    officers_detail,
    registry_query_hash,
    registry_searches,
)
from nextron.domain.segments import RegistryQueries, SegmentDef
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock
from nextron.ports.crawler import CrawlerPort
from nextron.ports.errors import AdapterError
from nextron.ports.registry import (
    PAGE_SIZE,
    REGISTRY_RATE_LIMITED,
    RegistryCompany,
    RegistryPort,
)
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

REGISTRY_RUN_REQUESTED: Final = "registry_run_requested"
REGISTRY_RUN_REFUSED: Final = "registry_run_refused"
REGISTRY_RUN_SKIPPED: Final = "registry_run_skipped"
REGISTRY_RUN_ENDED: Final = "registry_run_ended"
REGISTRY_RUNS_RECOVERED: Final = "registry_runs_recovered"
REGISTRY_RUN_DEFERRED: Final = "registry_run_deferred"
REGISTRY_HIT_INGESTED: Final = "registry_hit_ingested"
REGISTRY_CALL_FAILED: Final = "registry_call_failed"
WEBSITE_LOOKUP_FAILED: Final = "registry_website_lookup_failed"
# Why a request was refused
REFUSED_CAMPAIGN: Final = "campaign_not_active"
REFUSED_NOT_UK: Final = "campaign_not_uk"
REFUSED_NO_QUERIES: Final = "no_registry_queries"
# Why a scheduled run was skipped, as logged (Story 8.10; else REFUSED_CAMPAIGN)
SKIPPED_UNKNOWN: Final = "unknown"
SKIPPED_NOT_TODAY: Final = "not_today"
SKIPPED_ALREADY_RAN: Final = "already_ran"
REFUSAL_MESSAGES: Final = {
    REFUSED_CAMPAIGN: "That Campaign isn't active, so it can't run discovery. Nothing was queued.",
    REFUSED_NOT_UK: (
        "Companies House discovery runs only for a Campaign whose Market is the UK, and the UK "
        "Market must be enabled. Nothing was queued."
    ),
    REFUSED_NO_QUERIES: (
        "The Campaign's Segment has no Companies House SIC codes, so there is nothing to "
        "search. Nothing was queued."
    ),
}
# What a created Lead's website lookup came to, as logged
WEBSITE_ATTACHED: Final = "attached"
WEBSITE_NONE: Final = "none"
WEBSITE_UNDECIDED: Final = "undecided"


class RegistryRunRefused(DomainError):
    """A "Run Companies House discovery" request that can't run; ``reason`` is one of the
    ``REFUSED_*`` codes and ``message`` what the page says."""

    code = "registry_run_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(REFUSAL_MESSAGES[reason])
        self.reason = reason
        self.message = REFUSAL_MESSAGES[reason]


class _Deferred(Exception):
    """The budget or the register's rate limit ended the run ``deferred``."""


@dataclass(frozen=True, slots=True)
class _Run:
    """A started run: its row, Campaign, Segment and page cap; a scheduled run's one search and
    ``pace`` (Story 8.10), both ``None`` for a manual run."""

    run_id: str
    campaign_id: str
    segment_id: str
    segment: SegmentDef
    max_pages: int
    searches: tuple[tuple[str | None, tuple[str, ...]], ...] | None = None
    pace: Pace | None = None


def single_search_hash(keyword: str | None, sic_codes: tuple[str, ...]) -> str:
    """The ``query_hash`` of one registry search: ``registry_query_hash`` of that keyword (or
    none) with its SIC codes (Story 8.10's rotation)."""
    keywords = () if keyword is None else (keyword,)
    return registry_query_hash(RegistryQueries(keywords=keywords, sic_codes=sic_codes))


def _campaign_country(market_id: str) -> str | None:
    for market in setup_queries.markets():
        if market.id == market_id:
            return market.definition.country_code
    return None


def _uk_enabled() -> bool:
    """Whether ``GB`` is an enabled Market's country, read in the current unit of work."""
    markets = (market.definition for market in setup_queries.markets())
    return market_for_country(GB, markets) is not None


def _may_run(campaign_id: str) -> bool:
    """Whether the Campaign is stored, may run, and its Market is an enabled ``GB`` one."""
    campaign = setup_queries.campaign(campaign_id)
    return (
        campaign is not None
        and setup_queries.campaign_active(campaign.id)
        and _campaign_country(campaign.market_id) == GB
        and _uk_enabled()
    )


class RegistryDiscovery:
    """Companies House discovery runs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        meter: UsageMeterPort,
        registry: RegistryPort,
        place_searches: PlaceSearches,
        crawler: CrawlerPort,
        discovery: Discovery,
        research: Research,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._meter = meter
        self._registry = registry
        self._searches = place_searches
        self._crawler = crawler
        self._discovery = discovery
        self._research = research
        self._queue = queue

    # The request ------------------------------------------------------------------------------

    @use_case
    def request_run(self, campaign_id: str) -> Enqueued:
        """Queue a Companies House discovery run of the Campaign; see the module docstring."""
        try:
            with current().transaction():
                campaign = setup_queries.campaign(campaign_id)
                if campaign is None or not setup_queries.campaign_active(campaign.id):
                    raise RegistryRunRefused(REFUSED_CAMPAIGN)
                if _campaign_country(campaign.market_id) != GB or not _uk_enabled():
                    raise RegistryRunRefused(REFUSED_NOT_UK)
                segment = setup_queries.segment_by_id(campaign.segment_id)
                queries = None if segment is None else segment.definition.discovery_queries
                if queries is None or not registry_searches(queries.registry):
                    raise RegistryRunRefused(REFUSED_NO_QUERIES)
                enqueued = self._queue.enqueue(
                    DISCOVER_REGISTRY_JOB,
                    scope=campaign.id,
                    discriminator=registry_query_hash(queries.registry),
                    payload={VERSION_FIELD: PAYLOAD_VERSION, CAMPAIGN_ID_FIELD: campaign.id},
                    lane=Lane.BULK,
                    concurrency_key=REGISTRY_CONCURRENCY_KEY,
                )
        except RegistryRunRefused as refused:
            log.info(REGISTRY_RUN_REFUSED, extra={"reason": refused.reason})
            raise
        log.info(
            REGISTRY_RUN_REQUESTED,
            extra={
                "campaign_id": campaign.id,
                "job_id": enqueued.job_id,
                "job_created": enqueued.created,
            },
        )
        return enqueued

    # The run ----------------------------------------------------------------------------------

    @use_case
    def run(self, campaign_id: str) -> bool:
        """Run one Companies House discovery; ``False`` when it ended ``deferred``. See the
        module docstring."""
        return self._run_started(self._start(campaign_id))

    @use_case
    def run_scheduled(self, campaign_id: str, scheduled_for: date) -> bool:
        """Run one scheduled Companies House discovery (Story 8.10); ``False`` when it was
        deferred. See the module docstring."""
        return self._run_started(self._start_scheduled(campaign_id, scheduled_for))

    def _run_started(self, started: _Run | RunStatus | None) -> bool:
        """Steps 2 to 4 of a started run; see ``run``."""
        if started is RunStatus.DEFERRED:
            return False
        if not isinstance(started, _Run):
            return True
        try:
            status = self._pages(started)
        except _Deferred:
            status = RunStatus.DEFERRED
        except AdapterError as error:
            if error.code != REGISTRY_RATE_LIMITED:
                self._end(started.run_id, RunStatus.FAILED)
                raise
            status = RunStatus.DEFERRED
        except Exception:
            self._end(started.run_id, RunStatus.FAILED)
            raise
        self._end(started.run_id, status)
        return status is not RunStatus.DEFERRED

    def _start_scheduled(self, campaign_id: str, scheduled_for: date) -> _Run | RunStatus | None:
        """Scheduled step 1: the started run, ``RunStatus.DEFERRED`` (no row) or ``None``."""
        now = self._clock.now()
        with current().transaction():
            stale = runs_repo.fail_running(SOURCE_COMPANIES_HOUSE, now)
            if stale:
                log.warning(REGISTRY_RUNS_RECOVERED, extra={"count": stale})
            campaign = setup_queries.campaign(campaign_id)
            segment = (
                None if campaign is None else setup_queries.segment_by_id(campaign.segment_id)
            )
            timezone = sender_timezone()
            today = local_date_of(now, timezone)
            if campaign is None or segment is None:
                skipped: str | None = SKIPPED_UNKNOWN
            elif scheduled_for != today:
                skipped = SKIPPED_NOT_TODAY
            elif not _may_run(campaign.id):
                skipped = REFUSED_CAMPAIGN
            elif runs_repo.ran_scheduled(SOURCE_COMPANIES_HOUSE, campaign.id, scheduled_for):
                skipped = SKIPPED_ALREADY_RAN
            else:
                skipped = None
            if skipped is not None or campaign is None or segment is None:
                log.info(
                    REGISTRY_RUN_SKIPPED, extra={"campaign_id": campaign_id, "reason": skipped}
                )
                return None
            pairs = {
                (None, single_search_hash(keyword, codes)): (keyword, codes)
                for keyword, codes in registry_searches(
                    segment.definition.discovery_queries.registry
                )
            }
            chosen = next_pair(
                tuple(pairs),
                runs_repo.last_finished(SOURCE_COMPANIES_HOUSE, segment.id),
                now,
                int(setup_queries.get(Key.DISCOVERY_REQUERY_DAYS)),
            )
            if chosen is None:
                log.info(
                    DISCOVERY_NOTHING_DUE,
                    extra={"source": SOURCE_COMPANIES_HOUSE, "campaign_id": campaign.id},
                )
                return None
            if not self._budget_allows():
                log.info(
                    REGISTRY_RUN_DEFERRED,
                    extra={"campaign_id": campaign.id, "reason": REASON_AI_BUDGET},
                )
                return RunStatus.DEFERRED
            run_id = runs_repo.insert(
                source=SOURCE_COMPANIES_HOUSE,
                campaign_id=campaign.id,
                segment_id=segment.id,
                country_code=GB,
                location=None,
                query_hash=chosen[1],
                status=RunStatus.RUNNING,
                started_at=now,
                finished_at=None,
                scheduled_for=scheduled_for,
            )
            max_pages = int(setup_queries.get(Key.REGISTRY_MAX_PAGES_PER_RUN))
        return _Run(
            run_id,
            campaign.id,
            segment.id,
            segment.definition,
            max_pages,
            (pairs[chosen],),
            Pace(today, timezone),
        )

    def _start(self, campaign_id: str) -> _Run | RunStatus | None:
        """Step 1: the started run, ``RunStatus.DEFERRED`` when the budget refuses the run
        (no row written), or ``None`` when there is nothing to fetch."""
        now = self._clock.now()
        with current().transaction():
            stale = runs_repo.fail_running(SOURCE_COMPANIES_HOUSE, now)
            if stale:
                log.warning(REGISTRY_RUNS_RECOVERED, extra={"count": stale})
            campaign = setup_queries.campaign(campaign_id)
            segment = (
                None if campaign is None else setup_queries.segment_by_id(campaign.segment_id)
            )
            if campaign is None or segment is None:
                log.info(REGISTRY_RUN_SKIPPED, extra={"campaign_id": campaign_id})
                return None
            queries = segment.definition.discovery_queries.registry
            may_run = _may_run(campaign.id)
            if may_run and not self._budget_allows():  # Story 8.1: before any row is written
                log.info(
                    REGISTRY_RUN_DEFERRED,
                    extra={"campaign_id": campaign.id, "reason": REASON_AI_BUDGET},
                )
                return RunStatus.DEFERRED
            status = RunStatus.RUNNING if may_run else RunStatus.STOPPED
            run_id = runs_repo.insert(
                source=SOURCE_COMPANIES_HOUSE,
                campaign_id=campaign.id,
                segment_id=segment.id,
                country_code=GB,
                location=None,
                query_hash=registry_query_hash(queries),
                status=status,
                started_at=now,
                finished_at=None if may_run else now,
            )
            max_pages = int(setup_queries.get(Key.REGISTRY_MAX_PAGES_PER_RUN))
        if not may_run:
            self._log_end(run_id, status)
            return None
        return _Run(run_id, campaign.id, segment.id, segment.definition, max_pages)

    def _pages(self, run: _Run) -> RunStatus:
        """Every page of the run, each hit ingested: ``finished`` or ``stopped`` (a scheduled
        run also ``target_reached``, Story 8.10); ``_Deferred`` when the budget refused a
        page."""
        fetched = 0
        searches = (
            run.searches
            if run.searches is not None
            else registry_searches(run.segment.discovery_queries.registry)
        )
        for keyword, sic_codes in searches:
            page = 0
            while fetched < run.max_pages:
                if not self._may_continue(run):
                    return RunStatus.STOPPED
                if self._target_reached(run):
                    return RunStatus.TARGET_REACHED
                hits = self._registry.search(keyword, sic_codes, page)
                fetched += 1
                for hit in hits:
                    if self._target_reached(run):
                        return RunStatus.TARGET_REACHED
                    self.ingest_hit(
                        run.run_id, hit.number, run.segment_id, run.segment, pace=run.pace
                    )
                if len(hits) < PAGE_SIZE:
                    break
                page += 1
            if fetched >= run.max_pages:
                break
        return RunStatus.FINISHED

    def _may_continue(self, run: _Run) -> bool:
        """Whether the Campaign may still run (``False``: stop); ``_Deferred`` when the budget
        refuses the next page."""
        with current().transaction():
            if not _may_run(run.campaign_id):
                return False
            allowed = self._budget_allows()
        if not allowed:
            raise _Deferred
        return True

    def _target_reached(self, run: _Run) -> bool:
        """Whether a scheduled run must stop for the day's Lead target (never a manual one)."""
        if run.pace is None:
            return False
        with current().transaction():
            return target_reached(self._clock.now())

    def _budget_allows(self) -> bool:
        """The budget's check for the next Companies House call, inside the caller's open
        transaction: a fresh ``meter.snapshot`` against ``budget_limits()``."""
        return allow(
            BudgetClass.BLOCKABLE,
            self._meter.snapshot(self._clock.now()),
            setup_queries.budget_limits(),
        )

    def _end(self, run_id: str, status: RunStatus) -> None:
        with current().transaction():
            runs_repo.finish(run_id, status, self._clock.now())
        self._log_end(run_id, status)

    @staticmethod
    def _log_end(run_id: str, status: RunStatus) -> None:
        log.info(REGISTRY_RUN_ENDED, extra={"run_id": run_id, "status": str(status)})

    # One hit ----------------------------------------------------------------------------------

    def ingest_hit(
        self,
        run_id: str,
        number: str,
        segment_id: str,
        segment: SegmentDef,
        *,
        pace: Pace | None = None,
    ) -> Outcome | None:
        """Turn the company ``number`` a search found into a Lead, or count why not; the
        outcome (``None``: seen only). Runs inside the run's use case, with no transaction
        open; ``pace`` paces a scheduled run's website lookup (Story 8.10)."""
        with current().transaction():
            if leads_repo.by_ch_number(number) is not None:
                runs_repo.count(run_id, Outcome.MERGED)
                self._log_hit(run_id, Outcome.MERGED, None)
                return Outcome.MERGED
        profile = self._profile(number)
        retrieved_at = self._clock.now()
        lead_id: str | None = None
        with current().transaction():
            outcome, lead_id = self._decided(profile, segment_id, retrieved_at)
            runs_repo.count(run_id, outcome)
        if outcome is Outcome.CREATED and lead_id is not None and profile is not None:
            website = self._website(run_id, lead_id, profile, pace)
            officers = self._officers(lead_id, profile.number, segment)
        else:
            website, officers = None, 0
        self._log_hit(run_id, outcome, lead_id, website, officers)
        return outcome

    def _profile(self, number: str) -> RegistryCompany | None:
        """The company's profile, or ``None`` (no such company, or a non-retryable error)."""
        try:
            return self._registry.company(number)
        except AdapterError as error:
            if error.retryable:
                raise
            log.warning(REGISTRY_CALL_FAILED, extra={"operation": "company", "code": error.code})
            return None

    def _decided(
        self, profile: RegistryCompany | None, segment_id: str, retrieved_at: datetime
    ) -> tuple[Outcome | None, str | None]:
        """Steps 3b to 3f, in the caller's transaction: the outcome and the Lead."""
        if profile is None:
            return None, None
        if is_inactive(profile.company_status, profile.accounts_category):
            return Outcome.INACTIVE, None
        if not _uk_enabled():
            return Outcome.MARKET_BLOCKED, None
        name = clean_name(profile.name)
        if name is None:
            return None, None
        identity = LeadIdentity(
            name=name, segment_id=segment_id, ch_number=profile.number, country_code=GB
        )
        upserted = self._discovery.upsert_lead(identity, LeadSource.COMPANIES_HOUSE)
        if upserted.blocked is not None or upserted.lead_id is None:
            return Outcome.SUPPRESSED, None
        if not upserted.created:
            return Outcome.MERGED, upserted.lead_id
        if profile.company_type is not None and profile.company_status is not None:
            self._research.record_legal_form(
                upserted.lead_id, _registry_finding(profile, retrieved_at)
            )
        return Outcome.CREATED, upserted.lead_id

    def _website(
        self, run_id: str, lead_id: str, profile: RegistryCompany, pace: Pace | None = None
    ) -> str:
        """Step 3g: the created Lead's one website lookup; what it came to."""
        attached = False
        if profile.office_postcode is not None:
            try:
                page = self._searches.page(profile.name, profile.office_postcode, pace=pace)
            except AdapterError as error:
                log.warning(WEBSITE_LOOKUP_FAILED, extra={"lead_id": lead_id, "code": error.code})
                page = None
            except Paced:  # Story 8.10: today's allowance is used; left undecided
                page = None
            if page is None:  # refused, paced or failed: undecided, tried again by no one here
                with current().transaction():
                    runs_repo.add_outcome(run_id, Outcome.NO_WEBSITE)
                return WEBSITE_UNDECIDED
            first = page.candidates[0] if page.candidates else None
            site = None if first is None else own_site(self._crawler, first.website_uri)
            if site is not None:
                with current().transaction():
                    attached = self._discovery.attach_web_domain(lead_id, site.web_domain)
                    self._discovery.record_website_lookup(lead_id)
                    if not attached:
                        runs_repo.add_outcome(run_id, Outcome.NO_WEBSITE)
                return WEBSITE_ATTACHED if attached else WEBSITE_NONE
        with current().transaction():
            self._discovery.record_website_lookup(lead_id)
            runs_repo.add_outcome(run_id, Outcome.NO_WEBSITE)
        return WEBSITE_NONE

    def _officers(self, lead_id: str, number: str, segment: SegmentDef) -> int:
        """Step 3h: the created Lead's kept officers as Contacts; how many were stored."""
        try:
            officers = self._registry.officers(number)
        except AdapterError as error:
            if error.retryable:
                raise
            log.warning(
                REGISTRY_CALL_FAILED, extra={"operation": "officers", "code": error.code}
            )
            return 0
        retrieved_at = self._clock.now()
        kept = [
            candidate
            for officer in officers
            if (candidate := kept_officer(officer, segment)) is not None
        ]
        stored = 0
        if kept:
            with current().transaction():
                for candidate in kept:
                    added = self._research.add_contact(
                        lead_id,
                        candidate,
                        source=ProvenanceSource.REGISTRY,
                        detail=officers_detail(number),
                        retrieved_at=retrieved_at,
                    )
                    stored += added.contact_id is not None
        return stored

    @staticmethod
    def _log_hit(
        run_id: str,
        outcome: Outcome | None,
        lead_id: str | None,
        website: str | None = None,
        officers: int = 0,
    ) -> None:
        log.info(
            REGISTRY_HIT_INGESTED,
            extra={
                "run_id": run_id,
                "outcome": None if outcome is None else str(outcome),
                "lead_id": lead_id,
                "website": website,
                "contacts": officers,
            },
        )


def _registry_finding(profile: RegistryCompany, retrieved_at: datetime) -> LegalFormFinding:
    """The registry-only legal-form finding of a company the run created a Lead for."""
    return LegalFormFinding(
        status=LegalFormStatus.UNCONFIRMED,
        number=profile.number,
        legal_form=profile.company_type,
        company_status=profile.company_status,
        site=None,
        registry_quote=registry_quote(
            number=profile.number,
            name=profile.name,
            company_type=profile.company_type,
            company_status=profile.company_status,
            office_postcode=profile.office_postcode,
            accounts_category=profile.accounts_category,
            sic_codes=profile.sic_codes,
        ),
        retrieved_at=retrieved_at,
    )
