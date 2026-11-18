"""Story 2.9's extraction on a temporary database at head (FR-10, FR-11, FR-57, AD-7, AD-8, AD-11,
AD-18): ``SignalExtractions.extract_lead`` through ``ScriptedLlm`` on pages a real
``WebsiteCrawls`` stored from a ``ScriptedCrawler`` (and rendered by a ``ScriptedRenderer`` as
they were crawled, Story 2.6), with the shipped Segments, Markets and prompts
(``active_tuple``), a ``FrozenClock`` and the real job types.

Every row of the spec's matrix the service owns is here: the first extraction (the blocks sent,
the Evidence, the Signal, the run, the headquarters zone), unknown types and quotes, size clues,
a re-extraction, changed and vanished quotes, no work (no activation, no changed page, the
budget), bad results, a retryable failure and a merge during the call. Also the headquarters
change re-resolving the Lead's Contacts, reparenting on a merge, the ``extract_lead`` job the
crawl enqueues and its handler. No real Claude request is made.

Story 2.10 runs a people step after the Signals (``PeopleExtractions``, on its own
``ScriptedLlm`` here, answering no people by default), and its runs share ``extraction_runs``:
``World.runs`` and ``World.calls`` keep to the ``extract_signals`` route; Story 2.10's tests
(``tests/app/test_people_extraction.py``) cover the people step."""

import contextvars
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import Playbook
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research, WebsiteCrawls
from nextron.app.research.extraction import LeadSignals, SignalExtractions
from nextron.app.research.people import PeopleExtractions
from nextron.app.setup.api import Setup
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import Subscribers, UnitOfWorkFactory, command, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import AiCall
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.notifications import EvidenceChanged, LeadsMerged
from nextron.domain.playbook import PlaybookSnapshot
from nextron.domain.segments import signal_catalogue, validate_segments
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    LlmFailed,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmResult,
    LlmTruncated,
    LlmUsage,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.llm import ScriptedLlm
from tests.fakes.renderer import ScriptedRenderer, rendered_page
from tests.fakes.usage_meter import InMemoryUsageMeter

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SITE = "smith-law.com"
ORIGIN = f"https://{SITE}"
HOME = f"{ORIGIN}/"
TEAM = f"{ORIGIN}/team"
ABOUT = f"{ORIGIN}/about"
NEWS = f"{ORIGIN}/news"
ROBOTS = f"{ORIGIN}/robots.txt"
HAIKU = "claude-haiku-5-5"
ACTOR = "ai:extract_signals@v1"
SEGMENTS = main.SEED_DIR / main.SEGMENTS_FILE_NAME
MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME
# Story 4.4: every prompt sync also takes a Sales Playbook read; extraction doesn't use the
# Playbook, so these tests use a synthetic one.
PLAYBOOK = PlaybookSnapshot("1.0.0", "---\nversion: 1.0.0\n---\n\nA test Sales Playbook.\n")

HOME_HTML = """<html><body><h1>Smith Law LLP</h1>
<a href="/team">Our team</a> <a href="/about">About us</a> <a href="/news">News</a>
</body></html>"""
HIRING = "We are hiring a Legal Operations Manager."
AI_POST = "We never put client data into public AI tools."
TWELVE = "Smith & Jones LLP has 12 lawyers in Leeds."
TEAM_V1 = f"<p>{TWELVE}</p><p>{HIRING}</p><p>{AI_POST}</p>"
CHICAGO = "Our head office is in Chicago, IL 60601."
TWELVE_WORDS = "We have twelve lawyers."
OPENED = "On 14 October 2026 we opened a new office in Manchester."
ABOUT_HTML = f"<p>{CHICAGO}</p><p>{TWELVE_WORDS}</p>"
NEWS_HTML = f"<p>{OPENED}</p><p>Read more in our blog.</p>"


class EvidenceRecorder:
    """An ``EvidenceChanged`` subscriber that records what it is given."""

    def __init__(self) -> None:
        self.seen: list[EvidenceChanged] = []

    @command
    def evidence_changed(self, n: EvidenceChanged) -> None:
        self.seen.append(n)


def no_jobs() -> Any:
    raise AssertionError("no job runs in these tests")


NO_PEOPLE = LlmOk({"people": []}, model=HAIKU, usage=LlmUsage(requests=1))


def ok(**lists: list[dict[str, Any]]) -> LlmOk:
    parsed = {
        "signals": lists.get("signals", []),
        "profile_facts": lists.get("profile_facts", []),
        "size_clues": lists.get("size_clues", []),
        "job_postings": lists.get("job_postings", []),
    }
    return LlmOk(parsed, model=HAIKU, usage=LlmUsage(requests=1))


def signal(
    page: str, quote: str, kind: str = "law_hiring_legal_ops", day: str | None = None
) -> Any:
    return {"signal_type": kind, "block_id": page, "quote": quote, "event_date": day}


def size(page: str, quote: str, number: int, kind: str = "lawyers_firm_wide") -> Any:
    return {"kind": kind, "number": number, "block_id": page, "quote": quote}


def fact(page: str, quote: str, value: str, kind: str = "location") -> Any:
    return {"kind": kind, "value": value, "block_id": page, "quote": quote}


