"""Story 8.10's weekday discovery top-up (FR-5, FR-6, FR-9, AD-9, AD-10, AD-16) on a temporary
database at head seeded with the shipped files, ``FrozenClock``, ``ScriptedPlaces``,
``ScriptedRegistry`` and a recording ``ScriptedCrawler``: every row of the spec's matrix (a
weekday after selection, a weekend or no capacity, a sufficient pool, the allowance at 0 and
shared, the rotation, every pair recent, the day's target, a registry lookup over the
allowance), the stage's transaction guard and rollback, a second call the same day, the job
handlers' payloads, manual runs left as they were, today's figures on the Discovery page and the
health panel, and a scan proving no query or location text reaches a payload, a log line or an
``events`` row. No request reaches Google, Companies House or a website."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html import unescape
from typing import Any, Final

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.discovery.place_ingest import PlaceDiscovery
from nextron.app.discovery.places import PlaceSearches
from nextron.app.discovery.registry_ingest import RegistryDiscovery, single_search_hash
from nextron.app.discovery.topup import DiscoveryTopUp, DiscoveryToday, PoolLine
from nextron.app.jobqueue.api import JobQueue
from nextron.app.ops.api import Seeds
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.setup.api import Campaigns, Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.budget import AiCall, ApiCall
from nextron.domain.campaign import validate_campaigns
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.job import DEFERRED, InvalidJob, Job, Lane
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.place_discovery import query_hash
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.domain.settings_schema import Key
from nextron.ports.places import PlaceCandidate, PlaceSearchPage
from nextron.ports.registry import RegistryCompany
from tests.dashboard import Dashboard
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.places import ScriptedPlaces
from tests.fakes.registry import SEARCH_REGISTRY, ScriptedRegistry
from tests.fakes.usage_meter import InMemoryUsageMeter, Recorded

T0: Final = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)  # a Wednesday; 9 weekdays left
TODAY: Final = date(2026, 11, 18)
COLUMBUS: Final = "Columbus, OH"
LEEDS: Final = "Leeds"
LOCATIONS: Final = (f"{COLUMBUS} | US", f"{LEEDS} | GB")
LAW: Final = "law firm"
SOLICITORS: Final = "solicitors"
SIC: Final = ("69102",)
KEYWORDS: Final = ("solicitors", "law", "legal")
POSTCODE: Final = "LS1 4AP"
ALLOW_ALL: Final = "User-agent: *\nDisallow:\n"
LOGGERS: Final = ("nextron.app.discovery", "nextron.app.jobqueue", "nextron.jobs")


def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


def no_jobs() -> Any:
    raise AssertionError("no other job runs in these tests")


def places_call(count: int = 1) -> ApiCall:
    return ApiCall("places", "text_search", ok=True, sku="enterprise", count=count)


@dataclass
class FakePool:
    """A ``ReadyPool``: the same ready count and share for every Campaign."""

    ready: int
    share: int
    asked: list[tuple[str, date]] = field(default_factory=list)

    def ready_leads(self, campaign_id: str) -> int:
        return self.ready

    def capacity_share(self, campaign_id: str, local_date: date) -> int:
        self.asked.append((campaign_id, local_date))
        return self.share


@dataclass
class World:
    """The top-up, Places and Companies House discovery on real units of work."""

    engine: Engine
    units: UnitOfWorkFactory
    clock: FrozenClock
    meter: InMemoryUsageMeter
    places: ScriptedPlaces
    registry: ScriptedRegistry
    crawler: ScriptedCrawler
    queue: JobQueue
    place_searches: PlaceSearches
    place_discovery: PlaceDiscovery
    registry_discovery: RegistryDiscovery
    job_types: Any
    campaigns: dict[str, str] = field(default_factory=dict)

    @property
    def us(self) -> str:
        return self.campaigns["law_private_ai_us"]

    @property
    def uk(self) -> str:
        return self.campaigns["law_private_ai_uk"]

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def set(self, key: str, value: object) -> None:
        with self.units.open():
            Setup(self.clock).set(key, value)

    def topup(self, pool: FakePool | None = None) -> DiscoveryTopUp:
        return DiscoveryTopUp(self.clock, self.queue, self.place_searches, pool)

    def chain(self, day: date = TODAY, status: str = "ran", pool: FakePool | None = None) -> Any:
        topup = self.topup(pool)
        return self.transact(lambda: topup.chain(day, status))

    def segment(self) -> str:
        [(segment,)] = self.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
        return str(segment)

    def jobs(self, job_type: str | None = None) -> list[tuple[Any, ...]]:
        where = "" if job_type is None else " WHERE type = ?"
        return self.sql(
            "SELECT type, idempotency_key, payload, lane, concurrency_key FROM jobs"
            f"{where} ORDER BY rowid",
            () if job_type is None else (job_type,),
        )

    def runs(self) -> list[dict[str, Any]]:
        names = (
            "source campaign_id segment_id country_code location query_hash status started_at "
            "finished_at seen created scheduled_for ready_pool"
        ).split()
        rows = self.sql(f"SELECT {', '.join(names)} FROM discovery_runs ORDER BY rowid")
        return [dict(zip(names, row, strict=True)) for row in rows]

    def past_run(
        self,
        *,
        source: str = "places",
        location: str | None = COLUMBUS,
        hashed: str,
        days_ago: float,
        status: str = "finished",
        created: int = 0,
        country: str = "US",
    ) -> None:
        """A stored run that started ``days_ago`` days before now."""
        at = (self.clock.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        number = len(self.sql("SELECT id FROM discovery_runs"))
        campaign = self.us if country == "US" else self.uk
        self.sql(
            "INSERT INTO discovery_runs (id, source, campaign_id, segment_id, country_code, "
            "location, query_hash, status, started_at, finished_at, seen, created, merged, "
            "suppressed, no_website, market_blocked, inactive) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, 0, 0)",
            (
                f"01JD3QZ8X5V9K2M7N4P6R8T{number:03d}",
                source,
                campaign,
                self.segment(),
                country,
                location,
                hashed,
                status,
                at,
                at,
                created,
            ),
        )

    def record_places(self, count: int, at: datetime | None = None) -> None:
        self.meter.records.append(Recorded(places_call(count), at or self.clock.now()))

    def site(self, domain: str, title: str = "Firm") -> str:
        origin = f"https://www.{domain}"
        self.crawler.robots[f"{origin}/robots.txt"] = robots_txt(f"{origin}/robots.txt", ALLOW_ALL)
        self.crawler.pages[f"{origin}/"] = html_page(f"{origin}/", f"<title>{title}</title>")
        return f"{origin}/"

    def metered_pages(self, query: str, location: str, *pages: PlaceSearchPage) -> None:
        """Script ``query``'s pages, chained by tokens, each recording one Places request."""
        tokens: list[str | None] = [None, *(f"t{number}" for number in range(2, len(pages) + 1))]

        def answer(found: PlaceSearchPage) -> Callable[[], PlaceSearchPage]:
            def metered() -> PlaceSearchPage:
                self.meter.record(places_call())
                return found

            return metered

        for token, found in zip(tokens, pages, strict=True):
            self.places.pages[(query, location, token)] = answer(found)

    def scheduled_places(self, campaign: str | None = None, day: date = TODAY) -> bool:
        with self.units.open():
            return self.place_discovery.run_scheduled(campaign or self.us, day)

    def scheduled_registry(self, campaign: str | None = None, day: date = TODAY) -> bool:
        with self.units.open():
            return self.registry_discovery.run_scheduled(campaign or self.uk, day)

    def handler(self, job_type: str) -> Callable[[Job], object]:
        return self.job_types.get(job_type).handler


