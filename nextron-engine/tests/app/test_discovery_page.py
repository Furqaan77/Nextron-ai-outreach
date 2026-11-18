"""The Discovery page (Stories 8.3 and 8.4; FR-5, FR-6, UX-DR28, UX-DR29, AD-13) through
``Dashboard`` on a temporary database at head seeded with the shipped files: ``GET
/leads/discovery`` (the forms, the recent runs, ``Cache-Control: no-store``, the Leads list's
link), ``POST /leads/discovery/places`` and ``POST /leads/discovery/companies-house`` (CSRF, a
queued job, a double submit, refusals and malformed forms, nothing queued), and Story 8.4's
Leads list "Website needed" filter and ``POST /leads/{lead_id}/website``."""

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Final
from urllib.parse import urlencode

import pytest

from nextron.app.queries import discovery as discovery_queries
from nextron.app.setup.api import Campaigns, Setup
from nextron.db.repos.discovery import runs as runs_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.place_discovery import RunStatus, location_hash
from nextron.domain.settings_schema import Key
from nextron.web.csrf import SECURITY_CHECK_FAILED
from nextron.web.forms import MALFORMED_FORM
from tests.app.test_campaigns_panel import seed
from tests.asgi import Reply
from tests.dashboard import FORM, Dashboard
from tests.fakes.clock import FrozenClock

T0: Final = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
US_HASH: Final = location_hash("Columbus, OH", "US")
GB_HASH: Final = location_hash("Leeds", "GB")
QUEUED: Final = "Places discovery queued."
ALREADY: Final = "Places discovery is already queued for that Campaign and location."


