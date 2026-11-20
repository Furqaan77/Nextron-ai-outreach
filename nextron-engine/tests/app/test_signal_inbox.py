"""The Signal Inbox end to end (Story 2.16): pasted items become researched Leads on the
interactive lane, with real services on a temporary database, port fakes and a frozen clock."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.discovery.legal_form import LegalFormChecks
from nextron.app.discovery.signal_inbox import (
    ReprocessChoice,
    ReprocessOutcome,
    SignalInbox,
    lead_id_of,
)
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import Playbook
from nextron.app.research.api import Research, WebsiteCrawls
from nextron.app.research.batches import ResearchBatches
from nextron.app.research.extraction import SignalExtractions
from nextron.app.research.finding import EmailFinding
from nextron.app.research.job_boards import JobBoards
from nextron.app.research.people import PeopleExtractions, PrimaryContacts
from nextron.app.scoring.api import Scoring
from nextron.app.scoring.lead_scores import LeadScores
from nextron.app.setup.api import Setup
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.ai import Urgency
from nextron.domain.budget import AiCall
from nextron.domain.job import DEFERRED, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.markets import validate_markets
from nextron.domain.segments import validate_segments
from nextron.domain.signal_inbox_item import SubmittedItem
from nextron.jobs import handlers
from nextron.ports.errors import AdapterError
from nextron.ports.llm import (
    BatchSucceeded,
    LlmInvalid,
    LlmOk,
    LlmRefused,
    LlmResult,
    LlmTruncated,
    LlmUsage,
)
from tests.app.test_signal_extraction import HAIKU, MARKETS, PLAYBOOK, SEGMENTS
from tests.fakes.clock import FrozenClock
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder
from tests.fakes.job_board import ScriptedJobBoards
from tests.fakes.llm import ScriptedLlm
from tests.fakes.registry import ScriptedRegistry
from tests.fakes.renderer import ScriptedRenderer, rendered_page
from tests.fakes.usage_meter import InMemoryUsageMeter

T0 = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
SITE = "northgate-supply.co.uk"
ORIGIN = f"https://{SITE}"
HOME = f"{ORIGIN}/"
TEAM = f"{ORIGIN}/team"
ROBOTS = f"{ORIGIN}/robots.txt"
HOME_HTML = '<html><body><h1>Northgate Supply</h1><a href="/team">Our team</a></body></html>'
TEAM_HTML = "<p>Our people keep the warehouse moving.</p>"
POST_URL = "https://www.linkedin.com/posts/dana-cole-erp-123"
COO = "Dana Cole"
QUOTE = "Every morning two of my team re-key forty PDF purchase orders into our ERP by hand."
POST = f"{COO}, COO at Northgate Supply ({SITE})\n{QUOTE} There has to be a better way."
INBOX_ACTOR = "ai:signal_inbox_parse@v1"
LONG_TEXT = "We supply packaging and warehouse logistics across the north of England. " * 10


def parse(**overrides: Any) -> LlmOk:
    parsed: dict[str, Any] = {
        "segment": "ops_automation",
        "signal_type": "ops_rekeying_post",
        "website": SITE,
        "author_name": COO,
        "author_role": "COO",
        "event_date": None,
        "quote": QUOTE,
    }
    parsed.update(overrides)
    return LlmOk(parsed, model=HAIKU, usage=LlmUsage(requests=1))


NO_SIGNALS = LlmOk(
    {"signals": [], "profile_facts": [], "size_clues": [], "job_postings": []},
    model=HAIKU,
    usage=LlmUsage(requests=1),
)
NO_PEOPLE = LlmOk({"people": []}, model=HAIKU, usage=LlmUsage(requests=1))


def no_jobs() -> Any:
    raise AssertionError("this job doesn't run here")


@dataclass
class World:
    """Every research service on real units of work, with the job registry's real handlers."""

    engine: Engine
    units: UnitOfWorkFactory
    clock: FrozenClock
    meter: InMemoryUsageMeter
    llm: ScriptedLlm
    crawler: ScriptedCrawler
    renderer: ScriptedRenderer
    job_types: Any = None
    queue: JobQueue = field(init=False)
    discovery: Discovery = field(init=False)
    research: Research = field(init=False)
    inbox: SignalInbox = field(init=False)
    batches: ResearchBatches = field(init=False)  # Story 4.17: the bulk lane's AI work
    ran: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        clock, meter, llm = self.clock, self.meter, self.llm
        self.job_types = main.build_job_types(
            alerts=no_jobs,
            backups=no_jobs,
            restore_tests=no_jobs,
            recovery=no_jobs,
            contact_markets=no_jobs,
            crawls=lambda: crawls,
            discovery=lambda: self.discovery,
            research=lambda: self.research,
            extractions=lambda: extractions,
            primary=lambda: primary,
            test_runs=no_jobs,
            job_boards=lambda: boards,
            legal_forms=lambda: legal_forms,
            scores=lambda: scores,
            finding=lambda: finding,
            signal_inbox=lambda: self.inbox,
            ai_batches=lambda: (self.batches,),
        )
        self.queue = queue = JobQueue(clock, self.job_types)
        self.discovery = Discovery(clock, Pipeline(clock), queue)
        self.research = research = Research(clock)
        crawls = WebsiteCrawls(clock, meter, self.crawler, self.renderer, queue)
        people = PeopleExtractions(clock, meter, llm, research, queue)
        extractions = SignalExtractions(clock, meter, llm, research, people)
        self.batches = ResearchBatches(clock, meter, llm, extractions, people)
        primary = PrimaryContacts(clock, research, queue)
        boards = JobBoards(clock, meter, ScriptedJobBoards(), self.crawler, queue)
        legal_forms = LegalFormChecks(
            clock, meter, ScriptedRegistry(), self.discovery, research, queue
        )
        finding = EmailFinding(
            clock,
            meter,
            research,
            queue,
            domain_search=ScriptedDomainSearch(),
            hunter=ScriptedFinder(),
            prospeo=ScriptedFinder(),
        )
        scores = LeadScores(clock, Scoring(clock, Pipeline(clock), research), queue)
        self.inbox = SignalInbox(clock, meter, llm, self.discovery, research, queue)

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def submit(self, *items: SubmittedItem) -> tuple[str, ...]:
        with self.units.open():
            return self.inbox.submit(items)

    def site(self, *, long: bool = False) -> None:
        """The organization's site: near-empty pages (rendered), or with ``long`` pages whose
        text needs no render."""
        home, team = (HOME_HTML, TEAM_HTML)
        if long:
            home = home.replace("</body>", f"<p>{LONG_TEXT}</p></body>")
            team = f"<p>{LONG_TEXT}</p>{team}"
        self.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow:\n")
        self.crawler.pages[HOME] = html_page(HOME, home)
        self.crawler.pages[TEAM] = html_page(TEAM, team)
        if not long:
            for url, html in ((HOME, home), (TEAM, team)):
                self.renderer.pages[url] = rendered_page(url, html)  # near-empty: rendered

    def run_jobs(self, *, limit: int | None = None) -> list[tuple[str, str]]:
        """Claim and run every runnable job (or the first ``limit``), on any lane, through its
        registered handler; each job's ``(type, lane)``, in order. A deferred job is put back
        and not run again."""
        ran: list[tuple[str, str]] = []
        for _ in range(50 if limit is None else limit):
            claim = None
            for lane in (Lane.REALTIME, Lane.INTERACTIVE, Lane.BULK):
                with self.units.open():
                    claim = self.queue.claim(lane)
                if claim is not None:
                    break
            if claim is None:
                self.ran.extend(ran)
                return ran
            ran.append((claim.job.type, str(claim.job.lane)))
            with self.units.open():
                result = self.job_types.get(claim.job.type).handler(claim.job)
            with self.units.open():
                if result is DEFERRED:
                    self.queue.defer(claim)
                else:
                    self.queue.complete(claim)
        if limit is not None:
            self.ran.extend(ran)
            return ran
        raise AssertionError("the chain didn't settle")

    def item(self, item_id: str) -> dict[str, Any]:
        columns = (
            "status, flag_reason, lead_id, processed_at, parsed_segment, parsed_signal_type, "
            "parsed_website, parsed_author_name, parsed_author_role, parsed_event_date, "
            "parsed_quote, extracted_by"
        )
        [row] = self.sql(f"SELECT {columns} FROM signal_inbox_items WHERE id = ?", (item_id,))
        names = [name.strip() for name in columns.split(",")]
        return dict(zip(names, row, strict=True))

    def item_events(self, item_id: str) -> list[tuple[Any, ...]]:
        return self.sql(
            "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
            "WHERE entity = 'signal_inbox_items' AND entity_id = ? ORDER BY rowid",
            (item_id,),
        )

    def jobs(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT type, lane, payload FROM jobs ORDER BY rowid")

    def leads(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT id, name, web_domain, segment_id FROM leads ORDER BY rowid")

    def segment_id(self, key: str) -> str:
        [(found,)] = self.sql("SELECT id FROM segments WHERE key = ?", (key,))
        return str(found)


def build_world(engine: Engine, *script: LlmResult | AdapterError) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SEGMENTS.read_text(encoding="utf-8")))
    markets = validate_markets(seed_yaml.parse(MARKETS.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    with units.open():
        Setup(clock).sync_markets(markets)
    files, defaults = main.load_prompts()
    with units.open():
        Playbook(clock).sync_prompts(files, defaults, PLAYBOOK)
    return World(
        engine,
        units,
        clock,
        InMemoryUsageMeter(clock),
        ScriptedLlm(*script),
        ScriptedCrawler(),
        ScriptedRenderer(),
    )


def item(text: str = POST, **fields: Any) -> SubmittedItem:
    return SubmittedItem(fields.pop("url", POST_URL), text, **fields)


# The AC's COO chain -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("company", "lead_name"), [(None, SITE), ("Northgate Supply", "Northgate Supply")]
)
def test_a_coo_post_becomes_a_tier_a_lead_on_the_interactive_lane(
    db_engine: Engine, company: str | None, lead_name: str
) -> None:
    world = build_world(db_engine, parse(), NO_SIGNALS, NO_PEOPLE)
    world.site()
    [item_id] = world.submit(item(company=company))

    # stored pending, its job on the interactive lane, in the same transaction
    assert world.item(item_id)["status"] == "pending"
    assert [(kind, lane) for kind, lane, _ in world.jobs()] == [
        ("process_signal_item", "interactive")
    ]
    ran = world.run_jobs()

    assert [kind for kind, _ in ran] == [
        "process_signal_item",
        "enrich_lead",
        "render_pages",
        "extract_lead",
        "check_job_boards",
        "check_legal_form",
        "select_primary_contact",
        "find_contact_email",
        "score_lead",
    ]
    assert {lane for kind, lane in ran if kind != "render_pages"} == {"interactive"}
    assert dict(ran)["render_pages"] == "bulk"
    # every AI call of the chain is at urgency soon
    assert [(call[0].route, call[2]) for call in world.llm.calls] == [
        ("signal_inbox_parse", Urgency.SOON),
        ("extract_signals", Urgency.SOON),
        ("extract_people", Urgency.SOON),
    ]
    [(lead_id, name, web_domain, segment_id)] = world.leads()
    assert (name, web_domain) == (lead_name, SITE)
    assert segment_id == world.segment_id("ops_automation")
    stored = world.item(item_id)
    assert stored["status"] == "processed"
    assert stored["lead_id"] == lead_id
    assert stored["processed_at"] is not None
    assert stored["extracted_by"] == INBOX_ACTOR
    # the Quote Evidence and the Tier 1 Event first seen at the submission time
    [evidence] = world.sql(
        "SELECT type, quote, source, source_url, retrieved_at, extracted_by FROM evidence "
        "WHERE lead_id = ?",
        (lead_id,),
    )
    assert evidence[:4] == ("quote", QUOTE, "signal_inbox", POST_URL)
    assert evidence[5] == INBOX_ACTOR
    [signal] = world.sql(
        "SELECT signal_type, signal_class, tier, event_date, first_seen_at, retracted_at "
        "FROM signals WHERE lead_id = ?",
        (lead_id,),
    )
    assert signal[:4] == ("ops_rekeying_post", "event", 1, "2026-11-18")
    assert signal[4].startswith("2026-11-18T09:00:00")
    assert signal[5] is None
    # the COO is the Primary Contact, with Provenance source signal_inbox
    [(contact_id, contact_name, role, is_primary)] = world.sql(
        "SELECT id, name, role, is_primary FROM contacts WHERE lead_id = ?", (lead_id,)
    )
    assert (contact_name, role, is_primary) == (COO, "COO", 1)
    [(source, detail)] = world.sql(
        "SELECT source_kind, source_detail FROM provenance_records WHERE contact_id = ?",
        (contact_id,),
    )
    assert (source, detail) == ("signal_inbox", POST_URL)
    [(tier,)] = world.sql("SELECT tier FROM leads WHERE id = ?", (lead_id,))
    assert tier == "A"
    # the item's transitions, IDs only
    assert world.item_events(item_id) == [
        (None, "pending", "submitted", None, "operator"),
        ("pending", "processed", "parsed", lead_id, "system:discovery"),
    ]
    # the pasted URL is never fetched by any job
    fetched = [call.url for call in world.crawler.calls] + world.renderer.urls()
    assert POST_URL not in fetched
    assert all(SITE in url for url in fetched)


def test_a_site_that_needs_no_render_keeps_the_whole_chain_interactive(
    db_engine: Engine,
) -> None:
    world = build_world(db_engine, parse(), NO_SIGNALS, NO_PEOPLE)
    world.site(long=True)
    world.submit(item())

    ran = world.run_jobs()

    kinds = [kind for kind, _ in ran]
    assert "render_pages" not in kinds
    assert kinds[:3] == ["process_signal_item", "enrich_lead", "extract_lead"]
    assert kinds[-1] == "score_lead"
    assert {lane for _, lane in ran} == {"interactive"}
    assert world.renderer.urls() == []
    assert {lane for _, lane, _ in world.jobs()} == {"interactive"}
    assert [call[0].route for call in world.llm.calls] == [
        "signal_inbox_parse",
        "extract_signals",
        "extract_people",
    ]
    assert {call[2] for call in world.llm.calls} == {Urgency.SOON}


def test_the_parse_sees_the_catalogue_then_the_item_with_author_and_company(
    db_engine: Engine,
) -> None:
    world = build_world(db_engine, parse())
    [item_id] = world.submit(item(author="Dana Cole", company="Northgate Supply"))

    world.run_jobs(limit=1)

    [(route_tuple, blocks, urgency)] = world.llm.calls
    assert route_tuple.route == "signal_inbox_parse" and urgency is Urgency.SOON
    assert [(block.subject, block.source, block.block_id) for block in blocks] == [
        (f"signal_item:{item_id}", "signal_types", "signal_types"),
        (f"signal_item:{item_id}", "signal_inbox", item_id),
    ]
    assert "segment ops_automation: Ops-Automation" in blocks[0].text
    assert "segment law_private_ai" in blocks[0].text
    assert blocks[1].text == f"{POST}\nAuthor: Dana Cole\nCompany: Northgate Supply"


def test_a_stated_event_date_dates_the_event(db_engine: Engine) -> None:
    text = f"{POST} We counted on 3 November 2026."
    world = build_world(db_engine, parse(event_date="2026-11-03"))
    [item_id] = world.submit(item(text))

    world.run_jobs(limit=1)

    assert world.item(item_id)["parsed_event_date"] == "2026-11-03"
    [(event_date,)] = world.sql("SELECT event_date FROM signals")
    assert event_date == "2026-11-03"


# Flags ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        LlmRefused("policy", model=HAIKU, usage=LlmUsage(requests=1)),
        LlmInvalid(model=HAIKU, usage=LlmUsage(requests=1)),
        LlmTruncated(model=HAIKU, usage=LlmUsage(requests=1)),
        AdapterError(False, "llm_bad_request", "refused"),
        LlmOk({"segment": 12}, model=HAIKU, usage=LlmUsage(requests=1)),
    ],
)
def test_a_failed_parse_flags_parse_failed(db_engine: Engine, answer: Any) -> None:
    world = build_world(db_engine, answer)
    [item_id] = world.submit(item())

    assert world.run_jobs() == [("process_signal_item", "interactive")]

    assert world.item(item_id)["status"] == "flagged"
    assert world.item(item_id)["flag_reason"] == "parse_failed"
    assert world.leads() == []
    assert world.item_events(item_id)[-1] == (
        "pending",
        "flagged",
        "flagged",
        None,
        "system:discovery",
    )


