"""Story 8.4's Companies House discovery (FR-6, FR-9, FR-12, FR-13, AD-7, AD-10, AD-16) on a
temporary database at head seeded with the shipped files, ``FrozenClock``, ``ScriptedRegistry``,
``ScriptedPlaces`` and a recording ``ScriptedCrawler``: every row of the spec's matrix (a new
active company, a number already held, dissolved and dormant companies, the gate blocking, no
usable website, the Places quota refusing, officers, the budget refusing page 2, a 429 mid-run,
the Operator's website, a non-UK or inactive Campaign), plus officer naming, the page cap, a
stopped run, a failed run, the job handler, the request, the registry-only legal form kept by
``check_legal_form``, and nothing sensitive in the logs. No request reaches Companies House,
Google or a website."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import (
    REFUSED_BLOCKED,
    REFUSED_HAS_WEBSITE,
    REFUSED_LEAD,
    REFUSED_SHARED,
    REFUSED_URL,
    Discovery,
    WebsiteRefused,
)
from nextron.app.discovery.legal_form import LegalFormChecks
from nextron.app.discovery.places import PlaceSearches
from nextron.app.discovery import registry_ingest
from nextron.app.discovery.registry_ingest import (
    REFUSED_CAMPAIGN,
    REFUSED_NOT_UK,
    RegistryDiscovery,
    RegistryRunRefused,
)
from nextron.app.jobqueue.api import Enqueued, JobQueue
from nextron.app.ops.api import Seeds
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.setup.api import Campaigns, Setup
from nextron.bootstrap import load_bootstrap
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.budget import AiCall
from nextron.domain.campaign import validate_campaigns
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.contactability import (
    Blocked,
    BlockReason,
    DomainSubject,
    NewLeadSubject,
    SuppressionScope,
)
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.offers import validate_offers
from nextron.domain.registry_discovery import registry_query_hash
from nextron.domain.segments import RegistryQueries, validate_segments
from nextron.domain.sequences import validate_sequences
from nextron.domain.settings_schema import Key
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.errors import AdapterError
from nextron.ports.places import PlaceCandidate, PlaceSearchPage
from nextron.ports.registry import RegistryCompany, RegistryOfficer
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.places import ScriptedPlaces
from tests.fakes.registry import ScriptedRegistry
from tests.fakes.secrets import InMemorySecrets
from tests.fakes.units import FakeUnits
from tests.fakes.usage_meter import InMemoryUsageMeter

T0: Final = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
STAMP: Final = "2026-11-19T09:00:00.000000Z"
NUMBER: Final = "01234567"
REGISTERED: Final = "ACME SOLICITORS LIMITED"
POSTCODE: Final = "LS1 4AP"
SIC: Final = ("69102",)
KEYWORDS: Final = ("solicitors", "law", "legal")
ALLOW_ALL: Final = "User-agent: *\nDisallow:\n"
LOGGERS: Final = ("nextron.app.discovery", "nextron.app.research")
DETAIL: Final = f"Companies House API officers, company {NUMBER}"


def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


def no_jobs() -> Any:
    raise AssertionError("no other job runs in these tests")


def company(
    number: str = NUMBER,
    name: str = REGISTERED,
    *,
    company_type: str | None = "ltd",
    status: str | None = "active",
    postcode: str | None = POSTCODE,
    accounts: str | None = "full",
    sic: tuple[str, ...] = SIC,
) -> RegistryCompany:
    return RegistryCompany(number, name, company_type, status, postcode, accounts, sic)


def officer(
    name: str = "SMITH, Jane",
    role: str = "director",
    occupation: str | None = "Managing Partner",
    resigned: bool = False,
) -> RegistryOfficer:
    return RegistryOfficer(name, role, occupation, resigned)


@dataclass
class World:
    """Companies House discovery on real units of work with the shipped seed."""

    engine: Engine
    units: UnitOfWorkFactory
    clock: FrozenClock
    meter: InMemoryUsageMeter
    registry: ScriptedRegistry
    places: ScriptedPlaces
    crawler: ScriptedCrawler
    queue: JobQueue
    discovery: Discovery
    research: Research
    runs: RegistryDiscovery
    campaigns: dict[str, str] = field(default_factory=dict)

    @property
    def uk(self) -> str:
        return self.campaigns["law_private_ai_uk"]

    @property
    def us(self) -> str:
        return self.campaigns["law_private_ai_us"]

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

    def segment(self, key: str = "law_private_ai") -> str:
        [(segment,)] = self.sql("SELECT id FROM segments WHERE key = ?", (key,))
        return str(segment)

    def search(self, keyword: str | None, *hits: RegistryCompany, page: int = 0) -> None:
        self.registry.searches_by[(keyword, SIC, page)] = tuple(hits)

    def found(self, *companies: RegistryCompany) -> None:
        """Script the solicitors search's first page (and the other keywords' empty ones), each
        company's profile and empty officers."""
        self.search("solicitors", *companies)
        for keyword in KEYWORDS[1:]:
            self.registry.searches_by.setdefault((keyword, SIC, 0), ())
        for found in companies:
            self.registry.companies.setdefault(found.number, found)
            self.registry.officer_lists.setdefault(found.number, ())

    def site(self, domain: str, *, name: str = REGISTERED, postcode: str = POSTCODE) -> None:
        """Script the Places lookup's first candidate at ``https://www.<domain>/`` and its
        robots.txt and homepage."""
        origin = f"https://www.{domain}"
        self.places.pages[(name, postcode, None)] = PlaceSearchPage(
            (PlaceCandidate("ChIJ-maps", f"{origin}/?utm=maps", "Maps Name", 53.8, -1.5),), None
        )
        self.crawler.robots[f"{origin}/robots.txt"] = robots_txt(f"{origin}/robots.txt", ALLOW_ALL)
        self.crawler.pages[f"{origin}/"] = html_page(f"{origin}/", "<title>Site Name</title>")

    def run(self, campaign: str | None = None) -> bool:
        with self.units.open():
            return self.runs.run(campaign or self.uk)

    def request(self, campaign: str | None = None) -> Enqueued:
        with self.units.open():
            return self.runs.request_run(campaign or self.uk)

    def run_rows(self) -> list[dict[str, Any]]:
        names = (
            "source campaign_id segment_id country_code location query_hash status started_at "
            "finished_at seen created merged suppressed no_website market_blocked inactive"
        ).split()
        rows = self.sql(f"SELECT {', '.join(names)} FROM discovery_runs ORDER BY rowid")
        return [dict(zip(names, row, strict=True)) for row in rows]

    def counts(self) -> dict[str, int]:
        [run] = self.run_rows()
        names = "seen created merged suppressed no_website market_blocked inactive".split()
        return {name: run[name] for name in names if run[name]}

    def status(self) -> str:
        return str(self.run_rows()[-1]["status"])

    def leads(self) -> list[dict[str, Any]]:
        names = (
            "id name web_domain ch_number country_code merged_into pipeline_stage "
            "legal_form company_status legal_form_status legal_form_site_evidence_id "
            "legal_form_registry_evidence_id website_lookup_at place_id place_lat"
        ).split()
        rows = self.sql(f"SELECT {', '.join(names)} FROM leads ORDER BY rowid")
        return [dict(zip(names, row, strict=True)) for row in rows]

    def jobs(self, job_type: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT idempotency_key, payload, lane, concurrency_key FROM jobs WHERE type = ? "
            "ORDER BY rowid",
            (job_type,),
        )

    def held(self, name: str = "Held Firm", **keys: Any) -> str:
        identity = LeadIdentity(name, self.segment(), country_code="GB", **keys)
        upserted = self.transact(
            lambda: self.discovery.upsert_lead(identity, LeadSource.SIGNAL_INBOX)
        )
        assert upserted.lead_id is not None
        return upserted.lead_id

    def handler(self) -> Callable[[Job], object]:
        return job_types_on(lambda: self.runs).get("discover_registry").handler