def build_world(engine: Engine) -> World:
    """The shipped seed stored, both law Campaigns active, two locations set."""
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(engine, main.build_subscribers(clock=clock))
    with units.open():
        Seeds(clock).sync_seed(
            validate_segments(read(main.SEGMENTS_FILE_NAME)),
            validate_markets(read(main.MARKETS_FILE_NAME)),
            validate_sequences(read(main.SEQUENCES_FILE_NAME)),
            validate_company_facts(read(main.COMPANY_FACTS_FILE_NAME)),
            validate_offers(read(main.OFFERS_FILE_NAME)),
            validate_campaigns(read(main.CAMPAIGNS_FILE_NAME)),
        )
    meter = InMemoryUsageMeter(clock)
    places = ScriptedPlaces()
    registry = ScriptedRegistry()
    crawler = ScriptedCrawler()
    holder: dict[str, Any] = {}
    job_types = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=no_jobs,
        primary=no_jobs,
        test_runs=no_jobs,
        job_boards=no_jobs,
        legal_forms=no_jobs,
        scores=no_jobs,
        place_discovery=lambda: holder["places"],
        registry_discovery=lambda: holder["registry"],
    )
    queue = JobQueue(clock, job_types)
    discovery = Discovery(clock, Pipeline(clock), queue)
    place_searches = PlaceSearches(clock, meter, places)
    place_discovery = PlaceDiscovery(clock, place_searches, crawler, discovery, queue)
    registry_discovery = RegistryDiscovery(
        clock, meter, registry, place_searches, crawler, discovery, Research(clock), queue
    )
    holder.update(places=place_discovery, registry=registry_discovery)
    world = World(
        engine,
        units,
        clock,
        meter,
        places,
        registry,
        crawler,
        queue,
        place_searches,
        place_discovery,
        registry_discovery,
        job_types,
    )
    stored = world.sql("SELECT id, key FROM campaigns")
    world.campaigns = {str(key): str(id_) for id_, key in stored}
    with units.open():
        Campaigns(clock).activate(world.us)
    with units.open():
        Campaigns(clock).activate(world.uk, "LIA-2026-01", "2026-11-16")
    world.set(Key.PLACES_DISCOVERY_LOCATIONS, LOCATIONS)
    return world


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def page(*candidates: PlaceCandidate, token: str | None = None) -> PlaceSearchPage:
    return PlaceSearchPage(tuple(candidates), token)