def test_a_retryable_failure_is_raised_and_the_item_stays_pending(db_engine: Engine) -> None:
    world = build_world(db_engine, AdapterError(True, "llm_overloaded", "busy"))
    [item_id] = world.submit(item())

    with world.units.open(), pytest.raises(AdapterError):
        world.inbox.process_signal_item(item_id)

    assert world.item(item_id)["status"] == "pending"


def test_a_retryable_failure_on_the_last_attempt_flags_parse_failed(db_engine: Engine) -> None:
    world = build_world(db_engine, AdapterError(True, "llm_overloaded", "busy"))
    [item_id] = world.submit(item())
    with world.units.open():
        claim = world.queue.claim(Lane.INTERACTIVE)
    assert claim is not None
    last = replace(claim.job, attempts=handlers.PROCESS_SIGNAL_ITEM_RETRIES + 1)

    with world.units.open():
        assert world.job_types.get(last.type).handler(last) is None

    stored = world.item(item_id)
    assert (stored["status"], stored["flag_reason"]) == ("flagged", "parse_failed")
    assert world.item_events(item_id)[-1] == (
        "pending",
        "flagged",
        "flagged",
        None,
        "system:discovery",
    )
    assert world.leads() == []


def test_no_acceptable_website_flags_organization_unknown(db_engine: Engine) -> None:
    world = build_world(db_engine, parse(website="another-firm.co.uk"))
    [item_id] = world.submit(item())

    world.run_jobs()

    stored = world.item(item_id)
    assert (stored["status"], stored["flag_reason"]) == ("flagged", "organization_unknown")
    assert stored["parsed_quote"] == QUOTE  # the accepted fields are kept for a reprocess
    assert stored["parsed_website"] is None
    assert world.leads() == []


