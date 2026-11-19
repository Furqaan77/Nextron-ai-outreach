"""The weekday discovery top-up (Story 8.10; FR-5, FR-6, FR-9, AD-3, AD-9, AD-10), owned by the
``discovery`` service. ``nextron.app.discovery.api``'s docstring points here.

**``DiscoveryTopUp(clock, queue, place_searches, ready_pool=None)``.** ``ready_pool`` is a
``ReadyPool`` (``ready_leads(campaign_id) -> int``: the Campaign's Leads ready to enroll;
``capacity_share(campaign_id, local_date) -> int``: its share of that day's new-Sequence
capacity), read inside the caller's transaction. Until the scoring and sequencing services
provide one (Stories 2.15, 4.8, 4.10 and 8.9), ``nextron.main`` passes none, and with none a
pool is never sufficient.

``chain(local_date, selection_status) -> tuple[Enqueued, ...]`` is the stage the weekday
``daily_selection`` (Story 8.9) calls inside its own open transaction, so the jobs commit or
roll back with the selection; called with no transaction open it raises ``RuntimeError``. It
enqueues nothing (and logs ``discovery_topup_skipped`` with the status) unless
``selection_status`` is ``ran`` or ``away`` and ``local_date`` is Monday to Friday. Otherwise,
for each Campaign that may run (``queries.setup.campaign_active``), in the stored order, it first
works out the jobs the Campaign would get:

- **Places.** When its Segment has Places queries, its Market is enabled and at least one
  ``places_discovery_locations`` item has its Market's country: ``discover_places`` keyed
  ``discover_places:<campaign ULID>:<YYYY-MM-DD>``.
- **Companies House.** When its Market is an enabled ``GB`` one and its Segment has registry
  searches (``registry_discovery.registry_searches``): ``discover_registry`` keyed
  ``discover_registry:<campaign ULID>:<YYYY-MM-DD>``.

A Campaign that would get neither is passed over (no row). Otherwise the **pool check**: with a
``ReadyPool``, ``ready = ready_leads(campaign)``, ``share = capacity_share(campaign,
local_date)``; when ``discovery_topup.pool_sufficient(ready, discovery_pool_days, share)``
nothing is enqueued for it and one ``discovery_runs`` row is written (unless it already has one
for that date): source ``places``, the Campaign, its Segment, its Market's country, no location,
the ``query_hash`` of its Segment's Places queries, status ``pool_sufficient``, ``ready_pool`` =
``ready``, ``scheduled_for`` = ``local_date``, started and finished now, every count 0. Logged
``discovery_pool_sufficient`` (``campaign_id``, ``ready``). When it isn't, its jobs are
enqueued.

Each payload is ``{v: 1, campaign_id, scheduled_for: "<YYYY-MM-DD>"}``, on the bulk lane, with
the source's existing concurrency key. A live job with the same key is returned instead
(``created=False``), so a second call while the day's job is live adds nothing; a call after
that job ended does queue it again, but the scheduled run then skips (``already_ran``) when a
run for the same Campaign, source and date already ended ``finished``, ``paced`` or
``target_reached``, so no second pair is searched. A Campaign whose Market isn't
stored gets nothing. Logs ``discovery_topup_chained`` (``date``, ``jobs_created``: the jobs
created). The jobs run ``PlaceDiscovery.run_scheduled`` and ``RegistryDiscovery.run_scheduled``.

``today() -> DiscoveryToday`` (the Discovery page and the health panel), read in the current
unit of work (a read-only one included): the Operator's local date (``sender_timezone``), the
Leads created today (``daily.created_today``) and the target (``discovery_daily_lead_target``),
today's Places requests and allowance (``PlaceSearches.day_usage``), and today's
``pool_sufficient`` rows as ``PoolLine(campaign_id, ready)``.

Logs hold ids, dates, statuses and counts only: never a query or location text.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Final, Protocol

from nextron.app.discovery.daily import created_today, lead_target, sender_timezone
from nextron.app.discovery.place_ingest import configured_locations
from nextron.app.discovery.places import Pace, PlaceSearches
from nextron.app.jobqueue.api import Enqueued, JobQueue
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.uow import in_transaction
from nextron.domain.discovery_topup import (
    SCHEDULED_FOR_FIELD,
    TOP_UP_STATUSES,
    is_weekday,
    local_date_of,
    pool_sufficient,
)
from nextron.domain.job import VERSION_FIELD, Lane
from nextron.domain.markets import GB
from nextron.domain.place_discovery import (
    CAMPAIGN_ID_FIELD,
    DISCOVER_PLACES_JOB,
    PAYLOAD_VERSION,
    PLACES_CONCURRENCY_KEY,
    SOURCE_PLACES,
    RunStatus,
    query_hash,
)
from nextron.domain.registry_discovery import (
    DISCOVER_REGISTRY_JOB,
    REGISTRY_CONCURRENCY_KEY,
    registry_searches,
)
from nextron.domain.settings_schema import Key
from nextron.ports.clock import Clock

log = logging.getLogger(__name__)

DISCOVERY_TOPUP_CHAINED: Final = "discovery_topup_chained"
DISCOVERY_TOPUP_SKIPPED: Final = "discovery_topup_skipped"
DISCOVERY_POOL_SUFFICIENT: Final = "discovery_pool_sufficient"


class ReadyPool(Protocol):
    """How many Leads a Campaign has ready to enroll, and its share of a day's capacity."""

    def ready_leads(self, campaign_id: str) -> int: ...

    def capacity_share(self, campaign_id: str, local_date: date) -> int: ...


