"""Story 8.3's Places discovery (FR-5, FR-9, FR-2, FR-49, AD-7, AD-10, AD-16) on a temporary
database at head seeded with the shipped files, ``FrozenClock``, ``ScriptedPlaces`` and a
recording ``ScriptedCrawler``: every row of the spec's matrix (a new organization, no website,
denylisted, a shared platform, a site that fails to load, a place ID already held, a domain
held without a place ID, the gate blocking, a Market not enabled, the quota refusing page 2, a
double submit, refused requests), plus robots.txt, the page cap, a failed run, a stopped run,
the job handler and the request. No request reaches Google or a website."""

import dataclasses
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.discovery.place_ingest import (
    REFUSED_CAMPAIGN,
    REFUSED_LOCATION,
    REFUSED_NO_QUERIES,
    PlaceDiscovery,
    PlacesRunRefused,
)
from nextron.app.discovery.places import PlaceSearches
from nextron.app.jobqueue.api import Enqueued, JobQueue
from nextron.app.ops.api import Seeds
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Campaigns, Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.budget import ApiCall
from nextron.domain.campaign import validate_campaigns
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.contactability import DomainSubject, SuppressionScope
from nextron.domain.job import DEFERRED, InvalidJob, Job, Lane, decode_payload
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.place_discovery import location_hash, query_hash
from nextron.domain.segments import DiscoveryQueries, validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.domain.suppression_entry import SuppressionSource
from nextron.domain.settings_schema import Key
from nextron.ports.crawler import FetchedPage
from nextron.ports.errors import AdapterError
from nextron.ports.places import PlaceCandidate, PlaceSearchPage
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.places import ScriptedPlaces
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.fakes.usage_meter import InMemoryUsageMeter

T0: Final = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
STAMP: Final = "2026-11-18T09:00:00.000000Z"
LOCATION: Final = "Columbus, OH"
LEEDS: Final = "Leeds"
LOCATIONS: Final = (f"{LOCATION} | US", f"{LEEDS} | GB")
US_HASH: Final = location_hash(LOCATION, "US")
GB_HASH: Final = location_hash(LEEDS, "GB")
LAW: Final = "law firm"
SOLICITORS: Final = "solicitors"
MAPS_NAME: Final = "Zed Maps Name"
ALLOW_ALL: Final = "User-agent: *\nDisallow:\n"
LOGGER: Final = "nextron.app.discovery.place_ingest"


def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


def no_jobs() -> Any:
    raise AssertionError("no other job runs in these tests")


