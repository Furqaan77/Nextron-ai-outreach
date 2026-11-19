"""Story 8.1: supply jobs inside the quotas and the AI budget (FR-57, AD-9, AD-10, NFR-11) on a
temporary database at head: every row of the spec's matrix (paging at the quota's edge, two
concurrent calls at 999 of 1,000, a run refused at its start for Places and Companies House, the
Pro and aggregator quotas at 0, 50 deferrals raising one notice), the Quotas page's "Non-essential
work stopped" line, the health panel's "Discovery stopped" issues and its Discovery and Re-check
groups, and ``JobType``'s ``budget_class`` field. No request leaves the machine."""

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Final

import httpx2
import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.adapters.places import GooglePlaces
from nextron.app.discovery import places as places_module
from nextron.app.discovery.places import PlaceSearches
from nextron.app.notify.api import Notify
from nextron.app.ops.api import Budget
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Campaigns
from nextron.db.metering import UsageMeter
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import budget
from nextron.domain.alert import Channel
from nextron.domain.budget import (
    QUOTAS,
    AiCall,
    ApiCall,
    BudgetClass,
    Limits,
    Usage,
    allow,
    assess,
)
from nextron.domain.health import HealthIssue, LastRun, job_group_runs
from nextron.domain.job import DEFERRED, Job, JobGroup, JobType, Lane
from nextron.domain.settings_schema import Key
from nextron.ports.places import PlaceSearchPage
from nextron.web import quotas_page
from tests.app import test_place_discovery as places_world
from tests.app import test_registry_discovery as registry_world
from tests.fakes.alert import InMemoryAlert
from tests.fakes.clock import FrozenClock
from tests.fakes.places import ScriptedPlaces
from tests.fakes.secrets import InMemorySecrets

T0: Final = places_world.T0
LAW: Final = places_world.LAW
LOCATION: Final = places_world.LOCATION
SECRET_NAME: Final = "google_places/api_key"
KEY: Final = "Gp4Yt8Rm2Kx6Wq1Zv9Ld"
BODY: Final = {
    "places": [{"id": "ChIJ-first", "websiteUri": "https://smith-cpa.example.com/"}],
    "nextPageToken": "Nt3Pq7Xs1Wk5",
}
WHOLE_BUDGET: Final = 20_000_000  # the default $20 AI budget, in micro-dollars


def spent(micros: int = WHOLE_BUDGET) -> AiCall:
    return AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=micros)


def enterprise(count: int = 1) -> ApiCall:
    return ApiCall("places", "text_search", ok=True, sku="enterprise", count=count)


def places_job(world: places_world.World) -> Job:
    world.request()
    return places_world.stored_job(world)


def handle(world: places_world.World, job: Job) -> object:
    with world.units.open():
        return world.handler()(job)


@pytest.fixture
def places(db_engine: Engine) -> places_world.World:
    return places_world.build_world(db_engine)


@pytest.fixture
def registry(db_engine: Engine) -> registry_world.World:
    return registry_world.build_world(db_engine)


# JobType's budget class ---------------------------------------------------------------------------


def job_type(**changes: Any) -> JobType:
    fields: dict[str, Any] = {
        "name": "planted_job",
        "default_lane": Lane.BULK,
        "lanes": frozenset({Lane.BULK}),
        "priority": 10,
        "max_runtime": timedelta(minutes=1),
        "retry_limit": 0,
        "handler": lambda job: None,
    }
    return JobType(**(fields | changes))


def test_a_job_type_built_without_a_budget_class_is_essential() -> None:
    assert job_type().budget_class is BudgetClass.ESSENTIAL
    assert job_type(budget_class=BudgetClass.BLOCKABLE).budget_class is BudgetClass.BLOCKABLE


@pytest.mark.parametrize("value", ["blockable", None, 1, True])
def test_a_budget_class_that_is_not_a_budget_class_is_refused(value: object) -> None:
    with pytest.raises(TypeError, match="planted_job: budget_class must be a BudgetClass"):
        job_type(budget_class=value)