@dataclass(frozen=True, slots=True)
class PoolLine:
    """A Campaign whose ready pool was sufficient today, and how many Leads were ready."""

    campaign_id: str
    ready: int


@dataclass(frozen=True, slots=True)
class DiscoveryToday:
    """Today's scheduled-discovery figures; see the module docstring."""

    local_date: date
    created: int
    target: int
    places_requests: int
    places_allowance: int
    pools: tuple[PoolLine, ...]


class DiscoveryTopUp:
    """The weekday discovery top-up stage; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        queue: JobQueue,
        place_searches: PlaceSearches,
        ready_pool: ReadyPool | None = None,
    ) -> None:
        self._clock = clock
        self._queue = queue
        self._searches = place_searches
        self._ready_pool = ready_pool

    def chain(self, local_date: date, selection_status: str) -> tuple[Enqueued, ...]:
        """Enqueue the day's scheduled discovery in the caller's open transaction; see the
        module docstring."""
        if not in_transaction():
            raise RuntimeError("DiscoveryTopUp.chain runs inside the caller's open transaction")
        if selection_status not in TOP_UP_STATUSES or not is_weekday(local_date):
            log.info(
                DISCOVERY_TOPUP_SKIPPED,
                extra={"date": local_date.isoformat(), "status": str(selection_status)},
            )
            return ()
        pool_days = int(setup_queries.get(Key.DISCOVERY_POOL_DAYS))
        countries = {location.country_code for location in configured_locations()}
        markets = {market.id: market.definition for market in setup_queries.markets()}
        enqueued: list[Enqueued] = []
        for campaign in setup_queries.campaigns():
            market = markets.get(campaign.market_id)
            if market is None or not setup_queries.campaign_active(campaign.id):
                continue
            segment = setup_queries.segment_by_id(campaign.segment_id)
            if segment is None:
                continue
            queries = segment.definition.discovery_queries
            jobs = []
            if queries.places and market.enabled and market.country_code in countries:
                jobs.append((DISCOVER_PLACES_JOB, PLACES_CONCURRENCY_KEY))
            if market.country_code == GB and market.enabled and registry_searches(
                queries.registry
            ):
                jobs.append((DISCOVER_REGISTRY_JOB, REGISTRY_CONCURRENCY_KEY))
            if not jobs:
                continue
            ready = self._sufficient_pool(campaign.id, local_date, pool_days)
            if ready is not None:
                self._pool_row(
                    campaign.id, segment.id, market.country_code, queries.places, local_date, ready
                )
                continue
            for job_type, concurrency_key in jobs:
                enqueued.append(self._enqueue(job_type, campaign.id, local_date, concurrency_key))
        log.info(
            DISCOVERY_TOPUP_CHAINED,
            extra={
                "date": local_date.isoformat(),
                "jobs_created": sum(1 for found in enqueued if found.created),
            },
        )
        return tuple(enqueued)

    def _sufficient_pool(self, campaign_id: str, local_date: date, pool_days: int) -> int | None:
        """The Campaign's ready Leads when its pool is sufficient, else ``None``."""
        if self._ready_pool is None:
            return None
        ready = self._ready_pool.ready_leads(campaign_id)
        share = self._ready_pool.capacity_share(campaign_id, local_date)
        return ready if pool_sufficient(ready, pool_days, share) else None

    def _pool_row(
        self,
        campaign_id: str,
        segment_id: str,
        country_code: str,
        places_queries: tuple[str, ...],
        local_date: date,
        ready: int,
    ) -> None:
        """The ``pool_sufficient`` row, once per Campaign and date."""
        log.info(DISCOVERY_POOL_SUFFICIENT, extra={"campaign_id": campaign_id, "ready": ready})
        if runs_repo.has_pool_row(campaign_id, local_date):
            return
        now = self._clock.now()
        runs_repo.insert(
            source=SOURCE_PLACES,
            campaign_id=campaign_id,
            segment_id=segment_id,
            country_code=country_code,
            location=None,
            query_hash=query_hash(places_queries),
            status=RunStatus.POOL_SUFFICIENT,
            started_at=now,
            finished_at=now,
            scheduled_for=local_date,
            ready_pool=ready,
        )

    def _enqueue(
        self, job_type: str, campaign_id: str, local_date: date, concurrency_key: str
    ) -> Enqueued:
        return self._queue.enqueue(
            job_type,
            scope=campaign_id,
            discriminator=local_date.isoformat(),
            payload={
                VERSION_FIELD: PAYLOAD_VERSION,
                CAMPAIGN_ID_FIELD: campaign_id,
                SCHEDULED_FOR_FIELD: local_date.isoformat(),
            },
            lane=Lane.BULK,
            concurrency_key=concurrency_key,
        )

    def today(self) -> DiscoveryToday:
        """Today's figures, read in the current unit of work; see the module docstring."""
        now = self._clock.now()
        timezone = sender_timezone()
        local = local_date_of(now, timezone)
        day = self._searches.day_usage(Pace(local, timezone))
        pools = tuple(
            PoolLine(row.campaign_id, row.ready_pool or 0)
            for row in discovery_queries.pool_rows(local)
        )
        return DiscoveryToday(
            local_date=local,
            created=created_today(now),
            target=lead_target(),
            places_requests=day.requests,
            places_allowance=day.allowance,
            pools=pools,
        )