@dataclass
class World:
    """The research services on real units of work, a scripted crawler and the prompts."""

    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    meter: InMemoryUsageMeter
    crawler: ScriptedCrawler
    renderer: ScriptedRenderer
    queue: JobQueue
    crawls: WebsiteCrawls
    discovery: Discovery
    research: Research
    recorder: EvidenceRecorder
    segment: str
    calls: list[tuple[RouteTuple, tuple[UntrustedBlock, ...], Urgency]] = field(
        default_factory=list
    )
    people_calls: list[tuple[RouteTuple, tuple[UntrustedBlock, ...], Urgency]] = field(
        default_factory=list
    )
    _people_llms: list[ScriptedLlm] = field(default_factory=list)

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def read[R](self, fn: Callable[[], R]) -> R:
        with self.units.open(read_only=True):
            return fn()

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def lead(self, web_domain: str = SITE, country: str | None = "US") -> str:
        identity = LeadIdentity(
            name="Smith Law LLP",
            segment_id=self.segment,
            web_domain=web_domain,
            country_code=country,
        )
        upserted = self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.PLACES))
        return upserted.lead_id

    def site(
        self, team: str = TEAM_V1, *, about: str = ABOUT_HTML, news: str | None = None
    ) -> None:
        self.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow:\n")
        self.crawler.pages[HOME] = html_page(HOME, HOME_HTML)
        self.crawler.pages[TEAM] = html_page(TEAM, team)
        self.crawler.pages[ABOUT] = html_page(ABOUT, about)
        if news is not None:
            self.crawler.pages[NEWS] = html_page(NEWS, news)
        else:
            self.crawler.pages[NEWS] = html_page(NEWS, status=404)
        # every page here is near-empty (Story 2.6); a render shows what the crawl stored
        for url, html in ((HOME, HOME_HTML), (TEAM, team), (ABOUT, about), (NEWS, news)):
            if html is not None:
                self.renderer.pages[url] = rendered_page(url, html)

    def crawl(self, lead_id: str) -> str:
        """Crawl the scripted site; the crawl run's id."""
        with self.units.open():
            assert self.crawls.enrich_lead(lead_id, may_retry=False) is True
        run = self.read(lambda: research_queries.latest_crawl_run(lead_id))
        assert run is not None
        return run.id

    def render(self, lead_id: str, run_id: str) -> None:
        """Run the crawl run's ``render_pages`` job (Story 2.6), which queues its extraction."""
        with self.units.open():
            assert self.crawls.render_pages(lead_id, run_id) is True

    def page_ids(self, lead_id: str) -> dict[str, str]:
        pages = self.read(lambda: research_queries.crawled_pages(lead_id))
        return {page.url: page.id for page in pages}

    def extractions(
        self, llm: Any, people: Sequence[LlmResult | AdapterError] = (NO_PEOPLE,)
    ) -> SignalExtractions:
        """``SignalExtractions`` on ``llm``, its people step (Story 2.10) on a ``ScriptedLlm`` of
        its own answering ``people``, whose calls go to ``people_calls``."""
        people_llm = ScriptedLlm(*people)
        self._people_llms.append(people_llm)
        step = PeopleExtractions(self.clock, self.meter, people_llm, self.research, self.queue)
        return SignalExtractions(self.clock, self.meter, llm, self.research, step)

    def extract(
        self,
        lead_id: str,
        run_id: str,
        *script: LlmResult | AdapterError,
        llm: Any = None,
        people: Sequence[LlmResult | AdapterError] = (NO_PEOPLE,),
    ) -> bool:
        scripted = ScriptedLlm(*script)
        extractions = self.extractions(scripted if llm is None else llm, people)
        try:
            with self.units.open():
                return extractions.extract_lead(lead_id, run_id)
        finally:
            self.calls.extend(scripted.calls)
            self.people_calls.extend(self._people_llms.pop().calls)

    def runs(self, lead_id: str) -> list[tuple[Any, ...]]:
        """The Lead's ``extract_signals`` runs (Story 2.10's people runs share the table)."""
        return self.sql(
            "SELECT outcome, error_code, actor, route, pages_sent, pages_truncated, stored, "
            "discarded FROM extraction_runs WHERE lead_id = ? AND route = 'extract_signals' "
            "ORDER BY rowid",
            (lead_id,),
        )

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

    def events(self, lead_id: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT entity_id, from_status, to_status, cause, cause_ref, actor FROM events "
            "WHERE entity = 'leads' AND entity_id = ? AND actor LIKE 'ai:%' ORDER BY rowid",
            (lead_id,),
        )

    def hq(self, lead_id: str) -> tuple[Any, ...]:
        [row] = self.sql("SELECT hq_tz, hq_evidence_id FROM leads WHERE id = ?", (lead_id,))
        return row

    def jobs(self, job_type: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT idempotency_key, concurrency_key, payload, lane FROM jobs WHERE type = ? "
            "ORDER BY rowid",
            (job_type,),
        )


