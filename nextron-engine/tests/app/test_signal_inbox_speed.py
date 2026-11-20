"""Story 4.16's speed test (FR-8, NFR-30, AD-8, AD-10): one Signal Inbox item submitted with
"Draft Step 1 when ready" and idle lanes becomes revision 1 of a Step 1 Draft with a grounding
result within 15 minutes, every job on the interactive lane and every AI call at ``soon`` or
``interactive``.

The world is ``tests.app.test_enroll``'s (a temporary database at head, the real subscriber
registry, the shipped seeds, one Sending Inbox) with the footer settings saved, the inbox warmed
up, Ops-Automation x US active, the ``draft_sequence`` and ``grounding_check`` tuples activated
through the real 4.5 gate path, every port a fake, and a site whose pages need no render. The
runner claims and runs one job at a time through the real job registry and advances the
``FrozenClock`` by 60 s per job (a conservative per-job latency).

The organization is already a US Lead (as a CSV import would leave it): a Signal Inbox Lead
created from a post has no country, so its author's Contact has no Market and ``enroll`` would
refuse it (``missing_tz_or_market``; see the spec's overnight decisions).
"""

import json
from datetime import timedelta
from typing import Any, Final

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.discovery.legal_form import LegalFormChecks
from nextron.app.discovery.signal_inbox import SignalInbox
from nextron.app.drafting.generation import StepDrafts
from nextron.app.drafting.grounding import GroundingChecks
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research, WebsiteCrawls
from nextron.app.research.extraction import SignalExtractions
from nextron.app.research.finding import EmailFinding
from nextron.app.research.job_boards import JobBoards
from nextron.app.research.people import PeopleExtractions, PrimaryContacts
from nextron.app.research.verification import EmailVerification
from nextron.app.scoring.api import Scoring
from nextron.app.scoring.lead_scores import LeadScores
from nextron.app.scoring.selection import Selection
from nextron.app.sequencing.api import Sequencing
from nextron.app.setup.api import Setup
from nextron.domain.ai import Urgency
from nextron.domain.job import DEFERRED, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.signal_inbox_item import SubmittedItem
from nextron.ports.dns import MxRecord
from nextron.ports.llm import LlmOk, LlmUsage
from tests.app.test_draft_revisions import FOOTER_SETTINGS
from tests.app.test_enroll import OPS_US, World, build_world, no_jobs, stamp
from tests.app.test_selection import warm
from tests.app.test_step_drafting import activate
from tests.fakes.crawler import ScriptedCrawler, html_page, robots_txt
from tests.fakes.dns import FakeDns
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder, ScriptedVerifier
from tests.fakes.email_syntax import FakeEmailSyntax
from tests.fakes.job_board import ScriptedJobBoards
from tests.fakes.llm import ScriptedLlm
from tests.fakes.registry import ScriptedRegistry
from tests.fakes.renderer import ScriptedRenderer
from tests.fakes.usage_meter import InMemoryUsageMeter

HAIKU: Final = "claude-haiku-5-5"
OPUS: Final = "claude-opus-5-5"
USAGE: Final = LlmUsage(requests=1, cost_micros=10)
SITE: Final = "northgate-supply.com"
ORIGIN: Final = f"https://{SITE}"
HOME, TEAM, ROBOTS = f"{ORIGIN}/", f"{ORIGIN}/team", f"{ORIGIN}/robots.txt"
COO: Final = "Dana Cole"
ADDRESS: Final = f"dana.cole@{SITE}"
QUOTE: Final = "Every morning two of my team re-key forty PDF purchase orders into our ERP by hand."
POST: Final = f"{COO}, COO at Northgate Supply ({SITE})\n{QUOTE} There has to be a better way."
POST_URL: Final = "https://www.linkedin.com/posts/dana-cole-erp-123"
LONG_TEXT: Final = "We supply packaging and warehouse logistics across the United States. " * 10
SPAN: Final = "two of your team re-key forty PDF purchase orders into your ERP by hand"
SUBJECT: Final = "Re-keying purchase orders"
BODY: Final = f"Hi Dana,\n\nSaw your post: {SPAN}.\n\nWorth a short call?"
STEP: Final = timedelta(seconds=60)  # the simulated latency of each job
DEADLINE: Final = timedelta(minutes=15)


def llm_ok(answer: dict[str, Any], model: str) -> LlmOk:
    return LlmOk(answer, model=model, usage=USAGE)


PARSE: Final = llm_ok(
    {
        "segment": "ops_automation",
        "signal_type": "ops_rekeying_post",
        "website": SITE,
        "author_name": COO,
        "author_role": "COO",
        "event_date": None,
        "quote": QUOTE,
    },
    HAIKU,
)
NO_SIGNALS: Final = llm_ok(
    {"signals": [], "profile_facts": [], "size_clues": [], "job_postings": []}, HAIKU
)
NO_PEOPLE: Final = llm_ok({"people": []}, HAIKU)