def job_types_on(registry_discovery: Callable[[], Any]) -> Any:
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
        registry_discovery=registry_discovery,
    )


def build_world(engine: Engine) -> World:
    """The shipped seed stored, ``law_private_ai_uk`` active."""
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
    registry = ScriptedRegistry()
    places = ScriptedPlaces()
    crawler = ScriptedCrawler()
    holder: list[RegistryDiscovery] = []
    queue = JobQueue(clock, job_types_on(lambda: holder[0]))
    discovery = Discovery(clock, Pipeline(clock), queue)
    research = Research(clock)
    runs = RegistryDiscovery(
        clock,
        meter,
        registry,
        PlaceSearches(clock, meter, places),
        crawler,
        discovery,
        research,
        queue,
    )
    holder.append(runs)
    world = World(
        engine, units, clock, meter, registry, places, crawler, queue, discovery, research, runs
    )
    world.campaigns = {str(key): str(id_) for id_, key in world.sql("SELECT id, key FROM campaigns")}
    with units.open():
        Campaigns(clock).activate(world.uk, "LIA-2026-01", "2026-11-16")
    return world


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def logged(caplog: pytest.LogCaptureFixture) -> str:
    return " ".join(
        record.getMessage() + repr(vars(record))
        for record in caplog.records
        if record.name.startswith(LOGGERS)
    )


