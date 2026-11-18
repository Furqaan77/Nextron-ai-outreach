"""The erase confirmation step and the erased Contact on the Lead page (FR-52, UX-DR3, UX-DR17,
AD-13, AD-17, Story 3.13) through ``Dashboard(units=...)`` on a temporary database at head with
the real subscriber registry: the step names the Contact and holds the one form; every 404; the
POST needs CSRF and exactly one ``request_ref``; each answer (a refused reference, an erasure, a
repeat, a failure rolled back and logged) is 200 and shows no erased value; the Lead page's
tombstone line, for a request and for retention, holds no name, role, address, hash or LinkedIn
URL, and the Lead and its other Contacts are unchanged; both routes declare personal data and
come before ``/leads/{lead_id}``; and the view reads only through ``nextron.app.queries``.
Story 3.14: the erasure receipts on both pages (heading, state and one line per store, oldest
first) and the answers' links to them."""

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance.erasure import ErasureResult
from nextron.app.discovery.api import Discovery
from nextron.app.jobqueue.api import JobQueue
from nextron.app.pipeline.api import Pipeline
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.contactability import key_hash
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.leads import LeadIdentity, LeadSource
from nextron.domain.segments import validate_segments
from nextron.web.routing import declared_personal_data
from tests.asgi import Reply
from tests.contracts.support import PACKAGE
from tests.dashboard import FORM, Dashboard, form
from tests.fakes.clock import FrozenClock
from tests.unit.test_leads_page import Tree, cells

T = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
NAME = "Jane Doe"
ROLE = "Managing Partner"
EMAIL = "jane.doe@smith-law.co.uk"
LINKEDIN = "https://www.linkedin.com/in/jane-doe"
REF = "DSR-2026-0042"
UNKNOWN = "01KA0000000000000000000000"
ERASE = "Erase (data subject request)"
HINT = "a ticket or letter reference, never the person's name or address"
FAILED_LOGGER = "nextron.web.app"


@dataclass
class World:
    dashboard: Dashboard
    units: UnitOfWorkFactory
    engine: Engine
    clock: FrozenClock
    cookie: bytes
    token: str
    discovery: Discovery
    research: Research
    segment: str = ""

    def transact[R](self, fn: Callable[[], R]) -> R:
        @use_case
        def caller() -> R:
            with current().transaction():
                return fn()

        with self.units.open():
            return caller()

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[Any]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def lead(self, name: str = "Smith Law", domain: str = "smith-law.co.uk") -> str:
        identity = LeadIdentity(name, self.segment, web_domain=domain, country_code="GB")
        return self.transact(lambda: self.discovery.upsert_lead(identity, LeadSource.CSV)).lead_id

    def add(self, lead_id: str, **fields: str) -> str:
        added = self.transact(
            lambda: self.research.add_contact(
                lead_id,
                ContactCandidate(**fields),
                source=ProvenanceSource.CSV,
                detail="Law Society directory",
                retrieved_at=T,
            )
        )
        assert added.contact_id is not None
        return added.contact_id

    def get(self, path: str) -> Reply:
        return self.dashboard.get(path, cookie=self.cookie)

    def page(self, path: str) -> Tree:
        reply = self.get(path)
        assert reply.status == 200, reply
        assert reply.header(b"cache-control") == b"no-store"
        return Tree(reply.text)

    def post(self, path: str, body: bytes, *, token: str | None = "") -> Reply:
        return self.dashboard.post(
            path,
            cookie=self.cookie,
            token=self.token if token == "" else token,
            headers=[(b"content-type", FORM)],
            body=body,
        )

    def erase(self, path: str, ref: str = REF) -> Reply:
        return self.post(path, form(csrf_token=self.token, request_ref=ref))

    def requests(self) -> list[tuple[Any, ...]]:
        return self.sql("SELECT status, request_ref FROM data_subject_requests ORDER BY rowid")


