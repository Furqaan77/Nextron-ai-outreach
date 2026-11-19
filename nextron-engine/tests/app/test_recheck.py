"""Story 8.7's scheduled re-check on a temporary database at head (FR-11, AD-7, AD-10, AD-16):
``Rechecks.enqueue_due`` and ``refresh`` with the real ``WebsiteCrawls`` and ``JobBoards`` on a
``ScriptedCrawler``, ``ScriptedJobBoards`` and ``ScriptedLlm``, the shipped Segments and a
``FrozenClock``. The world is Story 2.9's (``tests.app.test_signal_extraction``), whose
subscribers record ``EvidenceChanged`` and ``EventClosed``.

Every row of the spec's matrix: due full, due jobs, not due, each exclusion, a Primary whose only
failing reason is ``recontact_not_allowed``, the daily limit, an unchanged page (no AI call, only
``last_confirmed_at`` moves), a changed value (superseded, ``EvidenceChanged``), a vanished quote
(retracted), a delisted posting (closed, ``EventClosed``), a Tier change's ``events`` row, and the
budget at 100% (``DEFERRED``, no crawl, board or AI call). Also the repository's mode filters, the
Evidence trigger, the re-validation when the job runs and the handlers. No test reaches the
network.

``world.lead`` queues the Lead's first ``enrich_lead`` job and ``world.crawl`` crawls without the
queue, so ``Bench.tick`` first marks those jobs done (their crawl has happened), unless a test
asks it not to."""

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy.exc
from sqlalchemy import Engine

from nextron.app.compliance.api import Compliance
from nextron.app.queries import research as research_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.research.job_boards import JobBoards
from nextron.app.research.recheck import Rechecks
from nextron.app.setup.api import Setup
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.contactability import DomainSubject, SuppressionScope
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import Decision, Reason, ReasonKind
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.notifications import EventClosed, EvidenceChanged
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.recheck import RecheckMode
from nextron.domain.settings_schema import Key
from nextron.domain.suppression_entry import SuppressionSource
from nextron.jobs import handlers
from nextron.ports.errors import AdapterError
from tests.app.test_job_boards import ACME, AP_CLERK, SALES_URL, ats_lead, check, first_listing
from tests.app.test_lead_scoring import enriched, lawyers, recompute, score
from tests.app.test_lead_scoring import signal as stored_signal
from tests.app.test_signal_extraction import (
    AI_POST,
    CHICAGO,
    FOURTEEN,
    HIRING,
    HOME,
    T0,
    SITE,
    TEAM,
    TEAM_V2,
    TEAM_V2_HIRING,
    TWELVE,
    World,
    build_world,
    crawled,
    first_answer,
    ok,
    signal,
    size,
    spend_the_budget,
    utc,
)
from tests.fakes.job_board import ScriptedJobBoards, listing, posting

RUN_ID = "01JD0000000000000000000011"
INBOX_QUOTE = "Smith Law is hiring a paralegal."


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


class RecontactOnly(Compliance):
    """A ``Compliance`` whose Eligibility of the Contacts in ``recontact`` fails only on
    ``recontact_not_allowed``, and of those in ``do_not_contact`` on that and
    ``asked_not_to_be_contacted``; every other Contact's is evaluated for real."""

    def __init__(
        self, world: World, recontact: set[str], do_not_contact: frozenset[str] = frozenset()
    ) -> None:
        super().__init__(world.clock)
        self.recontact = recontact
        self.do_not_contact = do_not_contact

    def evaluate_eligibility(self, contact_id: str) -> Decision:
        recent = Reason(ReasonKind.RECONTACT_NOT_ALLOWED, "Emailed too recently.")
        if contact_id in self.recontact:
            return Decision(eligible=False, basis_rule=None, reasons=(recent,))
        if contact_id in self.do_not_contact:
            asked = Reason(ReasonKind.ASKED_NOT_TO_BE_CONTACTED, "Asked not to be contacted.")
            return Decision(eligible=False, basis_rule=None, reasons=(asked, recent))
        return super().evaluate_eligibility(contact_id)


