"""Story 2.10's people step on a temporary database at head (FR-10, FR-13, FR-51, AD-7, AD-10,
AD-17, AD-18): ``SignalExtractions.extract_lead`` then ``PeopleExtractions.extract`` through
``ScriptedLlm`` on pages a real ``WebsiteCrawls`` stored from a ``ScriptedCrawler``, with the
shipped Segments, Markets and prompts (``active_tuple``) and a ``FrozenClock``. The world is
Story 2.9's (``tests.app.test_signal_extraction``): its Signals step answers on one
``ScriptedLlm`` and the people step on another (``World.people_calls``).

Every row of the spec's matrix the people step owns is here: the people of a Law Lead (the
Contact, its office and Market, its Quote Evidence and Provenance, the lawyer count, the
selection job), refused people, a re-crawl that drops a person, the budget's second check, and
also the blocks sent, no work (no activation, no changed page), bad results, a retryable failure,
a merge during the call, the log line and the route split of ``last_ok_run``. The selection
itself is ``tests/app/test_primary_contact.py``'s. No real Claude request is made."""

import contextvars
import json
import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron.app.research.extraction import SignalExtractions
from nextron.app.research import people as people_module
from nextron.app.research.people import PeopleExtractions, PrimaryContacts
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.ai import RouteTuple, UntrustedBlock, Urgency
from nextron.domain.budget import AiCall
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.notifications import EvidenceChanged
from nextron.ports.errors import AdapterError
from nextron.ports.llm import LlmFailed, LlmOk, LlmRefused, LlmResult, LlmUsage
from tests.app.test_signal_extraction import (
    ABOUT,
    HAIKU,
    HOME,
    NO_PEOPLE,
    T0,
    TEAM,
    World,
    build_world,
    crawled,
    ok,
    signal,
    utc,
)
from tests.fakes.crawler import html_page
from tests.fakes.llm import ScriptedLlm

PEOPLE_ACTOR = "ai:extract_people@v1"
SITE = "smith-law.com"
JANE = "Jane Roe, Managing Partner, Austin, TX"
ALEX = "Alex Poe, Associate"
PAT = "Pat Moe, Paralegal"
KIM = "Kim Lee, Managing Partner"
LAW_TEAM = f"<p>{JANE}</p><p>{ALEX}</p><p>{PAT}</p>"
LAW_HIRING = "We are hiring a Legal Operations Manager."
LAW_ABOUT = f"<p>Founded in 1999 by Jane Roe.</p><p>{LAW_HIRING}</p>"
TEAM_URL = f"https://{SITE}/team"
ABOUT_URL = f"https://{SITE}/about"
USAGE = LlmUsage(requests=1)


def person(page: str, name: str, role: str, quote: str) -> dict[str, Any]:
    return {"name": name, "role": role, "block_id": page, "quote": quote}


def people(*items: dict[str, Any]) -> LlmOk:
    return LlmOk({"people": list(items)}, model=HAIKU, usage=USAGE)


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def law_lead(world: World) -> tuple[str, str, dict[str, str]]:
    return crawled(world, team=LAW_TEAM, about=LAW_ABOUT)


def law_people(pages: dict[str, str]) -> LlmOk:
    return people(
        person(pages[TEAM], "Jane Roe", "Managing Partner", JANE),
        person(pages[TEAM], "Alex Poe", "Associate", ALEX),
        person(pages[TEAM], "Pat Moe", "Paralegal", PAT),
    )


def people_runs(world: World, lead: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT outcome, error_code, actor, pages_sent, pages_truncated, stored, discarded "
        "FROM extraction_runs WHERE lead_id = ? AND route = 'extract_people' ORDER BY rowid",
        (lead,),
    )