def candidate(number: int, domain: str | None = None) -> PlaceCandidate:
    website = None if domain is None else f"https://www.{domain}/"
    return PlaceCandidate(f"ChIJ-{number}", website, f"Maps Name {number}", 39.96, -82.99)


def company(number: str, name: str = "ACME SOLICITORS LIMITED") -> RegistryCompany:
    return RegistryCompany(number, name, "ltd", "active", POSTCODE, "full", SIC)


# The stage ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["ran", "away"])
def test_a_weekday_after_selection_queues_dated_jobs_for_each_active_campaign(
    world: World, status: str
) -> None:
    enqueued = world.chain(status=status)

    assert [found.created for found in enqueued] == [True, True, True]
    keys = {(row[0], row[1]) for row in world.jobs()}
    assert keys == {
        ("discover_places", f"discover_places:{world.us}:2026-11-18"),
        ("discover_places", f"discover_places:{world.uk}:2026-11-18"),
        ("discover_registry", f"discover_registry:{world.uk}:2026-11-18"),  # GB only
    }
    for job_type, key, payload, lane, concurrency in world.jobs():
        campaign = key.split(":")[1]
        assert json.loads(payload) == {
            "v": 1,
            "campaign_id": campaign,
            "scheduled_for": "2026-11-18",
        }
        assert lane == str(Lane.BULK)
        assert concurrency == (
            "domain:places.googleapis.com"
            if job_type == "discover_places"
            else "domain:api.company-information.service.gov.uk"
        )
    assert world.runs() == []  # no provider: a pool is never sufficient


def test_a_second_call_the_same_day_adds_nothing(world: World) -> None:
    first = world.chain()
    second = world.chain()

    assert [found.job_id for found in second] == [found.job_id for found in first]
    assert [found.created for found in second] == [False, False, False]
    assert len(world.jobs()) == 3


@pytest.mark.parametrize(
    ("day", "status"),
    [
        (date(2026, 11, 21), "ran"),  # a Saturday
        (date(2026, 11, 22), "away"),  # a Sunday
        (TODAY, "weekend"),
        (TODAY, "no_capacity"),
    ],
)
def test_a_weekend_or_a_selection_that_did_not_run_queues_nothing(
    world: World, day: date, status: str
) -> None:
    assert world.chain(day, status) == ()
    assert world.jobs() == []
    assert world.runs() == []


def test_the_stage_runs_only_inside_the_caller_s_transaction(world: World) -> None:
    topup = world.topup()

    @use_case
    def outside() -> None:
        topup.chain(TODAY, "ran")

    with world.units.open(), pytest.raises(RuntimeError, match="transaction"):
        outside()
    assert world.jobs() == []


def test_the_jobs_commit_with_the_caller_and_vanish_when_it_rolls_back(world: World) -> None:
    topup = world.topup()

    def chained_then_failed() -> None:
        topup.chain(TODAY, "ran")
        raise LookupError("the selection failed after chaining")

    with pytest.raises(LookupError):
        world.transact(chained_then_failed)
    assert world.jobs() == []

    world.transact(lambda: topup.chain(TODAY, "ran"))
    assert len(world.jobs()) == 3