@dataclass
class Bench:
    """The re-check on the world's services and a boards fake of its own."""

    world: World
    boards: ScriptedJobBoards = field(default_factory=ScriptedJobBoards)
    compliance: Compliance | None = None

    def rechecks(self) -> Rechecks:
        world = self.world
        job_boards = JobBoards(world.clock, world.meter, self.boards, world.crawler, world.queue)
        compliance = self.compliance if self.compliance is not None else Compliance(world.clock)
        return Rechecks(world.clock, world.meter, world.crawls, job_boards, world.queue, compliance)

    def tick(self, *, first_crawls_done: bool = True) -> int:
        if first_crawls_done:
            self.world.sql("UPDATE jobs SET status = 'done' WHERE type = 'enrich_lead'")
        with self.world.units.open():
            return self.rechecks().enqueue_due()

    def refresh(self, lead: str, mode: RecheckMode, *, may_retry: bool = False) -> bool:
        with self.world.units.open():
            return self.rechecks().refresh(lead, mode, may_retry=may_retry)

    def jobs(self) -> list[tuple[str, str | None, dict[str, Any], str]]:
        return [
            (key, concurrency, json.loads(payload), lane)
            for key, concurrency, payload, lane in self.world.jobs("recheck_lead")
        ]


def run(
    world: World,
    lead: str,
    days_ago: int,
    mode: str = "initial",
    *,
    hiring_checked: bool = False,
) -> str:
    """A crawl run of ``lead`` in ``mode`` that started ``days_ago`` days before now; with
    ``hiring_checked``, its ATS boards were checked (``board_errors`` 0)."""
    at = world.clock.now() - timedelta(days=days_ago)

    def store() -> str:
        run_id = crawl_repo.insert_run(
            lead_id=lead,
            site_domain="smith-law.com",
            final_domain=None,
            started_at=at,
            ended_at=at,
            outcome="ok",
            error_code=None,
            pages_fetched=0,
            pages_skipped_robots=0,
            renders=0,
            errors=0,
            mode=mode,
        )
        if hiring_checked:
            crawl_repo.set_posting_counts(run_id, {"board_errors": 0})
        return run_id

    return world.transact(store)


def lead_at(world: World, name: str) -> str:
    return world.lead(web_domain=f"{name}.com")


def key(lead: str, day: str = "2026-11-18") -> str:
    return f"recheck_lead:{lead}:{day}"


def payload(lead: str, mode: str) -> dict[str, Any]:
    return {"v": 1, "lead_id": lead, "mode": mode}


def primary(world: World, lead: str, **candidate: Any) -> str:
    """A Contact of ``lead`` made its Primary by the Operator."""
    fields = {"name": "Jane Doe", "role": "Managing Partner", **candidate}
    added = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(**fields),
            source=ProvenanceSource.WEBSITE,
            detail=TEAM,
            retrieved_at=T0,
        )
    )
    contact_id = added.contact_id
    assert contact_id is not None
    world.transact(lambda: world.research.make_primary(lead, contact_id))
    return contact_id


def runs_of(world: World, lead: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT mode, outcome, site_domain, pages_fetched, greenhouse_postings, board_errors, "
        "started_at FROM crawl_runs WHERE lead_id = ? ORDER BY started_at, rowid",
        (lead,),
    )


# Selection: the matrix's due, not due, excluded, recontact-only and limit rows -----------------


