"""Story 2.11's job boards on a temporary database at head (FR-7, FR-11, FR-49, AD-5, AD-7, AD-9,
INT-10, NFR-6): ``JobBoards.check_job_boards`` and ``recheck_event`` through
``ScriptedJobBoards`` on the ATS links a real ``WebsiteCrawls`` stored from a
``ScriptedCrawler``, with the shipped Segments, Markets and prompts and a ``FrozenClock``. The
world is Story 2.9's (``tests.app.test_signal_extraction``); careers postings themselves are
that file's (the careers step of ``extract_lead``).

Every row of the spec's matrix the boards own is here: an ATS board's postings, their hiring
Event and counts, and the selection queued with the Controller preferred; a description match;
a posting no longer listed (closed, its Evidence and Signal retracted, ``EvidenceChanged`` and
``EventClosed``); boards never called (a host removed from ``ats_hosts``, an unmapped host, a
job site); the board errors (404, 500, a timeout); the budget; and the recheck of an ATS
posting and of a careers posting whose title left its page. Also reopening, a merge collision,
the chain from ``extract_lead``, the handlers and the log lines. No job site and no real board
is ever reached."""

import contextvars
import json
import logging
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.research.job_boards import JobBoards
from nextron.app.research.people import PrimaryContacts
from nextron.app.setup.api import Setup
from nextron.domain.contacts import ContactCandidate, PrimaryChoice, PrimarySetBy, ProvenanceSource
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.notifications import EventClosed, EvidenceChanged
from nextron.domain.postings import Board, PostingSource
from nextron.domain.settings_schema import Key
from nextron.ports.errors import AdapterError
from nextron.ports.job_board import BoardListing
from tests.app.test_signal_extraction import (
    CAREERS,
    CAREERS_V1,
    HOME,
    LEGAL_OPS,
    ROBOTS,
    SITE,
    T0,
    World,
    build_world,
    careers_lead,
    careers_site,
    job,
    no_jobs,
    ok,
    postings_of,
    spend_the_budget,
    utc,
)
from tests.fakes.crawler import html_page, robots_txt
from tests.fakes.job_board import BoardAnswer, ScriptedJobBoards, listing, posting

ACME = Board(PostingSource.GREENHOUSE, "acme")
ACME_LEVER = Board(PostingSource.LEVER, "acme")
SMITHLAW = Board(PostingSource.GREENHOUSE, "smithlaw")  # the careers site's link
AP_URL = "https://boards.greenhouse.io/acme/jobs/1"
SALES_URL = "https://boards.greenhouse.io/acme/jobs/2"
AP_CLERK = "Accounts Payable Clerk"
OPS_HOME = (
    "<html><body><h1>Acme Distribution</h1>"
    f'<a href="{AP_URL}">Careers</a>'
    '<a href="https://www.indeed.com/cmp/acme/jobs">Indeed</a>'
    "</body></html>"
)


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def ops(world: World) -> None:
    [(segment,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    world.segment = segment


def older_lead(world: World) -> str:
    """An Ops Lead created before ``ats_lead``'s, so a merge may keep it as the survivor."""
    ops(world)
    return world.lead(web_domain="acme-group.com")


def ats_lead(world: World, home: str = OPS_HOME) -> tuple[str, str]:
    """An Ops Lead whose homepage links a Greenhouse board, crawled at ``T0``."""
    ops(world)
    lead = world.lead()
    world.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow:\n")
    world.crawler.pages[HOME] = html_page(HOME, home)
    return lead, world.crawl(lead)


def boards(answers: Mapping[Board, BoardAnswer]) -> ScriptedJobBoards:
    return ScriptedJobBoards(boards=answers)


def job_boards(world: World, scripted: ScriptedJobBoards) -> JobBoards:
    return JobBoards(world.clock, world.meter, scripted, world.crawler, world.queue)


def check(world: World, scripted: ScriptedJobBoards, lead: str, run_id: str) -> bool:
    with world.units.open():
        return job_boards(world, scripted).check_job_boards(lead, run_id)


def recheck(world: World, scripted: ScriptedJobBoards, signal_id: str) -> None:
    with world.units.open():
        job_boards(world, scripted).recheck_event(signal_id)


def first_listing() -> BoardListing:
    return listing(
        posting(AP_CLERK, AP_URL, posted=date(2026, 11, 10), description="Key in invoices."),
        posting("Sales Rep", SALES_URL),
    )


def counts(world: World, run_id: str) -> dict[str, Any]:
    names = (
        "careers_postings",
        "careers_events",
        "greenhouse_postings",
        "greenhouse_events",
        "lever_postings",
        "lever_events",
        "ashby_postings",
        "ashby_events",
        "board_errors",
    )
    [row] = world.sql(f"SELECT {', '.join(names)} FROM crawl_runs WHERE id = ?", (run_id,))
    return dict(zip(names, row, strict=True))


def evidence_of(world: World, lead: str, evidence_id: str) -> dict[str, Any]:
    [row] = [row for row in world.evidence(lead) if row["id"] == evidence_id]
    return row


def by_title(world: World, lead: str) -> dict[str, dict[str, Any]]:
    return {row["title"]: row for row in postings_of(world, lead)}


def add(world: World, lead: str, name: str, role: str, *, minutes: int = 0) -> str:
    world.clock.advance(timedelta(minutes=minutes))
    added = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name=name, role=role),
            source=ProvenanceSource.CSV,
            detail="list.csv",
            retrieved_at=T0,
        )
    )
    return added.contact_id