def test_budget_class_is_still_importable_from_the_budget_module() -> None:
    from nextron.domain.budget_class import BudgetClass as Moved  # noqa: PLC0415

    assert budget.BudgetClass is Moved is BudgetClass


# Paging at the quota's edge ---------------------------------------------------------------------


def test_at_999_of_1000_one_page_is_fetched_then_the_run_defers(
    places: places_world.World,
) -> None:
    places.set(Key.QUOTA_PLACES_ENTERPRISE, 1000)
    places.meter.record(enterprise(999))

    def metered(page: PlaceSearchPage) -> Callable[[], PlaceSearchPage]:
        def answer() -> PlaceSearchPage:  # what the real adapter meters for its request
            places.meter.record(enterprise())
            return page

        return answer

    places.places.pages[(LAW, LOCATION, None)] = metered(places_world.page(token="t2"))
    places.places.pages[(LAW, LOCATION, "t2")] = metered(places_world.page(token="t3"))
    places.places.pages[(LAW, LOCATION, "t3")] = metered(places_world.page())
    job = places_job(places)

    assert handle(places, job) is DEFERRED

    assert places.places.calls == [(LAW, LOCATION, None)]  # page 2 was never requested
    assert places.meter.snapshot(T0).units("places", "enterprise") == 1000
    [run] = places.runs()
    assert (run["status"], run["finished_at"]) == ("deferred", places_world.STAMP)


# Two calls at once --------------------------------------------------------------------------------


class SpyLock:
    """``PLACES_LOCK`` with a flag the named thread sets right before it tries to acquire it."""

    def __init__(self, real: Any, thread_name: str) -> None:
        self.real = real
        self.thread_name = thread_name
        self.attempted = threading.Event()

    def __enter__(self) -> bool:
        if threading.current_thread().name == self.thread_name:
            self.attempted.set()
        return bool(self.real.acquire())

    def __exit__(self, *exc: object) -> None:
        self.real.release()