def test_the_tick_enqueues_exactly_the_due_leads_with_their_modes_on_the_bulk_lane(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    due_full = lead_at(world, "due-full")
    run(world, due_full, 31)
    at_thirty = lead_at(world, "at-thirty")  # exactly recheck_full_days: due
    run(world, at_thirty, 30)
    never = lead_at(world, "never-crawled")
    due_jobs = lead_at(world, "due-jobs")
    run(world, due_jobs, 10)
    run(world, due_jobs, 8, "recheck_jobs", hiring_checked=True)
    at_seven = lead_at(world, "at-seven")  # exactly recheck_jobs_days: due
    run(world, at_seven, 12, hiring_checked=True)
    run(world, at_seven, 7, "recheck_jobs", hiring_checked=True)
    not_due = lead_at(world, "not-due")
    run(world, not_due, 10)
    run(world, not_due, 2, "recheck_jobs", hiring_checked=True)
    unchecked_run = lead_at(world, "unchecked-run")  # a jobs run whose boards weren't checked
    run(world, unchecked_run, 10, hiring_checked=True)
    run(world, unchecked_run, 1, "recheck_jobs")
    recontact = lead_at(world, "recontact-only")
    recontact_contact = primary(world, recontact, email="jane@recontact-only.com")
    for stage in (PipelineStage.LOST, PipelineStage.DISQUALIFIED, PipelineStage.NOT_NOW):
        closed = lead_at(world, f"stage-{stage.value.replace('_', '-')}")
        world.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage.value, closed))
    merged = lead_at(world, "merged")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (due_full, merged))
    no_domain = lead_at(world, "no-domain")
    world.sql("UPDATE leads SET web_domain = NULL, place_id = 'place-1' WHERE id = ?", (no_domain,))
    live = {lead_at(world, "enrolled"), lead_at(world, "paused")}  # active or paused
    real_live = sequencing_queries.live_enrollment
    monkeypatch.setattr(
        sequencing_queries,
        "live_enrollment",
        lambda lead_id: object() if lead_id in live else real_live(lead_id),
    )
    lead_at(world, "suppressed")
    world.transact(
        lambda: Compliance(world.clock).suppress(
            DomainSubject("suppressed.com"),
            SuppressionScope.FIRM,
            SuppressionSource.OPERATOR,
            None,
        )
    )
    ineligible = lead_at(world, "ineligible")
    primary(world, ineligible)  # no address: its Eligibility fails on more than recontact
    asked = lead_at(world, "do-not-contact")
    asked_contact = primary(world, asked, email="jane@do-not-contact.com")
    compliance = RecontactOnly(world, {recontact_contact}, frozenset({asked_contact}))
    bench = Bench(world, compliance=compliance)

    assert bench.tick() == 7

    # never crawled first (by id), then by when the last run of any mode started
    domains = {never: "domain:never-crawled.com", recontact: "domain:recontact-only.com"}
    first, second = sorted([never, recontact])
    assert bench.jobs() == [
        (key(first), domains[first], payload(first, "full"), "bulk"),
        (key(second), domains[second], payload(second, "full"), "bulk"),
        (key(due_full), "domain:due-full.com", payload(due_full, "full"), "bulk"),
        (key(at_thirty), "domain:at-thirty.com", payload(at_thirty, "full"), "bulk"),
        # its last hiring check and last counted run are the 10-day run: the later jobs run
        # checked no board, so it counts for neither
        (key(unchecked_run), "domain:unchecked-run.com", payload(unchecked_run, "jobs"), "bulk"),
        (key(due_jobs), "domain:due-jobs.com", payload(due_jobs, "jobs"), "bulk"),
        (key(at_seven), "domain:at-seven.com", payload(at_seven, "jobs"), "bulk"),
    ]