# Row: ATS -----------------------------------------------------------------------------------------


def test_a_boards_postings_become_live_postings_and_a_hiring_event(world: World) -> None:
    lead, run_id = ats_lead(world)
    scripted = boards({ACME: first_listing()})

    assert check(world, scripted, lead, run_id) is True

    assert scripted.calls == [ACME]  # one call: the Indeed link is never a board
    found = by_title(world, lead)
    where = {title: (row["source"], row["board_token"], row["url"]) for title, row in found.items()}
    assert where == {
        AP_CLERK: ("greenhouse", "acme", AP_URL),
        "Sales Rep": ("greenhouse", "acme", SALES_URL),
    }
    ap = found[AP_CLERK]
    assert (ap["status"], ap["first_seen_at"], ap["last_checked_at"]) == ("live", utc(T0), utc(T0))
    assert (ap["posted_date"], ap["description"]) == ("2026-11-10", "Key in invoices.")
    assert found["Sales Rep"]["signal_id"] is None  # no hiring term: no Event
    # one Tier 1 Event dated by the posting, with rule-made Quote Evidence of the title
    [signal_row] = world.signals(lead)
    assert signal_row == (
        ap["signal_id"],
        "ops_hiring_manual_entry",
        "event",
        1,
        0,
        "2026-11-10",
        utc(T0),
        None,
    )
    evidence = evidence_of(world, lead, ap["evidence_id"])
    assert (evidence["type"], evidence["quote"], evidence["source"]) == ("quote", AP_CLERK, "ats")
    assert (evidence["source_url"], json.loads(evidence["pages_checked"])) == (AP_URL, [AP_URL])
    assert (evidence["extracted_by"], evidence["retrieved_at"]) == ("rule", utc(T0))
    assert world.links() == {(ap["signal_id"], ap["evidence_id"])}
    assert counts(world, run_id) == {
        "careers_postings": None,  # the careers step didn't check this run
        "careers_events": None,
        "greenhouse_postings": 2,
        "greenhouse_events": 1,
        "lever_postings": 0,
        "lever_events": 0,
        "ashby_postings": 0,
        "ashby_events": 0,
        "board_errors": 0,
    }
    # the chain's next stage is queued (Story 2.12's legal-form check, which then queues the
    # selection), and the selection prefers the Controller for an AP/AR Event
    [(key, concurrency, payload, lane)] = world.jobs("check_legal_form")
    assert (key, concurrency, json.loads(payload), lane) == (
        f"check_legal_form:{lead}:tick",
        f"domain:{SITE}",
        {"v": 1, "lead_id": lead, "run_id": run_id},
        "bulk",
    )
    add(world, lead, "Ann Fox", "COO")
    controller = add(world, lead, "Bo Day", "Controller", minutes=1)
    with world.units.open():
        choice = PrimaryContacts(world.clock, world.research).select_primary_contact(lead)
    assert choice == PrimaryChoice(controller, PrimarySetBy.SYSTEM)


def test_a_description_match_quotes_the_excerpt_holding_the_term(world: World) -> None:
    lead = world.lead()  # a Law Lead
    careers_site(world)
    run_id = world.crawl(lead)
    filler = " ".join(f"Paragraph {index} about the firm." for index in range(40))
    description = f"{filler} The Coordinator runs our client intake every day. {filler}"
    scripted = boards({SMITHLAW: listing(posting("Coordinator", AP_URL, description=description))})

    check(world, scripted, lead, run_id)

    [found] = postings_of(world, lead)
    excerpt = found["description"]
    assert excerpt is not None and len(excerpt) <= 300 and "client intake" in excerpt
    assert excerpt in description
    assert evidence_of(world, lead, found["evidence_id"])["quote"] == excerpt
    assert [row[1] for row in world.signals(lead)] == ["law_hiring_legal_ops"]