def test_a_paused_campaign_or_one_without_queries_or_locations_gets_nothing(
    world: World,
) -> None:
    with world.units.open():
        Campaigns(world.clock).pause(world.uk)
    world.set(Key.PLACES_DISCOVERY_LOCATIONS, (f"{LEEDS} | GB",))  # no US location

    assert world.chain() == ()
    assert world.jobs() == []


def test_a_sufficient_pool_writes_a_row_and_queues_nothing(world: World) -> None:
    pool = FakePool(ready=30, share=6)  # 30 >= 5 days x 6

    assert world.chain(pool=pool) == ()
    assert world.chain(pool=pool) == ()  # a second call writes no second row

    assert world.jobs() == []
    rows = world.runs()
    assert [(row["campaign_id"], row["country_code"]) for row in rows] == [
        (world.uk, "GB"),  # Campaigns by key
        (world.us, "US"),
    ]
    for row in rows:
        assert row | {"campaign_id": None, "country_code": None} == {
            "source": "places",
            "campaign_id": None,
            "segment_id": world.segment(),
            "country_code": None,
            "location": None,
            "query_hash": query_hash((LAW, SOLICITORS)),
            "status": "pool_sufficient",
            "started_at": "2026-11-18T09:00:00.000000Z",
            "finished_at": "2026-11-18T09:00:00.000000Z",
            "seen": 0,
            "created": 0,
            "scheduled_for": "2026-11-18",
            "ready_pool": 30,
        }
    assert (world.us, TODAY) in pool.asked


def test_a_pool_short_of_the_days_queues_discovery(world: World) -> None:
    world.set(Key.DISCOVERY_POOL_DAYS, 6)  # 30 < 6 x 6

    assert len(world.chain(pool=FakePool(ready=30, share=6))) == 3
    assert world.runs() == []


def test_a_sufficient_pool_writes_a_row_only_for_a_campaign_that_would_get_discovery(
    world: World,
) -> None:
    world.set(Key.PLACES_DISCOVERY_LOCATIONS, (f"{LEEDS} | GB",))  # the US one gets no job
    pool = FakePool(ready=30, share=6)

    assert world.chain(pool=pool) == ()

    assert world.jobs() == []
    rows = world.runs()
    assert [(row["campaign_id"], row["status"]) for row in rows] == [
        (world.uk, "pool_sufficient")  # one row for both of the UK Campaign's jobs
    ]
    assert world.us not in {campaign for campaign, _ in pool.asked}  # never asked


# Scheduled Places runs ---------------------------------------------------------------------------


def test_a_scheduled_places_run_searches_one_never_run_pair_and_records_it(world: World) -> None:
    world.metered_pages(LAW, COLUMBUS, page(candidate(1, "acme-law.com")))
    world.site("acme-law.com", "Acme Law")

    assert world.scheduled_places() is True

    assert world.places.calls == [(LAW, COLUMBUS, None)]  # one query, one location
    [run] = world.runs()
    assert run["status"] == "finished"
    assert run["location"] == COLUMBUS
    assert run["query_hash"] == query_hash((LAW,))
    assert run["scheduled_for"] == "2026-11-18"
    assert (run["seen"], run["created"]) == (1, 1)


def test_rotation_takes_the_oldest_due_pair_and_skips_recent_ones(world: World) -> None:
    world.past_run(hashed=query_hash((LAW,)), days_ago=10)  # recent: skipped
    world.past_run(hashed=query_hash((SOLICITORS,)), days_ago=40)
    world.past_run(hashed=query_hash((LAW, SOLICITORS)), days_ago=100)  # a manual run: no pair
    world.metered_pages(SOLICITORS, COLUMBUS, page())

    assert world.scheduled_places() is True

    assert world.places.calls == [(SOLICITORS, COLUMBUS, None)]
    assert world.runs()[-1]["query_hash"] == query_hash((SOLICITORS,))


def test_a_pair_cut_short_counts_as_never_run(world: World) -> None:
    world.past_run(hashed=query_hash((LAW,)), days_ago=1, status="paced")
    world.past_run(hashed=query_hash((SOLICITORS,)), days_ago=40)
    world.metered_pages(LAW, COLUMBUS, page())

    assert world.scheduled_places() is True
    assert world.places.calls == [(LAW, COLUMBUS, None)]