# A new active company ---------------------------------------------------------------------------


def test_a_new_active_company_becomes_a_lead_with_its_registry_legal_form_and_website(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.found(company())
    world.site("acme-solicitors.co.uk")

    assert world.run() is True

    [lead] = world.leads()
    assert {key: lead[key] for key in lead if not key.startswith("legal_form_")} | {
        "id": None
    } == {
        "id": None,
        "name": REGISTERED,  # the registered name, never the Places or site name
        "web_domain": "acme-solicitors.co.uk",
        "ch_number": NUMBER,
        "country_code": "GB",
        "merged_into": None,
        "pipeline_stage": "new",
        "legal_form": "ltd",
        "company_status": "active",
        "website_lookup_at": STAMP,
        "place_id": None,  # nothing from the Places candidate is stored
        "place_lat": None,
    }
    assert lead["legal_form_status"] == "unconfirmed"  # FR-12: only the site confirms
    assert lead["legal_form_site_evidence_id"] is None
    [evidence] = world.sql(
        "SELECT id, source, quote, source_url FROM evidence WHERE lead_id = ?", (lead["id"],)
    )
    assert evidence[0] == lead["legal_form_registry_evidence_id"]
    assert evidence[1:] == (
        "registry",
        f"Companies House record {NUMBER}: company type ltd, company status active, "
        f"registered office postcode {POSTCODE}, accounts category full, SIC codes 69102, "
        f"registered name {REGISTERED}",
        f"https://find-and-update.company-information.service.gov.uk/company/{NUMBER}",
    )
    [(key, payload, lane, concurrency)] = world.jobs("enrich_lead")
    assert key == f"enrich_lead:{lead['id']}:2026-11-19"
    assert json.loads(payload) == {"v": 1, "lead_id": lead["id"]}
    assert concurrency == "domain:acme-solicitors.co.uk"
    assert world.places.calls == [(REGISTERED, POSTCODE, None)]
    assert world.crawler.urls("page") == ["https://www.acme-solicitors.co.uk/"]
    assert world.registry.operations() == [
        "search_registry",
        "get_company",
        "list_officers",
        "search_registry",
        "search_registry",
    ]
    [run] = world.run_rows()
    assert run == {
        "source": "companies_house",
        "campaign_id": world.uk,
        "segment_id": world.segment(),
        "country_code": "GB",
        "location": None,
        "query_hash": registry_query_hash(RegistryQueries(KEYWORDS, SIC)),
        "status": "finished",
        "started_at": STAMP,
        "finished_at": STAMP,
        "seen": 1,
        "created": 1,
        "merged": 0,
        "suppressed": 0,
        "no_website": 0,
        "market_blocked": 0,
        "inactive": 0,
    }
    [created] = world.sql("SELECT cause FROM events WHERE entity = 'leads'")
    assert created == ("created_from_companies_house",)
    shown = logged(caplog)
    for secret in ("ACME", "Acme", "LS1", "acme-solicitors", "solicitors'", "Site Name"):
        assert secret not in shown


def test_the_lead_s_legal_form_is_unconfirmed_until_the_site_confirms_it(world: World) -> None:
    world.found(company(postcode=None))

    world.run()

    [lead] = world.leads()
    assert (lead["legal_form"], lead["legal_form_status"]) == ("ltd", "unconfirmed")


def test_a_company_without_a_known_type_records_no_legal_form(world: World) -> None:
    world.found(company(company_type=None, postcode=None))

    world.run()

    [lead] = world.leads()
    assert (lead["legal_form"], lead["legal_form_status"]) == (None, None)
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(0,)]


# Officers ---------------------------------------------------------------------------------------