def test_an_unmatched_long_description_is_stored_as_its_start(world: World) -> None:
    lead, run_id = ats_lead(world)
    description = " ".join(f"Paragraph {index} about the role." for index in range(40))
    scripted = boards({ACME: listing(posting("Sales Rep", SALES_URL, description=description))})

    check(world, scripted, lead, run_id)

    [found] = postings_of(world, lead)
    assert found["description"] is not None and description.startswith(found["description"])
    assert world.signals(lead) == []


def test_a_future_date_or_a_title_too_long_is_dropped(world: World) -> None:
    lead, run_id = ats_lead(world)
    answer = listing(
        posting(AP_CLERK, AP_URL, posted=T0.date() + timedelta(days=5)),
        posting("x" * 301, SALES_URL),
        posting(" \n ", SALES_URL),
    )

    check(world, boards({ACME: answer}), lead, run_id)

    [found] = postings_of(world, lead)
    assert (found["title"], found["posted_date"]) == (AP_CLERK, None)
    assert world.signals(lead)[0][5] is None  # undated: it ages from first seen


# Row: Closed, and reopened -----------------------------------------------------------------------


def test_a_posting_no_longer_listed_closes_and_its_event_stops_being_live(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]
    t1 = T0 + timedelta(days=1)
    world.clock.set(t1)

    check(world, boards({ACME: listing(posting("Sales Rep", SALES_URL))}), lead, run_id)

    found = by_title(world, lead)
    assert (found[AP_CLERK]["status"], found[AP_CLERK]["closed_at"]) == ("closed", utc(t1))
    assert found[AP_CLERK]["last_checked_at"] == utc(T0)  # it wasn't listed again
    assert (found["Sales Rep"]["status"], found["Sales Rep"]["last_checked_at"]) == (
        "live",
        utc(t1),
    )
    assert evidence_of(world, lead, ap["evidence_id"])["retracted_at"] == utc(t1)
    assert world.signals(lead)[0][-1] == utc(t1)
    assert world.recorder.seen == [EvidenceChanged(lead_id=lead, evidence_id=ap["evidence_id"])]
    assert world.recorder.closed == [EventClosed(signal_id=ap["signal_id"], lead_id=lead)]
    assert counts(world, run_id)["greenhouse_postings"] == 1


def test_a_listed_live_posting_is_confirmed_not_matched_again(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]
    t1 = T0 + timedelta(days=1)
    world.clock.set(t1)

    check(world, boards({ACME: first_listing()}), lead, run_id)

    again = by_title(world, lead)[AP_CLERK]
    assert (again["signal_id"], again["evidence_id"], again["last_checked_at"]) == (
        ap["signal_id"],
        ap["evidence_id"],
        utc(t1),
    )
    assert evidence_of(world, lead, ap["evidence_id"])["last_confirmed_at"] == utc(t1)
    assert len(world.signals(lead)) == 1
    assert counts(world, run_id)["greenhouse_events"] == 0  # none created this check


