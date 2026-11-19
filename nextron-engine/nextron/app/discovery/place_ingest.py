"""Places discovery runs (Story 8.3; FR-5, FR-9, FR-2, FR-49, AD-3, AD-7, AD-9, AD-10, AD-16),
owned by the ``discovery`` service. ``nextron.app.discovery.api``'s docstring points here.

**``PlaceDiscovery(clock, place_searches, crawler, discovery, queue)``** turns Google Places
candidates into Leads named and keyed from their own website.

``@use_case request_run(campaign_id, location_hash) -> Enqueued`` (the Discovery page's "Run
Places discovery"): in one transaction it reads the Campaign, the stored Markets and the
``places_discovery_locations`` setting. The Campaign must be stored and able to run
(``queries.setup.campaign_active``), else ``PlacesRunRefused(REFUSED_CAMPAIGN)``; the location
must be an item of the setting (by ``location_hash``) whose country code is the Campaign's
Market's, and that Market must be enabled, else ``PlacesRunRefused(REFUSED_LOCATION)``; the
Campaign's Segment must have Places queries, else ``PlacesRunRefused(REFUSED_NO_QUERIES)``.
Then it enqueues the ``discover_places`` job on the bulk lane, key
``discover_places:<campaign ULID>:<location hash>``, payload ``{v: 1, campaign_id, location:
<location hash>, country_code: <code in lowercase>}`` and concurrency key
``domain:places.googleapis.com``; while that job is live the same request returns it
(``created=False``). It logs ``places_run_requested`` (ids, hash and whether it was created)
or ``places_run_refused`` (the reason).

``@use_case run(campaign_id, location_hash, country_code) -> bool`` (the job): ``False`` means
the budget refused the run or a page (the handler returns ``DEFERRED``).

1. **Start** (a transaction). First every ``running`` ``places`` run row is ended ``failed``
   (finished now; logged ``places_runs_recovered`` with the count): all ``discover_places``
   jobs share one concurrency key, so such a row was left by a crash (a Companies House run's
   row is never touched: Story 8.4). Then an unknown Campaign, or a
   location hash and code that no item of the setting holds any more: logs
   ``places_run_skipped`` and returns ``True`` with no run row.
   A Campaign that can't run now: a ``stopped`` run row (finished at once) and ``True``, with
   nothing fetched. **The pre-check (Story 8.1):** otherwise, still in that transaction and
   before any row is written, ``PlaceSearches.refusal()`` (the same AI-budget and quota check
   every page makes); a refusal logs ``places_run_deferred`` (``campaign_id``, ``reason``)
   and ``run`` returns ``False`` with no run row and nothing fetched, so an hourly
   re-deferral adds no row.
   Otherwise a ``running`` row: source ``places``, the Campaign's Segment, the
   code (uppercase), the location's text, ``query_hash`` of the Segment's Places queries,
   ``started_at`` = now, and the page cap ``places_max_pages_per_run``.
2. **Pages.** The queries run in seed order, each paged by its ``next_page_token`` until there is
   none, and the whole run stops once it has fetched the cap's pages. Before each page a short
   transaction re-checks that the Campaign may still run and that its Market's country is still
   the run's; if not, the run ends ``stopped`` (the job completes). Each page is
   ``PlaceSearches.page(query, location, token)`` with no transaction open (the budget's
   per-call check comes first in it). ``None``: the run ends ``deferred`` and ``run`` returns
   ``False``; the Leads already created stay. A page token is never stored: a deferred run's
   job starts again from page 1, as a new run.
3. **Each candidate** (``ingest_candidate``), in memory only, its outcome counted on the run in
   the transaction that decides it:

   a. a ``place_id`` that isn't a valid key (``identity.normalize_place_id``): counted as seen
      only; otherwise the normalised value is the one looked up and stored;
   b. the code isn't an enabled Market's (``markets.market_for_country``): ``market_blocked``;
   c. a Lead holds the ``place_id`` (any stage, merged included): ``merged``, nothing fetched;
   d. **the website**, outside any transaction: no ``website_uri``, one the egress rules
      refuse (``fetch_policy.url_refusal``: the FR-49 denylist included), or one on a shared
      platform (``leads.is_shared_platform``): ``no_website``, nothing fetched. Otherwise the
      origin's robots.txt is read (``research.api.robots_rule``, ``crawl.MIN_INTERVAL_S``) and
      the homepage (``<origin>/``) fetched at its interval. A disallowed homepage, an
      ``AdapterError``, an answer that isn't 2xx, a media type outside
      ``crawl.HTML_MEDIA_TYPES``, or a final URL the egress rules refuse, that has no
      registrable domain or is on a shared platform: ``no_website``;
   e. one transaction: ``discovery.upsert_lead(LeadIdentity(name, segment, web_domain=<the
      final URL's registrable domain>, place_id, country_code), places)``, the name being
      ``place_discovery.site_name`` of the homepage, else the domain. The contactability gate
      runs inside it first; ``blocked``: ``suppressed``, nothing written. Created:
      ``created``, and when the candidate has coordinates, ``record_place_coords`` (now);
      ``upsert_lead`` enqueues its ``enrich_lead`` job. Matched (by domain; a Lead without a
      ``place_id`` gets it): ``merged``.

   The candidate's ``display_name`` is never read, and nothing it holds but its ``place_id``
   and coordinates is written anywhere.
4. **End.** ``finished``. An exception from steps 2 to 3 ends the run ``failed`` in a
   transaction of its own and is raised again (a retryable ``AdapterError`` from Places is
   retried by the runner).

Logs hold ids, codes, counts and outcomes only: never a Places name, website, coordinate, page
token, query or location text.
"""

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Final