def test_a_second_tick_the_same_day_skips_the_lead_with_a_live_job(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = lead_at(world, "firm")
    bench = Bench(world)
    caplog.set_level(logging.INFO, logger="nextron.app.research.recheck")

    assert bench.tick() == 1
    assert bench.tick() == 0

    assert [job[0] for job in bench.jobs()] == [key(lead)]
    logged = [r for r in caplog.records if r.getMessage() == "rechecks_enqueued"]
    counts = [(getattr(r, "due"), getattr(r, "picked"), getattr(r, "enqueued")) for r in logged]
    assert counts == [(1, 1, 1), (1, 0, 0)]


def test_yesterdays_still_queued_recheck_blocks_todays(world: World) -> None:
    """Review row B: the key is per date, so the tick itself skips a Lead with a live
    ``recheck_lead`` job of any date (queued, deferred back to queued, or running)."""
    lead = lead_at(world, "firm")
    bench = Bench(world)
    assert bench.tick() == 1
    world.clock.advance(timedelta(days=1))

    assert bench.tick() == 0

    assert [job[0] for job in bench.jobs()] == [key(lead)]
    world.sql(
        "UPDATE jobs SET status = 'running', attempts = 1, lease_until = run_after "
        "WHERE type = 'recheck_lead'"
    )
    assert bench.tick() == 0
    world.sql("UPDATE jobs SET status = 'done', lease_until = NULL WHERE type = 'recheck_lead'")
    assert bench.tick() == 1  # once it is done, the next day's job is enqueued
    assert [job[0] for job in bench.jobs()] == [key(lead), key(lead, "2026-11-19")]


def test_a_queued_first_crawl_blocks_the_recheck_of_a_never_crawled_lead(world: World) -> None:
    """Review row C: a new Lead's ``enrich_lead`` job is still queued; it isn't crawled twice."""
    lead = lead_at(world, "new-firm")
    assert world.jobs("enrich_lead")  # queued by world.lead
    bench = Bench(world)

    assert bench.tick(first_crawls_done=False) == 0

    assert bench.jobs() == []
    assert bench.tick() == 1  # once its first crawl's job is done
    assert [job[2] for job in bench.jobs()] == [payload(lead, "full")]


def test_the_key_is_the_local_date_in_the_sender_timezone(world: World) -> None:
    lead = lead_at(world, "firm")
    with world.units.open():
        Setup(world.clock).set(Key.SENDER_TIMEZONE, "Pacific/Auckland")
    world.clock.set(T0 + timedelta(hours=12))  # 21:00 UTC: already the 19th in Auckland

    Bench(world).tick()

    assert [job[0] for job in Bench(world).jobs()] == [key(lead, "2026-11-19")]


def test_the_limit_takes_the_oldest_due_leads(world: World) -> None:
    leads = []
    for number in range(45):
        found = lead_at(world, f"firm-{number}")
        run(world, found, 31 + number)  # firm-44 was crawled longest ago
        leads.append(found)

    assert Bench(world).tick() == 40  # the default recheck_daily_limit

    enqueued = [job[2]["lead_id"] for job in Bench(world).jobs()]
    assert enqueued == list(reversed(leads))[:40]


def test_a_refused_oldest_lead_leaves_the_limit_to_the_next_oldest(world: World) -> None:
    """Review row R: the limit counts picked Leads, not walked ones."""
    oldest, second, third, fourth = (
        lead_at(world, name) for name in ("oldest", "second", "third", "fourth")
    )
    for days, found in ((60, oldest), (50, second), (40, third), (35, fourth)):
        run(world, found, days)
    world.transact(
        lambda: Compliance(world.clock).suppress(
            DomainSubject("oldest.com"), SuppressionScope.FIRM, SuppressionSource.OPERATOR, None
        )
    )
    with world.units.open():
        Setup(world.clock).set(Key.RECHECK_DAILY_LIMIT, 2)

    assert Bench(world).tick() == 2

    assert [job[2] for job in Bench(world).jobs()] == [
        payload(second, "full"),
        payload(third, "full"),
    ]


def test_a_smaller_limit_and_longer_periods_are_read_at_each_tick(world: World) -> None:
    old, older, oldest = (lead_at(world, name) for name in ("old", "older", "oldest"))
    run(world, old, 40)
    run(world, older, 50)
    run(world, oldest, 60)
    for setting, value in (
        (Key.RECHECK_DAILY_LIMIT, 1),
        (Key.RECHECK_FULL_DAYS, 55),
        (Key.RECHECK_JOBS_DAYS, 100),
    ):
        with world.units.open():
            Setup(world.clock).set(setting, value)

    assert Bench(world).tick() == 1

    assert [job[2] for job in Bench(world).jobs()] == [payload(oldest, "full")]


# Mode full: the matrix's unchanged page, changed value and vanished quote ------------------------


def test_an_unchanged_page_confirms_its_evidence_with_no_ai_call(world: World) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))
    by_quote = {row["quote"]: row["id"] for row in world.evidence(lead)}
    # Review rows T and N: a retracted and a superseded row cited from the unchanged pages, and
    # a live Signal Inbox row citing the same URL, keep their last_confirmed_at
    world.transact(lambda: signals_repo.retract_evidence(by_quote[CHICAGO], T0))
    inbox = world.transact(
        lambda: signals_repo.insert_evidence(
            lead_id=lead,
            evidence_type="quote",
            quote=INBOX_QUOTE,
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=(),
            source="signal_inbox",
            source_url=TEAM,
            retrieved_at=T0,
            extracted_by="ai:parse_signal_item@v1",
            person_contact_id=None,
            at=T0,
        )
    )
    world.transact(lambda: signals_repo.supersede_evidence(by_quote[HIRING], inbox))
    before = world.evidence(lead)
    world.calls.clear()
    world.clock.set(T0 + timedelta(days=31))
    bench = Bench(world)
    assert bench.tick() == 1
    assert bench.jobs()[0][2] == payload(lead, "full")

    assert bench.refresh(lead, RecheckMode.FULL) is True

    recheck = world.read(lambda: research_queries.latest_crawl_run(lead))
    assert recheck is not None and recheck.mode == "recheck_full" and recheck.id != run_id
    after = world.evidence(lead)
    assert [row["id"] for row in after] == [row["id"] for row in before]
    changed = {
        row["quote"]: {name for name in row if row[name] != old[name]}
        for row, old in zip(after, before, strict=True)
    }
    # only the live website row moves: the retracted, superseded and Signal Inbox rows don't
    assert changed == {
        HIRING: set(),
        TWELVE: {"last_confirmed_at"},
        CHICAGO: set(),
        INBOX_QUOTE: set(),
    }
    moved = {row["quote"]: row["last_confirmed_at"] for row in after}
    assert moved[TWELVE] == utc(T0 + timedelta(days=31))
    assert moved[CHICAGO] == moved[HIRING] == moved[INBOX_QUOTE] == utc(T0)

    # the chain's extraction sends nothing: every page is unchanged
    assert world.extract(lead, recheck.id) is True
    assert world.calls == []
    assert world.runs(lead)[-1][0] == "no_pages"
    assert world.recorder.seen == []