def no_jobs() -> Any:
    raise AssertionError("no job runs in these tests")


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    dashboard = Dashboard(units=units, clock=clock)
    cookie = dashboard.open_session()
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
        job_boards=no_jobs,
        legal_forms=no_jobs,
    )
    found = World(
        dashboard,
        units,
        db_engine,
        clock,
        cookie,
        dashboard.csrf_token(cookie),
        Discovery(clock, Pipeline(clock), JobQueue(clock, job_types)),
        Research(clock),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    return found


def jane_and_bob(world: World) -> tuple[str, str, str]:
    lead = world.lead()
    jane = world.add(lead, name=NAME, role=ROLE, email=EMAIL, linkedin_url=LINKEDIN)
    bob = world.add(lead, name="Bob Other", email="bob@smith-law.co.uk")
    return lead, jane, bob


def path_of(lead: str, contact: str) -> str:
    return f"/leads/{lead}/contacts/{contact}/erase"


def assert_holds_no_erased_value(text: str) -> None:
    for value in ("Jane", "Doe", ROLE, EMAIL, LINKEDIN, key_hash("address", EMAIL)):
        assert value not in text, value


# The step ---------------------------------------------------------------------------------------


def test_the_lead_page_links_each_contact_to_its_step(world: World) -> None:
    lead, jane, bob = jane_and_bob(world)

    page = world.page(f"/leads/{lead}")

    rows = page.find_all("tr", class_="contact-row")
    links = [row.find("a", class_="erase-link") for row in rows]
    assert [(link.text, link.attrs["href"]) for link in links] == [
        (ERASE, path_of(lead, jane)),
        (ERASE, path_of(lead, bob)),
    ]


def test_the_step_names_the_contact_and_holds_the_one_form(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    page = world.page(path_of(lead, jane))

    assert page.find("title").text == "Nextron: Erase a Contact"
    assert page.find("h1").text == "Erase a Contact"
    named = page.find("dl", class_="erase-contact")
    assert "hx-disable" in named.attrs
    facts = [node.text for node in named.walk() if node.tag in ("dt", "dd")]
    assert facts == ["Name", NAME, "Role", ROLE, "Email", EMAIL, "LinkedIn", LINKEDIN]
    assert "can't be undone" in page.text
    assert "never contacted again" in page.text
    [panel] = page.find_all("div", id="erase-panel")
    [formed] = panel.find_all("form")
    assert formed.attrs["method"] == "post"
    assert formed.attrs["hx-post"] == path_of(lead, jane)
    assert formed.attrs["hx-target"] == "#erase-panel"
    assert not [node for node in formed.ancestors() if "hx-disable" in node.attrs]
    inputs = formed.find_all("input")
    assert [(node.attrs["type"], node.attrs["name"]) for node in inputs] == [
        ("hidden", "csrf_token"),
        ("text", "request_ref"),
    ]
    assert inputs[0].attrs["value"] == world.token
    assert inputs[1].attrs["maxlength"] == "200"
    assert HINT in formed.find("p", class_="hint").text
    assert formed.find("button").text == "Erase permanently"
    on = page.find("p", class_="erase-lead")
    assert "hx-disable" in on.attrs
    assert on.find("a").attrs["href"] == f"/leads/{lead}"


def test_the_step_of_a_merged_leads_contact_answers_on_the_survivor(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    old = world.lead("Smith Law Old", "smith-law-old.co.uk")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead, old))

    assert world.get(path_of(old, jane)).status == 200


@pytest.mark.parametrize("case", ["lead", "contact", "other lead", "not ulid", "lowercase"])
def test_a_contact_not_on_the_leads_survivor_is_404(world: World, case: str) -> None:
    lead, jane, _ = jane_and_bob(world)
    other = world.lead("Jones Law", "jones.co.uk")
    path = {
        "lead": path_of(UNKNOWN, jane),
        "contact": path_of(lead, UNKNOWN),
        "other lead": path_of(other, jane),
        "not ulid": path_of(lead, "nope"),
        "lowercase": path_of(lead, jane.lower()),
    }[case]

    for reply in (world.get(path), world.erase(path)):
        assert reply.status == 404
        assert reply.header(b"cache-control") == b"no-store"
        assert Tree(reply.text).find("h1").text == "Lead not found"
        assert UNKNOWN not in reply.text and jane not in reply.text and NAME not in reply.text
    assert world.requests() == []


# The POST ---------------------------------------------------------------------------------------


def test_the_post_needs_its_csrf_token(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    reply = world.post(path_of(lead, jane), form(request_ref=REF), token=None)

    assert reply.status == 403
    assert world.requests() == []


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"request_ref": REF, "note": "x"},
        {"reference": REF},
    ],
)
def test_the_post_takes_exactly_one_request_ref(world: World, fields: dict[str, str]) -> None:
    lead, jane, _ = jane_and_bob(world)

    reply = world.post(path_of(lead, jane), form(csrf_token=world.token, **fields))

    assert reply.status == 400
    assert reply.text == "Request refused: malformed form"
    assert world.requests() == []


