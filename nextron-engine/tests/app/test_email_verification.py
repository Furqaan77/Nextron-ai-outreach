"""Story 2.14's address checks on a temporary database at head (FR-15, FR-57, FR-59, AD-4, AD-9,
AD-16, INT-6, INT-11): ``EmailVerification.verify_contact_email`` through the real
``verify_contact_email`` handler, with ``FakeDns``, ``FakeEmailSyntax`` and ``ScriptedVerifier``,
on Story 2.9's world (``tests.app.test_signal_extraction``: the shipped Segments and Markets, a
real ``WebsiteCrawls`` on a ``ScriptedCrawler``, the real gate and a ``FrozenClock``).

Every row of the spec's matrix is here: a published address that passes, no MX, a null MX, a
disposable domain (no DNS query), a DNS timeout (retried, nothing written), the Primary's
``guessed`` address verified or invalidated by Hunter (and the email search enqueued once), an
``accept_all`` answer (no second call), a non-Primary ``guessed`` address, the quota (the handler
defers), a suppressed address and a guessed address a crawl finds on the site. Also the syntax
check, the skips, the enqueues (the email search's assignment, the CSV import, the selection and
"Make primary"), a new address clearing the checks, the ``events`` and Provenance rows and no
address or domain in a log line. No DNS, SMTP or Hunter request is made."""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import ImportRequest, LeadImports
from nextron.app.research.finding import EmailFinding
from nextron.app.research.people import PrimaryContacts
from nextron.app.research.verification import EmailVerification
from nextron.db.repos.research import contacts as contacts_repo
from nextron.domain.budget import ApiCall
from nextron.domain.contactability import AddressSubject, SuppressionScope
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.job import DEFERRED, Job, Lane
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.dns import MxRecord
from nextron.ports.email_finder import FinderAnswer, VerifierAnswer
from nextron.ports.errors import AdapterError
from tests.app.test_signal_extraction import SITE, T0, TEAM, World, build_world, no_jobs, utc
from tests.fakes.dns import FakeDns
from tests.fakes.email_finder import ScriptedDomainSearch, ScriptedFinder, ScriptedVerifier
from tests.fakes.email_syntax import FakeEmailSyntax
from tests.fakes.renderer import rendered_page

JANE = "Jane Roe"
JANE_ADDRESS = f"jane.roe@{SITE}"
JANE_LINE = f"{JANE}, Managing Partner – {JANE_ADDRESS}"
ACTOR = "system:research"
MX = (MxRecord(10, f"mx.{SITE}"),)
JOB_ID = "01JD0000000000000000000099"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine, prompts=False)


@dataclass
class Checks:
    """The service on scripted ports, and the real handler on it."""

    service: EmailVerification
    dns: FakeDns
    syntax: FakeEmailSyntax
    verifier: ScriptedVerifier

    def handler(self) -> Callable[[Job], object]:
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
            legal_forms=no_jobs,
            scores=no_jobs,
            verification=lambda: self.service,
        ).get("verify_contact_email").handler


def checks(
    world: World,
    *,
    mx: dict[str, tuple[MxRecord, ...]] | None = None,
    errors: dict[str, AdapterError] | None = None,
    verifier: tuple[Any, ...] = (),
    refused: tuple[str, ...] = (),
) -> Checks:
    dns = FakeDns(mx={SITE: MX} if mx is None else mx, errors=errors)
    syntax = FakeEmailSyntax(refused)
    verifying = ScriptedVerifier(*verifier)
    service = EmailVerification(
        world.clock, world.meter, world.queue, dns=dns, email_syntax=syntax, verifier=verifying
    )
    return Checks(service, dns, syntax, verifying)


def run(world: World, found: Checks, lead: str, contact: str) -> object:
    """The ``verify_contact_email`` job, through the real handler, as the runner runs it."""
    payload = {"v": 1, "lead_id": lead, "contact_id": contact}
    job = Job(JOB_ID, "verify_contact_email", Lane.BULK, payload, 1)
    with world.units.open():
        return found.handler()(job)


def verify(world: World, found: Checks, lead: str, contact: str) -> str | None:
    with world.units.open():
        return found.service.verify_contact_email(lead, contact)


