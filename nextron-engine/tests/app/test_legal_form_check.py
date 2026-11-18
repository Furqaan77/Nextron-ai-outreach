"""Story 2.12's ``check_legal_form`` on a temporary database at head (FR-12, FR-9, NFR-3, AD-3,
AD-7, AD-9): ``LegalFormChecks.check_legal_form`` through ``ScriptedRegistry`` on pages a real
``WebsiteCrawls`` stored from a ``ScriptedCrawler``, with the shipped Segments and Markets, the
real job types and a ``FrozenClock``. The world is Story 2.9's
(``tests.app.test_signal_extraction``).

Every row of the spec's matrix is here: the number path (a footer, a short number, an unrelated
number), the name path (a match, no postcode, ambiguity), a record that isn't active or isn't a
corporate subscriber, the FR-9 merge, a conflicting number, a non-GB Lead, retryable and
non-retryable errors, the budget, re-runs (the same quotes, a changed record, a lost link) and
Eligibility. Also the Evidence rows, the logs (IDs and codes only), the queued jobs, the job
handler and the chain. No request reaches Companies House."""

import contextvars
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.legal_form import LegalFormChecks
from nextron.app.queries import discovery as discovery_queries
from nextron.app.research.people import PrimaryContacts
from nextron.domain.budget import AiCall
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.eligibility import ReasonKind
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.legal_form import LegalFormFinding, LegalFormStatus, SiteQuote
from nextron.domain.notifications import EvidenceChanged
from nextron.ports.errors import AdapterError
from nextron.ports.registry import RegistryCompany
from tests.app.test_signal_extraction import ABOUT, SITE, T0, World, build_world, no_jobs, utc
from tests.fakes.registry import ScriptedRegistry

LOGGER = "nextron.app.discovery.legal_form"
NUMBER = "01234567"
OTHER = "07654321"
HELD = "09999999"
CH_PAGE = "https://find-and-update.company-information.service.gov.uk/company/"
ACME = "Acme Accounting"
FOOTER = (
    "Acme Accounting Services Limited is registered in England and Wales, company number "
    "01234567"
)
FOOTER_QUOTE = "registered in England and Wales, company number 01234567"
OFFICE = "Our office: 1 High Street, London EC1A 1BB"
ACME_LTD = RegistryCompany(NUMBER, "ACME ACCOUNTING SERVICES LIMITED", "ltd", "active", "EC1A 1BB")
ACME_QUOTE = (
    "Companies House record 01234567: company type ltd, company status active, registered office "
    "postcode EC1A 1BB, registered name ACME ACCOUNTING SERVICES LIMITED"
)
SMITH = "Smith & Jones LLP"
SMITH_LLP = RegistryCompany(NUMBER, "SMITH AND JONES LLP", "llp", "active", "EC1A 1BB")
# Text that must never reach a log line: names, quotes, URLs and postcodes.
PLAIN = ("Acme", "ACME", "Smith", "SMITH", "EC1A", "https:", "registered in", "company number")


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def gb_lead(
    world: World,
    name: str = ACME,
    *,
    web_domain: str = SITE,
    country: str | None = "GB",
    ch_number: str | None = None,
) -> str:
    identity = LeadIdentity(
        name=name,
        segment_id=world.segment,
        web_domain=web_domain,
        ch_number=ch_number,
        country_code=country,
    )
    upserted = world.transact(lambda: world.discovery.upsert_lead(identity, LeadSource.PLACES))
    assert upserted.lead_id is not None
    return upserted.lead_id


def crawled(world: World, about: str, name: str = ACME, **lead: Any) -> str:
    """A Lead whose site was crawled at ``T0``, its about page holding ``about``."""
    lead_id = gb_lead(world, name, **lead)
    world.site(team="<p>Our team.</p>", about=f"<p>{about}</p>")
    world.crawl(lead_id)
    return lead_id


def checks(world: World, registry: ScriptedRegistry) -> LegalFormChecks:
    return LegalFormChecks(
        world.clock, world.meter, registry, world.discovery, world.research, world.queue
    )