def utc(at: datetime) -> str:
    """``at`` as the database stores it."""
    return at.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def build_world(engine: Engine, *, prompts: bool = True) -> World:
    clock = FrozenClock(T0)
    recorder = EvidenceRecorder()
    subscribers = Subscribers()
    subscribers.register_subscriber(LeadsMerged, Pipeline(clock).reparent_lead)
    subscribers.register_subscriber(LeadsMerged, Research(clock).reparent_lead)
    subscribers.register_subscriber(EvidenceChanged, recorder.evidence_changed)
    subscribers.freeze()
    units = UnitOfWorkFactory(engine, subscribers)
    segments = validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))
    markets = validate_markets(seed_yaml.parse(MARKETS.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).sync_markets(markets)
    if prompts:
        files, defaults = main.load_prompts()
        with units.open():
            Playbook(clock).sync_prompts(files, defaults, PLAYBOOK)
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
        primary=no_jobs,
        test_runs=no_jobs,
    )
    queue = JobQueue(clock, job_types)
    research = Research(clock)
    world = World(
        units,
        engine,
        clock,
        meter,
        crawler,
        renderer,
        queue,
        WebsiteCrawls(clock, meter, crawler, renderer, queue),
        Discovery(clock, Pipeline(clock), queue),
        research,
        recorder,
        "",
    )
    [(world.segment,)] = world.sql("SELECT id FROM segments WHERE key = 'law_private_ai'")
    return world


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def crawled(world: World, **site: Any) -> tuple[str, str, dict[str, str]]:
    """A Lead whose site was crawled at ``T0``: its id, the crawl run's id and its page ids by
    URL."""
    lead = world.lead()
    world.site(**site)
    run_id = world.crawl(lead)
    return lead, run_id, world.page_ids(lead)


def first_answer(pages: dict[str, str]) -> LlmOk:
    return ok(
        signals=[signal(pages[TEAM], HIRING)],
        size_clues=[size(pages[TEAM], TWELVE, 12)],
        profile_facts=[fact(pages[ABOUT], CHICAGO, "Chicago")],
    )


# Row: First extraction --------------------------------------------------------------------------


def test_the_first_extraction_sends_every_page_and_stores_what_validates(world: World) -> None:
    lead, run_id, pages = crawled(world)

    assert world.extract(lead, run_id, first_answer(pages)) is True

    # the call: the active tuple, bulk, the catalogue first, then every page by kind, then URL
    [(route_tuple, blocks, urgency)] = world.calls
    assert (route_tuple.route, route_tuple.actor) == ("extract_signals", ACTOR)
    assert urgency is Urgency.BULK
    [law] = [
        segment
        for segment in validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))
        if segment.key == "law_private_ai"
    ]
    assert [(b.subject, b.source, b.block_id) for b in blocks] == [
        (f"lead:{lead}", "signal_types", "signal_types"),
        (f"lead:{lead}", "website", pages[HOME]),
        (f"lead:{lead}", "website", pages[TEAM]),
        (f"lead:{lead}", "website", pages[ABOUT]),
    ]
    assert blocks[0].text == signal_catalogue([law])
    assert blocks[2].text == f"{TWELVE}\n{HIRING}\n{AI_POST}"
    # three Evidence rows, as the spec stores them (created at one time: keyed by quote)
    rows = {row["quote"]: row for row in world.evidence(lead)}
    assert {quote: (row["fact"], row["value"], row["number"]) for quote, row in rows.items()} == {
        HIRING: (None, None, None),
        CHICAGO: ("location", "Chicago", None),
        TWELVE: ("lawyers_firm_wide", None, 12),
    }
    for quote, url in ((HIRING, TEAM), (CHICAGO, ABOUT), (TWELVE, TEAM)):
        row = rows[quote]
        assert row["type"] == "quote" and row["sought"] is None
        assert (row["source"], row["source_url"], json.loads(row["pages_checked"])) == (
            "website",
            url,
            [url],
        )
        assert (row["retrieved_at"], row["last_confirmed_at"], row["created_at"]) == (
            utc(T0),
            utc(T0),
            utc(T0),
        )
        assert (row["extracted_by"], row["person_contact_id"]) == (ACTOR, None)
        assert (row["superseded_by"], row["retracted_at"]) == (None, None)
    # one Signal, linked to its Evidence, its class, Tier and score-only from the Segment
    [(signal_id, kind, signal_class, tier, score_only, event_date, first_seen, retracted)] = (
        world.signals(lead)
    )
    assert (kind, signal_class, tier, score_only, event_date, first_seen, retracted) == (
        "law_hiring_legal_ops",
        "event",
        1,
        0,
        None,
        utc(T0),
        None,
    )
    assert world.links() == {(signal_id, rows[HIRING]["id"])}
    assert world.runs(lead) == [("ok", None, ACTOR, "extract_signals", 3, 0, 3, 0)]
    [(crawl_run,)] = world.sql(
        "SELECT crawl_run_id FROM extraction_runs WHERE route = 'extract_signals'"
    )
    assert crawl_run == run_id
    # the headquarters zone, from the location quote
    assert world.hq(lead) == ("America/Chicago", rows[CHICAGO]["id"])
    assert world.events(lead) == [] and world.recorder.seen == []