def contacts(world: World, lead: str) -> list[dict[str, Any]]:
    columns = (
        "id, name, role, email, office_country, office_region, market_basis, tz, tz_basis, "
        "is_primary, primary_set_by"
    )
    rows = world.sql(
        f"SELECT {columns} FROM contacts WHERE lead_id = ? ORDER BY created_at, id", (lead,)
    )
    names = [name.strip() for name in columns.split(",")]
    return [dict(zip(names, row, strict=True)) for row in rows]


def person_evidence(world: World, lead: str) -> list[dict[str, Any]]:
    return [row for row in world.evidence(lead) if row["person_contact_id"] is not None]


def counts(world: World, lead: str) -> list[dict[str, Any]]:
    return [row for row in world.evidence(lead) if row["type"] == "count"]


def select_jobs(world: World) -> list[tuple[Any, ...]]:
    return world.jobs("select_primary_contact")


def page_fetched(world: World, lead: str) -> str:
    [(fetched,)] = world.sql(
        "SELECT DISTINCT fetched_at FROM crawled_pages WHERE lead_id = ?", (lead,)
    )
    return str(fetched)


# Row: People ------------------------------------------------------------------------------------


def test_a_law_leads_decision_maker_becomes_a_contact_with_evidence_and_a_lawyer_count(
    world: World,
) -> None:
    lead, run_id, pages = law_lead(world)

    assert world.extract(lead, run_id, ok(), people=[law_people(pages)]) is True

    # the call: the active extract_people tuple, bulk, the team then the about page
    [(route_tuple, blocks, urgency)] = world.people_calls
    assert (route_tuple.route, route_tuple.actor, urgency) == (
        "extract_people",
        PEOPLE_ACTOR,
        Urgency.BULK,
    )
    assert [(b.subject, b.source, b.block_id) for b in blocks] == [
        (f"lead:{lead}", "website", pages[TEAM]),
        (f"lead:{lead}", "website", pages[ABOUT]),
    ]
    assert blocks[0].text == f"{JANE}\n{ALEX}\n{PAT}"
    # only Jane, a Managing Partner, is a Contact: her office and Market from her own quote
    [jane] = contacts(world, lead)
    assert {k: v for k, v in jane.items() if k != "id"} == {
        "name": "Jane Roe",
        "role": "Managing Partner",
        "email": None,
        "office_country": "US",
        "office_region": "TX",
        "market_basis": "office_location",
        "tz": "America/Chicago",
        "tz_basis": "office_location",
        "is_primary": 0,
        "primary_set_by": None,
    }
    fetched = page_fetched(world, lead)
    assert world.sql(
        "SELECT subject, source_kind, source_detail, retrieved_at FROM provenance_records "
        "WHERE contact_id = ?",
        (jane["id"],),
    ) == [("contact", "website", f"https://{SITE}/team", fetched)]
    # her Quote Evidence
    [quote] = person_evidence(world, lead)
    assert {
        key: quote[key]
        for key in (
            "type",
            "quote",
            "sought",
            "number",
            "fact",
            "pages_checked",
            "source",
            "source_url",
            "retrieved_at",
            "last_confirmed_at",
            "extracted_by",
            "person_contact_id",
        )
    } == {
        "type": "quote",
        "quote": JANE,
        "sought": None,
        "number": None,
        "fact": None,
        "pages_checked": json.dumps([TEAM_URL], separators=(",", ":")),
        "source": "website",
        "source_url": TEAM_URL,
        "retrieved_at": fetched,
        "last_confirmed_at": fetched,
        "extracted_by": PEOPLE_ACTOR,
        "person_contact_id": jane["id"],
    }
    # the lawyer count: Jane and the associate, never the paralegal
    [count] = counts(world, lead)
    assert {
        key: count[key]
        for key in (
            "quote",
            "sought",
            "number",
            "fact",
            "pages_checked",
            "source",
            "source_url",
            "retrieved_at",
            "extracted_by",
            "person_contact_id",
        )
    } == {
        "quote": None,
        "sought": "lawyers listed",
        "number": 2,
        "fact": None,
        "pages_checked": json.dumps([TEAM_URL, ABOUT_URL], separators=(",", ":")),
        "source": "website",
        "source_url": None,
        "retrieved_at": fetched,
        "extracted_by": "rule",
        "person_contact_id": None,
    }
    # the run and the selection job
    assert people_runs(world, lead) == [("ok", None, PEOPLE_ACTOR, 2, 0, 2, 0)]
    [(key, concurrency, payload, lane)] = select_jobs(world)
    assert (key, concurrency, json.loads(payload), lane) == (
        f"select_primary_contact:{lead}:tick",
        f"domain:{SITE}",
        {"v": 1, "lead_id": lead},
        "bulk",
    )
    # nobody in another role is stored as a Contact
    assert world.sql("SELECT COUNT(*) FROM contacts WHERE name IN ('Alex Poe', 'Pat Moe')") == [
        (0,)
    ]


