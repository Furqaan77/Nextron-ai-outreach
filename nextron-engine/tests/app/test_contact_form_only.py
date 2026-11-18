"""Story 2.9's "contact form only" Absence on a temporary database at head (FR-10, AD-7, AD-18):
``WebsiteCrawls.enrich_lead`` and ``render_pages`` with a ``ScriptedCrawler``, a
``ScriptedRenderer`` and a ``FrozenClock`` (the world of ``tests/app/test_website_crawl.py``),
with an ``EvidenceChanged`` recorder subscribed.

Every row of the spec's matrix is here: the Absence that holds (Evidence, Signal and the
``absence_checked`` line), a re-crawl of the same pages (confirmed) and of other pages
(superseded, same Signal), a chat, booking or after-hours line added (retracted), the
unverifiable runs (a page that still needs rendering, a tag manager, a page never checked for
tag managers), no form, an Ops Lead, the render path, the Google tag, and a merged or stale
render. Also: a run that stored nothing, idempotence, the log keys, and an AI extraction that
leaves the Absence alone."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research, WebsiteCrawls
from nextron.app.setup.api import Setup
from nextron.db.uow import Subscribers, UnitOfWorkFactory, command
from nextron.domain import seed_yaml
from nextron.domain.absence import CONTACT_FORM_ONLY, CONTACT_FORM_ONLY_SOUGHT
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.notifications import EvidenceChanged, LeadsMerged
from nextron.domain.segments import validate_segments
from nextron.ports.errors import AdapterError
from tests.app import test_signal_extraction as extraction
from tests.app.test_website_crawl import HOME, ORIGIN, SHIPPED, SITE, T0, World, no_jobs
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.renderer import ScriptedRenderer, rendered_page
from tests.fakes.usage_meter import InMemoryUsageMeter

TEAM = f"{ORIGIN}/team"
CONTACT = f"{ORIGIN}/contact"
ABOUT = f"{ORIGIN}/about"
FILLER = "We advise families and businesses across the north of England. "  # 63 characters
LONG = f"<p>{FILLER * 9}</p>"  # 567 characters of text: no render needed
FORM = "<form><textarea name='message'></textarea></form>"
LINKS = "<a href='/team'>Our team</a><a href='/contact'>Contact us</a>"
SHELL = "<div id='root'>Smith Law LLP: loading our website for you</div>"
INTERCOM = "<script src='https://widget.intercom.io/widget/abc123'></script>"
CALENDLY = "<a href='https://calendly.com/smith-law/consultation'>Book</a>"
AFTER_HOURS = "<p>Call our 24/7 emergency line.</p>"
GTM_SNIPPET = (
    "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':new Date().getTime(),"
    "event:'gtm.js'});})(window,document,'script','dataLayer','GTM-ABC123');</script>"
)
GTAG = (
    "<script async src='https://www.googletagmanager.com/gtag/js?id=G-ABC123'></script>"
    "<script>window.dataLayer = window.dataLayer || [];function gtag(){dataLayer.push(arguments);}"
    "gtag('js', new Date());gtag('config', 'G-ABC123');</script>"
)
THREE_PAGES = [HOME, CONTACT, TEAM]  # sorted, as pages_checked holds them
ABSENCE_KEYS = {
    "lead_id",
    "run_id",
    "reason",
    "confirmed",
    "inserted",
    "superseded",
    "retracted",
    "signals_created",
    "signals_retracted",
}
STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord("x", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime"}
API_LOGGER = "nextron.app.research.api"


class EvidenceRecorder:
    """An ``EvidenceChanged`` subscriber that records what it is given."""

    def __init__(self) -> None:
        self.seen: list[EvidenceChanged] = []

    @command
    def evidence_changed(self, n: EvidenceChanged) -> None:
        self.seen.append(n)


@dataclass
class AbsenceWorld(World):
    """The crawl world with an ``EvidenceChanged`` recorder."""

    recorder: EvidenceRecorder

    def page(self, head: str = "", body: str = LONG, links: str = LINKS) -> str:
        return f"<html><head>{head}</head><body><h1>Smith Law LLP</h1>{body}{links}</body></html>"

    def script(
        self,
        *,
        home: str | None = None,
        team: str = LONG,
        contact: str = LONG + FORM,
        about: str | None = None,
    ) -> None:
        """Script the site: a home linking to its pages, each page's body as given."""
        links = LINKS + ("<a href='/about'>About us</a>" if about is not None else "")
        pages = {
            HOME: html_page(HOME, home if home is not None else self.page(links=links)),
            TEAM: html_page(TEAM, f"<html><body>{team}</body></html>"),
            CONTACT: html_page(CONTACT, f"<html><body>{contact}</body></html>"),
        }
        if about is not None:
            pages[ABOUT] = html_page(ABOUT, f"<html><body>{about}</body></html>")
        self.crawler.pages.clear()
        self.site(pages)

    def render(self, lead_id: str, run_id: str | None = None) -> bool:
        found = run_id if run_id is not None else self.run(lead_id).id
        with self.units.open():
            return self.crawls.render_pages(lead_id, found)

    def evidence(self, lead_id: str) -> list[dict[str, Any]]:
        columns = (
            "id, type, quote, sought, number, fact, value, pages_checked, source, source_url, "
            "retrieved_at, extracted_by, person_contact_id, superseded_by, retracted_at, "
            "last_confirmed_at, created_at"
        )
        rows = self.sql(
            f"SELECT {columns} FROM evidence WHERE lead_id = ? ORDER BY created_at, id",
            (lead_id,),
        )
        names = [name.strip() for name in columns.split(",")]
        return [dict(zip(names, row, strict=True)) for row in rows]

    def signals(self, lead_id: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT id, signal_type, signal_class, tier, score_only, event_date, first_seen_at, "
            "retracted_at FROM signals WHERE lead_id = ? ORDER BY first_seen_at, id",
            (lead_id,),
        )

    def links(self) -> set[tuple[str, str]]:
        return set(self.sql("SELECT signal_id, evidence_id FROM signal_evidence"))

    def tag_managers(self, lead_id: str) -> dict[str, str | None]:
        rows = self.sql("SELECT url, tag_managers FROM crawled_pages WHERE lead_id = ?", (lead_id,))
        return dict(rows)

    def events(self) -> int:
        """The ``events`` rows that aren't a job's (a crawl's enqueue writes those)."""
        [(count,)] = self.sql("SELECT COUNT(*) FROM events WHERE entity != 'jobs'")
        return int(count)