def test_only_active_natural_person_decision_makers_become_contacts_without_an_email(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    world.found(company(postcode=None))
    world.registry.officer_lists[NUMBER] = (
        officer("SMITH, Jane", "director", "Managing Partner"),
        officer("JONES, Ann", "director", "Managing Partner", resigned=True),
        officer("ACME HOLDINGS LIMITED", "corporate-director", "Managing Partner"),
        officer("BROWN, Tom", "director", "Accountant"),
        officer("GREEN, Amy", "director", None),
        officer("WHITE, Sam", "secretary", "Managing Partner"),
        officer("O'NEILL, Mary Ann", "llp-designated-member", "Owner"),
    )

    world.run()

    [lead] = world.leads()
    rows = world.sql(
        "SELECT c.name, c.role, c.email, p.source_kind, p.source_detail, p.retrieved_at "
        "FROM contacts c JOIN provenance_records p ON p.contact_id = c.id "
        "WHERE c.lead_id = ? ORDER BY c.rowid",
        (lead["id"],),
    )
    assert rows == [
        ("Jane Smith", "Managing Partner", None, "registry", DETAIL, STAMP),
        ("Mary Ann O'Neill", "Owner", None, "registry", DETAIL, STAMP),
    ]
    shown = logged(caplog)
    for secret in ("Jane", "SMITH", "Mary", "Managing Partner"):
        assert secret not in shown


def test_officers_are_not_fetched_for_a_matched_or_inactive_company(world: World) -> None:
    world.held(ch_number=NUMBER)
    world.found(company(), company("07654321", "OLD CO LIMITED", status="dissolved"))

    world.run()

    assert "list_officers" not in world.registry.operations()


# A number already held, inactive companies, the gate --------------------------------------------


@pytest.mark.parametrize("merged", [False, True])
def test_a_number_already_held_fetches_nothing_and_counts_merged(
    world: World, merged: bool
) -> None:
    holder = world.held(ch_number=NUMBER)
    if merged:  # the survivor holds another number, so the merged Lead keeps this one
        survivor = world.held("Older Firm", ch_number="09999999")
        world.sql(
            "UPDATE leads SET created_at = '2026-01-01T00:00:00.000000Z' WHERE id = ?",
            (survivor,),
        )
        world.transact(lambda: world.discovery.merge(survivor, holder))
        assert world.sql("SELECT merged_into, ch_number FROM leads WHERE id = ?", (holder,)) == [
            (survivor, NUMBER)
        ]
    world.found(company())

    world.run()

    assert world.registry.operations().count("get_company") == 0
    assert world.counts() == {"seen": 1, "merged": 1}
    assert len(world.leads()) == (2 if merged else 1)


@pytest.mark.parametrize(
    "found",
    [company(status="dissolved"), company(accounts="dormant"), company(status=None)],
    ids=["dissolved", "dormant", "unknown-status"],
)
def test_a_dissolved_or_dormant_company_is_counted_inactive(
    world: World, found: RegistryCompany
) -> None:
    world.found(found)

    world.run()

    assert world.leads() == []
    assert world.counts() == {"seen": 1, "inactive": 1}
    assert world.places.calls == []


def test_a_company_the_register_no_longer_has_is_seen_only(world: World) -> None:
    world.found(company())
    world.registry.companies[NUMBER] = None

    world.run()

    assert world.leads() == []
    assert world.counts() == {"seen": 1}


def test_the_gate_blocking_writes_nothing_and_counts_suppressed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []

    def blocked(subject: object, channel: object, purpose: object) -> Blocked:
        seen.append(subject)
        return Blocked((BlockReason.LEAD_DOMAIN_SUPPRESSED,))

    monkeypatch.setattr(compliance_api, "assert_contactable", blocked)
    world.found(company())

    world.run()

    assert seen == [NewLeadSubject(None)]
    assert world.leads() == []
    assert world.counts() == {"seen": 1, "suppressed": 1}
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(0,)]
    assert world.places.calls == []