def test_with_every_pair_recent_nothing_is_fetched_and_no_row_written(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world.past_run(hashed=query_hash((LAW,)), days_ago=3)
    world.past_run(hashed=query_hash((SOLICITORS,)), days_ago=29)

    assert world.scheduled_places() is True

    assert world.places.calls == []
    assert len(world.runs()) == 2
    [record] = [found for found in caplog.records if found.msg == "discovery_nothing_due"]
    assert (record.source, record.campaign_id) == ("places", world.us)


def test_an_allowance_of_0_ends_the_run_paced_with_no_request(world: World) -> None:
    world.clock.set(datetime(2026, 11, 3, 9, 0, tzinfo=UTC))  # 20 weekdays left
    world.record_places(990, datetime(2026, 11, 2, 12, 0, tzinfo=UTC))  # 10 of 1,000 left
    world.metered_pages(LAW, COLUMBUS, page(candidate(1, "acme-law.com")))

    assert world.scheduled_places(day=date(2026, 11, 3)) is True  # paced completes the job

    assert world.places.calls == []
    [run] = world.runs()
    assert run["status"] == "paced"


def test_the_allowance_is_shared_with_today_s_other_requests(world: World) -> None:
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 30)  # 30 // 9 weekdays = 3 today
    world.record_places(2)  # two requests already today (a manual run, a lookup)
    world.metered_pages(LAW, COLUMBUS, page(token="t2"), page(token="t3"), page())

    assert world.scheduled_places() is True

    assert world.places.calls == [(LAW, COLUMBUS, None)]  # one page, then paced
    [run] = world.runs()
    assert run["status"] == "paced"


def test_the_day_s_target_stops_the_run_after_the_one_lead_it_allows(world: World) -> None:
    world.past_run(hashed="0" * 16, days_ago=0.1, created=59)  # 59 created today
    world.metered_pages(
        LAW,
        COLUMBUS,
        page(candidate(1, "one-law.com"), candidate(2, "two-law.com"), candidate(3, "three.com")),
    )
    for domain in ("one-law.com", "two-law.com", "three.com"):
        world.site(domain)

    assert world.scheduled_places() is True

    run = world.runs()[-1]
    assert (run["status"], run["seen"], run["created"]) == ("target_reached", 1, 1)
    assert [row[0] for row in world.sql("SELECT web_domain FROM leads")] == ["one-law.com"]


def test_yesterday_s_leads_do_not_count_toward_today_s_target(world: World) -> None:
    world.past_run(hashed="0" * 16, days_ago=1, created=500)
    world.set(Key.DISCOVERY_DAILY_LEAD_TARGET, 1)
    world.metered_pages(LAW, COLUMBUS, page(candidate(1, "one-law.com")))
    world.site("one-law.com")

    assert world.scheduled_places() is True
    assert world.runs()[-1]["status"] == "finished"


def test_a_target_of_0_fetches_nothing(world: World) -> None:
    world.set(Key.DISCOVERY_DAILY_LEAD_TARGET, 0)

    assert world.scheduled_places() is True

    assert world.places.calls == []
    assert world.runs()[-1]["status"] == "target_reached"


def spend_ai_budget(world: World) -> None:
    world.meter.record(
        AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
    )


@pytest.mark.parametrize("source", ["places", "companies_house"])
def test_a_budget_refusal_defers_with_no_row(world: World, source: str) -> None:
    if source == "places":
        world.set(Key.QUOTA_PLACES_ENTERPRISE, 5)
        world.record_places(5)
        assert world.scheduled_places() is False
    else:
        registry_world(world, company("01234567"))
        spend_ai_budget(world)  # the month's whole AI budget
        assert world.scheduled_registry() is False

    assert world.runs() == []
    assert world.places.calls == []
    assert world.registry.calls == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        *(
            (source, reason)
            for source in ("places", "companies_house")
            for reason in ("not_today", "campaign_not_active", "unknown")
        ),
        ("companies_house", "not_uk"),
    ],
)
def test_a_job_for_another_day_or_a_paused_campaign_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture, source: str, reason: str
) -> None:
    caplog.set_level(logging.INFO)
    registry_world(world, company("01234567"))
    campaign = world.us if source == "places" else world.uk
    day = TODAY
    if reason == "not_today":
        day = TODAY - timedelta(days=1)
    elif reason == "campaign_not_active":
        with world.units.open():
            Campaigns(world.clock).pause(campaign)
    elif reason == "not_uk":
        campaign = world.us
    else:
        campaign = "01JD3QZ8X5V9K2M7N4P6R8T999"

    if source == "places":
        assert world.scheduled_places(campaign, day) is True
    else:
        assert world.scheduled_registry(campaign, day) is True

    assert world.places.calls == []
    assert world.registry.calls == []
    assert world.runs() == []
    logged = "places_run_skipped" if source == "places" else "registry_run_skipped"
    [record] = [found for found in caplog.records if found.msg == logged]
    expected = "campaign_not_active" if reason == "not_uk" else reason
    assert (record.campaign_id, record.reason) == (campaign, expected)