def check(world: World, registry: ScriptedRegistry, lead_id: str) -> bool:
    with world.units.open():
        return checks(world, registry).check_legal_form(lead_id)


def legal(world: World, lead_id: str) -> tuple[Any, ...]:
    [row] = world.sql(
        "SELECT ch_number, legal_form, company_status, legal_form_status, "
        "legal_form_site_evidence_id, legal_form_registry_evidence_id FROM leads WHERE id = ?",
        (lead_id,),
    )
    return tuple(row)


def selections(world: World) -> list[tuple[Any, ...]]:
    return world.jobs("select_primary_contact")


def done_selections(world: World) -> None:
    world.sql("UPDATE jobs SET status = 'done' WHERE type = 'select_primary_contact'")


def checked_logs(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage() == message]


def fields(record: logging.LogRecord, *names: str) -> tuple[Any, ...]:
    """The record's ``extra`` fields named."""
    return tuple(getattr(record, name) for name in names)


def check_job(lead_id: str) -> Job:
    payload = {"v": 1, "lead_id": lead_id}
    return Job("01JD0000000000000000000099", "check_legal_form", Lane.BULK, payload, 1)


def handler_on(world: World, registry: ScriptedRegistry) -> Callable[[Job], object]:
    """The real ``check_legal_form`` handler, on ``checks(world, registry)``."""
    return main.build_job_types(
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
        job_boards=no_jobs,
        legal_forms=lambda: checks(world, registry),
    ).get("check_legal_form").handler