def test_gb_not_an_enabled_market_counts_market_blocked(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.found(company())
    # the UK Market disabled after the run's own check (which would stop the run)
    monkeypatch.setattr(registry_ingest, "_may_run", lambda campaign_id: True)
    world.sql("UPDATE markets SET enabled = 0 WHERE country_code = 'GB'")

    world.run()

    assert world.leads() == []
    assert world.counts() == {"seen": 1, "market_blocked": 1}


# The website ------------------------------------------------------------------------------------


def test_a_company_without_a_registered_office_postcode_has_no_lookup(world: World) -> None:
    world.found(company(postcode=None))

    world.run()

    [lead] = world.leads()
    assert (lead["web_domain"], lead["website_lookup_at"]) == (None, STAMP)
    assert world.places.calls == []
    assert world.counts() == {"seen": 1, "created": 1, "no_website": 1}
    assert world.jobs("enrich_lead") == []


@pytest.mark.parametrize(
    "candidates",
    [
        (),
        (PlaceCandidate("ChIJ-x", None, "Maps", None, None),),
        (PlaceCandidate("ChIJ-x", "https://www.linkedin.com/company/acme", "Maps", None, None),),
        (PlaceCandidate("ChIJ-x", "https://www.yell.com/biz/acme", "Maps", None, None),),
    ],
    ids=["no-candidate", "no-website", "denylisted", "shared-platform"],
)
def test_no_usable_website_leaves_the_lead_without_one(
    world: World, candidates: tuple[PlaceCandidate, ...]
) -> None:
    world.found(company())
    world.places.pages[(REGISTERED, POSTCODE, None)] = PlaceSearchPage(candidates, None)

    world.run()

    [lead] = world.leads()
    assert (lead["web_domain"], lead["website_lookup_at"]) == (None, STAMP)
    assert world.crawler.calls == []  # a refused website is never fetched
    assert world.counts() == {"seen": 1, "created": 1, "no_website": 1}
    assert world.jobs("enrich_lead") == []


def test_only_the_first_candidate_s_website_is_used(world: World) -> None:
    world.found(company())
    world.site("second.co.uk")
    world.places.pages[(REGISTERED, POSTCODE, None)] = PlaceSearchPage(
        (
            PlaceCandidate("ChIJ-1", None, "Maps", None, None),
            PlaceCandidate("ChIJ-2", "https://www.second.co.uk/", "Maps", None, None),
        ),
        None,
    )

    world.run()

    assert world.leads()[0]["web_domain"] is None
    assert world.crawler.calls == []


def test_a_website_that_fails_to_load_leaves_the_lead_without_one(world: World) -> None:
    world.found(company())
    world.site("acme-solicitors.co.uk")
    world.crawler.pages["https://www.acme-solicitors.co.uk/"] = AdapterError(
        True, "fetch_failed", "the fetch failed"
    )

    world.run()

    [lead] = world.leads()
    assert (lead["web_domain"], lead["website_lookup_at"]) == (None, STAMP)
    assert world.counts() == {"seen": 1, "created": 1, "no_website": 1}


def test_a_website_another_lead_holds_is_not_attached(world: World) -> None:
    world.held(web_domain="acme-solicitors.co.uk")
    world.found(company())
    world.site("acme-solicitors.co.uk")

    world.run()

    [_, lead] = world.leads()
    assert (lead["web_domain"], lead["website_lookup_at"], lead["merged_into"]) == (
        None,
        STAMP,
        None,
    )
    assert world.counts() == {"seen": 1, "created": 1, "no_website": 1}
    assert world.jobs("enrich_lead") == []


def test_the_places_quota_refusing_leaves_the_lookup_undecided_and_the_run_going(
    world: World,
) -> None:
    world.set(Key.QUOTA_PLACES_ENTERPRISE, 0)
    world.found(company(), company("07654321", "BETA LAW LLP", company_type="llp"))

    assert world.run() is True

    leads = world.leads()
    assert [(lead["web_domain"], lead["website_lookup_at"]) for lead in leads] == [
        (None, None),
        (None, None),
    ]
    assert world.places.calls == []
    assert world.status() == "finished"
    assert world.counts() == {"seen": 2, "created": 2, "no_website": 2}


def test_a_places_error_leaves_the_lookup_undecided(world: World) -> None:
    world.found(company())
    world.places.pages[(REGISTERED, POSTCODE, None)] = AdapterError(
        True, "places_unavailable", "Places failed"
    )

    assert world.run() is True

    assert world.leads()[0]["website_lookup_at"] is None
    assert world.status() == "finished"


# Pages, budget, rate limit, failures ------------------------------------------------------------


def full_page(start: int, **changes: Any) -> tuple[RegistryCompany, ...]:
    return tuple(
        company(f"{number:08d}", f"FIRM {number} LIMITED", postcode=None, **changes)
        for number in range(start, start + 20)
    )


def test_the_next_page_is_fetched_only_after_a_full_page_and_the_cap_holds(
    world: World,
) -> None:
    world.set(Key.REGISTRY_MAX_PAGES_PER_RUN, 2)
    world.found(*full_page(100, status="dissolved"))
    world.search("solicitors", company(postcode=None), page=1)
    world.registry.companies[NUMBER] = company(postcode=None)
    world.registry.officer_lists[NUMBER] = ()

    world.run()

    searches = [call.key for call in world.registry.calls if call.operation == "search_registry"]
    assert searches == [("solicitors", SIC, 0), ("solicitors", SIC, 1)]
    assert world.counts() == {"seen": 21, "created": 1, "inactive": 20, "no_website": 1}


def test_a_short_page_moves_on_to_the_next_keyword(world: World) -> None:
    world.found(company(postcode=None))

    world.run()

    searches = [call.key for call in world.registry.calls if call.operation == "search_registry"]
    assert searches == [(keyword, SIC, 0) for keyword in KEYWORDS]


def test_the_budget_refusing_page_2_keeps_page_1_and_defers(world: World) -> None:
    def first_page() -> tuple[RegistryCompany, ...]:
        spent = AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
        world.meter.record(spent)  # the month's whole AI budget, spent during page 1
        return (company(postcode=None), *full_page(100, status="dissolved")[1:])

    world.found(*full_page(100, status="dissolved"))
    world.registry.searches_by[("solicitors", SIC, 0)] = first_page
    world.registry.companies[NUMBER] = company(postcode=None)
    world.registry.officer_lists[NUMBER] = ()

    job = Job(
        "01JD0000000000000000000001",
        "discover_registry",
        Lane.BULK,
        {"v": 1, "campaign_id": world.uk},
        1,
    )
    with world.units.open():
        handled = world.handler()(job)

    assert handled is DEFERRED
    assert [lead["ch_number"] for lead in world.leads()] == [NUMBER]
    assert world.status() == "deferred"
    searches = [call.key for call in world.registry.calls if call.operation == "search_registry"]
    assert searches == [("solicitors", SIC, 0)]


def test_a_429_mid_run_defers_and_keeps_what_was_created(world: World) -> None:
    second = company("07654321", "BETA LAW LLP", postcode=None)
    world.found(company(postcode=None), second)
    world.registry.companies[second.number] = AdapterError(
        True, "registry_rate_limited", "Companies House answered with HTTP 429"
    )

    assert world.run() is False

    assert [lead["ch_number"] for lead in world.leads()] == [NUMBER]
    assert world.status() == "deferred"


def test_another_retryable_error_fails_the_run_and_is_raised(world: World) -> None:
    world.search("solicitors")
    world.registry.searches_by[("solicitors", SIC, 0)] = AdapterError(
        True, "registry_unavailable", "Companies House answered with HTTP 503"
    )

    with pytest.raises(AdapterError, match="503"):
        world.run()

    assert world.status() == "failed"


def test_a_non_retryable_profile_error_counts_the_hit_as_seen_only(world: World) -> None:
    world.found(company())
    world.registry.companies[NUMBER] = AdapterError(
        False, "registry_bad_response", "a bad profile"
    )

    assert world.run() is True

    assert world.leads() == []
    assert world.counts() == {"seen": 1}


def test_a_stale_running_row_of_this_source_is_failed_but_a_places_row_is_kept(
    world: World,
) -> None:
    segment = world.segment()
    for source, location in (("companies_house", None), ("places", "Leeds")):
        world.sql(
            "INSERT INTO discovery_runs (id, source, campaign_id, segment_id, country_code, "
            "location, query_hash, status, started_at, seen, created, merged, suppressed, "
            "no_website, market_blocked, inactive) VALUES (?, ?, ?, ?, 'GB', ?, ?, 'running', "
            "?, 0, 0, 0, 0, 0, 0, 0)",
            (
                f"01JD000000000000000000000{len(source) % 10}",
                source,
                world.uk,
                segment,
                location,
                "0" * 16,
                STAMP,
            ),
        )
    world.found()

    world.run()

    statuses = [(run["source"], run["status"]) for run in world.run_rows()]
    assert statuses == [
        ("companies_house", "failed"),
        ("places", "running"),
        ("companies_house", "finished"),
    ]


# Stopped runs and requests ----------------------------------------------------------------------


def test_a_paused_campaign_s_run_is_stopped_with_nothing_fetched(world: World) -> None:
    with world.units.open():
        Campaigns(world.clock).pause(world.uk)

    assert world.run() is True

    assert world.status() == "stopped"
    assert world.registry.calls == []


def test_a_non_uk_campaign_s_run_is_stopped(world: World) -> None:
    with world.units.open():
        Campaigns(world.clock).activate(world.us)

    world.run(world.us)

    assert world.status() == "stopped"
    assert world.registry.calls == []


def test_an_unknown_campaign_is_skipped_without_a_row(world: World) -> None:
    assert world.run("01JD0000000000000000000000") is True

    assert world.run_rows() == []


def test_a_request_queues_one_bulk_job_and_a_double_submit_returns_it(world: World) -> None:
    first = world.request()
    again = world.request()

    assert first.created and not again.created and first.job_id == again.job_id
    [(key, payload, lane, concurrency)] = world.jobs("discover_registry")
    hashed = registry_query_hash(RegistryQueries(KEYWORDS, SIC))
    assert key == f"discover_registry:{world.uk}:{hashed}"
    assert json.loads(payload) == {"v": 1, "campaign_id": world.uk}
    assert (lane, concurrency) == ("bulk", "domain:api.company-information.service.gov.uk")


@pytest.mark.parametrize(
    ("campaign", "reason"),
    [
        ("law_private_ai_us", REFUSED_NOT_UK),
        ("ops_automation_uk", REFUSED_CAMPAIGN),  # a draft Campaign
        ("01JD0000000000000000000000", REFUSED_CAMPAIGN),
    ],
)
def test_a_non_uk_or_inactive_campaign_is_refused_and_nothing_queued(
    world: World, campaign: str, reason: str
) -> None:
    with world.units.open():
        Campaigns(world.clock).activate(world.us)

    with pytest.raises(RegistryRunRefused) as refused:
        world.request(world.campaigns.get(campaign, campaign))

    assert refused.value.reason == reason
    assert refused.value.message.endswith("Nothing was queued.")
    assert world.jobs("discover_registry") == []


# The Operator's website -------------------------------------------------------------------------


def registry_lead(world: World, number: str = NUMBER, name: str = "Beta Law LLP") -> str:
    identity = LeadIdentity(name, world.segment(), ch_number=number, country_code="GB")
    upserted = world.transact(
        lambda: world.discovery.upsert_lead(identity, LeadSource.COMPANIES_HOUSE)
    )
    assert upserted.lead_id is not None
    return upserted.lead_id


def enter(world: World, lead_id: str, url: str) -> Any:
    with world.units.open():
        return world.discovery.enter_website(lead_id, url)


def test_an_entered_website_is_saved_and_queued_for_research(world: World) -> None:
    lead_id = registry_lead(world)

    entered = enter(world, lead_id, "www.beta-law.co.uk/contact")

    assert (entered.lead_id, entered.merged) == (lead_id, False)
    assert world.sql("SELECT web_domain FROM leads WHERE id = ?", (lead_id,)) == [
        ("beta-law.co.uk",)
    ]
    [(key, _, _, concurrency)] = world.jobs("enrich_lead")
    assert key.startswith(f"enrich_lead:{lead_id}:")
    assert concurrency == "domain:beta-law.co.uk"


def test_an_entered_website_an_older_lead_holds_merges_into_it(world: World) -> None:
    older = world.held(web_domain="beta-law.co.uk")
    world.clock.advance(timedelta(seconds=60))
    lead_id = registry_lead(world)

    entered = enter(world, lead_id, "https://beta-law.co.uk/")

    assert (entered.lead_id, entered.merged) == (older, True)
    rows = dict(world.sql("SELECT id, merged_into FROM leads"))
    assert rows == {older: None, lead_id: older}
    assert world.sql("SELECT ch_number FROM leads WHERE id = ?", (older,)) == [(NUMBER,)]
    [(key, _, _, _)] = world.jobs("enrich_lead")
    assert key.startswith(f"enrich_lead:{older}:")


def test_an_address_without_a_scheme_but_with_a_url_in_its_query_is_read_as_https(
    world: World,
) -> None:
    lead_id = registry_lead(world)

    entered = enter(world, lead_id, "beta-law.co.uk/?r=https://x.example")

    assert (entered.lead_id, entered.merged) == (lead_id, False)
    assert world.sql("SELECT web_domain FROM leads WHERE id = ?", (lead_id,)) == [
        ("beta-law.co.uk",)
    ]


def test_an_entered_website_a_newer_lead_holds_merges_it_into_the_registry_lead(
    world: World,
) -> None:
    lead_id = registry_lead(world)
    world.clock.advance(timedelta(seconds=60))
    newer = world.held(web_domain="beta-law.co.uk")

    entered = enter(world, lead_id, "https://beta-law.co.uk/")

    assert (entered.lead_id, entered.merged) == (lead_id, True)
    rows = dict(world.sql("SELECT id, merged_into FROM leads"))
    assert rows == {lead_id: None, newer: lead_id}
    assert world.sql("SELECT web_domain, ch_number FROM leads WHERE id = ?", (lead_id,)) == [
        ("beta-law.co.uk", NUMBER)
    ]
    [(key, _, _, concurrency)] = world.jobs("enrich_lead")
    assert key.startswith(f"enrich_lead:{lead_id}:")
    assert concurrency == "domain:beta-law.co.uk"


def test_a_website_held_by_a_lead_merged_into_this_lead_is_refused(world: World) -> None:
    # Normally a merge moves the loser's domain to a survivor without one; this state (the
    # survivor without a domain, its loser still holding one) is built directly.
    lead_id = registry_lead(world)
    world.clock.advance(timedelta(seconds=60))
    loser = world.held(web_domain="beta-law.co.uk")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead_id, loser))

    with pytest.raises(WebsiteRefused) as refused:
        enter(world, lead_id, "https://beta-law.co.uk/")

    assert refused.value.reason == REFUSED_HAS_WEBSITE
    assert world.sql("SELECT id, web_domain, merged_into FROM leads ORDER BY rowid") == [
        (lead_id, None, None),
        (loser, "beta-law.co.uk", lead_id),
    ]
    assert world.jobs("enrich_lead") == []


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://www.linkedin.com/company/beta", REFUSED_URL),
        ("not a website", REFUSED_URL),
        ("http://10.0.0.1/", REFUSED_URL),
        ("https://www.yell.com/biz/beta", REFUSED_SHARED),
    ],
)
def test_a_refused_website_writes_nothing(world: World, url: str, reason: str) -> None:
    lead_id = registry_lead(world)

    with pytest.raises(WebsiteRefused) as refused:
        enter(world, lead_id, url)

    assert refused.value.reason == reason
    assert world.sql("SELECT web_domain FROM leads WHERE id = ?", (lead_id,)) == [(None,)]
    assert world.jobs("enrich_lead") == []