@pytest.mark.parametrize("source", ["places", "companies_house"])
def test_a_second_scheduled_run_the_same_day_is_skipped_once_one_ended(
    world: World, caplog: pytest.LogCaptureFixture, source: str
) -> None:
    caplog.set_level(logging.INFO)
    if source == "places":
        world.metered_pages(LAW, COLUMBUS, page())
        world.metered_pages(SOLICITORS, COLUMBUS, page())  # due, but must not run today
        assert world.scheduled_places() is True
        assert world.scheduled_places() is True  # a re-queued job, same date
        assert world.places.calls == [(LAW, COLUMBUS, None)]
    else:
        registry_world(world)
        world.registry.searches_by[("law", SIC, 0)] = ()
        assert world.scheduled_registry() is True
        assert world.scheduled_registry() is True
        searched = [call.key for call in world.registry.calls if call.operation == SEARCH_REGISTRY]
        assert searched == [("solicitors", SIC, 0)]

    [run] = world.runs()
    assert (run["status"], run["scheduled_for"]) == ("finished", "2026-11-18")
    logged = "places_run_skipped" if source == "places" else "registry_run_skipped"
    [record] = [found for found in caplog.records if found.msg == logged]
    assert record.reason == "already_ran"


def test_a_run_that_ended_paced_also_counts_as_today_s_run(world: World) -> None:
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 9)  # 9 // 9 = 1 today
    world.metered_pages(LAW, COLUMBUS, page(token="t2"), page())
    assert world.scheduled_places() is True  # one page, then paced
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 1000)  # room again, but today's run has ended

    assert world.scheduled_places() is True

    assert world.places.calls == [(LAW, COLUMBUS, None)]
    assert [run["status"] for run in world.runs()] == ["paced"]


def test_a_manual_run_keeps_no_rotation_no_allowance_and_no_target(world: World) -> None:
    from tests.app.test_place_discovery import US_HASH

    world.set(Key.DISCOVERY_DAILY_LEAD_TARGET, 0)
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 18)  # 18 // 9 = 2 today
    world.record_places(2)  # today's allowance used
    world.past_run(hashed=query_hash((LAW,)), days_ago=1)  # recent pairs
    world.past_run(hashed=query_hash((SOLICITORS,)), days_ago=1)
    world.metered_pages(LAW, COLUMBUS, page(candidate(1, "acme-law.com")))
    world.metered_pages(SOLICITORS, COLUMBUS, page())
    world.site("acme-law.com")

    with world.units.open():
        assert world.place_discovery.run(world.us, US_HASH, "us") is True

    assert world.places.calls == [(LAW, COLUMBUS, None), (SOLICITORS, COLUMBUS, None)]
    run = world.runs()[-1]
    assert (run["status"], run["created"], run["scheduled_for"]) == ("finished", 1, None)


# Scheduled Companies House runs ------------------------------------------------------------------


def registry_world(world: World, *companies: RegistryCompany) -> None:
    world.registry.searches_by[("solicitors", SIC, 0)] = tuple(companies)
    for found in companies:
        world.registry.companies[found.number] = found
        world.registry.officer_lists[found.number] = ()


def test_a_scheduled_registry_run_searches_one_query_and_paces_its_lookup(world: World) -> None:
    world.record_places(111)  # 1,000 // 9 = 111: today's allowance is used
    registry_world(world, company("01234567"))

    assert world.scheduled_registry() is True

    assert world.places.calls == []  # the lookup was paced, not made
    [lead] = world.sql("SELECT ch_number, web_domain, website_lookup_at FROM leads")
    assert lead == ("01234567", None, None)  # created, its lookup undecided
    [run] = world.runs()
    assert run["source"] == "companies_house"
    assert run["status"] == "finished"
    assert run["location"] is None
    assert run["query_hash"] == single_search_hash("solicitors", SIC)
    assert run["scheduled_for"] == "2026-11-18"
    searched = [call.key for call in world.registry.calls if call.operation == SEARCH_REGISTRY]
    assert searched == [("solicitors", SIC, 0)]  # only the first keyword's first page