def test_a_changed_value_supersedes_and_a_vanished_quote_retracts(world: World) -> None:
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
    old = {row["quote"]: row for row in world.evidence(lead)}
    world.calls.clear()
    world.clock.set(T0 + timedelta(days=31))
    world.site(team=TEAM_V2)

    assert Bench(world).refresh(lead, RecheckMode.FULL) is True
    recheck = world.read(lambda: research_queries.latest_crawl_run(lead))
    assert recheck is not None and recheck.mode == "recheck_full"
    world.extract(
        lead,
        recheck.id,
        ok(signals=[signal(team, TEAM_V2_HIRING)], size_clues=[size(team, FOURTEEN, 14)]),
    )

    [(_, blocks, _)] = world.calls
    assert [block.block_id for block in blocks] == ["signal_types", team]  # the changed page
    rows = {row["quote"]: row for row in world.evidence(lead)}
    assert rows[TWELVE]["superseded_by"] == rows[FOURTEEN]["id"]
    assert rows[FOURTEEN]["number"] == 14
    assert rows[HIRING]["superseded_by"] == rows[TEAM_V2_HIRING]["id"]
    assert rows[AI_POST]["retracted_at"] == utc(T0 + timedelta(days=31))
    # the old rows' observation columns never changed
    for quote in (TWELVE, HIRING, AI_POST):
        for column in ("type", "quote", "number", "source", "source_url", "retrieved_at"):
            assert rows[quote][column] == old[quote][column]
    assert world.recorder.seen == [
        EvidenceChanged(lead_id=lead, evidence_id=old[HIRING]["id"]),
        EvidenceChanged(lead_id=lead, evidence_id=old[TWELVE]["id"]),
        EvidenceChanged(lead_id=lead, evidence_id=old[AI_POST]["id"]),
    ]
    retracted = {row[1]: row[7] for row in world.signals(lead)}
    assert retracted["law_ai_confidentiality_post"] == utc(T0 + timedelta(days=31))


@pytest.mark.parametrize(
    ("column", "value"),
    [("quote", "We are hiring nobody."), ("type", "count"), ("source", "operator")],
)
def test_an_update_of_an_evidence_observation_raises(
    world: World, column: str, value: str
) -> None:
    lead, run_id, pages = crawled(world)
    world.extract(lead, run_id, first_answer(pages))
    [first, *_] = world.evidence(lead)

    with pytest.raises(sqlalchemy.exc.IntegrityError, match="Evidence is immutable"):
        world.sql(f"UPDATE evidence SET {column} = ? WHERE id = ?", (value, first["id"]))


