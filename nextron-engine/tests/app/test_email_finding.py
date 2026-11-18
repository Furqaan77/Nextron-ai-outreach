"""Story 2.13's email search on a temporary database at head (FR-14, FR-51, FR-57, AD-7, AD-9,
AD-10, AD-16, INT-6, NFR-12): ``EmailFinding.find_contact_email`` on pages a real
``WebsiteCrawls`` stored from a ``ScriptedCrawler`` (Story 2.9's world,
``tests.app.test_signal_extraction``), with ``ScriptedDomainSearch`` and two ``ScriptedFinder``s
for Hunter and Prospeo, the shipped Segments and Markets, the real gate and a ``FrozenClock``.

Every row of the spec's matrix is here: a published address (in the text and as a ``mailto:``
link), an initial alone, a learned pattern, role addresses, Hunter Domain Search (and not again),
the finders, the quota, a suppressed address, a duplicate and an address already held. Also the
order of the steps, the paid steps run once, the Provenance history, the gate up front (the
Suppression List, a Lost or Disqualified Lead), the ``events``, Evidence and Provenance rows, the
``email_domains`` row, the Market resolved again, Hunter's ``claimed`` answer, Prospeo's unset
key, the enqueues of the selection and "Make primary", ``queue_search``, the skipped cases, the
handler's ``DEFERRED`` and no address, name or domain in a log line or ``events`` row. No real
Hunter or Prospeo request is made; the fakes meter nothing, so the units each 2xx meters are the
adapter tests' (``tests/unit/test_hunter_adapter.py``, ``test_prospeo_adapter.py``)."""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.research.finding import EmailFinding, SearchQueued
from nextron.app.research.people import PrimaryContacts
from nextron.app.setup.api import Setup
from nextron.domain.budget import ApiCall
from nextron.domain.contactability import AddressSubject, ContactSubject, SuppressionScope
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.email_finding import EmailSearchOutcome
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.email_finder import (
    DomainPerson,
    DomainSearchAnswer,
    FinderAnswer,
    FinderQuery,
)
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import CREDENTIAL_NOT_SET
from tests.app.test_signal_extraction import T0, TEAM, World, build_world, no_jobs, utc
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder

SITE = "smith-law.com"
JANE = "Jane Roe"
JANE_LINE = f"{JANE}, Managing Partner – jane.roe@{SITE}"
JANE_ADDRESS = f"jane.roe@{SITE}"
ANN_LINE = f"Ann Fox, Chief Operating Officer – ann.fox@{SITE}"
BO_LINE = f"Bo Day, Financial Controller – bo.day@{SITE}"
ACTOR = "system:research"
UNKNOWN = "01KA0000000000000000000000"
PII = ("jane", "Jane", "roe", "Roe", "Ann", "fox", "Fox", "smith-law", "Smith", JANE_ADDRESS)


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


@dataclass
class Finders:
    """The service on scripted ports."""

    service: EmailFinding
    domain_search: ScriptedDomainSearch
    hunter: ScriptedFinder
    prospeo: ScriptedFinder

    def calls(self) -> tuple[int, int, int]:
        return len(self.domain_search.calls), len(self.hunter.calls), len(self.prospeo.calls)


def finders(
    world: World,
    *,
    domain_search: tuple[Any, ...] = (),
    hunter: tuple[Any, ...] = (),
    prospeo: tuple[Any, ...] = (),
) -> Finders:
    searched = ScriptedDomainSearch(*domain_search)
    hunted = ScriptedFinder(*hunter)
    prospected = ScriptedFinder(*prospeo)
    service = EmailFinding(
        world.clock,
        world.meter,
        world.research,
        world.queue,
        domain_search=searched,
        hunter=hunted,
        prospeo=prospected,
    )
    return Finders(service, searched, hunted, prospected)


def find(world: World, found: Finders, lead: str, contact: str) -> EmailSearchOutcome | None:
    with world.units.open():
        return found.service.find_contact_email(lead, contact)


def site(world: World, *lines: str, extra: str = "") -> str:
    """A Lead whose team page holds ``lines`` (one paragraph each), crawled at ``T0``."""
    lead = world.lead()
    world.site("".join(f"<p>{line}</p>" for line in lines) + extra)
    world.crawl(lead)
    return lead