def test_registry_rotation_moves_to_the_next_keyword(world: World) -> None:
    world.past_run(
        source="companies_house",
        location=None,
        hashed=single_search_hash("solicitors", SIC),
        days_ago=2,
        country="GB",
    )
    world.registry.searches_by[("law", SIC, 0)] = ()

    assert world.scheduled_registry() is True
    assert world.runs()[-1]["query_hash"] == single_search_hash("law", SIC)


def test_the_registry_target_stops_before_the_next_hit(world: World) -> None:
    world.set(Key.DISCOVERY_DAILY_LEAD_TARGET, 1)
    registry_world(world, company("01234567"), company("07654321", "BETA LAW LIMITED"))
    world.record_places(111)

    assert world.scheduled_registry() is True

    run = world.runs()[-1]
    assert (run["status"], run["created"]) == ("target_reached", 1)


# The handlers ------------------------------------------------------------------------------------


def job(job_type: str, payload: dict[str, object]) -> Job:
    return Job("01JD3QZ8X5V9K2M7N4P6R8T0J0", job_type, Lane.BULK, payload, 1)


def test_the_handlers_run_a_scheduled_payload_and_defer_on_a_refusal(world: World) -> None:
    world.metered_pages(LAW, COLUMBUS, page())
    places = world.handler("discover_places")
    payload = {"v": 1, "campaign_id": world.us, "scheduled_for": "2026-11-18"}

    world.set(Key.QUOTA_PLACES_ENTERPRISE, 1)
    world.record_places(1)  # the month's one request used: refused
    with world.units.open():
        assert places(job("discover_places", payload)) == DEFERRED
    assert world.runs() == []

    world.set(Key.QUOTA_PLACES_ENTERPRISE, 1000)
    with world.units.open():
        assert places(job("discover_places", payload)) is None
    assert world.runs()[-1]["scheduled_for"] == "2026-11-18"

    registry = world.handler("discover_registry")
    registry_world(world)
    uk = {"v": 1, "campaign_id": world.uk, "scheduled_for": "2026-11-18"}
    with world.units.open():
        assert registry(job("discover_registry", uk)) is None

    world.clock.set(T0 + timedelta(days=1))  # Thursday: a new day's job
    spend_ai_budget(world)
    thursday = uk | {"scheduled_for": "2026-11-19"}
    with world.units.open():
        assert registry(job("discover_registry", thursday)) == DEFERRED
    assert [run["scheduled_for"] for run in world.runs()] == ["2026-11-18", "2026-11-18"]


@pytest.mark.parametrize("bad", ["2026-02-30", "18/11/2026", 20261118, None])
@pytest.mark.parametrize("job_type", ["discover_places", "discover_registry"])
def test_a_scheduled_payload_without_a_valid_date_is_invalid(
    world: World, job_type: str, bad: object
) -> None:
    payload = {"v": 1, "campaign_id": world.us, "scheduled_for": bad}
    with world.units.open(), pytest.raises(InvalidJob, match="scheduled_for"):
        world.handler(job_type)(job(job_type, payload))


# Today's figures ---------------------------------------------------------------------------------


def test_today_reads_created_leads_target_requests_allowance_and_pools(world: World) -> None:
    world.past_run(hashed="0" * 16, days_ago=0.1, created=12)
    world.past_run(hashed="0" * 16, days_ago=1, created=40)  # yesterday's
    world.record_places(4)
    world.record_places(100, datetime(2026, 11, 17, 9, 0, tzinfo=UTC))  # earlier this month
    world.chain(pool=FakePool(ready=30, share=6))

    with world.units.open(read_only=True):
        today = world.topup().today()

    assert today == DiscoveryToday(
        local_date=TODAY,
        created=12,
        target=60,
        places_requests=4,
        places_allowance=(1000 - 100) // 9,
        pools=(PoolLine(world.uk, 30), PoolLine(world.us, 30)),
    )


def test_today_follows_the_operator_s_time_zone(world: World) -> None:
    world.set(Key.SENDER_TIMEZONE, "America/New_York")  # 09:00 UTC is 04:00 there
    world.record_places(3, datetime(2026, 11, 18, 4, 0, tzinfo=UTC))  # 23:00 yesterday there
    world.record_places(2, datetime(2026, 11, 18, 6, 0, tzinfo=UTC))  # 01:00 today there

    with world.units.open(read_only=True):
        today = world.topup().today()

    assert (today.local_date, today.places_requests) == (TODAY, 2)
    assert today.places_allowance == (1000 - 3) // 9