def site(world: World, *lines: str) -> str:
    """A Lead whose team page holds ``lines`` (one paragraph each), crawled at ``T0``."""
    lead = world.lead()
    world.site("".join(f"<p>{line}</p>" for line in lines))
    world.crawl(lead)
    return lead


def add(world: World, lead: str, name: str = JANE, **kw: Any) -> str:
    world.clock.advance(timedelta(seconds=1))
    added = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name=name, role="Managing Partner", **kw),
            source=ProvenanceSource.CSV,
            detail="list.csv",
            retrieved_at=T0,
        )
    )
    assert added.contact_id is not None
    return added.contact_id


def make_primary(world: World, contact: str) -> None:
    world.transact(lambda: contacts_repo.set_primary(contact, "operator", world.clock.now()))


COLUMNS = (
    "email",
    "address_class",
    "email_check_result",
    "email_checked_at",
    "email_invalid_reason",
    "email_verifier_at",
)


def contact(world: World, contact_id: str) -> dict[str, Any]:
    [row] = world.sql(f"SELECT {', '.join(COLUMNS)} FROM contacts WHERE id = ?", (contact_id,))
    return dict(zip(COLUMNS, row, strict=True))


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


def jobs(world: World, job_type: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT idempotency_key, payload, lane FROM jobs WHERE type = ? ORDER BY rowid",
        (job_type,),
    )


def now(world: World) -> str:
    return utc(world.clock.now())


# Row: Published, ok ------------------------------------------------------------------------------


def test_a_published_address_with_an_mx_passes_its_checks_with_no_verifier_call(
    world: World,
) -> None:
    lead = site(world, JANE_LINE)
    jane = add(world, lead)
    make_primary(world, jane)
    finding = EmailFinding(
        world.clock,
        world.meter,
        world.research,
        world.queue,
        domain_search=ScriptedDomainSearch(),
        hunter=ScriptedFinder(),
        prospeo=ScriptedFinder(),
    )
    with world.units.open():
        finding.find_contact_email(lead, jane)
    assert contact(world, jane)["address_class"] == "published"
    # the assignment enqueued the check, in its transaction
    [(key, payload, lane)] = jobs(world, "verify_contact_email")
    assert (key, json.loads(payload), lane) == (
        f"verify_contact_email:{jane}:tick",
        {"v": 1, "lead_id": lead, "contact_id": jane},
        "bulk",
    )
    found = checks(world, verifier=(VerifierAnswer("valid"),))
    before = len(contact_events(world, jane))

    assert run(world, found, lead, jane) is None

    assert found.dns.queries == [("mx", SITE)]
    assert found.syntax.checked == [JANE_ADDRESS]
    assert found.verifier.calls == []  # a published address is never verified
    assert contact(world, jane) == {
        "email": JANE_ADDRESS,
        "address_class": "published",
        "email_check_result": "checks_passed",
        "email_checked_at": now(world),
        "email_invalid_reason": None,
        "email_verifier_at": None,
    }
    assert len(contact_events(world, jane)) == before  # no class change


# Rows: No MX, null MX, disposable, syntax --------------------------------------------------------


def test_a_domain_without_mx_makes_the_address_invalid_with_provenance_and_an_event(
    world: World,
) -> None:
    lead = site(world)
    jroe = add(world, lead, email="jroe@dead.example")
    found = checks(world, mx={"dead.example": ()})

    assert run(world, found, lead, jroe) is None

    row = contact(world, jroe)
    assert (row["address_class"], row["email_check_result"], row["email_invalid_reason"]) == (
        "invalid",
        "invalid",
        "no_mx",
    )
    assert row["email_verifier_at"] is None and row["email_checked_at"] == now(world)
    assert address_provenance(world, jroe) == [
        ("csv", "list.csv", "jroe@dead.example", utc(T0), "guessed"),
        ("csv", "list.csv", "jroe@dead.example", now(world), "invalid"),  # the copy, now
    ]
    assert contact_events(world, jroe)[-1] == ("guessed", "invalid", "check_failed", None, ACTOR)
    assert found.verifier.calls == []