def utc(at: datetime) -> str:
    """``at`` as the database stores it."""
    return at.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def pages_json(urls: list[str]) -> str:
    return "[" + ",".join(f'"{url}"' for url in urls) + "]"


@pytest.fixture
def world(db_engine: Engine) -> AbsenceWorld:
    clock = FrozenClock(T0)
    recorder = EvidenceRecorder()
    subscribers = Subscribers()
    subscribers.register_subscriber(LeadsMerged, Pipeline(clock).reparent_lead)
    subscribers.register_subscriber(LeadsMerged, Research(clock).reparent_lead)
    subscribers.register_subscriber(EvidenceChanged, recorder.evidence_changed)
    subscribers.freeze()
    units = UnitOfWorkFactory(db_engine, subscribers)
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    meter = InMemoryUsageMeter(clock)
    crawler = ScriptedCrawler()
    renderer = ScriptedRenderer()
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
    )
    queue = JobQueue(clock, job_types)
    found = AbsenceWorld(
        units,
        db_engine,
        clock,
        meter,
        crawler,
        renderer,
        queue,
        WebsiteCrawls(clock, meter, crawler, renderer, queue),
        Discovery(clock, Pipeline(clock), queue),
        Research(clock),
        "",
        recorder,
    )
    [(found.segment,)] = found.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
    return found


def extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in STANDARD_RECORD_KEYS}