def add(world: World, lead: str, name: str, role: str = "Managing Partner", **kw: Any) -> str:
    world.clock.advance(timedelta(seconds=1))
    added = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name=name, role=role, **kw),
            source=ProvenanceSource.CSV,
            detail="list.csv",
            retrieved_at=T0,
        )
    )
    return added.contact_id


def contact(world: World, contact_id: str) -> dict[str, Any]:
    columns = (
        "email, address_class, email_search_tried, email_search_outcome, email_search_at, "
        "market_basis"
    )
    [row] = world.sql(f"SELECT {columns} FROM contacts WHERE id = ?", (contact_id,))
    return dict(zip([c.strip() for c in columns.split(",")], row, strict=True))


def address_provenance(world: World, contact_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT source_kind, source_detail, address, retrieved_at, address_class "
        "FROM provenance_records WHERE contact_id = ? AND subject = 'address' ORDER BY rowid",
        (contact_id,),
    )


def contact_events(world: World, contact_id: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'contacts' AND entity_id = ? ORDER BY rowid",
        (contact_id,),
    )


def email_domain(world: World) -> dict[str, Any] | None:
    columns = (
        "domain, pattern, pattern_source, pattern_evidence_ids, pattern_learned_at, "
        "domain_search_at"
    )
    rows = world.sql(f"SELECT {columns} FROM email_domains")
    if not rows:
        return None
    [row] = rows
    return dict(zip([c.strip() for c in columns.split(",")], row, strict=True))


def person_evidence(world: World, contact_id: str) -> list[dict[str, Any]]:
    [(lead,)] = world.sql("SELECT lead_id FROM contacts WHERE id = ?", (contact_id,))
    return [
        row
        for row in world.evidence(lead)
        if row["person_contact_id"] == contact_id and row["extracted_by"] == "rule"
    ]


def fetched(world: World, lead: str) -> str:
    [(at,)] = world.sql(
        "SELECT fetched_at FROM crawled_pages WHERE lead_id = ? AND url = ?", (lead, TEAM)
    )
    return str(at)


def jobs(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT idempotency_key, concurrency_key, payload, lane FROM jobs "
        "WHERE type = 'find_contact_email' ORDER BY rowid"
    )


def suppress(world: World, subject: Any) -> None:
    world.transact(
        lambda: Compliance(world.clock).suppress(
            subject, SuppressionScope.PERSON, SuppressionSource.OPERATOR, None
        )
    )


# Row: Published (text) and the order of the steps -----------------------------------------------