def test_an_unseeded_segment_flags_segment_unknown(db_engine: Engine) -> None:
    world = build_world(db_engine, parse(segment="ops_made_up"))
    [item_id] = world.submit(item())

    world.run_jobs()

    stored = world.item(item_id)
    assert (stored["status"], stored["flag_reason"]) == ("flagged", "segment_unknown")
    assert stored["parsed_website"] == SITE


def test_a_quote_not_in_the_text_stores_no_evidence_but_the_rest_applies(
    db_engine: Engine,
) -> None:
    world = build_world(db_engine, parse(quote="We re-key everything by hand"))
    [item_id] = world.submit(item())

    world.run_jobs(limit=1)

    assert world.item(item_id)["status"] == "processed"
    assert len(world.leads()) == 1
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM signals") == [(0,)]
    assert world.sql("SELECT name FROM contacts") == [(COO,)]


def test_a_new_lead_on_a_suppressed_domain_flags_the_item_and_writes_nothing_else(
    db_engine: Engine,
) -> None:
    world = build_world(db_engine, parse())
    with world.units.open():
        Compliance(world.clock).add_value(SITE)
    [item_id] = world.submit(item())

    world.run_jobs()

    stored = world.item(item_id)
    assert (stored["status"], stored["flag_reason"]) == ("flagged", "organization_suppressed")
    assert world.leads() == []
    assert world.sql("SELECT COUNT(*) FROM contacts") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM evidence") == [(0,)]