@pytest.mark.parametrize("may_retry", [True, False])
def test_a_full_recheck_passes_may_retry_to_the_crawl(world: World, may_retry: bool) -> None:
    """Review row S: a flaky homepage raises the retryable error while the job may retry; its
    last allowed attempt stores the ``unreachable`` run and completes."""
    lead, _, _ = crawled(world)
    world.clock.set(T0 + timedelta(days=31))
    world.crawler.pages[HOME] = AdapterError(True, "fetch_timeout", "slow")
    bench = Bench(world)

    if may_retry:
        with pytest.raises(AdapterError) as raised:
            bench.refresh(lead, RecheckMode.FULL, may_retry=True)
        assert (raised.value.code, raised.value.retryable) == ("crawl_retry", True)
    else:
        assert bench.refresh(lead, RecheckMode.FULL, may_retry=False) is True

    last = runs_of(world, lead)[-1]
    assert (last[0], last[1]) == ("recheck_full", "unreachable")


# Mode jobs: the matrix's due jobs and delisted posting rows -------------------------------------


def test_a_jobs_recheck_records_its_run_closes_a_delisted_posting_and_goes_on(
    world: World,
) -> None:
    lead, initial = ats_lead(world)
    assert check(world, ScriptedJobBoards(boards={ACME: first_listing()}), lead, initial)
    [(event_id,)] = world.sql(
        "SELECT signal_id FROM job_postings WHERE lead_id = ? AND title = ?", (lead, AP_CLERK)
    )
    world.clock.set(T0 + timedelta(days=8))
    still_listed = listing(posting("Sales Rep", SALES_URL))
    bench = Bench(world, ScriptedJobBoards(boards={ACME: still_listed}))
    assert bench.tick() == 1
    assert bench.jobs()[0][2] == payload(lead, "jobs")
    world.sql("DELETE FROM jobs WHERE type = 'check_legal_form'")  # the first chain's
    fetched = len(world.crawler.calls)

    assert bench.refresh(lead, RecheckMode.JOBS) is True

    assert bench.boards.calls == [ACME]
    assert len(world.crawler.calls) == fetched  # no page fetched
    [first, second] = runs_of(world, lead)
    assert first[0] == "initial"
    assert second == ("recheck_jobs", "ok", SITE, 0, 1, 0, utc(T0 + timedelta(days=8)))
    # the ATS links and extraction still read the initial crawl
    latest = world.read(lambda: research_queries.latest_crawl_run(lead))
    assert latest is not None and latest.id == initial
    [(status,)] = world.sql(
        "SELECT status FROM job_postings WHERE lead_id = ? AND title = ?", (lead, AP_CLERK)
    )
    assert status == "closed"
    assert world.recorder.closed == [EventClosed(lead_id=lead, signal_id=event_id)]
    [(legal_form_key, _, legal_payload, lane)] = world.jobs("check_legal_form")
    assert legal_form_key == f"check_legal_form:{lead}:tick"
    assert lane == "bulk"
    [(recheck_run,)] = world.sql(
        "SELECT id FROM crawl_runs WHERE lead_id = ? AND mode = 'recheck_jobs'", (lead,)
    )
    assert json.loads(legal_payload)["run_id"] == recheck_run
    # the hiring check is recorded: not due again for 7 days
    assert bench.tick() == 0


def test_a_jobs_recheck_with_no_board_still_records_a_hiring_check(world: World) -> None:
    lead = lead_at(world, "no-boards")
    run(world, lead, 10)
    bench = Bench(world)

    assert bench.refresh(lead, RecheckMode.JOBS) is True

    assert bench.boards.calls == []
    assert [row[0] for row in runs_of(world, lead)] == ["initial", "recheck_jobs"]
    hiring = world.read(lambda: crawl_repo.last_hiring_check(lead))
    assert hiring is not None and hiring.mode == "recheck_jobs" and hiring.board_errors == 0