def test_the_queries_read_what_was_stored(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))

    signals = world.read(lambda: research_queries.signals(lead))
    evidence = world.read(lambda: research_queries.evidence(lead))
    last = world.read(lambda: research_queries.last_ok_extraction(lead))

    assert [s.signal_type for s in signals] == ["law_hiring_legal_ops"]
    assert sorted(e.pages_checked for e in evidence) == [(ABOUT,), (TEAM,), (TEAM,)]
    assert world.read(lambda: research_queries.hq_tz(lead)) == "America/Chicago"
    assert last is not None and (last.outcome, last.started_at) == ("ok", T0)
    for unknown in ("not-a-ulid", "01KA0000000000000000000000"):
        reads = world.read(
            lambda: (
                research_queries.signals(unknown),  # noqa: B023
                research_queries.evidence(unknown),  # noqa: B023
                research_queries.hq_tz(unknown),  # noqa: B023
                research_queries.last_ok_extraction(unknown),  # noqa: B023
            )
        )
        assert reads == ((), (), None, None)


def test_pages_are_cut_to_their_share_of_the_budget_and_still_count(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nextron.app.research import extraction

    monkeypatch.setattr(extraction, "PAGE_TEXT_BUDGET", 150)
    lead, run_id, pages = crawled(world)
    answer = ok(signals=[signal(pages[TEAM], AI_POST, "law_ai_confidentiality_post")])

    world.extract(lead, run_id, answer)

    [(_, blocks, _)] = world.calls
    texts = {block.block_id: block.text for block in blocks[1:]}
    # 150 shared by 36, 131 and 64 characters: the home page whole, then 57 each
    assert texts[pages[HOME]] == "Smith Law LLP\nOur team About us News"
    assert texts[pages[TEAM]] == f"{TWELVE}\n{HIRING}"[:57]
    assert texts[pages[ABOUT]] == f"{CHICAGO}\n{TWELVE_WORDS}"[:57]
    # the quote beyond the cut is still checked against the full stored text
    assert world.runs(lead) == [("ok", None, ACTOR, "extract_signals", 3, 2, 1, 0)]


# Row: Unknown type or quote ---------------------------------------------------------------------


def test_unknown_types_and_quotes_are_discarded_with_one_event_each(world: World) -> None:
    lead, run_id, pages = crawled(world)
    answer = ok(
        signals=[
            signal(pages[TEAM], HIRING, "made_up"),
            signal(pages[TEAM], "We are hiring a robot."),
            signal(pages[TEAM], "x" * 301),
            signal("01KA0000000000000000000ZZZ", HIRING),
        ]
    )

    world.extract(lead, run_id, answer)

    assert world.evidence(lead) == [] and world.signals(lead) == []
    team = pages[TEAM]
    assert world.events(lead) == [
        (lead, None, "signal_discarded", "unknown_signal_type", team, ACTOR),
        (lead, None, "signal_discarded", "quote_not_in_page", team, ACTOR),
        (lead, None, "signal_discarded", "quote_too_long", team, ACTOR),
        (lead, None, "signal_discarded", "unknown_block", None, ACTOR),
    ]
    assert world.runs(lead) == [("ok", None, ACTOR, "extract_signals", 3, 0, 0, 4)]


# Row: Size clue ---------------------------------------------------------------------------------


def test_a_size_clue_needs_its_number_in_its_quote_and_in_range(world: World) -> None:
    lead, run_id, pages = crawled(world)
    answer = ok(
        size_clues=[size(pages[ABOUT], TWELVE_WORDS, 12), size(pages[TEAM], TWELVE, 0)],
        profile_facts=[fact(pages[ABOUT], CHICAGO, " ")],
    )

    world.extract(lead, run_id, answer)

    assert world.evidence(lead) == []
    assert [event[2:5] for event in world.events(lead)] == [
        ("profile_fact_discarded", "value_invalid", pages[ABOUT]),
        ("size_clue_discarded", "number_not_in_quote", pages[ABOUT]),
        ("size_clue_discarded", "number_out_of_range", pages[TEAM]),
    ]
    assert world.hq(lead) == (None, None)


def test_an_event_date_is_kept_when_stated_else_dropped_with_an_event(world: World) -> None:
    lead, run_id, pages = crawled(world, news=NEWS_HTML)
    news = pages[NEWS]
    answer = ok(
        signals=[
            signal(news, OPENED, "law_expansion", "2026-10-14"),
            signal(news, "Read more in our blog.", "law_expansion", "2026-10-15"),
        ]
    )

    world.extract(lead, run_id, answer)

    assert sorted((row[1:6] for row in world.signals(lead)), key=str) == [
        ("law_expansion", "event", 2, 0, "2026-10-14"),
        ("law_expansion", "event", 2, 0, None),  # undated: another Signal
    ]
    assert [event[2:5] for event in world.events(lead)] == [
        ("event_date_discarded", "date_not_in_quote", news)
    ]
    assert world.runs(lead)[0][6:] == (2, 0)  # a dropped date isn't a discarded proposal


# Row: Re-extraction -----------------------------------------------------------------------------


def recrawl(world: World, lead: str, *, days: int = 1, **site: Any) -> str:
    world.clock.set(T0 + timedelta(days=days))
    world.site(**site)
    return world.crawl(lead)


def test_a_re_proposed_identical_quote_only_moves_last_confirmed_at(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))
    before = world.evidence(lead)
    world.calls.clear()
    second_run = recrawl(world, lead, team=TEAM_V1 + "<p>New paragraph.</p>")
    t1 = T0 + timedelta(days=1)

    world.extract(lead, second_run, ok(signals=[signal(pages[TEAM], HIRING)]))

    [(_, blocks, _)] = world.calls
    assert [block.block_id for block in blocks] == ["signal_types", pages[TEAM]]  # changed only
    after = world.evidence(lead)
    assert [row["id"] for row in after] == [row["id"] for row in before]
    changed = {
        row["id"]: {key for key in row if row[key] != old[key]}
        for row, old in zip(after, before, strict=True)
    }
    # the hiring quote, and the size quote still on the page, are confirmed; the about page
    # wasn't sent, so its Evidence is untouched
    ids = {row["quote"]: row["id"] for row in before}
    assert changed == {
        ids[HIRING]: {"last_confirmed_at"},
        ids[CHICAGO]: set(),
        ids[TWELVE]: {"last_confirmed_at"},
    }
    assert {row["last_confirmed_at"] for row in after if row["quote"] != CHICAGO} == {utc(t1)}
    assert world.recorder.seen == []
    assert len(world.signals(lead)) == 1 and world.signals(lead)[0][7] is None
    assert world.runs(lead)[1] == ("ok", None, ACTOR, "extract_signals", 1, 0, 0, 0)