def test_the_budget_defers_the_job_and_the_item_stays_pending(db_engine: Engine) -> None:
    world = build_world(db_engine)
    world.meter.record(
        AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
    )
    [item_id] = world.submit(item())

    with world.units.open():
        assert world.inbox.process_signal_item(item_id) is False
    [(_, claim_lane, _)] = world.jobs()
    with world.units.open():
        claim = world.queue.claim(Lane.INTERACTIVE)
    assert claim is not None
    with world.units.open():
        assert world.job_types.get(claim.job.type).handler(claim.job) is DEFERRED

    assert claim_lane == "interactive"
    assert world.item(item_id)["status"] == "pending"
    assert world.llm.calls == []


def test_an_item_no_longer_pending_is_skipped(db_engine: Engine) -> None:
    world = build_world(db_engine, parse(website="another-firm.co.uk"))
    [item_id] = world.submit(item())
    world.run_jobs()

    with world.units.open():
        assert world.inbox.process_signal_item(item_id) is True
    with world.units.open():
        assert world.inbox.process_signal_item("not-a-ulid") is True

    assert len(world.llm.calls) == 1


# Existing Leads ---------------------------------------------------------------------------------


def test_an_existing_lead_is_returned_with_its_own_segment(db_engine: Engine) -> None:
    world = build_world(db_engine, parse())
    law = world.segment_id("law_private_ai")
    identity = LeadIdentity("Northgate LLP", law, web_domain=SITE)
    existing = world.transact(
        lambda: world.discovery.upsert_lead(identity, LeadSource.CSV)
    ).lead_id
    world.sql("DELETE FROM jobs")  # the CSV Lead's own crawl isn't under test
    [item_id] = world.submit(item())

    world.run_jobs(limit=1)

    assert [(lead_id, segment) for lead_id, _, _, segment in world.leads()] == [(existing, law)]
    assert world.item(item_id)["lead_id"] == existing
    # the Quote Evidence is stored; the Signal isn't, its type isn't the Lead's Segment's
    assert world.sql("SELECT source FROM evidence") == [("signal_inbox",)]
    assert world.sql("SELECT COUNT(*) FROM signals") == [(0,)]
    [(kind, lane, _)] = world.sql(
        "SELECT type, lane, payload FROM jobs WHERE type = 'enrich_lead'"
    )
    assert lane == "interactive"