def test_a_repeated_request_ref_is_malformed(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    body = form(csrf_token=world.token, request_ref=REF) + b"&request_ref=again"

    assert world.post(path_of(lead, jane), body).status == 400
    assert world.requests() == []


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("jane@smith-law.co.uk", "must not hold an email address"),
        ("DSR for Jane Doe", "must not hold the person's name"),
        ("", "1 to 200 characters"),
        ("x" * 201, "1 to 200 characters"),
    ],
)
def test_a_refused_reference_re_renders_the_panel(world: World, ref: str, message: str) -> None:
    lead, jane, _ = jane_and_bob(world)

    reply = world.erase(path_of(lead, jane), ref)

    assert reply.status == 200
    panel = Tree(reply.text)
    notice = panel.find("p", class_="notice")
    assert notice.attrs["role"] == "alert" and "hx-disable" in notice.attrs
    assert message in notice.text
    assert panel.find("input", name="request_ref").attrs["value"] == ref
    assert panel.find("dl", class_="erase-contact")  # nothing ran: the Contact is still named
    assert world.requests() == []
    assert world.sql("SELECT erased_at FROM contacts WHERE id = ?", (jane,)) == [(None,)]


def test_an_erasure_answers_with_its_request_and_no_erased_value(world: World) -> None:
    lead, jane, bob = jane_and_bob(world)
    bob_before = world.sql("SELECT * FROM contacts WHERE id = ?", (bob,))

    reply = world.erase(path_of(lead, jane))

    assert reply.status == 200
    assert reply.header(b"cache-control") == b"no-store"
    panel = Tree(reply.text)
    notice = panel.find("p", class_="notice")
    assert (notice.attrs["role"], notice.text) == (
        "status",
        f"Erased. Request {REF} completed. See the receipt on the Lead page",
    )
    [(receipt_id,)] = world.sql("SELECT id FROM erasure_receipts")
    assert notice.find("a").attrs["href"] == f"/leads/{lead}#receipt-{receipt_id}"  # Story 3.14
    erased = panel.find("p", class_="erased-contact")
    assert erased.text == "This Contact was erased on 2026-11-17."
    assert not panel.find_all("form") and not panel.find_all("dl")
    assert_holds_no_erased_value(reply.text)
    assert world.requests() == [("completed", REF)]
    assert world.sql("SELECT * FROM contacts WHERE id = ?", (bob,)) == bob_before


def test_an_erased_contacts_step_shows_only_when_it_was_erased(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))

    reply = world.get(path_of(lead, jane))

    assert reply.status == 200
    panel = Tree(reply.text).find("div", id="erase-panel")
    assert panel.find("p", class_="erased-contact").text == "This Contact was erased on 2026-11-17."
    assert not panel.find_all("form") and not panel.find_all("dl")
    assert len(panel.find_all("section", class_="erasure-receipt")) == 1  # Story 3.14
    assert_holds_no_erased_value(reply.text)