def test_a_suppressed_website_is_refused(world: World) -> None:
    lead_id = registry_lead(world)
    world.transact(
        lambda: Compliance(world.clock).suppress(
            DomainSubject("beta-law.co.uk"),
            SuppressionScope.FIRM,
            SuppressionSource.OPERATOR,
            None,
        )
    )

    with pytest.raises(WebsiteRefused) as refused:
        enter(world, lead_id, "https://beta-law.co.uk/")

    assert refused.value.reason == REFUSED_BLOCKED
    assert world.sql("SELECT web_domain FROM leads WHERE id = ?", (lead_id,)) == [(None,)]


def test_a_lead_with_a_website_or_unknown_is_refused(world: World) -> None:
    with_site = world.held(web_domain="gamma.co.uk")

    with pytest.raises(WebsiteRefused) as has_site:
        enter(world, with_site, "https://delta.co.uk/")
    with pytest.raises(WebsiteRefused) as unknown:
        enter(world, "01JD0000000000000000000000", "https://delta.co.uk/")

    assert (has_site.value.reason, unknown.value.reason) == (REFUSED_HAS_WEBSITE, REFUSED_LEAD)
    assert world.sql("SELECT web_domain FROM leads") == [("gamma.co.uk",)]


def test_the_website_needed_list_holds_only_leads_without_a_website(world: World) -> None:
    from nextron.app.queries import discovery as discovery_queries  # noqa: PLC0415

    needed = registry_lead(world)
    world.held(web_domain="gamma.co.uk")

    with world.units.open(read_only=True):
        found = discovery_queries.lead_list(website_needed=True)
        everything = discovery_queries.lead_list()

    assert [lead.id for lead in found.leads] == [needed]
    assert found.total == 1 and everything.total == 2