class Engine16:
    """The services of the Signal Inbox chain, scoring with Story 4.16's hook, enrollment and
    Step 1 drafting, all on one job queue and the real job registry."""

    def __init__(self, world: World) -> None:
        clock, units = world.clock, world.units
        self.world = world
        self.meter = InMemoryUsageMeter(clock)
        self.llm = ScriptedLlm(PARSE, NO_SIGNALS, NO_PEOPLE)
        self.crawler = ScriptedCrawler()
        self.renderer = ScriptedRenderer()
        self.job_types = main.build_job_types(
            alerts=no_jobs,
            backups=no_jobs,
            restore_tests=no_jobs,
            recovery=no_jobs,
            contact_markets=no_jobs,
            crawls=lambda: crawls,
            discovery=lambda: discovery,
            research=lambda: research,
            extractions=lambda: extractions,
            primary=lambda: primary,
            test_runs=no_jobs,
            job_boards=lambda: boards,
            legal_forms=lambda: legal_forms,
            scores=lambda: scores,
            finding=lambda: finding,
            signal_inbox=lambda: self.inbox,
            grounding=lambda: grounding,
            step_drafts=lambda: step_drafts,
            verification=lambda: verification,
        )
        self.queue = queue = JobQueue(clock, self.job_types)
        discovery = Discovery(clock, Pipeline(clock), queue)
        research = Research(clock)
        crawls = WebsiteCrawls(clock, self.meter, self.crawler, self.renderer, queue)
        people = PeopleExtractions(clock, self.meter, self.llm, research, queue)
        extractions = SignalExtractions(clock, self.meter, self.llm, research, people)
        primary = PrimaryContacts(clock, research, queue)
        boards = JobBoards(clock, self.meter, ScriptedJobBoards(), self.crawler, queue)
        legal_forms = LegalFormChecks(
            clock, self.meter, ScriptedRegistry(), discovery, research, queue
        )
        finding = EmailFinding(
            clock,
            self.meter,
            research,
            queue,
            domain_search=ScriptedDomainSearch(),
            hunter=ScriptedFinder(),
            prospeo=ScriptedFinder(),
        )
        verification = EmailVerification(  # a published address: checks only, no verifier call
            clock,
            self.meter,
            queue,
            dns=FakeDns(mx={SITE: (MxRecord(10, f"mx.{SITE}"),)}),
            email_syntax=FakeEmailSyntax(()),
            verifier=ScriptedVerifier(),
        )
        selection = Selection(clock, Sequencing(clock, queue), Compliance(clock))
        scores = LeadScores(
            clock,
            Scoring(clock, Pipeline(clock), research),
            queue,
            selection=selection,
            discovery=discovery,
        )
        grounding = GroundingChecks(clock, self.llm)
        step_drafts = StepDrafts(clock, self.llm, queue)
        self.inbox = SignalInbox(clock, self.meter, self.llm, discovery, research, queue)
        self.units = units
        self.ran: list[tuple[str, str]] = []
        self.on_job: Any = None

    def site(self) -> None:
        """The organization's site: two pages with enough text to need no render, the team
        page publishing the COO's address."""
        home = f'<html><body><h1>Northgate Supply</h1><p>{LONG_TEXT}</p><a href="/team">Team</a>'
        team = (
            f"<html><body><p>{LONG_TEXT}</p><p>{COO}, COO: "
            f'<a href="mailto:{ADDRESS}">{ADDRESS}</a></p></body></html>'
        )
        self.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow:\n")
        self.crawler.pages[HOME] = html_page(HOME, f"{home}</body></html>")
        self.crawler.pages[TEAM] = html_page(TEAM, team)

    def run_jobs(self) -> None:
        """Claim and run every runnable job, one at a time, through its registered handler;
        the clock advances ``STEP`` after each job."""
        for _ in range(40):
            claim = None
            for lane in (Lane.REALTIME, Lane.INTERACTIVE, Lane.BULK):
                with self.units.open():
                    claim = self.queue.claim(lane)
                if claim is not None:
                    break
            if claim is None:
                return
            self.ran.append((claim.job.type, str(claim.job.lane)))
            with self.units.open():
                result = self.job_types.get(claim.job.type).handler(claim.job)
            with self.units.open():
                if result is DEFERRED:
                    self.queue.defer(claim)
                else:
                    self.queue.complete(claim)
            self.world.clock.advance(STEP)
            if self.on_job is not None:
                self.on_job()
        raise AssertionError("the chain didn't settle")