def test_erasing_again_says_nothing_changed(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))

    # the tombstone's "[erased]" is no name: a reference holding "Erased" is accepted
    reply = world.erase(path_of(lead, jane), "Erased per letter 12")

    assert reply.status == 200
    notice = Tree(reply.text).find("p", class_="notice")
    assert notice.text == (
        "This Contact was already erased; nothing more was erased, and this request has its "
        "own receipt. See the receipt on the Lead page"
    )
    [(receipt_id,)] = world.sql(
        "SELECT id FROM erasure_receipts WHERE outcome = 'already_erased'"
    )
    assert notice.find("a").attrs["href"] == f"/leads/{lead}#receipt-{receipt_id}"
    assert_holds_no_erased_value(reply.text)
    assert world.requests() == [("completed", REF), ("completed", "Erased per letter 12")]


def test_a_bad_reference_for_an_erased_contact_is_refused_too(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))

    reply = world.erase(path_of(lead, jane), "x@y")

    assert reply.status == 200
    panel = Tree(reply.text)
    notice = panel.find("p", class_="notice")
    assert notice.attrs["role"] == "alert" and "(no @)" in notice.text
    assert panel.find("p", class_="erased-contact")
    assert world.requests() == [("completed", REF)]


def test_a_failed_erasure_is_rolled_back_logged_and_shown(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lead, jane, _ = jane_and_bob(world)
    contact = world.sql("SELECT * FROM contacts WHERE id = ?", (jane,))

    def broken(lead_ids: object) -> int:
        raise RuntimeError("the page store broke on Jane Doe")

    monkeypatch.setattr(crawl_repo, "tombstone_pages", broken)
    caplog.set_level(logging.INFO)

    reply = world.erase(path_of(lead, jane))

    assert reply.status == 200
    notice = Tree(reply.text).find("p", class_="notice")
    assert notice.attrs["role"] == "alert"
    assert notice.text == (
        "Erasure failed and was rolled back: nothing was erased, and the request stays open "
        "(RuntimeError)."
    )
    assert "page store broke" not in reply.text
    assert world.sql("SELECT * FROM contacts WHERE id = ?", (jane,)) == contact
    assert world.requests() == [("open", REF)]
    [record] = [r for r in caplog.records if r.getMessage() == "contact_erasure_failed"]
    assert record.name == FAILED_LOGGER
    found = (record.contact_id, record.error_type)  # type: ignore[attr-defined]
    assert found == (jane, "RuntimeError")
    assert "Doe" not in repr(record.__dict__)


# The Lead page ----------------------------------------------------------------------------------


def test_the_lead_page_shows_an_erased_contact_by_its_tombstone_line_only(world: World) -> None:
    lead, jane, bob = jane_and_bob(world)
    before = world.page(f"/leads/{lead}")
    bob_row = [row for row in before.find_all("tr", class_="contact-row") if "Bob" in row.text]
    header = before.find("header", class_="lead-header").text
    world.erase(path_of(lead, jane))

    reply = world.get(f"/leads/{lead}")

    page = Tree(reply.text)
    first, second = page.find_all("tr", class_="contact-row")
    assert "contact-erased" in (first.attrs.get("class") or "")
    assert cells(first) == [f"[erased], erased 2026-11-17 (request {REF})"]
    assert first.find_all("td")[0].attrs["colspan"] == "8"  # the Contacts panel's eight columns
    assert not first.find_all("a")
    assert cells(second) == cells(bob_row[0])
    assert len(page.find_all("tr", class_="provenance-row")) == 1  # Bob's only
    assert len(page.find_all("tr", class_="receipt-row")) == 1  # Jane's receipts (Story 3.14)
    assert page.find("header", class_="lead-header").text == header
    assert_holds_no_erased_value(reply.text)
    assert "guessed" not in cells(first)[0]


def test_a_retention_erasure_shows_no_request(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.transact(lambda: world.dashboard.erasures.erase_contact(jane, None, "retention"))

    page = world.page(f"/leads/{lead}")

    [erased] = page.find_all("tr", class_="contact-erased")
    assert cells(erased) == ["[erased], erased 2026-11-17"]
    assert_holds_no_erased_value(world.get(f"/leads/{lead}").text)


def test_the_tombstone_line_names_the_request_that_erased_the_contact(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))
    world.clock.set(T + timedelta(days=2))
    world.erase(path_of(lead, jane), "DSR-LATER")

    [erased] = world.page(f"/leads/{lead}").find_all("tr", class_="contact-erased")

    assert cells(erased) == [f"[erased], erased 2026-11-17 (request {REF})"]


def test_the_erase_use_case_result_is_what_the_answer_says(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    with world.units.open():
        result = world.dashboard.erasures.erase_on_request(jane, REF)

    [(request_id,)] = world.sql("SELECT id FROM data_subject_requests")
    assert result == ErasureResult(jane, request_id, erased=True, receipt_id=result.receipt_id)


# The routes and the view module -----------------------------------------------------------------


def test_both_routes_declare_personal_data_and_come_before_the_lead_page(world: World) -> None:
    routes = [
        route
        for route in world.dashboard.app.routes
        if getattr(route, "name", None) in ("contact_erase_page", "contact_erase", "lead_page")
    ]

    assert [(getattr(r, "name"), sorted(getattr(r, "methods"))) for r in routes] == [
        ("contact_erase_page", ["GET"]),
        ("contact_erase", ["POST"]),
        ("lead_page", ["GET"]),
    ]
    assert all(declared_personal_data(getattr(r, "endpoint")) is True for r in routes)


def test_the_step_is_locked_without_a_session(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    reply = world.dashboard.get(path_of(lead, jane))

    assert reply.status == 401
    assert NAME not in reply.text


def test_the_view_module_reads_only_through_queries() -> None:
    tree = ast.parse((PACKAGE / "web" / "erase_page.py").read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    modules += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]

    app_modules = [name for name in modules if name.startswith("nextron.app")]
    assert app_modules  # not vacuous
    assert all(name.startswith("nextron.app.queries.") for name in app_modules), app_modules
    assert not [name for name in modules if name.startswith("nextron.db")]


# Receipts (Story 3.14) --------------------------------------------------------------------------


def receipt_sections(page: Tree) -> list[tuple[str, str, list[list[str]]]]:
    """Each receipt shown: its element id, its heading and its store lines."""
    return [
        (
            section.attrs["id"] or "",
            section.find("h3").text,
            [cells(row) for row in section.find("tbody").find_all("tr")],
        )
        for section in page.find_all("section", class_="erasure-receipt")
    ]


def settle_all(world: World, status: str = "done") -> None:
    world.sql(
        "UPDATE erasure_receipt_stores SET status = ?, detail = 'Settled' "
        "WHERE status = 'pending'",
        (status,),
    )


def test_the_lead_page_follows_the_tombstone_line_with_its_receipt(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))
    [(receipt_id,)] = world.sql("SELECT id FROM erasure_receipts")

    page = world.page(f"/leads/{lead}")

    table = page.find("table", class_="contact-table")
    [body] = [node for node in table.children if not isinstance(node, str) and node.tag == "tbody"]
    rows = [row for row in body.children if not isinstance(row, str)]
    assert [row.attrs.get("class") for row in rows] == [
        "contact-row contact-erased",
        "receipt-row",
        "contact-row",
        "eligibility-row",  # main's 8-column Contacts panel (Stories 2.10, 3.6, 3.7)
        "provenance-row",
    ]
    [(anchor, heading, lines)] = receipt_sections(page)
    assert (anchor, heading) == (f"receipt-{receipt_id}", "Receipt 2026-11-17: In progress")
    assert [line[0] for line in lines] == [
        "Database",
        "Suppression List",
        "Evidence quotes",
        "Page addresses",
        "Other Contacts",
        "Sending Inbox mailboxes",
        "Crawled page text",
        "Telegram",
        "Anthropic",
        "Worker KV",
        "Backups",
        "AI Test Sets",
        "nextronai.com inbox",
        "Logs",
        "Dev and test sink files",
        "Restore work copies",
        "Erasure ledger",
    ]
    assert lines[1] == [
        "Suppression List",
        "Done",
        "3 keys (address, person, profile) stay suppressed",
        "2026-11-17 09:00 UTC",
    ]
    assert lines[7] == [
        "Telegram",
        "Pending",
        "Waiting for its erase_external job",
        "2026-11-17 09:00 UTC",
    ]
    section = page.find("section", class_="erasure-receipt")
    assert any("hx-disable" in node.attrs for node in (section, *section.ancestors()))
    assert_holds_no_erased_value(page.text)


def test_the_receipt_reads_complete_or_incomplete_as_its_stores_settle(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))
    world.sql(
        "UPDATE erasure_receipt_stores SET status = 'failed', detail = 'Failed: RuntimeError' "
        "WHERE store = 'telegram'"
    )

    [(_, heading, lines)] = receipt_sections(world.page(f"/leads/{lead}"))
    assert heading == "Receipt 2026-11-17: Incomplete: 1 failed"
    assert lines[7][:3] == ["Telegram", "Failed", "Failed: RuntimeError"]

    settle_all(world, "residual")
    [(_, heading, _)] = receipt_sections(world.page(f"/leads/{lead}"))
    assert heading == "Receipt 2026-11-17: Incomplete: 1 failed"  # a failed store stays failed


def test_a_settled_receipt_reads_complete(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))
    settle_all(world)

    [(_, heading, lines)] = receipt_sections(world.page(f"/leads/{lead}"))

    assert heading == "Receipt 2026-11-17: Complete"
    assert {line[1] for line in lines} == {"Done", "None held"}