@dataclass
class World:
    """Places discovery on real units of work with the shipped seed and the real job types."""

    engine: Engine
    units: UnitOfWorkFactory
    clock: FrozenClock
    meter: InMemoryUsageMeter
    places: ScriptedPlaces
    crawler: ScriptedCrawler
    queue: JobQueue
    discovery: Discovery
    place_discovery: PlaceDiscovery
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

    def segment(self) -> str:
        [(segment,)] = self.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
        return str(segment)

    def site(
        self,
        domain: str,
        html: str = "",
        *,
        robots: str = ALLOW_ALL,
        page: FetchedPage | AdapterError | None = None,
    ) -> str:
        """Script ``https://www.<domain>``'s robots.txt and homepage; its homepage URL."""
        origin = f"https://www.{domain}"
        self.crawler.robots[f"{origin}/robots.txt"] = robots_txt(f"{origin}/robots.txt", robots)
        self.crawler.pages[f"{origin}/"] = page if page is not None else html_page(
            f"{origin}/", html
        )
        return f"{origin}/"

    def search(self, *pages: PlaceSearchPage, query: str = LAW, location: str = LOCATION) -> None:
        """Script ``query``'s pages in ``location``, chained by tokens ``t2``, ``t3``."""
        tokens: list[str | None] = [None, *(f"t{number}" for number in range(2, len(pages) + 1))]
        for token, page in zip(tokens, pages, strict=True):
            self.places.pages[(query, location, token)] = page
        if query == LAW:
            self.places.pages.setdefault((SOLICITORS, location, None), PlaceSearchPage((), None))

    def run(self, campaign: str | None = None, hashed: str = US_HASH, code: str = "us") -> bool:
        with self.units.open():
            return self.place_discovery.run(campaign or self.us, hashed, code)

    def request(self, campaign: str | None = None, hashed: str = US_HASH) -> Enqueued:
        with self.units.open():
            return self.place_discovery.request_run(campaign or self.us, hashed)

    def runs(self) -> list[dict[str, Any]]:
        names = (
            "source campaign_id segment_id country_code location query_hash status started_at "
            "finished_at seen created merged suppressed no_website market_blocked"
        ).split()
        rows = self.sql(f"SELECT {', '.join(names)} FROM discovery_runs ORDER BY rowid")
        return [dict(zip(names, row, strict=True)) for row in rows]

    def counts(self) -> dict[str, int]:
        [run] = self.runs()
        names = "seen created merged suppressed no_website market_blocked".split()
        return {name: run[name] for name in names}

    def leads(self) -> list[dict[str, Any]]:
        names = (
            "id name web_domain place_id segment_id country_code merged_into pipeline_stage "
            "place_lat place_lng place_coords_at"
        ).split()
        rows = self.sql(f"SELECT {', '.join(names)} FROM leads ORDER BY rowid")
        return [dict(zip(names, row, strict=True)) for row in rows]

    def jobs(self, job_type: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT idempotency_key, payload, lane, concurrency_key FROM jobs WHERE type = ? "
            "ORDER BY rowid",
            (job_type,),
        )

    def lead(self, name: str = "Held Firm", **keys: Any) -> str:
        identity = LeadIdentity(name, self.segment(), country_code="US", **keys)
        upserted = self.transact(
            lambda: self.discovery.upsert_lead(identity, LeadSource.SIGNAL_INBOX)
        )
        assert upserted.lead_id is not None
        return upserted.lead_id

    def handler(self) -> Callable[[Job], object]:
        return job_types_on(lambda: self.place_discovery).get("discover_places").handler


def job_types_on(place_discovery: Callable[[], Any]) -> Any:
    return main.build_job_types(
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
        place_discovery=place_discovery,
    )


def build_world(engine: Engine) -> World:
    """The shipped seed stored, ``law_private_ai_us`` active, two locations set."""
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
    crawler = ScriptedCrawler()
    holder: list[PlaceDiscovery] = []
    queue = JobQueue(clock, job_types_on(lambda: holder[0]))
    discovery = Discovery(clock, Pipeline(clock), queue)
    place_discovery = PlaceDiscovery(
        clock, PlaceSearches(clock, meter, places), crawler, discovery, queue
    )
    holder.append(place_discovery)
    world = World(engine, units, clock, meter, places, crawler, queue, discovery, place_discovery)
    world.campaigns = {str(key): str(id_) for id_, key in world.sql("SELECT id, key FROM campaigns")}
    with units.open():
        Campaigns(clock).activate(world.us)
    world.set(Key.PLACES_DISCOVERY_LOCATIONS, LOCATIONS)
    return world


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def candidate(
    place_id: str = "ChIJ-acme",
    website: str | None = "https://www.acme-law.com/?utm_source=maps",
    name: str | None = MAPS_NAME,
    lat: float | None = 39.9612,
    lng: float | None = -82.9988,
) -> PlaceCandidate:
    return PlaceCandidate(place_id, website, name, lat, lng)


def page(*candidates: PlaceCandidate, token: str | None = None) -> PlaceSearchPage:
    return PlaceSearchPage(tuple(candidates), token)


# A new organization -----------------------------------------------------------------------------