# Reprocess --------------------------------------------------------------------------------------


def flagged(world: World) -> str:
    [item_id] = world.submit(item())
    world.run_jobs()
    assert world.item(item_id)["status"] == "flagged"
    return item_id


def reprocess(world: World, item_id: str, **choice: str) -> ReprocessOutcome:
    with world.units.open():
        return world.inbox.reprocess(item_id, ReprocessChoice(**choice))


def test_reprocess_with_a_website_and_segment_upserts_the_lead(db_engine: Engine) -> None:
    world = build_world(db_engine, parse(website=None))
    item_id = flagged(world)
    world.sql("DELETE FROM jobs")

    outcome = reprocess(world, item_id, website=f"https://www.{SITE}/", segment="ops_automation")

    assert outcome is ReprocessOutcome.PROCESSED
    [(lead_id, _, web_domain, _)] = world.leads()
    assert web_domain == SITE
    stored = world.item(item_id)
    assert (stored["status"], stored["flag_reason"], stored["lead_id"]) == (
        "processed",
        None,
        lead_id,
    )
    assert world.item_events(item_id)[-1] == (
        "flagged",
        "processed",
        "reprocessed_by_operator",
        lead_id,
        "operator",
    )
    assert world.sql("SELECT signal_type FROM signals") == [("ops_rekeying_post",)]
    assert world.sql("SELECT type, lane FROM jobs") == [("enrich_lead", "interactive")]
    assert len(world.llm.calls) == 1  # no second AI call