def assert_logs_clean(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        if record.name != LOGGER:
            continue
        text = json.dumps({key: str(value) for key, value in vars(record).items()})
        for plain in PLAIN:
            assert plain not in text, plain


def fetched(world: World, url: str = ABOUT) -> str:
    [(at,)] = world.sql("SELECT DISTINCT fetched_at FROM crawled_pages WHERE url = ?", (url,))
    return str(at)


def evidence_by_source(world: World, lead_id: str) -> dict[str, dict[str, Any]]:
    return {row["source"]: row for row in world.evidence(lead_id) if row["retracted_at"] is None}


def gb_contact(world: World, lead_id: str) -> str:
    added = world.transact(
        lambda: world.research.add_contact(
            lead_id,
            ContactCandidate(name="Jane Roe", email="jane.roe@acme.co.uk"),
            source=ProvenanceSource.WEBSITE,
            detail="https://smith-law.com/team",
            retrieved_at=T0,
        )
    )
    assert added.contact_id is not None
    return added.contact_id


def legal_form_reasons(world: World, contact_id: str) -> set[ReasonKind]:
    with world.units.open(read_only=True):
        decision = Compliance(world.clock).evaluate_eligibility(contact_id)
    return {kind for kind in decision.kinds if kind.startswith("uk_legal_form")}


# Row: Number ------------------------------------------------------------------------------------


def test_a_footer_number_links_and_confirms_the_lead(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    lead = crawled(world, FOOTER)
    registry = ScriptedRegistry(companies={NUMBER: ACME_LTD})
    world.clock.advance(timedelta(minutes=5))

    assert check(world, registry, lead) is True

    assert registry.operations() == ["get_company"]
    ch_number, legal_form, status, outcome, site_id, registry_id = legal(world, lead)
    assert (ch_number, legal_form, status, outcome) == (NUMBER, "ltd", "active", "confirmed")
    rows = {row["id"]: row for row in world.evidence(lead)}
    site, record = rows[site_id], rows[registry_id]
    assert len(rows) == 2
    assert site == {
        "id": site_id,
        "type": "quote",
        "quote": FOOTER_QUOTE,
        "sought": None,
        "number": None,
        "fact": None,
        "value": None,
        "pages_checked": json.dumps([ABOUT], separators=(",", ":")),
        "source": "website",
        "source_url": ABOUT,
        "retrieved_at": fetched(world),
        "extracted_by": "rule",
        "person_contact_id": None,
        "superseded_by": None,
        "retracted_at": None,
        "last_confirmed_at": fetched(world),
        "created_at": utc(T0 + timedelta(minutes=5)),
    }
    assert {key: record[key] for key in ("type", "quote", "source", "source_url")} == {
        "type": "quote",
        "quote": ACME_QUOTE,
        "source": "registry",
        "source_url": CH_PAGE + NUMBER,
    }
    assert record["pages_checked"] == json.dumps([CH_PAGE + NUMBER], separators=(",", ":"))
    assert record["retrieved_at"] == utc(T0 + timedelta(minutes=5))
    assert (record["extracted_by"], record["fact"], record["person_contact_id"]) == (
        "rule",
        None,
        None,
    )
    [(key, concurrency, payload, lane)] = selections(world)
    assert (key, concurrency, json.loads(payload), lane) == (
        f"select_primary_contact:{lead}:tick",
        f"domain:{SITE}",
        {"v": 1, "lead_id": lead},
        "bulk",
    )
    [logged] = checked_logs(caplog, "legal_form_checked")
    assert fields(logged, "lead_id", "status", "path", "error_code") == (
        lead,
        "confirmed",
        "number",
        None,
    )
    assert_logs_clean(caplog)


def test_a_short_number_is_looked_up_zero_padded(world: World) -> None:
    lead = crawled(world, "Acme Accounting Ltd. Company No. 1234567")
    registry = ScriptedRegistry(
        companies={NUMBER: RegistryCompany(NUMBER, "ACME ACCOUNTING LTD", "ltd", "active", None)}
    )

    check(world, registry, lead)

    assert [call.key for call in registry.calls] == [NUMBER]
    assert legal(world, lead)[:4] == (NUMBER, "ltd", "active", "confirmed")
    assert evidence_by_source(world, lead)["website"]["quote"] == "Company No. 1234567"


def test_an_unrelated_number_links_nothing_and_the_name_path_runs(world: World) -> None:
    lead = crawled(world, f"Registered number 07654321. {OFFICE}")
    other = RegistryCompany(OTHER, "OTHER CO LTD", "ltd", "active", "EC1A 1BB")
    registry = ScriptedRegistry(companies={OTHER: other}, searches={ACME: ()})

    check(world, registry, lead)

    assert registry.operations() == ["get_company", "search_companies"]
    assert legal(world, lead) == (None, None, None, "unconfirmed", None, None)
    assert world.evidence(lead) == []
    assert len(selections(world)) == 1


def test_at_most_three_numbers_are_looked_up_and_two_links_are_ambiguous(world: World) -> None:
    lead = crawled(
        world,
        "Company number 01111111; company number 02222222; company number 03333333; "
        "company number 04444444. No postcode here",
    )
    linked = {
        number: RegistryCompany(number, f"ACME ACCOUNTING {index} LTD", "ltd", "active", None)
        for index, number in enumerate(("01111111", "02222222", "03333333"), 1)
    }
    linked["02222222"] = RegistryCompany("02222222", "ACME ACCOUNTING", "ltd", "active", None)
    linked["03333333"] = RegistryCompany("03333333", "ACME ACCOUNTING LTD", "plc", "active", None)
    registry = ScriptedRegistry(companies=linked)

    check(world, registry, lead)

    assert [call.key for call in registry.calls] == ["01111111", "02222222", "03333333"]
    assert legal(world, lead) == (None, None, None, "unconfirmed", None, None)


def test_a_record_with_another_number_than_the_one_quoted_links_nothing(world: World) -> None:
    lead = crawled(world, "Acme Accounting Ltd, company number 01234567")
    moved = RegistryCompany(OTHER, "ACME ACCOUNTING LTD", "ltd", "active", None)

    check(world, ScriptedRegistry(companies={NUMBER: moved}), lead)

    assert legal(world, lead)[:4] == (None, None, None, "unconfirmed")


# Row: Name path ---------------------------------------------------------------------------------


def test_the_name_and_a_site_postcode_link_the_lead(world: World) -> None:
    lead = crawled(world, f"Smith & Jones, solicitors. {OFFICE}", SMITH)
    registry = ScriptedRegistry(companies={NUMBER: SMITH_LLP}, searches={SMITH: (SMITH_LLP,)})

    check(world, registry, lead)

    assert registry.operations() == ["search_companies", "get_company"]
    assert registry.calls[0].key == SMITH
    assert legal(world, lead)[:4] == (NUMBER, "llp", "active", "confirmed")
    rows = evidence_by_source(world, lead)
    # up to 80 characters before the postcode, from a word's start
    assert rows["website"]["quote"] == f"Smith & Jones, solicitors. {OFFICE}"
    assert rows["website"]["source_url"] == ABOUT
    assert rows["registry"]["quote"].endswith("registered name SMITH AND JONES LLP")


def test_without_a_site_postcode_there_is_no_search(world: World) -> None:
    lead = crawled(world, "Smith & Jones, solicitors in London.", SMITH)
    registry = ScriptedRegistry()

    check(world, registry, lead)

    assert registry.calls == []
    assert legal(world, lead) == (None, None, None, "unconfirmed", None, None)
    assert world.evidence(lead) == []
    assert len(selections(world)) == 1


def test_two_name_hits_with_a_site_postcode_are_ambiguous(world: World) -> None:
    lead = crawled(world, f"Smith & Jones. {OFFICE}", SMITH)
    twin = RegistryCompany(OTHER, "SMITH & JONES L.L.P.", "llp", "active", "EC1A1BB")
    registry = ScriptedRegistry(searches={SMITH: (SMITH_LLP, twin)})

    check(world, registry, lead)

    assert registry.operations() == ["search_companies"]
    assert legal(world, lead) == (None, None, None, "unconfirmed", None, None)


@pytest.mark.parametrize(
    "hit",
    [
        RegistryCompany(NUMBER, "SMITH AND JONES HOLDINGS LLP", "llp", "active", "EC1A 1BB"),
        RegistryCompany(NUMBER, "SMITH AND JONES LLP", "llp", "active", "SW1A 2AA"),
        RegistryCompany(NUMBER, "SMITH AND JONES LLP", "llp", "active", None),
    ],
    ids=["another name", "another postcode", "no postcode"],
)
def test_a_hit_must_match_the_name_and_a_site_postcode(world: World, hit: RegistryCompany) -> None:
    lead = crawled(world, f"Smith & Jones. {OFFICE}", SMITH)
    registry = ScriptedRegistry(searches={SMITH: (hit,)})

    check(world, registry, lead)

    assert registry.operations() == ["search_companies"]
    assert legal(world, lead)[3] == "unconfirmed"


def test_the_profile_must_pass_the_same_checks_as_the_hit(world: World) -> None:
    lead = crawled(world, f"Smith & Jones. {OFFICE}", SMITH)
    moved = RegistryCompany(NUMBER, "SMITH AND JONES LLP", "llp", "active", "SW1A 2AA")
    registry = ScriptedRegistry(companies={NUMBER: moved}, searches={SMITH: (SMITH_LLP,)})

    check(world, registry, lead)

    assert registry.operations() == ["search_companies", "get_company"]
    assert legal(world, lead) == (None, None, None, "unconfirmed", None, None)


# Rows: Not active, Not corporate ----------------------------------------------------------------


def test_a_record_that_isnt_active_is_linked_but_unconfirmed(world: World) -> None:
    lead = crawled(world, FOOTER)
    dissolved = RegistryCompany(NUMBER, ACME_LTD.name, "ltd", "dissolved", "EC1A 1BB")

    check(world, ScriptedRegistry(companies={NUMBER: dissolved}), lead)

    assert legal(world, lead)[:4] == (NUMBER, "ltd", "dissolved", "unconfirmed")
    rows = evidence_by_source(world, lead)
    assert set(rows) == {"website", "registry"}
    assert "company status dissolved" in rows["registry"]["quote"]


def test_a_partnership_is_not_corporate_and_eligibility_says_ineligible(world: World) -> None:
    lead = crawled(world, FOOTER)
    contact = gb_contact(world, lead)
    partnership = RegistryCompany(NUMBER, ACME_LTD.name, "limited-partnership", "active", None)
    before = legal_form_reasons(world, contact)

    check(world, ScriptedRegistry(companies={NUMBER: partnership}), lead)

    assert before == {ReasonKind.UK_LEGAL_FORM_UNCONFIRMED}  # never checked
    assert legal(world, lead)[:4] == (NUMBER, "limited-partnership", "active", "not_corporate")
    assert legal_form_reasons(world, contact) == {ReasonKind.UK_LEGAL_FORM_INELIGIBLE}


def test_a_confirmed_ltd_leaves_no_legal_form_reason(world: World) -> None:
    lead = crawled(world, FOOTER)
    contact = gb_contact(world, lead)

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    assert legal_form_reasons(world, contact) == set()
    with world.units.open(read_only=True):
        decision = Compliance(world.clock).evaluate_eligibility(contact)
    assert decision.eligible
    assert decision.basis_rule == (
        "UK: corporate subscriber (private limited company), legitimate interests"
    )


# Row: FR-9 --------------------------------------------------------------------------------------


def test_an_older_lead_holding_the_number_survives_and_gets_the_legal_form(world: World) -> None:
    older = gb_lead(
        world, "Acme Accounting Services", web_domain="acme-old.co.uk", ch_number=NUMBER
    )
    world.clock.advance(timedelta(minutes=1))
    lead = crawled(world, FOOTER)

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    [(merged_into,)] = world.sql("SELECT merged_into FROM leads WHERE id = ?", (lead,))
    assert merged_into == older
    assert legal(world, older)[:4] == (NUMBER, "ltd", "active", "confirmed")
    assert legal(world, lead)[1:] == (None, None, None, None, None)  # the loser's stay put
    rows = evidence_by_source(world, older)
    assert rows["website"]["source_url"] == ABOUT  # the page moved with the merge
    assert world.evidence(lead) == []
    [(key, concurrency, payload, _)] = selections(world)
    assert (key, concurrency, json.loads(payload)) == (
        f"select_primary_contact:{older}:tick",
        "domain:acme-old.co.uk",
        {"v": 1, "lead_id": older},
    )


def test_a_newer_lead_holding_the_number_merges_into_the_checked_lead(world: World) -> None:
    lead = crawled(world, FOOTER)
    world.clock.advance(timedelta(minutes=1))
    newer = gb_lead(
        world, "Acme Accounting Services", web_domain="acme-new.co.uk", ch_number=NUMBER
    )

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    [(merged_into,)] = world.sql("SELECT merged_into FROM leads WHERE id = ?", (newer,))
    assert merged_into == lead
    assert legal(world, lead)[:4] == (NUMBER, "ltd", "active", "confirmed")


# Row: Conflict ----------------------------------------------------------------------------------


def test_a_lead_holding_another_number_is_never_overwritten(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    lead = crawled(world, FOOTER, ch_number=HELD)

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    assert legal(world, lead) == (HELD, None, None, "unconfirmed", None, None)
    assert world.evidence(lead) == []
    [conflict] = checked_logs(caplog, "legal_form_conflict")
    assert fields(conflict, "lead_id") == (lead,)
    [logged] = checked_logs(caplog, "legal_form_checked")
    assert fields(logged, "status", "path") == ("unconfirmed", "none")
    assert len(selections(world)) == 1
    assert_logs_clean(caplog)


def test_a_holder_merged_into_a_lead_with_another_number_is_a_conflict(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    older = crawled(world, f"Acme Accounting Ltd, company number {HELD}", ch_number=HELD)
    held = RegistryCompany(HELD, "ACME ACCOUNTING LTD", "ltd", "active", None)
    check(world, ScriptedRegistry(companies={HELD: held}), older)
    before, evidence = legal(world, older), world.evidence(older)
    world.clock.advance(timedelta(minutes=1))
    holder = gb_lead(world, "Acme Holdings", web_domain="acme-holdings.co.uk", ch_number=NUMBER)
    world.transact(lambda: world.discovery.merge(older, holder))  # the number stays on it
    world.clock.advance(timedelta(minutes=1))
    lead = gb_lead(world, ACME, web_domain="acme-new.co.uk")
    world.sql(  # the checked Lead's own page cites the holder's number
        "INSERT INTO crawled_pages (id, lead_id, run_id, url, final_url, kind, fetched_at, "
        "http_status, content_hash, text, text_purge_after, changed_at, tag_managers) "
        "SELECT '01KA0000000000000000000077', ?, run_id, 'https://acme-new.co.uk/about', "
        "'https://acme-new.co.uk/about', kind, fetched_at, http_status, content_hash, ?, "
        "text_purge_after, changed_at, tag_managers FROM crawled_pages WHERE url = ?",
        (lead, FOOTER, ABOUT),
    )
    done_selections(world)
    caplog.set_level(logging.INFO, logger=LOGGER)

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    [(merged_into,)] = world.sql("SELECT merged_into FROM leads WHERE id = ?", (lead,))
    assert merged_into == older  # the merge happens
    assert before[:4] == (HELD, "ltd", "active", "confirmed")
    assert legal(world, older) == before  # the survivor's legal form is unchanged
    assert [row for row in world.evidence(older) if row["id"] in (before[4], before[5])] == [
        row for row in evidence if row["id"] in (before[4], before[5])
    ]
    [conflict] = checked_logs(caplog, "legal_form_conflict")
    assert fields(conflict, "lead_id") == (lead,)
    pending = world.sql(
        "SELECT payload FROM jobs WHERE type = 'select_primary_contact' AND status != 'done'"
    )
    assert [json.loads(payload)["lead_id"] for (payload,) in pending] == [older]


def test_a_csv_number_is_never_evidence(world: World) -> None:
    lead = crawled(world, "Acme Accounting, chartered accountants.", ch_number=NUMBER)
    registry = ScriptedRegistry()

    check(world, registry, lead)

    assert registry.calls == []  # the stored number isn't looked up
    assert legal(world, lead) == (NUMBER, None, None, "unconfirmed", None, None)


def test_the_same_number_held_already_is_confirmed(world: World) -> None:
    lead = crawled(world, FOOTER, ch_number=NUMBER)

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    assert legal(world, lead)[:4] == (NUMBER, "ltd", "active", "confirmed")


# Row: Non-GB ------------------------------------------------------------------------------------


@pytest.mark.parametrize("country", ["US", None])
def test_a_lead_outside_gb_makes_no_call_and_is_selected(
    world: World, caplog: pytest.LogCaptureFixture, country: str | None
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    lead = crawled(world, FOOTER, country=country)
    registry = ScriptedRegistry()

    assert check(world, registry, lead) is True

    assert registry.calls == []
    assert legal(world, lead) == (None, None, None, None, None, None)
    assert len(selections(world)) == 1
    [skipped] = checked_logs(caplog, "legal_form_skipped")
    assert fields(skipped, "lead_id", "reason") == (lead, "not_gb")


@pytest.mark.parametrize("merged", [False, True])
def test_an_unknown_or_merged_lead_is_skipped(
    world: World, caplog: pytest.LogCaptureFixture, merged: bool
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    older = gb_lead(world, "Older", web_domain="older.co.uk")
    world.clock.advance(timedelta(minutes=1))
    lead = crawled(world, FOOTER)
    target = "01KA0000000000000000000000"
    if merged:
        world.transact(lambda: world.discovery.merge(older, lead))
        target = lead
    registry = ScriptedRegistry()

    assert check(world, registry, target) is True

    assert registry.calls == [] and selections(world) == []
    [skipped] = checked_logs(caplog, "legal_form_skipped")
    assert fields(skipped, "reason") == ("merged_lead" if merged else "unknown_lead",)


def test_a_lead_merged_during_the_calls_is_skipped_with_nothing_written(world: World) -> None:
    older = gb_lead(world, "Older", web_domain="older.co.uk")
    world.clock.advance(timedelta(minutes=1))
    lead = crawled(world, FOOTER)

    def merging() -> RegistryCompany:
        contextvars.Context().run(world.transact, lambda: world.discovery.merge(older, lead))
        return ACME_LTD

    check(world, ScriptedRegistry(companies={NUMBER: merging}), lead)

    assert legal(world, older) == (None, None, None, None, None, None)
    assert legal(world, lead) == (None, None, None, None, None, None)
    assert selections(world) == []


# Row: Errors ------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["registry_unavailable", "registry_rate_limited"])
def test_a_retryable_error_raises_and_nothing_is_recorded_or_queued(
    world: World, caplog: pytest.LogCaptureFixture, code: str
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    lead = crawled(world, FOOTER)
    registry = ScriptedRegistry(companies={NUMBER: AdapterError(True, code, "unavailable")})

    with pytest.raises(AdapterError) as raised:
        check(world, registry, lead)

    assert (raised.value.code, raised.value.retryable) == (code, True)
    assert legal(world, lead) == (None, None, None, None, None, None)
    assert world.evidence(lead) == [] and selections(world) == []
    [logged] = checked_logs(caplog, "legal_form_checked")
    assert fields(logged, "status", "path", "error_code") == (None, "number", code)


@pytest.mark.parametrize(
    "code",
    ["credential_not_set", "registry_unauthorized", "registry_refused", "registry_bad_response"],
)
def test_a_non_retryable_error_keeps_the_prior_state_and_still_selects(
    world: World, caplog: pytest.LogCaptureFixture, code: str
) -> None:
    lead = crawled(world, f"Smith & Jones. {OFFICE}", SMITH)
    found = ScriptedRegistry(companies={NUMBER: SMITH_LLP}, searches={SMITH: (SMITH_LLP,)})
    check(world, found, lead)
    before, evidence = legal(world, lead), world.evidence(lead)
    done_selections(world)
    caplog.set_level(logging.INFO, logger=LOGGER)
    failing = ScriptedRegistry(searches={SMITH: AdapterError(False, code, "refused")})

    assert check(world, failing, lead) is True

    assert before[3] == "confirmed"
    assert (legal(world, lead), world.evidence(lead)) == (before, evidence)
    assert len(selections(world)) == 2
    [logged] = checked_logs(caplog, "legal_form_checked")
    assert fields(logged, "status", "path", "error_code") == (None, "name", code)


# Row: Budget ------------------------------------------------------------------------------------


def test_a_budget_refusal_defers_with_no_call(world: World) -> None:
    lead = crawled(world, FOOTER)
    spent = AiCall("r", "t", "m", input_tokens=1, output_tokens=1, cost_micros=20_000_000)
    world.meter.record(spent)  # the month's whole AI budget
    registry = ScriptedRegistry()

    with world.units.open():
        assert handler_on(world, registry)(check_job(lead)) is DEFERRED

    assert registry.calls == [] and selections(world) == []
    assert legal(world, lead) == (None, None, None, None, None, None)


def test_the_job_handler_runs_the_check(world: World) -> None:
    lead = crawled(world, FOOTER)
    registry = ScriptedRegistry(companies={NUMBER: ACME_LTD})

    with world.units.open():
        assert handler_on(world, registry)(check_job(lead)) is None

    assert legal(world, lead)[3] == "confirmed"


# Row: Re-run ------------------------------------------------------------------------------------


def test_a_rerun_with_the_same_quotes_confirms_the_same_rows(world: World) -> None:
    lead = crawled(world, FOOTER)
    registry = ScriptedRegistry(companies={NUMBER: ACME_LTD})
    check(world, registry, lead)
    first = legal(world, lead)
    seen = len(world.recorder.seen)
    world.clock.advance(timedelta(days=1))

    check(world, registry, lead)

    assert legal(world, lead) == first
    rows = {row["id"]: row for row in world.evidence(lead)}
    assert set(rows) == {first[4], first[5]}
    assert rows[first[5]]["last_confirmed_at"] == utc(T0 + timedelta(days=1))
    assert rows[first[4]]["last_confirmed_at"] == fetched(world)  # the page wasn't fetched again
    assert all(row["superseded_by"] is None for row in rows.values())
    assert all(row["retracted_at"] is None for row in rows.values())
    assert len(world.recorder.seen) == seen


def test_a_changed_record_supersedes_its_registry_row(world: World) -> None:
    lead = crawled(world, FOOTER)
    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)
    first = legal(world, lead)
    world.clock.advance(timedelta(days=1))
    dissolved = RegistryCompany(NUMBER, ACME_LTD.name, "ltd", "dissolved", "EC1A 1BB")

    check(world, ScriptedRegistry(companies={NUMBER: dissolved}), lead)

    now = legal(world, lead)
    assert now[:4] == (NUMBER, "ltd", "dissolved", "unconfirmed")
    assert now[4] == first[4]  # the site quote is the same row
    assert now[5] != first[5]
    rows = {row["id"]: row for row in world.evidence(lead)}
    assert rows[first[5]]["superseded_by"] == now[5]
    assert EvidenceChanged(lead_id=lead, evidence_id=first[5]) in world.recorder.seen


def test_a_lost_link_retracts_both_rows_and_clears_the_pointers(world: World) -> None:
    lead = crawled(world, FOOTER)
    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)
    first = legal(world, lead)
    world.sql(
        "UPDATE crawled_pages SET text = ? WHERE url = ?",
        ("Acme Accounting, chartered accountants.", ABOUT),
    )
    world.clock.advance(timedelta(days=1))

    check(world, ScriptedRegistry(), lead)

    assert legal(world, lead) == (NUMBER, None, None, "unconfirmed", None, None)
    rows = {row["id"]: row for row in world.evidence(lead)}
    assert {rows[first[4]]["retracted_at"], rows[first[5]]["retracted_at"]} == {
        utc(T0 + timedelta(days=1))
    }
    seen = {notice.evidence_id for notice in world.recorder.seen}
    assert {first[4], first[5]} <= seen


def test_a_pointer_to_a_row_that_isnt_live_is_ignored(world: World) -> None:
    lead = crawled(world, FOOTER)
    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)
    first = legal(world, lead)
    world.sql(
        "UPDATE evidence SET retracted_at = ? WHERE id = ?", (utc(T0), first[5])
    )  # as another writer might

    check(world, ScriptedRegistry(companies={NUMBER: ACME_LTD}), lead)

    now = legal(world, lead)
    assert now[4] == first[4] and now[5] not in (None, first[5])


# The chain --------------------------------------------------------------------------------------


def test_an_unconfirmed_gb_lead_is_still_selected_and_stays_on_the_leads_list(world: World) -> None:
    lead = crawled(world, "Acme Accounting, chartered accountants.")

    check(world, ScriptedRegistry(), lead)
    [(_, _, payload, _)] = selections(world)
    with world.units.open():
        choice = PrimaryContacts(world.clock, world.research).select_primary_contact(
            json.loads(payload)["lead_id"]
        )

    assert choice is not None  # selection ran (no decision-maker here)
    listed = world.read(lambda: discovery_queries.lead_list())
    assert lead in {row.id for row in listed.leads}
    assert legal(world, lead)[3] == "unconfirmed"


def test_the_record_command_refuses_a_site_quote_that_isnt_on_the_page(world: World) -> None:
    lead = crawled(world, FOOTER)
    finding = LegalFormFinding(
        LegalFormStatus.CONFIRMED,
        NUMBER,
        "ltd",
        "active",
        SiteQuote(ABOUT, "Company No. 07777777", T0),
        ACME_QUOTE,
        T0,
    )
    attempts: list[Callable[[], None]] = [
        lambda: world.research.record_legal_form(lead, finding),
        lambda: world.research.record_legal_form(
            lead,
            LegalFormFinding(
                LegalFormStatus.CONFIRMED,
                NUMBER,
                "ltd",
                "active",
                SiteQuote("https://smith-law.com/missing", FOOTER_QUOTE, T0),
                ACME_QUOTE,
                T0,
            ),
        ),
    ]

    for attempt in attempts:
        with pytest.raises(ValueError, match="site quote"):
            world.transact(attempt)

    assert legal(world, lead) == (None, None, None, None, None, None)
    assert world.evidence(lead) == []