def test_only_a_null_mx_is_null_mx_and_a_null_beside_a_real_one_passes(world: World) -> None:
    lead = site(world)
    nul = add(world, lead, "Ann Fox", email="ann@nomail.example")
    mixed = add(world, lead, "Bo Day", email="bo@mixed.example")
    found = checks(
        world,
        mx={
            "nomail.example": (MxRecord(0, ""),),
            "mixed.example": (MxRecord(0, ""), MxRecord(10, "mx.mixed.example")),
        },
    )

    run(world, found, lead, nul)
    run(world, found, lead, mixed)

    assert contact(world, nul)["email_invalid_reason"] == "null_mx"
    assert contact(world, nul)["address_class"] == "invalid"
    assert contact(world, mixed)["email_check_result"] == "checks_passed"
    assert contact(world, mixed)["address_class"] == "guessed"


@pytest.mark.parametrize("address", ["x@mailinator.com", "x@eu.mailinator.com"])
def test_a_disposable_domain_is_invalid_with_no_dns_query(world: World, address: str) -> None:
    lead = site(world)
    x = add(world, lead, email=address)
    found = checks(world)

    run(world, found, lead, x)

    row = contact(world, x)
    assert (row["address_class"], row["email_invalid_reason"]) == ("invalid", "disposable")
    assert found.dns.queries == []


def test_a_syntax_refusal_is_invalid_with_no_dns_query(world: World) -> None:
    lead = site(world)
    x = add(world, lead, email=f"x@{SITE}")
    found = checks(world, refused=(f"x@{SITE}",))

    run(world, found, lead, x)

    row = contact(world, x)
    assert (row["address_class"], row["email_invalid_reason"]) == ("invalid", "syntax")
    assert found.dns.queries == []


def test_the_mx_lookup_asks_the_idna_form_of_the_domain(world: World) -> None:
    lead = site(world)
    x = add(world, lead, email="anna@xn--bcher-kva.example")
    # a row stored before the normalising (or by hand) holds the Unicode form
    world.sql("UPDATE contacts SET email = 'anna@bücher.example' WHERE id = ?", (x,))
    found = checks(world, mx={"xn--bcher-kva.example": (MxRecord(5, "mx.example"),)})

    run(world, found, lead, x)

    assert found.dns.queries == [("mx", "xn--bcher-kva.example")]
    assert contact(world, x)["email_check_result"] == "checks_passed"