# Row: Changed or vanished -----------------------------------------------------------------------

TEAM_V2_HIRING = "We are hiring an Innovation Lead."
FOURTEEN = "Smith & Jones LLP has 14 lawyers in Leeds."
TEAM_V2 = f"<p>{FOURTEEN}</p><p>{TEAM_V2_HIRING}</p>"


def test_changed_quotes_supersede_vanished_ones_are_retracted_and_so_are_bare_signals(
    world: World,
) -> None:
    lead, run_id, pages = crawled(world)
    team = pages[TEAM]
    world.extract(
        lead,
        run_id,
        ok(
            signals=[signal(team, HIRING), signal(team, AI_POST, "law_ai_confidentiality_post")],
            size_clues=[size(team, TWELVE, 12)],
        ),
    )
    old = {row["quote"]: row["id"] for row in world.evidence(lead)}
    by_type = {row[1]: row[0] for row in world.signals(lead)}
    hiring_signal = by_type["law_hiring_legal_ops"]
    ai_signal = by_type["law_ai_confidentiality_post"]
    second_run = recrawl(world, lead, team=TEAM_V2)
    t1 = T0 + timedelta(days=1)

    world.extract(
        lead,
        second_run,
        ok(signals=[signal(team, TEAM_V2_HIRING)], size_clues=[size(team, FOURTEEN, 14)]),
    )

    rows = {row["quote"]: row for row in world.evidence(lead)}
    assert rows[HIRING]["superseded_by"] == rows[TEAM_V2_HIRING]["id"]
    assert rows[TWELVE]["superseded_by"] == rows[FOURTEEN]["id"]
    assert (rows[AI_POST]["retracted_at"], rows[AI_POST]["superseded_by"]) == (utc(t1), None)
    assert (rows[TEAM_V2_HIRING]["retrieved_at"], rows[FOURTEEN]["number"]) == (utc(t1), 14)
    # the hiring Signal (undated) keeps going on the new row; the AI post's has no live Evidence
    signals = {row[0]: row for row in world.signals(lead)}
    assert signals[hiring_signal][7] is None
    assert signals[ai_signal][7] == utc(t1)
    assert (hiring_signal, rows[TEAM_V2_HIRING]["id"]) in world.links()
    # superseded rows first (signal claims, then facts), then the retracted one
    assert world.recorder.seen == [
        EvidenceChanged(lead_id=lead, evidence_id=old[HIRING]),
        EvidenceChanged(lead_id=lead, evidence_id=old[TWELVE]),
        EvidenceChanged(lead_id=lead, evidence_id=old[AI_POST]),
    ]
    assert world.runs(lead)[1] == ("ok", None, ACTOR, "extract_signals", 1, 0, 2, 0)


def add_contact(world: World, lead: str) -> str:
    added = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name="Jane Doe", role="Managing Partner"),
            source=ProvenanceSource.WEBSITE,
            detail=TEAM,
            retrieved_at=T0,
        )
    )
    return added.contact_id


def test_a_vanished_location_clears_the_headquarters_and_resolves_the_contacts_again(
    world: World,
) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))
    contact = add_contact(world, lead)
    query = "SELECT tz, tz_basis FROM contacts WHERE id = ?"
    assert world.sql(query, (contact,)) == [("America/Chicago", "lead_hq")]
    second_run = recrawl(world, lead, about=f"<p>{TWELVE_WORDS}</p>")

    world.extract(lead, second_run, ok())

    assert world.hq(lead) == (None, None)
    assert world.sql(query, (contact,)) == [("America/New_York", "us_unknown_fallback")]


BRISTOL = "On 1 November 2026 we opened in Bristol."
MANCHESTER_AGAIN = "Our Manchester office opened on 14 October 2026."