def test_west_of_utc_the_last_local_evening_spreads_the_local_month_s_leftover(
    world: World,
) -> None:
    world.set(Key.SENDER_TIMEZONE, "America/New_York")
    world.clock.set(datetime(2026, 12, 1, 1, 0, tzinfo=UTC))  # Mon 30 Nov 20:00 there
    world.record_places(600, datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    world.record_places(5, datetime(2026, 11, 30, 18, 0, tzinfo=UTC))  # 13:00 today there

    with world.units.open(read_only=True):
        today = world.topup().today()

    assert today.local_date == date(2026, 11, 30)  # 1 weekday left, today
    assert (today.places_requests, today.places_allowance) == (5, 1000 - 600)


def test_east_of_utc_the_1st_starts_a_fresh_local_month(world: World) -> None:
    world.set(Key.SENDER_TIMEZONE, "Asia/Tokyo")
    world.clock.set(datetime(2026, 11, 30, 18, 0, tzinfo=UTC))  # Tue 1 Dec 03:00 there
    world.record_places(600, datetime(2026, 11, 10, 12, 0, tzinfo=UTC))
    world.record_places(30, datetime(2026, 11, 30, 14, 0, tzinfo=UTC))  # 23:00 on 30 Nov there
    world.record_places(50, datetime(2026, 11, 30, 16, 0, tzinfo=UTC))  # 01:00 today there

    with world.units.open(read_only=True):
        today = world.topup().today()

    assert today.local_date == date(2026, 12, 1)  # 23 weekdays left in December
    assert (today.places_requests, today.places_allowance) == (50, 1000 // 23)


def test_the_discovery_page_and_health_panel_show_today(world: World) -> None:
    world.past_run(hashed="0" * 16, days_ago=0.1, created=7)
    world.record_places(5)
    world.chain(pool=FakePool(ready=30, share=6))
    dashboard = Dashboard(
        units=world.units, clock=world.clock, meter=world.meter, with_topup=True
    )
    cookie = dashboard.open_session()

    discovery = unescape(dashboard.get("/leads/discovery", cookie=cookie).text)
    health = unescape(dashboard.get("/health/panel", cookie=cookie).text)

    allowance = (1000 - 0) // 9
    for text in (discovery, health):
        assert "Leads created today: 7 of 60" in text
        assert f"Places requests today: 5 of {allowance} allowed" in text
    assert "Law-PrivateAI × US: Pool sufficient: 30 Leads ready" in discovery
    assert "pool_sufficient" in discovery  # the runs table shows the new status
    assert "discover_places" in health and "discover_registry" in health  # last successes


def test_without_a_top_up_the_pages_show_no_today_section(world: World) -> None:
    dashboard = Dashboard(units=world.units, clock=world.clock)
    cookie = dashboard.open_session()

    assert "Leads created today" not in dashboard.get("/leads/discovery", cookie=cookie).text
    assert "Leads created today" not in dashboard.get("/health/panel", cookie=cookie).text


# No query or location text leaks -----------------------------------------------------------------


def test_no_query_or_location_text_reaches_a_payload_a_log_line_or_an_event(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.chain()
    world.chain(TODAY, "ran", FakePool(ready=30, share=6))
    world.metered_pages(LAW, COLUMBUS, page(candidate(1, "acme-law.com")))
    world.site("acme-law.com")
    registry_world(world, company("01234567"))
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 18)
    world.record_places(1)  # the run's page then makes 2 of 2: the registry lookup is paced

    assert world.scheduled_places() is True
    assert world.scheduled_registry() is True

    planted = (LAW, SOLICITORS, COLUMBUS, LEEDS, "Maps Name", POSTCODE, "ACME SOLICITORS")
    texts = [row[0] for row in world.sql("SELECT payload FROM jobs")]
    texts += [
        " ".join(str(cell) for cell in row) for row in world.sql("SELECT * FROM events")
    ]
    for record in caplog.records:
        if record.name.startswith(LOGGERS):
            texts.append(record.getMessage() + repr(vars(record)))
    leaked = [(text, word) for text in texts for word in planted if word in text]
    assert leaked == []
    assert texts  # something was scanned