def test_reprocess_with_an_existing_lead_link_resolves_a_merged_lead(db_engine: Engine) -> None:
    world = build_world(db_engine, parse(website=None))
    item_id = flagged(world)
    ops = world.segment_id("ops_automation")
    older = world.transact(
        lambda: world.discovery.upsert_lead(
            LeadIdentity("Older", ops, web_domain="older-firm.co.uk"), LeadSource.CSV
        )
    ).lead_id
    world.clock.advance(timedelta(seconds=1))
    newer = world.transact(
        lambda: world.discovery.upsert_lead(
            LeadIdentity("Newer", ops, web_domain="newer-firm.co.uk"), LeadSource.CSV
        )
    ).lead_id
    assert older is not None and newer is not None
    world.transact(lambda: world.discovery.merge(older, newer))

    outcome = reprocess(world, item_id, lead=f"http://127.0.0.1:8765/leads/{newer}?tab=x")

    assert outcome is ReprocessOutcome.PROCESSED
    assert world.item(item_id)["lead_id"] == older


def test_a_merge_moves_a_processed_item_to_the_survivor(db_engine: Engine) -> None:
    world = build_world(db_engine, parse())
    ops = world.segment_id("ops_automation")
    survivor = world.transact(
        lambda: world.discovery.upsert_lead(
            LeadIdentity("Older", ops, web_domain="older-firm.co.uk"), LeadSource.CSV
        )
    ).lead_id
    world.sql("DELETE FROM jobs")
    world.clock.advance(timedelta(seconds=1))
    [item_id] = world.submit(item())
    world.run_jobs(limit=1)
    merged = world.item(item_id)["lead_id"]
    assert survivor is not None and merged not in (None, survivor)

    world.transact(lambda: world.discovery.merge(survivor, merged))

    assert world.item(item_id)["lead_id"] == survivor