def test_a_repeat_request_adds_its_receipt_after_the_first(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))
    world.clock.set(T + timedelta(days=2))
    world.erase(path_of(lead, jane), "DSR-LATER")
    ids = [row[0] for row in world.sql("SELECT id FROM erasure_receipts ORDER BY created_at")]

    sections = receipt_sections(world.page(f"/leads/{lead}"))

    assert [(anchor, heading) for anchor, heading, _ in sections] == [
        (f"receipt-{ids[0]}", "Receipt 2026-11-17: In progress"),
        (f"receipt-{ids[1]}", "Receipt 2026-11-19: In progress"),
    ]
    assert sections[1][2][0][:3] == [
        "Database",
        "None held",
        "Already erased on 2026-11-17; see that erasure's receipt",
    ]


def test_the_erase_step_of_an_erased_contact_shows_the_same_receipts(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    world.erase(path_of(lead, jane))

    step = receipt_sections(world.page(path_of(lead, jane)))

    assert step == receipt_sections(world.page(f"/leads/{lead}"))
    assert len(step) == 1


def test_a_merged_leads_answer_links_the_receipt_on_the_survivor(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)
    old = world.lead("Smith Law Old", "smith-law-old.co.uk")
    world.sql("UPDATE leads SET merged_into = ? WHERE id = ?", (lead, old))

    reply = world.erase(path_of(old, jane))

    [(receipt_id,)] = world.sql("SELECT id FROM erasure_receipts")
    link = Tree(reply.text).find("p", class_="notice").find("a")
    assert link.attrs["href"] == f"/leads/{lead}#receipt-{receipt_id}"


def test_a_contact_that_isnt_erased_shows_no_receipt(world: World) -> None:
    lead, jane, _ = jane_and_bob(world)

    assert receipt_sections(world.page(f"/leads/{lead}")) == []
    assert receipt_sections(world.page(path_of(lead, jane))) == []


def test_each_receipt_names_the_request_it_answers(world: World) -> None:
    lead, jane, bob = jane_and_bob(world)
    world.erase(path_of(lead, jane))
    world.clock.set(T + timedelta(days=2))
    world.erase(path_of(lead, jane), "DSR-LATER")
    world.transact(lambda: world.dashboard.erasures.erase_contact(bob, None, "retention"))

    page = world.page(f"/leads/{lead}")

    lines = [
        section.find("p", class_="receipt-request").text
        for section in page.find_all("section", class_="erasure-receipt")
    ]
    assert lines == [f"Request {REF}", "Request DSR-LATER, already erased", "Retention"]
    assert all(page.disabled("DSR-LATER"))  # the typed reference renders inside hx-disable