def test_a_deferred_jobs_recheck_reuses_its_unchecked_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review row A: a ``check_boards`` that deferred leaves its run unchecked; the retry checks
    the boards on that run, so the Lead holds exactly one ``recheck_jobs`` run."""
    lead = lead_at(world, "no-boards")
    initial = run(world, lead, 10, hiring_checked=True)
    real = JobBoards.check_boards
    monkeypatch.setattr(JobBoards, "check_boards", lambda *_, **__: False)
    bench = Bench(world)

    assert bench.refresh(lead, RecheckMode.JOBS) is False
    assert bench.refresh(lead, RecheckMode.JOBS) is False

    [unchecked] = [row for row in runs_of(world, lead) if row[0] == "recheck_jobs"]
    assert unchecked[5] is None  # board_errors: its boards weren't checked
    assert world.read(lambda: crawl_repo.last_recheck(lead)) is None
    facts = world.read(lambda: crawl_repo.recheck_candidates()[lead])
    stored = world.read(lambda: crawl_repo.run(initial))
    assert stored is not None
    assert facts.last_run_at == facts.last_hiring_at == stored.started_at
    monkeypatch.setattr(JobBoards, "check_boards", real)

    assert bench.refresh(lead, RecheckMode.JOBS) is True

    assert [row[0] for row in runs_of(world, lead)] == ["initial", "recheck_jobs"]
    recheck = world.read(lambda: crawl_repo.last_recheck(lead))
    assert recheck is not None and recheck.mode == "recheck_jobs" and recheck.board_errors == 0
    assert world.read(lambda: crawl_repo.unchecked_jobs_run(lead)) is None


# Tier changes -----------------------------------------------------------------------------------


def tier_events(world: World, lead: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'leads' AND entity_id = ? AND to_status LIKE 'tier_%' ORDER BY rowid",
        (lead,),
    )


def test_a_tier_change_appends_an_events_row(world: World) -> None:
    lead = world.lead()
    enriched(world, lead)
    lawyers(world, lead, 20)
    stored_signal(world, lead, "law_expansion", tier=2)
    score(world, lead)
    assert tier_events(world, lead) == [(None, "tier_b", "lead_rescored", None, "system:scoring")]

    score(world, lead)  # the same Tier: no row
    stored_signal(world, lead, "law_hiring_legal_ops", tier=1)  # a new Tier 1 Event
    score(world, lead)

    assert tier_events(world, lead) == [
        (None, "tier_b", "lead_rescored", None, "system:scoring"),
        ("tier_b", "tier_a", "lead_rescored", None, "system:scoring"),
    ]
    world.clock.advance(timedelta(days=90))  # both Events have aged out
    assert recompute(world) == 1
    assert tier_events(world, lead)[2:] == [
        ("tier_a", "tier_c", "lead_retiered", None, "system:scoring")
    ]
    assert recompute(world) == 1  # retiered again, at the same Tier: no row
    assert len(tier_events(world, lead)) == 3


# The budget and the re-validation ---------------------------------------------------------------


@pytest.mark.parametrize("mode", list(RecheckMode))
def test_the_budget_at_100_percent_defers_with_no_call(world: World, mode: RecheckMode) -> None:
    lead, _, pages = crawled(world)
    world.extract(lead, _latest(world, lead), first_answer(pages))
    world.calls.clear()
    fetched = len(world.crawler.calls)
    bench = Bench(world)
    spend_the_budget(world)
    job_types = handlers.recheck_lead(bench.rechecks)

    with world.units.open():
        result = job_types(Job(RUN_ID, "recheck_lead", Lane.BULK, payload(lead, str(mode)), 1))

    assert result is DEFERRED
    assert len(world.crawler.calls) == fetched and bench.boards.calls == [] and world.calls == []
    assert [row[0] for row in runs_of(world, lead)] == ["initial"]


def _latest(world: World, lead: str) -> str:
    found = world.read(lambda: research_queries.latest_crawl_run(lead))
    assert found is not None
    return found.id


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", "lead_closed"),
        ("UPDATE leads SET pipeline_stage = 'not_now' WHERE id = ?", "lead_parked"),
        ("UPDATE leads SET web_domain = NULL, place_id = 'place-1' WHERE id = ?", "no_website"),
    ],
)
def test_a_lead_that_stopped_qualifying_is_skipped_when_the_job_runs(
    world: World, caplog: pytest.LogCaptureFixture, change: str, reason: str
) -> None:
    lead = lead_at(world, "firm")
    bench = Bench(world)
    assert bench.tick() == 1
    world.sql(change, (lead,))
    caplog.set_level(logging.INFO, logger="nextron.app.research.recheck")

    assert bench.refresh(lead, RecheckMode.FULL) is True
    assert bench.refresh(lead, RecheckMode.JOBS) is True

    assert world.crawler.calls == [] and runs_of(world, lead) == []
    skipped = [r for r in caplog.records if r.getMessage() == "recheck_skipped"]
    assert [(getattr(r, "lead_id"), getattr(r, "mode"), getattr(r, "reason")) for r in skipped] == [
        (lead, "full", reason),
        (lead, "jobs", reason),
    ]


def test_an_unknown_or_merged_lead_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    survivor, loser = lead_at(world, "survivor"), lead_at(world, "loser")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (survivor, loser))
    caplog.set_level(logging.INFO, logger="nextron.app.research.recheck")

    assert Bench(world).refresh(loser, RecheckMode.FULL) is True
    assert Bench(world).refresh(RUN_ID, RecheckMode.FULL) is True

    skipped = [r for r in caplog.records if r.getMessage() == "recheck_skipped"]
    reasons = [getattr(r, "reason") for r in skipped]
    assert reasons == ["merged_lead", "unknown_lead"]
    assert world.crawler.calls == []


def test_a_suppressed_domain_is_skipped_when_the_job_runs(world: World) -> None:
    lead = lead_at(world, "firm")
    world.transact(
        lambda: Compliance(world.clock).suppress(
            DomainSubject("firm.com"), SuppressionScope.FIRM, SuppressionSource.OPERATOR, None
        )
    )

    assert Bench(world).refresh(lead, RecheckMode.JOBS) is True

    assert runs_of(world, lead) == []


# The repository ---------------------------------------------------------------------------------


def test_latest_run_and_run_count_ignore_jobs_runs_and_the_last_checks_are_found(
    world: World,
) -> None:
    lead = lead_at(world, "firm")
    initial = run(world, lead, 20, hiring_checked=True)
    full = run(world, lead, 9, "recheck_full")
    jobs = run(world, lead, 2, "recheck_jobs", hiring_checked=True)
    unchecked = run(world, lead, 1, "recheck_jobs")

    def read() -> tuple[Any, ...]:
        return (
            crawl_repo.latest_run(lead),
            crawl_repo.run_count(lead),
            crawl_repo.last_hiring_check(lead),
            crawl_repo.last_recheck(lead),
            crawl_repo.recheck_candidates()[lead],
            crawl_repo.run(initial),
        )

    latest, count, hiring, recheck, facts, stored = world.read(read)

    assert (latest.id, latest.mode, count) == (full, "recheck_full", 2)
    assert hiring.id == jobs
    assert recheck.id == jobs  # the unchecked jobs run doesn't count
    found = world.read(lambda: crawl_repo.unchecked_jobs_run(lead))
    assert found is not None and found.id == unchecked
    now = world.clock.now()
    assert (facts.last_full_at, facts.last_hiring_at, facts.last_run_at) == (
        now - timedelta(days=9),
        now - timedelta(days=2),
        now - timedelta(days=2),
    )
    assert stored.mode == "initial"
    other = lead_at(world, "never-crawled")
    assert other not in world.read(crawl_repo.recheck_candidates)


def test_a_lead_with_only_jobs_runs_has_no_full_crawl(world: World) -> None:
    lead = lead_at(world, "firm")
    run(world, lead, 3, "recheck_jobs", hiring_checked=True)

    facts = world.read(lambda: crawl_repo.recheck_candidates()[lead])

    assert world.read(lambda: crawl_repo.latest_run(lead)) is None
    checked = world.clock.now() - timedelta(days=3)
    assert (facts.last_full_at, facts.last_hiring_at) == (None, checked)


def test_the_mode_column_refuses_another_value(world: World) -> None:
    lead = lead_at(world, "firm")
    run_id = run(world, lead, 1)

    with pytest.raises(sqlalchemy.exc.IntegrityError, match="ck_crawl_runs_mode"):
        world.sql("UPDATE crawl_runs SET mode = 'weekly' WHERE id = ?", (run_id,))