def checked(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """The ``absence_checked`` lines logged, as their fields."""
    return [
        extras(record)
        for record in caplog.records
        if record.name == API_LOGGER and record.getMessage() == "absence_checked"
    ]


def counts(
    reason: str,
    *,
    confirmed: int = 0,
    inserted: int = 0,
    superseded: int = 0,
    retracted: int = 0,
    signals_created: int = 0,
    signals_retracted: int = 0,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "confirmed": confirmed,
        "inserted": inserted,
        "superseded": superseded,
        "retracted": retracted,
        "signals_created": signals_created,
        "signals_retracted": signals_retracted,
    }


def without_ids(line: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in line.items() if k not in ("lead_id", "run_id")}


def held(world: AbsenceWorld) -> tuple[str, str, str]:
    """A Law Lead whose 3-page site holds the Absence at ``T0``: its id, its Absence's id and
    its Signal's id."""
    lead = world.lead()
    world.script()
    world.crawl(lead)
    [row] = world.evidence(lead)
    [signal] = world.signals(lead)
    return lead, row["id"], signal[0]


# Row: Holds -------------------------------------------------------------------------------------


def test_a_form_and_nothing_else_stores_the_absence_and_its_score_only_signal(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    world.script()
    events_before = world.events()

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        assert world.crawl(lead) is True

    [row] = world.evidence(lead)
    assert {k: v for k, v in row.items() if k != "id"} == {
        "type": "absence",
        "quote": None,
        "sought": CONTACT_FORM_ONLY_SOUGHT,
        "number": None,
        "fact": None,
        "value": None,
        "pages_checked": pages_json(THREE_PAGES),
        "source": "website",
        "source_url": None,
        "retrieved_at": utc(T0),
        "extracted_by": "rule",
        "person_contact_id": None,
        "superseded_by": None,
        "retracted_at": None,
        "last_confirmed_at": utc(T0),
        "created_at": utc(T0),
    }
    [signal] = world.signals(lead)
    assert signal[1:] == (CONTACT_FORM_ONLY, "attribute", 3, 1, None, utc(T0), None)
    assert world.links() == {(signal[0], row["id"])}
    assert world.recorder.seen == []
    assert world.events() == events_before  # no events row
    assert world.tag_managers(lead) == {HOME: "[]", TEAM: "[]", CONTACT: "[]"}
    run_id = world.run(lead).id
    assert checked(caplog) == [
        {"lead_id": lead, "run_id": run_id, **counts("holds", inserted=1, signals_created=1)}
    ]


# Row: Re-crawl, same pages ----------------------------------------------------------------------


def test_a_recrawl_of_the_same_pages_confirms_the_absence_and_nothing_else(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead, absence, signal = held(world)
    world.clock.advance(timedelta(days=1))

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    [row] = world.evidence(lead)
    assert (row["id"], row["last_confirmed_at"], row["retrieved_at"]) == (
        absence,
        utc(T0 + timedelta(days=1)),
        utc(T0),
    )
    assert (row["superseded_by"], row["retracted_at"]) == (None, None)
    assert [s[0] for s in world.signals(lead)] == [signal]
    assert world.links() == {(signal, absence)}
    assert world.recorder.seen == []
    assert [without_ids(line) for line in checked(caplog)] == [counts("holds", confirmed=1)]


def test_the_rule_is_idempotent(world: AbsenceWorld) -> None:
    lead, absence, signal = held(world)
    for _ in range(2):
        world.clock.advance(timedelta(hours=1))
        world.crawl(lead)

    rows = world.evidence(lead)
    assert [(row["id"], row["superseded_by"], row["retracted_at"]) for row in rows] == [
        (absence, None, None)
    ]
    assert [(s[0], s[-1]) for s in world.signals(lead)] == [(signal, None)]
    assert world.recorder.seen == []


# Row: Re-crawl, other pages ---------------------------------------------------------------------


def test_a_recrawl_of_other_pages_supersedes_the_absence_on_the_same_signal(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead, absence, signal = held(world)
    world.clock.advance(timedelta(days=1))
    world.script(about=LONG)  # a fourth page

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    old, new = world.evidence(lead)
    assert old["id"] == absence
    assert (old["superseded_by"], old["retracted_at"]) == (new["id"], None)
    assert new["pages_checked"] == pages_json([HOME, ABOUT, CONTACT, TEAM])
    assert (new["retrieved_at"], new["sought"], new["extracted_by"]) == (
        utc(T0 + timedelta(days=1)),
        CONTACT_FORM_ONLY_SOUGHT,
        "rule",
    )
    [current] = world.signals(lead)
    assert (current[0], current[-1]) == (signal, None)  # the same Signal, still current
    assert world.links() == {(signal, absence), (signal, new["id"])}
    assert world.recorder.seen == [EvidenceChanged(lead_id=lead, evidence_id=absence)]
    assert [without_ids(line) for line in checked(caplog)] == [counts("holds", superseded=1)]


# Row: Chat added --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "body", "reason"),
    [
        (INTERCOM, LONG, "chat_found"),
        ("", LONG + CALENDLY, "booking_found"),
        ("", LONG + AFTER_HOURS, "after_hours_found"),
    ],
)
def test_a_chat_booking_link_or_after_hours_line_retracts_the_absence_and_its_signal(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture, head: str, body: str, reason: str
) -> None:
    lead, absence, signal = held(world)
    later = T0 + timedelta(days=1)
    world.clock.advance(timedelta(days=1))
    world.script(home=world.page(head=head, body=body))

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    [row] = world.evidence(lead)
    assert (row["id"], row["superseded_by"], row["retracted_at"]) == (absence, None, utc(later))
    [retracted] = world.signals(lead)
    assert (retracted[0], retracted[-1]) == (signal, utc(later))
    assert world.recorder.seen == [EvidenceChanged(lead_id=lead, evidence_id=absence)]
    assert [without_ids(line) for line in checked(caplog)] == [
        counts(reason, retracted=1, signals_retracted=1)
    ]


# Row: Unverifiable ------------------------------------------------------------------------------


def test_a_page_still_near_empty_after_its_render_failed_claims_nothing(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    world.script(home=world.page(body=SHELL))
    world.renderer.pages[HOME] = AdapterError(False, "render_unavailable", "no browser")

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)
        assert checked(caplog) == []  # the crawl queued a render and set no contact_methods
        assert world.render(lead) is True

    assert world.evidence(lead) == []
    assert world.signals(lead) == []
    assert [without_ids(line) for line in checked(caplog)] == [counts("page_needs_render")]


def test_a_page_that_now_needs_rendering_retracts_a_live_absence(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead, absence, signal = held(world)
    later = T0 + timedelta(days=1)
    world.clock.advance(timedelta(days=1))
    world.script(home=world.page(body=SHELL))
    world.renderer.pages[HOME] = AdapterError(True, "render_timeout", "too slow")

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    # in between: the crawl queued render_pages, so the rule didn't run and nothing changed
    assert checked(caplog) == []
    [between] = world.evidence(lead)
    assert (between["id"], between["last_confirmed_at"], between["retracted_at"]) == (
        absence,
        utc(T0),
        None,
    )
    assert [(s[0], s[-1]) for s in world.signals(lead)] == [(signal, None)]
    assert world.recorder.seen == []
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.render(lead)

    [row] = world.evidence(lead)
    assert (row["id"], row["retracted_at"]) == (absence, utc(later))
    assert world.signals(lead)[0][-1] == utc(later)
    assert world.recorder.seen == [EvidenceChanged(lead_id=lead, evidence_id=absence)]
    [line] = checked(caplog)
    assert without_ids(line) == counts("page_needs_render", retracted=1, signals_retracted=1)
    assert line["run_id"] == world.run(lead).id


def test_a_tag_manager_retracts_a_live_absence(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead, absence, _ = held(world)
    world.clock.advance(timedelta(days=1))
    world.script(home=world.page(head=GTM_SNIPPET))

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    [row] = world.evidence(lead)
    assert (row["id"], row["retracted_at"]) == (absence, utc(T0 + timedelta(days=1)))
    assert world.tag_managers(lead)[HOME] == '["google_tag_manager"]'
    assert [without_ids(line) for line in checked(caplog)] == [
        counts("tag_manager", retracted=1, signals_retracted=1)
    ]


def test_a_render_records_the_tag_managers_its_html_loads(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    world.script(home=world.page(body=SHELL))
    world.renderer.pages[HOME] = rendered_page(
        HOME, world.page(head=GTM_SNIPPET, body=LONG * 2)
    )

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)
        assert world.tag_managers(lead)[HOME] == "[]"  # the shell loads none
        assert world.render(lead) is True

    assert world.tag_managers(lead) == {HOME: '["google_tag_manager"]', TEAM: "[]", CONTACT: "[]"}
    assert world.evidence(lead) == []
    assert world.signals(lead) == []
    assert [without_ids(line) for line in checked(caplog)] == [counts("tag_manager")]


def test_a_render_adds_to_the_tag_managers_the_crawl_stored_and_never_replaces_them(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    world.script(home=world.page(head=GTM_SNIPPET, body=SHELL))
    world.renderer.pages[HOME] = rendered_page(HOME, world.page(body=LONG * 2))  # no GTM

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)
        assert world.tag_managers(lead)[HOME] == '["google_tag_manager"]'
        assert world.render(lead) is True

    assert len(world.pages(lead)[HOME].text) >= 500  # the render's text replaced the shell's
    assert world.tag_managers(lead)[HOME] == '["google_tag_manager"]'  # the crawl's key kept
    assert world.evidence(lead) == []
    assert [without_ids(line) for line in checked(caplog)] == [counts("tag_manager")]


def test_an_absence_after_a_retraction_gets_a_new_signal_and_tag_managers_are_replaced(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead, absence, signal = held(world)
    world.clock.advance(timedelta(days=1))
    world.script(home=world.page(head=INTERCOM))
    world.crawl(lead)  # a chat: the Absence and its Signal are retracted
    world.clock.advance(timedelta(days=1))
    world.script(home=world.page(head=GTM_SNIPPET))

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    assert world.tag_managers(lead)[HOME] == '["google_tag_manager"]'
    assert [without_ids(line) for line in checked(caplog)] == [counts("tag_manager")]
    caplog.clear()
    seen = list(world.recorder.seen)
    last = T0 + timedelta(days=3)
    world.clock.advance(timedelta(days=1))
    world.script()

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    assert world.tag_managers(lead)[HOME] == "[]"  # replaced, not added to
    assert [without_ids(line) for line in checked(caplog)] == [
        counts("holds", inserted=1, signals_created=1)
    ]
    old, new = world.evidence(lead)
    assert (old["id"], old["retracted_at"]) == (absence, utc(T0 + timedelta(days=1)))
    assert (new["retracted_at"], new["superseded_by"], new["retrieved_at"]) == (
        None,
        None,
        utc(last),
    )
    retracted, current = world.signals(lead)
    assert (retracted[0], retracted[1], retracted[-1]) == (
        signal,
        CONTACT_FORM_ONLY,
        utc(T0 + timedelta(days=1)),
    )
    assert (current[1], current[6], current[-1]) == (CONTACT_FORM_ONLY, utc(last), None)
    assert world.links() == {(signal, absence), (current[0], new["id"])}
    assert world.recorder.seen == seen  # no EvidenceChanged on the last crawl


def test_a_page_never_checked_for_tag_managers_retracts_a_live_absence(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    """A run applied before migration 0028 left NULL ``tag_managers``; its render runs after."""
    lead, absence, _ = held(world)
    later = T0 + timedelta(days=1)
    world.clock.advance(timedelta(days=1))
    world.script(home=world.page(body=SHELL))
    world.crawl(lead)
    world.sql("UPDATE crawled_pages SET tag_managers = NULL")
    world.renderer.pages[HOME] = rendered_page(HOME, world.page(body=LONG * 2))

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        assert world.render(lead) is True

    assert world.tag_managers(lead) == {HOME: "[]", TEAM: None, CONTACT: None}
    [row] = world.evidence(lead)
    assert (row["id"], row["retracted_at"]) == (absence, utc(later))
    assert [without_ids(line) for line in checked(caplog)] == [
        counts("tag_manager_unknown", retracted=1, signals_retracted=1)
    ]


# Row: No form / Ops Lead ------------------------------------------------------------------------


def test_no_form_claims_nothing(world: AbsenceWorld, caplog: pytest.LogCaptureFixture) -> None:
    lead = world.lead()
    world.script(contact=LONG + "<p>Call 0161 234 5678</p>")

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    assert world.evidence(lead) == []
    assert world.signals(lead) == []
    assert [without_ids(line) for line in checked(caplog)] == [counts("no_form")]


def test_an_ops_lead_is_not_in_the_segment(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    [(ops,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    identity = LeadIdentity(name="Acme Freight", segment_id=ops, web_domain=SITE)
    lead = world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.PLACES)).lead_id
    world.script()

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)

    assert world.evidence(lead) == []
    assert world.signals(lead) == []
    assert [without_ids(line) for line in checked(caplog)] == [counts("not_in_segment")]


# Row: Render path -------------------------------------------------------------------------------


def test_a_shell_rendered_with_a_form_gets_the_absence_from_the_render(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    world.script(home=world.page(body=SHELL), contact=LONG)  # no form until the render
    filler = f"<p>{FILLER * 19}</p>"  # about 1,200 characters
    world.renderer.pages[HOME] = rendered_page(HOME, world.page(body=filler + FORM))

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead)
        assert world.evidence(lead) == []
        assert world.render(lead) is True

    run_id = world.run(lead).id
    assert 1100 < len(world.pages(lead)[HOME].text) < 1400
    [row] = world.evidence(lead)
    assert (row["type"], row["pages_checked"], row["extracted_by"]) == (
        "absence",
        pages_json(THREE_PAGES),
        "rule",
    )
    [signal] = world.signals(lead)
    assert signal[1:4] == (CONTACT_FORM_ONLY, "attribute", 3)
    assert checked(caplog) == [
        {"lead_id": lead, "run_id": run_id, **counts("holds", inserted=1, signals_created=1)}
    ]


# Row: gtag only ---------------------------------------------------------------------------------


def test_the_google_tag_is_no_tag_manager(world: AbsenceWorld) -> None:
    lead = world.lead()
    world.script(home=world.page(head=GTAG))

    world.crawl(lead)

    assert world.tag_managers(lead)[HOME] == "[]"
    [row] = world.evidence(lead)
    assert row["type"] == "absence"


# Row: Merged / stale render ---------------------------------------------------------------------


def test_a_stale_render_does_not_run_the_rule(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead = world.lead()
    world.script(home=world.page(body=SHELL))
    world.crawl(lead)
    stale = world.run(lead).id
    world.clock.advance(timedelta(hours=1))
    world.crawl(lead)
    world.renderer.pages[HOME] = rendered_page(HOME, world.page())

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        assert world.render(lead, stale) is True

    assert checked(caplog) == []
    assert world.evidence(lead) == []


def test_a_merged_leads_render_does_not_run_the_rule(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    older = world.lead(web_domain="older-firm.com")
    world.clock.advance(timedelta(minutes=1))
    lead = world.lead()
    world.script(home=world.page(body=SHELL))
    world.crawl(lead)
    run_id = world.run(lead).id
    world.transact(lambda: world.discovery.merge(older, lead))
    world.renderer.pages[HOME] = rendered_page(HOME, world.page())

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        assert world.render(lead, run_id) is True

    assert checked(caplog) == []
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(0,)]


def test_a_run_that_stored_nothing_leaves_the_absence(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    lead, absence, signal = held(world)
    world.clock.advance(timedelta(days=1))
    world.crawler.pages[HOME] = html_page(HOME, status=404)

    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        world.crawl(lead, may_retry=False)

    assert world.run(lead).outcome == "unreachable"
    assert checked(caplog) == []
    [row] = world.evidence(lead)
    assert (row["id"], row["last_confirmed_at"], row["retracted_at"]) == (absence, utc(T0), None)
    assert world.signals(lead)[0][-1] is None


# The log line -----------------------------------------------------------------------------------


def test_the_log_line_holds_ids_reasons_and_counts_only(
    world: AbsenceWorld, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=API_LOGGER):
        lead, _, _ = held(world)

    [line] = checked(caplog)
    assert set(line) == ABSENCE_KEYS
    for value in line.values():
        assert not any(word in str(value) for word in ("http", "smith-law", "Smith", "advise"))


# Extraction leaves it alone ---------------------------------------------------------------------


def test_an_ai_extraction_leaves_the_absence_and_its_signal_alone(db_engine: Engine) -> None:
    ext = extraction.build_world(db_engine)
    ext.crawler.robots[extraction.ROBOTS] = robots_txt(
        extraction.ROBOTS, "User-agent: *\nDisallow:\n"
    )
    links = "<a href='/team'>Our team</a><a href='/about'>About us</a>"
    ext.crawler.pages[extraction.HOME] = html_page(extraction.HOME, f"{LONG}{links}")
    ext.crawler.pages[extraction.TEAM] = html_page(
        extraction.TEAM, f"{LONG}<p>{extraction.HIRING}</p>"
    )
    ext.crawler.pages[extraction.ABOUT] = html_page(extraction.ABOUT, LONG + FORM)
    lead = ext.lead()
    run_id = ext.crawl(lead)
    [absence] = ext.evidence(lead)
    pages = ext.page_ids(lead)

    hiring = extraction.signal(pages[extraction.TEAM], extraction.HIRING)
    assert ext.extract(lead, run_id, extraction.ok(signals=[hiring])) is True
    # the next crawl drops the hiring quote; its extraction retracts it, not the Absence
    ext.clock.advance(timedelta(days=1))
    ext.crawler.pages[extraction.TEAM] = html_page(extraction.TEAM, LONG)
    second = ext.crawl(lead)
    assert ext.extract(lead, second, extraction.ok()) is True

    live = [row for row in ext.evidence(lead) if row["retracted_at"] is None]
    assert [(row["id"], row["type"]) for row in live] == [(absence["id"], "absence")]
    current = [s for s in ext.signals(lead) if s[-1] is None]
    assert [s[1] for s in current] == [CONTACT_FORM_ONLY]
    assert [s[1] for s in ext.signals(lead) if s[-1] is not None] == ["law_hiring_legal_ops"]