def test_a_person_already_on_the_lead_is_filled_not_duplicated(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name="Jane Roe"),
            source=ProvenanceSource.CSV,
            detail="list.csv",
            retrieved_at=T0,
        )
    )

    world.extract(lead, run_id, ok(), people=[law_people(pages)])

    [jane] = contacts(world, lead)
    assert (jane["role"], jane["office_country"], jane["office_region"]) == (
        "Managing Partner",
        "US",
        "TX",
    )
    [quote] = person_evidence(world, lead)
    assert quote["person_contact_id"] == jane["id"]


def test_the_office_is_kept_only_when_it_never_loosens_the_rules(world: World) -> None:
    # a UK Lead whose team page names a Texas office: a US office would loosen the UK rules
    lead = world.lead(country="GB")
    world.site(team=f"<p>{JANE}</p>", about="<p>About us</p>")
    run_id = world.crawl(lead)
    pages = world.page_ids(lead)

    world.extract(
        lead,
        run_id,
        ok(),
        people=[people(person(pages[TEAM], "Jane Roe", "Managing Partner", JANE))],
    )

    [jane] = contacts(world, lead)
    assert (jane["office_country"], jane["office_region"], jane["tz"]) == (
        None,
        None,
        "Europe/London",
    )


# Row: Refused -----------------------------------------------------------------------------------