# Row: DNS timeout ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (AdapterError(True, "dns_timeout", "the query timed out"), True),
        (AdapterError(True, "dns_unavailable", "no nameserver answered"), True),
        (AdapterError(False, "dns_failed", "another DNS error"), False),
    ],
)
def test_a_dns_error_propagates_and_nothing_is_written(
    world: World, error: AdapterError, retryable: bool
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    before = len(contact_events(world, jane))
    found = checks(world, errors={SITE: error})

    with pytest.raises(AdapterError) as raised:
        run(world, found, lead, jane)

    assert raised.value.retryable is retryable
    assert contact(world, jane) | {"email": None} == {
        "email": None,
        "address_class": "guessed",
        "email_check_result": None,
        "email_checked_at": None,
        "email_invalid_reason": None,
        "email_verifier_at": None,
    }
    assert len(contact_events(world, jane)) == before


# Rows: the Primary's guessed address and Hunter ---------------------------------------------------


def test_the_primarys_guessed_address_hunter_calls_valid_is_verified(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    found = checks(world, verifier=(VerifierAnswer("valid"),))

    assert run(world, found, lead, jane) is None

    assert found.verifier.calls == [JANE_ADDRESS]
    assert contact(world, jane) == {
        "email": JANE_ADDRESS,
        "address_class": "verified",
        "email_check_result": "valid",
        "email_checked_at": now(world),
        "email_invalid_reason": None,
        "email_verifier_at": now(world),
    }
    assert address_provenance(world, jane)[-1] == (
        "finder",
        "Hunter Email Verifier",
        JANE_ADDRESS,
        now(world),
        "verified",
    )
    assert contact_events(world, jane)[-1] == (
        "guessed",
        "verified",
        "verification_valid",
        None,
        ACTOR,
    )


def test_hunters_invalid_makes_it_invalid_and_enqueues_the_search_once(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    world.sql("UPDATE contacts SET email_search_tried = 'hunter' WHERE id = ?", (jane,))
    found = checks(world, verifier=(VerifierAnswer("invalid"),))

    run(world, found, lead, jane)
    run(world, found, lead, jane)  # skipped now: invalid is terminal

    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"], row["email_invalid_reason"]) == (
        "invalid",
        "invalid",
        "verifier_invalid",
    )
    assert row["email_verifier_at"] == now(world)
    assert found.verifier.calls == [JANE_ADDRESS]
    assert address_provenance(world, jane)[-1][:2] + address_provenance(world, jane)[-1][4:] == (
        "finder",
        "Hunter Email Verifier",
        "invalid",
    )
    assert contact_events(world, jane)[-1] == ("guessed", "invalid", "check_failed", None, ACTOR)
    [(key, payload, lane)] = jobs(world, "find_contact_email")  # Prospeo is untried
    assert (key, json.loads(payload), lane) == (
        f"find_contact_email:{jane}:tick",
        {"v": 1, "lead_id": lead, "contact_id": jane},
        "bulk",
    )


def test_no_search_is_enqueued_when_every_source_was_tried(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    world.sql("UPDATE contacts SET email_search_tried = 'hunter,prospeo' WHERE id = ?", (jane,))
    world.sql(
        "INSERT INTO email_domains (id, domain, domain_search_at, created_at, updated_at) "
        "VALUES ('01JD0000000000000000000001', ?, ?, ?, ?)",
        (SITE, utc(T0), utc(T0), utc(T0)),
    )

    run(world, checks(world, mx={SITE: ()}), lead, jane)

    assert contact(world, jane)["address_class"] == "invalid"
    assert jobs(world, "find_contact_email") == []


def test_an_invalid_address_on_a_contact_that_isnt_primary_enqueues_no_search(
    world: World,
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)

    run(world, checks(world, mx={SITE: ()}), lead, jane)

    assert contact(world, jane)["address_class"] == "invalid"
    assert jobs(world, "find_contact_email") == []


@pytest.mark.parametrize("status", ["accept_all", "unknown", "webmail", "disposable", "Odd!"])
def test_another_status_stays_guessed_and_a_re_run_makes_no_call(
    world: World, status: str
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    found = checks(world, verifier=(VerifierAnswer(status), VerifierAnswer("valid")))

    run(world, found, lead, jane)

    stored = "other" if status == "Odd!" else status
    first = contact(world, jane)
    assert (first["address_class"], first["email_check_result"]) == ("guessed", stored)
    assert first["email_verifier_at"] == now(world)

    world.clock.advance(timedelta(days=1))
    run(world, found, lead, jane)

    assert found.verifier.calls == [JANE_ADDRESS]  # once per address
    assert contact(world, jane) == first  # Hunter's status and its dates survive the re-run


def test_a_guessed_address_on_a_contact_that_isnt_primary_has_no_verifier_call(
    world: World,
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    found = checks(world, verifier=(VerifierAnswer("valid"),))

    run(world, found, lead, jane)

    assert found.verifier.calls == []
    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"]) == ("guessed", "checks_passed")


def test_hunters_quota_reached_defers_with_no_call_then_runs(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    world.meter.record(ApiCall("hunter", "find", ok=True, count=50))  # 100 units: 50 credits
    found = checks(world, verifier=(VerifierAnswer("valid"),))

    assert run(world, found, lead, jane) is DEFERRED

    assert found.verifier.calls == []
    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"]) == ("guessed", "quota_reached")
    assert row["email_verifier_at"] is None

    world.meter.records.clear()
    assert run(world, found, lead, jane) is None
    assert contact(world, jane)["address_class"] == "verified"


def test_a_verifier_error_propagates_and_nothing_is_written(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    found = checks(world, verifier=(AdapterError(True, "hunter_unavailable", "busy"),))

    with pytest.raises(AdapterError):
        run(world, found, lead, jane)

    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"], row["email_verifier_at"]) == (
        "guessed",
        None,
        None,
    )


# Row: Suppressed ----------------------------------------------------------------------------------


def test_a_suppressed_address_records_suppressed_with_no_lookup(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    world.transact(
        lambda: Compliance(world.clock).suppress(
            AddressSubject(JANE_ADDRESS),
            SuppressionScope.PERSON,
            SuppressionSource.OPERATOR,
            None,
        )
    )
    before = len(contact_events(world, jane))
    found = checks(world, verifier=(VerifierAnswer("invalid"),))

    assert run(world, found, lead, jane) is None

    assert found.dns.queries == [] and found.verifier.calls == [] and found.syntax.checked == []
    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"]) == ("guessed", "suppressed")
    assert row["email_checked_at"] == now(world)
    assert len(contact_events(world, jane)) == before


# Row: Crawl finds it ------------------------------------------------------------------------------


def test_a_crawl_that_finds_a_guessed_address_publishes_it_with_its_quote(world: World) -> None:
    lead = world.lead()
    jane = add(world, lead, email=JANE_ADDRESS)
    other = add(world, lead, "Bo Day", email=f"bo.day@{SITE}")
    world.site(f"<p>{JANE_LINE}</p>")

    world.crawl(lead)

    assert contact(world, jane)["address_class"] == "published"
    assert contact(world, other)["address_class"] == "guessed"  # not on the page
    [(fetched,)] = world.sql(
        "SELECT fetched_at FROM crawled_pages WHERE lead_id = ? AND url = ?", (lead, TEAM)
    )
    assert address_provenance(world, jane)[-1] == (
        "website",
        TEAM,
        JANE_ADDRESS,
        fetched,
        "published",
    )
    assert contact_events(world, jane)[-1] == (
        "guessed",
        "published",
        "found_on_site",
        None,
        ACTOR,
    )
    quotes = [
        row
        for row in world.evidence(lead)
        if row["person_contact_id"] == jane and row["extracted_by"] == "rule"
    ]
    assert [(row["type"], row["quote"], row["source_url"]) for row in quotes] == [
        ("quote", JANE_LINE, TEAM)
    ]

    world.crawl(lead)  # again: nothing more moves, and the quote is confirmed, not added
    assert len([row for row in world.evidence(lead) if row["person_contact_id"] == jane]) == 1
    assert contact_events(world, jane)[-1][2] == "found_on_site"


def test_a_mailto_link_publishes_a_guessed_address(world: World) -> None:
    lead = world.lead()
    jane = add(world, lead, email=JANE_ADDRESS)
    world.site(f'<p><a href="mailto:{JANE_ADDRESS}">Email Jane</a></p>')

    world.crawl(lead)

    assert contact(world, jane)["address_class"] == "published"
    quotes = [row["quote"] for row in world.evidence(lead) if row["person_contact_id"] == jane]
    assert quotes == [f"mailto:{JANE_ADDRESS}"]


def test_a_longer_address_holding_the_guessed_one_doesnt_publish_it(world: World) -> None:
    lead = world.lead()
    jane = add(world, lead, email=f"roe@{SITE}")
    world.site(f"<p>Write to jane.roe@{SITE} or xroe@{SITE}.</p>")

    world.crawl(lead)

    assert contact(world, jane)["address_class"] == "guessed"


# Skips --------------------------------------------------------------------------------------------


def test_the_skipped_cases_write_nothing_and_call_nothing(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.verification")
    lead = site(world)
    other_lead = world.lead("other-firm.com")
    nobody = add(world, lead, "Ann Fox")  # no address
    gone = add(world, lead, "Bo Day", email=f"bo@{SITE}")
    world.sql("UPDATE contacts SET address_class = 'invalid' WHERE id = ?", (gone,))
    found = checks(world)

    for contact_id, on in (
        (nobody, lead),
        (gone, lead),
        ("01JD0000000000000000000077", lead),
        (gone, other_lead),
    ):
        assert verify(world, found, on, contact_id) is None

    assert found.dns.queries == [] and found.verifier.calls == []
    assert contact(world, nobody)["email_check_result"] is None
    assert contact(world, gone)["email_check_result"] is None
    assert [
        getattr(r, "reason") for r in caplog.records if r.getMessage() == "email_check_skipped"
    ] == ["no_address", "invalid", "not_on_lead", "not_on_lead"]


def test_the_log_lines_hold_no_address_or_domain(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.verification")
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)

    run(world, checks(world, verifier=(VerifierAnswer("valid"),)), lead, jane)

    [finished] = [r for r in caplog.records if r.getMessage() == "email_check_finished"]
    assert (finished.result, finished.calls) == ("valid", 1)  # type: ignore[attr-defined]
    shown = " ".join(str(vars(record)) for record in caplog.records)
    assert "jane" not in shown and SITE not in shown


# Enqueues -----------------------------------------------------------------------------------------


def test_the_selection_and_make_primary_enqueue_the_check_of_a_guessed_primary(
    world: World,
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    pat = add(world, lead, "Pat Moe", email=f"pat.moe@{SITE}")
    selection = PrimaryContacts(world.clock, world.research, world.queue)

    with world.units.open():
        choice = selection.select_primary_contact(lead)
    assert choice is not None and choice.contact_id == jane
    with world.units.open():
        selection.make_primary(lead, pat)

    assert [(key, lane) for key, _, lane in jobs(world, "verify_contact_email")] == [
        (f"verify_contact_email:{jane}:tick", "bulk"),
        (f"verify_contact_email:{pat}:tick", "interactive"),
    ]
    assert jobs(world, "find_contact_email") == []


def test_a_verified_primary_enqueues_no_check(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    run(world, checks(world, verifier=(VerifierAnswer("accept_all"),)), lead, jane)
    selection = PrimaryContacts(world.clock, world.research, world.queue)

    with world.units.open():
        selection.make_primary(lead, jane)

    assert jobs(world, "verify_contact_email") == []


def test_the_csv_import_enqueues_a_check_for_each_contact_it_creates_with_an_address(
    world: World,
) -> None:
    imports = LeadImports(
        world.clock, world.discovery, world.research, FakeEmailSyntax(), world.queue
    )
    data = (
        "Organization name,Website,Country,Contact name,Email\r\n"
        "Roe LLP,roe-law.com,US,Jane Roe,jane@roe-law.com\r\n"
        "Fox LLP,fox-law.com,US,Ann Fox,\r\n"
    ).encode()

    with world.units.open():
        summary = imports.run(ImportRequest("list.csv", "law_private_ai", data))

    assert summary.imported == 2
    [(key, payload, _)] = jobs(world, "verify_contact_email")
    [(jane,)] = world.sql("SELECT id FROM contacts WHERE email = 'jane@roe-law.com'")
    assert key == f"verify_contact_email:{jane}:tick"
    assert json.loads(payload)["contact_id"] == jane


def test_a_new_address_clears_the_old_ones_checks(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    run(world, checks(world, verifier=(VerifierAnswer("accept_all"),)), lead, jane)
    assert contact(world, jane)["email_verifier_at"] is not None

    world.transact(
        lambda: contacts_repo.set_address(
            jane, email=f"jr@{SITE}", address_class="guessed", at=world.clock.now()
        )
    )

    assert contact(world, jane) == {
        "email": f"jr@{SITE}",
        "address_class": "guessed",
        "email_check_result": None,
        "email_checked_at": None,
        "email_invalid_reason": None,
        "email_verifier_at": None,
    }


def test_a_finder_address_is_checked_after_the_search_assigns_it(world: World) -> None:
    lead = site(world, f"{JANE}, Managing Partner")
    jane = add(world, lead)
    make_primary(world, jane)
    finding = EmailFinding(
        world.clock,
        world.meter,
        world.research,
        world.queue,
        domain_search=ScriptedDomainSearch(),
        hunter=ScriptedFinder(FinderAnswer(f"jr@{SITE}")),
        prospeo=ScriptedFinder(),
    )
    with world.units.open():
        finding.find_contact_email(lead, jane)
    assert [key for key, _, _ in jobs(world, "verify_contact_email")] == [
        f"verify_contact_email:{jane}:tick"
    ]
    found = checks(world, verifier=(VerifierAnswer("valid"),))

    run(world, found, lead, jane)

    assert found.verifier.calls == [f"jr@{SITE}"]
    assert contact(world, jane)["address_class"] == "verified"


# Review fixes (pass 1) ----------------------------------------------------------------------------


def suppress(world: World, address: str) -> None:
    world.transact(
        lambda: Compliance(world.clock).suppress(
            AddressSubject(address), SuppressionScope.PERSON, SuppressionSource.OPERATOR, None
        )
    )


def elsewhere(fn: Callable[[], object]) -> None:
    """Run ``fn`` on another thread (its own unit of work), as another process would."""
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join()


class CallbackDns(FakeDns):
    """``FakeDns`` that runs ``during`` inside each MX query."""

    def __init__(self, during: Callable[[], object], **kw: Any) -> None:
        super().__init__(**kw)
        self.during = during

    def mx(self, name: str) -> tuple[MxRecord, ...]:
        self.during()
        return super().mx(name)


class CallbackVerifier(ScriptedVerifier):
    """``ScriptedVerifier`` that runs ``during`` inside each call."""

    def __init__(self, during: Callable[[], object], *script: Any) -> None:
        super().__init__(*script)
        self.during = during

    def verify_email(self, email: str) -> VerifierAnswer:
        self.during()
        return super().verify_email(email)


def with_ports(
    world: World, *, dns: FakeDns | None = None, verifier: ScriptedVerifier | None = None
) -> Checks:
    found = checks(world)
    found.dns = dns or found.dns
    found.verifier = verifier or found.verifier
    found.service = EmailVerification(
        world.clock,
        world.meter,
        world.queue,
        dns=found.dns,
        email_syntax=found.syntax,
        verifier=found.verifier,
    )
    return found


def test_find_on_site_leaves_a_suppressed_contact_alone(world: World) -> None:
    lead = world.lead()
    jane = add(world, lead, email=JANE_ADDRESS)
    suppress(world, JANE_ADDRESS)
    records = address_provenance(world, jane)
    events = contact_events(world, jane)
    world.site(f"<p>{JANE_LINE}</p>")

    world.crawl(lead)

    assert contact(world, jane)["address_class"] == "guessed"
    assert address_provenance(world, jane) == records
    assert contact_events(world, jane) == events
    assert [row for row in world.evidence(lead) if row["person_contact_id"] == jane] == []


def test_find_on_site_leaves_an_erased_contact_alone(world: World) -> None:
    lead = world.lead()
    jane = add(world, lead, email=JANE_ADDRESS)
    world.transact(lambda: contacts_repo.tombstone(jane, world.clock.now()))
    events = contact_events(world, jane)
    world.site(f"<p>{JANE_LINE}</p>")

    world.crawl(lead)

    assert contact_events(world, jane) == events
    assert [row for row in world.evidence(lead) if row["person_contact_id"] == jane] == []


def test_find_on_site_through_the_render_publishes_an_address_only_the_render_shows(
    world: World,
) -> None:
    lead = world.lead()
    jane = add(world, lead, email=JANE_ADDRESS)
    world.site("<p>Our team</p>")
    world.renderer.pages[TEAM] = rendered_page(TEAM, f"<p>{JANE_LINE}</p>")
    run_id = world.crawl(lead)
    assert contact(world, jane)["address_class"] == "guessed"  # not in the crawled page

    world.clock.advance(timedelta(minutes=1))
    world.render(lead, run_id)

    assert contact(world, jane)["address_class"] == "published"
    [(fetched,)] = world.sql(
        "SELECT fetched_at FROM crawled_pages WHERE lead_id = ? AND url = ?", (lead, TEAM)
    )
    assert address_provenance(world, jane)[-1] == (
        "website",
        TEAM,
        JANE_ADDRESS,
        fetched,
        "published",
    )
    quotes = [
        (row["type"], row["quote"], row["source_url"], row["source"])
        for row in world.evidence(lead)
        if row["person_contact_id"] == jane and row["extracted_by"] == "rule"
    ]
    assert quotes == [("quote", JANE_LINE, TEAM, "website")]


def test_an_address_changed_during_the_mx_lookup_is_skipped_and_nothing_written(
    world: World,
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    events = contact_events(world, jane)
    dns = CallbackDns(
        lambda: world.sql("UPDATE contacts SET email = ? WHERE id = ?", (f"jr@{SITE}", jane)),
        mx={SITE: ()},  # no MX: the old address would have been invalid
    )
    found = with_ports(world, dns=dns)

    assert verify(world, found, lead, jane) is None

    assert contact(world, jane) == {
        "email": f"jr@{SITE}",
        "address_class": "guessed",
        "email_check_result": None,
        "email_checked_at": None,
        "email_invalid_reason": None,
        "email_verifier_at": None,
    }
    assert contact_events(world, jane) == events


def test_a_class_changed_during_the_verifier_call_is_skipped_and_nothing_written(
    world: World,
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    events = contact_events(world, jane)
    verifier = CallbackVerifier(
        lambda: world.sql("UPDATE contacts SET address_class = 'published' WHERE id = ?", (jane,)),
        VerifierAnswer("invalid"),
    )
    found = with_ports(world, verifier=verifier)

    assert verify(world, found, lead, jane) is None

    assert verifier.calls == [JANE_ADDRESS]
    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"], row["email_verifier_at"]) == (
        "published",
        None,
        None,
    )
    assert contact_events(world, jane) == events


def test_a_suppression_written_after_the_mx_lookup_makes_no_verifier_call(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    dns = CallbackDns(lambda: elsewhere(lambda: suppress(world, JANE_ADDRESS)), mx={SITE: MX})
    found = with_ports(world, dns=dns)

    assert verify(world, found, lead, jane) == "suppressed"

    assert found.verifier.calls == []
    row = contact(world, jane)
    assert (row["address_class"], row["email_check_result"], row["email_verifier_at"]) == (
        "guessed",
        "suppressed",
        None,
    )


def test_an_offline_invalidation_of_the_primary_enqueues_the_search_once(world: World) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    world.sql("UPDATE contacts SET email_search_tried = 'hunter' WHERE id = ?", (jane,))
    found = checks(world, mx={SITE: ()})

    run(world, found, lead, jane)
    run(world, found, lead, jane)  # skipped: invalid is terminal

    assert (contact(world, jane)["address_class"], contact(world, jane)["email_invalid_reason"]) == (
        "invalid",
        "no_mx",
    )
    assert [key for key, _, _ in jobs(world, "find_contact_email")] == [
        f"find_contact_email:{jane}:tick"
    ]


def email_domain(world: World, *, pattern: str | None, searched: bool) -> None:
    world.sql(
        "INSERT INTO email_domains (id, domain, pattern, pattern_source, pattern_learned_at, "
        "domain_search_at, created_at, updated_at) "
        "VALUES ('01JD0000000000000000000001', ?, ?, ?, ?, ?, ?, ?)",
        (
            SITE,
            pattern,
            None if pattern is None else "hunter_domain_search",
            None if pattern is None else utc(T0),
            utc(T0) if searched else None,
            utc(T0),
            utc(T0),
        ),
    )


@pytest.mark.parametrize(
    ("domain", "enqueued"),
    [
        (None, True),  # no row: finding would run the Domain Search
        ({"pattern": None, "searched": False}, True),
        ({"pattern": "first.last", "searched": False}, False),  # a stored pattern: it never runs
        ({"pattern": None, "searched": True}, False),
    ],
)
def test_with_both_providers_tried_the_search_is_enqueued_only_if_a_domain_search_would_run(
    world: World, domain: dict[str, Any] | None, enqueued: bool
) -> None:
    lead = site(world)
    jane = add(world, lead, email=JANE_ADDRESS)
    make_primary(world, jane)
    world.sql("UPDATE contacts SET email_search_tried = 'hunter,prospeo' WHERE id = ?", (jane,))
    if domain is not None:
        email_domain(world, **domain)

    run(world, checks(world, mx={SITE: ()}), lead, jane)

    assert contact(world, jane)["address_class"] == "invalid"
    assert len(jobs(world, "find_contact_email")) == (1 if enqueued else 0)


def test_the_csv_import_checks_an_address_filled_into_an_existing_contact_not_a_dropped_one(
    world: World,
) -> None:
    lead = world.lead("roe-law.com")
    jane = add(world, lead)  # no address yet
    ann = add(world, lead, "Ann Fox", email="ann@roe-law.com")
    world.sql("DELETE FROM jobs WHERE type = 'verify_contact_email'")
    imports = LeadImports(
        world.clock, world.discovery, world.research, FakeEmailSyntax(), world.queue
    )
    data = (
        "Organization name,Website,Country,Contact name,Email\r\n"
        "Roe LLP,roe-law.com,US,Jane Roe,jane@roe-law.com\r\n"  # filled into Jane
        "Roe LLP,roe-law.com,US,Ann Fox,a.fox@roe-law.com\r\n"  # differs from Ann's: dropped
    ).encode()

    with world.units.open():
        imports.run(ImportRequest("list.csv", "law_private_ai", data))

    assert contact(world, jane)["email"] == "jane@roe-law.com"
    assert contact(world, ann)["email"] == "ann@roe-law.com"
    assert [key for key, _, _ in jobs(world, "verify_contact_email")] == [
        f"verify_contact_email:{jane}:tick"
    ]