def test_a_published_address_on_the_name_line_is_assigned_with_its_quote_and_no_call(
    world: World,
) -> None:
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE)
    before = len(contact_events(world, jane))
    found = finders(
        world,
        domain_search=(DomainSearchAnswer("{f}{last}", ()),),
        hunter=(FinderAnswer(f"jroe@{SITE}"),),
        prospeo=(FinderAnswer(f"j.roe@{SITE}"),),
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (0, 0, 0)  # the free step found it: no port call
    page_at = fetched(world, lead)
    assert contact(world, jane) | {"market_basis": None} == {
        "email": JANE_ADDRESS,
        "address_class": "published",
        "email_search_tried": None,
        "email_search_outcome": "found",
        "email_search_at": utc(T0 + timedelta(seconds=1)),
        "market_basis": None,
    }
    assert address_provenance(world, jane) == [
        ("website", TEAM, JANE_ADDRESS, page_at, "published")
    ]
    assert contact_events(world, jane)[before:] == [
        (None, "published", "assigned_from_website", None, ACTOR)
    ]
    [quote] = person_evidence(world, jane)
    assert {
        key: quote[key]
        for key in ("type", "quote", "pages_checked", "source", "source_url", "retrieved_at")
    } == {
        "type": "quote",
        "quote": JANE_LINE,
        "pages_checked": json.dumps([TEAM], separators=(",", ":")),
        "source": "website",
        "source_url": TEAM,
        "retrieved_at": page_at,
    }
    assert email_domain(world) is None  # nothing learned, nothing searched


def test_a_re_run_after_the_address_went_invalid_takes_the_next_published_address(
    world: World,
) -> None:
    lead = site(world, JANE_LINE, f"Write to {JANE} at jroe@{SITE}")
    jane = add(world, lead, JANE)
    found = finders(world)
    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND
    world.sql("UPDATE contacts SET address_class = 'invalid' WHERE id = ?", (jane,))

    # the address is in her Provenance history now: the next published one is taken
    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert contact(world, jane)["email"] == f"jroe@{SITE}"
    assert [row[2] for row in address_provenance(world, jane)] == [JANE_ADDRESS, f"jroe@{SITE}"]
    assert found.calls() == (0, 0, 0)


# Row: Published (mailto) ------------------------------------------------------------------------


def test_a_mailto_link_is_published_and_its_quote_is_the_mailto(world: World) -> None:
    lead = site(
        world, f'{JANE}, Managing Partner <a href="mailto:{JANE_ADDRESS}?subject=Hi">Email</a>'
    )
    jane = add(world, lead, JANE)
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert (contact(world, jane)["email"], contact(world, jane)["address_class"]) == (
        JANE_ADDRESS,
        "published",
    )
    [quote] = person_evidence(world, jane)
    assert quote["quote"] == f"mailto:{JANE_ADDRESS}"
    assert found.calls() == (0, 0, 0)


# Row: Initial only, the paid steps in order and once per Contact --------------------------------


def test_an_initial_alone_is_not_published_and_the_paid_steps_run_in_order_once(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = site(world, f"{JANE}, Managing Partner", f"Write to jroe@{SITE}")
    jane = add(world, lead, JANE)
    found = finders(world)
    caplog.set_level(logging.INFO, logger="nextron.app.research.finding")

    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND

    assert found.domain_search.calls == [SITE]
    assert found.hunter.calls == [FinderQuery(SITE, "jane", "roe")]
    assert found.prospeo.calls == [FinderQuery(SITE, "jane", "roe")]
    assert contact(world, jane) | {"market_basis": None} == {
        "email": None,
        "address_class": None,
        "email_search_tried": "hunter,prospeo",
        "email_search_outcome": "not_found",
        "email_search_at": utc(world.clock.now()),
        "market_basis": None,
    }
    row = email_domain(world)
    assert row is not None
    assert (row["domain"], row["pattern"], row["domain_search_at"]) == (
        SITE,
        None,
        utc(world.clock.now()),
    )
    [record] = [r for r in caplog.records if r.getMessage() == "email_search_finished"]
    assert (getattr(record, "outcome"), getattr(record, "step"), getattr(record, "calls")) == (
        "not_found",
        "prospeo",
        3,
    )

    # "Find email" again: the free steps run, no paid step runs a second time
    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND
    assert found.calls() == (1, 1, 1)
    # nor for another Contact at the same domain: the Domain Search ran once for it
    ann = add(world, lead, "Ann Fox", "COO")
    assert find(world, found, lead, ann) is EmailSearchOutcome.NOT_FOUND
    assert found.calls() == (1, 2, 2)


# Row: Pattern learned ---------------------------------------------------------------------------


def test_two_contacts_matching_one_address_are_one_example(world: World) -> None:
    lead = site(world, f"Ann Fox and Ann Lee, Partners – ann@{SITE}", f"{JANE}, Managing Partner")
    add(world, lead, "Ann Fox", "Owner")
    add(world, lead, "Ann Lee", "Owner")
    jane = add(world, lead, JANE)
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND

    learned = email_domain(world)
    assert learned is not None and learned["pattern"] is None  # "first" not learned from 1
    assert contact(world, jane)["email"] is None


def test_a_pattern_learned_from_two_other_contacts_gives_a_guessed_address(world: World) -> None:
    lead = site(world, ANN_LINE, BO_LINE, f"{JANE}, Managing Partner")
    ann = add(world, lead, "Ann Fox", "Chief Operating Officer")
    bo = add(world, lead, "Bo Day", "Financial Controller")
    jane = add(world, lead, JANE)
    found = finders(world, domain_search=(DomainSearchAnswer("{f}{last}", ()),))

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (0, 0, 0)
    assert (contact(world, jane)["email"], contact(world, jane)["address_class"]) == (
        JANE_ADDRESS,
        "guessed",
    )
    assert address_provenance(world, jane) == [
        (
            "pattern",
            "first.last pattern learned from published addresses",
            JANE_ADDRESS,
            utc(world.clock.now()),
            "guessed",
        )
    ]
    assert contact_events(world, jane)[-1] == (
        None,
        "guessed",
        "assigned_from_pattern",
        None,
        ACTOR,
    )
    row = email_domain(world)
    assert row is not None
    [ann_quote] = person_evidence(world, ann)
    [bo_quote] = person_evidence(world, bo)
    assert (ann_quote["quote"], bo_quote["quote"]) == (ANN_LINE, BO_LINE)
    assert row | {"pattern_evidence_ids": json.loads(row["pattern_evidence_ids"])} == {
        "domain": SITE,
        "pattern": "first.last",
        "pattern_source": "published",
        "pattern_evidence_ids": [ann_quote["id"], bo_quote["id"]],
        "pattern_learned_at": utc(world.clock.now()),
        "domain_search_at": None,
    }
    # the examples' Contacts aren't changed, and Jane's guess has no Quote Evidence
    assert (contact(world, ann)["email"], contact(world, bo)["email"]) == (None, None)
    assert person_evidence(world, jane) == []


def test_a_suppressed_example_isnt_counted_so_one_example_learns_nothing(world: World) -> None:
    lead = site(world, ANN_LINE, BO_LINE, f"{JANE}, Managing Partner")
    add(world, lead, "Ann Fox", "Chief Operating Officer")
    add(world, lead, "Bo Day", "Financial Controller")
    jane = add(world, lead, JANE)
    suppress(world, AddressSubject(f"bo.day@{SITE}"))
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND

    row = email_domain(world)
    assert row is not None and row["pattern"] is None
    assert found.calls() == (1, 1, 1)


def test_an_examples_existing_quote_is_confirmed_not_duplicated(world: World) -> None:
    lead = site(world, ANN_LINE, BO_LINE, f"{JANE}, Managing Partner")
    ann = add(world, lead, "Ann Fox", "Chief Operating Officer")
    add(world, lead, "Bo Day", "Financial Controller")
    jane = add(world, lead, JANE)
    found = finders(world)
    assert find(world, found, lead, ann) is EmailSearchOutcome.FOUND  # her own, published
    [ann_quote] = person_evidence(world, ann)
    world.clock.advance(timedelta(hours=1))

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert [row["id"] for row in person_evidence(world, ann)] == [ann_quote["id"]]
    row = email_domain(world)
    assert row is not None and json.loads(row["pattern_evidence_ids"])[0] == ann_quote["id"]
    assert contact(world, jane)["email"] == JANE_ADDRESS


# Row: Role --------------------------------------------------------------------------------------


def test_a_role_address_is_never_assigned_or_learned(world: World) -> None:
    lead = site(
        world,
        f"{JANE}, Managing Partner – info@{SITE}",
        f"Ann Fox, COO – info@{SITE}",
        f"Bo Day, Controller – sales@{SITE}",
    )
    add(world, lead, "Ann Fox", "COO")
    add(world, lead, "Bo Day", "Controller")
    jane = add(world, lead, JANE)
    found = finders(
        world,
        domain_search=(
            DomainSearchAnswer(None, (DomainPerson(f"info@{SITE}", "Jane", "Roe", True),)),
        ),
        hunter=(FinderAnswer(f"contact@{SITE}"),),
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND

    assert contact(world, jane)["email"] is None
    row = email_domain(world)
    assert row is not None and row["pattern"] is None
    assert found.calls() == (1, 1, 1)  # Hunter's role address was dropped: Prospeo was asked
    assert contact(world, jane)["email_search_tried"] == "hunter,prospeo"


# Rows: Domain Search, and Domain Search again ---------------------------------------------------


def test_a_domain_search_stores_its_pattern_and_the_next_contact_uses_it(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    found = finders(
        world,
        domain_search=(DomainSearchAnswer("{f}{last}", ()), DomainSearchAnswer("{first}", ())),
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (1, 0, 0)
    assert (contact(world, jane)["email"], contact(world, jane)["address_class"]) == (
        f"jroe@{SITE}",
        "guessed",
    )
    assert contact(world, jane)["email_search_tried"] is None
    assert address_provenance(world, jane) == [
        ("finder", "Hunter Domain Search", f"jroe@{SITE}", utc(world.clock.now()), "guessed")
    ]
    assert contact_events(world, jane)[-1] == (
        None,
        "guessed",
        "assigned_from_finder",
        None,
        ACTOR,
    )
    stamp = utc(world.clock.now())
    assert email_domain(world) == {
        "domain": SITE,
        "pattern": "flast",
        "pattern_source": "hunter_domain_search",
        "pattern_evidence_ids": None,
        "pattern_learned_at": stamp,
        "domain_search_at": stamp,
    }

    # another Contact at the same domain: no second Domain Search, the stored pattern
    ann = add(world, lead, "Ann Fox", "COO")
    assert find(world, found, lead, ann) is EmailSearchOutcome.FOUND
    assert found.calls() == (1, 0, 0)
    assert address_provenance(world, ann) == [
        (
            "pattern",
            "flast pattern from Hunter Domain Search",
            f"afox@{SITE}",
            utc(world.clock.now()),
            "guessed",
        )
    ]
    assert email_domain(world) == {  # never overwritten
        "domain": SITE,
        "pattern": "flast",
        "pattern_source": "hunter_domain_search",
        "pattern_evidence_ids": None,
        "pattern_learned_at": stamp,
        "domain_search_at": stamp,
    }


def test_a_domain_searchs_personal_person_named_as_the_contact_comes_first(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    found = finders(
        world,
        domain_search=(
            DomainSearchAnswer(
                "{first}.{last}",
                (
                    DomainPerson(f"roe.j@{SITE}", "Jane", "Roe", False),  # not personal
                    DomainPerson(f"ann@{SITE}", "Ann", "Fox", True),  # someone else
                    DomainPerson(f"jroe1@{SITE}", "Jane", "Roe", True),
                ),
            ),
        ),
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert contact(world, jane)["email"] == f"jroe1@{SITE}"
    # nothing else from the answer is stored
    assert world.sql("SELECT COUNT(*) FROM contacts WHERE name = 'Ann Fox'") == [(0,)]
    stored = repr(world.sql("SELECT * FROM email_domains"))
    stored += repr(world.sql("SELECT * FROM evidence"))
    assert "ann@" not in stored and "roe.j@" not in stored


# Row: Finders, and the Market resolved again ----------------------------------------------------


def test_hunter_finds_nothing_then_prospeos_address_is_guessed_and_the_market_resolved(
    world: World,
) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    assert contact(world, jane)["market_basis"] == "lead_country"
    found = finders(world, prospeo=(FinderAnswer("jane.roe@smith-law.co.uk"),))

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (1, 1, 1)
    row = contact(world, jane)
    assert (row["email"], row["address_class"], row["email_search_tried"]) == (
        "jane.roe@smith-law.co.uk",
        "guessed",
        "hunter,prospeo",
    )
    assert row["market_basis"] == "conflict_stricter"  # the .uk address: the Market again
    assert address_provenance(world, jane)[0][:2] == ("finder", "Prospeo")


def test_hunters_address_is_taken_and_prospeo_is_never_asked(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    found = finders(world, hunter=(FinderAnswer(f"jr@{SITE}"),))

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (1, 1, 0)
    assert contact(world, jane)["email_search_tried"] == "hunter"
    assert address_provenance(world, jane)[0][:2] == ("finder", "Hunter Email Finder")


def test_hunters_claimed_answer_ends_the_search_without_prospeo(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    found = finders(world, hunter=(FinderAnswer(None, claimed=True),))

    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND

    assert found.calls() == (1, 1, 0)
    row = contact(world, jane)
    assert (row["email"], row["email_search_outcome"]) == (None, "not_found")
    assert row["email_search_tried"] is None  # a claim isn't a Hunter answer to move past
    # a re-run asks Hunter again (a claim is free), and still never reaches Prospeo
    again = finders(world, hunter=(FinderAnswer(None, claimed=True),))
    assert find(world, again, lead, jane) is EmailSearchOutcome.NOT_FOUND
    assert again.calls() == (0, 1, 0)
    assert contact(world, jane)["email_search_tried"] is None


def test_prospeos_unset_key_skips_it_without_recording_it(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    unset = AdapterError(False, CREDENTIAL_NOT_SET, "credential prospeo is not set")
    found = finders(world, prospeo=(unset,))

    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND

    assert contact(world, jane)["email_search_tried"] == "hunter"
    # any other error propagates (the runner retries a retryable one)
    found = finders(world, prospeo=(AdapterError(True, "prospeo_unavailable", "x"),))
    with pytest.raises(AdapterError):
        find(world, found, lead, jane)


def test_no_paid_call_without_a_last_name_or_a_web_domain(world: World) -> None:
    lead = site(world, "Cher, Managing Partner")
    cher = add(world, lead, "Cher")
    found = finders(world)

    assert find(world, found, lead, cher) is EmailSearchOutcome.NOT_FOUND
    assert found.calls() == (0, 0, 0)

    world.sql(
        "UPDATE leads SET web_domain = NULL, place_id = 'ChIJN1t_tDeuEmsRUsoyG83frY4' "
        "WHERE id = ?",
        (lead,),
    )
    jane = add(world, lead, JANE)
    assert find(world, found, lead, jane) is EmailSearchOutcome.NOT_FOUND
    assert found.calls() == (0, 0, 0)


# Row: Quota -------------------------------------------------------------------------------------


def test_hunters_quota_reached_makes_no_call_and_the_handler_defers(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    world.meter.record(ApiCall("hunter", "find", ok=True, count=50))  # 100 units: 50 credits
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.QUOTA_REACHED

    assert found.calls() == (0, 0, 0)  # and never on to Prospeo
    row = contact(world, jane)
    assert (row["email"], row["email_search_outcome"], row["email_search_tried"]) == (
        None,
        "quota_reached",
        None,
    )

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
        finding=lambda: found.service,
    ).get("find_contact_email").handler
    job = Job(
        "01JD0000000000000000000099",
        "find_contact_email",
        Lane.BULK,
        {"v": 1, "lead_id": lead, "contact_id": jane},
        1,
    )
    with world.units.open():
        assert handler(job) is DEFERRED
    assert found.calls() == (0, 0, 0)

    # with the quota back, the handler runs the search to its end
    world.meter.records.clear()
    with world.units.open():
        assert handler(job) is None
    assert found.calls() == (1, 1, 1)


def test_prospeos_quota_of_zero_stops_after_hunter(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    with world.units.open():
        Setup(world.clock).set("quota_prospeo_credits", 0)
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.QUOTA_REACHED

    assert found.calls() == (1, 1, 0)
    row = contact(world, jane)
    assert (row["email_search_outcome"], row["email_search_tried"]) == ("quota_reached", "hunter")


# Row: Suppressed, and the gate up front ---------------------------------------------------------


def test_a_suppressed_found_address_ends_the_search(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    suppress(world, AddressSubject(f"jr@{SITE}"))
    before = len(contact_events(world, jane))
    found = finders(
        world, hunter=(FinderAnswer(f"jr@{SITE}"),), prospeo=(FinderAnswer(f"j@{SITE}"),)
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.SUPPRESSED

    assert found.calls() == (1, 1, 0)  # no further call
    row = contact(world, jane)
    assert (row["email"], row["email_search_outcome"], row["email_search_tried"]) == (
        None,
        "suppressed",
        "hunter",
    )
    assert contact_events(world, jane)[before:] == [
        (None, "email_search_blocked", "address_suppressed", None, ACTOR)
    ]
    assert address_provenance(world, jane) == []


def test_a_suppressed_contact_is_never_searched(world: World) -> None:
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE)
    suppress(world, ContactSubject(jane))
    before = len(contact_events(world, jane))
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.SUPPRESSED

    assert found.calls() == (0, 0, 0)
    assert contact(world, jane)["email"] is None  # not even the published address
    assert contact_events(world, jane)[before:] == [
        (None, "email_search_blocked", "person_suppressed", None, ACTOR)
    ]


@pytest.mark.parametrize(
    ("stage", "reason"), [("lost", "lead_lost"), ("disqualified", "lead_disqualified")]
)
def test_a_lost_or_disqualified_lead_closes_the_search(
    world: World, stage: str, reason: str
) -> None:
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE)
    world.sql("UPDATE leads SET pipeline_stage = ? WHERE id = ?", (stage, lead))
    found = finders(world)

    assert find(world, found, lead, jane) is EmailSearchOutcome.LEAD_CLOSED

    assert found.calls() == (0, 0, 0)
    row = contact(world, jane)
    assert (row["email"], row["email_search_outcome"]) == (None, "lead_closed")
    assert contact_events(world, jane)[-1] == (None, "email_search_blocked", reason, None, ACTOR)


# Row: Duplicate, and the Provenance history -----------------------------------------------------


def test_an_address_another_contact_holds_is_skipped_for_the_next_source(world: World) -> None:
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE)
    ann = add(world, lead, "Ann Fox", "COO")
    world.sql(
        "UPDATE contacts SET email = ?, address_class = 'guessed' WHERE id = ?",
        (JANE_ADDRESS, ann),
    )
    found = finders(
        world, hunter=(FinderAnswer(JANE_ADDRESS),), prospeo=(FinderAnswer(f"jr@{SITE}"),)
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (1, 1, 1)
    assert contact(world, jane)["email"] == f"jr@{SITE}"
    assert address_provenance(world, jane)[0][:2] == ("finder", "Prospeo")


def test_an_address_in_the_contacts_provenance_history_is_never_assigned_again(
    world: World,
) -> None:
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE, email=JANE_ADDRESS)
    world.sql("UPDATE contacts SET address_class = 'invalid' WHERE id = ?", (jane,))
    found = finders(
        world, hunter=(FinderAnswer(JANE_ADDRESS),), prospeo=(FinderAnswer(f"jr@{SITE}"),)
    )

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND

    assert found.calls() == (1, 1, 1)  # the published one and Hunter's were her old address
    assert contact(world, jane)["email"] == f"jr@{SITE}"
    assert [row[2] for row in address_provenance(world, jane)] == [JANE_ADDRESS, f"jr@{SITE}"]


# Row: Has address, and the skipped cases --------------------------------------------------------


def skipped(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        getattr(r, "reason") for r in caplog.records if r.getMessage() == "email_search_skipped"
    ]


def test_a_contact_with_a_usable_address_is_skipped_with_no_call(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.finding")
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE, email=f"jr@{SITE}")  # a CSV guessed address
    assert contact(world, jane)["address_class"] == "guessed"
    found = finders(world)

    assert find(world, found, lead, jane) is None

    assert found.calls() == (0, 0, 0)
    assert contact(world, jane)["email"] == f"jr@{SITE}"
    assert contact(world, jane)["email_search_outcome"] is None
    assert skipped(caplog) == ["has_address"]


def test_an_unknown_or_merged_lead_or_a_contact_not_on_it_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.finding")
    other = world.lead(web_domain="other-firm.com")  # older: the merge's survivor
    lead = site(world, JANE_LINE)
    jane = add(world, lead, JANE)
    found = finders(world)

    assert find(world, found, UNKNOWN, jane) is None
    assert find(world, found, other, jane) is None
    assert find(world, found, lead, UNKNOWN) is None
    assert find(world, found, lead, "not-a-ulid") is None
    world.transact(lambda: world.discovery.merge(other, lead))
    assert find(world, found, lead, jane) is None

    assert skipped(caplog) == [
        "unknown_lead",
        "not_on_lead",
        "not_on_lead",
        "not_on_lead",
        "merged_lead",
    ]
    assert found.calls() == (0, 0, 0)


# The enqueues -----------------------------------------------------------------------------------


def test_the_selection_and_make_primary_enqueue_the_search(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    pat = add(world, lead, "Pat Moe", "Paralegal")
    selection = PrimaryContacts(world.clock, world.research, world.queue)

    with world.units.open():
        choice = selection.select_primary_contact(lead)
    assert choice is not None and choice.contact_id == jane

    payload = {"v": 1, "lead_id": lead, "contact_id": jane}
    [(key, concurrency, stored, lane)] = jobs(world)
    assert (key, concurrency, json.loads(stored), lane) == (
        f"find_contact_email:{jane}:tick",
        f"domain:{SITE}",
        payload,
        "bulk",
    )

    with world.units.open():
        selection.make_primary(lead, pat)
    [_, (key, concurrency, stored, lane)] = jobs(world)
    assert (key, concurrency, json.loads(stored), lane) == (
        f"find_contact_email:{pat}:tick",
        f"domain:{SITE}",
        {"v": 1, "lead_id": lead, "contact_id": pat},
        "interactive",
    )


def test_a_primary_with_a_usable_address_enqueues_nothing(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE, email=f"jr@{SITE}")
    selection = PrimaryContacts(world.clock, world.research, world.queue)

    with world.units.open():
        selection.select_primary_contact(lead)
    with world.units.open():
        selection.make_primary(lead, jane)

    assert jobs(world) == []


def test_queue_search_has_five_results(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    held = add(world, lead, "Ann Fox", "COO", email=f"af@{SITE}")
    other = world.lead(web_domain="other-firm.com")
    merged = world.lead(web_domain="merged-firm.com")
    world.transact(lambda: world.discovery.merge(other, merged))
    found = finders(world)

    def queue(lead_id: str, contact_id: str) -> SearchQueued:
        with world.units.open():
            return found.service.queue_search(lead_id, contact_id)

    assert queue(UNKNOWN, jane) is SearchQueued.UNKNOWN_LEAD
    assert queue("nope", jane) is SearchQueued.UNKNOWN_LEAD
    assert queue(merged, jane) is SearchQueued.MERGED_LEAD
    assert queue(other, jane) is SearchQueued.NOT_ON_LEAD
    assert queue(lead, UNKNOWN) is SearchQueued.NOT_ON_LEAD
    assert queue(lead, held) is SearchQueued.HAS_ADDRESS
    assert jobs(world) == []

    assert queue(lead, jane) is SearchQueued.QUEUED
    [(key, concurrency, stored, lane)] = jobs(world)
    assert (key, concurrency, json.loads(stored), lane) == (
        f"find_contact_email:{jane}:tick",
        f"domain:{SITE}",
        {"v": 1, "lead_id": lead, "contact_id": jane},
        "interactive",
    )
    assert queue(lead, jane) is SearchQueued.QUEUED  # the live job absorbs it
    assert len(jobs(world)) == 1
    assert found.calls() == (0, 0, 0)


# No address, name or domain in a log line or an events row -------------------------------------


def test_the_log_lines_and_events_hold_no_address_name_or_domain(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    lead = site(world, ANN_LINE, BO_LINE, f"{JANE}, Managing Partner")
    add(world, lead, "Ann Fox", "Chief Operating Officer")
    add(world, lead, "Bo Day", "Financial Controller")
    jane = add(world, lead, JANE)
    pat = add(world, lead, "Pat Moe", "Paralegal")
    suppress(world, AddressSubject(f"pat.moe@{SITE}"))
    found = finders(world)
    [(event_count,)] = world.sql("SELECT COUNT(*) FROM events")
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    assert find(world, found, lead, jane) is EmailSearchOutcome.FOUND
    assert find(world, found, lead, pat) is EmailSearchOutcome.SUPPRESSED
    assert find(world, found, lead, jane) is None

    assert caplog.records
    standard = set(vars(logging.makeLogRecord({})))
    for record in caplog.records:
        extras = {k: v for k, v in vars(record).items() if k not in standard}
        text = record.getMessage() + repr(extras)
        for word in PII + ("Pat", "Moe", "pat.moe"):
            assert word not in text, (record.getMessage(), word)
    finished = [r for r in caplog.records if r.getMessage() == "email_search_finished"]
    assert [
        (getattr(r, "outcome"), getattr(r, "step"), getattr(r, "calls")) for r in finished
    ] == [("found", "pattern", 0), ("suppressed", "pattern", 0)]
    rows = world.sql("SELECT * FROM events LIMIT -1 OFFSET ?", (event_count,))
    assert rows
    text = repr(rows)
    for word in PII + ("Pat", "Moe", "pat.moe"):
        assert word not in text


def test_a_paid_answer_is_kept_when_the_contact_gets_an_address_meanwhile(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead, JANE)
    found = finders(world, hunter=(FinderAnswer(f"jane@{SITE}"),))
    real_search, real_find = found.domain_search.domain_search, found.hunter.find_email

    def search_then_fill(domain: str) -> DomainSearchAnswer:
        answer = real_search(domain)
        world.sql(
            "UPDATE contacts SET email = ?, address_class = 'guessed' WHERE id = ?",
            (f"other@{SITE}", jane),
        )
        return answer

    found.domain_search.domain_search = search_then_fill  # type: ignore[method-assign]

    assert find(world, found, lead, jane) is None  # skipped: it has an address now

    learned = email_domain(world)
    assert learned is not None and learned["domain_search_at"] is not None  # paid, recorded
    # Hunter's answer is recorded the same way when the address arrives during its call
    world.sql("UPDATE contacts SET email = NULL, address_class = NULL WHERE id = ?", (jane,))

    def find_then_fill(query: Any) -> FinderAnswer:
        answer = real_find(query)
        world.sql(
            "UPDATE contacts SET email = ?, address_class = 'guessed' WHERE id = ?",
            (f"other@{SITE}", jane),
        )
        return answer

    found.hunter.find_email = find_then_fill  # type: ignore[method-assign]

    assert find(world, found, lead, jane) is None
    assert contact(world, jane)["email_search_tried"] == "hunter"
    assert contact(world, jane)["email"] == f"other@{SITE}"