def test_refused_people_store_nothing_and_each_writes_one_event(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    answer = people(
        person(pages[TEAM], "Jane Roe", "Managing Partner", "Jane Roe, Managing Partner, Dallas"),
        person(pages[TEAM], "John Doe", "Managing Partner", JANE),
        person("01KA00000000000000000000X1", "Jane Roe", "Managing Partner", JANE),
    )

    world.extract(lead, run_id, ok(), people=[answer])

    assert contacts(world, lead) == [] and world.evidence(lead) == []
    assert world.events(lead) == [
        (lead, None, "person_discarded", "quote_not_in_page", pages[TEAM], PEOPLE_ACTOR),
        (lead, None, "person_discarded", "name_not_in_quote", pages[TEAM], PEOPLE_ACTOR),
        (lead, None, "person_discarded", "unknown_block", None, PEOPLE_ACTOR),
    ]
    assert people_runs(world, lead) == [("ok", None, PEOPLE_ACTOR, 2, 0, 0, 3)]
    assert len(select_jobs(world)) == 1  # selection still runs: the Lead has no decision-maker


def test_people_in_other_roles_are_never_stored(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    answer = people(
        person(pages[TEAM], "Alex Poe", "Associate", ALEX),
        person(pages[TEAM], "Pat Moe", "Paralegal", PAT),
    )

    world.extract(lead, run_id, ok(), people=[answer])

    assert contacts(world, lead) == [] and person_evidence(world, lead) == []
    [count] = counts(world, lead)
    assert count["number"] == 1  # the associate is still a lawyer listed


# Row: Re-crawl ----------------------------------------------------------------------------------


def test_a_person_gone_from_the_changed_team_page_has_their_evidence_retracted(
    world: World,
) -> None:
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    [jane] = contacts(world, lead)
    [old_quote] = person_evidence(world, lead)
    [old_count] = counts(world, lead)

    world.clock.advance(timedelta(days=1))
    world.site(team=f"<p>{KIM}</p><p>{ALEX}</p>", about=LAW_ABOUT)
    second = world.crawl(lead)
    pages = world.page_ids(lead)
    answer = people(
        person(pages[TEAM], "Kim Lee", "Managing Partner", KIM),
        person(pages[TEAM], "Alex Poe", "Associate", ALEX),
    )
    world.extract(lead, second, ok(), people=[answer])

    rows = {row["id"]: row for row in world.evidence(lead)}
    assert rows[old_quote["id"]]["retracted_at"] == utc(world.clock.now())
    [kim] = [c for c in contacts(world, lead) if c["name"] == "Kim Lee"]
    live = [r for r in person_evidence(world, lead) if r["retracted_at"] is None]
    assert [(r["person_contact_id"], r["quote"]) for r in live] == [(kim["id"], KIM)]
    # the count changed from 2 (Jane and Alex) to 2 (Kim and Alex) but its pages are the same
    assert rows[old_count["id"]]["superseded_by"] is None
    assert rows[old_count["id"]]["last_confirmed_at"] == utc(world.clock.now())
    assert {
        n.evidence_id for n in world.recorder.seen if isinstance(n, EvidenceChanged)
    } == {old_quote["id"]}
    assert people_runs(world, lead)[1] == ("ok", None, PEOPLE_ACTOR, 2, 0, 1, 0)
    # Jane is departed: the selection picks Kim
    with world.units.open():
        choice = PrimaryContacts(world.clock, world.research).select_primary_contact(lead)
    assert choice is not None and choice.contact_id == kim["id"]
    assert jane["id"] != kim["id"]


def test_a_changed_count_supersedes_the_old_one(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    [old_count] = counts(world, lead)

    world.clock.advance(timedelta(days=1))
    world.site(team=f"<p>{JANE}</p>", about=LAW_ABOUT)
    second = world.crawl(lead)
    pages = world.page_ids(lead)
    world.extract(
        lead,
        second,
        ok(),
        people=[people(person(pages[TEAM], "Jane Roe", "Managing Partner", JANE))],
    )

    rows = {row["id"]: row for row in world.evidence(lead)}
    new_id = rows[old_count["id"]]["superseded_by"]
    assert new_id is not None and rows[new_id]["number"] == 1
    assert old_count["id"] in {n.evidence_id for n in world.recorder.seen}
    [jane_quote] = [r for r in person_evidence(world, lead) if r["retracted_at"] is None]
    assert jane_quote["last_confirmed_at"] == utc(world.clock.now())  # confirmed, not new


def people_log(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "nextron.app.research.people" and r.getMessage() == "people_extracted"
    ]


def recrawl(world: World, lead: str, team: str) -> tuple[str, dict[str, str]]:
    """Advance a day and crawl the site again with this team page; the run and page ids."""
    world.clock.advance(timedelta(days=1))
    world.site(team=team, about=LAW_ABOUT)
    run_id = world.crawl(lead)
    return run_id, world.page_ids(lead)


JANE_DALLAS = "Jane Roe, Managing Partner, Dallas, TX"


def test_a_decision_makers_changed_line_supersedes_their_evidence(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.people")
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    [jane] = contacts(world, lead)
    [old_quote] = person_evidence(world, lead)

    second, pages = recrawl(world, lead, f"<p>{JANE_DALLAS}</p><p>{ALEX}</p><p>{PAT}</p>")
    answer = people(
        person(pages[TEAM], "Jane Roe", "Managing Partner", JANE_DALLAS),
        person(pages[TEAM], "Alex Poe", "Associate", ALEX),
        person(pages[TEAM], "Pat Moe", "Paralegal", PAT),
    )
    world.extract(lead, second, ok(), people=[answer])

    rows = {row["id"]: row for row in world.evidence(lead)}
    new_id = rows[old_quote["id"]]["superseded_by"]
    assert new_id is not None
    assert (rows[new_id]["quote"], rows[new_id]["person_contact_id"]) == (JANE_DALLAS, jane["id"])
    assert old_quote["id"] in {n.evidence_id for n in world.recorder.seen}
    assert people_runs(world, lead)[1] == ("ok", None, PEOPLE_ACTOR, 2, 0, 1, 0)
    [_, record] = people_log(caplog)
    assert (getattr(record, "stored"), getattr(record, "superseded")) == (1, 1)
    assert [c["name"] for c in contacts(world, lead)] == ["Jane Roe"]  # not a second Jane
    # she isn't departed: the selection still picks her
    with world.units.open():
        choice = PrimaryContacts(world.clock, world.research).select_primary_contact(lead)
    assert choice is not None and choice.contact_id == jane["id"]


def test_a_quote_that_qualifies_the_role_away_stores_no_contact(world: World) -> None:
    assistant = "Jane Roe, Assistant to the Managing Partner"
    lead, run_id, pages = crawled(world, team=f"<p>{assistant}</p><p>{KIM}</p>", about=LAW_ABOUT)
    answer = people(
        person(pages[TEAM], "Jane Roe", "Managing Partner", assistant),
        person(pages[TEAM], "Kim Lee", "Managing Partner", KIM),
    )

    world.extract(lead, run_id, ok(), people=[answer])

    assert [c["name"] for c in contacts(world, lead)] == ["Kim Lee"]
    [count] = counts(world, lead)
    assert count["number"] == 1  # the assistant isn't a lawyer listed either


def test_no_count_is_stored_or_confirmed_from_cut_pages(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    [count] = counts(world, lead)

    monkeypatch.setattr(people_module, "PAGE_TEXT_BUDGET", 40)
    world.clock.advance(timedelta(hours=1))
    world.sql("UPDATE crawled_pages SET changed_at = ?", (utc(T0 + timedelta(minutes=30)),))
    jane_only = people(person(pages[TEAM], "Jane Roe", "Managing Partner", JANE))
    world.extract(lead, run_id, ok(), people=[jane_only])

    assert people_runs(world, lead)[1][4] == 2  # both pages were cut
    assert counts(world, lead) == [count]  # neither superseded nor confirmed again
    assert len(contacts(world, lead)) == 1  # the quotes still check against the full text


def second_count(world: World, lead: str, number: int) -> str:
    """Another live lawyer count on the Lead, as a merge brings in."""
    return world.transact(
        lambda: signals_repo.insert_evidence(
            lead_id=lead,
            evidence_type="count",
            quote=None,
            sought="lawyers listed",
            number=number,
            fact=None,
            value=None,
            pages_checked=("https://smith-law-llp.com/team",),
            source="website",
            source_url=None,
            retrieved_at=T0,
            extracted_by="rule",
            person_contact_id=None,
            at=T0,
        )
    )


def test_a_confirmed_count_retracts_every_other_live_count(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.people")
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    [kept] = counts(world, lead)
    other = second_count(world, lead, 5)

    world.clock.advance(timedelta(hours=1))
    world.sql("UPDATE crawled_pages SET changed_at = ?", (utc(T0 + timedelta(minutes=30)),))
    world.extract(lead, run_id, ok(), people=[law_people(pages)])

    rows = {row["id"]: row for row in world.evidence(lead)}
    assert (rows[kept["id"]]["superseded_by"], rows[kept["id"]]["retracted_at"]) == (None, None)
    assert rows[other]["retracted_at"] == utc(world.clock.now())
    assert other in {n.evidence_id for n in world.recorder.seen}
    [_, record] = people_log(caplog)
    assert (getattr(record, "superseded"), getattr(record, "retracted")) == (0, 1)


def test_a_new_count_supersedes_every_live_count(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.people")
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    [first] = counts(world, lead)
    other = second_count(world, lead, 5)

    second, pages = recrawl(world, lead, f"<p>{JANE}</p>")
    world.extract(
        lead,
        second,
        ok(),
        people=[people(person(pages[TEAM], "Jane Roe", "Managing Partner", JANE))],
    )

    rows = {row["id"]: row for row in world.evidence(lead)}
    new_id = rows[first["id"]]["superseded_by"]
    assert new_id is not None and rows[new_id]["number"] == 1
    assert rows[other]["superseded_by"] == new_id
    assert {first["id"], other} <= {n.evidence_id for n in world.recorder.seen}
    [_, record] = people_log(caplog)
    assert (getattr(record, "superseded"), getattr(record, "retracted")) == (2, 0)


def test_a_lead_outside_the_law_segment_gets_no_count(world: World) -> None:
    [(ops,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    world.segment = ops
    lead, run_id, pages = crawled(world, team="<p>Ann Fox, COO</p><p>Bo Day, Partner</p>")

    world.extract(
        lead,
        run_id,
        ok(),
        people=[
            people(
                person(pages[TEAM], "Ann Fox", "COO", "Ann Fox, COO"),
                person(pages[TEAM], "Bo Day", "Partner", "Bo Day, Partner"),
            )
        ],
    )

    assert counts(world, lead) == []
    assert [c["name"] for c in contacts(world, lead)] == ["Ann Fox"]


def test_no_team_page_sent_means_no_count(world: World) -> None:
    lead = world.lead()
    world.site(about=f"<p>{JANE}</p><p>{ALEX}</p>")
    world.crawler.pages[TEAM_URL] = html_page(TEAM_URL, status=404)
    run_id = world.crawl(lead)
    pages = world.page_ids(lead)

    world.extract(
        lead,
        run_id,
        ok(),
        people=[people(person(pages[ABOUT], "Jane Roe", "Managing Partner", JANE))],
    )

    assert len(contacts(world, lead)) == 1 and counts(world, lead) == []


# Row: Budget ------------------------------------------------------------------------------------


class SpendingLlm:
    """The Signals step's model: answers, then meters the month's whole AI budget, as the
    gateway meters each call."""

    def __init__(self, world: World, answer: LlmResult) -> None:
        self.world = world
        self.answer = answer

    def run(
        self, route_tuple: RouteTuple, inputs: Sequence[UntrustedBlock], urgency: Urgency
    ) -> LlmResult:
        self.world.meter.record(
            AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
        )
        return self.answer


def test_the_people_steps_own_budget_check_defers_the_job(world: World) -> None:
    lead, run_id, _ = law_lead(world)

    assert world.extract(lead, run_id, llm=SpendingLlm(world, ok()), people=[]) is False

    assert world.people_calls == [] and people_runs(world, lead) == []
    assert world.runs(lead)[0][0] == "ok"  # the Signals step stored its run
    assert select_jobs(world) == []


# No work, bad results, failures -----------------------------------------------------------------


def test_no_changed_people_page_records_a_no_pages_run_and_still_selects(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    world.clock.advance(timedelta(hours=1))
    world.sql("UPDATE jobs SET status = 'done' WHERE type = 'select_primary_contact'")
    # only the homepage changed: the Signals step sends it, the people step sends nothing
    world.sql(
        "UPDATE crawled_pages SET changed_at = ? WHERE url = ?",
        (utc(T0 + timedelta(minutes=30)), HOME),
    )

    world.extract(lead, run_id, ok(), people=[])

    assert len(world.people_calls) == 1
    assert people_runs(world, lead)[1] == ("no_pages", None, PEOPLE_ACTOR, 0, 0, 0, 0)
    assert len(select_jobs(world)) == 2


def test_any_changed_people_page_sends_every_people_page(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    world.extract(lead, run_id, ok(), people=[law_people(pages)])
    world.clock.advance(timedelta(hours=1))
    world.sql(
        "UPDATE crawled_pages SET changed_at = ? WHERE url = ?",
        (utc(T0 + timedelta(minutes=30)), ABOUT_URL),
    )

    world.extract(lead, run_id, ok(), people=[law_people(pages)])

    [_, (_, blocks, _)] = world.people_calls
    assert [b.block_id for b in blocks] == [pages[TEAM], pages[ABOUT]]
    [jane_quote] = person_evidence(world, lead)  # confirmed, not stored again
    assert jane_quote["last_confirmed_at"] == page_fetched(world, lead)


def test_with_no_activation_a_no_activation_run_is_recorded(db_engine: Engine) -> None:
    world = build_world(db_engine, prompts=False)
    lead, run_id, _ = law_lead(world)

    assert world.extract(lead, run_id, people=[]) is True

    assert people_runs(world, lead) == [("no_activation", None, None, 0, 0, 0, 0)]
    assert len(select_jobs(world)) == 1


@pytest.mark.parametrize(
    ("result", "outcome", "code"),
    [
        (LlmRefused("cyber", model=HAIKU, usage=USAGE), "refused", "cyber"),
        (
            LlmFailed(False, "llm_bad_request", 400, model=HAIKU, usage=USAGE),
            "failed",
            "llm_bad_request",
        ),
    ],
)
def test_a_bad_result_records_its_outcome_and_still_selects(
    world: World, result: LlmResult, outcome: str, code: str
) -> None:
    lead, run_id, _ = law_lead(world)

    assert world.extract(lead, run_id, ok(), people=[result]) is True

    assert people_runs(world, lead) == [(outcome, code, PEOPLE_ACTOR, 2, 0, 0, 0)]
    assert contacts(world, lead) == [] and len(select_jobs(world)) == 1


def test_a_retryable_failure_records_a_failed_run_and_raises_without_selecting(
    world: World,
) -> None:
    lead, run_id, _ = law_lead(world)

    with pytest.raises(AdapterError):
        world.extract(lead, run_id, ok(), people=[AdapterError(True, "llm_timeout", "slow")])

    assert people_runs(world, lead) == [("failed", "llm_timeout", PEOPLE_ACTOR, 2, 0, 0, 0)]
    assert select_jobs(world) == []


def test_a_signals_failure_that_raises_skips_the_people_step(world: World) -> None:
    lead, run_id, _ = law_lead(world)

    with pytest.raises(AdapterError):
        world.extract(lead, run_id, AdapterError(True, "llm_timeout", "slow"), people=[])

    assert world.people_calls == [] and people_runs(world, lead) == []


def test_a_signals_result_other_than_ok_still_runs_the_people_step(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    refused = LlmRefused("cyber", model=HAIKU, usage=USAGE)

    world.extract(lead, run_id, refused, people=[law_people(pages)])

    assert world.runs(lead)[0][0] == "refused"
    assert people_runs(world, lead)[0][0] == "ok" and len(contacts(world, lead)) == 1


class MergingPeopleLlm:
    """The people step's model: merges the Lead into another, as another job would, then
    answers."""

    def __init__(self, world: World, survivor: str, loser: str, answer: LlmResult) -> None:
        self.world, self.survivor, self.loser, self.answer = world, survivor, loser, answer

    def run(
        self, route_tuple: RouteTuple, inputs: Sequence[UntrustedBlock], urgency: Urgency
    ) -> LlmResult:
        contextvars.Context().run(
            self.world.transact, lambda: self.world.discovery.merge(self.survivor, self.loser)
        )
        return self.answer


def test_a_lead_merged_during_the_people_call_records_lead_merged(world: World) -> None:
    older = world.lead(web_domain="smith-law-llp.com")
    lead, run_id, pages = law_lead(world)
    step = PeopleExtractions(
        world.clock,
        world.meter,
        MergingPeopleLlm(world, older, lead, law_people(pages)),
        world.research,
        world.queue,
    )
    extractions = SignalExtractions(
        world.clock, world.meter, ScriptedLlm(ok()), world.research, step
    )

    with world.units.open():
        assert extractions.extract_lead(lead, run_id) is True

    assert people_runs(world, lead) == [("lead_merged", None, PEOPLE_ACTOR, 2, 0, 0, 0)]
    assert world.sql("SELECT COUNT(*) FROM contacts") == [(0,)]
    assert world.sql("SELECT COUNT(*) FROM evidence WHERE person_contact_id IS NOT NULL") == [
        (0,)
    ]


@pytest.mark.parametrize("merged", [False, True])
def test_an_unknown_or_merged_lead_is_skipped_by_both_steps(world: World, merged: bool) -> None:
    older = world.lead(web_domain="smith-law-llp.com")
    lead, run_id, _ = law_lead(world)
    target = "01KA0000000000000000000000"
    if merged:
        world.transact(lambda: world.discovery.merge(older, lead))
        target = lead

    assert world.extract(target, run_id, people=[]) is True

    assert world.sql("SELECT COUNT(*) FROM extraction_runs") == [(0,)]
    assert select_jobs(world) == []


# The route split and the log line ---------------------------------------------------------------


def test_each_step_counts_only_its_own_ok_runs(world: World) -> None:
    lead, run_id, pages = law_lead(world)
    refused = LlmRefused("cyber", model=HAIKU, usage=USAGE)
    # the Signals step refuses, the people step succeeds
    world.extract(lead, run_id, refused, people=[law_people(pages)])
    world.clock.advance(timedelta(hours=1))

    # nothing changed: the Signals step still has no ok run, so it sends every page again;
    # the people step has one, so it sends nothing
    world.extract(lead, run_id, ok(signals=[signal(pages[ABOUT], LAW_HIRING)]), people=[])

    [_, (_, blocks, _)] = world.calls
    assert len(blocks) == 4  # the catalogue and the three pages
    assert world.runs(lead)[1][0] == "ok"
    assert [run[0] for run in people_runs(world, lead)] == ["ok", "no_pages"]


def test_the_people_log_line_holds_ids_and_counts_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.people")
    lead, run_id, pages = law_lead(world)

    world.extract(lead, run_id, ok(), people=[law_people(pages)])

    [record] = [
        r
        for r in caplog.records
        if r.name == "nextron.app.research.people" and r.getMessage() == "people_extracted"
    ]
    [(run,)] = world.sql("SELECT id FROM extraction_runs WHERE route = 'extract_people'")
    extras = {k: v for k, v in record.__dict__.items() if k in PEOPLE_KEYS}
    assert extras == {
        "lead_id": lead,
        "run_id": run,
        "pages_sent": 2,
        "pages_truncated": 0,
        "people": 3,
        "contacts_created": 1,
        "not_decision_makers": 2,
        "stored": 2,
        "confirmed": 0,
        "superseded": 0,
        "retracted": 0,
        "discarded": 0,
        "lawyers": 2,
    }
    words = ("Jane", "Roe", "Partner", "Austin", "smith-law", "Alex", "Paralegal")
    for each in caplog.records:
        text = each.getMessage() + " ".join(str(v) for v in each.__dict__.values())
        assert not any(word in text for word in words), each.getMessage()


PEOPLE_KEYS = frozenset(
    {
        "lead_id",
        "run_id",
        "pages_sent",
        "pages_truncated",
        "people",
        "contacts_created",
        "not_decision_makers",
        "stored",
        "confirmed",
        "superseded",
        "retracted",
        "discarded",
        "lawyers",
    }
)


def test_no_people_and_no_decision_maker_still_records_an_ok_run(world: World) -> None:
    lead, run_id, _ = law_lead(world)

    world.extract(lead, run_id, ok(), people=[NO_PEOPLE])

    assert people_runs(world, lead) == [("ok", None, PEOPLE_ACTOR, 2, 0, 0, 0)]
    assert counts(world, lead) == []  # a zero would falsely disqualify the Lead