def test_a_new_organization_becomes_a_lead_named_and_keyed_from_its_own_website(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.site("acme-law.com", "<html><head><title>  Acme\n Law </title></head></html>")
    world.search(page(candidate()))

    assert world.run() is True

    [lead] = world.leads()
    assert {key: lead[key] for key in lead if key != "id"} == {
        "name": "Acme Law",  # the site's title, never the Maps name
        "web_domain": "acme-law.com",
        "place_id": "ChIJ-acme",
        "segment_id": world.segment(),
        "country_code": "US",
        "merged_into": None,
        "pipeline_stage": "new",
        "place_lat": 39.9612,
        "place_lng": -82.9988,
        "place_coords_at": STAMP,
    }
    [(key, payload, lane, concurrency)] = world.jobs("enrich_lead")
    assert key == f"enrich_lead:{lead['id']}:2026-11-18"
    assert json.loads(payload) == {"v": 1, "lead_id": lead["id"]}
    assert concurrency == "domain:acme-law.com"
    assert world.crawler.urls("robots") == ["https://www.acme-law.com/robots.txt"]
    assert world.crawler.urls("page") == ["https://www.acme-law.com/"]
    assert world.places.calls == [(LAW, LOCATION, None), (SOLICITORS, LOCATION, None)]
    [run] = world.runs()
    assert run == {
        "source": "places",
        "campaign_id": world.us,
        "segment_id": world.segment(),
        "country_code": "US",
        "location": LOCATION,
        "query_hash": query_hash((LAW, SOLICITORS)),
        "status": "finished",
        "started_at": STAMP,
        "finished_at": STAMP,
        "seen": 1,
        "created": 1,
        "merged": 0,
        "suppressed": 0,
        "no_website": 0,
        "market_blocked": 0,
    }
    [created] = world.sql("SELECT cause FROM events WHERE entity = 'leads'")
    assert created == ("created_from_places",)
    shown = " ".join(record.getMessage() + repr(vars(record)) for record in caplog.records)
    assert MAPS_NAME not in shown
    assert "acme-law" not in shown
    assert "39.96" not in shown


def test_og_site_name_wins_over_the_title_and_the_domain_is_the_last_resort(
    world: World,
) -> None:
    world.site(
        "acme-law.com",
        '<head><meta property="og:site_name" content="Acme Law LLP"><title>Home</title></head>',
    )
    world.site("bare-firm.com", "<p>No title here.</p>")
    world.search(
        page(
            candidate(),
            candidate("ChIJ-bare", "https://www.bare-firm.com/", MAPS_NAME),
        )
    )

    world.run()

    assert [lead["name"] for lead in world.leads()] == ["Acme Law LLP", "bare-firm.com"]
    assert world.counts()["created"] == 2


def test_the_name_comes_from_the_final_url_s_site_after_a_redirect(world: World) -> None:
    landed = html_page(
        "https://www.acme-law.com/",
        "<title>Acme Law</title>",
        final_url="https://acmelaw.co.uk/home",
    )
    world.site("acme-law.com", page=landed)
    world.search(page(candidate()))

    world.run()

    [lead] = world.leads()
    assert (lead["name"], lead["web_domain"]) == ("Acme Law", "acmelaw.co.uk")


def test_a_candidate_without_coordinates_gets_a_lead_without_them(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.search(page(candidate(lat=None, lng=None)))

    world.run()

    [lead] = world.leads()
    assert (lead["place_lat"], lead["place_lng"], lead["place_coords_at"]) == (None, None, None)


# No own website ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "website",
    [
        None,
        "https://www.linkedin.com/company/acme-law",  # FR-49 denylist
        "https://www.google.com/maps/place/acme",  # denylisted Maps path
        "https://www.indeed.com/cmp/acme",
        "https://linktr.ee/acmelaw",  # a shared platform
        "https://acme-law.business.site/",
        "ftp://acme-law.com/",
        "https://10.0.0.5/",
    ],
)
def test_no_own_website_creates_nothing_and_fetches_nothing(
    world: World, website: str | None
) -> None:
    world.search(page(candidate(website=website)))

    world.run()

    assert world.leads() == []
    assert world.crawler.calls == []
    assert world.counts() == {
        "seen": 1,
        "created": 0,
        "merged": 0,
        "suppressed": 0,
        "no_website": 1,
        "market_blocked": 0,
    }


@pytest.mark.parametrize(
    "answer",
    [
        AdapterError(True, "fetch_timeout", "the fetch timed out"),
        AdapterError(False, "fetch_refused", "the fetch was refused (denylisted)"),
        html_page("https://www.acme-law.com/", status=404),
        html_page("https://www.acme-law.com/", status=503),
        html_page("https://www.acme-law.com/", "{}", media_type="application/json"),
        html_page(
            "https://www.acme-law.com/", "<title>x</title>", final_url="https://acme.wix.com/"
        ),
        html_page(
            "https://www.acme-law.com/",
            "<title>x</title>",
            final_url="https://www.linkedin.com/company/acme",
        ),
    ],
)
def test_a_site_that_fails_to_load_is_no_own_website(
    world: World, answer: FetchedPage | AdapterError
) -> None:
    world.site("acme-law.com", page=answer)
    world.search(page(candidate()))

    world.run()

    assert world.leads() == []
    assert world.counts()["no_website"] == 1
    assert world.crawler.urls("page") == ["https://www.acme-law.com/"]


def test_a_homepage_robots_txt_disallows_is_never_fetched(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>", robots="User-agent: *\nDisallow: /\n")
    world.search(page(candidate()))

    world.run()

    assert world.leads() == []
    assert world.counts()["no_website"] == 1
    assert world.crawler.urls("robots") == ["https://www.acme-law.com/robots.txt"]
    assert world.crawler.urls("page") == []


def test_an_unreadable_robots_txt_fetches_no_homepage(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.crawler.robots["https://www.acme-law.com/robots.txt"] = AdapterError(
        True, "fetch_timeout", "the fetch timed out"
    )
    world.search(page(candidate()))

    world.run()

    assert world.crawler.urls("page") == []
    assert world.counts()["no_website"] == 1


def test_the_homepage_is_fetched_at_the_robots_crawl_delay(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>", robots="User-agent: *\nCrawl-delay: 4\n")
    world.search(page(candidate()))

    world.run()

    [robots, home] = world.crawler.calls
    assert (robots.min_interval_s, home.min_interval_s, home.site) == (1.0, 4.0, None)


# Deduplication ----------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["new", "lost", "disqualified", "merged"])
def test_a_place_id_any_lead_holds_merges_without_a_fetch(world: World, state: str) -> None:
    held = world.lead(place_id="ChIJ-acme", web_domain="held-firm.com")
    if state == "merged":
        survivor = world.lead("Older", web_domain="older-firm.com")
        world.sql("UPDATE leads SET created_at = '2026-01-01T00:00:00.000000Z' WHERE id = ?", (survivor,))
        world.transact(lambda: world.discovery.merge(survivor, held))
    elif state != "new":
        world.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (state, held))
    before = world.leads()
    world.search(page(candidate()))

    world.run()

    assert world.leads() == before
    assert world.crawler.calls == []
    assert world.counts()["merged"] == 1
    assert world.jobs("enrich_lead") == []


def test_a_domain_held_by_a_lead_without_a_place_id_gets_it_attached(world: World) -> None:
    held = world.lead("Acme (CSV)", web_domain="acme-law.com")
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.search(page(candidate()))

    world.run()

    [lead] = world.leads()
    assert (lead["id"], lead["name"], lead["place_id"]) == (held, "Acme (CSV)", "ChIJ-acme")
    assert (lead["place_lat"], lead["place_coords_at"]) == (None, None)  # only a new Lead
    assert world.counts()["merged"] == 1
    assert world.jobs("enrich_lead") == []


def test_the_same_candidate_twice_in_a_run_merges_the_second(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.search(page(candidate()))
    world.places.pages[(SOLICITORS, LOCATION, None)] = page(candidate())

    world.run()

    assert len(world.leads()) == 1
    assert world.counts() == {
        "seen": 2,
        "created": 1,
        "merged": 1,
        "suppressed": 0,
        "no_website": 0,
        "market_blocked": 0,
    }


# The gate and the Market ------------------------------------------------------------------------


def test_a_suppressed_domain_creates_nothing_and_counts_suppressed(world: World) -> None:
    world.transact(
        lambda: Compliance(world.clock).suppress(
            DomainSubject("acme-law.com"),
            SuppressionScope.FIRM,
            SuppressionSource.OPERATOR,
            None,
        )
    )
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.search(page(candidate()))

    world.run()

    assert world.leads() == []
    assert world.jobs("enrich_lead") == []
    assert world.counts()["suppressed"] == 1


def test_a_country_that_is_not_an_enabled_market_is_market_blocked(world: World) -> None:
    world.sql("UPDATE markets SET enabled = 0 WHERE country_code = 'US'")
    world.search(page(candidate()))

    world.run()

    assert world.leads() == []
    assert world.crawler.calls == []
    assert world.counts()["market_blocked"] == 1


def test_an_invalid_place_id_is_only_seen(world: World) -> None:
    world.search(page(candidate("not a place id")))

    world.run()

    assert world.leads() == []
    assert world.crawler.calls == []
    assert world.counts() == {
        "seen": 1,
        "created": 0,
        "merged": 0,
        "suppressed": 0,
        "no_website": 0,
        "market_blocked": 0,
    }


# Pages, quota and failures ----------------------------------------------------------------------


def test_the_quota_refusing_page_2_keeps_page_1_and_defers(world: World) -> None:
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 1)

    def first() -> PlaceSearchPage:  # what the real adapter meters for its one request
        world.meter.record(ApiCall("places", "text_search", ok=True, sku="enterprise"))
        return page(candidate(), token="t2")

    world.places.pages[(LAW, LOCATION, None)] = first
    world.site("acme-law.com", "<title>Acme Law</title>")

    assert world.run() is False

    assert [lead["name"] for lead in world.leads()] == ["Acme Law"]
    assert world.places.calls == [(LAW, LOCATION, None)]  # page 2 was never requested
    [run] = world.runs()
    assert (run["status"], run["finished_at"], run["created"]) == ("deferred", STAMP, 1)


def test_the_page_cap_covers_the_whole_run(world: World) -> None:
    world.set(Key.PLACES_MAX_PAGES_PER_RUN, 2)
    world.search(page(token="t2"), page(token="t3"), page())

    world.run()

    assert world.places.calls == [(LAW, LOCATION, None), (LAW, LOCATION, "t2")]
    assert world.runs()[0]["status"] == "finished"


def test_queries_run_in_seed_order_each_paged_to_its_end(world: World) -> None:
    world.search(page(token="t2"), page())
    world.places.pages[(SOLICITORS, LOCATION, None)] = page()

    world.run()

    assert world.places.calls == [
        (LAW, LOCATION, None),
        (LAW, LOCATION, "t2"),
        (SOLICITORS, LOCATION, None),
    ]


def test_a_places_error_fails_the_run_and_is_raised(world: World) -> None:
    error = AdapterError(True, "places_rate_limited", "Places answered 429")
    world.places.pages[(LAW, LOCATION, None)] = error

    with pytest.raises(AdapterError) as raised:
        world.run()

    assert raised.value is error
    [run] = world.runs()
    assert (run["status"], run["finished_at"]) == ("failed", STAMP)


def test_an_error_after_page_1_keeps_its_leads_and_fails(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.search(page(candidate(), token="t2"))
    world.places.pages[(LAW, LOCATION, "t2")] = AdapterError(
        False, "places_refused", "Places refused the request"
    )

    with pytest.raises(AdapterError):
        world.run()

    assert len(world.leads()) == 1
    assert world.runs()[0]["status"] == "failed"


def test_a_campaign_that_cannot_run_any_more_stops_with_nothing_fetched(world: World) -> None:
    with world.units.open():
        Campaigns(world.clock).pause(world.us)

    assert world.run() is True

    [run] = world.runs()
    assert (run["status"], run["finished_at"], run["seen"]) == ("stopped", STAMP, 0)
    assert world.places.calls == []


@pytest.mark.parametrize(
    ("campaign", "hashed", "code"),
    [
        ("01JD0000000000000000000000", US_HASH, "us"),  # unknown Campaign
        (None, "0" * 16, "us"),  # a location no longer set
        (None, US_HASH, "gb"),  # the code doesn't match the location
    ],
)
def test_a_run_whose_campaign_or_location_is_gone_is_skipped(
    world: World, campaign: str | None, hashed: str, code: str
) -> None:
    assert world.run(campaign, hashed, code) is True

    assert world.runs() == []
    assert world.places.calls == []


# The handler ------------------------------------------------------------------------------------


def stored_job(world: World) -> Job:
    [(_, payload, _, _)] = world.jobs("discover_places")
    return Job("01JD0000000000000000000099", "discover_places", Lane.BULK, decode_payload(payload), 1)


def test_the_handler_runs_the_queued_job_and_defers_on_a_refusal(world: World) -> None:
    world.request()
    job = stored_job(world)
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 0)

    with world.units.open():
        result = world.handler()(job)

    assert result is DEFERRED
    assert world.runs() == []  # Story 8.1: refused before the run row is written
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 1000)
    world.search(page())
    with world.units.open():
        assert world.handler()(job) is None
    assert [run["status"] for run in world.runs()] == ["finished"]


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 1, "location": US_HASH, "country_code": "us"},
        {"v": 1, "campaign_id": "01JD0000000000000000000000", "location": "abc", "country_code": "us"},
        {"v": 1, "campaign_id": "01JD0000000000000000000000", "location": US_HASH.upper(), "country_code": "us"},
        {"v": 1, "campaign_id": "01JD0000000000000000000000", "location": US_HASH, "country_code": "US"},
        {"v": 1, "campaign_id": "01JD0000000000000000000000", "location": US_HASH, "country_code": "u1"},
        {"v": 1, "campaign_id": "01JD0000000000000000000000", "location": US_HASH},
    ],
)
def test_the_handler_refuses_a_bad_payload(world: World, payload: dict[str, object]) -> None:
    job = Job("01JD0000000000000000000099", "discover_places", Lane.BULK, payload, 1)

    with pytest.raises(InvalidJob):
        world.handler()(job)