def test_a_new_rows_event_signal_is_found_by_its_type_and_stated_date(world: World) -> None:
    lead, run_id, pages = crawled(world, news=NEWS_HTML)
    news = pages[NEWS]
    world.extract(lead, run_id, ok(signals=[signal(news, OPENED, "law_expansion", "2026-10-14")]))
    [(dated_a, *_)] = world.signals(lead)
    second_run = recrawl(
        world, lead, news=f"<p>{OPENED}</p><p>{BRISTOL}</p><p>{MANCHESTER_AGAIN}</p>"
    )

    world.extract(
        lead,
        second_run,
        ok(
            signals=[
                signal(news, BRISTOL, "law_expansion", "2026-11-01"),  # another date: a new one
                signal(news, MANCHESTER_AGAIN, "law_expansion", "2026-10-14"),  # the same date
            ]
        ),
    )

    current = {row[5]: row for row in world.signals(lead) if row[7] is None}
    assert set(current) == {"2026-10-14", "2026-11-01"}
    assert current["2026-10-14"][0] == dated_a  # linked, not created again
    dated_b = current["2026-11-01"][0]
    ids = {row["quote"]: row["id"] for row in world.evidence(lead)}
    assert world.links() == {
        (dated_a, ids[OPENED]),  # still on the page: confirmed
        (dated_a, ids[MANCHESTER_AGAIN]),
        (dated_b, ids[BRISTOL]),
    }
    assert current["2026-11-01"][6] == utc(T0 + timedelta(days=1))  # first seen: its page fetch
    assert world.runs(lead)[1][6] == 2


def test_only_this_routes_website_quotes_of_the_pages_sent_are_reconciled(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))

    def insert(quote: str, extracted_by: str, **fact: Any) -> str:
        return world.transact(
            lambda: signals_repo.insert_evidence(
                lead_id=lead,
                evidence_type="quote",
                quote=quote,
                sought=None,
                number=fact.get("number"),
                fact=fact.get("fact"),
                value=fact.get("value"),
                pages_checked=(TEAM,),
                source="website",
                source_url=TEAM,
                retrieved_at=T0,
                extracted_by=extracted_by,
                person_contact_id=None,
                at=T0,
            )
        )

    earlier = insert("We employ 40 staff.", "ai:extract_signals@v0", fact="staff", number=40)
    operator = insert("A firm Mac knows.", "operator", fact="what_we_do", value="Law firm")
    second_run = recrawl(world, lead, team=f"<p>{TWELVE}</p><p>{HIRING}</p>")

    world.extract(lead, second_run, ok())

    rows = {row["id"]: row for row in world.evidence(lead)}
    # an earlier prompt version's row is this route's: its quote vanished, so it is retracted
    assert rows[earlier]["retracted_at"] == utc(T0 + timedelta(days=1))
    # an operator's row is out of scope: still live and not confirmed
    assert (rows[operator]["retracted_at"], rows[operator]["superseded_by"]) == (None, None)
    assert rows[operator]["last_confirmed_at"] == utc(T0)
    assert world.recorder.seen == [EvidenceChanged(lead_id=lead, evidence_id=earlier)]


# The log lines -----------------------------------------------------------------------------------

STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord("x", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime"}
EXTRACTED_KEYS = {
    "lead_id",
    "run_id",
    "pages_sent",
    "pages_truncated",
    "stored",
    "confirmed",
    "superseded",
    "retracted",
    "discarded",
    "dates_dropped",
    "signals_created",
    "signals_retracted",
    "hq_changed",
}
FINISHED_KEYS = {"lead_id", "run_id", "outcome", "error_code"}
PAGE_WORDS = (HIRING, TWELVE, AI_POST, CHICAGO, TWELVE_WORDS, "Smith Law", "http", "smith-law")


def extras(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in STANDARD_RECORD_KEYS}


def lines(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "nextron.app.research.extraction" and record.getMessage() == event
    ]


def test_the_log_lines_hold_ids_and_counts_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.extraction")
    lead, run_id, pages = crawled(world)
    refused = LlmRefused("cyber", model=HAIKU, usage=LlmUsage(requests=1))

    world.extract(lead, run_id, first_answer(pages))
    world.clock.advance(timedelta(hours=1))
    world.sql("UPDATE crawled_pages SET changed_at = ?", (utc(T0 + timedelta(minutes=30)),))
    world.extract(lead, run_id, refused)

    [ok_run, refused_run] = world.sql(
        "SELECT id FROM extraction_runs WHERE route = 'extract_signals' ORDER BY rowid"
    )
    [extracted] = lines(caplog, "signals_extracted")
    [finished] = lines(caplog, "extraction_finished")
    assert extras(extracted) == {
        "lead_id": lead,
        "run_id": ok_run[0],
        "pages_sent": 3,
        "pages_truncated": 0,
        "stored": 3,
        "confirmed": 0,
        "superseded": 0,
        "retracted": 0,
        "discarded": 0,
        "dates_dropped": 0,
        "signals_created": 1,
        "signals_retracted": 0,
        "hq_changed": True,
    }
    assert set(extras(extracted)) == EXTRACTED_KEYS
    assert extras(finished) == {
        "lead_id": lead,
        "run_id": refused_run[0],
        "outcome": "refused",
        "error_code": "cyber",
    }
    assert set(extras(finished)) == FINISHED_KEYS
    for record in (extracted, finished):
        for value in extras(record).values():
            assert not any(word in str(value) for word in PAGE_WORDS)
    assert not any(
        word in record.getMessage() for record in caplog.records for word in PAGE_WORDS
    )


# Row: No work -----------------------------------------------------------------------------------


def test_without_an_activation_a_no_activation_run_and_no_call(db_engine: Engine) -> None:
    world = build_world(db_engine, prompts=False)
    lead, run_id, _ = crawled(world)

    assert world.extract(lead, run_id) is True

    assert world.calls == []
    assert world.runs(lead) == [("no_activation", None, None, "extract_signals", 0, 0, 0, 0)]


def test_without_a_changed_page_a_no_pages_run_and_no_call(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))
    world.calls.clear()
    world.clock.advance(timedelta(hours=1))

    assert world.extract(lead, run_id) is True

    assert world.calls == []
    assert world.runs(lead)[1] == ("no_pages", None, ACTOR, "extract_signals", 0, 0, 0, 0)