@pytest.mark.parametrize(
    ("choice", "outcome"),
    [
        ({}, ReprocessOutcome.INCOMPLETE),
        ({"website": SITE}, ReprocessOutcome.INCOMPLETE),
        ({"lead": "01JUNKJUNKJUNKJUNKJUNKJUNK", "website": SITE}, ReprocessOutcome.INCOMPLETE),
        ({"lead": "01ARZ3NDEKTSV4RRFFQ69G5FAV"}, ReprocessOutcome.UNKNOWN_LEAD),
        ({"lead": "nonsense"}, ReprocessOutcome.UNKNOWN_LEAD),
        (
            {"website": "linkedin.com", "segment": "ops_automation"},
            ReprocessOutcome.WEBSITE_REFUSED,
        ),
        ({"website": "yelp.com", "segment": "ops_automation"}, ReprocessOutcome.WEBSITE_REFUSED),
        ({"website": "not a site", "segment": "ops_automation"}, ReprocessOutcome.WEBSITE_REFUSED),
        ({"website": SITE, "segment": "ops_unknown"}, ReprocessOutcome.UNKNOWN_SEGMENT),
    ],
)
def test_a_refused_reprocess_leaves_the_item_flagged_and_unchanged(
    db_engine: Engine, choice: dict[str, str], outcome: ReprocessOutcome
) -> None:
    world = build_world(db_engine, parse(website=None))
    item_id = flagged(world)
    before = world.item(item_id)
    events = world.item_events(item_id)

    assert reprocess(world, item_id, **choice) is outcome

    assert world.item(item_id) == before
    assert world.item_events(item_id) == events
    assert world.leads() == []


def test_reprocess_refuses_a_suppressed_organization(db_engine: Engine) -> None:
    world = build_world(db_engine, parse(website=None))
    item_id = flagged(world)
    with world.units.open():
        Compliance(world.clock).add_value(SITE)

    outcome = reprocess(world, item_id, website=SITE, segment="ops_automation")

    assert outcome is ReprocessOutcome.SUPPRESSED
    assert world.item(item_id)["status"] == "flagged"
    assert world.leads() == []