from nextron.app.discovery.api import Discovery, UpsertedLead, website_domain
from nextron.app.discovery.places import PlaceSearches
from nextron.app.jobqueue.api import Enqueued, JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.app.research.api import robots_rule
from nextron.db.repos.discovery import leads as leads_repo
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.uow import current, use_case
from nextron.domain.crawl import HTML_MEDIA_TYPES, MIN_INTERVAL_S
from nextron.domain.errors import DomainError
from nextron.domain.fetch_policy import check_url
from nextron.domain.identity import IdentityError, normalize_place_id
from nextron.domain.job import VERSION_FIELD, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import market_for_country
from nextron.domain.place_discovery import (
    CAMPAIGN_ID_FIELD,
    COUNTRY_CODE_FIELD,
    DISCOVER_PLACES_JOB,
    LOCATION_FIELD,
    PAYLOAD_VERSION,
    PLACES_CONCURRENCY_KEY,
    SOURCE_PLACES,
    DiscoveryLocation,
    Outcome,
    RunStatus,
    parse_location,
    query_hash,
    site_name,
)
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock
from nextron.ports.crawler import CrawlerPort, FetchedPage
from nextron.ports.errors import AdapterError
from nextron.ports.places import PlaceCandidate

log = logging.getLogger(__name__)

PLACES_RUN_REQUESTED: Final = "places_run_requested"
PLACES_RUN_REFUSED: Final = "places_run_refused"
PLACES_RUN_SKIPPED: Final = "places_run_skipped"
PLACES_RUN_ENDED: Final = "places_run_ended"
PLACES_RUNS_RECOVERED: Final = "places_runs_recovered"
PLACES_RUN_DEFERRED: Final = "places_run_deferred"
PLACE_CANDIDATE_INGESTED: Final = "place_candidate_ingested"
# Why a request was refused
REFUSED_CAMPAIGN: Final = "campaign_not_active"
REFUSED_LOCATION: Final = "location_not_in_market"
REFUSED_NO_QUERIES: Final = "no_places_queries"
REFUSAL_MESSAGES: Final = {
    REFUSED_CAMPAIGN: "That Campaign isn't active, so it can't run discovery. Nothing was queued.",
    REFUSED_LOCATION: (
        "That location isn't one of the Campaign's Market's Places discovery locations. "
        "Nothing was queued."
    ),
    REFUSED_NO_QUERIES: (
        "The Campaign's Segment has no Places search queries, so there is nothing to search. "
        "Nothing was queued."
    ),
}