# check_legal_form keeps the registry-only finding -----------------------------------------------


def test_check_legal_form_without_a_link_keeps_the_registry_only_finding(world: World) -> None:
    world.found(company(postcode=None))
    world.run()
    [lead] = world.leads()
    checks = LegalFormChecks(
        world.clock, world.meter, world.registry, world.discovery, world.research, world.queue
    )

    with world.units.open():
        assert checks.check_legal_form(lead["id"]) is True

    [after] = world.leads()
    assert after["legal_form_registry_evidence_id"] == lead["legal_form_registry_evidence_id"]
    assert (after["legal_form"], after["legal_form_status"]) == ("ltd", "unconfirmed")
    assert world.sql("SELECT COUNT(*) FROM evidence WHERE retracted_at IS NULL") == [(1,)]


# Wiring -----------------------------------------------------------------------------------------


def test_build_app_shares_one_registry_between_the_legal_form_check_and_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "test")
    monkeypatch.setenv("NEXTRON_DATA_DIR", str(tmp_path / "data"))
    checks: list[LegalFormChecks] = []
    registries: list[dict[str, Any]] = []
    real_checks, real_build_job_types = main.LegalFormChecks, main.build_job_types

    def legal_form_checks(*args: Any) -> LegalFormChecks:
        checks.append(real_checks(*args))
        return checks[-1]

    def recording_job_types(**kwargs: Any) -> Any:
        registries.append(kwargs)
        return real_build_job_types(**kwargs)

    monkeypatch.setattr(main, "KeyringSecrets", lambda env: InMemorySecrets(env))
    monkeypatch.setattr(main, "LegalFormChecks", legal_form_checks)
    monkeypatch.setattr(main, "build_job_types", recording_job_types)
    registry = ScriptedRegistry()
    crawler = ScriptedCrawler()

    app = main.build_app(load_bootstrap(), units=FakeUnits(), crawler=crawler, registry=registry)

    built = app.state.registry_discovery
    [given] = registries
    assert given["registry_discovery"]() is built
    assert built._registry is registry  # noqa: SLF001 - one rate window
    assert checks[0]._registry is registry  # noqa: SLF001
    assert built._crawler is crawler  # noqa: SLF001
    assert built._searches is app.state.place_searches  # noqa: SLF001