class Page(HTMLParser):
    """The notices, the select options and the runs table's cells."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.notices: list[tuple[str, str]] = []
        self.options: dict[str, list[tuple[str, str, bool]]] = {}
        self.cells: list[list[str]] = []
        self.forms: list[dict[str, str | None]] = []
        self.links: list[str] = []
        self._select: str | None = None
        self._capture: str | None = None
        self._attrs: dict[str, str | None] = {}
        self._text: list[str] = []
        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "select":
            self._select = values.get("name")
            self.options[self._select or ""] = []
        elif tag == "form":
            self.forms.append(values)
        elif tag == "a":
            self.links.append(values.get("href") or "")
        elif tag == "tr":
            self.cells.append([])
        if tag in {"p", "option", "td"}:
            self._capture, self._attrs, self._text = tag, values, []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select = None
        if tag != self._capture:
            return
        text = " ".join("".join(self._text).split())
        if tag == "p" and self._attrs.get("role") in {"status", "alert"}:
            self.notices.append((str(self._attrs["role"]), text))
        elif tag == "option" and self._select is not None:
            self.options[self._select].append(
                (str(self._attrs.get("value")), text, "selected" in self._attrs)
            )
        elif tag == "td":
            self.cells[-1].append(text)
        self._capture = None


@pytest.fixture
def seeded(units: UnitOfWorkFactory) -> UnitOfWorkFactory:
    seed(units)
    clock = FrozenClock(T0)
    with units.open():
        Setup(clock).set(Key.PLACES_DISCOVERY_LOCATIONS, ("Columbus, OH | US", "Leeds | GB"))
    return units


@pytest.fixture
def ids(seeded: UnitOfWorkFactory, db_engine: Any) -> dict[str, str]:
    with db_engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT key, id FROM campaigns").all()
    return {str(key): str(found) for key, found in rows}


@pytest.fixture
def active(seeded: UnitOfWorkFactory, ids: dict[str, str]) -> str:
    with seeded.open():
        Campaigns(FrozenClock(T0)).activate(ids["law_private_ai_us"])
    return ids["law_private_ai_us"]


@pytest.fixture
def dashboard(seeded: UnitOfWorkFactory) -> Dashboard:
    return Dashboard(units=seeded, clock=FrozenClock(T0))


@pytest.fixture
def session(dashboard: Dashboard) -> tuple[bytes, str]:
    cookie = dashboard.open_session()
    return cookie, dashboard.csrf_token(cookie)


def post(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    fields: list[tuple[str, str]],
    *,
    token: bool = True,
) -> Reply:
    cookie, csrf = session
    return dashboard.post(
        "/leads/discovery/places",
        cookie=cookie,
        token=csrf if token else None,
        headers=[(b"content-type", FORM)],
        body=urlencode(fields).encode("ascii"),
    )


def jobs(db_engine: Any) -> list[tuple[Any, ...]]:
    with db_engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT id, idempotency_key, lane FROM jobs WHERE type = 'discover_places'"
            ).all()
        ]


def test_the_page_offers_active_campaigns_and_locations_and_is_not_stored(
    dashboard: Dashboard, session: tuple[bytes, str], active: str
) -> None:
    reply = dashboard.get("/leads/discovery", cookie=session[0])

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    found = Page(reply.text)
    assert found.options["campaign_id"] == [(active, "Law-PrivateAI × US", False)]
    assert found.options["location"] == [
        (US_HASH, "Columbus, OH (US)", False),
        (GB_HASH, "Leeds (GB)", False),
    ]
    [form] = found.forms
    assert (form["method"], form["hx-post"]) == ("post", "/leads/discovery/places")
    assert 'name="csrf_token"' in reply.text
    assert "No discovery has run yet." in reply.text


def test_without_an_active_campaign_or_a_location_the_page_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, active: str
) -> None:
    with seeded.open():
        Setup(FrozenClock(T0)).save({str(Key.PLACES_DISCOVERY_LOCATIONS): ""})
    no_location = dashboard.get("/leads/discovery", cookie=session[0])
    with seeded.open():
        Campaigns(FrozenClock(T0)).pause(active)
    no_campaign = dashboard.get("/leads/discovery", cookie=session[0])

    assert "No Places discovery location is set." in no_location.text
    assert "No Campaign is active." in no_campaign.text
    assert Page(no_campaign.text).forms == []


def test_recent_runs_show_source_campaign_location_status_and_counts(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, active: str
) -> None:
    segment = segment_of(seeded)

    @use_case
    def store() -> None:
        with current().transaction():
            run_id = runs_repo.insert(
                source="places",
                campaign_id=active,
                segment_id=segment,
                country_code="US",
                location="Columbus, OH",
                query_hash="0" * 16,
                status=RunStatus.RUNNING,
                started_at=T0,
                finished_at=None,
            )
            for outcome in ("created", "created", "merged", "no_website", None):
                runs_repo.count(run_id, outcome)  # type: ignore[arg-type]
            runs_repo.finish(run_id, RunStatus.DEFERRED, T0)

    with seeded.open():
        store()

    reply = dashboard.get("/leads/discovery", cookie=session[0])

    rows = [cells for cells in Page(reply.text).cells if cells]
    assert rows == [
        [
            "Google Places",
            "Law-PrivateAI × US",
            "Columbus, OH (US)",
            "deferred",
            "2026-11-18 09:00 UTC",
            "5",
            "2",
            "1",
            "0",
            "1",
            "0",
            "0",
        ]
    ]


def segment_of(units: UnitOfWorkFactory) -> str:
    from nextron.app.queries import setup as setup_queries  # noqa: PLC0415

    with units.open(read_only=True):
        found = setup_queries.segment("law_private_ai")
    assert found is not None
    return found.id


def test_the_leads_list_links_to_the_discovery_page(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/leads", cookie=session[0])

    assert "/leads/discovery" in Page(reply.text).links


def test_a_run_request_queues_one_job_and_a_double_submit_returns_it(
    dashboard: Dashboard, session: tuple[bytes, str], active: str, db_engine: Any
) -> None:
    fields = [("campaign_id", active), ("location", US_HASH)]

    first = post(dashboard, session, fields)
    again = post(dashboard, session, fields)

    assert first.status == again.status == 200
    assert Page(first.text).notices == [("status", QUEUED)]
    assert Page(again.text).notices == [("status", ALREADY)]
    assert 'id="discovery"' in first.text and "<html" not in first.text
    assert ("Columbus, OH (US)" in first.text) and Page(first.text).options["location"][0][2]
    [(_, key, lane)] = jobs(db_engine)
    assert (key, lane) == (f"discover_places:{active}:{US_HASH}", "bulk")


def test_the_form_s_own_csrf_field_is_accepted(
    dashboard: Dashboard, session: tuple[bytes, str], active: str, db_engine: Any
) -> None:
    fields = [("csrf_token", session[1]), ("campaign_id", active), ("location", US_HASH)]

    reply = post(dashboard, session, fields)

    assert Page(reply.text).notices == [("status", QUEUED)]
    assert len(jobs(db_engine)) == 1


@pytest.mark.parametrize(
    ("campaign", "location", "message"),
    [
        ("law_private_ai_uk", GB_HASH, "That Campaign isn't active"),  # draft
        ("law_private_ai_us", GB_HASH, "That location isn't one of the Campaign's Market's"),
        ("law_private_ai_us", "0" * 16, "That location isn't one of the Campaign's Market's"),
        ("01JD0000000000000000000000", US_HASH, "That Campaign isn't active"),
    ],
)
def test_a_refused_request_says_why_and_queues_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    active: str,
    ids: dict[str, str],
    db_engine: Any,
    campaign: str,
    location: str,
    message: str,
) -> None:
    reply = post(dashboard, session, [("campaign_id", ids.get(campaign, campaign)), ("location", location)])

    [(role, text)] = Page(reply.text).notices
    assert reply.status == 200
    assert role == "alert" and text.startswith(message) and text.endswith("Nothing was queued.")
    assert jobs(db_engine) == []


@pytest.mark.parametrize(
    "fields",
    [
        [("campaign_id", "{us}")],
        [("location", US_HASH)],
        [("campaign_id", "{us}"), ("location", US_HASH), ("extra", "1")],
        [("campaign_id", "{us}"), ("campaign_id", "{us}"), ("location", US_HASH)],
    ],
    ids=["no-location", "no-campaign", "extra-field", "repeated-field"],
)
def test_a_malformed_form_is_refused_with_400_and_queues_nothing(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    active: str,
    db_engine: Any,
    fields: list[tuple[str, str]],
) -> None:
    sent = [(name, active if value == "{us}" else value) for name, value in fields]

    reply = post(dashboard, session, sent)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert jobs(db_engine) == []


def test_a_post_without_the_csrf_token_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], active: str, db_engine: Any
) -> None:
    reply = post(dashboard, session, [("campaign_id", active), ("location", US_HASH)], token=False)

    assert reply.status == 403
    assert reply.text == SECURITY_CHECK_FAILED
    assert jobs(db_engine) == []


def test_the_page_needs_a_session(dashboard: Dashboard) -> None:
    reply = dashboard.get("/leads/discovery")

    assert reply.status == 401
    assert b"discovery" not in reply.body


def test_the_page_lists_the_newest_20_runs_newest_first(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, active: str
) -> None:
    segment = segment_of(seeded)

    @use_case
    def store() -> None:
        with current().transaction():
            for minute in range(21):
                runs_repo.insert(
                    source="places",
                    campaign_id=active,
                    segment_id=segment,
                    country_code="US",
                    location=f"Place {minute:02d}",
                    query_hash="0" * 16,
                    status=RunStatus.FINISHED,
                    started_at=T0 + timedelta(minutes=minute),
                    finished_at=T0 + timedelta(minutes=minute),
                )

    with seeded.open():
        store()

    reply = dashboard.get("/leads/discovery", cookie=session[0])

    rows = [cells for cells in Page(reply.text).cells if cells]
    assert [row[2] for row in rows] == [f"Place {minute:02d} (US)" for minute in range(20, 0, -1)]


@pytest.mark.parametrize("limit", [0, 201, True, "20"])
def test_recent_runs_takes_a_limit_from_1_to_200(
    seeded: UnitOfWorkFactory, limit: object
) -> None:
    with seeded.open(read_only=True), pytest.raises(ValueError, match="limit"):
        discovery_queries.recent_runs(limit)  # type: ignore[arg-type]


# Story 8.4: Companies House discovery ------------------------------------------------------------

REGISTRY_QUEUED: Final = "Companies House discovery queued."
REGISTRY_ALREADY: Final = "Companies House discovery is already queued for that Campaign."


@pytest.fixture
def uk(seeded: UnitOfWorkFactory, ids: dict[str, str]) -> str:
    with seeded.open():
        Campaigns(FrozenClock(T0)).activate(ids["law_private_ai_uk"], "LIA-2026-01", "2026-11-16")
    return ids["law_private_ai_uk"]


def post_registry(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    fields: list[tuple[str, str]],
    *,
    token: bool = True,
) -> Reply:
    cookie, csrf = session
    return dashboard.post(
        "/leads/discovery/companies-house",
        cookie=cookie,
        token=csrf if token else None,
        headers=[(b"content-type", FORM)],
        body=urlencode(fields).encode("ascii"),
    )


def registry_jobs(db_engine: Any) -> list[tuple[Any, ...]]:
    with db_engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT idempotency_key, lane FROM jobs WHERE type = 'discover_registry'"
            ).all()
        ]


def test_the_page_offers_companies_house_discovery_for_active_uk_campaigns_only(
    dashboard: Dashboard, session: tuple[bytes, str], active: str, uk: str
) -> None:
    reply = dashboard.get("/leads/discovery", cookie=session[0])

    found = Page(reply.text)
    places_form, registry_form = found.forms
    assert (registry_form["method"], registry_form["hx-post"]) == (
        "post",
        "/leads/discovery/companies-house",
    )
    assert places_form["hx-post"] == "/leads/discovery/places"
    # the parser keeps the last select of a name: the Companies House one
    assert found.options["campaign_id"] == [(uk, "Law-PrivateAI × UK", False)]
    assert reply.text.count('name="csrf_token"') == 2


def test_without_an_active_uk_campaign_the_page_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], active: str
) -> None:
    reply = dashboard.get("/leads/discovery", cookie=session[0])

    assert "No active UK Campaign." in reply.text
    assert [form["hx-post"] for form in Page(reply.text).forms] == ["/leads/discovery/places"]


def test_a_companies_house_request_queues_one_job_and_a_double_submit_returns_it(
    dashboard: Dashboard, session: tuple[bytes, str], uk: str, db_engine: Any
) -> None:
    first = post_registry(dashboard, session, [("campaign_id", uk)])
    again = post_registry(dashboard, session, [("csrf_token", session[1]), ("campaign_id", uk)])

    assert first.status == again.status == 200
    assert Page(first.text).notices == [("status", REGISTRY_QUEUED)]
    assert Page(again.text).notices == [("status", REGISTRY_ALREADY)]
    assert 'id="discovery"' in first.text and "<html" not in first.text
    [(key, lane)] = registry_jobs(db_engine)
    assert key.startswith(f"discover_registry:{uk}:") and lane == "bulk"


@pytest.mark.parametrize(
    ("campaign", "message"),
    [
        ("law_private_ai_us", "Companies House discovery runs only for a Campaign whose Market"),
        ("ops_automation_uk", "That Campaign isn't active"),
        ("01JD0000000000000000000000", "That Campaign isn't active"),
    ],
)
def test_a_non_uk_or_inactive_campaign_is_refused_and_nothing_queued(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    active: str,
    ids: dict[str, str],
    db_engine: Any,
    campaign: str,
    message: str,
) -> None:
    reply = post_registry(dashboard, session, [("campaign_id", ids.get(campaign, campaign))])

    [(role, text)] = Page(reply.text).notices
    assert reply.status == 200
    assert role == "alert" and text.startswith(message) and text.endswith("Nothing was queued.")
    assert registry_jobs(db_engine) == []


@pytest.mark.parametrize(
    "fields",
    [[], [("campaign_id", "x"), ("campaign_id", "x")], [("campaign_id", "x"), ("extra", "1")]],
    ids=["no-campaign", "repeated", "extra"],
)
def test_a_malformed_companies_house_form_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], db_engine: Any, fields: list[tuple[str, str]]
) -> None:
    reply = post_registry(dashboard, session, fields)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
    assert registry_jobs(db_engine) == []


def test_a_companies_house_post_without_the_csrf_token_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str], uk: str, db_engine: Any
) -> None:
    reply = post_registry(dashboard, session, [("campaign_id", uk)], token=False)

    assert reply.status == 403
    assert registry_jobs(db_engine) == []


def test_a_companies_house_run_shows_no_location_and_its_inactive_count(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, uk: str
) -> None:
    segment = segment_of(seeded)

    @use_case
    def store() -> None:
        with current().transaction():
            run_id = runs_repo.insert(
                source="companies_house",
                campaign_id=uk,
                segment_id=segment,
                country_code="GB",
                location=None,
                query_hash="0" * 16,
                status=RunStatus.RUNNING,
                started_at=T0,
                finished_at=None,
            )
            for outcome in ("created", "inactive", "inactive"):
                runs_repo.count(run_id, outcome)  # type: ignore[arg-type]
            runs_repo.add_outcome(run_id, "no_website")  # type: ignore[arg-type]
            runs_repo.finish(run_id, RunStatus.FINISHED, T0)

    with seeded.open():
        store()

    reply = dashboard.get("/leads/discovery", cookie=session[0])

    rows = [cells for cells in Page(reply.text).cells if cells]
    assert rows == [
        [
            "Companies House",
            "Law-PrivateAI × UK",
            "— (GB)",
            "finished",
            "2026-11-18 09:00 UTC",
            "3",
            "1",
            "0",
            "0",
            "1",
            "0",
            "2",
        ]
    ]


# Story 8.4: the Leads list's "Website needed" filter and website form --------------------------


def registry_lead(seeded: UnitOfWorkFactory, name: str = "Beta Law LLP") -> str:
    from nextron.app.discovery.api import Discovery  # noqa: PLC0415
    from nextron.app.pipeline.api import Pipeline  # noqa: PLC0415
    from nextron.domain.leads import LeadIdentity, LeadSource  # noqa: PLC0415
    from tests.app.test_leads import queue_on  # noqa: PLC0415

    clock = FrozenClock(T0)
    discovery = Discovery(clock, Pipeline(clock), queue_on(clock))
    segment = segment_of(seeded)

    @use_case
    def create(identity: LeadIdentity) -> str:
        with current().transaction():
            upserted = discovery.upsert_lead(identity, LeadSource.COMPANIES_HOUSE)
        assert upserted.lead_id is not None
        return upserted.lead_id

    with seeded.open():
        return create(LeadIdentity(name, segment, ch_number="01234567", country_code="GB"))


def post_website(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    lead_id: str,
    fields: list[tuple[str, str]],
) -> Reply:
    cookie, csrf = session
    return dashboard.post(
        f"/leads/{lead_id}/website",
        cookie=cookie,
        token=csrf,
        headers=[(b"content-type", FORM)],
        body=urlencode(fields).encode("ascii"),
    )


def test_the_website_needed_filter_lists_leads_without_a_website_with_a_form(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory
) -> None:
    lead_id = registry_lead(seeded)

    reply = dashboard.get("/leads", cookie=session[0], query=b"website=needed")

    assert reply.status == 200
    found = Page(reply.text)
    [form] = [form for form in found.forms if form.get("method") == "post"]
    assert (form["action"], form["hx-post"]) == (
        f"/leads/{lead_id}/website",
        f"/leads/{lead_id}/website",
    )
    assert 'name="csrf_token"' in reply.text
    assert found.options["website"] == [("", "Any", False), ("needed", "Website needed", True)]
    assert "Beta Law LLP" in reply.text


def test_a_bad_website_filter_is_refused(
    dashboard: Dashboard, session: tuple[bytes, str]
) -> None:
    reply = dashboard.get("/leads", cookie=session[0], query=b"website=always")

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)


def test_an_entered_website_is_saved_and_the_cell_says_so(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, db_engine: Any
) -> None:
    lead_id = registry_lead(seeded)

    reply = post_website(dashboard, session, lead_id, [("website", "www.beta-law.co.uk")])

    assert reply.status == 200
    assert Page(reply.text).notices == [
        ("status", "Website saved; the Lead is queued for research.")
    ]
    assert f'href="/leads/{lead_id}"' in reply.text
    assert "Beta Law LLP" not in reply.text and "<html" not in reply.text
    with db_engine.connect() as connection:
        domain = connection.exec_driver_sql(
            "SELECT web_domain FROM leads WHERE id = ?", (lead_id,)
        ).scalar_one()
        crawls = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM jobs WHERE type = 'enrich_lead'"
        ).scalar_one()
    assert (domain, crawls) == ("beta-law.co.uk", 1)


def test_a_refused_website_shows_why_above_the_form_and_saves_nothing(
    dashboard: Dashboard, session: tuple[bytes, str], seeded: UnitOfWorkFactory, db_engine: Any
) -> None:
    lead_id = registry_lead(seeded)

    reply = post_website(
        dashboard, session, lead_id, [("website", "https://www.linkedin.com/company/beta")]
    )

    [(role, text)] = Page(reply.text).notices
    assert role == "alert" and text.endswith("Nothing was saved.")
    assert "linkedin" not in reply.text  # nothing typed is rendered back
    assert [form["hx-post"] for form in Page(reply.text).forms] == [f"/leads/{lead_id}/website"]
    with db_engine.connect() as connection:
        domain = connection.exec_driver_sql(
            "SELECT web_domain FROM leads WHERE id = ?", (lead_id,)
        ).scalar_one()
    assert domain is None


@pytest.mark.parametrize(
    "fields",
    [[], [("website", "a"), ("website", "b")], [("website", "a"), ("x", "1")], [("website", "a" * 2049)]],
    ids=["none", "repeated", "extra", "too-long"],
)
def test_a_malformed_website_form_is_refused(
    dashboard: Dashboard,
    session: tuple[bytes, str],
    seeded: UnitOfWorkFactory,
    fields: list[tuple[str, str]],
) -> None:
    lead_id = registry_lead(seeded)

    reply = post_website(dashboard, session, lead_id, fields)

    assert (reply.status, reply.text) == (400, MALFORMED_FORM)