def test_a_purged_page_is_not_sent(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.sql("UPDATE crawled_pages SET text = NULL WHERE url <> ?", (TEAM,))

    world.extract(lead, run_id, ok())

    [(_, blocks, _)] = world.calls
    assert [block.block_id for block in blocks] == ["signal_types", pages[TEAM]]


def spend_the_budget(world: World) -> None:
    world.meter.record(
        AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
    )


def test_when_the_budget_refuses_nothing_is_called_or_written(world: World) -> None:
    lead, run_id, _ = crawled(world)
    spend_the_budget(world)

    assert world.extract(lead, run_id) is False

    assert world.calls == [] and world.runs(lead) == [] and world.events(lead) == []


def test_the_handler_defers_when_the_budget_refuses(world: World) -> None:
    lead, run_id, _ = crawled(world)
    spend_the_budget(world)
    extractions = world.extractions(ScriptedLlm())
    handler = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=lambda: extractions,
        primary=no_jobs,
        test_runs=no_jobs,
    ).get("extract_lead").handler
    payload = {"v": 1, "lead_id": lead, "run_id": run_id}
    job = Job("01JD0000000000000000000099", "extract_lead", Lane.BULK, payload, 1)

    with world.units.open():
        assert handler(job) is DEFERRED

    assert world.runs(lead) == []


@pytest.mark.parametrize("merged", [False, True])
def test_an_unknown_or_merged_lead_is_skipped(
    world: World, merged: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.extraction")
    older = world.lead(web_domain="smith-law-llp.com")
    lead, run_id, _ = crawled(world)
    target = "01KA0000000000000000000000"
    if merged:
        world.transact(lambda: world.discovery.merge(older, lead))
        target = lead

    assert world.extract(target, run_id) is True

    assert world.calls == [] and world.sql("SELECT COUNT(*) FROM extraction_runs") == [(0,)]
    [record] = [r for r in caplog.records if r.getMessage() == "extraction_skipped"]
    assert getattr(record, "reason") == ("merged_lead" if merged else "unknown_lead")


# Row: Bad result --------------------------------------------------------------------------------

USAGE = LlmUsage(requests=1)


@pytest.mark.parametrize(
    ("result", "outcome", "code"),
    [
        (LlmRefused("cyber", model=HAIKU, usage=USAGE), "refused", "cyber"),
        (LlmRefused(None, model=HAIKU, usage=USAGE), "refused", None),
        (LlmTruncated(model=HAIKU, usage=USAGE), "truncated", None),
        (LlmInvalid(model=HAIKU, usage=USAGE), "invalid", None),
        (
            LlmFailed(False, "llm_bad_request", 400, model=HAIKU, usage=USAGE),
            "failed",
            "llm_bad_request",
        ),
    ],
)
def test_a_bad_result_records_its_outcome_stores_nothing_and_completes(
    world: World, result: LlmResult, outcome: str, code: str | None
) -> None:
    lead, run_id, _ = crawled(world)

    assert world.extract(lead, run_id, result) is True

    assert world.runs(lead) == [(outcome, code, ACTOR, "extract_signals", 3, 0, 0, 0)]
    assert (world.evidence(lead), world.signals(lead)) == ([], [])
    assert world.hq(lead) == (None, None)


# Row: Retryable ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retryable", "code"), [(True, "llm_unavailable"), (False, "llm_unknown_route")]
)
def test_an_adapter_error_records_a_failed_run_and_is_raised_again(
    world: World, retryable: bool, code: str
) -> None:
    lead, run_id, _ = crawled(world)
    error = AdapterError(retryable, code, "the API is unavailable")

    with pytest.raises(AdapterError) as raised:
        world.extract(lead, run_id, error)

    assert raised.value is error
    assert world.runs(lead) == [("failed", code, ACTOR, "extract_signals", 3, 0, 0, 0)]
    assert world.evidence(lead) == []


# Row: Merged meanwhile --------------------------------------------------------------------------


class MergingLlm:
    """Answers like ``ScriptedLlm`` after merging the Lead into another, as another job would."""

    def __init__(self, world: World, survivor: str, loser: str, answer: LlmResult) -> None:
        self.world, self.survivor, self.loser = world, survivor, loser
        self.scripted = ScriptedLlm(answer)

    def run(
        self, route_tuple: RouteTuple, inputs: Sequence[UntrustedBlock], urgency: Urgency
    ) -> LlmResult:
        contextvars.Context().run(
            self.world.transact, lambda: self.world.discovery.merge(self.survivor, self.loser)
        )
        return self.scripted.run(route_tuple, inputs, urgency)