# The request ------------------------------------------------------------------------------------


def test_a_request_enqueues_one_bulk_job_and_a_double_submit_returns_it(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    first = world.request()
    again = world.request()

    assert first.created and not again.created
    assert again.job_id == first.job_id
    [(key, payload, lane, concurrency)] = world.jobs("discover_places")
    assert key == f"discover_places:{world.us}:{US_HASH}"
    assert json.loads(payload) == {
        "v": 1,
        "campaign_id": world.us,
        "location": US_HASH,
        "country_code": "us",
    }
    assert (lane, concurrency) == ("bulk", "domain:places.googleapis.com")
    assert LOCATION not in caplog.text and "Columbus" not in payload


def test_a_request_for_a_campaign_that_isnt_active_is_refused(world: World) -> None:
    with pytest.raises(PlacesRunRefused) as refused:
        world.request(world.uk, GB_HASH)

    assert refused.value.reason == REFUSED_CAMPAIGN
    assert world.jobs("discover_places") == []


@pytest.mark.parametrize("hashed", [GB_HASH, "0" * 16, "not-a-hash"])
def test_a_request_for_a_location_outside_the_campaign_s_market_is_refused(
    world: World, hashed: str
) -> None:
    with pytest.raises(PlacesRunRefused) as refused:
        world.request(hashed=hashed)

    assert refused.value.reason == REFUSED_LOCATION
    assert world.jobs("discover_places") == []


def test_an_unknown_campaign_is_refused(world: World) -> None:
    with pytest.raises(PlacesRunRefused) as refused:
        world.request("01JD0000000000000000000000")

    assert refused.value.reason == REFUSED_CAMPAIGN


# Review pass 1 ----------------------------------------------------------------------------------


def test_a_request_for_a_market_that_is_not_enabled_is_refused(world: World) -> None:
    world.sql("UPDATE markets SET enabled = 0 WHERE country_code = 'US'")

    with pytest.raises(PlacesRunRefused) as refused:
        world.request()

    assert refused.value.reason == REFUSED_LOCATION
    assert world.jobs("discover_places") == []


def test_a_request_for_a_segment_without_places_queries_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = setup_queries.segment_by_id

    def without_queries(segment_id: str) -> Any:
        found = real(segment_id)
        assert found is not None
        definition = dataclasses.replace(
            found.definition, discovery_queries=DiscoveryQueries(places=())
        )
        return dataclasses.replace(found, definition=definition)

    monkeypatch.setattr(setup_queries, "segment_by_id", without_queries)

    with pytest.raises(PlacesRunRefused) as refused:
        world.request()

    assert refused.value.reason == REFUSED_NO_QUERIES
    assert "no Places search queries" in refused.value.message
    assert world.jobs("discover_places") == []


def test_pausing_the_campaign_between_pages_stops_the_run(world: World) -> None:
    world.places.pages[(LAW, LOCATION, None)] = page(token="t2")
    world.places.pages[(LAW, LOCATION, "t2")] = page()
    paused = []

    real_page = world.place_discovery._searches.page

    def page_then_pause(query: str, location: str, token: str | None = None, **kw: Any) -> Any:
        answer = real_page(query, location, token, **kw)
        if not paused:  # the Operator pauses the Campaign after page 1 arrives
            world.sql("UPDATE campaigns SET status = 'paused' WHERE id = ?", (world.us,))
            paused.append(True)
        return answer

    world.place_discovery._searches.page = page_then_pause  # type: ignore[method-assign]

    assert world.run() is True

    assert world.places.calls == [(LAW, LOCATION, None)]
    [run] = world.runs()
    assert (run["status"], run["finished_at"]) == ("stopped", STAMP)


def test_a_run_left_running_by_a_crash_is_failed_when_the_next_run_starts(world: World) -> None:
    world.search(page())
    world.sql(
        "INSERT INTO discovery_runs (id, source, campaign_id, segment_id, country_code, "
        "location, query_hash, status, started_at, finished_at, seen, created, merged, "
        "suppressed, no_website, market_blocked) VALUES ('01JD0000000000000000000077', "
        "'places', ?, ?, 'US', 'Columbus, OH', '0000000000000000', 'running', "
        "'2026-11-17T09:00:00.000000Z', NULL, 0, 0, 0, 0, 0, 0)",
        (world.us, world.segment()),
    )

    world.run()

    assert [(run["status"], run["finished_at"]) for run in world.runs()] == [
        ("failed", STAMP),
        ("finished", STAMP),
    ]


def test_a_place_id_is_looked_up_and_stored_as_normalised(world: World) -> None:
    world.site("acme-law.com", "<title>Acme Law</title>")
    world.lead(place_id="ChIJ-held", web_domain="held-firm.com")
    world.search(page(candidate(" ChIJ-acme "), candidate(" ChIJ-held ")))

    world.run()

    assert sorted(lead["place_id"] for lead in world.leads()) == ["ChIJ-acme", "ChIJ-held"]
    assert world.counts()["created"] == 1
    assert world.counts()["merged"] == 1


# record_place_coords ----------------------------------------------------------------------------


def test_record_place_coords_sets_all_three_columns_at_now(world: World) -> None:
    lead_id = world.lead(place_id="ChIJ-held")

    world.transact(lambda: world.discovery.record_place_coords(lead_id, 51.5, -0.12))

    [lead] = world.leads()
    assert (lead["place_lat"], lead["place_lng"], lead["place_coords_at"]) == (51.5, -0.12, STAMP)


@pytest.mark.parametrize(
    ("lat", "lng", "error"),
    [
        (51, -0.12, TypeError),
        (True, -0.12, TypeError),
        (51.5, "0", TypeError),
        (float("nan"), -0.12, ValueError),
        (51.5, float("inf"), ValueError),
        (90.5, -0.12, ValueError),
        (51.5, -180.5, ValueError),
    ],
)
def test_record_place_coords_refuses_bad_coordinates(
    world: World, lat: Any, lng: Any, error: type[Exception]
) -> None:
    lead_id = world.lead(place_id="ChIJ-held")

    with pytest.raises(error):
        world.transact(lambda: world.discovery.record_place_coords(lead_id, lat, lng))

    assert world.leads()[0]["place_lat"] is None


def test_record_place_coords_refuses_an_unknown_lead(world: World) -> None:
    with pytest.raises(LookupError):
        world.transact(
            lambda: world.discovery.record_place_coords("01JD0000000000000000000000", 1.0, 2.0)
        )


# Wiring -----------------------------------------------------------------------------------------


def test_build_app_gives_the_discover_places_jobs_the_built_place_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    built: list[PlaceDiscovery] = []
    registries: list[dict[str, Any]] = []
    real_discovery, real_build_job_types = main.PlaceDiscovery, main.build_job_types

    def place_discovery(*args: Any) -> PlaceDiscovery:
        built.append(real_discovery(*args))
        return built[-1]

    def recording_job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_build_job_types(**kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "PlaceDiscovery", place_discovery)
    monkeypatch.setattr(main, "build_job_types", recording_job_types)
    crawler = ScriptedCrawler()

    app = main.build_app(load_bootstrap(), units=FakeUnits(), crawler=crawler)

    [discovery] = built
    [given] = registries  # the discover_places jobs reach the built service, not the default
    assert given["place_discovery"]() is discovery
    assert app.state.place_discovery is discovery
    assert discovery._crawler is crawler  # noqa: SLF001 - the crawl's own crawler
    assert discovery._searches is app.state.place_searches  # noqa: SLF001