def test_a_closed_posting_listed_again_reopens_with_a_new_event(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    old = by_title(world, lead)[AP_CLERK]
    world.clock.set(T0 + timedelta(days=1))
    check(world, boards({ACME: listing()}), lead, run_id)
    t2 = T0 + timedelta(days=2)
    world.clock.set(t2)

    check(world, boards({ACME: first_listing()}), lead, run_id)

    reopened = by_title(world, lead)[AP_CLERK]
    assert (reopened["status"], reopened["closed_at"], reopened["last_checked_at"]) == (
        "live",
        None,
        utc(t2),
    )
    assert reopened["signal_id"] not in (None, old["signal_id"])
    assert evidence_of(world, lead, old["evidence_id"])["retracted_at"] is not None
    new = evidence_of(world, lead, reopened["evidence_id"])
    assert (new["retracted_at"], new["retrieved_at"]) == (None, utc(t2))
    # the Event keeps the posting's first-seen time and date
    current = [row for row in world.signals(lead) if row[-1] is None]
    assert [(row[0], row[5], row[6]) for row in current] == [
        (reopened["signal_id"], "2026-11-10", utc(T0))
    ]
    assert counts(world, run_id)["greenhouse_events"] == 1


# Row: Not called ---------------------------------------------------------------------------------


def test_a_host_removed_from_ats_hosts_is_never_called(world: World) -> None:
    lead, run_id = ats_lead(world)
    with world.units.open():
        Setup(world.clock).set(Key.ATS_HOSTS, ("jobs.lever.co",))
    scripted = boards({})

    assert check(world, scripted, lead, run_id) is True

    assert scripted.calls == [] and postings_of(world, lead) == []
    assert counts(world, run_id)["greenhouse_postings"] == 0
    assert len(world.jobs("check_legal_form")) == 1  # the chain still follows


def test_an_unmapped_host_or_a_job_site_is_never_called(world: World) -> None:
    ops(world)
    lead = world.lead()
    with world.units.open():
        Setup(world.clock).set(Key.ATS_HOSTS, ("apply.workable.com", "boards.greenhouse.io"))
    home = (
        '<a href="https://apply.workable.com/acme/">Jobs</a>'  # recorded, but no endpoint
        '<a href="https://uk.indeed.com/cmp/acme">Indeed</a>'  # a job site: never recorded
    )
    world.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow:\n")
    world.crawler.pages[HOME] = html_page(HOME, home)
    run_id = world.crawl(lead)
    assert [row[0] for row in world.sql("SELECT host FROM ats_links")] == ["apply.workable.com"]
    scripted = boards({})

    check(world, scripted, lead, run_id)

    assert scripted.calls == []


def test_only_the_latest_crawls_links_name_boards(world: World) -> None:
    """A link an older crawl run saw names no board, unless a live posting holds it."""
    lead, run_id = ats_lead(world)
    world.clock.set(T0 + timedelta(days=1))
    world.crawler.pages[HOME] = html_page(HOME, "<h1>Acme Distribution</h1>")
    second = world.crawl(lead)  # the link left the site
    scripted = boards({})

    check(world, scripted, lead, second)

    assert scripted.calls == []
    assert [row[0] for row in world.sql("SELECT run_id FROM ats_links")] == [run_id]


def test_a_live_postings_board_is_checked_after_its_link_left_the_site(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    world.clock.set(T0 + timedelta(days=1))
    world.crawler.pages[HOME] = html_page(HOME, "<h1>Acme Distribution</h1>")
    second = world.crawl(lead)  # the link left the site: only the live postings name the board
    scripted = boards({ACME: listing()})

    check(world, scripted, lead, second)

    assert scripted.calls == [ACME]
    assert {row["status"] for row in postings_of(world, lead)} == {"closed"}


def test_a_disabled_sources_postings_are_left_alone(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    before = postings_of(world, lead)
    with world.units.open():
        Setup(world.clock).set(Key.ATS_HOSTS, ("jobs.lever.co",))
    scripted = boards({})

    check(world, scripted, lead, run_id)

    assert scripted.calls == [] and postings_of(world, lead) == before


# Row: Board errors -------------------------------------------------------------------------------


def test_a_board_gone_closes_its_postings(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]

    check(world, boards({ACME: AdapterError(False, "board_not_found", "gone")}), lead, run_id)

    assert {row["status"] for row in postings_of(world, lead)} == {"closed"}
    assert world.recorder.closed == [EventClosed(signal_id=ap["signal_id"], lead_id=lead)]
    assert counts(world, run_id)["board_errors"] == 0


@pytest.mark.parametrize(
    "error",
    [
        AdapterError(True, "board_unavailable", "the job board is unavailable for now"),
        AdapterError(True, "fetch_timeout", "the request timed out"),
        AdapterError(False, "board_invalid", "the job board's answer isn't its JSON shape"),
    ],
)
def test_any_other_error_changes_nothing_and_counts(world: World, error: AdapterError) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    before = postings_of(world, lead)
    world.clock.set(T0 + timedelta(days=1))

    assert check(world, boards({ACME: error}), lead, run_id) is True  # no retry inside the job

    assert postings_of(world, lead) == before  # last_checked_at stays
    assert counts(world, run_id)["board_errors"] == 1
    assert counts(world, run_id)["greenhouse_postings"] == 0
    assert world.recorder.closed == []


def test_each_board_is_applied_on_its_own(world: World) -> None:
    home = OPS_HOME + '<a href="https://jobs.lever.co/acme">Lever</a>'
    lead, run_id = ats_lead(world, home)
    scripted = boards(
        {ACME: first_listing(), ACME_LEVER: AdapterError(True, "board_unavailable", "later")}
    )

    check(world, scripted, lead, run_id)

    assert scripted.calls == [ACME, ACME_LEVER]  # sorted: greenhouse, then lever
    assert len(postings_of(world, lead)) == 2
    found = counts(world, run_id)
    assert (found["greenhouse_postings"], found["board_errors"]) == (2, 1)


# Row: Budget, skips and the chain ----------------------------------------------------------------


def test_when_the_budget_refuses_nothing_is_called_or_written(world: World) -> None:
    lead, run_id = ats_lead(world)
    spend_the_budget(world)
    scripted = boards({ACME: first_listing()})

    assert check(world, scripted, lead, run_id) is False

    assert scripted.calls == [] and postings_of(world, lead) == []
    assert world.jobs("check_legal_form") == []
    assert counts(world, run_id)["board_errors"] is None


def test_an_unknown_or_merged_lead_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    survivor = older_lead(world)
    lead, run_id = ats_lead(world)
    world.transact(lambda: world.discovery.merge(survivor, lead))
    scripted = boards({ACME: first_listing()})

    with caplog.at_level(logging.INFO):
        assert check(world, scripted, lead, run_id) is True
        assert check(world, scripted, "01KA0000000000000000000000", run_id) is True

    assert scripted.calls == [] and world.jobs("check_legal_form") == []
    records = [r for r in caplog.records if r.getMessage() == "job_boards_skipped"]
    assert [getattr(r, "reason") for r in records] == ["merged_lead", "unknown_lead"]


def test_a_lead_merged_during_the_calls_stores_nothing(world: World) -> None:
    survivor = older_lead(world)
    lead, run_id = ats_lead(world)

    def merge_then_list() -> BoardListing:  # in a context of its own: the check's unit is open
        contextvars.Context().run(
            world.transact, lambda: world.discovery.merge(survivor, lead)
        )
        return first_listing()

    check(world, boards({ACME: merge_then_list}), lead, run_id)

    assert world.sql("SELECT COUNT(*) FROM job_postings") == [(0,)]
    assert world.jobs("check_legal_form") == []


def test_extract_lead_queues_check_job_boards_which_the_handler_runs(world: World) -> None:
    lead, run_id, pages = careers_lead(world)
    world.extract(lead, run_id, ok(job_postings=[job(pages[CAREERS], LEGAL_OPS)]))
    [(key, concurrency, payload, lane)] = world.jobs("check_job_boards")
    assert (key, concurrency, json.loads(payload), lane) == (
        f"check_job_boards:{lead}:tick",
        f"domain:{SITE}",
        {"v": 1, "lead_id": lead, "run_id": run_id},
        "bulk",
    )
    assert world.jobs("check_legal_form") == []  # the boards come first
    scripted = boards({SMITHLAW: listing()})
    service = job_boards(world, scripted)
    handler = main.build_job_types(
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
        job_boards=lambda: service,
        legal_forms=no_jobs,
    ).get("check_job_boards").handler

    with world.units.open():
        assert handler(Job(run_id, "check_job_boards", Lane.BULK, json.loads(payload), 1)) is None

    assert scripted.calls == [SMITHLAW]
    assert len(world.jobs("check_legal_form")) == 1
    # the careers posting and its Event are untouched by the board's empty answer
    assert {row["source"]: row["status"] for row in postings_of(world, lead)} == {
        "careers_page": "live"
    }
    spend_the_budget(world)
    with world.units.open():
        assert handler(Job(run_id, "check_job_boards", Lane.BULK, json.loads(payload), 1)) is (
            DEFERRED
        )


def test_the_log_lines_hold_ids_and_counts_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead, run_id = ats_lead(world)

    with caplog.at_level(logging.INFO):
        check(world, boards({ACME: first_listing()}), lead, run_id)

    [record] = [r for r in caplog.records if r.getMessage() == "job_boards_checked"]
    fields = {
        key: getattr(record, key)
        for key in ("lead_id", "run_id", "boards", "board_errors", "listed", "events")
    }
    assert fields == {
        "lead_id": lead,
        "run_id": run_id,
        "boards": 1,
        "board_errors": 0,
        "listed": 2,
        "events": 1,
    }
    assert (getattr(record, "reopened"), getattr(record, "closed")) == (0, 0)
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (AP_CLERK, AP_URL, "acme/jobs", "Key in invoices"):
        assert secret not in text


# Row: Recheck ------------------------------------------------------------------------------------


def test_recheck_of_an_ats_posting_rechecks_its_board_with_the_same_rule(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]
    world.clock.set(T0 + timedelta(days=1))
    scripted = boards({ACME: listing(posting("Sales Rep", SALES_URL))})

    recheck(world, scripted, ap["signal_id"])

    assert scripted.calls == [ACME]  # one board call
    assert by_title(world, lead)[AP_CLERK]["status"] == "closed"
    assert world.recorder.closed == [EventClosed(signal_id=ap["signal_id"], lead_id=lead)]
    assert counts(world, run_id)["greenhouse_postings"] == 2  # a recheck writes no counts
    assert len(world.jobs("check_legal_form")) == 1  # nor queues the next stage


def test_a_recheck_that_still_lists_the_posting_confirms_it(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]
    t1 = T0 + timedelta(days=1)
    world.clock.set(t1)

    recheck(world, boards({ACME: first_listing()}), ap["signal_id"])

    assert by_title(world, lead)[AP_CLERK]["last_checked_at"] == utc(t1)
    assert evidence_of(world, lead, ap["evidence_id"])["last_confirmed_at"] == utc(t1)


def test_a_recheck_reraises_a_retryable_error_and_logs_any_other(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]
    before = postings_of(world, lead)
    unavailable = AdapterError(True, "board_unavailable", "later")

    with pytest.raises(AdapterError) as raised:
        recheck(world, boards({ACME: unavailable}), ap["signal_id"])
    with caplog.at_level(logging.INFO):
        recheck(world, boards({ACME: AdapterError(False, "board_invalid", "x")}), ap["signal_id"])

    assert raised.value is unavailable
    assert postings_of(world, lead) == before
    [record] = [r for r in caplog.records if r.getMessage() == "recheck_failed"]
    assert (getattr(record, "signal_id"), getattr(record, "error_code")) == (
        ap["signal_id"],
        "board_invalid",
    )
    # a board gone closes it, as check_job_boards would
    recheck(world, boards({ACME: AdapterError(False, "board_not_found", "gone")}), ap["signal_id"])
    assert by_title(world, lead)[AP_CLERK]["status"] == "closed"


# A careers page a recheck fetches: long enough not to need a render (500 characters or more of
# visible text), as the crawl's rendered text was.
FILLER = "<p>{}</p>".format(
    " ".join(f"Paragraph {index} about working at Smith Law." for index in range(20))
)


def long_page(body: str) -> str:
    return f"{body}{FILLER}"


def careers_posting(world: World) -> tuple[str, dict[str, Any]]:
    """A Law Lead whose careers page lists the Legal Operations Manager, extracted at ``T0``."""
    lead, run_id, pages = careers_lead(world)
    world.extract(lead, run_id, ok(job_postings=[job(pages[CAREERS], LEGAL_OPS)]))
    [posting_row] = postings_of(world, lead)
    world.crawler.calls.clear()
    return lead, posting_row


def test_a_recheck_of_a_careers_posting_reads_robots_then_the_page(world: World) -> None:
    lead, posting_row = careers_posting(world)
    t1 = T0 + timedelta(days=1)
    world.clock.set(t1)
    world.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nCrawl-delay: 5\nDisallow:\n")
    # the title left the page
    world.crawler.pages[CAREERS] = html_page(CAREERS, long_page("<p>No vacancies right now.</p>"))

    recheck(world, boards({}), posting_row["signal_id"])

    assert [(c.kind, c.url, c.site) for c in world.crawler.calls] == [
        ("robots", ROBOTS, None),
        ("page", CAREERS, SITE),
    ]
    assert world.crawler.calls[1].min_interval_s == 5.0  # robots.txt's crawl delay
    [closed] = postings_of(world, lead)
    assert (closed["status"], closed["closed_at"]) == ("closed", utc(t1))
    assert evidence_of(world, lead, closed["evidence_id"])["retracted_at"] == utc(t1)
    assert world.recorder.closed == [EventClosed(signal_id=closed["signal_id"], lead_id=lead)]
    # the page text is never stored by a recheck
    [(text,)] = world.sql("SELECT text FROM crawled_pages WHERE url = ?", (CAREERS,))
    assert "No vacancies" not in (text or "")


def test_a_careers_recheck_keeps_a_title_still_there_and_a_404_closes(world: World) -> None:
    lead, posting_row = careers_posting(world)
    t1 = T0 + timedelta(days=1)
    world.clock.set(t1)
    world.crawler.pages[CAREERS] = html_page(CAREERS, long_page(CAREERS_V1))

    recheck(world, boards({}), posting_row["signal_id"])

    assert postings_of(world, lead)[0]["last_checked_at"] == utc(t1)
    world.crawler.pages[CAREERS] = html_page(CAREERS, status=404)

    recheck(world, boards({}), posting_row["signal_id"])

    assert postings_of(world, lead)[0]["status"] == "closed"


def test_a_careers_page_that_needs_a_render_isnt_judged(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """A JavaScript page's raw text: its titles were checked against the rendered text the
    crawl stored, so they can't be judged against it."""
    lead, posting_row = careers_posting(world)
    before = postings_of(world, lead)
    world.crawler.pages[CAREERS] = html_page(CAREERS, '<div id="app">Loading</div>')

    with caplog.at_level(logging.INFO):
        recheck(world, boards({}), posting_row["signal_id"])

    assert postings_of(world, lead) == before and world.recorder.closed == []
    [record] = [r for r in caplog.records if r.getMessage() == "recheck_failed"]
    assert getattr(record, "error_code") == "needs_render"


def test_a_careers_recheck_robots_disallows_skips(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead, posting_row = careers_posting(world)
    before = postings_of(world, lead)
    world.crawler.robots[ROBOTS] = robots_txt(ROBOTS, "User-agent: *\nDisallow: /careers\n")

    with caplog.at_level(logging.INFO):
        recheck(world, boards({}), posting_row["signal_id"])

    assert [c.kind for c in world.crawler.calls] == ["robots"]  # the page isn't fetched
    assert postings_of(world, lead) == before
    [record] = [r for r in caplog.records if r.getMessage() == "recheck_skipped"]
    assert getattr(record, "reason") == "robots_disallowed"


@pytest.mark.parametrize(
    ("robots", "raised"),
    [
        (AdapterError(True, "fetch_timeout", "the request timed out"), "fetch_timeout"),
        (robots_txt(ROBOTS, "", status=503), "http_503"),
        (AdapterError(False, "fetch_dns_failed", "the host name did not resolve"), None),
        (robots_txt(ROBOTS, "", status=429), None),  # not a retryable crawl code
    ],
)
def test_a_careers_recheck_whose_robots_txt_fails_fetches_no_page(
    world: World, caplog: pytest.LogCaptureFixture, robots: Any, raised: str | None
) -> None:
    lead, posting_row = careers_posting(world)
    before = postings_of(world, lead)
    world.crawler.robots[ROBOTS] = robots

    with caplog.at_level(logging.INFO):
        if raised is None:
            recheck(world, boards({}), posting_row["signal_id"])
        else:
            with pytest.raises(AdapterError) as error:
                recheck(world, boards({}), posting_row["signal_id"])
            assert (error.value.code, error.value.retryable) == (raised, True)

    assert world.crawler.urls("page") == []
    assert postings_of(world, lead) == before
    failed = [r for r in caplog.records if r.getMessage() == "recheck_failed"]
    if raised is None:
        expected = robots.code if isinstance(robots, AdapterError) else "http_429"
        assert [getattr(r, "error_code") for r in failed] == [expected]
    else:
        assert failed == []
    assert not [r for r in caplog.records if getattr(r, "reason", None) == "robots_disallowed"]


@pytest.mark.parametrize(
    ("status", "retried"), [(500, True), (503, True), (429, True), (403, False)]
)
def test_a_careers_recheck_failure_changes_nothing_or_is_retried(
    world: World, status: int, retried: bool
) -> None:
    lead, posting_row = careers_posting(world)
    before = postings_of(world, lead)
    world.crawler.pages[CAREERS] = html_page(CAREERS, status=status)

    if retried:
        with pytest.raises(AdapterError) as error:
            recheck(world, boards({}), posting_row["signal_id"])
        assert (error.value.code, error.value.retryable) == (f"http_{status}", True)
    else:
        recheck(world, boards({}), posting_row["signal_id"])

    assert postings_of(world, lead) == before
    world.crawler.pages[CAREERS] = AdapterError(True, "fetch_timeout", "the request timed out")
    with pytest.raises(AdapterError):
        recheck(world, boards({}), posting_row["signal_id"])
    assert postings_of(world, lead) == before


@pytest.mark.parametrize("case", ["no_posting", "posting_closed", "merged_lead", "source_disabled"])
def test_a_recheck_skips_what_it_cant_check(
    world: World, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    survivor = older_lead(world)
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    signal_id = by_title(world, lead)[AP_CLERK]["signal_id"]
    if case == "no_posting":
        signal_id = "01KA0000000000000000000000"
    elif case == "posting_closed":
        check(world, boards({ACME: listing()}), lead, run_id)
    elif case == "merged_lead":
        world.transact(lambda: world.discovery.merge(survivor, lead))
        world.sql("UPDATE job_postings SET lead_id = ? WHERE lead_id = ?", (lead, survivor))
    else:
        with world.units.open():
            Setup(world.clock).set(Key.ATS_HOSTS, ("jobs.lever.co",))
    scripted = boards({})

    with caplog.at_level(logging.INFO):
        recheck(world, scripted, signal_id)

    assert scripted.calls == []
    [record] = [r for r in caplog.records if r.getMessage() == "recheck_skipped"]
    assert getattr(record, "reason") == case


def test_the_recheck_event_handler_checks_the_signal_id(world: World) -> None:
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    ap = by_title(world, lead)[AP_CLERK]
    scripted = boards({ACME: listing()})
    service = job_boards(world, scripted)
    handler = main.build_job_types(
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
        job_boards=lambda: service,
        legal_forms=no_jobs,
    ).get("recheck_event").handler
    payload = {"v": 1, "signal_id": ap["signal_id"]}

    with world.units.open():
        assert handler(Job(run_id, "recheck_event", Lane.INTERACTIVE, payload, 1)) is None

    assert scripted.calls == [ACME]
    assert by_title(world, lead)[AP_CLERK]["status"] == "closed"


# Merges ------------------------------------------------------------------------------------------


def test_a_merge_moves_postings_and_closes_a_colliding_one(world: World) -> None:
    survivor = older_lead(world)
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)
    # the survivor already holds the Sales Rep posting (another crawl of the same board)
    world.sql(
        "INSERT INTO job_postings (id, lead_id, source, board_token, url, title, description, "
        "posted_date, first_seen_at, last_checked_at, status, closed_at, signal_id, evidence_id) "
        "VALUES ('01KA00000000000000000000P1', ?, 'greenhouse', 'acme', ?, 'Sales Rep', NULL, "
        "NULL, ?, ?, 'live', NULL, NULL, NULL)",
        (survivor, SALES_URL, utc(T0), utc(T0)),
    )
    # and its own AP Clerk Event, so the merged Lead's colliding one must close
    world.sql(
        "INSERT INTO job_postings (id, lead_id, source, board_token, url, title, description, "
        "posted_date, first_seen_at, last_checked_at, status, closed_at, signal_id, evidence_id) "
        "VALUES ('01KA00000000000000000000P2', ?, 'greenhouse', 'acme', ?, ?, NULL, NULL, ?, ?, "
        "'live', NULL, NULL, NULL)",
        (survivor, AP_URL, AP_CLERK, utc(T0), utc(T0)),
    )
    ap = by_title(world, lead)[AP_CLERK]
    t1 = T0 + timedelta(hours=1)
    world.clock.set(t1)

    world.transact(lambda: world.discovery.merge(survivor, lead))

    stayed = by_title(world, lead)
    assert set(stayed) == {AP_CLERK, "Sales Rep"}  # both collide: both stay on the merged Lead
    assert (stayed[AP_CLERK]["status"], stayed[AP_CLERK]["closed_at"]) == ("closed", utc(t1))
    assert stayed["Sales Rep"]["status"] == "closed"
    assert evidence_of(world, survivor, ap["evidence_id"])["retracted_at"] == utc(t1)
    assert [row[-1] for row in world.signals(survivor)] == [utc(t1)]
    assert world.recorder.seen == [
        EvidenceChanged(lead_id=survivor, evidence_id=ap["evidence_id"])
    ]
    assert world.recorder.closed == [EventClosed(signal_id=ap["signal_id"], lead_id=survivor)]
    assert {row["status"] for row in postings_of(world, survivor)} == {"live"}


def test_a_merge_moves_a_posting_the_survivor_lacks(world: World) -> None:
    survivor = older_lead(world)
    lead, run_id = ats_lead(world)
    check(world, boards({ACME: first_listing()}), lead, run_id)

    world.transact(lambda: world.discovery.merge(survivor, lead))

    assert postings_of(world, lead) == []
    moved = by_title(world, survivor)
    assert {row["status"] for row in moved.values()} == {"live"}
    assert [row[0] for row in world.signals(survivor)] == [moved[AP_CLERK]["signal_id"]]
    assert world.recorder.closed == []