class PlacesRunRefused(DomainError):
    """A "Run Places discovery" request that can't run; ``reason`` is ``REFUSED_CAMPAIGN`` or
    ``REFUSED_LOCATION`` and ``message`` what the page says."""

    code = "places_run_refused"

    def __init__(self, reason: str) -> None:
        super().__init__(REFUSAL_MESSAGES[reason])
        self.reason = reason
        self.message = REFUSAL_MESSAGES[reason]


@dataclass(frozen=True, slots=True)
class _Run:
    """A started run: its row, Segment, location, queries and page cap."""

    run_id: str
    campaign_id: str
    segment_id: str
    location: DiscoveryLocation
    queries: tuple[str, ...]
    max_pages: int


@dataclass(frozen=True, slots=True)
class OwnSite:
    """An organization's own website, as its homepage's final URL gave it: its registrable
    domain and the name the homepage shows (else the domain)."""

    web_domain: str
    name: str


def configured_locations() -> tuple[DiscoveryLocation, ...]:
    """The ``places_discovery_locations`` setting's items, read in the current unit of work;
    an item that no longer parses is left out."""
    found = []
    for item in setup_queries.get(Key.PLACES_DISCOVERY_LOCATIONS):
        try:
            found.append(parse_location(item))
        except ValueError:
            continue
    return tuple(found)


def _location(location_hash: str, country_code: str) -> DiscoveryLocation | None:
    for location in configured_locations():
        if location.hash == location_hash and location.country_code == country_code:
            return location
    return None


def _market_country(market_id: str) -> str | None:
    for market in setup_queries.markets():
        if market.id == market_id:
            return market.definition.country_code
    return None


def _enabled_market(country_code: str) -> bool:
    """Whether ``country_code`` is an enabled Market's, read in the current unit of work."""
    markets = (market.definition for market in setup_queries.markets())
    return market_for_country(country_code, markets) is not None


def _origin(url: str) -> str:
    target = check_url(url)
    return f"{target.scheme}://{urllib.parse.urlsplit(target.url).netloc}"


def own_site(crawler: CrawlerPort, website_uri: str | None) -> OwnSite | None:
    """The organization's own website at ``website_uri``, or ``None`` when it has none it may
    fetch (step 3d of the module docstring; Story 8.4's website lookup reuses it). Call it
    with no transaction open."""
    if website_uri is None or website_domain(website_uri) is None:
        return None  # nothing fetched: missing, refused, denylisted or a shared platform
    origin = _origin(website_uri)
    try:
        robots = crawler.fetch_robots(f"{origin}/robots.txt", min_interval_s=MIN_INTERVAL_S)
    except AdapterError as error:
        allows, interval, _ = robots_rule(error)
    else:
        allows, interval, _ = robots_rule(robots)
    home = f"{origin}/"
    if not allows(home):
        return None
    try:
        fetched = crawler.fetch_page(home, site=None, min_interval_s=interval)
    except AdapterError:
        return None
    return _site_of(fetched)