def test_a_lead_merged_during_the_call_gets_a_lead_merged_run_and_nothing_stored(
    world: World,
) -> None:
    survivor = world.lead(web_domain="smith-law-llp.com")
    lead, run_id, pages = crawled(world)

    world.extract(lead, run_id, llm=MergingLlm(world, survivor, lead, first_answer(pages)))

    assert world.runs(lead) == [("lead_merged", None, ACTOR, "extract_signals", 3, 0, 0, 0)]
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(0,)]
    assert world.events(lead) == []


# The headquarters re-resolves the Lead's Contacts -----------------------------------------------


def test_a_new_headquarters_zone_resolves_the_leads_contacts_again(world: World) -> None:
    lead, run_id, pages = crawled(world)
    contact = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name="Jane Doe", role="Managing Partner"),
            source=ProvenanceSource.WEBSITE,
            detail=TEAM,
            retrieved_at=T0,
        )
    ).contact_id
    query = "SELECT tz, tz_basis FROM contacts WHERE id = ?"
    assert world.sql(query, (contact,)) == [("America/New_York", "us_unknown_fallback")]

    world.extract(lead, run_id, first_answer(pages))

    assert world.sql(query, (contact,)) == [("America/Chicago", "lead_hq")]


# Reparenting --------------------------------------------------------------------------------------


def test_a_merge_moves_evidence_and_signals_but_not_runs_or_the_headquarters(world: World) -> None:
    survivor = world.lead(web_domain="smith-law-llp.com", country="GB")
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))

    world.transact(lambda: world.discovery.merge(survivor, lead))

    assert world.sql("SELECT DISTINCT lead_id FROM evidence") == [(survivor,)]
    assert world.sql("SELECT DISTINCT lead_id FROM signals") == [(survivor,)]
    assert world.sql("SELECT DISTINCT lead_id FROM extraction_runs") == [(lead,)]
    assert world.hq(survivor) == (None, None)
    assert world.hq(lead)[0] == "America/Chicago"


# The extract_lead job ---------------------------------------------------------------------------


def test_a_crawl_that_stored_pages_enqueues_extract_lead_once_per_run(world: World) -> None:
    lead = world.lead()
    world.site()
    first = world.crawl(lead)
    # the pages are near-empty, so the extraction waits for their render (Story 2.6)
    assert world.jobs("extract_lead") == []
    world.render(lead, first)
    second = recrawl(world, lead)
    world.render(lead, second)

    assert world.jobs("extract_lead") == [
        (
            f"extract_lead:{lead}:{revision}",
            f"domain:{SITE}",
            json.dumps({"lead_id": lead, "run_id": run_id, "v": 1}, separators=(",", ":")),
            "bulk",
        )
        for revision, run_id in ((1, first), (2, second))
    ]


def test_a_crawl_that_stored_nothing_enqueues_no_extraction(world: World) -> None:
    lead = world.lead()
    world.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow:\n")
    world.crawler.pages[HOME] = html_page(HOME, status=404)

    world.crawl(lead)

    assert world.jobs("extract_lead") == []


def test_the_extract_lead_job_runs_the_extraction(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.render(lead, run_id)
    extractions = world.extractions(ScriptedLlm(first_answer(pages)))
    handler = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=lambda: extractions,
        primary=no_jobs,
        test_runs=no_jobs,
    ).get("extract_lead").handler
    [(_, _, payload, _)] = world.jobs("extract_lead")
    job = Job("01JD0000000000000000000099", "extract_lead", Lane.BULK, json.loads(payload), 1)

    with world.units.open():
        assert handler(job) is None

    assert world.runs(lead)[0][0] == "ok" and len(world.evidence(lead)) == 3


# The Lead page's view ---------------------------------------------------------------------------


def test_lead_signals_reads_the_clock_once_and_names_the_types(world: World) -> None:
    lead, run_id, pages = crawled(world, news=NEWS_HTML)
    world.extract(
        lead,
        run_id,
        ok(
            signals=[
                signal(pages[TEAM], HIRING),
                signal(pages[NEWS], OPENED, "law_expansion", "2026-10-14"),
            ],
            profile_facts=[fact(pages[ABOUT], CHICAGO, "Chicago")],
        ),
    )
    reads: list[datetime] = []

    class CountingClock:
        def now(self) -> datetime:
            reads.append(T0 + timedelta(days=40))
            return reads[-1]

    view = world.read(lambda: LeadSignals(CountingClock()).view(lead))

    assert len(reads) == 1
    assert view.hq_tz == "America/Chicago"
    hiring = "Hiring intake, legal-ops, knowledge-management, or innovation staff"
    expansion = "New office, merger, or practice-area expansion"
    assert sorted((s.name, s.age, s.live, s.signal.event_date) for s in view.signals) == [
        (hiring, 40, True, None),
        (expansion, 75, True, date(2026, 10, 14)),
    ]
    assert {row.evidence.quote: row.supports for row in view.evidence} == {
        HIRING: (hiring,),
        OPENED: (expansion,),
        CHICAGO: (),
    }
    empty = world.read(lambda: LeadSignals(world.clock).view("not-a-ulid"))
    assert (empty.hq_tz, empty.signals, empty.evidence) == (None, (), ())