def test_only_a_flagged_item_can_be_reprocessed(db_engine: Engine) -> None:
    world = build_world(db_engine, parse())
    [item_id] = world.submit(item())

    assert reprocess(world, item_id, website=SITE, segment="ops_automation") is (
        ReprocessOutcome.NOT_FLAGGED
    )
    assert reprocess(world, "01ARZ3NDEKTSV4RRFFQ69G5FAV", website=SITE, segment="x") is (
        ReprocessOutcome.UNKNOWN_ITEM
    )


def test_a_lead_field_takes_an_id_or_a_lead_page_link() -> None:
    lead = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    assert lead_id_of(f" {lead} ") == lead
    assert lead_id_of(f"http://127.0.0.1:8765/leads/{lead}") == lead
    assert lead_id_of(f"http://127.0.0.1:8765/leads/{lead}/contacts#x") == lead
    assert lead_id_of("http://127.0.0.1:8765/leads/") is None
    assert lead_id_of("Acme") is None


# The bulk chain stays as it was -----------------------------------------------------------------


def test_a_csv_chain_stays_on_the_bulk_lane_at_urgency_bulk(db_engine: Engine) -> None:
    world = build_world(db_engine, NO_SIGNALS, NO_PEOPLE)
    world.site()
    ops = world.segment_id("ops_automation")
    world.transact(
        lambda: world.discovery.upsert_lead(
            LeadIdentity("Northgate Supply", ops, web_domain=SITE), LeadSource.CSV
        )
    )

    ran = world.run_jobs()

    # Story 4.17: on the bulk lane extract_lead queues both steps' items and calls nothing; the
    # daily submit sends them as Message Batches and the poll applies the ended results
    assert [kind for kind, _ in ran] == ["enrich_lead", "render_pages", "extract_lead"]
    assert world.llm.calls == []
    with world.units.open():
        assert world.batches.submit_bulk() == 2
    custom = dict(world.sql("SELECT route, custom_id FROM ai_batch_items"))
    world.llm.end_batch(
        "msgbatch_fake_1", {custom["extract_signals"]: BatchSucceeded(NO_SIGNALS)}
    )
    world.llm.end_batch("msgbatch_fake_2", {custom["extract_people"]: BatchSucceeded(NO_PEOPLE)})
    with world.units.open():
        assert world.batches.poll() == 2
    ran += world.run_jobs()

    assert [kind for kind, _ in ran] == [
        "enrich_lead",
        "render_pages",
        "extract_lead",
        "check_job_boards",
        "check_legal_form",
        "select_primary_contact",
        "score_lead",
    ]
    assert {lane for _, lane in ran} == {"bulk"}
    assert world.llm.calls == []  # no direct call: both steps went through the batches
    assert all("chain_lane" not in payload for _, _, payload in world.jobs())


def test_submission_stores_each_item_with_one_job_in_one_transaction(db_engine: Engine) -> None:
    world = build_world(db_engine)

    ids = world.submit(item(), item("Second post", url="https://example.org/2", company="Acme"))

    assert len(ids) == 2
    rows = world.sql(
        "SELECT id, url, author, company, status, submitted_at FROM signal_inbox_items "
        "ORDER BY rowid"
    )
    assert [row[1:5] for row in rows] == [
        (POST_URL, None, None, "pending"),
        ("https://example.org/2", None, "Acme", "pending"),
    ]
    assert all(row[5].startswith("2026-11-18T09:00:00") for row in rows)
    jobs = world.sql("SELECT type, lane, payload FROM jobs ORDER BY rowid")
    assert [(kind, lane) for kind, lane, _ in jobs] == [
        ("process_signal_item", "interactive"),
        ("process_signal_item", "interactive"),
    ]
    assert jobs[0][2] == f'{{"item_id":"{ids[0]}","v":1}}'