class PlaceDiscovery:
    """Places discovery runs; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        place_searches: PlaceSearches,
        crawler: CrawlerPort,
        discovery: Discovery,
        queue: JobQueue,
    ) -> None:
        self._clock = clock
        self._searches = place_searches
        self._crawler = crawler
        self._discovery = discovery
        self._queue = queue

    # The request ------------------------------------------------------------------------------

    @use_case
    def request_run(self, campaign_id: str, location_hash: str) -> Enqueued:
        """Queue a Places discovery run of the Campaign in the location; see the module
        docstring."""
        try:
            with current().transaction():
                campaign = setup_queries.campaign(campaign_id)
                if campaign is None or not setup_queries.campaign_active(campaign.id):
                    raise PlacesRunRefused(REFUSED_CAMPAIGN)
                country = _market_country(campaign.market_id)
                location = (
                    None
                    if country is None or not _enabled_market(country)
                    else _location(location_hash, country)
                )
                if location is None:
                    raise PlacesRunRefused(REFUSED_LOCATION)
                segment = setup_queries.segment_by_id(campaign.segment_id)
                if segment is None or not segment.definition.discovery_queries.places:
                    raise PlacesRunRefused(REFUSED_NO_QUERIES)
                enqueued = self._queue.enqueue(
                    DISCOVER_PLACES_JOB,
                    scope=campaign.id,
                    discriminator=location.hash,
                    payload={
                        VERSION_FIELD: PAYLOAD_VERSION,
                        CAMPAIGN_ID_FIELD: campaign.id,
                        LOCATION_FIELD: location.hash,
                        COUNTRY_CODE_FIELD: location.country_code.lower(),
                    },
                    lane=Lane.BULK,
                    concurrency_key=PLACES_CONCURRENCY_KEY,
                )
        except PlacesRunRefused as refused:
            log.info(PLACES_RUN_REFUSED, extra={"reason": refused.reason})
            raise
        log.info(
            PLACES_RUN_REQUESTED,
            extra={
                "campaign_id": campaign.id,
                "location_hash": location.hash,
                "job_id": enqueued.job_id,
                "job_created": enqueued.created,
            },
        )
        return enqueued

    # The run ----------------------------------------------------------------------------------

    @use_case
    def run(self, campaign_id: str, location_hash: str, country_code: str) -> bool:
        """Run one Places discovery; ``False`` when the budget refused a page. See the module
        docstring."""
        started = self._start(campaign_id, location_hash, country_code)
        if started is RunStatus.DEFERRED:
            return False
        if not isinstance(started, _Run):
            return True
        try:
            status = self._pages(started)
        except Exception:
            self._end(started.run_id, RunStatus.FAILED)
            raise
        self._end(started.run_id, status)
        return status is not RunStatus.DEFERRED

    def _start(
        self, campaign_id: str, location_hash: str, country_code: str
    ) -> _Run | RunStatus | None:
        """Step 1: the started run, ``RunStatus.DEFERRED`` when the budget refuses the run
        (no row written), or ``None`` when there is nothing to fetch."""
        now = self._clock.now()
        code = country_code.upper() if isinstance(country_code, str) else ""
        with current().transaction():
            # every discover_places job shares one concurrency key, so a running row is stale
            stale = runs_repo.fail_running(SOURCE_PLACES, now)
            if stale:
                log.warning(PLACES_RUNS_RECOVERED, extra={"count": stale})
            campaign = setup_queries.campaign(campaign_id)
            location = None if campaign is None else _location(location_hash, code)
            segment = (
                None if campaign is None else setup_queries.segment_by_id(campaign.segment_id)
            )
            if campaign is None or location is None or segment is None:
                log.info(PLACES_RUN_SKIPPED, extra={"campaign_id": campaign_id})
                return None
            queries = segment.definition.discovery_queries.places
            active = setup_queries.campaign_active(campaign.id)
            refusal = self._searches.refusal() if active else None
            if refusal is not None:  # Story 8.1: refused before any row is written
                log.info(
                    PLACES_RUN_DEFERRED, extra={"campaign_id": campaign.id, "reason": refusal}
                )
                return RunStatus.DEFERRED
            status = RunStatus.RUNNING if active else RunStatus.STOPPED
            run_id = runs_repo.insert(
                source=SOURCE_PLACES,
                campaign_id=campaign.id,
                segment_id=segment.id,
                country_code=location.country_code,
                location=location.location,
                query_hash=query_hash(queries),
                status=status,
                started_at=now,
                finished_at=None if active else now,
            )
            max_pages = int(setup_queries.get(Key.PLACES_MAX_PAGES_PER_RUN))
        if not active:
            self._log_end(run_id, status)
            return None
        return _Run(run_id, campaign.id, segment.id, location, tuple(queries), max_pages)

    def _pages(self, run: _Run) -> RunStatus:
        """Every page of the run, each candidate ingested: ``finished``, ``deferred`` when the
        budget refused a page, or ``stopped`` when the Campaign could no longer run."""
        fetched = 0
        for search in run.queries:
            token: str | None = None
            while fetched < run.max_pages:
                if not self._may_continue(run):
                    return RunStatus.STOPPED
                page = self._searches.page(search, run.location.location, token)
                if page is None:
                    return RunStatus.DEFERRED
                fetched += 1
                for candidate in page.candidates:
                    self.ingest_candidate(run.run_id, candidate, run.segment_id, run.location)
                token = page.next_page_token
                if token is None:
                    break
            if fetched >= run.max_pages:
                break
        return RunStatus.FINISHED

    def _may_continue(self, run: _Run) -> bool:
        """Whether the Campaign may still run and its Market is still the run's country."""
        with current().transaction():
            campaign = setup_queries.campaign(run.campaign_id)
            return (
                campaign is not None
                and setup_queries.campaign_active(campaign.id)
                and _market_country(campaign.market_id) == run.location.country_code
            )

    def _end(self, run_id: str, status: RunStatus) -> None:
        with current().transaction():
            runs_repo.finish(run_id, status, self._clock.now())
        self._log_end(run_id, status)

    @staticmethod
    def _log_end(run_id: str, status: RunStatus) -> None:
        log.info(PLACES_RUN_ENDED, extra={"run_id": run_id, "status": str(status)})

    # One candidate ----------------------------------------------------------------------------

    def ingest_candidate(
        self,
        run_id: str,
        candidate: PlaceCandidate,
        segment_id: str,
        location: DiscoveryLocation,
    ) -> Outcome | None:
        """Turn one candidate into a Lead, or count why not; the outcome (``None``: not a valid
        place ID). Runs inside the run's use case, with no transaction open."""
        lead_id: str | None = None
        outcome, place_id = self._prechecked(run_id, candidate, location.country_code)
        if outcome is False and place_id is not None:
            site = own_site(self._crawler, candidate.website_uri)
            with current().transaction():
                if site is None:
                    outcome = Outcome.NO_WEBSITE
                else:
                    identity = LeadIdentity(
                        name=site.name,
                        segment_id=segment_id,
                        web_domain=site.web_domain,
                        place_id=place_id,
                        country_code=location.country_code,
                    )
                    upserted = self._discovery.upsert_lead(identity, LeadSource.PLACES)
                    outcome, lead_id = self._upserted(candidate, upserted)
                runs_repo.count(run_id, outcome)
        log.info(
            PLACE_CANDIDATE_INGESTED,
            extra={
                "run_id": run_id,
                "outcome": None if outcome is None else str(outcome),
                "lead_id": lead_id,
            },
        )
        return outcome

    def _prechecked(
        self, run_id: str, candidate: PlaceCandidate, country_code: str
    ) -> tuple[Outcome | None | bool, str | None]:
        """The checks before any fetch, counted when they decide: the outcome (``None`` for an
        invalid place ID, ``False`` when the website must decide) and the normalised place ID."""
        with current().transaction():
            try:
                place_id = normalize_place_id(candidate.place_id)
            except IdentityError:
                runs_repo.count(run_id, None)
                return None, None
            if not _enabled_market(country_code):
                runs_repo.count(run_id, Outcome.MARKET_BLOCKED)
                return Outcome.MARKET_BLOCKED, place_id
            if leads_repo.by_place_id(place_id) is not None:
                runs_repo.count(run_id, Outcome.MERGED)
                return Outcome.MERGED, place_id
        return False, place_id

    def _upserted(
        self, candidate: PlaceCandidate, upserted: UpsertedLead
    ) -> tuple[Outcome, str | None]:
        """What ``upsert_lead``'s answer comes to; a new Lead gets the candidate's coordinates,
        in the caller's transaction."""
        if upserted.blocked is not None or upserted.lead_id is None:
            return Outcome.SUPPRESSED, None
        if not upserted.created:
            return Outcome.MERGED, upserted.lead_id
        if candidate.lat is not None and candidate.lng is not None:
            self._discovery.record_place_coords(upserted.lead_id, candidate.lat, candidate.lng)
        return Outcome.CREATED, upserted.lead_id


def _site_of(fetched: FetchedPage) -> OwnSite | None:
    """The website a fetched homepage shows, or ``None`` when it shows none of its own."""
    if not fetched.ok or fetched.media_type not in HTML_MEDIA_TYPES:
        return None
    domain = website_domain(fetched.final_url)
    if domain is None:
        return None
    return OwnSite(domain, site_name(fetched.html) or domain)