@pytest.fixture
def world(db_engine: Engine) -> World:
    found = build_world(db_engine)
    with found.units.open():
        Setup(found.clock).save(FOOTER_SETTINGS)
    found.activate(OPS_US)
    warm(found, 30)
    activate(found, "draft_sequence", "grounding_check")
    return found


def test_a_submitted_item_is_drafted_and_grounded_within_15_minutes(world: World) -> None:
    engine = Engine16(world)
    engine.site()
    [(ops,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    identity = LeadIdentity("Northgate Supply", str(ops), web_domain=SITE, country_code="US")
    lead_id = world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.CSV)).lead_id
    world.sql("DELETE FROM jobs")  # idle lanes: the import's own crawl is done
    submitted_at = world.clock.now()

    with world.units.open():
        [item_id] = engine.inbox.submit((SubmittedItem(POST_URL, POST, draft_when_ready=True),))

    def script_the_draft() -> None:
        """Once the item's Event exists, the drafting and grounding answers cite it."""
        if len(engine.llm.script) > 2 or not engine.ran or engine.ran[-1][0] != "score_lead":
            return
        [(event, evidence)] = world.sql(
            "SELECT s.id, e.id FROM signals s JOIN evidence e ON e.lead_id = s.lead_id "
            "WHERE s.lead_id = ? AND e.source = 'signal_inbox'",
            (lead_id,),
        )
        engine.llm.script.append(
            llm_ok(
                {
                    "subject": SUBJECT,
                    "body": BODY,
                    "claims": [
                        {"span": SPAN, "evidence_id": evidence, "fact_id": None,
                         "fact_version": None},
                    ],
                    "cited_signal_id": event,
                    "explanation": "The post's Event.",
                },
                OPUS,
            )
        )
        engine.llm.script.append(
            llm_ok(
                {
                    "claims": [
                        {
                            "span": SPAN,
                            "evidence_id": evidence,
                            "fact_id": None,
                            "fact_version": None,
                            "supported": True,
                            "explanation": "The post says so.",
                        }
                    ],
                    "flags": [],
                },
                HAIKU,
            )
        )

    engine.on_job = script_the_draft
    engine.run_jobs()

    kinds = [kind for kind, _ in engine.ran]
    assert kinds[0] == "process_signal_item"
    assert kinds.index("score_lead") < kinds.index("draft_step") < kinds.index("check_grounding")
    assert "render_pages" not in kinds
    # every job on the way to the Draft runs on the interactive lane; only Story 2.14's check
    # of the published address (which nothing here waits for) runs on the bulk lane
    assert {lane for kind, lane in engine.ran if kind != "verify_contact_email"} == {
        "interactive"
    }
    assert [kind for kind, lane in engine.ran if lane == "bulk"] == ["verify_contact_email"]
    assert [(call[0].route, call[2]) for call in engine.llm.calls] == [
        ("signal_inbox_parse", Urgency.SOON),
        ("extract_signals", Urgency.SOON),
        ("extract_people", Urgency.SOON),
        ("draft_sequence", Urgency.SOON),
        ("grounding_check", Urgency.INTERACTIVE),  # its job runs on the interactive lane
    ]
    # picked by score_lead: an operator_pick Enrollment, the item decided enrolled
    [(enrollment_id, origin)] = world.sql(
        "SELECT id, origin FROM enrollments WHERE lead_id = ?", (lead_id,)
    )
    assert origin == "operator_pick"
    [(outcome, decided_at)] = world.sql(
        "SELECT draft_outcome, draft_decided_at FROM signal_inbox_items WHERE id = ?", (item_id,)
    )
    assert outcome == "enrolled"
    # revision 1 and its grounding result, both within 15 minutes of the submission
    [(draft_id,)] = world.sql(
        "SELECT id FROM drafts WHERE enrollment_id = ? AND step_no = 1", (enrollment_id,)
    )
    [(rev, created_at, cited, evidence_ids)] = world.sql(
        "SELECT rev, created_at, cited_signal_id, evidence_ids FROM draft_revisions "
        "WHERE draft_id = ?",
        (draft_id,),
    )
    [(status, checked_at)] = world.sql(
        "SELECT status, ai_checked_at FROM grounding_results WHERE draft_id = ? AND rev = 1",
        (draft_id,),
    )
    deadline = stamp(submitted_at + DEADLINE)
    assert rev == 1
    assert status != "pending"
    assert status == "pass"
    assert decided_at <= deadline
    assert created_at <= deadline
    assert checked_at is not None and checked_at <= deadline
    # the revision cites the item's Event, first seen when it was submitted
    [(signal_class, first_seen_at)] = world.sql(
        "SELECT signal_class, first_seen_at FROM signals WHERE id = ?", (cited,)
    )
    assert signal_class == "event"
    assert first_seen_at == stamp(submitted_at)
    assert len(json.loads(evidence_ids)) == 1