def test_two_concurrent_calls_at_999_of_1000_make_one_request(
    units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FrozenClock(T0)
    meter = UsageMeter(units, clock)
    meter.record(enterprise(999))
    entered = threading.Event()
    release = threading.Event()
    requests: list[httpx2.Request] = []
    spy = SpyLock(places_module.PLACES_LOCK, "second")
    monkeypatch.setattr(places_module, "PLACES_LOCK", spy)

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        entered.set()
        assert release.wait(30)  # the slow transport: held until the second call is blocked
        return httpx2.Response(200, json=BODY)

    secrets = InMemorySecrets()
    secrets.write(SECRET_NAME, KEY)
    google = GooglePlaces(
        secrets, meter=meter, http=httpx2.Client(transport=httpx2.MockTransport(answer))
    )
    searches = PlaceSearches(clock, meter, google)
    results: dict[str, object] = {}

    def call(name: str) -> None:
        with units.open():
            results[name] = searches.search_page(LAW, LOCATION)

    first = threading.Thread(target=call, args=("first",), name="first")
    second = threading.Thread(target=call, args=("second",), name="second")
    first.start()
    assert entered.wait(30)
    second.start()
    try:
        # the second call has reached PLACES_LOCK while the first still holds it mid-request
        assert spy.attempted.wait(30)
        assert spy.real.locked() and len(requests) == 1
    finally:
        release.set()
        first.join(30)
        second.join(30)

    assert not first.is_alive() and not second.is_alive()
    assert len(requests) == 1
    assert results["first"] is not None and results["second"] is None
    with units.open(read_only=True):
        assert meter.snapshot(T0).units("places", "enterprise") == 1000


def test_page_refuses_to_run_inside_a_transaction(places: places_world.World) -> None:
    searches = PlaceSearches(places.clock, places.meter, ScriptedPlaces())

    @use_case
    def inside() -> None:
        with current().transaction():
            searches.page(LAW, LOCATION)

    with places.units.open(), pytest.raises(RuntimeError, match="no transaction open"):
        inside()


# Refused at the start ---------------------------------------------------------------------------


@pytest.mark.parametrize("refusal", ["ai_budget", "quota"])
def test_a_places_run_refused_at_its_start_writes_no_row_and_defers(
    places: places_world.World, refusal: str
) -> None:
    if refusal == "ai_budget":
        places.meter.record(spent())
    else:
        places.meter.record(enterprise(1000))
    places.search(places_world.page())
    job = places_job(places)

    assert handle(places, job) is DEFERRED
    places.clock.advance(timedelta(hours=1))  # the runner's re-queue
    assert handle(places, job) is DEFERRED

    assert places.runs() == []
    assert places.places.calls == []


def test_a_registry_run_refused_at_its_start_writes_no_row_and_defers(
    registry: registry_world.World,
) -> None:
    registry.meter.record(spent())
    registry.found(registry_world.company())
    job = Job(
        "01JD0000000000000000000001",
        "discover_registry",
        Lane.BULK,
        {"v": 1, "campaign_id": registry.uk},
        1,
    )

    for _ in range(2):  # deferred, then re-queued an hour later and deferred again
        with registry.units.open():
            assert registry.handler()(job) is DEFERRED
        registry.clock.advance(timedelta(hours=1))

    assert registry.run_rows() == []
    assert registry.registry.calls == []


@pytest.mark.parametrize(
    ("source", "refusal"),
    [("places", "ai_budget"), ("places", "quota"), ("registry", "ai_budget")],
)
def test_a_paused_campaign_with_the_budget_refused_is_stopped_not_deferred(
    db_engine: Engine, source: str, refusal: str
) -> None:
    if source == "places":
        world = places_world.build_world(db_engine)
        world.meter.record(spent() if refusal == "ai_budget" else enterprise(1000))
        with world.units.open():
            Campaigns(world.clock).pause(world.us)

        assert world.run() is True

        [run] = world.runs()
        assert run["status"] == "stopped"
        assert world.places.calls == []
    else:
        uk = registry_world.build_world(db_engine)
        uk.meter.record(spent())
        with uk.units.open():
            Campaigns(uk.clock).pause(uk.uk)

        assert uk.run() is True

        [row] = uk.run_rows()
        assert row["status"] == "stopped"
        assert uk.registry.calls == []


# Pro and the aggregator at 0 --------------------------------------------------------------------


def test_a_pro_mask_search_at_quota_0_is_refused_before_the_adapter(
    places: places_world.World,
) -> None:
    scripted = ScriptedPlaces({(LAW, LOCATION, None): places_world.page()})
    pro = PlaceSearches(places.clock, places.meter, scripted, fields=("places.id",))
    with places.units.open():
        assert pro.search_page(LAW, LOCATION) is None
    assert scripted.calls == []


def limits(**quotas: int) -> Limits:
    return Limits({spec.name: quotas.get(spec.name, 1000) for spec in QUOTAS}, WHOLE_BUDGET, 80)


def test_the_aggregator_quota_at_0_refuses_blockable_work() -> None:
    usage = Usage("2026-11", (), 0, 0)
    refused = limits(job_aggregator=0)
    assert allow(BudgetClass.BLOCKABLE, usage, refused, quota="job_aggregator") is False
    assert allow(BudgetClass.ESSENTIAL, usage, refused, quota="job_aggregator") is True


# 50 deferrals, one notice -----------------------------------------------------------------------


def notify_on(world: places_world.World) -> Notify:
    return Notify(world.clock, world.queue, alert_ports={Channel.TELEGRAM: InMemoryAlert})


def test_fifty_deferrals_raise_one_quota_notice(places: places_world.World) -> None:
    checks = Budget(places.clock, places.meter, notify_on(places))
    places.meter.record(enterprise(1000))
    places.request()

    for _ in range(50):
        with places.units.open():  # each step in its own unit of work, as the runner does
            claim = places.queue.claim(Lane.BULK)
        assert claim is not None and claim.job.type == "discover_places"
        with places.units.open():
            assert places.handler()(claim.job) is DEFERRED
        with places.units.open():
            assert places.queue.defer(claim) is True
        with places.units.open():
            checks.check()
        places.clock.advance(timedelta(hours=1, seconds=1))

    notices = places.sql("SELECT kind, subject FROM notifications ORDER BY rowid")
    assert notices == [("quota_reached", "places_enterprise:2026-11")]
    assert places.runs() == []
    assert places.places.calls == []


# The Quotas page and the health panel -----------------------------------------------------------


def status_on(world: places_world.World) -> budget.Assessment:
    with world.units.open(read_only=True):
        return assess(world.meter.snapshot(T0), setup_queries.budget_limits())


def test_the_quotas_page_says_non_essential_work_stopped(places: places_world.World) -> None:
    places.meter.record(enterprise(1000))

    assert quotas_page.stopped_text(status_on(places)) == (
        "Non-essential work stopped: Places Enterprise quota reached"
    )


def assessment(usage: Usage, **quotas: int) -> budget.Assessment:
    return assess(usage, limits(**quotas))


def test_a_reached_discovery_quota_is_a_discovery_stopped_issue() -> None:
    used = budget.ApiUse("places", "text_search", "enterprise", 1, 0, 1000)
    usage = Usage("2026-11", (used,), 0, 0)

    assert discovery_queries.health_issues(assessment(usage)) == (
        HealthIssue(
            "discovery_stopped",
            "places_enterprise",
            "Discovery stopped: Places Enterprise quota reached",
        ),
    )


def test_the_ai_budget_reached_is_a_discovery_stopped_issue() -> None:
    usage = Usage("2026-11", (), 1, WHOLE_BUDGET)

    assert discovery_queries.health_issues(assessment(usage)) == (
        HealthIssue("discovery_stopped", "ai_budget", "Discovery stopped: AI budget reached"),
    )


def test_enrichment_quotas_off_quotas_and_ok_usage_are_no_discovery_issue() -> None:
    hunter = budget.ApiUse("hunter", "find", None, 500, 0, 1000)  # stops enrichment only
    usage = Usage("2026-11", (hunter,), 1, WHOLE_BUDGET - 1)

    assert discovery_queries.health_issues(assessment(usage, hunter=1000)) == ()
    assert discovery_queries.health_issues(assessment(usage, places_pro=0, job_aggregator=0)) == ()


def test_the_health_registry_reads_the_discovery_source_after_sending(
    places: places_world.World,
) -> None:
    places.meter.record(spent())
    issues = main.build_health_issues(
        budget=Budget(places.clock, places.meter, notify_on(places)),
        clock=places.clock,
    )

    names = [found.name for found in issues.sources]
    source = issues.sources[names.index("sending") + 1]
    with places.units.open(read_only=True):
        found = tuple(source.read())

    assert source.name == "discovery"
    assert found == (
        HealthIssue("discovery_stopped", "ai_budget", "Discovery stopped: AI budget reached"),
    )


def test_the_health_panel_lists_the_discovery_and_recheck_groups_last_runs(
    places: places_world.World,
) -> None:
    job_types = places_world.job_types_on(lambda: places.place_discovery)
    pairs = [(name, job_types.get(name).group) for name in job_types.names()]
    with places.units.open(read_only=True):
        last = jobqueue_queries.last_successes()

    groups = {runs.group: runs.runs for runs in job_group_runs(pairs, last)}

    assert LastRun("discover_places", None) in groups[JobGroup.DISCOVERY]
    assert LastRun("discover_registry", None) in groups[JobGroup.DISCOVERY]
    assert LastRun("recheck_event", None) in groups[JobGroup.RECHECK]
